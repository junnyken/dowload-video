"""
ScraperAPI Key Pool
===================
Redis-backed key pool. Keys can be added/removed from the admin UI
without touching .env.

On first boot (Redis list empty), keys are imported from SCRAPERAPI_API_KEY
env var (comma-separated) so existing setups keep working.

Redis keys:
    scraperapi:keys              — LIST of API key strings (source of truth)
    scraperapi:active_idx        — index of current active key
    scraperapi:credits:{hash}    — cached credit balance (TTL 10 min)
    scraperapi:exhausted:{hash}  — key marked exhausted (TTL 24h, auto-recover)

Rotation triggers:
    • credits < EXHAUST_THRESHOLD (50) on response header
    • HTTP 429 from ScraperAPI
    • explicit rotate_key() / admin button
"""

import hashlib
import os
from typing import Optional

import httpx

from app.core.redis_client import get_redis

_KEYS_KEY       = "scraperapi:keys"
_CREDITS_TTL    = 600
_EXHAUSTED_TTL  = 86_400
EXHAUST_THRESHOLD = 50
_SCRAPERAPI_HOST  = "proxy-server.scraperapi.com:8011"


def _hash(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── Key list (Redis-backed, env-seeded) ───────────────────────────

def _ensure_seeded(rc) -> None:
    """Import env var keys into Redis if pool is empty."""
    if rc.llen(_KEYS_KEY) == 0:
        raw = os.getenv("SCRAPERAPI_API_KEY", "")
        for k in [x.strip() for x in raw.split(",") if x.strip()]:
            if k not in rc.lrange(_KEYS_KEY, 0, -1):
                rc.rpush(_KEYS_KEY, k)


def get_all_keys() -> list[str]:
    """Return all keys currently in the pool."""
    try:
        rc = get_redis()
        _ensure_seeded(rc)
        raw = rc.lrange(_KEYS_KEY, 0, -1)
        return [k if isinstance(k, str) else k.decode() for k in raw]
    except Exception as e:
        print(f"[ScraperAPIPool] get_all_keys failed: {e}")
        # Fallback to env var
        raw = os.getenv("SCRAPERAPI_API_KEY", "")
        return [k.strip() for k in raw.split(",") if k.strip()]


def add_key(key: str) -> int:
    """Add a key to the pool. Returns new pool size. Skips duplicates."""
    key = key.strip()
    if not key:
        return len(get_all_keys())
    try:
        rc = get_redis()
        _ensure_seeded(rc)
        existing = rc.lrange(_KEYS_KEY, 0, -1)
        existing_str = [k if isinstance(k, str) else k.decode() for k in existing]
        if key in existing_str:
            return len(existing_str)
        rc.rpush(_KEYS_KEY, key)
        # Clear exhausted flag if re-adding a previously removed key
        rc.delete(f"scraperapi:exhausted:{_hash(key)}")
        print(f"[ScraperAPIPool] Added key {_hash(key)}")
        return rc.llen(_KEYS_KEY)
    except Exception as e:
        print(f"[ScraperAPIPool] add_key failed: {e}")
        return 0


def remove_key(index: int) -> int:
    """Remove key at index. Returns remaining pool size."""
    try:
        rc = get_redis()
        keys = get_all_keys()
        if index < 0 or index >= len(keys):
            raise ValueError(f"Index {index} out of range")
        key = keys[index]
        sentinel = f"__del_{index}__"
        rc.lset(_KEYS_KEY, index, sentinel)
        rc.lrem(_KEYS_KEY, 0, sentinel)
        # Clean up related Redis state
        h = _hash(key)
        rc.delete(f"scraperapi:credits:{h}", f"scraperapi:exhausted:{h}")
        # Reset active index if needed
        size = rc.llen(_KEYS_KEY)
        if size > 0:
            cur_idx = int(rc.get("scraperapi:active_idx") or 0)
            rc.set("scraperapi:active_idx", cur_idx % size)
        else:
            rc.delete("scraperapi:active_idx")
        print(f"[ScraperAPIPool] Removed key {h}")
        return size
    except Exception as e:
        print(f"[ScraperAPIPool] remove_key failed: {e}")
        return len(get_all_keys())


# ── Active key selection ───────────────────────────────────────────

def get_active_key() -> str:
    """Return current active (non-exhausted) key, or '' if none."""
    try:
        rc = get_redis()
        _ensure_seeded(rc)
        keys = get_all_keys()
        if not keys:
            return ""
        if len(keys) == 1:
            return keys[0]

        idx = int(rc.get("scraperapi:active_idx") or 0) % len(keys)
        for offset in range(len(keys)):
            candidate = keys[(idx + offset) % len(keys)]
            if not rc.get(f"scraperapi:exhausted:{_hash(candidate)}"):
                if offset > 0:
                    rc.set("scraperapi:active_idx", (idx + offset) % len(keys))
                return candidate
    except Exception as e:
        print(f"[ScraperAPIPool] get_active_key error: {e}")

    keys = get_all_keys()
    return keys[0] if keys else ""


def rotate_key(reason: str = "manual") -> str:
    """Advance to next key, mark current exhausted. Returns new active key."""
    try:
        rc = get_redis()
        keys = get_all_keys()
        if not keys:
            return ""
        idx = int(rc.get("scraperapi:active_idx") or 0) % len(keys)
        current_key = keys[idx]
        rc.setex(f"scraperapi:exhausted:{_hash(current_key)}", _EXHAUSTED_TTL, "1")
        print(f"[ScraperAPIPool] Key {_hash(current_key)} exhausted ({reason})")
        rc.set("scraperapi:active_idx", (idx + 1) % len(keys))
        return get_active_key()
    except Exception as e:
        print(f"[ScraperAPIPool] rotate_key failed: {e}")
        return ""


def check_response_and_rotate(credits_left: Optional[int]) -> None:
    """Update credit cache; rotate if below threshold."""
    key = get_active_key()
    if not key or credits_left is None:
        return
    try:
        get_redis().setex(f"scraperapi:credits:{_hash(key)}", _CREDITS_TTL, str(credits_left))
    except Exception:
        pass
    if credits_left < EXHAUST_THRESHOLD:
        rotate_key(reason=f"low credits ({credits_left})")


# ── Credit queries ─────────────────────────────────────────────────

def fetch_credits(key: str, use_cache: bool = True) -> Optional[int]:
    """Fetch remaining credits for one key (Redis-cached)."""
    h = _hash(key)
    if use_cache:
        try:
            cached = get_redis().get(f"scraperapi:credits:{h}")
            if cached is not None:
                return int(cached)
        except Exception:
            pass
    try:
        resp = httpx.get(
            f"http://api.scraperapi.com/account?api_key={key}",
            timeout=6.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            remaining = data.get("requestLimit", 0) - data.get("requestCount", 0)
            try:
                get_redis().setex(f"scraperapi:credits:{h}", _CREDITS_TTL, str(remaining))
            except Exception:
                pass
            return remaining
    except Exception as e:
        print(f"[ScraperAPIPool] fetch_credits({h}) error: {e}")
    return None


def fetch_all_credits(use_cache: bool = True) -> list[dict]:
    """Return credit status for every key in the pool."""
    keys = get_all_keys()
    if not keys:
        return []
    try:
        rc = get_redis()
        active_idx = int(rc.get("scraperapi:active_idx") or 0) % max(len(keys), 1)
    except Exception:
        active_idx = 0

    result = []
    for i, key in enumerate(keys):
        h = _hash(key)
        try:
            is_exhausted = bool(get_redis().get(f"scraperapi:exhausted:{h}"))
        except Exception:
            is_exhausted = False
        credits = fetch_credits(key, use_cache=use_cache)
        result.append({
            "index":      i,
            "key_hash":   h,
            "key_prefix": key[:8] + "***",
            "credits":    credits,
            "active":     i == active_idx and not is_exhausted,
            "exhausted":  is_exhausted,
        })
    return result


# ── Proxy URL helpers ──────────────────────────────────────────────

def scraperapi_proxy(country_code: str = "") -> str:
    key = get_active_key()
    if not key:
        return ""
    user = f"scraperapi.country_code={country_code}" if country_code else "scraperapi"
    return f"http://{user}:{key}@{_SCRAPERAPI_HOST}"


def scraperapi_url(target_url: str, render: bool = False) -> str:
    from urllib.parse import urlencode
    key = get_active_key()
    if not key:
        return ""
    params = urlencode({"api_key": key, "url": target_url, "render": str(render).lower()})
    return f"http://api.scraperapi.com/?{params}"
