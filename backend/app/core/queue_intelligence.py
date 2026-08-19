"""
Queue Intelligence Service
==========================
Phase 12 — VidGrab priority management, health monitoring, and fairness tracking.

Priority levels (lower = higher priority):
    INTERACTIVE = 1   single download, user-triggered
    REDOWNLOAD  = 2   user-triggered re-download / link refresh
    BATCH       = 3   bulk downloads
    SCHEDULED   = 4   scheduled jobs
    MAINTENANCE = 5   cleanup, reports, housekeeping

All Redis keys are namespaced under vidgrab:queue:*.
"""

from __future__ import annotations

import logging
from datetime import date
from enum import IntEnum
from typing import Any

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Priority levels
# ---------------------------------------------------------------------------

class Priority(IntEnum):
    INTERACTIVE = 1
    REDOWNLOAD  = 2
    BATCH       = 3
    SCHEDULED   = 4
    MAINTENANCE = 5


_PRIORITY_NAMES: dict[int, str] = {
    Priority.INTERACTIVE: "INTERACTIVE",
    Priority.REDOWNLOAD:  "REDOWNLOAD",
    Priority.BATCH:       "BATCH",
    Priority.SCHEDULED:   "SCHEDULED",
    Priority.MAINTENANCE: "MAINTENANCE",
}

# Celery queue names used in this project (best-effort introspection)
_CELERY_QUEUE_KEY = "celery"  # default Celery Redis list key

# Redis key constants
_KEY_LOW_PRIORITY_PAUSED = "vidgrab:queue:low_priority_paused"
_KEY_AVG_JOB_DURATION    = "vidgrab:queue:avg_job_duration"
_KEY_USER_COUNTS_PREFIX  = "vidgrab:queue:user_counts"

_LOW_PRIORITY_PAUSE_TTL  = 30 * 60   # 30 minutes in seconds
_DEFAULT_AVG_DURATION    = 45.0       # seconds
_EMA_ALPHA               = 0.1        # exponential moving average weight for new sample

# Thresholds for auto_manage_priority
_DISK_STRESS_THRESHOLD   = 85.0       # pause low-priority above this %
_DISK_NORMAL_THRESHOLD   = 70.0       # resume below this %
_QUEUE_STRESS_THRESHOLD  = 100        # pause if queue depth exceeds this
_QUEUE_NORMAL_THRESHOLD  = 50         # resume if queue depth drops below this


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _redis_get_float(key: str, default: float) -> float:
    try:
        r = get_redis()
        val = r.get(key)
        return float(val) if val is not None else default
    except Exception:
        return default


def _redis_set_float(key: str, value: float) -> None:
    try:
        get_redis().set(key, str(value))
    except Exception as exc:
        logger.warning("queue_intelligence: failed to write %s — %s", key, exc)


# ---------------------------------------------------------------------------
# Queue depth
# ---------------------------------------------------------------------------

