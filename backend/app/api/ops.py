"""
Ops Signals API
===============
Single aggregated endpoint for operational health signals.

Endpoints (all require admin auth):
  GET /admin/ops-signals  — aggregated ops state
  GET /admin/cookie-health — per-platform cookie pool detail
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from app.api.admin import verify_admin
from app.core.metrics import (
    get_job_metrics,
    get_quota_denial_count,
    get_cookie_pool_health,
    get_provider_circuit_states,
    get_stale_job_count,
    get_queue_depths,
)

router = APIRouter()


def _recovery_log(limit: int = 20) -> list:
    """Read from vidgrab:recovery_log Redis list (most-recent first)."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        raw = rc.lrange("vidgrab:recovery_log", 0, limit - 1)
        entries = []
        for item in raw:
            if isinstance(item, bytes):
                item = item.decode()
            try:
                entries.append(json.loads(item))
            except Exception:
                pass
        return entries
    except Exception:
        return []


def _worker_count() -> int:
    """Best-effort Celery worker count via Redis KEYS scan."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        # Celery workers write a heartbeat key: celery@{hostname}
        # Quick heuristic: count _kombu.{queue}.binding keys (one per active worker)
        # Safer: check vidgrab:pending_tasks SET cardinality as proxy
        # Use celery inspect via cached key if available
        cached = rc.get("vg:worker_count_cache")
        if cached:
            return int(cached)
        return -1  # unknown
    except Exception:
        return -1


def _audit_events(limit: int = 10) -> list:
    """Return recent admin audit log entries from Supabase."""
    try:
        from app.core.audit import get_recent_admin_actions
        return get_recent_admin_actions(limit=limit)
    except Exception:
        return []


@router.get("/ops-signals")
async def get_ops_signals(_: Any = Depends(verify_admin)):
    """
    Single aggregated ops health endpoint.

    Returns job metrics, queue depths, stale job count, provider circuit states,
    cookie pool health, recent recovery log entries, and quota denial rate.
    All sections are best-effort — unavailable data returns safe defaults.
    """
    # --- run synchronously (all Redis calls, fast) ---
    job_metrics = get_job_metrics(window_minutes=30)
    queue_depths = get_queue_depths()
    stale_count = get_stale_job_count()
    circuits = get_provider_circuit_states()
    cookie_health = get_cookie_pool_health()
    quota_denials_30m = get_quota_denial_count(window_minutes=30)
    recovery_log = _recovery_log(limit=20)
    worker_count = _worker_count()

    # Derive quick success/fail ratio
    succeeded = job_metrics["by_event"].get("succeeded", 0)
    failed = job_metrics["by_event"].get("failed", 0)
    total_terminal = succeeded + failed
    success_rate = round(succeeded / total_terminal, 3) if total_terminal > 0 else None

    # Identify platforms with open circuits
    open_platforms = [p for p, state in circuits.items() if state == "open"]

    # Identify platforms with zero available cookies (that have a pool)
    depleted_cookie_platforms = [
        p for p, h in cookie_health.items()
        if h["total"] > 0 and h["available"] == 0
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_minutes": 30,

        # ── Job health ───────────────────────────────────────────
        "job_metrics_30m": job_metrics["by_event"],
        "job_by_platform": job_metrics["by_platform"],
        "success_rate": success_rate,

        # ── Queue ────────────────────────────────────────────────
        "queue_depths": queue_depths,
        "total_queue_depth": sum(queue_depths.values()),

        # ── Stale jobs ───────────────────────────────────────────
        "stale_job_count": stale_count,

        # ── Providers ────────────────────────────────────────────
        "provider_circuits": circuits,
        "open_platforms": open_platforms,

        # ── Cookie health ────────────────────────────────────────
        "cookie_health": cookie_health,
        "depleted_cookie_platforms": depleted_cookie_platforms,

        # ── Quota / rate limit ───────────────────────────────────
        "quota_denials_30m": quota_denials_30m,

        # ── Recovery ─────────────────────────────────────────────
        "recovery_log": recovery_log[:10],  # last 10 for dashboard
        "recovery_actions_30m": _count_recent_recovery(recovery_log, window_minutes=30),

        # ── Workers ──────────────────────────────────────────────
        "worker_count": worker_count,

        # ── Quick summary ────────────────────────────────────────
        "alerts": _derive_alerts(
            stale_count, open_platforms, depleted_cookie_platforms,
            failed, total_terminal, quota_denials_30m,
        ),
    }


@router.get("/cookie-health")
async def get_cookie_health_detail(_: Any = Depends(verify_admin)):
    """Detailed per-platform cookie pool health."""
    health = get_cookie_pool_health()
    # Add meta: is the platform configured at all?
    import os
    configured: dict = {}
    for platform in health:
        env_key = f"{platform.upper()}_COOKIES_B64"
        has_env = bool(os.environ.get(env_key, "").strip())
        configured[platform] = {**health[platform], "env_configured": has_env}
    return {"cookie_health": configured, "generated_at": datetime.now(timezone.utc).isoformat()}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_recent_recovery(log_entries: list, window_minutes: int = 30) -> int:
    """Count log entries within the window."""
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    count = 0
    for entry in log_entries:
        ts_raw = entry.get("ts") or entry.get("timestamp") or entry.get("t")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if ts >= cutoff:
                count += 1
        except Exception:
            pass
    return count


def _derive_alerts(
    stale_count: int,
    open_platforms: list,
    depleted_cookies: list,
    failed: int,
    total_terminal: int,
    quota_denials: int,
) -> list:
    """Return concise actionable alert strings for dashboard rendering."""
    alerts: list = []
    if stale_count > 0:
        alerts.append({"level": "warning", "msg": f"{stale_count} job(s) stuck in stale state"})
    if open_platforms:
        alerts.append({"level": "critical", "msg": f"Circuit OPEN: {', '.join(open_platforms)}"})
    if depleted_cookies:
        alerts.append({"level": "critical", "msg": f"Cookie pool depleted: {', '.join(depleted_cookies)}"})
    if total_terminal > 10 and failed / total_terminal >= 0.5:
        pct = round(failed / total_terminal * 100)
        alerts.append({"level": "critical", "msg": f"High failure rate: {pct}% ({failed}/{total_terminal} jobs)"})
    if quota_denials > 50:
        alerts.append({"level": "warning", "msg": f"High quota denial rate: {quota_denials} denials in 30m"})
    return alerts
