"""
Cookie Pool Manager
===================
Redis-backed rotating cookie pool per platform.

Each worker picks ONE cookie and caches it locally.
When a block signal is detected, that cookie is marked blocked in Redis
and the local cache is cleared — next request picks a fresh cookie.

Redis keys:
  cookie_pool:{platform}          LIST of base64-encoded cookie strings (round-robin)
  cookie_health:{platform}:{hash} "blocked" with TTL (auto-recovery after BLOCK_TTL)

Platforms: youtube, tiktok, facebook, instagram
"""

import hashlib
from typing import Optional

from app.core.redis_client import get_redis

BLOCK_TTL = 3600  # seconds before a blocked cookie is auto-retried


def _hash(cookie_b64: str) -> str:
    return hashlib.md5(cookie_b64[:32].encode()).hexdigest()[:16]


def get_cookie_from_pool(platform: str) -> Optional[str]:
    """
    Round-robin: pick next healthy cookie from pool.
    Returns None if pool empty or all cookies blocked.
    """
    rc = get_redis()
    pool_key = f"cookie_pool:{platform}"
    size = rc.llen(pool_key)
    if not size:
        return None

    for _ in range(size):
        cookie_b64 = rc.lmove(pool_key, pool_key, "LEFT", "RIGHT")
        if not cookie_b64:
            break
        if rc.get(f"cookie_health:{platform}:{_hash(cookie_b64)}") != "blocked":
            return cookie_b64

    return None  # all blocked


def mark_cookie_blocked(platform: str, cookie_b64: str) -> None:
    """Mark cookie as blocked for BLOCK_TTL seconds, then auto-recover."""
    h = _hash(cookie_b64)
    get_redis().setex(f"cookie_health:{platform}:{h}", BLOCK_TTL, "blocked")
    print(f"[CookiePool] {platform} cookie {h} blocked (retry in {BLOCK_TTL}s)")


def add_cookie(platform: str, cookie_b64: str) -> int:
    """Add cookie to pool. Skips duplicates. Returns new pool size."""
    rc = get_redis()
    pool_key = f"cookie_pool:{platform}"
    existing = rc.lrange(pool_key, 0, -1)
    if cookie_b64 in existing:
        return rc.llen(pool_key)
    rc.rpush(pool_key, cookie_b64)
    rc.delete(f"cookie_health:{platform}:{_hash(cookie_b64)}")
    return rc.llen(pool_key)


def remove_cookie(platform: str, index: int) -> int:
    """Remove cookie at index from pool. Returns remaining pool size."""
    rc = get_redis()
    pool_key = f"cookie_pool:{platform}"
    sentinel = f"__del_{index}__"
    rc.lset(pool_key, index, sentinel)
    rc.lrem(pool_key, 0, sentinel)
    return rc.llen(pool_key)


def get_pool_status() -> dict:
    """Return health summary for all platforms."""
    rc = get_redis()
    result = {}
    for platform in ("youtube", "tiktok", "facebook", "instagram"):
        pool_key = f"cookie_pool:{platform}"
        cookies = rc.lrange(pool_key, 0, -1)
        blocked = sum(
            1 for c in cookies
            if rc.get(f"cookie_health:{platform}:{_hash(c)}") == "blocked"
        )
        result[platform] = {
            "total": len(cookies),
            "healthy": len(cookies) - blocked,
            "blocked": blocked,
        }
    return result