def get_queue_depth() -> int:
    """Return the number of tasks currently waiting in the Celery Redis queue."""
    try:
        r = get_redis()
        depth = r.llen(_CELERY_QUEUE_KEY)
        return int(depth)
    except Exception as exc:
        logger.warning("queue_intelligence: cannot read queue depth — %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Average job duration (exponential moving average)
# ---------------------------------------------------------------------------

def _get_avg_duration() -> float:
    return _redis_get_float(_KEY_AVG_JOB_DURATION, _DEFAULT_AVG_DURATION)


def update_avg_duration(duration_seconds: float) -> None:
    """
    Update rolling average job duration with a new sample.
    Formula: new_avg = 0.9 * old_avg + 0.1 * duration_seconds
    """
    old_avg = _get_avg_duration()
    new_avg = (1.0 - _EMA_ALPHA) * old_avg + _EMA_ALPHA * duration_seconds
    _redis_set_float(_KEY_AVG_JOB_DURATION, new_avg)


# ---------------------------------------------------------------------------
# Active worker count
# ---------------------------------------------------------------------------

# Written by worker_heartbeat_check (which only runs when a worker is alive) and
# read by everything else, so no request path pays for a broadcast ping.
WORKER_COUNT_CACHE_KEY = "vg:worker_count_cache"
WORKER_SEEN_KEY = "vidgrab:last_worker_seen_at"
WORKER_COUNT_TTL = 300           # > the 120s heartbeat period, so a healthy
                                 # system always has a fresh value


def _get_active_workers(force_refresh: bool = False) -> int:
    """
    Number of Celery workers currently answering. Returns -1 when we could not
    determine it — callers must not read that as "healthy".

    The old implementation counted `_kombu.binding.*` keys, which are queue
    *bindings*: they exist whenever the queue has been declared, with or
    without a worker attached. It then clamped 0 up to 1 and also returned 1
    from its except branch, so it could never report an outage — and
    main.py called len() on this int, which raised TypeError and left /health
    permanently reporting worker_count: -1.

    By default this reads the cached value only (a plain Redis GET), because
    /health is polled frequently and a control-plane ping blocks. Pass
    force_refresh=True from the periodic heartbeat task, which is the writer.
    """
    if not force_refresh:
        try:
            cached = get_redis().get(WORKER_COUNT_CACHE_KEY)
            if cached is not None:
                return int(cached)
        except Exception:
            pass
        return -1

    try:
        from app.core.celery_app import celery_app
        replies = celery_app.control.ping(timeout=2.0) or []
        count = len(replies)
    except Exception:
        return -1

    try:
        get_redis().set(WORKER_COUNT_CACHE_KEY, count, ex=WORKER_COUNT_TTL)
    except Exception:
        pass
    return count


# ---------------------------------------------------------------------------
# Low-priority pause / resume
# ---------------------------------------------------------------------------

def pause_low_priority() -> bool:
    """
    Signal that low-priority jobs (BATCH, SCHEDULED, MAINTENANCE) should be
    deferred. Sets vidgrab:queue:low_priority_paused=1 with a 30-minute TTL.
    Returns True on success.
    """
    try:
        get_redis().set(_KEY_LOW_PRIORITY_PAUSED, "1", ex=_LOW_PRIORITY_PAUSE_TTL)
        logger.info("queue_intelligence: low-priority paused (TTL 30min)")
        return True
    except Exception as exc:
        logger.error("queue_intelligence: pause_low_priority failed — %s", exc)
        return False


def resume_low_priority() -> bool:
    """
    Clear the low-priority pause flag.
    Returns True on success.
    """
    try:
        get_redis().delete(_KEY_LOW_PRIORITY_PAUSED)
        logger.info("queue_intelligence: low-priority resumed")
        return True
    except Exception as exc:
        logger.error("queue_intelligence: resume_low_priority failed — %s", exc)
        return False


def is_low_priority_paused() -> bool:
    """Return True if the low-priority pause flag is currently set."""
    try:
        val = get_redis().get(_KEY_LOW_PRIORITY_PAUSED)
        return val == "1"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Jobs by priority (best-effort sampling)
# ---------------------------------------------------------------------------

def _get_jobs_by_priority() -> dict[str, int]:
    """
    Return a count of queued jobs broken down by priority name.
    Celery encodes task metadata as JSON in each list element; we do a
    best-effort scan of the queue for known priority markers rather than
    decoding every message (which could be expensive).
    """
    counts: dict[str, int] = {name: 0 for name in _PRIORITY_NAMES.values()}
    try:
        r = get_redis()
        # Sample up to 500 items to keep this O(n) but bounded.
        items = r.lrange(_CELERY_QUEUE_KEY, 0, 499)
        for raw in items:
            matched = False
            for priority, name in _PRIORITY_NAMES.items():
                if f'"priority": {priority}' in raw or f'"priority":{priority}' in raw:
                    counts[name] += 1
                    matched = True
                    break
            if not matched:
                # Default untagged tasks count as INTERACTIVE
                counts["INTERACTIVE"] += 1
    except Exception as exc:
        logger.debug("queue_intelligence: jobs_by_priority scan failed — %s", exc)
    return counts


# ---------------------------------------------------------------------------
# Provider pressure
# ---------------------------------------------------------------------------

def _get_provider_pressure() -> str:
    """
    Return 'high' | 'medium' | 'low' based on platform throttle state.
    Tries to read a platform throttle summary key; falls back to 'unknown'.
    """
    try:
        r = get_redis()
        # Reuse platform_throttle module's key if present
        throttled_keys = r.keys("vidgrab:throttle:*:blocked") or []
        count = len(throttled_keys)
        if count >= 3:
            return "high"
        if count >= 1:
            return "medium"
        return "low"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Queue health
# ---------------------------------------------------------------------------

def get_queue_health(disk_pressure_pct: float = 0.0) -> dict[str, Any]:
    """
    Return a snapshot of queue health.

    Keys:
        queue_depth           int   — waiting tasks
        estimated_wait_seconds float — queue_depth / max(active_workers,1) * avg_duration
        active_workers        int   — estimated worker count
        paused_low_priority   bool  — whether low-priority is currently paused
        jobs_by_priority      dict  — counts per priority tier (sampled)
        disk_pressure_pct     float — passed-in disk usage percentage
        provider_pressure     str   — 'low' | 'medium' | 'high' | 'unknown'
    """
    queue_depth     = get_queue_depth()
    active_workers  = _get_active_workers()
    avg_duration    = _get_avg_duration()
    paused          = is_low_priority_paused()
    jobs_by_prio    = _get_jobs_by_priority()
    provider_press  = _get_provider_pressure()

    estimated_wait = (queue_depth / max(active_workers, 1)) * avg_duration

    return {
        "queue_depth":              queue_depth,
        "estimated_wait_seconds":   round(estimated_wait, 2),
        "active_workers":           active_workers,
        "paused_low_priority":      paused,
        "jobs_by_priority":         jobs_by_prio,
        "disk_pressure_pct":        disk_pressure_pct,
        "provider_pressure":        provider_press,
    }


# ---------------------------------------------------------------------------
# Per-job priority info
# ---------------------------------------------------------------------------

def get_job_priority_info(job_id: str, job_type: str) -> dict[str, Any]:
    """
    Return priority metadata for a specific job.

    job_type should match one of the Priority enum names (case-insensitive);
    unrecognised types default to INTERACTIVE.

    Keys:
        priority_level         int
        priority_name          str
        estimated_wait_seconds float
        paused_by_policy       bool  — True when job is low-priority and paused
    """
    # Resolve priority
    normalised = job_type.upper().replace("-", "_").replace(" ", "_")
    priority_level = Priority.INTERACTIVE  # default
    for member in Priority:
        if member.name == normalised:
            priority_level = member
            break

    priority_name  = _PRIORITY_NAMES[priority_level]
    paused         = is_low_priority_paused()
    is_low_prio    = priority_level >= Priority.BATCH
    paused_by_pol  = paused and is_low_prio

    queue_depth    = get_queue_depth()
    active_workers = _get_active_workers()
    avg_duration   = _get_avg_duration()
    estimated_wait = (queue_depth / max(active_workers, 1)) * avg_duration

    return {
        "priority_level":          int(priority_level),
        "priority_name":           priority_name,
        "estimated_wait_seconds":  round(estimated_wait, 2),
        "paused_by_policy":        paused_by_pol,
    }


# ---------------------------------------------------------------------------
# Auto priority management
# ---------------------------------------------------------------------------

def auto_manage_priority(disk_pct: float, queue_depth: int) -> list[str]:
    """
    Automatically pause or resume low-priority jobs based on system stress.

    Pause when:
        disk_pct  >= _DISK_STRESS_THRESHOLD  (85%)  OR
        queue_depth >= _QUEUE_STRESS_THRESHOLD (100)

    Resume when both:
        disk_pct  <  _DISK_NORMAL_THRESHOLD   (70%)  AND
        queue_depth <  _QUEUE_NORMAL_THRESHOLD  (50)

    Returns a list of human-readable action strings taken.
    """
    actions: list[str] = []
    currently_paused = is_low_priority_paused()

    should_pause = (
        disk_pct   >= _DISK_STRESS_THRESHOLD or
        queue_depth >= _QUEUE_STRESS_THRESHOLD
    )
    should_resume = (
        disk_pct   < _DISK_NORMAL_THRESHOLD and
        queue_depth < _QUEUE_NORMAL_THRESHOLD
    )

    if should_pause and not currently_paused:
        ok = pause_low_priority()
        if ok:
            reason_parts = []
            if disk_pct >= _DISK_STRESS_THRESHOLD:
                reason_parts.append(f"disk={disk_pct:.1f}%>={_DISK_STRESS_THRESHOLD}%")
            if queue_depth >= _QUEUE_STRESS_THRESHOLD:
                reason_parts.append(f"depth={queue_depth}>={_QUEUE_STRESS_THRESHOLD}")
            actions.append(f"paused_low_priority: {', '.join(reason_parts)}")

    elif should_resume and currently_paused:
        ok = resume_low_priority()
        if ok:
            actions.append(
                f"resumed_low_priority: disk={disk_pct:.1f}%<{_DISK_NORMAL_THRESHOLD}%"
                f", depth={queue_depth}<{_QUEUE_NORMAL_THRESHOLD}"
            )

    return actions


# ---------------------------------------------------------------------------
# Per-user fairness tracking
# ---------------------------------------------------------------------------

def _user_counts_key(user_id: str) -> str:
    today = date.today().isoformat()          # YYYY-MM-DD
    return f"{_KEY_USER_COUNTS_PREFIX}:{today}"


def record_user_job(user_id: str) -> None:
    """
    Increment the per-user job counter for today.
    Hash: vidgrab:queue:user_counts:{YYYY-MM-DD}  field: user_id
    TTL auto-set to 48 h so stale days are cleaned up.
    """
    try:
        r   = get_redis()
        key = _user_counts_key(user_id)
        r.hincrby(key, user_id, 1)
        # Refresh TTL on every write; 48 h is enough to survive day boundaries.
        r.expire(key, 48 * 3600)
    except Exception as exc:
        logger.warning("queue_intelligence: record_user_job failed — %s", exc)


def get_user_queue_count(user_id: str) -> int:
    """Return how many jobs the given user has queued today."""
    try:
        r   = get_redis()
        key = _user_counts_key(user_id)
        val = r.hget(key, user_id)
        return int(val) if val is not None else 0
    except Exception as exc:
        logger.warning("queue_intelligence: get_user_queue_count failed — %s", exc)
        return 0
