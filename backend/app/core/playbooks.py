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
    (["queue", "backlog", "depth", "pending_spike", "stale"], "queue_backlog"),
    (["extraction", "extractor", "yt_dlp", "error_rate", "failure_spike", "success_drop", "broken"], "extraction_failure_spike"),
    (["schedule", "drift", "beat", "late", "missed_beat"], "schedule_drift"),
    (["webhook", "notification", "delivery", "endpoint"], "webhook_delivery_failures"),
]

AUTO_HEAL_ENABLED_KEY = "vidgrab:auto_heal:enabled"
AUTO_HEAL_LAST_RUN_KEY = "vidgrab:auto_heal:last_run"
AUTO_HEAL_COOLDOWN_PREFIX = "vidgrab:auto_heal:cooldown:"
AUTO_HEAL_COOLDOWN_SEC = 900  # 15 minutes per distinct issue id


def is_auto_heal_enabled() -> bool:
    """Default ON ('1') unless explicitly disabled ('0') by admin."""
    try:
        raw = get_redis().get(AUTO_HEAL_ENABLED_KEY)
        if raw is None:
            return True
        val = raw.decode() if isinstance(raw, bytes) else str(raw)
        return val != "0"
    except Exception:
        return True


def set_auto_heal_enabled(enabled: bool) -> bool:
    try:
        get_redis().set(AUTO_HEAL_ENABLED_KEY, "1" if enabled else "0")
        return enabled
    except Exception:
        return enabled


def match_active_playbooks(anomalies: list[dict]) -> list[dict]:
    """
    Given a list of active anomaly dicts (checks 'metric', 'type', 'name', 'likely_cause'),
    return the relevant playbooks (deduplicated, preserving first-match order).
    """
    matched_ids: list[str] = []
    seen: set[str] = set()

    for anomaly in anomalies:
        haystack = " ".join(
            str(anomaly.get(k) or "")
            for k in ("metric", "type", "name", "likely_cause")
        ).lower()
        for keywords, playbook_id in _ANOMALY_PLAYBOOK_MAP:
            if any(kw in haystack for kw in keywords):
                if playbook_id not in seen:
                    matched_ids.append(playbook_id)
                    seen.add(playbook_id)
                break

    return [PLAYBOOKS[pid] for pid in matched_ids if pid in PLAYBOOKS]


# ---------------------------------------------------------------------------
# Safe remediation actions
# ---------------------------------------------------------------------------

def _action_refresh_cookies(params: dict) -> dict:
    """Trigger cookie pool refresh and clear temporary soft-blocks for a platform."""
    platform = params.get("platform", "all")
    refreshed = []
    try:
        from app.core.cookie_pool import refresh_pool  # type: ignore
        targets = ["youtube", "facebook", "instagram", "tiktok", "twitter"] if platform == "all" else [platform]
        for p in targets:
            try:
                refresh_pool(p)
                refreshed.append(p)
            except Exception:
                pass
    except Exception:
        pass

    # Also clear any transient cookie cooldown flags in Redis
    cleared_keys = 0
    try:
        r = get_redis()
        pattern = "cookie:cooldown:*" if platform == "all" else f"cookie:cooldown:{platform}:*"
        for k in r.scan_iter(match=pattern, count=100):
            r.delete(k)
            cleared_keys += 1
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Đã làm mới bể Cookie ({', '.join(refreshed) or platform}) và gỡ {cleared_keys} cờ tạm khoá.",
        "action_taken": f"refresh_cookies({platform})",
    }


