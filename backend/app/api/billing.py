"""
Billing & Usage API
===================
Endpoints for workspace billing, quota tracking, and usage analytics.

Routes:
  GET  /api/v1/billing/summary   — current plan, usage, quota remaining
  GET  /api/v1/billing/usage     — detailed usage (daily breakdown, last 30 days)
  GET  /api/v1/billing/seats     — team seats usage
  GET  /api/v1/billing/history   — usage history (monthly, last 12 months)
  POST /api/v1/billing/license   — activate enterprise license key (self-hosted)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Billing"])


# =============================================================================
# Response / Request models
# =============================================================================


class PlanLimits(BaseModel):
    api_calls_per_month: int
    seats: int
    batch_size_max: int


class UsageSnapshot(BaseModel):
    api_calls_today: int = 0
    api_calls_this_month: int = 0
    downloads_today: int = 0
    downloads_this_month: int = 0
    storage_bytes: int = 0


class RemainingQuota(BaseModel):
    api_calls: int


class BillingSummaryResponse(BaseModel):
    plan: str
    plan_display_name: str
    status: str
    trial_ends_at: Optional[datetime]
    limits: PlanLimits
    usage: UsageSnapshot
    remaining: RemainingQuota
    upgrade_url: str
    features: Dict[str, Any]


class DailyUsageRow(BaseModel):
    date: date
    api_calls: int = 0
    downloads: int = 0
    batch_jobs: int = 0
    webhook_deliveries: int = 0


class UsageDetailResponse(BaseModel):
    days: List[DailyUsageRow]


class SeatMember(BaseModel):
    user_id: str
    role: str
    joined_at: Optional[datetime]


class SeatsResponse(BaseModel):
    total_seats: int
    used_seats: int
    max_seats: int
    members: List[SeatMember]


class MonthlyUsageRow(BaseModel):
    month: str  # "YYYY-MM"
    api_calls: int = 0
    downloads: int = 0
    batch_jobs: int = 0
    webhook_deliveries: int = 0


class UsageHistoryResponse(BaseModel):
    months: List[MonthlyUsageRow]


class LicenseActivateRequest(BaseModel):
    license_key: str = Field(..., min_length=10)


class LicenseActivateResponse(BaseModel):
    success: bool
    plan: str
    max_seats: int
    expires_at: Optional[datetime]
    message: str


# =============================================================================
# Plan metadata
# =============================================================================

_PLAN_META: Dict[str, Dict[str, Any]] = {
    "starter": {
        "display_name": "Starter",
        "api_calls_per_month": 1_000,
        "seats": 1,
        "batch_size_max": 10,
    },
    "growth": {
        "display_name": "Growth",
        "api_calls_per_month": 10_000,
        "seats": 5,
        "batch_size_max": 50,
    },
    "enterprise": {
        "display_name": "Enterprise",
        "api_calls_per_month": 100_000,
        "seats": 100,
        "batch_size_max": 500,
    },
}

_UPGRADE_URL = "https://vidgrab.dev/pricing"


# =============================================================================
# Internal helpers
# =============================================================================


async def _get_tenant_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Locate the primary tenant for *user_id*.

    Resolution order:
    1. Find all *active* workspace memberships for the user, ranked so that
       owner-level memberships come first.
    2. Look up the tenant whose workspace_id is in that set.
    3. Return the first (highest-priority) tenant row.

    Returns None if the user has no active workspace membership or no tenant.
    """
    supabase = get_service_client()

    # Step 1 — workspace memberships
    memberships_resp = (
        supabase.table("workspace_memberships")
        .select("workspace_id, role")
        .eq("user_id", user_id)
        .eq("status", "active")
        .order("role", desc=True)  # owner > admin > member alphabetically desc
        .execute()
    )
    memberships = memberships_resp.data or []
    if not memberships:
        return None

    workspace_ids = [m["workspace_id"] for m in memberships]

    # Step 2 — find tenant(s) for those workspaces
    tenants_resp = (
        supabase.table("tenants")
        .select("*")
        .in_("workspace_id", workspace_ids)
        .execute()
    )
    tenants = tenants_resp.data or []
    if not tenants:
        return None

    # Step 3 — return the tenant whose workspace ranked first in memberships
    workspace_rank = {ws_id: idx for idx, ws_id in enumerate(workspace_ids)}
    tenants_sorted = sorted(
        tenants, key=lambda t: workspace_rank.get(t.get("workspace_id"), 999)
    )
    return tenants_sorted[0]


