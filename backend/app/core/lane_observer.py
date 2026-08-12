"""
Phase 27A — Lane Observer
===========================
Pure read aggregator: composes existing signals (cookie pool, circuit breaker,
throttle, metrics, obs_recorder timestamps) into a structured LaneObservation
per platform.

NO scheduling behavior changes — this module only READS data.
All writes are in obs_recorder.py.

Lane states:
  healthy     — circuit closed, ≥1 healthy cookie, throttle < 80%
  constrained — 0 healthy cookies (all cooling/soft-blocked) OR throttle 80–100%
  degraded    — hard block > 0 OR fail_rate_1h > 40% OR all cookies soft-blocked
  paused      — circuit OPEN
  disabled    — circuit OPEN AND pool empty

Probe platforms that need cookies or proxy:
  COOKIE_REQUIRED = {instagram, twitter, reddit, facebook, youtube, bilibili}
  PROXY_REQUIRED  = {youtube, instagram, twitter, douyin, bilibili}
"""

from __future__ import annotations

import time
import logging
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

# Platform metadata (used for context flags only — no routing decisions)
COOKIE_REQUIRED: set[str] = {
    "instagram", "twitter", "reddit", "facebook", "youtube",
    "bilibili", "xiaohongshu", "lemon8",
}
PROXY_REQUIRED: set[str] = {
    "youtube", "instagram", "twitter", "douyin", "bilibili",
}

# Platform base RPM (mirrors throughput_policy.py defaults — read-only)
_BASE_RPM: dict[str, float] = {
    "youtube": 40, "tiktok": 30, "facebook": 20, "instagram": 8,
    "twitter": 15, "reddit": 25, "bilibili": 20, "spotify": 60,
    "soundcloud": 50, "threads": 15, "douyin": 25, "pinterest": 30,
}
_DEFAULT_RPM = 20.0


def _derive_lane_state(
    circuit_state: str,
    healthy: int,
    cooling: int,
    soft_blocked: int,
    hard_blocked: int,
    total_cookies: int,
    throttle_pct: float,
    fail_rate_1h: float,
) -> tuple[str, Optional[str]]:
    """
    Returns (laneState, constrainedReason).
    Pure deterministic derivation from observed signals.
    """
    if circuit_state == "open":
        if total_cookies == 0:
            return "disabled", "circuit_open"
        return "paused", "circuit_open"

    if circuit_state == "half":
        return "constrained", "circuit_recovering"

    # Hard block present → degraded
    if hard_blocked > 0 and healthy == 0:
        return "degraded", "all_cookies_hard_blocked"

    # High failure rate → degraded
    if fail_rate_1h > 40:
        return "degraded", f"high_fail_rate_{int(fail_rate_1h)}pct"

    # All cookies blocked (not just cooling)
    if healthy == 0 and soft_blocked > 0 and cooling == 0:
        return "degraded", "all_cookies_soft_blocked"

    # No healthy cookies but some are just cooling → constrained
    if healthy == 0 and cooling > 0:
        return "constrained", "all_cookies_cooling"

    # No cookies at all and circuit is closed
    if total_cookies == 0:
        return "constrained", "no_cookies"

    # Throttle pressure
    if throttle_pct >= 100:
        return "constrained", "throttle_over_limit"
    if throttle_pct >= 80:
        return "constrained", "throttle_near_limit"

    return "healthy", None


def _get_fail_rate_1h(platform: str) -> float:
    """Returns failure rate % over last 1h from existing vg:m:j metrics."""
    try:
        from app.core.metrics import get_job_metrics
        m = get_job_metrics(window_minutes=60)
        pdata = m.get("by_platform", {}).get(platform, {})
        ok  = pdata.get("succeeded", 0)
        err = pdata.get("failed", 0)
        total = ok + err
        return round(err / total * 100, 1) if total > 0 else 0.0
    except Exception:
        return 0.0


