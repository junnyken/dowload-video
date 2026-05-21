"""
ScraperAPI Key Pool
===================
Manages multiple ScraperAPI keys with automatic rotation and credit tracking.

Set multiple keys in .env (comma-separated):
    SCRAPERAPI_API_KEY=key1,key2,key3

Redis keys:
    scraperapi:active_idx            — index of current active key
    scraperapi:credits:{hash}        — cached credit balance (TTL 10 min)
    scraperapi:exhausted:{hash}      — key marked exhausted (TTL 24h, auto-recover)

Rotation triggers:
    • credits < EXHAUST_THRESHOLD (default 50) on a request response header
    • explicit rotate_key() call from admin
    • credits = 0 on account API check
"""

import hashlib
import os
from typing import Optional

import httpx

from app.core.redis_client import get_redis

_CREDITS_TTL    = 600       # 10 min cache for credits
_EXHAUSTED_TTL  = 86_400    # 24h before auto-retrying an exhausted key
EXHAUST_THRESHOLD = 50      # rotate when credits drop below this
_SCRAPERAPI_HOST  = "proxy-server.scraperapi.com:8011"


def _parse_keys() -> list[str]:
    raw = os.getenv("SCRAPERAPI_API_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def _hash(key: str) -> str:
    return hashlib.md5(key.encode()).hexdigest()[:12]


# ── Active key selection ───────────────────────────────────────────

def get_active_key() -> str:
    """Return the current active (non-exhausted) key, or "" if none."""
    keys = _parse_keys()
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]

    try:
        rc = get_redis()
        idx = int(rc.get("scraperapi:active_idx") or 0) % len(keys)
        # Walk forward until we find a non-exhausted key
        for offset in range(len(keys)):
            candidate = keys[(idx + offset) % len(keys)]
            if not rc.get(f"scraperapi:exhausted:{_hash(candidate)}"):
                if offset > 0:
                    # Update index to skip the exhausted key(s)
                    rc.set("scraperapi:active_idx", (idx + offset) % len(keys))
                return candidate
    except Exception as e:
        print(f"[ScraperAPIPool] Redis error in get_active_key: {e}")

    # Redis unavailable — return first key as fallback
    return keys[0]


def rotate_key(reason: str = "manual") -> str:
    """
    Advance to the next key in the pool.
    Marks the current key exhausted for EXHAUSTED_TTL seconds.
    Returns the new active key (or "" if all exhausted).
    """
    keys = _parse_keys()
    if not keys:
        return ""

    try:
        rc = get_redis()
        idx = int(rc.get("scraperapi:active_idx") or 0) % len(keys)
        current_key = keys[idx]

        # Mark current key exhausted
        rc.setex(f"scraperapi:exhausted:{_hash(current_key)}", _EXHAUSTED_TTL, "1")
        print(f"[ScraperAPIPool] Key {_hash(current_key)} exhausted ({reason}), rotating...")

        # Advance index
        new_idx = (idx + 1) % len(keys)
        rc.set("scraperapi:active_idx", new_idx)

        return get_active_key()
    except Exception as e:
        print(f"[ScraperAPIPool] Rotate failed: {e}")
        return keys[0] if keys else ""


def check_response_and_rotate(credits_left: Optional[int]) -> None:
    """
    Called after each ScraperAPI request with the X-Rest-Api-Credits-Left header.
    Caches credits and rotates if below threshold.
    """
    key = get_active_key()
    if not key or credits_left is None:
        return

    h = _hash(key)
    try:
        get_redis().setex(f"scraperapi:credits:{h}", _CREDITS_TTL, str(credits_left))
    except Exception:
        pass

    if credits_left < EXHAUST_THRESHOLD:
        rotate_key(reason=f"low credits ({credits_left})")


# ── Credit queries ─────────────────────────────────────────────────

def fetch_credits(key: str, use_cache: bool = True) -> Optional[int]:
    """Fetch remaining credits for a single key (cached in Redis)."""
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
        print(f"[ScraperAPIPool] fetch_credits({h}) failed: {e}")
    return None


def fetch_all_credits(use_cache: bool = True) -> list[dict]:
    """Return credit info for every configured key."""
    keys = _parse_keys()
    result = []
    for i, key in enumerate(keys):
        h = _hash(key)
        try:
            is_exhausted = bool(get_redis().get(f"scraperapi:exhausted:{h}"))
            active_idx = int(get_redis().get("scraperapi:active_idx") or 0) % max(len(keys), 1)
        except Exception:
            is_exhausted = False
            active_idx = 0

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
    """Build an HTTP proxy URL using the current active key."""
    key = get_active_key()
    if not key:
        return ""
    user = f"scraperapi.country_code={country_code}" if country_code else "scraperapi"
    return f"http://{user}:{key}@{_SCRAPERAPI_HOST}"


def scraperapi_url(target_url: str, render: bool = False) -> str:
    """Build a direct ScraperAPI fetch URL."""
    from urllib.parse import urlencode
    key = get_active_key()
    if not key:
        return ""
    params = urlencode({"api_key": key, "url": target_url, "render": str(render).lower()})
    return f"http://api.scraperapi.com/?{params}"
