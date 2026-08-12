"""
Phase 27 — Platform Scheduler (Lane-Aware Orchestrator)
=========================================================
Gate and route every download request through:
  1. Throughput policy check (effective RPM ceiling)
  2. Lane admission (concurrency slots per platform)
  3. Fair queue admission control (per-user cap + global depth)
  4. Cookie scored selection (best-score cookie from pool)
  5. Circuit breaker gate (block if platform circuit is OPEN)

Lane state machine:
    ACTIVE     — normal operation
    THROTTLED  — over RPM, shedding BATCH requests, jittering INTERACTIVE
    OFFLINE    — circuit OPEN, all requests return 503 immediately
    RECOVERY   — circuit HALF, only probe (1 req) allowed through

Redis keys:
    p27:lane:{platform}:state   string — ACTIVE | THROTTLED | OFFLINE | RECOVERY
    p27:lane:{platform}:ts      float  — when state last changed (for TTL / health age)

Usage in routes.py or downloader.py:
    from app.core.platform_scheduler import schedule_request, release_request

    sched = schedule_request(platform, user_id, tier, priority)
    # ... do download ...
    schedule_result(platform, sched["cookie_b64"], success=True, latency_ms=1200)
    release_request(platform, user_id, sched.get("cookie_b64"))
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

# Lane state constants
STATE_ACTIVE    = "ACTIVE"
STATE_THROTTLED = "THROTTLED"
STATE_OFFLINE   = "OFFLINE"
STATE_RECOVERY  = "RECOVERY"

_STATE_TTL = 600  # lane state key TTL (auto-reset if scheduler crashes)


class SchedulerBlocked(Exception):
    """Request cannot be dispatched right now."""
    def __init__(self, reason: str, retry_after: int = 30, platform: str = ""):
        self.reason = reason
        self.retry_after = retry_after
        self.platform = platform
        super().__init__(f"scheduler_blocked:{reason}:{platform}")


def _lk(platform: str, suffix: str) -> str:
    return f"p27:lane:{platform}:{suffix}"


def get_lane_state(platform: str) -> str:
    try:
        rc = get_redis()
        raw = rc.get(_lk(platform, "state"))
        return (raw.decode() if isinstance(raw, bytes) else raw) or STATE_ACTIVE
    except Exception:
        return STATE_ACTIVE


def _set_lane_state(platform: str, state: str) -> None:
    try:
        rc = get_redis()
        rc.setex(_lk(platform, "state"), _STATE_TTL, state)
        rc.setex(_lk(platform, "ts"), _STATE_TTL, str(time.time()))
        log.info(f"[Scheduler] {platform} lane → {state}")
    except Exception:
        pass


def _sync_lane_state(platform: str) -> str:
    """Derive lane state from circuit breaker + throttle, update if changed."""
    try:
        from app.core.platform_circuit import get_state as cb_state
        cs = cb_state(platform)
        if cs == "open":
            new_state = STATE_OFFLINE
        elif cs == "half":
            new_state = STATE_RECOVERY
        else:
            from app.core.throughput_policy import is_over_limit
            new_state = STATE_THROTTLED if is_over_limit(platform) else STATE_ACTIVE
    except Exception:
        new_state = STATE_ACTIVE

    current = get_lane_state(platform)
    if new_state != current:
        _set_lane_state(platform, new_state)
    return new_state


def schedule_request(
    platform: str,
    user_id: str = "",
    tier: str = "free",
    priority: int = 1,
) -> dict:
    """
    Gate + route a download request. Returns a context dict with the chosen
    cookie (if any). Raises SchedulerBlocked if the request cannot proceed.

    Callers MUST call release_request() in a finally block.
    """
    state = _sync_lane_state(platform)

    if state == STATE_OFFLINE:
        from app.core.platform_circuit import cooldown_remaining
        cd = cooldown_remaining(platform)
        raise SchedulerBlocked("platform_offline", retry_after=max(30, cd), platform=platform)

    if state == STATE_RECOVERY and priority >= 3:
        # Only interactive probes (priority 1–2) pass through RECOVERY
        raise SchedulerBlocked("platform_recovery_batch_blocked", retry_after=60, platform=platform)

    if state == STATE_THROTTLED and priority >= 3:
        raise SchedulerBlocked("platform_throttled", retry_after=20, platform=platform)

    # Admission control
    try:
        from app.core.fair_queue import try_admit
        try_admit(user_id, tier=tier, priority=priority)
    except Exception as e:
        if "admission_denied" in str(e) or "AdmissionDenied" in type(e).__name__:
            raise SchedulerBlocked(str(e), retry_after=getattr(e, "retry_after", 30), platform=platform)
        # Redis error → fail open

    # Lane concurrency
    try:
        from app.core.platform_lanes import try_acquire_platform, try_acquire_user
        if not try_acquire_platform(platform):
            raise SchedulerBlocked("lane_full", retry_after=5, platform=platform)
        if user_id and not try_acquire_user(user_id):
            from app.core.platform_lanes import release_platform
            release_platform(platform)
            raise SchedulerBlocked("user_lane_full", retry_after=5, platform=platform)
    except SchedulerBlocked:
        raise
    except Exception:
        pass

    # Cookie selection (scored)
    cookie_b64 = None
    try:
        from app.core.cookie_score import get_scored_cookie
        cookie_b64 = get_scored_cookie(platform)
    except Exception:
        pass

    return {
        "platform":    platform,
        "cookie_b64":  cookie_b64,
        "lane_state":  state,
        "user_id":     user_id,
        "admitted_at": time.time(),
    }


def schedule_result(
    platform: str,
    cookie_b64: Optional[str],
    success: bool,
    latency_ms: int = 0,
) -> None:
    """Record the outcome of a completed request."""
    if cookie_b64:
        try:
            from app.core.cookie_score import record_outcome
            record_outcome(platform, cookie_b64, success=success, latency_ms=latency_ms)
        except Exception:
            pass

    try:
        from app.core.platform_circuit import record_success, record_failure
        if success:
            record_success(platform)
        else:
            record_failure(platform)
    except Exception:
        pass


def release_request(platform: str, user_id: str = "", cookie_b64: Optional[str] = None) -> None:
    """Release all slots taken by schedule_request."""
    try:
        from app.core.platform_lanes import release_platform, release_user
        release_platform(platform)
        if user_id:
            release_user(user_id)
    except Exception:
        pass
    try:
        from app.core.fair_queue import release_slot
        release_slot(user_id)
    except Exception:
        pass


def get_all_lane_states() -> list[dict]:
    """Admin observability: lane state + metrics for all known platforms."""
    try:
        rc = get_redis()
        # Discover platforms from throttle keys + cookie pool keys
        throttle_keys = rc.keys("throttle:*") or []
        pool_keys     = rc.keys("cookie_pool:*") or []
        platforms = set()
        for k in throttle_keys:
            kstr = k.decode() if isinstance(k, bytes) else k
            platforms.add(kstr.split(":", 1)[1])
        for k in pool_keys:
            kstr = k.decode() if isinstance(k, bytes) else k
            platforms.add(kstr.split(":", 1)[1])

        result = []
        for p in sorted(platforms):
            state = _sync_lane_state(p)
            ts_raw = rc.get(_lk(p, "ts"))
            ts = float(ts_raw) if ts_raw else 0
            rpm  = int(rc.get(f"throttle:{p}") or 0)
            lane = int(rc.get(f"lane:{p}") or 0)
            from app.core.throughput_policy import get_effective_rpm
            ceiling = get_effective_rpm(p)
            result.append({
                "platform":    p,
                "state":       state,
                "state_age_s": round(time.time() - ts) if ts else None,
                "rpm_current": rpm,
                "rpm_ceiling": ceiling,
                "lane_active": lane,
            })
        return result
    except Exception as e:
        log.warning(f"[Scheduler] get_all_lane_states error: {e}")
        return []
