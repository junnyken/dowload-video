"""
Lightweight Job Metrics
=======================
Redis-backed counters and sliding-window metrics for VidGrab job lifecycle.

No external dependency (Prometheus, StatsD).  Uses Redis sorted sets for
rate-window queries and a daily hash for totals.

Redis key scheme
----------------
  vg:m:j:{event}              — sorted set  score=unix_ts  member={platform}:{job_id}
  vg:m:q                      — sorted set  score=unix_ts  member={reason}:{uid}
  vg:m:t:{YYYY-MM-DD}         — hash  field={event}:{platform}  value=count (totals)

All sorted sets capped to 7 days; trimmed lazily on each write.

Usage
-----
    from app.core.metrics import emit_job_event, get_job_metrics

    emit_job_event("succeeded", job_id="abc", platform="youtube", job_type="single")
    metrics = get_job_metrics(window_minutes=30)
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

_WINDOW_DAYS  = 7                         # sorted-set retention
_WINDOW_S     = _WINDOW_DAYS * 86_400

PLATFORMS = [
    "youtube", "instagram", "facebook", "tiktok", "twitter",
    "reddit", "bilibili", "xiaohongshu", "threads", "pinterest",
    "douyin", "spotify", "soundcloud", "other",
]

JOB_EVENTS = [
    "started", "succeeded", "failed", "partial",
    "stale", "retrying", "cancelled", "recovered",
]


def _redis():
    from app.core.redis_client import get_redis
    return get_redis()


def _ts() -> float:
    return time.time()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Job lifecycle events ──────────────────────────────────────────────────────

def emit_job_event(
    event: str,
    job_id: str = "",
    platform: str = "other",
    job_type: str = "single",
    *,
    extra: Optional[dict] = None,
) -> None:
    """
    Record a job lifecycle event.

    Parameters
    ----------
    event    : one of JOB_EVENTS
    job_id   : download_jobs.id (may be empty for anonymous events)
    platform : e.g. "youtube", "tiktok"
    job_type : "single" | "bulk" | "container" | "artist_all"
    extra    : optional dict for future extension (currently unused)
    """
    if event not in JOB_EVENTS:
        return
    try:
        rc = _redis()
        ts = _ts()
        key = f"vg:m:j:{event}"
        member = f"{platform}:{job_id or uuid.uuid4().hex[:8]}"
        rc.zadd(key, {member: ts})
        # Trim entries older than 7 days to bound memory
        rc.zremrangebyscore(key, 0, ts - _WINDOW_S)
        rc.expire(key, _WINDOW_S + 3600)
        # Increment daily total counter
        rc.hincrby(f"vg:m:t:{_today()}", f"{event}:{platform}", 1)
        rc.expire(f"vg:m:t:{_today()}", 8 * 86400)
    except Exception:
        pass  # metrics are best-effort; never fail the caller


def emit_job_events_bulk(
    events: list[dict],
) -> None:
    """Batch-emit multiple events; each dict must have 'event', optionally 'job_id', 'platform'."""
    for e in events:
        emit_job_event(
            e.get("event", ""),
            job_id=e.get("job_id", ""),
            platform=e.get("platform", "other"),
            job_type=e.get("job_type", "single"),
        )


# ── Quota / rate-limit denials ────────────────────────────────────────────────

def track_quota_denial(reason: str, platform: str = "other", user_id: str = "") -> None:
    """
    Record a quota or rate-limit denial event.

    reason: "quota_exceeded_daily" | "tier_limit_batch" | "tier_required_feature" |
            "rate_limit_ip" | "quota_anon" | ...
    """
    try:
        rc = _redis()
        ts = _ts()
        member = f"{reason}:{user_id or uuid.uuid4().hex[:8]}"
        rc.zadd("vg:m:q", {member: ts})
        rc.zremrangebyscore("vg:m:q", 0, ts - _WINDOW_S)
        rc.expire("vg:m:q", _WINDOW_S + 3600)
        rc.hincrby(f"vg:m:t:{_today()}", f"quota_denial:{reason}", 1)
        rc.expire(f"vg:m:t:{_today()}", 8 * 86400)
    except Exception:
        pass


# ── Provider health events ────────────────────────────────────────────────────

def track_provider_event(platform: str, event_type: str) -> None:
    """
    Record a provider-level event such as circuit open/close.
    event_type: "circuit_open" | "circuit_half" | "circuit_closed" |
                "block_detected" | "rate_limited"
    """
    try:
        rc = _redis()
        ts = _ts()
        key = f"vg:m:p:{platform}"
        member = f"{event_type}:{uuid.uuid4().hex[:8]}"
        rc.zadd(key, {member: ts})
        rc.zremrangebyscore(key, 0, ts - _WINDOW_S)
        rc.expire(key, _WINDOW_S + 3600)
        rc.hincrby(f"vg:m:t:{_today()}", f"provider:{platform}:{event_type}", 1)
        rc.expire(f"vg:m:t:{_today()}", 8 * 86400)
    except Exception:
        pass


# ── Aggregated reads ──────────────────────────────────────────────────────────

def get_job_metrics(window_minutes: int = 30) -> dict:
    """
    Return job event counts per-event and per-platform for the given window.

    Returns
    -------
    {
      "window_minutes": 30,
      "by_event": {"succeeded": 40, "failed": 3, ...},
      "by_platform": {"youtube": {"succeeded": 30, "failed": 1}, ...},
      "total": 90,
    }
    """
    result: dict = {
        "window_minutes": window_minutes,
        "by_event": {e: 0 for e in JOB_EVENTS},
        "by_platform": {},
        "total": 0,
    }
    try:
        rc = _redis()
        cutoff = _ts() - window_minutes * 60
        for event in JOB_EVENTS:
            members = rc.zrangebyscore(f"vg:m:j:{event}", cutoff, "+inf")
            count = len(members)
            result["by_event"][event] = count
            result["total"] += count
            for m in members:
                if isinstance(m, bytes):
                    m = m.decode()
                platform = m.split(":", 1)[0]
                result["by_platform"].setdefault(platform, {e: 0 for e in JOB_EVENTS})
                result["by_platform"][platform][event] = result["by_platform"][platform].get(event, 0) + 1
    except Exception:
        pass
    return result


def get_quota_denial_count(window_minutes: int = 30) -> int:
    """Return total quota denial events in the window."""
    try:
        rc = _redis()
        cutoff = _ts() - window_minutes * 60
        return rc.zcount("vg:m:q", cutoff, "+inf")
    except Exception:
        return 0


def get_daily_totals(date: Optional[str] = None) -> dict:
    """Return all event counters for a given UTC date (default today)."""
    try:
        rc = _redis()
        key = f"vg:m:t:{date or _today()}"
        raw = rc.hgetall(key)
        return {
            (k.decode() if isinstance(k, bytes) else k): int(v)
            for k, v in raw.items()
        }
    except Exception:
        return {}


# ── Cookie pool health ────────────────────────────────────────────────────────

def get_cookie_pool_health() -> dict:
    """
    Scan Redis cookie_pool:{platform} keys and classify health per platform.

    Returns
    -------
    {
      "youtube": {"total": 3, "available": 2, "blocked_soft": 1, "blocked_hard": 0},
      ...
    }
    """
    result: dict = {}
    try:
        rc = _redis()
        for platform in PLATFORMS:
            pool_key = f"cookie_pool:{platform}"
            entries = rc.lrange(pool_key, 0, -1)
            if not entries:
                continue
            import hashlib, base64
            total = len(entries)
            blocked_soft = 0
            blocked_hard = 0
            for raw in entries:
                if isinstance(raw, bytes):
                    raw_str = raw.decode()
                else:
                    raw_str = raw
                try:
                    cookie_bytes = base64.b64decode(raw_str)
                    h = hashlib.sha256(cookie_bytes).hexdigest()[:16]
                except Exception:
                    h = hashlib.sha256(raw_str.encode()).hexdigest()[:16]
                health_val = rc.get(f"cookie_health:{platform}:{h}")
                if health_val:
                    health_str = health_val.decode() if isinstance(health_val, bytes) else health_val
                    if health_str == "hard":
                        blocked_hard += 1
                    else:
                        blocked_soft += 1
            available = total - blocked_soft - blocked_hard
            result[platform] = {
                "total": total,
                "available": max(0, available),
                "blocked_soft": blocked_soft,
                "blocked_hard": blocked_hard,
            }
    except Exception:
        pass
    return result


# ── Provider circuit states ───────────────────────────────────────────────────

def get_provider_circuit_states() -> dict:
    """
    Return current circuit breaker state per platform.

    Returns {"youtube": "closed", "instagram": "open", ...}
    """
    try:
        from app.core.platform_circuit import get_state as _get_state
        return {p: _get_state(p) for p in PLATFORMS}
    except Exception:
        return {}


# ── Stale job count ───────────────────────────────────────────────────────────

def get_stale_job_count() -> int:
    """Count jobs currently in 'stale' status in Supabase."""
    try:
        from app.core.database import get_service_client
        sb = get_service_client()
        res = sb.table("download_jobs").select("id", count="exact") \
            .eq("status", "stale").limit(1).execute()
        return res.count or 0
    except Exception:
        return -1  # -1 indicates unavailable


# ── Queue depths ──────────────────────────────────────────────────────────────

def get_queue_depths() -> dict:
    """
    Return Celery queue depths for each named queue.

    Returns {"downloads": 5, "bulk": 0, "media": 2, "light": 0, "celery": 1}
    """
    queues = ["downloads", "bulk", "media", "analysis", "light", "celery"]
    result: dict = {q: 0 for q in queues}
    try:
        rc = _redis()
        for q in queues:
            result[q] = rc.llen(q) or 0
    except Exception:
        pass
    return result
