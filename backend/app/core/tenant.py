"""
Tenant Context
==============
TenantContext dataclass represents the resolved tenant for a request.
Used by partner API endpoints to enforce quotas, scopes, and feature flags.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        pass
from typing import Any, Optional

from app.core.database import get_service_client


# ── Plan enum ────────────────────────────────────────────────────────────────

class TenantPlan(StrEnum):
    starter    = "starter"
    growth     = "growth"
    enterprise = "enterprise"


# ── Plan limits table ─────────────────────────────────────────────────────────

PLAN_LIMITS: dict[str, dict] = {
    TenantPlan.starter: {
        "api_calls_per_month": 1_000,
        "batch_size_max":      10,
        "seats_max":           1,
    },
    TenantPlan.growth: {
        "api_calls_per_month": 50_000,
        "batch_size_max":      100,
        "seats_max":           10,
    },
    TenantPlan.enterprise: {
        "api_calls_per_month": -1,   # unlimited
        "batch_size_max":      500,
        "seats_max":           -1,   # unlimited
    },
}


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class TenantContext:
    tenant_id:          str
    workspace_id:       str
    plan:               TenantPlan
    scopes:             list[str]                  # e.g. ["read","write","batch","webhook"]
    user_id:            Optional[str]   = None
    api_key_id:         Optional[str]   = None
    is_partner_request: bool            = False
    features:           dict[str, Any]  = field(default_factory=dict)
    rate_limit_per_min: int             = 60
    rate_limit_per_day: int             = 10_000

    # ── helpers ───────────────────────────────────────────────────────────────

    def has_scope(self, scope: str) -> bool:
        """True if *scope* is in self.scopes OR "admin" is in self.scopes."""
        return scope in self.scopes or "admin" in self.scopes

    def has_feature(self, feature: str) -> bool:
        """True if *feature* is enabled in the features flag dict."""
        return bool(self.features.get(feature, False))

    def plan_limits(self) -> dict:
        """Return resource limits dict for the current plan."""
        return PLAN_LIMITS.get(self.plan, PLAN_LIMITS[TenantPlan.starter]).copy()


# ── DB helper ─────────────────────────────────────────────────────────────────

async def get_tenant_for_workspace(workspace_id: str) -> Optional[TenantContext]:
    """
    Resolve a TenantContext from the workspace_id.

    Queries `tenants` joined with `tenant_settings` for the given workspace.
    Returns None when no tenant record exists.
    """
    try:
        supabase = get_service_client()

        # Fetch tenant row
        tenant_res = (
            supabase.table("tenants")
            .select("id, plan, workspace_id")
            .eq("workspace_id", workspace_id)
            .single()
            .execute()
        )
        tenant = tenant_res.data
        if not tenant:
            return None

        tenant_id = tenant["id"]
        plan      = TenantPlan(tenant.get("plan", TenantPlan.starter))

        # Fetch settings / feature flags
        settings_res = (
            supabase.table("tenant_settings")
            .select("features")
            .eq("tenant_id", tenant_id)
            .maybe_single()
            .execute()
        )
        settings     = settings_res.data or {}
        features     = settings.get("features") or {}

        return TenantContext(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            plan=plan,
            scopes=["read", "write", "batch", "webhook"],
            is_partner_request=False,
            features=features,
        )

    except Exception:
        return None
