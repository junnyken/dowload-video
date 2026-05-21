"""
PO Token Cache
==============
Redis-backed cache for YouTube Proof-of-Origin tokens.

bgutil-pot generates tokens via headless Chrome (~1-3s each).
Without caching, every Celery worker hits bgutil-pot on every YouTube
download — 8 workers = 8 Chrome calls/minute under load.

With caching: ONE token shared across all workers, refreshed every 3.5h.
Tokens are valid ~6h, so 3.5h TTL gives safe overlap.

Key:  youtube:po_token        — current valid token (string)
Key:  youtube:po_visitor_data — paired visitor_data used to generate it
"""

import os
import time
from typing import Optional

import httpx

from app.core.redis_client import get_redis

_TOKEN_KEY        = "youtube:po_token"
_VISITOR_KEY      = "youtube:po_visitor_data"
_TOKEN_TTL        = 12_600   # 3.5 hours in seconds
_FETCH_TIMEOUT    = 20       # seconds to wait for bgutil-pot Chrome
_LOCK_KEY         = "youtube:po_token:lock"
_LOCK_TTL         = 30       # prevent thundering herd on cache miss


def get_po_token() -> Optional[str]:
    """Return cached PO token, or fetch fresh from bgutil-pot."""
    try:
        rc = get_redis()
        token = rc.get(_TOKEN_KEY)
        if token:
            t = token if isinstance(token, str) else token.decode()
            if t:
                return t
    except Exception as e:
        print(f"[POToken] Redis read error: {e}")
    return _fetch_and_cache()


def get_po_visitor_data() -> Optional[str]:
    """Return paired visitor_data for the cached token."""
    try:
        rc = get_redis()
        vd = rc.get(_VISITOR_KEY)
        if vd:
            return vd if isinstance(vd, str) else vd.decode()
    except Exception:
        pass
    return None


def invalidate_po_token() -> None:
    """Force-expire the cached token (call when YouTube blocks)."""
    try:
        get_redis().delete(_TOKEN_KEY, _VISITOR_KEY)
        print("[POToken] Cache invalidated")
    except Exception as e:
        print(f"[POToken] Invalidate error: {e}")


def refresh_po_token() -> Optional[str]:
    """Force-fetch a fresh token from bgutil-pot and update cache."""
    return _fetch_and_cache(force=True)


def get_cache_ttl() -> int:
    """Return remaining TTL of cached token in seconds, -1 if absent."""
    try:
        return get_redis().ttl(_TOKEN_KEY)
    except Exception:
        return -1


# ── Internal ──────────────────────────────────────────────────────────

def _fetch_and_cache(force: bool = False) -> Optional[str]:
    bgutil_urls = _get_bgutil_urls()
    if not bgutil_urls:
        return None

    rc = get_redis()

    # Distributed lock — only one worker fetches at a time
    if not force:
        locked = rc.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL)
        if not locked:
            # Another worker is fetching; wait briefly then return whatever is cached
            time.sleep(2)
            token = rc.get(_TOKEN_KEY)
            if token:
                return token if isinstance(token, str) else token.decode()
            return None

    for url in bgutil_urls:
        token, visitor_data = _call_bgutil(url)
        if token:
            try:
                pipe = rc.pipeline()
                pipe.setex(_TOKEN_KEY, _TOKEN_TTL, token)
                if visitor_data:
                    pipe.setex(_VISITOR_KEY, _TOKEN_TTL, visitor_data)
                pipe.execute()
                print(f"[POToken] Cached new token (TTL {_TOKEN_TTL}s) via {url}")
            except Exception as e:
                print(f"[POToken] Cache write error: {e}")
            finally:
                try:
                    rc.delete(_LOCK_KEY)
                except Exception:
                    pass
            return token

    try:
        rc.delete(_LOCK_KEY)
    except Exception:
        pass
    print("[POToken] All bgutil-pot instances failed")
    return None


def _call_bgutil(base_url: str) -> tuple[Optional[str], Optional[str]]:
    """POST to bgutil-pot /get_pot, return (po_token, visitor_data)."""
    try:
        resp = httpx.post(
            f"{base_url}/get_pot",
            json={"videoId": "dQw4w9WgXcQ"},
            timeout=_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("po_token") or data.get("poToken") or data.get("token")
        visitor_data = data.get("visitor_data") or data.get("visitorData")
        if token:
            print(f"[POToken] Fetched from {base_url}: {token[:20]}...")
        return token, visitor_data
    except Exception as e:
        print(f"[POToken] bgutil-pot at {base_url} failed: {e}")
        return None, None


def _get_bgutil_urls() -> list[str]:
    """Support comma-separated BGUTIL_POT_URL for multi-instance."""
    raw = os.getenv("BGUTIL_POT_URL", "")
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]
