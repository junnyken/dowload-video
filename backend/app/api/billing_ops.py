"""
Admin Billing Ops Dashboard — VidGrab Phase 20
===============================================
Endpoints for internal admin visibility into billing, revenue, and usage.
All routes require admin role (RBAC check via rbac.require_admin).

Routes:
  GET  /admin/billing/overview          — MRR, active subs, tier distribution
  GET  /admin/billing/users             — paginated user list with billing info
  GET  /admin/billing/usage-trends      — daily download/event trends (last 30d)
  GET  /admin/billing/payment-events    — recent Stripe webhook events
  POST /admin/billing/credits/grant     — grant promotional credits to a user
  GET  /admin/billing/plan-summary      — downloads/events per plan (today + MTD)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/billing", tags=["Billing Ops"])

_PLAN_MONTHLY_CENTS = {
    "free": 0,
    "pro": 999,
    "team": 2999,
    "api": 1999,
    "enterprise": 0,  # custom — not counted in automated MRR
}


# ── Auth guard ────────────────────────────────────────────────────────────────

async def _require_admin(request: Request) -> Dict[str, Any]:
    user = await get_required_user(request)
    user_id = user.get("id") or user.get("sub", "")
    db = get_service_client()
    try:
        res = db.table("profiles").select("is_admin,role").eq("id", user_id).maybe_single().execute()
        profile = res.data or {}
        is_admin = profile.get("is_admin") or profile.get("role") in ("admin", "superadmin")
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=403, detail="Admin check failed")
    return user


# ── Models ────────────────────────────────────────────────────────────────────

class CreditGrantIn(BaseModel):
    user_id: str
    amount: int
    reason: str
    expires_days: Optional[int] = None  # None = never expires


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/overview")
async def billing_overview(request: Request):
    """
    Key billing metrics snapshot:
    - Total users by tier
    - Estimated MRR (monthly recurring revenue)
    - Active Pro/Team subs
    - Payment failures in last 7 days
    """
    await _require_admin(request)
    db = get_service_client()

    today = date.today()
    seven_days_ago = (today - timedelta(days=7)).isoformat()

    try:
        # Tier distribution
        tiers_res = db.table("profiles").select("tier").execute()
        tier_counts: Dict[str, int] = {}
        for row in (tiers_res.data or []):
            t = (row.get("tier") or "free").lower()
            tier_counts[t] = tier_counts.get(t, 0) + 1

        total_users = sum(tier_counts.values())

        # Estimated MRR
        mrr_cents = sum(
            tier_counts.get(tier, 0) * price
            for tier, price in _PLAN_MONTHLY_CENTS.items()
        )

        # Payment failures (past_due) in last 7d
        past_due_res = db.table("payment_events").select("id").eq("event_type", "invoice.payment_failed").gte("created_at", seven_days_ago).execute()
        payment_failures_7d = len(past_due_res.data or [])

        # Active paid subscriptions
        active_paid = (
            tier_counts.get("pro", 0)
            + tier_counts.get("team", 0)
            + tier_counts.get("api", 0)
            + tier_counts.get("enterprise", 0)
        )

        return {
            "total_users": total_users,
            "tier_distribution": tier_counts,
            "active_paid_subscriptions": active_paid,
            "estimated_mrr_cents": mrr_cents,
            "estimated_mrr_usd": round(mrr_cents / 100, 2),
            "payment_failures_last_7d": payment_failures_7d,
            "snapshot_date": today.isoformat(),
        }

    except Exception as exc:
        logger.error("billing_overview error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load billing overview")


@router.get("/users")
async def billing_users(
    request: Request,
    page: int = 1,
    per_page: int = 50,
    tier: Optional[str] = None,
    billing_status: Optional[str] = None,
):
    """
    Paginated list of users with billing info.
    Filter by tier (free/pro/team/enterprise) or billing_status.
    """
    await _require_admin(request)
    db = get_service_client()

    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    try:
        q = db.table("profiles").select(
            "id,email,tier,billing_status,subscription_expiry,grace_period_ends_at,"
            "stripe_customer_id,stripe_subscription_id,created_at,updated_at"
        )
        if tier:
            q = q.eq("tier", tier.lower())
        if billing_status:
            q = q.eq("billing_status", billing_status.lower())
        q = q.order("created_at", desc=True).range(offset, offset + per_page - 1)
        res = q.execute()
        rows = res.data or []

        # Attach today's download count per user from user_usage
        user_ids = [r["id"] for r in rows]
        usage_map: Dict[str, int] = {}
        if user_ids:
            try:
                usage_res = db.table("user_usage").select("user_id,downloads_today").in_("user_id", user_ids).execute()
                for u in (usage_res.data or []):
                    usage_map[u["user_id"]] = u.get("downloads_today", 0)
            except Exception:
                pass

        for row in rows:
            row["downloads_today"] = usage_map.get(row["id"], 0)

        return {"users": rows, "page": page, "per_page": per_page, "count": len(rows)}

    except Exception as exc:
        logger.error("billing_users error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load billing users")


@router.get("/usage-trends")
async def usage_trends(request: Request, days: int = 30):
    """
    Daily download and event counts for the last N days.
    Data from usage_events table aggregated by day + event_type.
    """
    await _require_admin(request)
    db = get_service_client()

    days = min(days, 90)
    since = (date.today() - timedelta(days=days)).isoformat()

    try:
        # Aggregate usage_events by day and metric
        res = (
            db.table("usage_events")
            .select("metric,created_at,quantity")
            .gte("created_at", since)
            .execute()
        )
        rows = res.data or []

        # Group by day + metric
        trend: Dict[str, Dict[str, int]] = {}
        for row in rows:
            day = (row.get("created_at") or "")[:10]
            metric = row.get("metric", "unknown")
            qty = row.get("quantity", 0)
            if day not in trend:
                trend[day] = {}
            trend[day][metric] = trend[day].get(metric, 0) + qty

        # Sort chronologically
        sorted_days = sorted(trend.keys())
        return {
            "days": days,
            "since": since,
            "trends": [{"date": d, **trend[d]} for d in sorted_days],
        }

    except Exception as exc:
        logger.error("usage_trends error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load usage trends")


@router.get("/payment-events")
async def recent_payment_events(request: Request, limit: int = 50):
    """Recent Stripe webhook events from payment_events table."""
    await _require_admin(request)
    db = get_service_client()

    limit = min(limit, 200)

    try:
        res = (
            db.table("payment_events")
            .select("id,provider,event_type,processed,processed_at,error,created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"events": res.data or [], "count": len(res.data or [])}

    except Exception as exc:
        logger.error("payment_events error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load payment events")


@router.post("/credits/grant", status_code=201)
async def grant_credits(request: Request, body: CreditGrantIn):
    """
    Grant promotional credits to a user.
    Creates a credit_grants row and increments user_credits.balance.
    """
    admin = await _require_admin(request)
    granted_by = admin.get("id") or admin.get("sub", "system")
    db = get_service_client()

    expires_at = None
    if body.expires_days:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=body.expires_days)
        ).isoformat()

    try:
        # Insert credit grant
        db.table("credit_grants").insert({
            "user_id": body.user_id,
            "granted_by": granted_by,
            "amount": body.amount,
            "reason": body.reason,
            "expires_at": expires_at,
            "is_active": True,
            "used_amount": 0,
        }).execute()

        # Increment user_credits balance
        db.table("user_credits").upsert({
            "user_id": body.user_id,
            "balance": body.amount,
            "total_earned": body.amount,
        }, on_conflict="user_id").execute()

        return {
            "success": True,
            "user_id": body.user_id,
            "amount": body.amount,
            "reason": body.reason,
            "expires_at": expires_at,
        }

    except Exception as exc:
        logger.error("grant_credits error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to grant credits: {exc}")


@router.get("/plan-summary")
async def plan_usage_summary(request: Request):
    """
    Today's usage broken down by plan tier.
    Shows how many downloads each tier consumed today.
    """
    await _require_admin(request)
    db = get_service_client()

    today = date.today().isoformat()
    today_start = f"{today}T00:00:00+00:00"

    try:
        # Join usage_events with profiles to get tier
        res = (
            db.table("usage_events")
            .select("metric,quantity,plan")
            .gte("created_at", today_start)
            .execute()
        )
        rows = res.data or []

        summary: Dict[str, Dict[str, int]] = {}
        for row in rows:
            plan = row.get("plan") or "unknown"
            metric = row.get("metric", "unknown")
            qty = row.get("quantity", 0)
            if plan not in summary:
                summary[plan] = {}
            summary[plan][metric] = summary[plan].get(metric, 0) + qty

        return {"date": today, "by_plan": summary}

    except Exception as exc:
        logger.error("plan_usage_summary error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load plan summary")
