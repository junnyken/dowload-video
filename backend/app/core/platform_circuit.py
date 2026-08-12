"""
Per-Platform Circuit Breaker
============================
Generalizes the Oracle circuit breaker to EVERY platform (tiktok, youtube,
facebook, instagram, threads, spotify, soundcloud, twitter, douyin, …).

Goal: when a platform starts rate-limiting / challenging us (429, bot wall,
"login required"), STOP hammering it for a cooldown so we don't escalate a
temporary throttle into a hard ban that kills the feature for ALL users.

3 states (Redis-backed, shared across backend + celery):
  CLOSED → normal.
  OPEN   → too many blocks recently → reject new requests for a cooldown.
  HALF   → after cooldown, allow ONE probe; success→CLOSED, fail→OPEN.

Tunables (env):
  PLATFORM_CB_WINDOW     fail-count sliding window (s, default 300)
  PLATFORM_CB_THRESHOLD  fails within window → OPEN (default 5)
  PLATFORM_CB_COOLDOWN   seconds to stay OPEN before HALF (default 300)
"""

import os
import time
from app.core.redis_client import get_redis

_FAIL_WINDOW    = int(os.getenv("PLATFORM_CB_WINDOW", "300"))
_FAIL_THRESHOLD = int(os.getenv("PLATFORM_CB_THRESHOLD", "5"))
_OPEN_DURATION  = int(os.getenv("PLATFORM_CB_COOLDOWN", "300"))

# Platforms that ride a third-party proxy which absorbs reputation (TikWM) or
# are low-risk direct: don't trip the breaker (a TikWM hiccup ≠ TikTok banning us).
_EXEMPT = {"tiktok", "douyin", "soundcloud", "pinterest"}


def _k(platform: str, suffix: str) -> str:
    return f"pcircuit:{platform}:{suffix}"


def get_state(platform: str) -> str:
    try:
        rc = get_redis()
        raw = rc.get(_k(platform, "state"))
        state = (raw.decode() if isinstance(raw, bytes) else raw) or "closed"
        if state == "open":
            since = rc.get(_k(platform, "open_since"))
            if since:
                since = float(since.decode() if isinstance(since, bytes) else since)
                if time.time() - since >= _OPEN_DURATION:
                    rc.set(_k(platform, "state"), "half")
                    print(f"[PlatformCircuit] {platform} OPEN → HALF (cooldown elapsed)")
                    return "half"
        return state
    except Exception:
        return "closed"  # fail-safe: never block on Redis trouble


def is_open(platform: str) -> bool:
    if platform in _EXEMPT:
        return False
    return get_state(platform) == "open"


def cooldown_remaining(platform: str) -> int:
    try:
        rc = get_redis()
        since = rc.get(_k(platform, "open_since"))
        if since:
            since = float(since.decode() if isinstance(since, bytes) else since)
            return max(0, int(_OPEN_DURATION - (time.time() - since)))
    except Exception:
        pass
    return 0


def record_success(platform: str) -> None:
    try:
        rc = get_redis()
        state = get_state(platform)
        if state in ("half", "open"):
            rc.set(_k(platform, "state"), "closed")
            rc.delete(_k(platform, "fails"), _k(platform, "open_since"))
            print(f"[PlatformCircuit] {platform} → CLOSED (probe/success)")
        else:
            rc.delete(_k(platform, "fails"))
    except Exception:
        pass


def record_failure(platform: str) -> None:
    """Record a block/rate-limit/challenge failure for a platform."""
    if platform in _EXEMPT:
        return
    try:
        rc = get_redis()
        state = get_state(platform)
        if state == "half":
            rc.set(_k(platform, "state"), "open")
            rc.set(_k(platform, "open_since"), str(time.time()))
            rc.set(_k(platform, "fails"), "0")
            print(f"[PlatformCircuit] {platform} HALF → OPEN (probe failed)")
            return
        pipe = rc.pipeline()
        pipe.incr(_k(platform, "fails"))
        pipe.expire(_k(platform, "fails"), _FAIL_WINDOW)
        fails = pipe.execute()[0]
        if fails >= _FAIL_THRESHOLD:
            rc.set(_k(platform, "state"), "open")
            rc.set(_k(platform, "open_since"), str(time.time()))
            print(f"[PlatformCircuit] {platform} CLOSED → OPEN ({fails} fails in {_FAIL_WINDOW}s)")
    except Exception:
        pass


class PlatformCircuitOpen(Exception):
    """Raised when a platform's circuit is OPEN (temporarily paused)."""
    def __init__(self, platform: str, cooldown: int = 0):
        self.platform = platform
        self.cooldown = cooldown
        super().__init__(f"platform_circuit_open:{platform}")