def _action_lower_concurrency(params: dict) -> dict:
    """Reduce batch_concurrency by 2 via auto_tuner."""
    try:
        from app.core import auto_tuner
        current = int(auto_tuner.get_param("batch_concurrency"))
        new_val = max(2, current - 2)
        auto_tuner.set_param("batch_concurrency", new_val, reason="Auto-Heal giảm tải đồng thời", source="auto_heal")
        return {
            "success": True,
            "message": f"Đã giảm tải đồng thời (batch_concurrency) từ {current} xuống {new_val}",
            "action_taken": f"lower_concurrency({current} → {new_val})",
        }
    except Exception:
        try:
            r = get_redis()
            raw = r.hget("vidgrab:tuning:params", "batch_concurrency")
            current = int(raw) if raw else 8
            new_val = max(2, current - 2)
            r.hset("vidgrab:tuning:params", "batch_concurrency", str(new_val))
            return {
                "success": True,
                "message": f"Đã giảm tải đồng thời từ {current} xuống {new_val}",
                "action_taken": f"lower_concurrency({current} → {new_val})",
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
        r.set(LOW_PRIORITY_PAUSED_KEY, "1", ex=3600 * 4)
        return {
            "success": True,
            "message": "Đã tạm dừng các lịch quét ưu tiên thấp trong 4 giờ để ưu tiên hàng đợi chính.",
            "action_taken": "pause_low_priority_schedules()",
        }
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": "pause_low_priority_schedules(failed)",
        }


def _action_rotate_fallback_order(params: dict) -> dict:
    """Put Cobalt first for FB/IG/X (for 6h), or trigger yt-dlp remediation for other platforms."""
    platform = params.get("platform", "facebook")
    try:
        from app.core import platform_health  # type: ignore
        targets = ["facebook", "instagram", "twitter"] if platform in ("all", "youtube") else [platform]
        switched = []
        for p in targets:
            if platform_health.set_prefer_cobalt(p, True, "Auto-Heal chuyển ưu tiên sang Cobalt"):
                switched.append(p)
        if switched:
            return {
                "success": True,
                "message": f"Đã chuyển hướng ưu tiên sang máy chủ Cobalt trong 6 giờ cho: {', '.join(switched)}",
                "action_taken": f"rotate_fallback_order({','.join(switched)})",
            }
        # If platform is not a Cobalt platform (e.g. tiktok/youtube), trigger yt-dlp remediation
        return _action_upgrade_ytdlp({"platform": platform})
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": f"rotate_fallback_order({platform})",
        }


def _action_upgrade_ytdlp(params: dict) -> dict:
    """Enqueue hot yt-dlp upgrade / remediation task."""
    platform = params.get("platform", "youtube")
    try:
        from app.tasks.video_tasks import ytdlp_remediate  # type: ignore
        ytdlp_remediate.apply_async(args=[[platform], f"Auto-Heal remediation for {platform}"], queue="light")
        return {
            "success": True,
            "message": f"Đã kích hoạt tác vụ tự động nâng cấp nóng bộ giải mã yt-dlp cho {platform}.",
            "action_taken": f"upgrade_ytdlp({platform})",
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Không gửi được tác vụ nâng cấp yt-dlp: {str(exc)[:200]}",
            "action_taken": f"upgrade_ytdlp({platform})",
        }


def _action_recover_stale_jobs(params: dict) -> dict:
    """Find stuck jobs (>15 min in pending/processing/queued) in download_jobs & partner_jobs and mark failed/cleaned."""
    cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=15)).isoformat()
    cleaned_dl = 0
    cleaned_partner = 0
    try:
        from app.core.database import get_service_client
        db = get_service_client()
        res1 = (
            db.table("download_jobs")
            .update({"status": "failed", "error_message": "Auto-Heal: Giải phóng job bị treo quá 15 phút"})
            .in_("status", ["pending", "processing"])
            .lt("created_at", cutoff)
            .execute()
        )
        cleaned_dl = len(res1.data or [])
    except Exception:
        pass

    try:
        from app.core.database import get_service_client
        db = get_service_client()
        res2 = (
            db.table("partner_jobs")
            .update({"status": "failed", "error_message": "Auto-Heal: Giải phóng job bị treo quá 15 phút"})
            .in_("status", ["queued", "processing"])
            .lt("created_at", cutoff)
            .execute()
        )
        cleaned_partner = len(res2.data or [])
    except Exception:
        pass

    return {
        "success": True,
        "message": f"Đã giải phóng {cleaned_dl} job tải web và {cleaned_partner} job Partner API bị kẹt quá 15 phút.",
        "action_taken": f"recover_stale_jobs(dl={cleaned_dl}, partner={cleaned_partner})",
    }


