"""
Entitlement Resolver — VidGrab Phase 20
========================================
Single source of truth for "what can user X do right now?"

Bridges two existing plan systems:
  - profiles.tier ('free' | 'pro' | 'team' | 'enterprise')   → web users
  - tenants.plan  ('starter' | 'growth' | 'enterprise')       → partner/API users

Usage:
    from app.core.entitlements import get_entitlement, check_feature, PLAN_DEFS

    ent = await get_entitlement(user_id, supabase_client)
    if not ent["features"]["bulk_zip"]:
        raise HTTPException(402, detail={"error_code": "tier_required_feature", "feature": "bulk_zip"})

    if ent["limits"]["downloads_per_day"] != -1 and daily_count >= ent["limits"]["downloads_per_day"]:
        raise HTTPException(402, detail={"error_code": "quota_exceeded_daily"})
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Plan definitions (authoritative, kept in sync with migration 015 seeds) ─

PLAN_DEFS: Dict[str, Dict[str, Any]] = {
    "free": {
        "name": "Free",
        "limits": {
            "downloads_per_day":     int(os.getenv("FREE_DAILY_LIMIT", "10")),
            "batch_size_max":        int(os.getenv("FREE_BATCH_LIMIT", "5")),
            "max_height":            1080,
            "history_days":          7,
            "history_limit":         20,
            "ai_analyses_per_day":   0,
            "concurrent_jobs":       2,
            "api_calls_per_month":   0,
            "seats_max":             1,
        },
        "features": {
            "bulk_zip":              False,
            "youtube_video":         False,
            "spotify_full":          False,
            "cloud_save":            False,
            "chapters":              False,
            "logo_inpaint":          False,
            "api_key":               False,
            "webhook":               False,
            "ai_tools":              False,
            "priority_queue":        False,
            "team_workspace":        False,
            "partner_api":           False,
            "sso":                   False,
            "white_label":           False,
        },
        "queue_priority": 10,  # higher = lower priority
    },
    "pro": {
        "name": "Pro",
        "limits": {
            "downloads_per_day":     int(os.getenv("PRO_DAILY_LIMIT", "100")),
            "batch_size_max":        100,
            "max_height":            2160,
            "history_days":          90,
            "history_limit":         None,
            "ai_analyses_per_day":   50,
            "concurrent_jobs":       10,
            "api_calls_per_month":   3000,
            "seats_max":             1,
        },
        "features": {
            "bulk_zip":              True,
            "youtube_video":         True,
            "spotify_full":          True,
            "cloud_save":            True,
            "chapters":              True,
            "logo_inpaint":          True,
            "api_key":               True,
            "webhook":               True,
            "ai_tools":              True,
            "priority_queue":        True,
            "team_workspace":        False,
            "partner_api":           False,
            "sso":                   False,
            "white_label":           False,
        },
        "queue_priority": 3,
    },
    "team": {
        "name": "Team",
        "limits": {
            "downloads_per_day":     500,
            "batch_size_max":        200,
            "max_height":            2160,
            "history_days":          90,
            "history_limit":         None,
            "ai_analyses_per_day":   200,
            "concurrent_jobs":       30,
            "api_calls_per_month":   10000,
            "seats_max":             5,  # default; overridden by profiles.seats_max
        },
        "features": {
            "bulk_zip":              True,
            "youtube_video":         True,
            "spotify_full":          True,
            "cloud_save":            True,
            "chapters":              True,
            "logo_inpaint":          True,
            "api_key":               True,
            "webhook":               True,
            "ai_tools":              True,
            "priority_queue":        True,
            "team_workspace":        True,
            "partner_api":           False,
            "sso":                   False,
            "white_label":           False,
        },
        "queue_priority": 3,
    },
    "api": {
        "name": "API Partner",
        "limits": {
            "downloads_per_day":     1000,
            "batch_size_max":        500,
            "max_height":            2160,
            "history_days":          90,
            "history_limit":         None,
            "ai_analyses_per_day":   100,
            "concurrent_jobs":       50,
            "api_calls_per_month":   1000,
            "seats_max":             1,
        },
        "features": {
            "bulk_zip":              True,
            "youtube_video":         True,
            "spotify_full":          True,
            "cloud_save":            True,
            "chapters":              True,
            "logo_inpaint":          True,
            "api_key":               True,
            "webhook":               True,
            "ai_tools":              True,
            "priority_queue":        True,
            "team_workspace":        False,
            "partner_api":           True,
            "sso":                   False,
            "white_label":           False,
        },
        "queue_priority": 4,
    },
    "enterprise": {
        "name": "Enterprise",
        "limits": {
            "downloads_per_day":     -1,   # -1 = unlimited
            "batch_size_max":        -1,
            "max_height":            2160,
            "history_days":          -1,
            "history_limit":         None,
            "ai_analyses_per_day":   -1,
            "concurrent_jobs":       -1,
            "api_calls_per_month":   -1,
            "seats_max":             -1,
        },
        "features": {
            "bulk_zip":              True,
            "youtube_video":         True,
            "spotify_full":          True,
            "cloud_save":            True,
            "chapters":              True,
            "logo_inpaint":          True,
            "api_key":               True,
            "webhook":               True,
            "ai_tools":              True,
            "priority_queue":        True,
            "team_workspace":        True,
            "partner_api":           True,
            "sso":                   True,
            "white_label":           True,
        },
        "queue_priority": 1,
    },
    # Tenant-tier aliases → map to closest web-tier equivalent
    "starter": "free",       # tenants.plan 'starter' → free entitlements
    "growth":  "pro",        # tenants.plan 'growth'  → pro entitlements
}


def _resolve_tier(tier: Optional[str]) -> str:
    """Normalize any tier name to a concrete PLAN_DEFS key."""
    if not tier:
        return "free"
    t = tier.lower()
    mapped = PLAN_DEFS.get(t)
    if isinstance(mapped, str):
        return mapped  # alias redirect
    if isinstance(mapped, dict):
        return t
    return "free"  # unknown tier → safe default


def get_plan_def(tier: Optional[str]) -> Dict[str, Any]:
    """Return the plan definition dict for a tier string."""
    return PLAN_DEFS[_resolve_tier(tier)]


def check_feature(tier: Optional[str], feature: str) -> bool:
    """True if the tier has this feature enabled."""
    plan = get_plan_def(tier)
    return plan["features"].get(feature, False)


def get_limit(tier: Optional[str], limit_key: str, default: Any = 0) -> Any:
    """Return a numeric limit for a tier. -1 = unlimited."""
    plan = get_plan_def(tier)
    return plan["limits"].get(limit_key, default)


def is_unlimited(tier: Optional[str], limit_key: str) -> bool:
    return get_limit(tier, limit_key) == -1


def resolve_effective_tier(profile: Dict[str, Any]) -> str:
    """
    The one place that turns a profiles row into the tier the user actually has.

    There used to be three answers to this question and they disagreed. For an
    account with tier='enterprise' and billing_status='none':

        quotas.py               → enterprise  (used the declared tier)
        entitlements.py         → free        (no billing record ⇒ free)
        payments/billing-status → enterprise  (returned profiles.tier raw)

    So the account menu displayed ENTERPRISE while every require_feature gate
    refused, and the download quota let them through unlimited anyway. Whichever
    answer is right, three of them cannot be.

    Rules, in order:
      canceled                          → free
      canceling, period not yet over    → keep the tier until it ends
      past_due, still inside grace      → keep the tier
      past_due, grace expired           → free
      none, on a paid tier              → free (no billing record at all)
      otherwise                         → the tier as declared

    Note the grace cases keep the DECLARED tier. quotas.py returned a hardcoded
    "pro" here, which quietly demoted a Team or Enterprise account to Pro for
    the duration of a payment problem.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    declared = (profile.get("tier") or "free").lower()
    status = (profile.get("billing_status") or "none").lower()
    now = datetime.now(timezone.utc)

    def _dt(value):
        if not value:
            return None
        try:
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except Exception:
            return None

    expiry = _dt(profile.get("subscription_expiry"))
    grace_end = _dt(profile.get("grace_period_ends_at"))

    if status == "canceled":
        return "free"
    if status == "canceling":
        return declared if (expiry and expiry > now) else "free"
    if status == "past_due":
        # Either clock may carry the grace period depending on which code path
        # wrote the row, so honour whichever is set and still in the future.
        if (grace_end and grace_end > now) or (expiry and expiry > now):
            return declared
        return "free"
    if status == "none" and declared != "free":
        # An admin grant sets billing_status='active'; a paid tier with no
        # billing record at all is a leftover, not an entitlement.
        return "free"
    return declared


