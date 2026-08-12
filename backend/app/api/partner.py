"""
Partner API
===========
API-first partner endpoints for B2B integrations.
All endpoints require a valid partner API key (vgp_ prefix).

Routes:
  POST   /api/v1/partner/jobs           — submit download job
  GET    /api/v1/partner/jobs/{job_id}  — poll job status
  GET    /api/v1/partner/jobs           — list recent jobs
  DELETE /api/v1/partner/jobs/{job_id}  — cancel job
  GET    /api/v1/partner/usage          — current usage stats
  POST   /api/v1/partner/webhooks       — register webhook
  GET    /api/v1/partner/webhooks       — list webhooks
  DELETE /api/v1/partner/webhooks/{id}  — remove webhook
  POST   /api/v1/partner/validate-urls  — batch URL validation
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.database import get_service_client
from app.core.partner_auth import get_partner_tenant
from app.core.tenant import TenantContext, TenantPlan

router = APIRouter(tags=["Partner API"])

_PLAN_PRIORITY: dict[str, int] = {
    TenantPlan.starter:    5,
    TenantPlan.growth:     3,
    TenantPlan.enterprise: 1,   # highest priority (lower number = higher)
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class JobSubmitRequest(BaseModel):
    url:     str
    quality: str                 = "best"
    format:  Optional[str]       = None
    options: Dict[str, Any]      = Field(default_factory=dict)


class JobResponse(BaseModel):
    id:          str
    status:      str
    url:         str
    quality:     str
    format:      Optional[str]
    options:     Dict[str, Any]
    priority:    int
    tenant_id:   str
    api_key_id:  Optional[str]
    created_at:  str
    result:      Optional[Dict[str, Any]] = None
    error:       Optional[str]            = None


class WebhookRegisterRequest(BaseModel):
    url:    str
    events: List[str]
    label:  Optional[str] = None


class ValidateUrlsRequest(BaseModel):
    urls: List[str] = Field(..., max_items=100)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_scope(tenant: TenantContext, scope: str, status: int = 403) -> None:
    if not tenant.has_scope(scope):
        raise HTTPException(
            status_code=status,
            detail={
                "code":    "insufficient_scope",
                "message": f"This operation requires the '{scope}' scope",
            },
        )


def _validate_http_url(url: str, require_https: bool = False) -> None:
    scheme = url.split("://")[0].lower() if "://" in url else ""
    if require_https:
        if scheme != "https":
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_url", "message": "URL must use HTTPS"},
            )
    else:
        if scheme not in ("http", "https"):
            raise HTTPException(
                status_code=422,
                detail={
                    "code":    "invalid_url",
                    "message": "URL must start with http:// or https://",
                },
            )


def _check_daily_rate_limit(tenant: TenantContext) -> None:
    """Query tenant_usage_daily for today and enforce plan limit."""
    limits = tenant.plan_limits()
    monthly_cap = limits["api_calls_per_month"]
    if monthly_cap == -1:
        return  # enterprise: unlimited

    supabase = get_service_client()
    today    = date.today().isoformat()

    try:
        usage_res = (
            supabase.table("tenant_usage_daily")
            .select("api_calls")
            .eq("tenant_id", tenant.tenant_id)
            .eq("date", today)
            .maybe_single()
            .execute()
        )
        usage_row = usage_res.data or {}
        calls_today = usage_row.get("api_calls") or 0
    except Exception:
        calls_today = 0

    # daily cap = rate_limit_per_day from the key (set on key creation)
    if calls_today >= tenant.rate_limit_per_day:
        raise HTTPException(
            status_code=429,
            detail={
                "code":      "rate_limit_exceeded",
                "message":   "Daily API call limit reached",
                "limit":     tenant.rate_limit_per_day,
                "used_today": calls_today,
            },
        )


def _increment_usage(tenant_id: str) -> None:
    """Upsert today's usage row, incrementing api_calls by 1 (best-effort)."""
    try:
        supabase = get_service_client()
        today    = date.today().isoformat()

        # Try server-side RPC first
        try:
            supabase.rpc(
                "increment_tenant_usage",
                {"p_tenant_id": tenant_id, "p_date": today},
            ).execute()
            return
        except Exception:
            pass

        # Fallback: upsert with manual increment
        try:
            existing = (
                supabase.table("tenant_usage_daily")
                .select("id, api_calls")
                .eq("tenant_id", tenant_id)
                .eq("date", today)
                .maybe_single()
                .execute()
            )
            row = existing.data
        except Exception:
            row = None

        if row:
            supabase.table("tenant_usage_daily").update(
                {"api_calls": (row.get("api_calls") or 0) + 1}
            ).eq("id", row["id"]).execute()
        else:
            supabase.table("tenant_usage_daily").insert(
                {
                    "id":         str(uuid.uuid4()),
                    "tenant_id":  tenant_id,
                    "date":       today,
                    "api_calls":  1,
                }
            ).execute()
    except Exception:
        pass  # never let a counter update kill a real request


# ── Jobs ──────────────────────────────────────────────────────────────────────