def _get_effective_rpm_estimate(
    platform: str,
    healthy_cookies: int,
    circuit_state: str,
    proxy_available: bool,
    allbusy_rate: float,
) -> float:
    """Conservative read-only RPM estimate for display only."""
    base = _BASE_RPM.get(platform, _DEFAULT_RPM)
    # Cookie headroom: each healthy cookie adds ~35% capacity, capped at 3×
    cookie_mult = min(3.0, 1.0 + max(0, healthy_cookies - 1) * 0.35)
    # Proxy factor
    proxy_factor = 1.0 if (platform not in PROXY_REQUIRED or proxy_available) else 0.5
    # Circuit factor
    circuit_factor = {"closed": 1.0, "half": 0.2, "open": 0.0}.get(circuit_state, 1.0)
    # Pressure penalty
    pressure = max(0.0, 1.0 - allbusy_rate * 0.3)

    return round(base * cookie_mult * proxy_factor * circuit_factor * pressure, 1)


def _get_avg_wait_estimate(
    platform: str,
    healthy_cookies: int,
    active_jobs: int,
) -> float:
    """Heuristic avg wait estimate in seconds.  Display only."""
    try:
        from app.core.metrics import get_job_metrics
        m = get_job_metrics(window_minutes=30)
        # Estimate avg runtime from succeeded events (use 45s default)
        succeeded = m.get("by_event", {}).get("succeeded", 0)
        # We don't have per-platform avg runtime yet — 27A records it, 27B uses it
        avg_runtime_s = 40.0
        if healthy_cookies == 0:
            # All cooling — wait for shortest cooldown as proxy
            try:
                rc = get_redis()
                from app.core.cookie_pool import _hash, _COOLDOWN, _DEFAULT_COOLDOWN
                cookies = rc.lrange(f"cookie_pool:{platform}", 0, -1)
                min_cd = min(
                    (rc.ttl(f"cookie_cooldown:{platform}:{_hash(c)}") or 0)
                    for c in cookies
                ) if cookies else 30
                return float(max(0, min_cd))
            except Exception:
                return 20.0
        if active_jobs == 0:
            return 0.0
        return round(active_jobs * avg_runtime_s / max(4, 1), 1)
    except Exception:
        return 0.0


def _get_proxy_available(platform: str) -> bool:
    if platform not in PROXY_REQUIRED:
        return True
    try:
        from app.core.proxy_manager import get_proxy_for_platform
        return bool(get_proxy_for_platform(platform))
    except Exception:
        return True  # unknown → assume available