# ── DB-backed effective tier resolver ────────────────────────────────────────

async def get_entitlement(user_id: str, db) -> Dict[str, Any]:
    """
    Resolve the full entitlement dict for a user.

    Returns:
        {
            "tier": "pro",
            "billing_status": "active",
            "limits": {...},       # from PLAN_DEFS, with seats_max overridden from profile
            "features": {...},     # boolean feature flags
            "queue_priority": 3,
            "is_pro_equivalent": True,
        }

    Effective tier rules (in priority order):
      1. billing_status='canceling' AND subscription_expiry > now  → treat as pro (grace)
      2. billing_status='past_due'  AND grace_period_ends_at > now → treat as pro (grace)
      3. billing_status='canceled' OR subscription_expiry < now    → downgrade to free
      4. Otherwise: use profiles.tier as declared
    """
    try:
        res = (
            db.table("profiles")
            .select(
                "tier,billing_status,subscription_expiry,grace_period_ends_at,seats_max,seats_used"
            )
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        profile = res.data or {}
    except Exception:
        profile = {}

    declared_tier = (profile.get("tier") or "free").lower()
    billing_status = (profile.get("billing_status") or "none").lower()

    # Effective tier resolution — one shared rule, see resolve_effective_tier.
    effective_tier = resolve_effective_tier(profile)

    plan = get_plan_def(effective_tier)
    limits = dict(plan["limits"])

    # Override seats_max from profile if team/enterprise
    if effective_tier in ("team", "enterprise"):
        db_seats_max = profile.get("seats_max")
        if db_seats_max and db_seats_max > 1:
            limits["seats_max"] = db_seats_max

    is_pro_equiv = effective_tier in ("pro", "team", "api", "enterprise")

    return {
        "tier": effective_tier,
        "declared_tier": declared_tier,
        "billing_status": billing_status,
        "limits": limits,
        "features": dict(plan["features"]),
        "queue_priority": plan["queue_priority"],
        "is_pro_equivalent": is_pro_equiv,
        "plan_name": plan["name"],
    }


# ── FastAPI dependency wrappers ───────────────────────────────────────────────

async def _get_current_entitlement(request, user_id: str):
    """Internal helper used by FastAPI dependencies."""
    from app.core.database import get_service_client
    db = get_service_client()
    return await get_entitlement(user_id, db)


def require_feature(feature: str):
    """
    FastAPI dependency factory: raise 402 if user's plan doesn't include feature.

    Usage:
        @router.post("/zip")
        async def create_zip(
            request: Request,
            user = Depends(get_required_user),
            _: None = Depends(require_feature("bulk_zip")),
        ):
    """
    async def _dep(request, user=None):
        from fastapi import HTTPException
        from app.core.auth_middleware import get_required_user
        if user is None:
            user = await get_required_user(request)
        user_id = user.get("id") or user.get("sub", "")
        ent = await _get_current_entitlement(request, user_id)
        if not ent["features"].get(feature, False):
            raise HTTPException(
                status_code=402,
                detail={
                    "error_code": "tier_required_feature",
                    "feature": feature,
                    "required_plan": "pro",
                    "current_plan": ent["tier"],
                    "upgrade_url": "/upgrade",
                },
            )
    return _dep
