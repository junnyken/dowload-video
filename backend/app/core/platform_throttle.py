"""
Platform Rate Limiter
=====================
Redis sliding-window rate limiter per platform.

Problem: 8 Celery workers can all hit the same platform (TikTok/Instagram)
simultaneously → burst triggers IP-level rate limiting faster.

Solution: per-platform request counter in Redis with a sliding 60s window.
If a platform is over its limit, the caller sleeps with jitter rather than
firing blind and getting a 429.

Limits (requests per 60s across ALL workers):
  youtube   — 60  (very lenient; PO token + cookies handle most throttling)
  tiktok    — 20  (aggressive rate limiting; 20 req/min is safe with cookies)
  facebook  — 15  (session-based; cookies help but still strict)
  instagram — 10  (strictest — easily triggers 429 and 24h block)
  default   — 30

When limit hit: sleep up to MAX_WAIT_S with jitter, then retry once.
If still throttled: raise PlatformThrottleError so the task can retry later.
"""

import time
import random
import logging
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

# Per-platform req/min limits (tune based on observed 429 patterns)
_LIMITS: dict[str, int] = {
    "youtube":   60,
    "tiktok":    20,
    "facebook":  15,
    "instagram": 10,
}
_DEFAULT_LIMIT = 30
_WINDOW_S      = 60    # sliding window in seconds
_MAX_WAIT_S    = 8     # max sleep before giving up


class PlatformThrottleError(Exception):
    """Raised when a platform is still throttled after MAX_WAIT_S."""


def check_and_acquire(platform: str) -> None:
    """
    Increment platform counter. Sleep+retry if over limit.
    Raises PlatformThrottleError if still throttled after waiting.
    """
    limit = _LIMITS.get(platform, _DEFAULT_LIMIT)
    key   = f"throttle:{platform}"

    try:
        rc = get_redis()
        pipe = rc.pipeline()
        pipe.incr(key)
        pipe.expire(key, _WINDOW_S)
        count, _ = pipe.execute()

        if count <= limit:
            return  # under limit, proceed

        # Over limit — calculate wait with jitter
        ttl     = rc.ttl(key)
        overage = count - limit
        wait    = min(_MAX_WAIT_S, max(1.0, ttl / max(overage, 1)))
        jitter  = random.uniform(0.1, 0.5)
        sleep_s = wait + jitter

        log.warning(
            f"[Throttle] {platform} over limit ({count}/{limit}/60s) — sleeping {sleep_s:.1f}s"
        )
        time.sleep(sleep_s)

        # Re-check after sleep
        count2 = int(rc.get(key) or 0)
        if count2 > limit * 1.5:
            raise PlatformThrottleError(
                f"{platform} still throttled ({count2}/{limit}/60s). Task will retry."
            )

    except PlatformThrottleError:
        raise
    except Exception as e:
        # Redis error — don't block the download, just warn
        log.warning(f"[Throttle] Redis error for {platform}: {e}")


def get_platform_rate(platform: str) -> dict:
    """Return current rate info for admin display."""
    try:
        rc = get_redis()
        key   = f"throttle:{platform}"
        count = int(rc.get(key) or 0)
        ttl   = rc.ttl(key)
        limit = _LIMITS.get(platform, _DEFAULT_LIMIT)
        return {
            "platform": platform,
            "requests_in_window": count,
            "limit": limit,
            "window_s": _WINDOW_S,
            "ttl_s": ttl,
            "utilization_pct": round(count / limit * 100) if limit else 0,
        }
    except Exception:
        return {"platform": platform, "requests_in_window": 0, "limit": _LIMITS.get(platform, _DEFAULT_LIMIT)}


def get_all_rates() -> list[dict]:
    return [get_platform_rate(p) for p in ("youtube", "tiktok", "facebook", "instagram")]
