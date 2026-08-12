"""
Partner API Auth
================
Resolves tenant_api_keys for partner requests.
Partner keys use prefix "vgp_" (vs "vidgrab_" for user keys).

Key resolution order:
  1. X-API-Key header
  2. Authorization: Bearer vgp_... (only accepted when value starts with "vgp_")

On success returns a fully populated TenantContext.
On failure raises HTTPException 401/403.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

from app.core.database import get_service_client
from app.core.tenant import TenantContext, TenantPlan

_PARTNER_KEY_PREFIX = "vgp_"


# ── Key extraction ────────────────────────────────────────────────────────────

def _extract_partner_key(request: Request) -> Optional[str]:
    """
    Pull the raw partner API key from the incoming request.

    Checks X-API-Key header first; then Authorization: Bearer <token> but
    ONLY when the bearer value starts with the partner prefix "vgp_".
    """
    # 1. Explicit X-API-Key header
    x_api_key = request.headers.get("X-API-Key")
    if x_api_key:
        return x_api_key

    # 2. Authorization: Bearer vgp_...
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):]
        if token.startswith(_PARTNER_KEY_PREFIX):
            return token

    return None


# ── Background counter update ─────────────────────────────────────────────────

async def _update_key_usage(key_id: str) -> None:
    """
    Increment requests_today / requests_total and stamp last_used_at.
    Runs as a fire-and-forget background task — failures are silently swallowed.
    """
    try:
        supabase = get_service_client()

        # Attempt a server-side RPC increment first (no race); fall back to
        # a plain read-increment-write when the RPC is not deployed yet.
        try:
            supabase.rpc(
                "increment_partner_key_usage",
                {"p_key_id": key_id},
            ).execute()
        except Exception:
            # Fallback: best-effort read → increment → write
            res = (
                supabase.table("tenant_api_keys")
                .select("requests_today, requests_total")
                .eq("id", key_id)
                .single()
                .execute()
            )
            row = res.data or {}
            supabase.table("tenant_api_keys").update(
                {
                    "requests_today":  (row.get("requests_today")  or 0) + 1,
                    "requests_total":  (row.get("requests_total")  or 0) + 1,
                    "last_used_at":    "now()",
                }
            ).eq("id", key_id).execute()
    except Exception:
        pass  # never let a counter update kill the real request


# ── Main dependency ───────────────────────────────────────────────────────────

async def get_partner_tenant(request: Request) -> TenantContext:
    """
    FastAPI dependency — resolves a partner API key and returns TenantContext.

    Raises:
      401 – no key / invalid key / expired key
      403 – key inactive / IP not in allowlist
    """
    raw_key = _extract_partner_key(request)

    # 1. Key must be present
    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail={
                "code":    "partner_auth_required",
                "message": "X-API-Key header required",
            },
        )

    # 2. Hash and look up
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    supabase = get_service_client()
    try:
        key_res = (
            supabase.table("tenant_api_keys")
            .select(
                "id, tenant_id, workspace_id, scopes, "
                "rate_limit_per_min, rate_limit_per_day, "
                "is_active, ip_allowlist, expires_at"
            )
            .eq("key_hash", key_hash)
            .single()
            .execute()
        )
        key_row = key_res.data
    except Exception:
        key_row = None

    if not key_row:
        raise HTTPException(
            status_code=401,
            detail={
                "code":    "invalid_api_key",
                "message": "Invalid or revoked API key",
            },
        )

    # 3. Active check
    if not key_row.get("is_active", False):
        raise HTTPException(
            status_code=403,
            detail={
                "code":    "api_key_inactive",
                "message": "This API key has been deactivated",
            },
        )

    # 4. Expiry check
    expires_at_raw = key_row.get("expires_at")
    if expires_at_raw:
        # Supabase returns ISO-8601 strings; parse defensively
        if isinstance(expires_at_raw, str):
            try:
                expires_at = datetime.fromisoformat(
                    expires_at_raw.replace("Z", "+00:00")
                )
            except ValueError:
                expires_at = None
        elif isinstance(expires_at_raw, datetime):
            expires_at = expires_at_raw
        else:
            expires_at = None

        if expires_at and expires_at < datetime.now(tz=timezone.utc):
            raise HTTPException(
                status_code=401,
                detail={
                    "code":    "api_key_expired",
                    "message": "This API key has expired",
                },
            )

    # 5. IP allowlist check
    ip_allowlist = key_row.get("ip_allowlist")
    if ip_allowlist is not None:
        client_host = request.client.host if request.client else ""
        if client_host not in ip_allowlist:
            raise HTTPException(
                status_code=403,
                detail={
                    "code":    "ip_not_allowed",
                    "message": "Client IP is not in the allowlist for this key",
                },
            )

    key_id       = key_row["id"]
    tenant_id    = key_row["tenant_id"]
    workspace_id = key_row.get("workspace_id") or ""

    # 6. Fetch tenant plan
    try:
        tenant_res = (
            supabase.table("tenants")
            .select("plan, workspace_id")
            .eq("id", tenant_id)
            .single()
            .execute()
        )
        tenant_row   = tenant_res.data or {}
        plan_str     = tenant_row.get("plan", "starter")
        # Use workspace_id from key first; fall back to tenant row
        workspace_id = workspace_id or tenant_row.get("workspace_id", "")
    except Exception:
        plan_str = "starter"

    # 7. Fetch feature flags from tenant_settings
    try:
        settings_res = (
            supabase.table("tenant_settings")
            .select("features")
            .eq("tenant_id", tenant_id)
            .maybe_single()
            .execute()
        )
        settings_row = settings_res.data or {}
        features     = settings_row.get("features") or {}
    except Exception:
        features = {}

    # 8. Fire-and-forget usage counter
    asyncio.create_task(_update_key_usage(key_id))

    # 9. Build and return TenantContext
    scopes = key_row.get("scopes") or ["read", "write", "batch", "webhook"]

    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        plan=TenantPlan(plan_str),
        scopes=scopes,
        api_key_id=key_id,
        is_partner_request=True,
        features=features,
        rate_limit_per_min=key_row.get("rate_limit_per_min") or 60,
        rate_limit_per_day=key_row.get("rate_limit_per_day") or 10_000,
    )


# ── Optional variant (no-key → None, bad-key → still raises) ─────────────────

async def get_optional_partner_tenant(request: Request) -> Optional[TenantContext]:
    """
    Like get_partner_tenant but returns None when no key is provided at all.
    Still raises 401/403 when a key is present but invalid/expired/inactive.
    """
    raw_key = _extract_partner_key(request)
    if not raw_key:
        return None
    return await get_partner_tenant(request)