@router.post("/api/v1/partner/jobs", response_model=JobResponse, status_code=202)
async def submit_job(
    body:   JobSubmitRequest,
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """Submit a download job on behalf of a tenant."""
    _require_scope(tenant, "write")
    _validate_http_url(body.url)
    _check_daily_rate_limit(tenant)

    supabase  = get_service_client()
    job_id    = str(uuid.uuid4())
    priority  = _PLAN_PRIORITY.get(tenant.plan, 5)

    job_row = {
        "id":          job_id,
        "tenant_id":   tenant.tenant_id,
        "api_key_id":  tenant.api_key_id,
        "url":         body.url,
        "quality":     body.quality,
        "format":      body.format,
        "options":     body.options,
        "priority":    priority,
        "status":      "queued",
    }

    try:
        ins = supabase.table("partner_jobs").insert(job_row).execute()
        saved = ins.data[0] if ins.data else job_row
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "db_error", "message": str(exc)},
        ) from exc

    # Dispatch to Celery
    try:
        from app.tasks.video_tasks import process_video_task  # lazy import
        process_video_task.apply_async(
            args=[job_id, body.url],
            kwargs={"quality": body.quality},
            priority=priority,
        )
    except Exception:
        # Celery unavailable — job stays queued and will be picked up on retry
        pass

    _increment_usage(tenant.tenant_id)

    return JobResponse(
        id=saved.get("id", job_id),
        status=saved.get("status", "queued"),
        url=body.url,
        quality=body.quality,
        format=body.format,
        options=body.options,
        priority=priority,
        tenant_id=tenant.tenant_id,
        api_key_id=tenant.api_key_id,
        created_at=saved.get("created_at", ""),
    )


