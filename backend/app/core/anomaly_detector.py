"""
anomaly_detector.py — Phase 12 VidGrab
Rolling-window heuristic anomaly detection using Redis stats.
Keys consumed: vidgrab:stats:{YYYY-MM-DD} (hashes: platform:ok, platform:err)
Keys written:  vidgrab:anomalies:active (list of JSON, max 100)
               vidgrab:anomalies:history (list of JSON, max 500)
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Optional

from app.core.redis_client import get_redis as _get_redis


class _RedisProxy:
    """Lazy proxy — forwards attribute access to get_redis() on each call."""
    def __getattr__(self, name):
        return getattr(_get_redis(), name)


redis_client = _RedisProxy()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_PREFIX = "vidgrab:"
STATS_KEY_TPL = KEY_PREFIX + "stats:{date}"
ANOMALY_ACTIVE_KEY = KEY_PREFIX + "anomalies:active"
ANOMALY_HISTORY_KEY = KEY_PREFIX + "anomalies:history"

MAX_ACTIVE = 100
MAX_HISTORY = 500

ROLLING_WINDOW_DAYS = 7

# Thresholds
FAILURE_SPIKE_MULTIPLIER = 2.5      # today error_rate > avg * 2.5
FAILURE_SPIKE_MIN_REQUESTS = 5      # ignore platforms with fewer requests
FAILURE_ABS_THRESHOLD = 0.5         # always alert if error_rate > 50%

QUEUE_LAG_MULTIPLIER = 3.0          # queue length > avg * 3
QUEUE_LAG_ABS_THRESHOLD = 200       # or absolute length > 200

DISK_PRESSURE_WARNING = 80.0        # % used
DISK_PRESSURE_CRITICAL = 90.0       # % used

RETRY_RATE_MULTIPLIER = 2.5         # retry_rate > avg * 2.5
RETRY_RATE_ABS_THRESHOLD = 0.30     # or absolute rate > 30 %

DROP_IN_SUCCESS_THRESHOLD = 0.20    # success_rate dropped by 20 pp vs 7-day avg

# Redis keys for queue + retry counters (written elsewhere in the app)
QUEUE_LEN_KEY = KEY_PREFIX + "queue:length"          # updated by task producer
RETRY_COUNTER_KEY = KEY_PREFIX + "counters:retries"  # incremented on each retry
TOTAL_COUNTER_KEY = KEY_PREFIX + "counters:total"    # incremented on each task start

# Rolling baseline keys
BASELINE_QUEUE_KEY = KEY_PREFIX + "baselines:queue_avg"
BASELINE_RETRY_KEY = KEY_PREFIX + "baselines:retry_rate_avg"


# ---------------------------------------------------------------------------
# Anomaly state
# ---------------------------------------------------------------------------

class AnomalyState(str, Enum):
    DETECTED = "anomaly_detected"
    UNDER_WATCH = "anomaly_under_watch"
    ESCALATED = "anomaly_escalated"
    RESOLVED = "anomaly_resolved"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _date_key(date_str: str) -> str:
    return STATS_KEY_TPL.format(date=date_str)


def _past_dates(days: int = ROLLING_WINDOW_DAYS) -> list[str]:
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]


def _build_anomaly(
    metric: str,
    window: str,
    magnitude: float,
    likely_cause: str,
    auto_mitigated: bool = False,
    mitigation_applied: Optional[str] = None,
    state: AnomalyState = AnomalyState.DETECTED,
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "detected_at": _now_iso(),
        "state": state.value,
        "metric": metric,
        "window": window,
        "magnitude": round(magnitude, 4),
        "likely_cause": likely_cause,
        "auto_mitigated": auto_mitigated,
        "mitigation_applied": mitigation_applied,
    }


def _store_anomaly(anomaly: dict) -> None:
    try:
        payload = json.dumps(anomaly)
        # Prepend to active list and trim
        redis_client.lpush(ANOMALY_ACTIVE_KEY, payload)
        redis_client.ltrim(ANOMALY_ACTIVE_KEY, 0, MAX_ACTIVE - 1)
        # Also append to history
        redis_client.rpush(ANOMALY_HISTORY_KEY, payload)
        redis_client.ltrim(ANOMALY_HISTORY_KEY, -MAX_HISTORY, -1)
    except Exception:
        pass


def _get_platform_stats_for_day(date_str: str) -> dict[str, dict[str, int]]:
    """Return {platform: {ok: N, err: N}} for a given date."""
    result: dict[str, dict[str, int]] = {}
    try:
        raw: dict = redis_client.hgetall(_date_key(date_str)) or {}
        for key_bytes, val_bytes in raw.items():
            key = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
            val = int(val_bytes.decode() if isinstance(val_bytes, bytes) else val_bytes)
            if ":" not in key:
                continue
            platform, status = key.split(":", 1)
            if platform not in result:
                result[platform] = {"ok": 0, "err": 0}
            if status == "ok":
                result[platform]["ok"] += val
            elif status == "err":
                result[platform]["err"] += val
    except Exception:
        pass
    return result


def _get_today_platform_stats() -> dict[str, dict[str, int]]:
    return _get_platform_stats_for_day(_today_str())


def _get_rolling_platform_stats(days: int = ROLLING_WINDOW_DAYS) -> dict[str, dict[str, int]]:
    """Aggregate stats over the past N days (excludes today)."""
    totals: dict[str, dict[str, int]] = {}
    for date_str in _past_dates(days):
        day_stats = _get_platform_stats_for_day(date_str)
        for platform, counts in day_stats.items():
            if platform not in totals:
                totals[platform] = {"ok": 0, "err": 0}
            totals[platform]["ok"] += counts.get("ok", 0)
            totals[platform]["err"] += counts.get("err", 0)
    return totals


def _error_rate(stats: dict[str, int]) -> float:
    total = stats.get("ok", 0) + stats.get("err", 0)
    if total == 0:
        return 0.0
    return stats.get("err", 0) / total


def _success_rate(stats: dict[str, int]) -> float:
    return 1.0 - _error_rate(stats)


# ---------------------------------------------------------------------------
# Public check functions
# ---------------------------------------------------------------------------

def check_failure_spike(platform: str) -> Optional[dict]:
    """
    Compare today's error rate for `platform` against 7-day rolling average.
    Returns anomaly dict if spike detected, else None.
    """
    try:
        today_all = _get_today_platform_stats()
        if platform not in today_all:
            return None

        today_stats = today_all[platform]
        today_total = today_stats.get("ok", 0) + today_stats.get("err", 0)
        if today_total < FAILURE_SPIKE_MIN_REQUESTS:
            return None

        today_err_rate = _error_rate(today_stats)

        rolling_all = _get_rolling_platform_stats()
        rolling_stats = rolling_all.get(platform, {"ok": 0, "err": 0})
        rolling_total = rolling_stats.get("ok", 0) + rolling_stats.get("err", 0)

        if rolling_total == 0:
            avg_err_rate = 0.0
        else:
            avg_err_rate = _error_rate(rolling_stats)

        # Spike condition
        spike = False
        if today_err_rate >= FAILURE_ABS_THRESHOLD:
            spike = True
        elif avg_err_rate > 0 and today_err_rate >= avg_err_rate * FAILURE_SPIKE_MULTIPLIER:
            spike = True

        if not spike:
            return None

        magnitude = today_err_rate / avg_err_rate if avg_err_rate > 0 else float("inf")
        likely_cause = (
            f"Platform '{platform}' error rate today {today_err_rate:.1%} "
            f"vs 7-day avg {avg_err_rate:.1%} "
            f"({magnitude:.1f}x spike)"
        )
        anomaly = _build_anomaly(
            metric=f"failure_spike:{platform}",
            window="today_vs_7d",
            magnitude=today_err_rate,
            likely_cause=likely_cause,
        )
        _store_anomaly(anomaly)
        return anomaly

    except Exception:
        return None


def check_queue_lag() -> Optional[dict]:
    """
    Compare current Redis queue length vs stored rolling average.
    Returns anomaly dict if lag spike detected, else None.
    """
    try:
        current_len_raw = redis_client.get(QUEUE_LEN_KEY)
        if current_len_raw is None:
            return None

        current_len = int(current_len_raw.decode() if isinstance(current_len_raw, bytes) else current_len_raw)

        avg_raw = redis_client.get(BASELINE_QUEUE_KEY)
        avg_len = float(avg_raw.decode() if isinstance(avg_raw, bytes) else avg_raw) if avg_raw else 0.0

        spike = False
        if current_len >= QUEUE_LAG_ABS_THRESHOLD:
            spike = True
        elif avg_len > 0 and current_len >= avg_len * QUEUE_LAG_MULTIPLIER:
            spike = True

        if not spike:
            # Update rolling average (simple exponential smoothing)
            new_avg = avg_len * 0.9 + current_len * 0.1 if avg_len > 0 else float(current_len)
            try:
                redis_client.set(BASELINE_QUEUE_KEY, str(new_avg), ex=86400 * 8)
            except Exception:
                pass
            return None

        magnitude = current_len / avg_len if avg_len > 0 else float(current_len)
        likely_cause = (
            f"Queue length {current_len} "
            f"vs rolling avg {avg_len:.1f} "
            f"({magnitude:.1f}x). Possible worker backlog or consumer failure."
        )
        anomaly = _build_anomaly(
            metric="queue_lag",
            window="realtime_vs_baseline",
            magnitude=current_len,
            likely_cause=likely_cause,
        )
        _store_anomaly(anomaly)
        return anomaly

    except Exception:
        return None


def check_disk_pressure() -> Optional[dict]:
    """
    Check disk usage percentage on the filesystem containing /tmp (or CWD).
    Returns anomaly dict if usage exceeds thresholds, else None.
    """
    try:
        check_path = os.environ.get("VIDGRAB_DISK_CHECK_PATH", "/tmp")
        usage = shutil.disk_usage(check_path)
        pct_used = (usage.used / usage.total) * 100.0 if usage.total > 0 else 0.0

        if pct_used < DISK_PRESSURE_WARNING:
            return None

        state = AnomalyState.ESCALATED if pct_used >= DISK_PRESSURE_CRITICAL else AnomalyState.DETECTED
        likely_cause = (
            f"Disk usage at {pct_used:.1f}% on '{check_path}'. "
            f"Total={usage.total // (1024**3)}GB, "
            f"Used={usage.used // (1024**3)}GB, "
            f"Free={usage.free // (1024**3)}GB. "
            "Possible download accumulation or log growth."
        )
        anomaly = _build_anomaly(
            metric="disk_pressure",
            window="realtime",
            magnitude=round(pct_used, 2),
            likely_cause=likely_cause,
            state=state,
        )
        _store_anomaly(anomaly)
        return anomaly

    except Exception:
        return None


def check_retry_rate() -> Optional[dict]:
    """
    Compute retry_rate = retries / total_tasks from Redis counters.
    Compare vs rolling baseline.
    Returns anomaly dict if rate is elevated, else None.
    """
    try:
        retry_raw = redis_client.get(RETRY_COUNTER_KEY)
        total_raw = redis_client.get(TOTAL_COUNTER_KEY)

        retries = int(retry_raw.decode() if isinstance(retry_raw, bytes) else retry_raw) if retry_raw else 0
        total = int(total_raw.decode() if isinstance(total_raw, bytes) else total_raw) if total_raw else 0

        if total == 0:
            return None

        current_rate = retries / total

        avg_raw = redis_client.get(BASELINE_RETRY_KEY)
        avg_rate = float(avg_raw.decode() if isinstance(avg_raw, bytes) else avg_raw) if avg_raw else 0.0

        spike = False
        if current_rate >= RETRY_RATE_ABS_THRESHOLD:
            spike = True
        elif avg_rate > 0 and current_rate >= avg_rate * RETRY_RATE_MULTIPLIER:
            spike = True

        if not spike:
            new_avg = avg_rate * 0.9 + current_rate * 0.1 if avg_rate > 0 else current_rate
            try:
                redis_client.set(BASELINE_RETRY_KEY, str(new_avg), ex=86400 * 8)
            except Exception:
                pass
            return None

        magnitude = current_rate / avg_rate if avg_rate > 0 else float("inf")
        likely_cause = (
            f"Retry rate {current_rate:.1%} "
            f"vs baseline {avg_rate:.1%} "
            f"({magnitude:.1f}x). "
            "Possible upstream platform instability or worker OOM."
        )
        anomaly = _build_anomaly(
            metric="high_retry_rate",
            window="session_vs_baseline",
            magnitude=current_rate,
            likely_cause=likely_cause,
        )
        _store_anomaly(anomaly)
        return anomaly

    except Exception:
        return None


def check_platform_success_drop() -> list[dict]:
    """
    Detect sudden drops in per-platform success rate (today vs 7-day avg).
    Returns list of anomaly dicts (one per affected platform).
    """
    anomalies = []
    try:
        today_all = _get_today_platform_stats()
        rolling_all = _get_rolling_platform_stats()

        for platform, today_stats in today_all.items():
            today_total = today_stats.get("ok", 0) + today_stats.get("err", 0)
            if today_total < FAILURE_SPIKE_MIN_REQUESTS:
                continue

            today_sr = _success_rate(today_stats)

            rolling_stats = rolling_all.get(platform, {"ok": 0, "err": 0})
            rolling_total = rolling_stats.get("ok", 0) + rolling_stats.get("err", 0)
            if rolling_total < FAILURE_SPIKE_MIN_REQUESTS:
                continue

            avg_sr = _success_rate(rolling_stats)
            drop = avg_sr - today_sr

            if drop < DROP_IN_SUCCESS_THRESHOLD:
                continue

            likely_cause = (
                f"Platform '{platform}' success rate dropped {drop:.1%} "
                f"(today {today_sr:.1%} vs 7-day avg {avg_sr:.1%})."
            )
            anomaly = _build_anomaly(
                metric=f"success_drop:{platform}",
                window="today_vs_7d",
                magnitude=drop,
                likely_cause=likely_cause,
            )
            _store_anomaly(anomaly)
            anomalies.append(anomaly)

    except Exception:
        pass
    return anomalies


def check_schedule_drift() -> Optional[dict]:
    """
    Detect if the periodic anomaly-check itself is running late.
    Reads vidgrab:anomaly_check:last_run and compares to expected cadence.
    Returns anomaly if drift is detected, else None.
    """
    LAST_RUN_KEY = KEY_PREFIX + "anomaly_check:last_run"
    EXPECTED_INTERVAL_SEC = int(os.environ.get("ANOMALY_CHECK_INTERVAL_SEC", "300"))
    DRIFT_TOLERANCE = 2.0  # allow 2x the expected interval before flagging

    try:
        last_raw = redis_client.get(LAST_RUN_KEY)
        now_ts = datetime.now(timezone.utc).timestamp()

        if last_raw is None:
            # First run — record and skip
            redis_client.set(LAST_RUN_KEY, str(now_ts), ex=86400)
            return None

        last_ts = float(last_raw.decode() if isinstance(last_raw, bytes) else last_raw)
        elapsed = now_ts - last_ts
        max_allowed = EXPECTED_INTERVAL_SEC * DRIFT_TOLERANCE

        # Update timestamp
        try:
            redis_client.set(LAST_RUN_KEY, str(now_ts), ex=86400)
        except Exception:
            pass

        if elapsed <= max_allowed:
            return None

        drift_minutes = elapsed / 60
        likely_cause = (
            f"Anomaly check ran {drift_minutes:.1f}min after previous run "
            f"(expected ~{EXPECTED_INTERVAL_SEC/60:.1f}min). "
            "Celery beat may have missed a tick or worker was down."
        )
        anomaly = _build_anomaly(
            metric="schedule_drift",
            window="last_run_vs_expected",
            magnitude=round(elapsed, 1),
            likely_cause=likely_cause,
        )
        _store_anomaly(anomaly)
        return anomaly

    except Exception:
        return None


def check_webhook_failures() -> Optional[dict]:
    """
    Check webhook delivery failure counter.
    Key: vidgrab:counters:webhook_failures / vidgrab:counters:webhook_total
    """
    WEBHOOK_FAIL_KEY = KEY_PREFIX + "counters:webhook_failures"
    WEBHOOK_TOTAL_KEY = KEY_PREFIX + "counters:webhook_total"
    BASELINE_WH_KEY = KEY_PREFIX + "baselines:webhook_fail_rate"
    WH_FAIL_ABS_THRESHOLD = 0.25
    WH_FAIL_MULTIPLIER = 3.0

    try:
        fail_raw = redis_client.get(WEBHOOK_FAIL_KEY)
        total_raw = redis_client.get(WEBHOOK_TOTAL_KEY)

        if fail_raw is None or total_raw is None:
            return None

        fails = int(fail_raw.decode() if isinstance(fail_raw, bytes) else fail_raw)
        total = int(total_raw.decode() if isinstance(total_raw, bytes) else total_raw)

        if total == 0:
            return None

        current_rate = fails / total

        avg_raw = redis_client.get(BASELINE_WH_KEY)
        avg_rate = float(avg_raw.decode() if isinstance(avg_raw, bytes) else avg_raw) if avg_raw else 0.0

        spike = False
        if current_rate >= WH_FAIL_ABS_THRESHOLD:
            spike = True
        elif avg_rate > 0 and current_rate >= avg_rate * WH_FAIL_MULTIPLIER:
            spike = True

        if not spike:
            new_avg = avg_rate * 0.9 + current_rate * 0.1 if avg_rate > 0 else current_rate
            try:
                redis_client.set(BASELINE_WH_KEY, str(new_avg), ex=86400 * 8)
            except Exception:
                pass
            return None

        magnitude = current_rate / avg_rate if avg_rate > 0 else float("inf")
        likely_cause = (
            f"Webhook failure rate {current_rate:.1%} "
            f"vs baseline {avg_rate:.1%} "
            f"({magnitude:.1f}x). "
            "Downstream webhook consumers may be unreachable."
        )
        anomaly = _build_anomaly(
            metric="webhook_delivery_failures",
            window="session_vs_baseline",
            magnitude=current_rate,
            likely_cause=likely_cause,
        )
        _store_anomaly(anomaly)
        return anomaly

    except Exception:
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_all_checks() -> list[dict]:
    """
    Run all anomaly checks, store new anomalies in Redis, return current active list.
    Called by Celery beat task on a periodic schedule.
    """
    # Schedule drift must go first so it uses the previous timestamp
    check_schedule_drift()

    # Per-platform checks
    try:
        today_all = _get_today_platform_stats()
        for platform in today_all:
            check_failure_spike(platform)
    except Exception:
        pass

    check_queue_lag()
    check_disk_pressure()
    check_retry_rate()
    check_platform_success_drop()
    check_webhook_failures()

    return get_active_anomalies()


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def get_active_anomalies() -> list[dict]:
    """Fetch all active anomalies from Redis (newest first)."""
    try:
        raw_list = redis_client.lrange(ANOMALY_ACTIVE_KEY, 0, MAX_ACTIVE - 1) or []
        result = []
        for item in raw_list:
            try:
                decoded = item.decode() if isinstance(item, bytes) else item
                result.append(json.loads(decoded))
            except Exception:
                continue
        return result
    except Exception:
        return []


def _update_anomaly_state(anomaly_id: str, new_state: AnomalyState) -> bool:
    """
    Find anomaly by id in the active list, update its state, re-store.
    Returns True if found and updated.
    """
    try:
        raw_list = redis_client.lrange(ANOMALY_ACTIVE_KEY, 0, MAX_ACTIVE - 1) or []
        updated = False
        new_list = []
        for item in raw_list:
            try:
                decoded = item.decode() if isinstance(item, bytes) else item
                obj = json.loads(decoded)
                if obj.get("id") == anomaly_id:
                    obj["state"] = new_state.value
                    obj["updated_at"] = _now_iso()
                    updated = True
                new_list.append(json.dumps(obj))
            except Exception:
                new_list.append(item if isinstance(item, str) else item.decode(errors="replace"))

        if updated:
            # Rewrite the list atomically using a pipeline
            try:
                pipe = redis_client.pipeline()
                pipe.delete(ANOMALY_ACTIVE_KEY)
                for entry in new_list:
                    pipe.rpush(ANOMALY_ACTIVE_KEY, entry)
                pipe.ltrim(ANOMALY_ACTIVE_KEY, 0, MAX_ACTIVE - 1)
                pipe.execute()
            except Exception:
                pass

        return updated
    except Exception:
        return False


def resolve_anomaly(anomaly_id: str) -> bool:
    """Mark anomaly as resolved. Returns True if the anomaly was found."""
    return _update_anomaly_state(anomaly_id, AnomalyState.RESOLVED)


def escalate_anomaly(anomaly_id: str) -> bool:
    """Escalate anomaly state. Returns True if the anomaly was found."""
    return _update_anomaly_state(anomaly_id, AnomalyState.ESCALATED)


def watch_anomaly(anomaly_id: str) -> bool:
    """Move anomaly to under-watch state. Returns True if found."""
    return _update_anomaly_state(anomaly_id, AnomalyState.UNDER_WATCH)
