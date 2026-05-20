"""
Proxy Pool Manager
==================
Random rotating proxy pool per platform.

Sources (priority order):
  1. Redis pool: proxy_pool:{platform} LIST
  2. Env var: PROXY_POOL_{PLATFORM} (comma-separated proxy URLs)

Proxy URL format: http://user:pass@host:port
Platform → env var mapping:
  youtube   → PROXY_POOL_YT
  tiktok    → PROXY_POOL_TT
  facebook  → PROXY_POOL_FB
  instagram → PROXY_POOL_IG
  douyin    → PROXY_POOL_CN
  twitter   → PROXY_POOL_TW
  default   → PROXY_POOL_DEFAULT
"""

import os
import random
from typing import Optional

from app.core.redis_client import get_redis

_ENV_VARS = {
    "youtube":   "PROXY_POOL_YT",
    "tiktok":    "PROXY_POOL_TT",
    "facebook":  "PROXY_POOL_FB",
    "instagram": "PROXY_POOL_IG",
    "douyin":    "PROXY_POOL_CN",
    "twitter":   "PROXY_POOL_TW",
    "default":   "PROXY_POOL_DEFAULT",
}


def _env_proxies(platform: str) -> list[str]:
    key = _ENV_VARS.get(platform) or _ENV_VARS["default"]
    raw = os.getenv(key, "") or os.getenv(_ENV_VARS["default"], "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_proxy_from_pool(platform: str) -> Optional[str]:
    """Pick a random proxy for the platform. Returns None for direct connection."""
    rc = get_redis()
    pool_key = f"proxy_pool:{platform}"
    size = rc.llen(pool_key)

    if size > 0:
        idx = random.randint(0, size - 1)
        proxy = rc.lindex(pool_key, idx)
        if proxy:
            return proxy.strip()

    env = _env_proxies(platform)
    return random.choice(env) if env else None


def add_proxy(platform: str, proxy_url: str) -> int:
    """Add proxy to Redis pool. Skips duplicates. Returns new pool size."""
    rc = get_redis()
    pool_key = f"proxy_pool:{platform}"
    existing = rc.lrange(pool_key, 0, -1)
    if proxy_url in existing:
        return rc.llen(pool_key)
    rc.rpush(pool_key, proxy_url)
    return rc.llen(pool_key)


def remove_proxy(platform: str, index: int) -> int:
    """Remove proxy at index. Returns remaining pool size."""
    rc = get_redis()
    pool_key = f"proxy_pool:{platform}"
    sentinel = f"__del_{index}__"
    rc.lset(pool_key, index, sentinel)
    rc.lrem(pool_key, 0, sentinel)
    return rc.llen(pool_key)


def get_pool_status() -> dict:
    """Return count of proxies per platform (Redis + env)."""
    rc = get_redis()
    result = {}
    for platform in ("youtube", "tiktok", "facebook", "instagram", "douyin", "twitter"):
        redis_count = rc.llen(f"proxy_pool:{platform}")
        env_count = len(_env_proxies(platform))
        result[platform] = {
            "redis_pool": redis_count,
            "env_fallback": env_count,
            "total": redis_count + env_count,
        }
    return result
