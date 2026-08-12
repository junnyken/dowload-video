"""
Tenant API Key Management
=========================
Allows workspace admins/owners to manage partner API keys (vgp_ prefix).
These are distinct from personal user api_keys (vidgrab_ prefix).

Routes:
  GET    /api/v1/partner/api-keys          — list tenant API keys
  POST   /api/v1/partner/api-keys          — create new key
  PATCH  /api/v1/partner/api-keys/{key_id} — update label/scopes/limits
  DELETE /api/v1/partner/api-keys/{key_id} — revoke key
  POST   /api/v1/partner/api-keys/{key_id}/rotate — rotate (new secret, same id)
"""

import hashlib
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client

router = APIRouter(tags=["Partner API Keys"])

MAX_KEYS_PER_TENANT = 10


# ── Pydantic models ───────────────────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    scopes: List[str] = Field(default_factory=list)
    rate_limit_per_min: Optional[int] = Field(None, ge=1)
    rate_limit_per_day: Optional[int] = Field(None, ge=1)
    ip_allowlist: Optional[List[str]] = Field(default_factory=list)
    expires_at: Optional[datetime] = None


class UpdateKeyRequest(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=120)
    scopes: Optional[List[str]] = None
    rate_limit_per_min: Optional[int] = Field(None, ge=1)
    rate_limit_per_day: Optional[int] = Field(None, ge=1)
    ip_allowlist: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _generate_raw_key() -> str:
    """Generate a 68-char vgp_ prefixed key: 'vgp_' + 64 hex chars."""
    return "vgp_" + secrets.token_hex(32)


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _key_prefix(raw: str) -> str:
    """First 8 chars of the hex portion (after the 'vgp_' prefix)."""
    hex_part = raw[4:]  # strip 'vgp_'
    return "vgp_" + hex_part[:8]


