"""
Usage Metering Pipeline — VidGrab Phase 20
==========================================
Records usage events for billing, analytics, and quota enforcement.

Architecture:
  1. record_*()  → write usage_events row (idempotent via idempotency_key)
                → increment Redis daily counter (fast quota reads)
                → update user_usage row (backward-compatible with quotas.py)

  2. get_daily_count() → Redis first, DB fallback

  3. check_quota()     → returns QuotaStatus (ok / warning / blocked)

Redis key convention:
  vg:usage:{user_id}:{metric}:{YYYY-MM-DD}   → integer count, TTL 48h

Idempotency:
  idempotency_key = f"{event_type}:{job_id}"
  duplicate events are silently ignored (INSERT ... ON CONFLICT DO NOTHING)
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Redis TTL for daily counters (48h — covers midnight rollover)
_REDIS_TTL = 60 * 60 * 48


class QuotaStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"    # >= 80% consumed
    BLOCKED = "blocked"    # >= 100% consumed


@dataclass
class QuotaResult:
    status: QuotaStatus
    metric: str
    used: int
    limit: int             # -1 = unlimited
    remaining: int         # -1 = unlimited
    percent: float         # 0-100; -1 = unlimited
    warning_threshold: float = 0.80

    @property
    def is_blocked(self) -> bool:
        return self.status == QuotaStatus.BLOCKED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "metric": self.metric,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
            "percent": self.percent,
        }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _today_str() -> str:
    return date.today().isoformat()


def _redis_key(user_id: str, metric: str, day: Optional[str] = None) -> str:
    return f"vg:usage:{user_id}:{metric}:{day or _today_str()}"


def _get_redis():
    """Lazy Redis client via existing cache module."""
    try:
        from app.core.cache import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def _get_db():
    from app.core.database import get_service_client
    return get_service_client()


# ── Core recording functions ──────────────────────────────────────────────────

async def record_event(
    *,
    event_type: str,
    metric: str,
    user_id: Optional[str] = None,
    job_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    quantity: int = 1,
    plan: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Record a usage event. Returns True on success, False if duplicate.
    Fire-and-forget safe — swallows errors after logging.
    """
    idempotency_key = f"{event_type}:{job_id}" if job_id else None
    day = _today_str()

    db = _get_db()

    # 1. Write to usage_events (idempotent)
    row: Dict[str, Any] = {
        "event_type": event_type,
        "metric": metric,
        "quantity": quantity,
        "plan": plan,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if user_id:
        row["user_id"] = user_id
    if job_id:
        row["job_id"] = job_id
    if workspace_id:
        row["workspace_id"] = workspace_id
    if tenant_id:
        row["tenant_id"] = tenant_id
    if idempotency_key:
        row["idempotency_key"] = idempotency_key
    if metadata:
        row["metadata"] = metadata

    try:
        db.table("usage_events").upsert(
            row,
            on_conflict="idempotency_key" if idempotency_key else None,
            ignore_duplicates=True,
        ).execute()
    except Exception as exc:
        logger.warning("usage_events upsert failed: %s", exc)

    if not user_id:
        return True  # anonymous — skip Redis + user_usage

    # 2. Increment Redis counter (non-blocking)
    redis = _get_redis()
    if redis:
        key = _redis_key(user_id, metric, day)
        try:
            redis.incrby(key, quantity)
            redis.expire(key, _REDIS_TTL)
        except Exception as exc:
            logger.debug("Redis metering error: %s", exc)

    # 3. Update user_usage row for backward compat with quotas.py
    _update_user_usage_async(user_id, metric, quantity, day)

    return True


def _update_user_usage_async(user_id: str, metric: str, quantity: int, day: str):
    """Fire-and-forget update of user_usage row."""
    try:
        db = _get_db()
        col_map = {
            "downloads":    "downloads_today",
            "ai_analyses":  "ai_analyses_today",
            "batch_items":  "batch_items_today",
            "zip_exports":  "zip_exports_today",
        }
        col = col_map.get(metric)
        if not col:
            return

        # Lazy reset check — if last_reset_at < today, reset counters first
        res = db.table("user_usage").select("last_reset_at").eq("user_id", user_id).maybe_single().execute()
        row_data = res.data or {}
        last_reset = row_data.get("last_reset_at", "")
        needs_reset = not last_reset or not last_reset.startswith(day)

        if needs_reset:
            db.table("user_usage").upsert({
                "user_id": user_id,
                "downloads_today": 0,
                "bulk_jobs_count": 0,
                "ai_analyses_today": 0,
                "batch_items_today": 0,
                "zip_exports_today": 0,
                "last_reset_at": f"{day}T00:00:00+00:00",
            }, on_conflict="user_id").execute()

        db.rpc("increment_user_usage", {
            "p_user_id": user_id,
            "p_col": col,
            "p_qty": quantity,
        }).execute()
    except Exception as exc:
        logger.debug("user_usage update error: %s", exc)


# ── Public shorthand recorders ────────────────────────────────────────────────

async def record_download(
    user_id: str,
    *,
    job_id: Optional[str] = None,
    plan: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> bool:
    return await record_event(
        event_type="download",
        metric="downloads",
        user_id=user_id,
        job_id=job_id,
        plan=plan,
        metadata=metadata,
    )


async def record_ai_analysis(
    user_id: str,
    *,
    job_id: Optional[str] = None,
    plan: Optional[str] = None,
    media_minutes: Optional[float] = None,
) -> bool:
    meta = {"media_minutes": media_minutes} if media_minutes else None
    return await record_event(
        event_type="ai_analysis",
        metric="ai_analyses",
        user_id=user_id,
        job_id=job_id,
        plan=plan,
        metadata=meta,
    )


async def record_batch_job(
    user_id: str,
    *,
    job_id: Optional[str] = None,
    item_count: int = 1,
    plan: Optional[str] = None,
) -> bool:
    return await record_event(
        event_type="batch_job",
        metric="batch_items",
        user_id=user_id,
        job_id=job_id,
        quantity=item_count,
        plan=plan,
    )


# ── Quota reading ─────────────────────────────────────────────────────────────

async def get_daily_count(user_id: str, metric: str, day: Optional[str] = None) -> int:
    """
    Return current daily count for user+metric.
    Redis first (fast), falls back to usage_events DB sum.
    """
    day = day or _today_str()

    # Try Redis
    redis = _get_redis()
    if redis:
        try:
            val = redis.get(_redis_key(user_id, metric, day))
            if val is not None:
                return int(val)
        except Exception:
            pass

    # Fallback: sum from usage_events for today
    try:
        db = _get_db()
        start = f"{day}T00:00:00+00:00"
        end   = f"{day}T23:59:59+00:00"
        res = (
            db.table("usage_events")
            .select("quantity")
            .eq("user_id", user_id)
            .eq("metric", metric)
            .gte("created_at", start)
            .lte("created_at", end)
            .execute()
        )
        total = sum(row.get("quantity", 0) for row in (res.data or []))
        # Warm Redis
        if redis and total > 0:
            try:
                key = _redis_key(user_id, metric, day)
                redis.set(key, total, ex=_REDIS_TTL)
            except Exception:
                pass
        return total
    except Exception as exc:
        logger.warning("get_daily_count DB fallback error: %s", exc)
        return 0


async def check_quota(
    user_id: str,
    metric: str,
    limit: int,
    *,
    warning_threshold: float = 0.80,
) -> QuotaResult:
    """
    Check if user is within quota for metric.

    Args:
        limit: -1 = unlimited. 0 = feature not available (always blocked).
    """
    if limit == -1:
        return QuotaResult(
            status=QuotaStatus.OK,
            metric=metric,
            used=0,
            limit=-1,
            remaining=-1,
            percent=-1,
        )

    if limit == 0:
        return QuotaResult(
            status=QuotaStatus.BLOCKED,
            metric=metric,
            used=0,
            limit=0,
            remaining=0,
            percent=100.0,
        )

    used = await get_daily_count(user_id, metric)
    remaining = max(0, limit - used)
    percent = (used / limit * 100.0) if limit > 0 else 0.0

    if used >= limit:
        status = QuotaStatus.BLOCKED
    elif percent >= warning_threshold * 100:
        status = QuotaStatus.WARNING
    else:
        status = QuotaStatus.OK

    return QuotaResult(
        status=status,
        metric=metric,
        used=used,
        limit=limit,
        remaining=remaining,
        percent=round(percent, 1),
        warning_threshold=warning_threshold,
    )


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def enforce_download_quota(request, user_id: str, tier: str) -> QuotaResult:
    """
    Check download quota and raise 402 if blocked.
    Import and call directly from endpoint handlers.
    """
    from app.core.entitlements import get_limit
    from fastapi import HTTPException

    limit = get_limit(tier, "downloads_per_day")
    result = await check_quota(user_id, "downloads", limit)

    if result.is_blocked:
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "quota_exceeded_daily",
                "used": result.used,
                "limit": result.limit,
                "reset_at": "midnight UTC",
                "upgrade_url": "/upgrade",
            },
        )
    return result


async def enforce_ai_quota(request, user_id: str, tier: str) -> QuotaResult:
    """Check AI analysis quota and raise 402 if blocked."""
    from app.core.entitlements import get_limit, check_feature
    from fastapi import HTTPException

    if not check_feature(tier, "ai_tools"):
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "feature": "ai_tools",
                "required_plan": "pro",
                "current_plan": tier,
                "upgrade_url": "/upgrade",
            },
        )

    limit = get_limit(tier, "ai_analyses_per_day")
    result = await check_quota(user_id, "ai_analyses", limit)

    if result.is_blocked:
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "quota_exceeded_ai_analyses",
                "used": result.used,
                "limit": result.limit,
                "upgrade_url": "/upgrade",
            },
        )
    return result