def _validate_license(
    license_key: str, tenant_id: str
) -> Dict[str, Any]:
    """
    Validate a VidGrab Enterprise license key.

    License format (self-hosted):
        base64url( JSON(payload) + "." + hex(HMAC-SHA256(JSON(payload), secret)) )

    Payload fields:
        tenant_id   — must match the requesting tenant
        plan        — "enterprise" (or future tier names)
        max_seats   — integer
        expires_at  — ISO-8601 datetime string (UTC); null = perpetual
        issued_at   — ISO-8601 datetime string (UTC)
        sig         — hex HMAC included inside payload for double-verification
    """
    secret = os.environ.get("ENTERPRISE_LICENSE_SECRET", "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="License validation unavailable: ENTERPRISE_LICENSE_SECRET not configured.",
        )

    # Decode outer base64
    try:
        raw = base64.urlsafe_b64decode(license_key + "==")
        raw_str = raw.decode("utf-8")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid license key format (base64 decode failed).",
        )

    # Split payload and signature: "<json>.<hex_sig>"
    if "." not in raw_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid license key format (missing signature delimiter).",
        )

    last_dot = raw_str.rfind(".")
    json_body = raw_str[:last_dot]
    provided_sig = raw_str[last_dot + 1 :]

    # Verify HMAC-SHA256
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        json_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, provided_sig):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="License key signature verification failed.",
        )

    # Parse payload
    try:
        payload: Dict[str, Any] = json.loads(json_body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid license key payload (JSON parse error).",
        )

    # Validate tenant binding
    if payload.get("tenant_id") != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="License key is not issued for this tenant.",
        )

    # Validate expiry
    expires_at_str = payload.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid expires_at in license payload.",
            )
        if expires_at < datetime.now(tz=timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="License key has expired.",
            )

    return payload


# =============================================================================
# GET /billing/summary
# =============================================================================


@router.get(
    "/billing/summary",
    response_model=BillingSummaryResponse,
    summary="Current plan, quota, and usage snapshot",
)
async def billing_summary(user: dict = Depends(get_required_user)):
    """
    Returns the tenant's active plan, feature flags, and a combined
    today/month usage snapshot with remaining quota.
    """
    user_id: str = user["id"]
    tenant = await _get_tenant_for_user(user_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active workspace or tenant found for this user.",
        )

    tenant_id: str = tenant["id"]
    plan: str = tenant.get("plan", "starter")
    plan_info = _PLAN_META.get(plan, _PLAN_META["starter"])

    supabase = get_service_client()

    # Fetch tenant_settings for features + branding
    settings_resp = (
        supabase.table("tenant_settings")
        .select("features, branding")
        .eq("tenant_id", tenant_id)
        .maybe_single()
        .execute()
    )
    tenant_settings = settings_resp.data or {}
    features: Dict[str, Any] = tenant_settings.get("features") or {}

    # Today's usage row
    today_str = date.today().isoformat()
    today_resp = (
        supabase.table("tenant_usage_daily")
        .select("api_calls, downloads, batch_jobs, webhook_deliveries")
        .eq("tenant_id", tenant_id)
        .eq("date", today_str)
        .maybe_single()
        .execute()
    )
    today_row = today_resp.data or {}

    # Month-to-date aggregates (SUM via PostgREST)
    month_start = date.today().replace(day=1).isoformat()
    month_resp = (
        supabase.table("tenant_usage_daily")
        .select("api_calls.sum(), downloads.sum()")
        .eq("tenant_id", tenant_id)
        .gte("date", month_start)
        .execute()
    )
    month_agg = (month_resp.data or [{}])[0]

    api_calls_today: int = today_row.get("api_calls", 0) or 0
    downloads_today: int = today_row.get("downloads", 0) or 0
    api_calls_month: int = int(month_agg.get("sum", 0) or 0)
    downloads_month: int = int(month_agg.get("sum", 0) or 0)

    # More precise: run two separate sums when PostgREST aliasing is unavailable
    if month_agg.get("api_calls"):
        api_calls_month = int(month_agg["api_calls"] or 0)
    if month_agg.get("downloads"):
        downloads_month = int(month_agg["downloads"] or 0)

    monthly_limit: int = plan_info["api_calls_per_month"]
    remaining_calls = max(0, monthly_limit - api_calls_month)

    return BillingSummaryResponse(
        plan=plan,
        plan_display_name=plan_info["display_name"],
        status=tenant.get("status", "active"),
        trial_ends_at=tenant.get("trial_ends_at"),
        limits=PlanLimits(
            api_calls_per_month=monthly_limit,
            seats=plan_info["seats"],
            batch_size_max=plan_info["batch_size_max"],
        ),
        usage=UsageSnapshot(
            api_calls_today=api_calls_today,
            api_calls_this_month=api_calls_month,
            downloads_today=downloads_today,
            downloads_this_month=downloads_month,
            storage_bytes=tenant.get("storage_bytes_used", 0) or 0,
        ),
        remaining=RemainingQuota(api_calls=remaining_calls),
        upgrade_url=_UPGRADE_URL,
        features=features,
    )