def _action_reset_proxy_circuits(params: dict) -> dict:
    """Reset open provider/proxy circuit breakers in Redis so traffic can retry healthy routes."""
    cleared = 0
    try:
        r = get_redis()
        for pattern in ("*circuit*", "*cooldown*", "apify:pool:cooldown:*", "health:degraded"):
            for k in r.scan_iter(match=pattern, count=100):
                r.delete(k)
                cleared += 1
    except Exception as exc:
        return {
            "success": False,
            "message": str(exc)[:300],
            "action_taken": "reset_proxy_circuits(failed)",
        }
    return {
        "success": True,
        "message": f"Đã đặt lại {cleared} mạch ngắt (Circuit Breaker / Cooldown) của Proxy & Provider.",
        "action_taken": f"reset_proxy_circuits(cleared={cleared})",
    }


def _action_restore_normal_mode(params: dict) -> dict:
    """Restore auto_tuner params to defaults and unpause low-priority schedules."""
    try:
        from app.core.auto_tuner import reset_to_defaults
        reset_to_defaults()
    except Exception:
        pass
    try:
        r = get_redis()
        r.delete(LOW_PRIORITY_PAUSED_KEY)
    except Exception:
        pass
    return {
        "success": True,
        "message": "Đã khôi phục thông số tải đồng thời về mặc định và bật lại toàn bộ lịch định kỳ.",
        "action_taken": "restore_normal_mode()",
    }


def _action_trigger_cleanup(params: dict) -> dict:
    """Run an immediate cleanup pass to free disk space."""
    removed = 0
    import os
    import glob
    for pattern in ["/tmp/vidgrab/*.part", "/tmp/vidgrab/*.ytdl", "/tmp/vidgrab/*.tmp", "/tmp/*.part"]:
        for path in glob.glob(pattern):
            try:
                os.remove(path)
                removed += 1
            except Exception:
                pass
    try:
        from app.tasks.maintenance_tasks import cleanup_temp_files  # type: ignore
        cleanup_temp_files.delay()
        return {
            "success": True,
            "message": f"Đã xoá trực tiếp {removed} file rác tạm và kích hoạt tác vụ dọn ổ cứng sâu.",
            "action_taken": "trigger_cleanup()",
        }
    except Exception:
        return {
            "success": True,
            "message": f"Đã dọn trực tiếp {removed} file tạm trên ổ cứng.",
            "action_taken": f"trigger_cleanup(removed={removed})",
        }


_ACTION_REGISTRY: dict[str, callable] = {
    "refresh_cookies": _action_refresh_cookies,
    "lower_concurrency": _action_lower_concurrency,
    "pause_low_priority_schedules": _action_pause_low_priority_schedules,
    "rotate_fallback_order": _action_rotate_fallback_order,
    "upgrade_ytdlp": _action_upgrade_ytdlp,
    "recover_stale_jobs": _action_recover_stale_jobs,
    "reset_proxy_circuits": _action_reset_proxy_circuits,
    "restore_normal_mode": _action_restore_normal_mode,
    "trigger_cleanup": _action_trigger_cleanup,
}


# ---------------------------------------------------------------------------
# Real-time Diagnostics & Self-Healing Engine (Auto-Heal)
# ---------------------------------------------------------------------------

