"""
Per-Platform Concurrency Lanes + Per-User Cap
=============================================
Redis-backed semaphores so concurrent downloads are isolated PER platform
instead of sharing one global pool. A TikTok flood can no longer starve
YouTube/Spotify, and one platform's downstream identity (cookie/proxy/IP)
isn't overwhelmed by a burst.

Also a per-user active-download cap so one user can't monopolize all workers
during peak hours (fairness).

All counters auto-expire (TTL) so a crashed worker can't leak a slot forever.

Tunables (env): LANE_<PLATFORM> (e.g. LANE_YOUTUBE=4), LANE_DEFAULT,
USER_MAX_CONCURRENT, LANE_TTL.
"""

import os
from app.core.redis_client import get_redis

# Soft splits across the 8-worker pool — generous so normal load is untouched;
# they only bite under heavy concurrent bursts.
_LANES = {
    "youtube":   int(os.getenv("LANE_YOUTUBE",   "4")),
    "tiktok":    int(os.getenv("LANE_TIKTOK",    "8")),
    "facebook":  int(os.getenv("LANE_FACEBOOK",  "4")),
    "instagram": int(os.getenv("LANE_INSTAGRAM", "3")),
    "spotify":   int(os.getenv("LANE_SPOTIFY",   "4")),
    "soundcloud":int(os.getenv("LANE_SOUNDCLOUD","6")),
    "twitter":   int(os.getenv("LANE_TWITTER",   "4")),
    "threads":   int(os.getenv("LANE_THREADS",   "4")),
    "douyin":    int(os.getenv("LANE_DOUYIN",    "4")),
    "pinterest": int(os.getenv("LANE_PINTEREST", "6")),
    "reddit":    int(os.getenv("LANE_REDDIT",    "5")),
}
_DEFAULT_LANE = int(os.getenv("LANE_DEFAULT", "6"))
_USER_MAX     = int(os.getenv("USER_MAX_CONCURRENT", "6"))
_TTL          = int(os.getenv("LANE_TTL", "600"))  # safety auto-release (10 min)


def _limit(platform: str) -> int:
    return _LANES.get(platform, _DEFAULT_LANE)


def try_acquire_platform(platform: str) -> bool:
    """Try to take one slot in a platform's lane. False if the lane is full."""
    try:
        rc = get_redis()
        key = f"lane:{platform}"
        n = rc.incr(key)
        rc.expire(key, _TTL)
        if n > _limit(platform):
            rc.decr(key)
            return False
        return True
    except Exception:
        return True  # fail-open: never block downloads on Redis trouble


def release_platform(platform: str) -> None:
    try:
        rc = get_redis()
        if rc.decr(f"lane:{platform}") < 0:
            rc.set(f"lane:{platform}", 0)
    except Exception:
        pass


def try_acquire_user(user_id: str) -> bool:
    """Per-user concurrent-download cap (no-op for anonymous)."""
    if not user_id:
        return True
    try:
        rc = get_redis()
        key = f"user_active:{user_id}"
        n = rc.incr(key)
        rc.expire(key, _TTL)
        if n > _USER_MAX:
            rc.decr(key)
            return False
        return True
    except Exception:
        return True


def release_user(user_id: str) -> None:
    if not user_id:
        return
    try:
        rc = get_redis()
        if rc.decr(f"user_active:{user_id}") < 0:
            rc.set(f"user_active:{user_id}", 0)
    except Exception:
        pass