@router.get("/api/v1/partner/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """Poll status of a specific job (tenant-isolated)."""
    supabase = get_service_client()
    try:
        res = (
            supabase.table("partner_jobs")
            .select("*")
            .eq("id", job_id)
            .eq("tenant_id", tenant.tenant_id)   # tenant isolation
            .single()
            .execute()
        )
        row = res.data
    except Exception:
        row = None

    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": f"Job {job_id} not found"},
        )

    return JobResponse(
        id=row["id"],
        status=row.get("status", "unknown"),
        url=row.get("url", ""),
        quality=row.get("quality", "best"),
        format=row.get("format"),
        options=row.get("options") or {},
        priority=row.get("priority", 5),
        tenant_id=row.get("tenant_id", tenant.tenant_id),
        api_key_id=row.get("api_key_id"),
        created_at=row.get("created_at", ""),
        result=row.get("result"),
        error=row.get("error"),
    )


@router.get("/api/v1/partner/jobs", response_model=List[JobResponse])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit:  int           = Query(50, ge=1, le=100),
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """List recent jobs for the tenant (newest first, max 100)."""
    supabase = get_service_client()
    try:
        q = (
            supabase.table("partner_jobs")
            .select("*")
            .eq("tenant_id", tenant.tenant_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if status:
            q = q.eq("status", status)

        res  = q.execute()
        rows = res.data or []
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "db_error", "message": str(exc)},
        ) from exc

    return [
        JobResponse(
            id=r["id"],
            status=r.get("status", "unknown"),
            url=r.get("url", ""),
            quality=r.get("quality", "best"),
            format=r.get("format"),
            options=r.get("options") or {},
            priority=r.get("priority", 5),
            tenant_id=r.get("tenant_id", tenant.tenant_id),
            api_key_id=r.get("api_key_id"),
            created_at=r.get("created_at", ""),
            result=r.get("result"),
            error=r.get("error"),
        )
        for r in rows
    ]


@router.delete("/api/v1/partner/jobs/{job_id}", status_code=200)
async def cancel_job(
    job_id: str,
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """Cancel a queued job (tenant-isolated). No-op if already running/done."""
    _require_scope(tenant, "write")

    supabase = get_service_client()
    try:
        res = (
            supabase.table("partner_jobs")
            .select("id, status, celery_task_id")
            .eq("id", job_id)
            .eq("tenant_id", tenant.tenant_id)
            .single()
            .execute()
        )
        row = res.data
    except Exception:
        row = None

    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "job_not_found", "message": f"Job {job_id} not found"},
        )

    if row.get("status") != "queued":
        return {
            "id":      job_id,
            "status":  row.get("status"),
            "message": "Job is not in queued state; cancellation skipped",
        }

    # Revoke Celery task if we have its ID
    celery_task_id = row.get("celery_task_id")
    if celery_task_id:
        try:
            from app.core.celery_app import celery_app
            celery_app.control.revoke(celery_task_id, terminate=False)
        except Exception:
            pass

    try:
        supabase.table("partner_jobs").update({"status": "expired"}).eq(
            "id", job_id
        ).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "db_error", "message": str(exc)},
        ) from exc

    return {"id": job_id, "status": "expired", "message": "Job cancelled"}


# ── Usage ─────────────────────────────────────────────────────────────────────

@router.get("/api/v1/partner/usage")
async def get_usage(
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """Return plan limits, last-30-day usage breakdown, and remaining calls."""
    supabase    = get_service_client()
    today       = date.today()
    cutoff      = (today - timedelta(days=29)).isoformat()
    month_start = today.replace(day=1).isoformat()

    # Last 30 days daily breakdown
    try:
        daily_res = (
            supabase.table("tenant_usage_daily")
            .select("date, api_calls")
            .eq("tenant_id", tenant.tenant_id)
            .gte("date", cutoff)
            .order("date", desc=True)
            .execute()
        )
        daily = daily_res.data or []
    except Exception:
        daily = []

    # Current month total
    try:
        month_res = (
            supabase.table("tenant_usage_daily")
            .select("api_calls")
            .eq("tenant_id", tenant.tenant_id)
            .gte("date", month_start)
            .execute()
        )
        month_calls = sum(r.get("api_calls") or 0 for r in (month_res.data or []))
    except Exception:
        month_calls = 0

    limits    = tenant.plan_limits()
    monthly   = limits["api_calls_per_month"]
    remaining = (monthly - month_calls) if monthly != -1 else -1

    return {
        "plan":           tenant.plan,
        "limits":         limits,
        "this_month":     {"api_calls": month_calls},
        "remaining":      remaining,
        "daily_30d":      daily,
        "rate_limit": {
            "per_minute": tenant.rate_limit_per_min,
            "per_day":    tenant.rate_limit_per_day,
        },
    }


# ── Webhooks ──────────────────────────────────────────────────────────────────

@router.post("/api/v1/partner/webhooks", status_code=201)
async def register_webhook(
    body:   WebhookRegisterRequest,
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """
    Register a webhook endpoint for the tenant.
    The HMAC secret is returned ONLY once at creation.
    """
    _require_scope(tenant, "webhook")

    if not tenant.has_feature("webhooks"):
        raise HTTPException(
            status_code=402,
            detail={
                "code":    "feature_not_available",
                "message": "Webhooks require a plan upgrade",
            },
        )

    _validate_http_url(body.url, require_https=True)

    secret = secrets.token_hex(32)  # 64-char hex string

    supabase     = get_service_client()
    endpoint_id  = str(uuid.uuid4())

    endpoint_row = {
        "id":        endpoint_id,
        "tenant_id": tenant.tenant_id,
        "url":       body.url,
        "events":    body.events,
        "label":     body.label,
        "secret":    secret,    # MVP: stored AS-IS; use vault in prod
        "is_active": True,
    }

    try:
        ins   = supabase.table("webhook_endpoints").insert(endpoint_row).execute()
        saved = ins.data[0] if ins.data else endpoint_row
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "db_error", "message": str(exc)},
        ) from exc

    return {
        "id":         saved.get("id", endpoint_id),
        "url":        body.url,
        "events":     body.events,
        "label":      body.label,
        "is_active":  True,
        "created_at": saved.get("created_at", ""),
        # Secret shown ONLY once
        "secret":     secret,
        "note":       "Store this secret securely — it will not be shown again.",
    }


@router.get("/api/v1/partner/webhooks")
async def list_webhooks(
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """List webhook endpoints for the tenant (secret field hidden)."""
    supabase = get_service_client()
    try:
        res = (
            supabase.table("webhook_endpoints")
            .select("id, url, events, label, is_active, created_at")
            .eq("tenant_id", tenant.tenant_id)
            .order("created_at", desc=True)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "db_error", "message": str(exc)},
        ) from exc

    return {"webhooks": rows}


@router.delete("/api/v1/partner/webhooks/{webhook_id}", status_code=200)
async def remove_webhook(
    webhook_id: str,
    tenant:     TenantContext = Depends(get_partner_tenant),
):
    """Deactivate a webhook endpoint (soft delete)."""
    supabase = get_service_client()

    # Verify tenant ownership
    try:
        res = (
            supabase.table("webhook_endpoints")
            .select("id, tenant_id, is_active")
            .eq("id", webhook_id)
            .single()
            .execute()
        )
        row = res.data
    except Exception:
        row = None

    if not row or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(
            status_code=404,
            detail={
                "code":    "webhook_not_found",
                "message": f"Webhook {webhook_id} not found",
            },
        )

    try:
        supabase.table("webhook_endpoints").update({"is_active": False}).eq(
            "id", webhook_id
        ).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "db_error", "message": str(exc)},
        ) from exc

    return {"id": webhook_id, "is_active": False, "message": "Webhook deactivated"}


# ── URL validation ────────────────────────────────────────────────────────────

@router.post("/api/v1/partner/validate-urls")
async def validate_urls(
    body:   ValidateUrlsRequest,
    tenant: TenantContext = Depends(get_partner_tenant),
):
    """
    Batch URL validation against the extractor registry.
    Accepts up to 100 URLs; returns per-URL support and platform info.
    """
    _require_scope(tenant, "read")

    urls = body.urls[:100]  # hard cap even if Pydantic max_items is set

    try:
        from app.services.extractor_registry import REGISTRY
        results = REGISTRY.validate_batch(urls)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "validation_error", "message": str(exc)},
        ) from exc

    return {
        "count":   len(urls),
        "results": results,
    }