def diagnose_system_issues() -> list[dict]:
    """
    Inspect all live health signals (platform outcomes, anomalies, stale jobs,
    circuit breakers, cookie pools, disk space, queue depth) and return a list
    of actionable diagnostic issues with recommended auto-remediation steps.
    """
    issues: list[dict] = []

    # 1. Check platform health (last 2 hours)
    try:
        from app.core import platform_health as ph
        counts = ph.recent_counts(hours=2)
        for platform, c in sorted(counts.items()):
            if platform in ("other", "unknown"):
                continue
            v = ph.verdict(c)
            if not v:
                continue
            base = max(1, c.get("total", 0) - c.get("user_err", 0))
            err_pct = round(100 * c.get("system_err", 0) / base)
            if v == "broken":
                actions = [{"action": "upgrade_ytdlp", "params": {"platform": platform}}]
                if platform in ph.COBALT_ROUTE_PLATFORMS:
                    actions.insert(0, {"action": "rotate_fallback_order", "params": {"platform": platform}})
                issues.append({
                    "id": f"platform_broken_{platform}",
                    "severity": "critical",
                    "title": f"Nền tảng {ph.display(platform)} đổi cấu trúc trang ({c.get('broken', 0)}/{c.get('total', 0)} lượt lỗi)",
                    "cause": f"Bộ trích xuất không đọc được dữ liệu từ {ph.display(platform)} trong 2 giờ qua.",
                    "solution_summary": (
                        f"Chuyển hướng ưu tiên {ph.display(platform)} sang máy chủ Cobalt trong 6 giờ và tự động nâng cấp nóng yt-dlp."
                        if platform in ph.COBALT_ROUTE_PLATFORMS
                        else f"Tự động tải và cập nhật nóng bản yt-dlp mới nhất cho {ph.display(platform)}."
                    ),
                    "recommended_actions": actions,
                })
            else:
                actions = [{"action": "refresh_cookies", "params": {"platform": platform}}]
                if platform in ph.COBALT_ROUTE_PLATFORMS:
                    actions.insert(0, {"action": "rotate_fallback_order", "params": {"platform": platform}})
                else:
                    actions.append({"action": "reset_proxy_circuits", "params": {"platform": platform}})
                issues.append({
                    "id": f"platform_{v}_{platform}",
                    "severity": "critical" if v == "critical" else "warning",
                    "title": f"Tỷ lệ lỗi hệ thống trên {ph.display(platform)} cao ({err_pct}%)",
                    "cause": f"{c.get('system_err', 0)}/{base} lượt tải hợp lệ bị lỗi hệ thống (chặn IP/Cookie hoặc hết hạn phiên).",
                    "solution_summary": (
                        f"Chuyển ưu tiên {ph.display(platform)} sang Cobalt và làm mới bể Cookie/Proxy."
                        if platform in ph.COBALT_ROUTE_PLATFORMS
                        else f"Làm mới bể Cookie và đặt lại mạch ngắt Proxy cho {ph.display(platform)}."
                    ),
                    "recommended_actions": actions,
                })
    except Exception as exc:
        logger.debug("[diagnose] platform_health check skipped: %s", exc)

    # 2. Check stale / stuck jobs, open circuit breakers, depleted cookies, queue depth
    try:
        from app.core.metrics import (
            get_stale_job_count,
            get_provider_circuit_states,
            get_cookie_pool_health,
            get_queue_depths,
        )
        stale_count = get_stale_job_count()
        if stale_count > 0:
            issues.append({
                "id": "stale_jobs_stuck",
                "severity": "warning",
                "title": f"Có {stale_count} Job đang bị treo quá thời gian quy định",
                "cause": "Một số tiến trình tải bị ngắt giữa chừng khiến job giữ trạng thái đang xử lý quá lâu.",
                "solution_summary": "Tự động giải phóng các job bị treo quá 15 phút để làm sạch hàng đợi.",
                "recommended_actions": [{"action": "recover_stale_jobs", "params": {}}],
            })

        circuits = get_provider_circuit_states() or {}
        open_circuits = [p for p, st in circuits.items() if st == "open"]
        if open_circuits:
            issues.append({
                "id": f"circuit_open_{'_'.join(sorted(open_circuits))}",
                "severity": "critical",
                "title": f"Mạch bảo vệ (Circuit Breaker) đang ngắt tạm thời: {', '.join(open_circuits)}",
                "cause": "Nền tảng hoặc nhà cung cấp gặp chuỗi lỗi liên tiếp trước đó nên bị tạm ngắt.",
                "solution_summary": "Đặt lại mạch ngắt (Reset Circuit Breaker) và làm mới Cookie để khôi phục luồng tải.",
                "recommended_actions": [
                    {"action": "reset_proxy_circuits", "params": {}},
                    {"action": "refresh_cookies", "params": {"platform": open_circuits[0]}},
                ],
            })

        cookie_health = get_cookie_pool_health() or {}
        depleted = [p for p, h in cookie_health.items() if h.get("total", 0) > 0 and h.get("available", 0) == 0]
        if depleted:
            issues.append({
                "id": f"cookie_depleted_{'_'.join(sorted(depleted))}",
                "severity": "critical",
                "title": f"Bể Cookie cạn kiệt cho nền tảng: {', '.join(depleted)}",
                "cause": "Tất cả Cookie trong bể của nền tảng này đang bị đánh dấu tạm nghỉ hoặc hết hạn.",
                "solution_summary": "Gỡ cờ tạm khoá và làm mới lại bể Cookie ngay lập tức.",
                "recommended_actions": [
                    {"action": "refresh_cookies", "params": {"platform": p}} for p in depleted
                ],
            })

        q_depths = get_queue_depths() or {}
        total_q = sum(q_depths.values())
        if total_q > 80:
            issues.append({
                "id": "queue_backlog_high",
                "severity": "warning",
                "title": f"Hàng đợi đang tồn đọng cao ({total_q} tác vụ)",
                "cause": "Lưu lượng tải hàng loạt tăng đột biến vượt tốc độ xử lý tức thời của Worker.",
                "solution_summary": "Tạm dừng lịch quét ưu tiên thấp và giải phóng các job bị kẹt.",
                "recommended_actions": [
                    {"action": "pause_low_priority_schedules", "params": {}},
                    {"action": "recover_stale_jobs", "params": {}},
                ],
            })
    except Exception as exc:
        logger.debug("[diagnose] metrics check skipped: %s", exc)

    # 3. Check disk usage
    try:
        import shutil
        usage = shutil.disk_usage("/")
        disk_pct = round(usage.used / usage.total * 100, 1)
        if disk_pct >= 80.0:
            issues.append({
                "id": "disk_pressure_high",
                "severity": "critical" if disk_pct >= 90.0 else "warning",
                "title": f"Dung lượng ổ cứng máy chủ đã dùng {disk_pct}%",
                "cause": "Các tệp video tải tạm tích tụ trên ổ cứng của Worker.",
                "solution_summary": "Chạy tác vụ dọn dẹp ngay toàn bộ tệp tạm (.part, .ytdl, .tmp) để giải phóng dung lượng.",
                "recommended_actions": [{"action": "trigger_cleanup", "params": {}}],
            })
    except Exception:
        pass

    # 4. Also fold in any active anomalies from anomaly_detector not already covered
    try:
        from app.core.anomaly_detector import get_active_anomalies
        for anom in get_active_anomalies():
            metric = str(anom.get("metric") or "")
            plat = metric.split(":", 1)[1] if ":" in metric else "all"
            issue_id = f"anomaly_{metric.replace(':', '_')}"
            if any(plat != "all" and plat in existing["id"] for existing in issues):
                continue
            matched_pbs = match_active_playbooks([anom])
            pb = matched_pbs[0] if matched_pbs else None
            auto_acts = (pb.get("auto_actions") if pb else None) or ["refresh_cookies"]
            issues.append({
                "id": issue_id,
                "severity": "warning",
                "title": f"Bất thường chỉ số: {metric}",
                "cause": anom.get("likely_cause") or "Hệ thống phát hiện biến động bất thường so với trung bình 7 ngày.",
                "solution_summary": pb.get("description") if pb else "Thực thi kịch bản tự cân bằng tải và làm mới phiên.",
                "recommended_actions": [
                    {"action": act, "params": {"platform": plat} if plat != "all" else {}}
                    for act in auto_acts
                ],
            })
    except Exception:
        pass

    return issues