# =============================================================================
# GET /billing/usage
# =============================================================================


@router.get(
    "/billing/usage",
    response_model=UsageDetailResponse,
    summary="Daily usage breakdown for the last 30 days",
)
async def billing_usage(user: dict = Depends(get_required_user)):
    """
    Returns one row per day for the last 30 calendar days, showing
    api_calls, downloads, batch_jobs, and webhook_deliveries.
    """
    user_id: str = user["id"]
    tenant = await _get_tenant_for_user(user_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active workspace or tenant found for this user.",
        )

    supabase = get_service_client()
    cutoff = (date.today() - timedelta(days=29)).isoformat()

    resp = (
        supabase.table("tenant_usage_daily")
        .select("date, api_calls, downloads, batch_jobs, webhook_deliveries")
        .eq("tenant_id", tenant["id"])
        .gte("date", cutoff)
        .order("date", desc=False)
        .execute()
    )
    rows = resp.data or []

    return UsageDetailResponse(
        days=[
            DailyUsageRow(
                date=r["date"],
                api_calls=r.get("api_calls", 0) or 0,
                downloads=r.get("downloads", 0) or 0,
                batch_jobs=r.get("batch_jobs", 0) or 0,
                webhook_deliveries=r.get("webhook_deliveries", 0) or 0,
            )
            for r in rows
        ]
    )


# =============================================================================
# GET /billing/seats
# =============================================================================


@router.get(
    "/billing/seats",
    response_model=SeatsResponse,
    summary="Team seat usage and member roster",
)
async def billing_seats(user: dict = Depends(get_required_user)):
    """
    Returns seat utilisation for the tenant's primary workspace:
    total slots in plan vs. active members, with a per-member breakdown.
    """
    user_id: str = user["id"]
    tenant = await _get_tenant_for_user(user_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active workspace or tenant found for this user.",
        )

    plan: str = tenant.get("plan", "starter")
    max_seats: int = _PLAN_META.get(plan, _PLAN_META["starter"])["seats"]

    supabase = get_service_client()
    members_resp = (
        supabase.table("workspace_memberships")
        .select("user_id, role, created_at")
        .eq("workspace_id", tenant["workspace_id"])
        .eq("status", "active")
        .order("role", desc=True)
        .execute()
    )
    members = members_resp.data or []
    used_seats = len(members)

    return SeatsResponse(
        total_seats=max_seats,
        used_seats=used_seats,
        max_seats=max_seats,
        members=[
            SeatMember(
                user_id=m["user_id"],
                role=m.get("role", "member"),
                joined_at=m.get("created_at"),
            )
            for m in members
        ],
    )


# =============================================================================
# GET /billing/history
# =============================================================================


