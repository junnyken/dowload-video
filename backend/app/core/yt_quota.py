"""
YouTube download quota
======================
YouTube is the only platform that costs paid residential-proxy bandwidth, so we
cap it (other platforms are unaffected):

  • Anonymous (by IP)   : YT_QUOTA_ANON  videos/day (default 1), NO channel.
  • Registered (account): YT_QUOTA_USER  videos/day (default 5) + channel allowed.

Counter resets daily (Asia/Bangkok, UTC+7). Redis-backed, fail-open (never block
a download on a Redis hiccup). reserve() takes a slot up-front; refund() returns
it if the download fails so a failure doesn't burn the user's daily allowance.
"""

import os
from datetime import datetime, timezone, timedelta
from app.core.redis_client import get_redis

YT_QUOTA_ANON = int(os.getenv("YT_QUOTA_ANON", "1"))
YT_QUOTA_USER = int(os.getenv("YT_QUOTA_USER", "5"))
# Site-wide hard ceiling on YouTube proxy downloads/day (budget protector).
# 0 = unlimited. Counts video-equivalents across ALL users.
YT_GLOBAL_DAILY_CAP = int(os.getenv("YT_GLOBAL_DAILY_CAP", "300"))
_TZ = timezone(timedelta(hours=7))  # reset boundary = VN midnight


def yt_identity(user_id, ip) -> str:
    return f"u:{user_id}" if user_id else f"ip:{ip or 'unknown'}"


def yt_limit(is_registered: bool) -> int:
    return YT_QUOTA_USER if is_registered else YT_QUOTA_ANON


def _key(identity: str) -> str:
    day = datetime.now(_TZ).strftime("%Y%m%d")
    return f"ytq:{identity}:{day}"


def reserve(identity: str, is_registered: bool):
    """Reserve one daily YouTube slot. Returns (allowed, used, limit)."""
    limit = yt_limit(is_registered)
    try:
        rc = get_redis()
        k = _key(identity)
        n = int(rc.incr(k))
        if n == 1:
            rc.expire(k, 90000)  # ~25h safety
        if n > limit:
            rc.decr(k)
            return (False, limit, limit)
        return (True, n, limit)
    except Exception:
        return (True, 0, limit)  # fail-open


def refund(identity: str) -> None:
    try:
        rc = get_redis()
        k = _key(identity)
        if int(rc.decr(k)) < 0:
            rc.set(k, 0)
    except Exception:
        pass


def _global_key() -> str:
    return f"ytq:global:{datetime.now(_TZ).strftime('%Y%m%d')}"


def global_reserve():
    """Reserve one slot in the site-wide daily YouTube budget. (ok, used, cap)."""
    if YT_GLOBAL_DAILY_CAP <= 0:
        return (True, 0, 0)
    try:
        rc = get_redis()
        k = _global_key()
        n = int(rc.incr(k))
        if n == 1:
            rc.expire(k, 90000)
        if n > YT_GLOBAL_DAILY_CAP:
            rc.decr(k)
            return (False, YT_GLOBAL_DAILY_CAP, YT_GLOBAL_DAILY_CAP)
        return (True, n, YT_GLOBAL_DAILY_CAP)
    except Exception:
        return (True, 0, 0)


def global_refund() -> None:
    if YT_GLOBAL_DAILY_CAP <= 0:
        return
    try:
        rc = get_redis()
        k = _global_key()
        if int(rc.decr(k)) < 0:
            rc.set(k, 0)
    except Exception:
        pass

