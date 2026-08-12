"""
Recovery Playbooks — Phase 12
================================
Catalog of known failure patterns with structured remediation steps.

Each playbook describes:
  - trigger_conditions: anomaly signals that activate it
  - auto_actions: safe one-click remediations
  - manual_steps: human operator runbook
  - rollback_available: whether the action is reversible
  - confidence: how reliable the recommended actions are (low/medium/high)

Usage:
  from app.core.playbooks import get_playbook, match_active_playbooks, execute_safe_action

  active = match_active_playbooks(anomalies)
  result = execute_safe_action("lower_concurrency", {})
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

HISTORY_KEY = "vidgrab:playbooks:history"
HISTORY_MAX = 200

LOW_PRIORITY_PAUSED_KEY = "vidgrab:schedules:low_priority_paused"


# ---------------------------------------------------------------------------
# Playbook catalog
# ---------------------------------------------------------------------------

PLAYBOOKS: dict[str, dict] = {
    "provider_outage": {
        "id": "provider_outage",
        "name": "Provider / Platform Outage",
        "trigger_conditions": [
            "platform_error_rate > 0.5 for >= 3 platforms simultaneously",
            "YouTube HTTP 5xx rate > 30%",
            "extraction success rate drops > 40% vs 1h average",
        ],
        "description": (
            "Mass download failures across multiple platforms indicate an upstream "
            "outage (YouTube, TikTok, etc.) rather than a local problem. "
            "Avoid hammering the platform — back off and monitor."
        ),
        "auto_actions": [
            "lower_concurrency",
            "pause_low_priority_schedules",
        ],
        "manual_steps": [
            "Check platform status pages (downdetector.com, statuspage links).",
            "Increase Celery task visibility_timeout to avoid duplicate retries.",
            "Pause new job intake if outage confirmed (feature flag: INTAKE_PAUSED=1).",
            "Resume auto_actions rollback once platform recovers.",
        ],
        "rollback_available": True,
        "confidence": "high",
    },

    "token_invalidation": {
        "id": "token_invalidation",
        "name": "Cookie / Token Pool Invalidation",
        "trigger_conditions": [
            "cookie_pool hit rate drops below 20%",
            "login_required or sign_in errors > 25% of jobs",
            "po_token cache miss rate > 80%",
        ],
        "description": (
            "Stored session cookies or PO tokens have been invalidated by the platform. "
            "Triggers when authentication-related errors spike suddenly."
        ),
        "auto_actions": [
            "refresh_cookies",
        ],
        "manual_steps": [
            "Log into affected platform accounts manually and export fresh cookies.",
            "Update COOKIE_FILE or cookie_pool store in Supabase/Redis.",
            "Rotate user-agents in download config to reduce bot fingerprinting.",
            "Consider adding proxy rotation to spread sessions.",
        ],
        "rollback_available": False,
        "confidence": "high",
    },

    "disk_pressure": {
        "id": "disk_pressure",
        "name": "Disk Space Pressure",
        "trigger_conditions": [
            "disk_usage_pct > 85",
            "disk_free_gb < 5",
        ],
        "description": (
            "Worker node disk usage exceeds safe threshold. "
            "New downloads may fail mid-write, corrupting output files."
        ),
        "auto_actions": [
            "trigger_cleanup",
            "pause_low_priority_schedules",
        ],
        "manual_steps": [
            "Run: df -h && du -sh /tmp/vidgrab/* | sort -rh | head -20",
            "Delete stale temp files: find /tmp/vidgrab -mmin +60 -delete",
            "Check Supabase storage or S3 and prune expired objects.",
            "If persistent: expand volume or add additional mount.",
            "Set DISK_QUOTA_PER_JOB_MB lower in env to reduce per-job footprint.",
        ],
        "rollback_available": True,
        "confidence": "high",
    },

    "queue_backlog": {
        "id": "queue_backlog",
        "name": "Queue Backlog — Depth > 100",
        "trigger_conditions": [
            "celery queue depth > 100 jobs",
            "average job wait time > 10 minutes",
            "pending jobs older than 15 min > 20",
        ],
        "description": (
            "The Celery task queue has built up faster than workers can drain it. "
            "Could indicate worker crash, concurrency too low, or traffic spike."
        ),
        "auto_actions": [
            "lower_concurrency",
        ],
        "manual_steps": [
            "Check worker count: celery -A app.core.celery_app inspect active",
            "Scale workers: increase CELERY_CONCURRENCY or add replicas.",
            "Review task ETA distribution — batch tasks may be crowding out singles.",
            "Consider priority queues: route batch to separate low-priority queue.",
            "If workers crashed: check Redis memory and restart celery worker.",
        ],
        "rollback_available": False,
        "confidence": "medium",
    },

    "extraction_failure_spike": {
        "id": "extraction_failure_spike",
        "name": "Extraction Failure Spike",
        "trigger_conditions": [
            "platform error_rate > 0.5 for single platform",
            "yt-dlp extraction errors spike > 50% within 15 min window",
            "failure_classifier category=PERMANENT > 30%",
        ],
        "description": (
            "A specific platform extractor is failing at a high rate. "
            "Could be yt-dlp version mismatch, platform UI change, or targeted blocking."
        ),
        "auto_actions": [
            "rotate_fallback_order",
            "lower_concurrency",
        ],
        "manual_steps": [
            "Run: yt-dlp --version && yt-dlp -U  (update extractor)",
            "Check yt-dlp GitHub issues for the affected platform.",
            "Test manual extraction: yt-dlp <url> --verbose",
            "If bot-blocked: switch proxy pool or add cookie rotation.",
            "Temporarily disable the failing platform in SUPPORTED_PLATFORMS env.",
        ],
        "rollback_available": True,
        "confidence": "medium",
    },

    "schedule_drift": {
        "id": "schedule_drift",
        "name": "Scheduled Job Drift",
        "trigger_conditions": [
            "scheduled job execution latency > 5 minutes consistently",
            "beat_schedule last_run_at drift > 300s vs expected",
            "missed_beats_count > 3 in 1 hour",
        ],
        "description": (
            "Celery Beat scheduled tasks are running significantly late. "
            "Indicates worker overload, clock skew, or Beat process issues."
        ),
        "auto_actions": [
            "pause_low_priority_schedules",
            "lower_concurrency",
        ],
        "manual_steps": [
            "Confirm Beat is running: ps aux | grep celery",
            "Check system clock: date && timedatectl",
            "Review Beat schedule log: celery -A app.core.celery_app beat -l debug",
            "If overloaded: reduce beat schedule frequency for non-critical tasks.",
            "Consider separating Beat from worker into its own container.",
            "Check Redis latency: redis-cli --latency-history -i 1",
        ],
        "rollback_available": True,
        "confidence": "medium",
    },

    "webhook_delivery_failures": {
        "id": "webhook_delivery_failures",
        "name": "Webhook Delivery Failures",
        "trigger_conditions": [
            "webhook 4xx/5xx response rate > 20%",
            "webhook retry exhausted count > 5 in 30 min",
            "webhook endpoint unreachable (connection timeout)",
        ],
        "description": (
            "Outgoing webhook notifications to consumer endpoints are failing. "
            "Jobs complete successfully but downstream systems are not notified."
        ),
        "auto_actions": [],
        "manual_steps": [
            "Check endpoint health: curl -I <webhook_url>",
            "Review webhook error log for specific HTTP status codes.",
            "4xx errors: verify endpoint authentication (headers/secrets).",
            "5xx errors: notify endpoint owner — their service is unhealthy.",
            "Temporarily disable webhook to prevent retry storm if endpoint is down.",
            "Check WEBHOOK_SECRET and WEBHOOK_TIMEOUT env vars.",
            "Drain retry queue once endpoint recovers.",
        ],
        "rollback_available": False,
        "confidence": "low",
    },
    # ── Phase 27 playbooks ───────────────────────────────────────────────────
    "platform_cookie_depletion": {
        "id":          "platform_cookie_depletion",
        "name":        "Platform Cookie Depletion",
        "severity":    "critical",
        "description": "A platform's cookie pool dropped to critical levels (<2 healthy). "
                       "Downloads will fail or fall back to unauthenticated mode.",
        "triggers":    ["p27_cookie_low_*", "cookie_pool_depleted_*"],
        "steps": [
            "1. Check Admin → Cookies panel for the affected platform to see which cookies are blocked.",
            "2. Rotate blocked cookies: upload replacement cookies via Admin → Cookies → Add.",
            "3. If all cookies are hard-blocked, wait for hard_block_ttl to expire (see platform policy).",
            "4. If blocked by IP not cookie, switch proxy or enable residential proxy fallback.",
            "5. Consider enabling freeze-scoring for this platform to stop burning remaining cookies: "
               "POST /admin/policy/platform/{platform}/freeze-scoring",
            "6. Once new cookies are active, unfreeze: DELETE /admin/policy/platform/{platform}/freeze-scoring",
        ],
        "rollback_available": False,
        "confidence": "high",
    },
    "platform_reduced_mode": {
        "id":          "platform_reduced_mode",
        "name":        "Platform Reduced / Paused Mode",
        "severity":    "warning",
        "description": "A platform has been stuck in degraded/paused lane state "
                       "or needs to be manually throttled due to rate-limit signals.",
        "triggers":    ["p27_lane_stuck_*", "p27_soft_block_*", "p27_hard_block_*"],
        "steps": [
            "1. Check Admin → Orchestrator → Wave Snapshot for the platform's current lane state.",
            "2. Check obs_recorder failure breakdown for the platform (last 30 min).",
            "3. If soft-block surge: manually set reduced mode for 30-60 min: "
               "POST /admin/policy/platform/{platform}/mode  {mode: 'reduced', ttl: 3600}",
            "4. If hard-block surge: set paused mode + investigate cookie health: "
               "POST /admin/policy/platform/{platform}/mode  {mode: 'paused', ttl: 7200}",
            "5. After block window passes, clear the mode override: "
               "DELETE /admin/policy/platform/{platform}/mode",
            "6. Monitor lane state for 10 min to confirm recovery before removing override.",
        ],
        "rollback_available": True,
        "confidence": "high",
    },
    "wave_scheduler_freeze": {
        "id":          "wave_scheduler_freeze",
        "name":        "Wave Scheduler Freeze (Emergency Throttle)",
        "severity":    "warning",
        "description": "Adaptive wave scheduling is producing erratic behavior "
                       "(oscillating wave size, wrong delay multipliers). Freeze to static params.",
        "triggers":    ["p27_silent_failure_*", "retry_storm"],
        "steps": [
            "1. Check Admin → Orchestrator → Wave Snapshot for platforms with unusual mode transitions.",
            "2. For a specific platform: freeze adaptive scheduling: "
               "POST /admin/policy/platform/{platform}/freeze-adaptive  {ttl: 3600}",
            "3. Or enable global safe mode for all platforms: POST /admin/policy/safe-mode",
            "4. Investigate wave history (p27d:history:{platform} in Redis) for the anomaly.",
            "5. Fix root cause (obs_recorder data spike, lane_observer bug, etc.).",
            "6. Unfreeze: DELETE /admin/policy/platform/{platform}/freeze-adaptive "
               "or DELETE /admin/policy/safe-mode",
        ],
        "rollback_available": True,
        "confidence": "medium",
    },
}


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_playbook(playbook_id: str) -> Optional[dict]:
    """Return a single playbook by ID, or None if not found."""
    return PLAYBOOKS.get(playbook_id)


def get_all_playbooks() -> list[dict]:
    """Return all playbooks as a list."""
    return list(PLAYBOOKS.values())


# ---------------------------------------------------------------------------
# Anomaly → playbook matching
# ---------------------------------------------------------------------------

# Maps anomaly type keywords → playbook ids (ordered by relevance)
_ANOMALY_PLAYBOOK_MAP: list[tuple[list[str], str]] = [
    (["provider_outage", "platform_outage", "mass_failure"], "provider_outage"),
    (["cookie", "token", "login_required", "auth", "sign_in"], "token_invalidation"),
    (["disk", "storage", "space", "volume"], "disk_pressure"),
    (["queue", "backlog", "depth", "pending_spike"], "queue_backlog"),
    (["extraction", "extractor", "yt_dlp", "error_rate", "failure_spike"], "extraction_failure_spike"),
    (["schedule", "drift", "beat", "late", "missed_beat"], "schedule_drift"),
    (["webhook", "notification", "delivery", "endpoint"], "webhook_delivery_failures"),
]


def match_active_playbooks(anomalies: list[dict]) -> list[dict]:
    """
    Given a list of active anomaly dicts (each must have a 'type' or 'name' key),
    return the relevant playbooks (deduplicated, preserving first-match order).

    Example anomaly shape:
      {"type": "disk_pressure", "severity": "critical", "value": 91.2}
    """
    matched_ids: list[str] = []
    seen: set[str] = set()

    for anomaly in anomalies:
        anomaly_type = (anomaly.get("type") or anomaly.get("name") or "").lower()
        for keywords, playbook_id in _ANOMALY_PLAYBOOK_MAP:
            if any(kw in anomaly_type for kw in keywords):
                if playbook_id not in seen:
                    matched_ids.append(playbook_id)
                    seen.add(playbook_id)
                break

    return [PLAYBOOKS[pid] for pid in matched_ids if pid in PLAYBOOKS]


# ---------------------------------------------------------------------------
# Safe remediation actions
# ---------------------------------------------------------------------------

def _action_refresh_cookies(params: dict) -> dict:
    """Trigger cookie pool refresh for a given platform."""
    platform = params.get("platform", "youtube")
    try:
        from app.core.cookie_pool import refresh_pool  # type: ignore
        refresh_pool(platform)
        return {
            "success": True,
            "message": f"Cookie pool refresh triggered for platform={platform}",
            "action_taken": f"refresh_cookies({platform})",
        }
    except ImportError:
        return {
            "success": False,
            "message": "cookie_pool module not available",
            "action_taken": f"refresh_cookies({platform})",
        }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": f"refresh_cookies({platform})",
        }


def _action_lower_concurrency(params: dict) -> dict:
    """Reduce batch_concurrency by 2 via auto_tuner."""
    try:
        from app.core import auto_tuner  # lazy import to avoid circulars
        current = auto_tuner.get_concurrency()
        new_val = max(1, current - 2)
        auto_tuner.set_concurrency(new_val)
        return {
            "success": True,
            "message": f"Concurrency reduced from {current} to {new_val}",
            "action_taken": f"lower_concurrency({current} → {new_val})",
        }
    except ImportError:
        # auto_tuner might not exist yet — fall back to direct Redis write
        try:
            r = get_redis()
            raw = r.get("vidgrab:auto_tuner:concurrency")
            current = int(raw) if raw else 4
            new_val = max(1, current - 2)
            r.set("vidgrab:auto_tuner:concurrency", new_val)
            return {
                "success": True,
                "message": f"Concurrency reduced from {current} to {new_val} (via Redis direct)",
                "action_taken": f"lower_concurrency({current} → {new_val})",
            }
        except Exception as exc:
            return {
                "success": False,
                "message": str(exc)[:300],
                "action_taken": "lower_concurrency(failed)",
            }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": "lower_concurrency(failed)",
        }


def _action_pause_low_priority_schedules(params: dict) -> dict:
    """Set Redis flag to pause low-priority scheduled jobs."""
    try:
        r = get_redis()
        r.set(LOW_PRIORITY_PAUSED_KEY, "1")
        return {
            "success": True,
            "message": f"Low-priority schedules paused (key={LOW_PRIORITY_PAUSED_KEY}=1)",
            "action_taken": "pause_low_priority_schedules()",
        }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": "pause_low_priority_schedules(failed)",
        }


def _action_rotate_fallback_order(params: dict) -> dict:
    """Demote current top fallback for a platform via adaptive_fallback."""
    platform = params.get("platform", "youtube")
    try:
        from app.core import adaptive_fallback  # type: ignore  # lazy import
        adaptive_fallback.demote_top(platform)
        return {
            "success": True,
            "message": f"Top fallback demoted for platform={platform}",
            "action_taken": f"rotate_fallback_order({platform})",
        }
    except ImportError:
        return {
            "success": False,
            "message": "adaptive_fallback module not available",
            "action_taken": f"rotate_fallback_order({platform})",
        }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": f"rotate_fallback_order({platform})",
        }


def _action_trigger_cleanup(params: dict) -> dict:
    """Run an immediate cleanup pass to free disk space."""
    try:
        from app.tasks.maintenance_tasks import cleanup_temp_files  # type: ignore
        cleanup_temp_files.delay()
        return {
            "success": True,
            "message": "Immediate cleanup task dispatched to Celery",
            "action_taken": "trigger_cleanup()",
        }
    except ImportError:
        # Fallback: direct temp file removal
        import os
        import glob
        removed = 0
        errors = 0
        for pattern in ["/tmp/vidgrab/*.part", "/tmp/vidgrab/*.ytdl", "/tmp/vidgrab/*.tmp"]:
            for path in glob.glob(pattern):
                try:
                    os.remove(path)
                    removed += 1
                except Exception:
                    errors += 1
        return {
            "success": True,
            "message": f"Direct cleanup: removed {removed} temp files ({errors} errors)",
            "action_taken": "trigger_cleanup(direct)",
        }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": "trigger_cleanup(failed)",
        }


_ACTION_REGISTRY: dict[str, callable] = {
    "refresh_cookies": _action_refresh_cookies,
    "lower_concurrency": _action_lower_concurrency,
    "pause_low_priority_schedules": _action_pause_low_priority_schedules,
    "rotate_fallback_order": _action_rotate_fallback_order,
    "trigger_cleanup": _action_trigger_cleanup,
}


def execute_safe_action(
    action_name: str,
    params: dict = {},
    *,
    playbook_id: str = "",
    source: str = "manual",
) -> dict:
    """
    Execute a named safe remediation action.

    Returns:
      {success: bool, message: str, action_taken: str}

    Every execution is logged to Redis history (LPUSH, capped at HISTORY_MAX).
    """
    if action_name not in _ACTION_REGISTRY:
        result = {
            "success": False,
            "message": f"Unknown action '{action_name}'. Available: {list(_ACTION_REGISTRY)}",
            "action_taken": action_name,
        }
        _log_history(playbook_id=playbook_id, action=action_name, params=params, result=result, source=source)
        return result

    try:
        result = _ACTION_REGISTRY[action_name](params)
    except Exception as exc:
        result = {
            "success": False,
            "message": f"Unexpected error in action '{action_name}': {exc}",
            "action_taken": action_name,
        }

    _log_history(playbook_id=playbook_id, action=action_name, params=params, result=result, source=source)
    return result


# ---------------------------------------------------------------------------
# Execution history
# ---------------------------------------------------------------------------

def _log_history(
    *,
    playbook_id: str,
    action: str,
    params: dict,
    result: dict,
    source: str,
) -> None:
    """LPUSH execution record to Redis, capped at HISTORY_MAX."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "playbook_id": playbook_id,
        "action": action,
        "params": params,
        "result": result,
        "source": source,
    }
    try:
        r = get_redis()
        r.lpush(HISTORY_KEY, json.dumps(entry))
        r.ltrim(HISTORY_KEY, 0, HISTORY_MAX - 1)
    except Exception as exc:
        logger.warning("[playbooks] Failed to write execution history: %s", exc)


def get_execution_history(limit: int = 50) -> list[dict]:
    """
    Retrieve the last `limit` playbook action executions from Redis.

    Returns a list of dicts ordered newest-first.
    """
    limit = min(limit, HISTORY_MAX)
    try:
        r = get_redis()
        raw_entries = r.lrange(HISTORY_KEY, 0, limit - 1)
        result = []
        for raw in raw_entries:
            try:
                result.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        return result
    except Exception as exc:
        logger.warning("[playbooks] Failed to read execution history: %s", exc)
        return []