def _require_admin_or_owner(user_id: str, workspace_id: str) -> None:
    """Raise 403 if user is not admin or owner of the workspace."""
    db = get_service_client()
    result = (
        db.table("workspace_memberships")
        .select("role")
        .eq("user_id", user_id)
        .eq("workspace_id", workspace_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=403, detail="Not a member of this workspace.")
    role = result.data[0].get("role", "")
    if role not in ("admin", "owner"):
        raise HTTPException(
            status_code=403,
            detail="Admin or owner role required to manage partner API keys.",
        )


def _get_user_tenant(user_id: str) -> Dict[str, Any]:
    """Return the tenant row for the user's primary workspace. Raises 404 if missing."""
    db = get_service_client()

    # Find user's workspace membership
    membership = (
        db.table("workspace_memberships")
        .select("workspace_id")
        .eq("user_id", user_id)
        .order("joined_at")
        .limit(1)
        .execute()
    )
    if not membership.data:
        raise HTTPException(status_code=404, detail="No workspace found for this user.")

    workspace_id = membership.data[0]["workspace_id"]

    # Find the tenant linked to that workspace
    tenant_res = (
        db.table("tenants")
        .select("*")
        .eq("workspace_id", workspace_id)
        .limit(1)
        .execute()
    )
    if not tenant_res.data:
        raise HTTPException(
            status_code=404,
            detail="No tenant configuration found for this workspace.",
        )

    tenant = tenant_res.data[0]
    tenant["_workspace_id"] = workspace_id  # carry for role checks
    return tenant


def _get_key_for_tenant(key_id: str, tenant_id: str) -> Dict[str, Any]:
    """Return key row, raising 404 if not found or not owned by this tenant."""
    db = get_service_client()
    result = (
        db.table("tenant_api_keys")
        .select("*")
        .eq("id", key_id)
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="API key not found.")
    return result.data[0]


def _strip_hash(row: Dict[str, Any]) -> Dict[str, Any]:
    """Remove key_hash before returning to client."""
    row.pop("key_hash", None)
    return row


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/partner/api-keys")
async def list_partner_api_keys(user: Dict = Depends(get_required_user)):
    """List all partner API keys for the authenticated user's tenant."""
    tenant = _get_user_tenant(user["id"])
    tenant_id = tenant["id"]

    db = get_service_client()
    result = (
        db.table("tenant_api_keys")
        .select(
            "id, key_prefix, label, scopes, rate_limit_per_min, rate_limit_per_day, "
            "ip_allowlist, expires_at, is_active, created_at, "
            "requests_today, requests_total, last_used_at"
        )
        .eq("tenant_id", tenant_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"keys": result.data or []}


@router.post("/partner/api-keys", status_code=201)
async def create_partner_api_key(
    body: CreateKeyRequest,
    user: Dict = Depends(get_required_user),
):
    """Create a new partner API key. Returns raw_key ONCE — store it securely."""
    try:
        tenant = _get_user_tenant(user["id"])
        tenant_id = tenant["id"]
        workspace_id = tenant["_workspace_id"]

        _require_admin_or_owner(user["id"], workspace_id)

        db = get_service_client()

        # Enforce active-key limit
        count_res = (
            db.table("tenant_api_keys")
            .select("id", count="exact")
            .eq("tenant_id", tenant_id)
            .eq("is_active", True)
            .execute()
        )
        active_count = count_res.count or 0
        if active_count >= MAX_KEYS_PER_TENANT:
            raise HTTPException(
                status_code=422,
                detail=f"Tenant already has {MAX_KEYS_PER_TENANT} active API keys. Revoke one before creating another.",
            )

        raw_key = _generate_raw_key()
        key_hash = _hash_key(raw_key)
        prefix = _key_prefix(raw_key)

        payload: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "key_hash": key_hash,
            "key_prefix": prefix,
            "label": body.label,
            "scopes": body.scopes,
            "rate_limit_per_min": body.rate_limit_per_min,
            "rate_limit_per_day": body.rate_limit_per_day,
            "ip_allowlist": body.ip_allowlist or [],
            "is_active": True,
            "requests_today": 0,
            "requests_total": 0,
            "last_used_at": None,
        }
        if body.expires_at:
            payload["expires_at"] = body.expires_at.isoformat()

        insert_res = (
            db.table("tenant_api_keys")
            .insert(payload)
            .execute()
        )
        if not insert_res.data:
            raise HTTPException(status_code=500, detail="Failed to create API key.")

        row = _strip_hash(insert_res.data[0])
        row["raw_key"] = raw_key  # shown exactly once
        return row
    except HTTPException:
        raise
    except Exception as exc:
        import traceback as _tb
        print(f"create_partner_api_key failed: {type(exc).__name__}: {exc}")
        print(_tb.format_exc())
        raise HTTPException(status_code=500, detail="Failed to create API key.") from exc


@router.patch("/partner/api-keys/{key_id}")
async def update_partner_api_key(
    key_id: str,
    body: UpdateKeyRequest,
    user: Dict = Depends(get_required_user),
):
    """Update mutable fields on a partner API key."""
    tenant = _get_user_tenant(user["id"])
    tenant_id = tenant["id"]
    workspace_id = tenant["_workspace_id"]

    _require_admin_or_owner(user["id"], workspace_id)
    _get_key_for_tenant(key_id, tenant_id)  # verify ownership

    updates: Dict[str, Any] = {}
    if body.label is not None:
        updates["label"] = body.label
    if body.scopes is not None:
        updates["scopes"] = body.scopes
    if body.rate_limit_per_min is not None:
        updates["rate_limit_per_min"] = body.rate_limit_per_min
    if body.rate_limit_per_day is not None:
        updates["rate_limit_per_day"] = body.rate_limit_per_day
    if body.ip_allowlist is not None:
        updates["ip_allowlist"] = body.ip_allowlist
    if body.expires_at is not None:
        updates["expires_at"] = body.expires_at.isoformat()
    if body.is_active is not None:
        updates["is_active"] = body.is_active

    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided to update.")

    db = get_service_client()
    result = (
        db.table("tenant_api_keys")
        .update(updates)
        .eq("id", key_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Update failed.")

    return _strip_hash(result.data[0])


@router.delete("/partner/api-keys/{key_id}", status_code=204)
async def revoke_partner_api_key(
    key_id: str,
    user: Dict = Depends(get_required_user),
):
    """Soft-delete (revoke) a partner API key."""
    tenant = _get_user_tenant(user["id"])
    tenant_id = tenant["id"]
    workspace_id = tenant["_workspace_id"]

    _require_admin_or_owner(user["id"], workspace_id)
    _get_key_for_tenant(key_id, tenant_id)  # verify ownership

    db = get_service_client()
    db.table("tenant_api_keys").update({"is_active": False}).eq("id", key_id).execute()
    return None


@router.post("/partner/api-keys/{key_id}/rotate", status_code=200)
async def rotate_partner_api_key(
    key_id: str,
    user: Dict = Depends(get_required_user),
):
    """Rotate a partner API key — generates new secret, same ID. Returns new raw_key once."""
    tenant = _get_user_tenant(user["id"])
    tenant_id = tenant["id"]
    workspace_id = tenant["_workspace_id"]

    _require_admin_or_owner(user["id"], workspace_id)
    _get_key_for_tenant(key_id, tenant_id)  # verify ownership

    raw_key = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    prefix = _key_prefix(raw_key)

    updates = {
        "key_hash": key_hash,
        "key_prefix": prefix,
        "requests_today": 0,
        "last_used_at": None,
    }

    db = get_service_client()
    result = (
        db.table("tenant_api_keys")
        .update(updates)
        .eq("id", key_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Rotation failed.")

    row = _strip_hash(result.data[0])
    row["raw_key"] = raw_key  # shown exactly once
    return row