@router.get(
    "/billing/history",
    response_model=UsageHistoryResponse,
    summary="Monthly usage aggregates for the last 12 months",
)
async def billing_history(user: dict = Depends(get_required_user)):
    """
    Returns one row per calendar month for the last 12 months, aggregating
    api_calls, downloads, batch_jobs, and webhook_deliveries.

    Aggregation is done client-side (Python) to stay compatible with
    PostgREST without requiring a database view or RPC function.
    """
    user_id: str = user["id"]
    tenant = await _get_tenant_for_user(user_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active workspace or tenant found for this user.",
        )

    supabase = get_service_client()
    cutoff = (date.today().replace(day=1) - timedelta(days=365)).isoformat()

    resp = (
        supabase.table("tenant_usage_daily")
        .select("date, api_calls, downloads, batch_jobs, webhook_deliveries")
        .eq("tenant_id", tenant["id"])
        .gte("date", cutoff)
        .order("date", desc=False)
        .execute()
    )
    rows = resp.data or []

    # Aggregate by YYYY-MM
    monthly: Dict[str, Dict[str, int]] = {}
    for r in rows:
        month_key = r["date"][:7]  # "YYYY-MM"
        if month_key not in monthly:
            monthly[month_key] = {
                "api_calls": 0,
                "downloads": 0,
                "batch_jobs": 0,
                "webhook_deliveries": 0,
            }
        monthly[month_key]["api_calls"] += r.get("api_calls", 0) or 0
        monthly[month_key]["downloads"] += r.get("downloads", 0) or 0
        monthly[month_key]["batch_jobs"] += r.get("batch_jobs", 0) or 0
        monthly[month_key]["webhook_deliveries"] += r.get("webhook_deliveries", 0) or 0

    return UsageHistoryResponse(
        months=[
            MonthlyUsageRow(
                month=mk,
                api_calls=mv["api_calls"],
                downloads=mv["downloads"],
                batch_jobs=mv["batch_jobs"],
                webhook_deliveries=mv["webhook_deliveries"],
            )
            for mk, mv in sorted(monthly.items())
        ]
    )


# =============================================================================
# POST /billing/license
# =============================================================================


@router.post(
    "/billing/license",
    response_model=LicenseActivateResponse,
    summary="Activate an enterprise license key (self-hosted)",
)
async def billing_license(
    body: LicenseActivateRequest,
    user: dict = Depends(get_required_user),
):
    """
    Validate and apply a VidGrab Enterprise license key to the requesting
    user's tenant.

    On success:
    - Updates ``tenants.plan`` → ``"enterprise"``
    - Persists the raw ``license_key`` and ``license_expires_at``
    - Returns the new plan details

    License key format (issued by VidGrab team):
        base64url( <json_payload> + "." + HMAC-SHA256(<json_payload>, ENTERPRISE_LICENSE_SECRET) )

    JSON payload fields:
        {
          "tenant_id":  "<uuid>",
          "plan":       "enterprise",
          "max_seats":  100,
          "expires_at": "2027-01-01T00:00:00Z",  // null = perpetual
          "issued_at":  "2026-01-01T00:00:00Z"
        }
    """
    user_id: str = user["id"]
    tenant = await _get_tenant_for_user(user_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active workspace or tenant found for this user.",
        )

    tenant_id: str = tenant["id"]

    # Validate: raises HTTPException on any failure
    payload = _validate_license(body.license_key, tenant_id)

    new_plan: str = payload.get("plan", "enterprise")
    max_seats: int = int(payload.get("max_seats", 100))
    expires_at_str: Optional[str] = payload.get("expires_at")
    expires_at: Optional[datetime] = None
    if expires_at_str:
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))

    # Apply to tenant record
    supabase = get_service_client()
    update_data: Dict[str, Any] = {
        "plan": new_plan,
        "license_key": body.license_key,
        "status": "active",
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    if expires_at:
        update_data["license_expires_at"] = expires_at.isoformat()

    supabase.table("tenants").update(update_data).eq("id", tenant_id).execute()

    logger.info(
        "Enterprise license activated for tenant=%s plan=%s seats=%d expires=%s",
        tenant_id,
        new_plan,
        max_seats,
        expires_at_str or "never",
    )

    return LicenseActivateResponse(
        success=True,
        plan=new_plan,
        max_seats=max_seats,
        expires_at=expires_at,
        message=(
            f"License activated successfully. Plan upgraded to {new_plan.capitalize()} "
            f"with {max_seats} seats."
            + (f" Expires {expires_at_str}." if expires_at_str else " No expiry date.")
        ),
    )
