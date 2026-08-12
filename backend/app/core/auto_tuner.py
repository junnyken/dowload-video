"""
Auto Tuner — Phase 12
======================
Dynamically adjusts VidGrab operational parameters within safe bounds based on
real-time signals: disk pressure, provider instability, and queue depth.

All parameters are persisted in Redis hash  vidgrab:tuning:params
Change history is stored in Redis list       vidgrab:tuning:history  (LIFO, capped 200)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Parameter catalogue
# ---------------------------------------------------------------------------

PARAM_DEFS: dict[str, dict[str, Any]] = {
    "batch_concurrency": {
        "default": 8,
        "min": 2,
        "max": 16,
        "type": int,
        "description": "Celery worker_concurrency",
    },
    "wave_size": {
        "default": 10,
        "min": 3,
        "max": 20,
        "type": int,
        "description": "Items per wave in scrape_channel_task",
    },
    "retry_delay_multiplier": {
        "default": 1.0,
        "min": 0.5,
        "max": 3.0,
        "type": float,
        "description": "Multiplier applied to retry back-off delays",
    },
    "cleanup_freq_seconds": {
        "default": 300,
        "min": 60,
        "max": 900,
        "type": int,
        "description": "How often the cleanup job runs (seconds)",
    },
    "poll_interval_ms": {
        "default": 1500,
        "min": 800,
        "max": 5000,
        "type": int,
        "description": "Front-end/worker polling interval (ms)",
    },
    "scheduler_spacing_seconds": {
        "default": 60,
        "min": 30,
        "max": 300,
        "type": int,
        "description": "Minimum gap between scheduled task launches (seconds)",
    },
}

REDIS_PARAMS_KEY = "vidgrab:tuning:params"
REDIS_HISTORY_KEY = "vidgrab:tuning:history"
HISTORY_MAX_LEN = 200

# Thresholds that trigger tuning rules
DISK_PRESSURE_PCT = 80.0
HIGH_QUEUE_DEPTH = 50
HIGH_RETRY_RATE = 0.15  # 15 % of tasks retried


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(value: Any, param_name: str) -> Any:
    """Return value clamped to [min, max] for the given param."""
    defn = PARAM_DEFS[param_name]
    lo, hi, typ = defn["min"], defn["max"], defn["type"]
    return typ(max(lo, min(hi, typ(value))))


def _coerce(value: Any, param_name: str) -> Any:
    """Cast a raw Redis string/value to the correct Python type."""
    typ = PARAM_DEFS[param_name]["type"]
    return typ(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_change(
    param: str,
    old_value: Any,
    new_value: Any,
    reason: str,
    source: str,
) -> None:
    """Append a change record to the Redis history list, capped at HISTORY_MAX_LEN."""
    record = json.dumps(
        {
            "timestamp": _now_iso(),
            "param": param,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason,
            "source": source,
        }
    )
    r = get_redis()
    pipe = r.pipeline()
    pipe.lpush(REDIS_HISTORY_KEY, record)
    pipe.ltrim(REDIS_HISTORY_KEY, 0, HISTORY_MAX_LEN - 1)
    pipe.execute()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_param(name: str) -> Any:
    """Return the current value of a tunable parameter (Redis first, then default)."""
    if name not in PARAM_DEFS:
        raise KeyError(f"Unknown tuning parameter: {name!r}")
    r = get_redis()
    raw = r.hget(REDIS_PARAMS_KEY, name)
    if raw is None:
        return PARAM_DEFS[name]["default"]
    return _coerce(raw, name)


def set_param(
    name: str,
    value: Any,
    reason: str,
    source: str = "auto_tuner",
) -> bool:
    """
    Validate and persist a parameter update.

    Returns True if the value actually changed, False if it was already at
    that value or was rejected.
    """
    if name not in PARAM_DEFS:
        logger.warning("set_param: unknown parameter %r — ignored", name)
        return False

    clamped = _clamp(value, name)
    old_value = get_param(name)

    if clamped == old_value:
        return False

    r = get_redis()
    r.hset(REDIS_PARAMS_KEY, name, str(clamped))
    _log_change(name, old_value, clamped, reason, source)
    logger.info(
        "auto_tuner: %s %s → %s (%s) [%s]", name, old_value, clamped, reason, source
    )
    return True


def get_all_params() -> dict[str, Any]:
    """Return all current parameter values (Redis overlay on top of defaults)."""
    r = get_redis()
    raw_map: dict[str, str] = r.hgetall(REDIS_PARAMS_KEY) or {}
    result: dict[str, Any] = {}
    for name, defn in PARAM_DEFS.items():
        if name in raw_map:
            result[name] = _coerce(raw_map[name], name)
        else:
            result[name] = defn["default"]
    return result


def reset_to_defaults() -> dict[str, Any]:
    """
    Wipe all stored parameters so every get_param call returns the built-in
    default.  Logs each change and returns the new (default) state.
    """
    r = get_redis()
    current = get_all_params()
    r.delete(REDIS_PARAMS_KEY)
    for name, defn in PARAM_DEFS.items():
        old = current.get(name, defn["default"])
        if old != defn["default"]:
            _log_change(
                param=name,
                old_value=old,
                new_value=defn["default"],
                reason="reset_to_defaults",
                source="auto_tuner",
            )
    return {name: defn["default"] for name, defn in PARAM_DEFS.items()}


def get_tune_history(limit: int = 50) -> list[dict]:
    """Return up to *limit* most-recent tuning events (newest first)."""
    r = get_redis()
    raw_list = r.lrange(REDIS_HISTORY_KEY, 0, limit - 1)
    result: list[dict] = []
    for raw in raw_list:
        try:
            result.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning("auto_tuner: corrupt history entry skipped")
    return result


# ---------------------------------------------------------------------------
# Core tuning logic
# ---------------------------------------------------------------------------


def run_auto_tune(
    disk_pct: float,
    queue_depth: int,
    retry_rate: float,
) -> list[dict]:
    """
    Evaluate all tuning rules given current signals and apply safe changes.

    Parameters
    ----------
    disk_pct:     Current disk usage percentage (0–100).
    queue_depth:  Number of tasks currently in the Celery queue.
    retry_rate:   Fraction of recent tasks that required a retry (0.0–1.0).

    Returns
    -------
    List of action dicts describing every change made this cycle.
    """
    actions: list[dict] = []

    disk_pressure = disk_pct > DISK_PRESSURE_PCT
    provider_instability = retry_rate > HIGH_RETRY_RATE
    high_load = queue_depth > HIGH_QUEUE_DEPTH

    any_pressure = disk_pressure or provider_instability or high_load

    # ------------------------------------------------------------------
    # Rule 1: Disk pressure
    # ------------------------------------------------------------------
    if disk_pressure:
        for param, target in [
            ("cleanup_freq_seconds", PARAM_DEFS["cleanup_freq_seconds"]["min"]),
            ("wave_size", PARAM_DEFS["wave_size"]["min"]),
        ]:
            reason = f"disk_pressure (disk={disk_pct:.1f}%)"
            changed = set_param(param, target, reason=reason)
            if changed:
                actions.append(
                    {
                        "param": param,
                        "new_value": target,
                        "rule": "disk_pressure",
                        "reason": reason,
                    }
                )

    # ------------------------------------------------------------------
    # Rule 2: Provider instability (high retry rate)
    # ------------------------------------------------------------------
    if provider_instability:
        reason = f"provider_instability (retry_rate={retry_rate:.2%})"

        # batch_concurrency: reduce by 2
        current_concurrency = get_param("batch_concurrency")
        new_concurrency = current_concurrency - 2
        changed = set_param("batch_concurrency", new_concurrency, reason=reason)
        if changed:
            actions.append(
                {
                    "param": "batch_concurrency",
                    "new_value": _clamp(new_concurrency, "batch_concurrency"),
                    "rule": "provider_instability",
                    "reason": reason,
                }
            )

        # wave_size: push to min
        target_wave = PARAM_DEFS["wave_size"]["min"]
        changed = set_param("wave_size", target_wave, reason=reason)
        if changed:
            actions.append(
                {
                    "param": "wave_size",
                    "new_value": target_wave,
                    "rule": "provider_instability",
                    "reason": reason,
                }
            )

    # ------------------------------------------------------------------
    # Rule 3: High system load (queue depth > 50)
    # ------------------------------------------------------------------
    if high_load:
        reason = f"high_load (queue_depth={queue_depth})"
        for param, target in [
            ("poll_interval_ms", PARAM_DEFS["poll_interval_ms"]["max"]),
            ("scheduler_spacing_seconds", PARAM_DEFS["scheduler_spacing_seconds"]["max"]),
        ]:
            changed = set_param(param, target, reason=reason)
            if changed:
                actions.append(
                    {
                        "param": param,
                        "new_value": target,
                        "rule": "high_load",
                        "reason": reason,
                    }
                )

    # ------------------------------------------------------------------
    # Rule 4: Conditions normalized — step toward defaults (10% per cycle)
    # ------------------------------------------------------------------
    if not any_pressure:
        reason = "conditions_normalized"
        current = get_all_params()
        for name, defn in PARAM_DEFS.items():
            cur = current[name]
            default = defn["default"]
            if cur == default:
                continue

            typ = defn["type"]
            span = defn["max"] - defn["min"]
            step = max(typ(span * 0.10), typ(1))

            if cur < default:
                new_val = min(default, cur + step)
            else:
                new_val = max(default, cur - step)

            changed = set_param(name, new_val, reason=reason)
            if changed:
                actions.append(
                    {
                        "param": name,
                        "new_value": _clamp(new_val, name),
                        "rule": "normalize",
                        "reason": reason,
                    }
                )

    return actions