def run_auto_heal(*, force: bool = False) -> dict:
    """
    Diagnose all current system issues and automatically execute their
    recommended remediation actions when Auto-Heal is enabled (or when force=True).
    """
    enabled = is_auto_heal_enabled()
    issues = diagnose_system_issues()
    executed: list[dict] = []
    skipped_cooldown: list[str] = []

    try:
        r = get_redis()
        r.set(AUTO_HEAL_LAST_RUN_KEY, datetime.now(timezone.utc).isoformat(), ex=86400 * 7)
    except Exception:
        r = None

    if not enabled and not force:
        return {
            "auto_heal_enabled": enabled,
            "issues_found": len(issues),
            "issues": issues,
            "actions_executed": [],
            "skipped_cooldown": [],
        }

    for issue in issues:
        iid = issue["id"]
        if not force and r is not None:
            cd_key = f"{AUTO_HEAL_COOLDOWN_PREFIX}{iid}"
            try:
                if not r.set(cd_key, "1", nx=True, ex=AUTO_HEAL_COOLDOWN_SEC):
                    skipped_cooldown.append(iid)
                    continue
            except Exception:
                pass

        for step in issue.get("recommended_actions") or []:
            act_name = step.get("action")
            act_params = step.get("params") or {}
            if not act_name:
                continue
            res = execute_safe_action(
                act_name,
                act_params,
                playbook_id=iid,
                source="auto_heal",
                reason=f"{issue['title']} → {issue['solution_summary']}",
            )
            executed.append({
                "issue_id": iid,
                "issue_title": issue["title"],
                "action": act_name,
                "params": act_params,
                "result": res,
            })

    if executed:
        try:
            from app.core.alerts import send_admin_alert
            summary_lines = [
                f"• {item['issue_title']}: {item['result'].get('message', '')}"
                for item in executed
            ]
            send_admin_alert(
                "warning",
                f"🤖 Tự khắc phục lỗi (Auto-Heal) đã xử lý {len(executed)} tác vụ",
                "\n".join(summary_lines[:6]),
                alert_key="auto_heal_summary",
            )
        except Exception:
            pass

    return {
        "auto_heal_enabled": enabled,
        "issues_found": len(issues),
        "issues": issues,
        "actions_executed": executed,
        "skipped_cooldown": skipped_cooldown,
    }


def execute_safe_action(
    action_name: str,
    params: dict = {},
    *,
    playbook_id: str = "",
    source: str = "manual",
    reason: str = "",
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
        _log_history(playbook_id=playbook_id, action=action_name, params=params, result=result, source=source, reason=reason)
        return result

    try:
        result = _ACTION_REGISTRY[action_name](params)
    except Exception as exc:
        result = {
            "success": False,
            "message": f"Unexpected error in action '{action_name}': {exc}",
            "action_taken": action_name,
        }

    _log_history(playbook_id=playbook_id, action=action_name, params=params, result=result, source=source, reason=reason)
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
    reason: str = "",
) -> None:
    """LPUSH execution record to Redis, capped at HISTORY_MAX."""
    pb = PLAYBOOKS.get(playbook_id)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "playbook_id": playbook_id,
        "action": result.get("action_taken") or action,
        "params": params,
        "reason": reason or (pb.get("name") if pb else playbook_id) or "Quản trị viên kích hoạt",
        "outcome": result.get("message") if isinstance(result, dict) else str(result),
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