def observe_platform(platform: str) -> dict:
    """
    Build a LaneObservation dict for one platform.
    All data from Redis reads — no side effects.
    """
    rc = get_redis()

    # ── Cookie pool health ───────────────────────────────────────────────────
    from app.core.cookie_pool import _hash, get_pool_status, get_expiry_report
    pool_status = get_pool_status()
    pstatus = pool_status.get(platform, {})
    total_cookies   = pstatus.get("total", 0)
    healthy_cookies = pstatus.get("healthy", 0)
    cooling_cookies = pstatus.get("in_cooldown", 0)
    soft_blocked    = pstatus.get("soft_blocked", 0)
    hard_blocked    = pstatus.get("hard_blocked", 0)
    # Expired cookies: parse expiry report
    try:
        expiry_report = get_expiry_report(platform)
        expired_cookies = sum(1 for e in expiry_report if e.get("expiry_status") == "expired")
    except Exception:
        expired_cookies = 0

    # ── Circuit breaker ──────────────────────────────────────────────────────
    try:
        from app.core.platform_circuit import get_state, cooldown_remaining
        circuit_state = get_state(platform)
    except Exception:
        circuit_state = "closed"

    # ── Throttle utilization ─────────────────────────────────────────────────
    try:
        from app.core.platform_throttle import get_platform_rate
        rate = get_platform_rate(platform)
        throttle_pct = rate.get("utilization_pct", 0)
    except Exception:
        throttle_pct = 0.0

    # ── Active jobs + queue depth ────────────────────────────────────────────
    try:
        active_jobs = int(rc.get(f"lane:{platform}") or 0)
    except Exception:
        active_jobs = 0
    # Queue depth: approximate from celery depth attributed to platform
    # 27A heuristic: total celery depth × platform share of started events
    try:
        celery_depth = int(rc.llen("celery") or 0)
        from app.core.metrics import get_job_metrics
        m = get_job_metrics(window_minutes=5)
        pstarted = m.get("by_platform", {}).get(platform, {}).get("started", 0)
        total_started = max(m.get("by_event", {}).get("started", 0), 1)
        queue_depth = max(0, round(celery_depth * pstarted / total_started))
    except Exception:
        queue_depth = 0

    # ── Failure rate ─────────────────────────────────────────────────────────
    fail_rate_1h = _get_fail_rate_1h(platform)

    # ── Lane state derivation ────────────────────────────────────────────────
    lane_state, constrained_reason = _derive_lane_state(
        circuit_state, healthy_cookies, cooling_cookies,
        soft_blocked, hard_blocked, total_cookies,
        throttle_pct, fail_rate_1h,
    )

    # ── Observability recorder data ──────────────────────────────────────────
    from app.core.obs_recorder import (
        get_failure_buckets, get_allbusy_count_1h, get_last_timestamps
    )
    failure_buckets = get_failure_buckets(platform)
    allbusy_1h      = get_allbusy_count_1h(platform)
    timestamps      = get_last_timestamps(platform)

    # ── Capacity estimates ───────────────────────────────────────────────────
    proxy_available   = _get_proxy_available(platform)
    allbusy_rate      = min(1.0, allbusy_1h / 60)  # normalize to events/min
    effective_rpm     = _get_effective_rpm_estimate(
        platform, healthy_cookies, circuit_state, proxy_available, allbusy_rate
    )
    avg_wait          = _get_avg_wait_estimate(platform, healthy_cookies, active_jobs)

    return {
        "platform":             platform,
        "laneState":            lane_state,
        "constrainedReason":    constrained_reason,
        "activeJobs":           active_jobs,
        "queueDepth":           queue_depth,
        "healthyCookies":       healthy_cookies,
        "coolingCookies":       cooling_cookies,
        "softBlockedCookies":   soft_blocked,
        "hardBlockedCookies":   hard_blocked,
        "expiredCookies":       expired_cookies,
        "allBusyCount1h":       allbusy_1h,
        "cookieRequired":       platform in COOKIE_REQUIRED,
        "proxyRequired":        platform in PROXY_REQUIRED,
        "effectiveRpmEstimate": effective_rpm,
        "avgWaitSecEstimate":   avg_wait,
        "recentFailureReasons": failure_buckets,
        "lastSuccessAt":        timestamps["lastSuccessAt"],
        "lastFailureAt":        timestamps["lastFailureAt"],
        "circuitState":         circuit_state,
        "throttleUtilizationPct": throttle_pct,
    }


def observe_all_platforms() -> list[dict]:
    """
    Observe all platforms that have any data in Redis (cookie pool or throttle keys).
    Falls back to the hardcoded major platform list if Redis is empty.
    """
    _MAJOR = [
        "youtube", "tiktok", "facebook", "instagram",
        "twitter", "reddit", "bilibili", "spotify",
        "soundcloud", "threads", "douyin",
    ]
    try:
        rc = get_redis()
        pool_keys    = rc.keys("cookie_pool:*") or []
        throttle_keys = rc.keys("throttle:*") or []
        platforms = set()
        for k in pool_keys + throttle_keys:
            kstr = k.decode() if isinstance(k, bytes) else k
            platforms.add(kstr.split(":", 1)[1])
        if not platforms:
            platforms = set(_MAJOR)
    except Exception:
        platforms = set(_MAJOR)

    result = []
    for p in sorted(platforms):
        try:
            result.append(observe_platform(p))
        except Exception as e:
            log.warning(f"[lane_observer] observe_platform({p}) error: {e}")
    return result
