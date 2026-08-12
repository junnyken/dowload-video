"""
Phase 27C — Admission Control
===============================
Composes lane state (27A), per-user fairness caps (27C), and circuit state into
a single 4-outcome admission decision evaluated before each download request.

Outcomes:
  ACCEPT_IMMEDIATE         — proceed normally
  ACCEPT_DELAYED           — accepted but queued; caller should return HTTP 202
  REJECT_TEMPORARY         — over cap; retry_after=30s; HTTP 429
  REJECT_PLATFORM_UNAVAILABLE — circuit open or platform disabled; HTTP 503

Feature flags:
  ADMISSION_CONTROL_ENABLED=false  — master switch (default OFF, fail-open)
  DELAYED_ACCEPT_ENABLED=false     — when false, constrained → REJECT_TEMPORARY

Redis keys (p27c:):
  p27c:admit:{platform}   HASH  immediate/delayed/temp_reject/unavail  (daily counters)
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# ── Feature flags ─────────────────────────────────────────────────────────────
_ENABLED      = os.getenv("ADMISSION_CONTROL_ENABLED", "false").lower() in ("1", "true", "yes")
_DELAYED_OK   = os.getenv("DELAYED_ACCEPT_ENABLED",    "false").lower() in ("1", "true", "yes")
_SHADOW_LOG   = os.getenv("FAIRNESS_SHADOW_LOG",       "false").lower() in ("1", "true", "yes")

ADMIT_IMMEDIATE  = "ACCEPT_IMMEDIATE"
ADMIT_DELAYED    = "ACCEPT_DELAYED"
REJECT_TEMP      = "REJECT_TEMPORARY"
REJECT_UNAVAIL   = "REJECT_PLATFORM_UNAVAILABLE"


@dataclass
class AdmissionResult:
    decision:              str            # one of the four constants above
    reason:                str            # machine-readable reason code
    delayed:               bool           # True iff ACCEPT_DELAYED
    estimated_wait_sec:    Optional[int]  # non-None only when delayed=True
    retry_after:           int            # seconds until client should retry (0 = no advice)
    fairness_pressure:     float          # 0.0–1.0; 1.0 = fully saturated
    user_active_platform:  int
    platform_active_limit: int
    shadow:                bool = False   # True iff shadow mode overrode to ACCEPT_IMMEDIATE

    @property
    def http_status(self) -> int:
        if self.decision == ADMIT_IMMEDIATE:
            return 200
        if self.decision == ADMIT_DELAYED:
            return 202
        if self.decision == REJECT_TEMP:
            return 429
        return 503  # REJECT_PLATFORM_UNAVAILABLE

    def to_dict(self) -> dict:
        return {
            "admissionDecision":    self.decision,
            "admissionReason":      self.reason,
            "delayed":              self.delayed,
            "estimatedWaitSec":     self.estimated_wait_sec,
            "retryAfter":           self.retry_after,
            "fairnessPressure":     round(self.fairness_pressure, 3),
            "userActivePlatform":   self.user_active_platform,
            "platformActiveLimit":  self.platform_active_limit,
            "canRetry":             self.decision in (REJECT_TEMP, ADMIT_DELAYED),
            "shadow":               self.shadow,
        }


_IMMEDIATE_PASS = AdmissionResult(
    decision=ADMIT_IMMEDIATE,
    reason="admission_disabled",
    delayed=False,
    estimated_wait_sec=None,
    retry_after=0,
    fairness_pressure=0.0,
    user_active_platform=0,
    platform_active_limit=999,
)


def evaluate(
    platform: str,
    user_key: str,
    is_batch: bool = False,
) -> AdmissionResult:
    """
    Evaluate admission for a download request.
    Returns ACCEPT_IMMEDIATE on any error (fail-open).

    Call BEFORE acquiring any resource slots.
    """
    if not _ENABLED:
        return _IMMEDIATE_PASS

    try:
        return _evaluate_inner(platform, user_key, is_batch)
    except Exception as e:
        log.warning("[Admission] evaluate error for %s/%s: %s", platform, user_key[:8], e)
        return _IMMEDIATE_PASS


def _evaluate_inner(platform: str, user_key: str, is_batch: bool) -> AdmissionResult:
    from app.core.fairness_control import check_user_cap, get_cap, is_fairness_enabled
    from app.core.redis_client import get_redis

    fairness_active = is_fairness_enabled(platform)
    cap = get_cap(platform)
    hard_cap = cap.batch if is_batch else cap.single
    soft_cap = cap.soft_batch if is_batch else cap.soft_cap

    # ── 1. Check lane / circuit state ─────────────────────────────────
    circuit_state = "closed"
    lane_state    = "healthy"
    try:
        from app.core.lane_observer import observe_platform
        obs = observe_platform(platform)
        circuit_state = obs.get("circuitState", "closed").lower()
        lane_state    = obs.get("laneState", "healthy")
    except Exception:
        pass

    # Circuit open → unavailable
    if circuit_state == "open":
        _count_decision(platform, "unavail")
        return AdmissionResult(
            decision=REJECT_UNAVAIL, reason="circuit_open",
            delayed=False, estimated_wait_sec=None,
            retry_after=120, fairness_pressure=1.0,
            user_active_platform=0, platform_active_limit=hard_cap,
        )

    # Platform lane disabled
    if lane_state == "disabled":
        _count_decision(platform, "unavail")
        return AdmissionResult(
            decision=REJECT_UNAVAIL, reason="platform_disabled",
            delayed=False, estimated_wait_sec=None,
            retry_after=300, fairness_pressure=1.0,
            user_active_platform=0, platform_active_limit=hard_cap,
        )

    # ── 2. Per-user per-platform fairness cap ─────────────────────────
    user_active    = 0
    over_hard      = False
    over_soft      = False
    pressure       = 0.0

    if fairness_active:
        cap_status = check_user_cap(platform, user_key, is_batch)
        user_active = cap_status["active"]
        over_hard   = cap_status["over_hard"]
        over_soft   = cap_status["over_soft"]
        pressure    = min(1.0, user_active / max(1, hard_cap))

    # Hard cap → temporary reject
    if over_hard:
        _count_decision(platform, "temp_reject")
        result = AdmissionResult(
            decision=REJECT_TEMP, reason="user_cap_reached",
            delayed=False, estimated_wait_sec=None,
            retry_after=30, fairness_pressure=pressure,
            user_active_platform=user_active, platform_active_limit=hard_cap,
        )
        if _SHADOW_LOG:
            log.info("[Admission:shadow:%s] would_reject=%s reason=%s user=%s** active=%d/%d",
                     platform, REJECT_TEMP, "user_cap_reached", user_key[:8], user_active, hard_cap)
            result.shadow = True
            result.decision = ADMIT_IMMEDIATE
        return result

    # ── 3. Lane degraded/paused + over soft cap → delayed ─────────────
    if lane_state in ("degraded", "paused") and over_soft:
        est = _estimate_wait(platform)
        if _DELAYED_OK and not _SHADOW_LOG:
            _count_decision(platform, "delayed")
            return AdmissionResult(
                decision=ADMIT_DELAYED, reason=f"lane_{lane_state}_soft_cap",
                delayed=True, estimated_wait_sec=est,
                retry_after=max(10, est or 30), fairness_pressure=pressure,
                user_active_platform=user_active, platform_active_limit=hard_cap,
            )
        # Delayed not enabled → reject temporary
        _count_decision(platform, "temp_reject")
        result = AdmissionResult(
            decision=REJECT_TEMP, reason=f"lane_{lane_state}",
            delayed=False, estimated_wait_sec=None,
            retry_after=max(20, est or 30), fairness_pressure=pressure,
            user_active_platform=user_active, platform_active_limit=hard_cap,
        )
        if _SHADOW_LOG:
            log.info("[Admission:shadow:%s] would=%s reason=%s user=%s**",
                     platform, result.decision, result.reason, user_key[:8])
            result.shadow = True
            result.decision = ADMIT_IMMEDIATE
        return result

    # ── 4. Constrained lane + over soft cap → delayed ─────────────────
    if lane_state == "constrained" and over_soft:
        est = _estimate_wait(platform)
        if _DELAYED_OK and not _SHADOW_LOG:
            _count_decision(platform, "delayed")
            return AdmissionResult(
                decision=ADMIT_DELAYED, reason="lane_constrained_soft_cap",
                delayed=True, estimated_wait_sec=est,
                retry_after=max(10, est or 20), fairness_pressure=pressure,
                user_active_platform=user_active, platform_active_limit=hard_cap,
            )
        # No delayed → temp reject
        _count_decision(platform, "temp_reject")
        result = AdmissionResult(
            decision=REJECT_TEMP, reason="lane_constrained",
            delayed=False, estimated_wait_sec=None,
            retry_after=20, fairness_pressure=pressure,
            user_active_platform=user_active, platform_active_limit=hard_cap,
        )
        if _SHADOW_LOG:
            log.info("[Admission:shadow:%s] would=%s reason=lane_constrained user=%s**",
                     platform, REJECT_TEMP, user_key[:8])
            result.shadow = True
            result.decision = ADMIT_IMMEDIATE
        return result

    # ── 5. All clear → accept immediately ─────────────────────────────
    _count_decision(platform, "immediate")
    _log_admit(platform, user_key, ADMIT_IMMEDIATE, "ok", user_active, hard_cap, pressure)
    return AdmissionResult(
        decision=ADMIT_IMMEDIATE, reason="ok",
        delayed=False, estimated_wait_sec=None,
        retry_after=0, fairness_pressure=pressure,
        user_active_platform=user_active, platform_active_limit=hard_cap,
    )


def _estimate_wait(platform: str) -> int:
    """Rough estimate based on delayed queue depth. Returns seconds."""
    try:
        from app.core.delayed_queue import get_platform_queue_depth
        depth = get_platform_queue_depth(platform)
        # Assume each slot takes ~15s average
        return max(10, min(300, depth * 15))
    except Exception:
        return 30


def _count_decision(platform: str, field: str) -> None:
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        key = f"p27c:admit:{platform}"
        rc.hincrby(key, field, 1)
        rc.expire(key, 86400)
    except Exception:
        pass


def _log_admit(platform, user_key, decision, reason, active, cap, pressure):
    if log.isEnabledFor(logging.DEBUG) or decision != ADMIT_IMMEDIATE:
        log.info("[Admission:%s] %s reason=%s user=%s** active=%d/%d pressure=%.2f",
                 platform, decision, reason, user_key[:8], active, cap, pressure)


def get_admission_counters(platform: str) -> dict:
    """Admin: admission decision counts for a platform (last 24h)."""
    try:
        from app.core.redis_client import get_redis
        rc  = get_redis()
        raw = rc.hgetall(f"p27c:admit:{platform}") or {}
        return {
            k.decode() if isinstance(k, bytes) else k:
            int(v)
            for k, v in raw.items()
        }
    except Exception:
        return {}


def get_all_admission_summary() -> list[dict]:
    """Admin: across all platforms — admission decision totals."""
    try:
        from app.core.redis_client import get_redis
        rc   = get_redis()
        keys = rc.keys("p27c:admit:*") or []
        out  = []
        for k in keys:
            k_str = k.decode() if isinstance(k, bytes) else k
            platform = k_str.split(":", 2)[-1]
            counters = get_admission_counters(platform)
            total = sum(counters.values()) or 1
            out.append({
                "platform":     platform,
                "immediate":    counters.get("immediate", 0),
                "delayed":      counters.get("delayed", 0),
                "temp_reject":  counters.get("temp_reject", 0),
                "unavail":      counters.get("unavail", 0),
                "total":        total,
                "reject_rate":  round((counters.get("temp_reject", 0) + counters.get("unavail", 0)) / total, 3),
            })
        return sorted(out, key=lambda x: x["total"], reverse=True)
    except Exception:
        return []
