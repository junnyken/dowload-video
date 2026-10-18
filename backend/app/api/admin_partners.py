"""
Admin — Partner API onboarding
==============================
Issue Partner API access (vgp_ keys) to a customer that calls VidGrab from
its own backend. Before this there was no way to do it: tenants were only
created by the one-time backfill in migration 012, and the self-service
/api/v1/partner/api-keys needs a tenant linked to the caller's workspace —
so a new customer got 404 "No tenant configuration found".

A partner tenant stands alone (workspace_id NULL): the customer never logs
into the website; VidGrab hands them a key.

Routes (all behind verify_admin, mounted under /api/v1/admin):
  POST  /enterprise/tenants                     — create a partner customer
  POST  /enterprise/tenants/{id}/limits         — set its jobs/month cap
  POST  /enterprise/tenants/{id}/api-keys       — issue a key (raw key shown ONCE)
  POST  /enterprise/api-keys/{id}/rotate        — new secret, same key (shown ONCE)
  PATCH /enterprise/api-keys/{id}               — label / per-key limits / IP allowlist / expiry
  DELETE /enterprise/api-keys/{id}              — delete a key for good
  DELETE /enterprise/tenants/{id}               — delete a partner customer (not a web-linked one)
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.admin import verify_admin
from app.api.tenant_api_keys import (
    _generate_raw_key, _hash_key, _key_prefix, _strip_hash, MAX_KEYS_PER_TENANT,
)
from app.core.audit import log_admin_action
from app.core.database import get_supabase_client
from app.core.tenant import PLAN_LIMITS, TenantPlan

router = APIRouter()

ALL_SCOPES = ["read", "write", "batch", "webhook"]
_PLANS = ("starter", "growth", "enterprise")


def plan_default_cap(plan: str) -> int:
    """Jobs/month a plan starts with; -1 = unlimited."""
    return int(PLAN_LIMITS[TenantPlan(plan)]["api_calls_per_month"])


def _slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name.lower().replace("đ", "d"))
    ascii_name = ascii_name.encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:40] or "partner"
    return f"{base}-{secrets.token_hex(3)}"


def _check_ips(ips: Optional[List[str]]) -> Optional[List[str]]:
    if ips is None:
        return None
    out = []
    for ip in ips:
        ip = (ip or "").strip()
        if not ip:
            continue
        try:
            out.append(str(ipaddress.ip_address(ip)))
        except ValueError as exc:
            raise ValueError(f"IP không hợp lệ: {ip}") from exc
    return out or None


# ── Models ────────────────────────────────────────────────────────────────────

class CreateTenantBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    plan: str = "starter"
    # None = the plan's default; -1 = unlimited
    api_calls_per_month: Optional[int] = Field(None, ge=-1)

    @field_validator("plan")
    @classmethod
    def _plan(cls, v: str) -> str:
        if v not in _PLANS:
            raise ValueError("plan phải là starter, growth hoặc enterprise")
        return v


class TenantLimitsBody(BaseModel):
    api_calls_per_month: int = Field(..., ge=-1)


class IssueKeyBody(BaseModel):
    label: str = Field("API Key", min_length=1, max_length=120)
    rate_limit_per_min: int = Field(60, ge=1, le=6000)
    rate_limit_per_day: int = Field(10_000, ge=1, le=10_000_000)
    ip_allowlist: Optional[List[str]] = None   # None = any IP
    expires_at: Optional[datetime] = None

    @field_validator("ip_allowlist")
    @classmethod
    def _ips(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        return _check_ips(v)


class UpdateKeyBody(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=120)
    rate_limit_per_min: Optional[int] = Field(None, ge=1, le=6000)
    rate_limit_per_day: Optional[int] = Field(None, ge=1, le=10_000_000)
    ip_allowlist: Optional[List[str]] = None   # send null to allow any IP
    expires_at: Optional[datetime] = None      # send null to never expire

    @field_validator("ip_allowlist")
    @classmethod
    def _ips(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        return _check_ips(v)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_tenant(tenant_id: str) -> Dict[str, Any]:
    res = (
        get_supabase_client().table("tenants")
        .select("id, name, plan, status")
        .eq("id", tenant_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách hàng")
    return res.data


def _get_key(key_id: str) -> Dict[str, Any]:
    res = (
        get_supabase_client().table("tenant_api_keys")
        .select("id, tenant_id")
        .eq("id", key_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Không tìm thấy API key")
    return res.data


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/enterprise/tenants", status_code=201)
async def create_partner_tenant(body: CreateTenantBody, request: Request, _=Depends(verify_admin)):
    db = get_supabase_client()
    cap = body.api_calls_per_month if body.api_calls_per_month is not None else plan_default_cap(body.plan)
    res = db.table("tenants").insert({
        "name": body.name.strip(),
        "slug": _slugify(body.name),
        "plan": body.plan,
        "plan_api_calls_per_month": cap,
        "status": "active",
    }).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Không tạo được khách hàng")
    tenant = res.data[0]
    try:
        db.table("tenant_settings").insert({"tenant_id": tenant["id"]}).execute()
    except Exception:  # noqa: BLE001 — partner_auth treats a missing row as no features
        pass
    log_admin_action(request, "admin.tenant.created", resource_type="tenant", resource_id=tenant["id"])
    return tenant


@router.post("/enterprise/tenants/{tenant_id}/limits")
async def set_partner_tenant_limits(tenant_id: str, body: TenantLimitsBody, request: Request,
                                    _=Depends(verify_admin)):
    _get_tenant(tenant_id)
    get_supabase_client().table("tenants").update(
        {"plan_api_calls_per_month": body.api_calls_per_month}
    ).eq("id", tenant_id).execute()
    log_admin_action(request, "admin.tenant.limits_changed", resource_type="tenant", resource_id=tenant_id)
    return {"success": True, "tenant_id": tenant_id, "api_calls_per_month": body.api_calls_per_month}


@router.post("/enterprise/tenants/{tenant_id}/api-keys", status_code=201)
async def issue_partner_key(tenant_id: str, body: IssueKeyBody, request: Request,
                            _=Depends(verify_admin)):
    _get_tenant(tenant_id)
    db = get_supabase_client()
    active = (
        db.table("tenant_api_keys").select("id", count="exact")
        .eq("tenant_id", tenant_id).eq("is_active", True).execute()
    )
    if (active.count or 0) >= MAX_KEYS_PER_TENANT:
        raise HTTPException(
            status_code=422,
            detail=f"Khách này đã có {MAX_KEYS_PER_TENANT} key đang bật. Tắt bớt một key trước khi cấp key mới.",
        )
    raw_key = _generate_raw_key()
    payload: Dict[str, Any] = {
        "tenant_id": tenant_id,
        "key_hash": _hash_key(raw_key),
        "key_prefix": _key_prefix(raw_key),
        "label": body.label,
        "scopes": ALL_SCOPES,
        "rate_limit_per_min": body.rate_limit_per_min,
        "rate_limit_per_day": body.rate_limit_per_day,
        "ip_allowlist": body.ip_allowlist,
        "is_active": True,
        "created_by": "admin",
    }
    if body.expires_at:
        payload["expires_at"] = body.expires_at.isoformat()
    res = db.table("tenant_api_keys").insert(payload).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Không cấp được API key")
    row = _strip_hash(res.data[0])
    row["raw_key"] = raw_key  # shown exactly once
    log_admin_action(request, "admin.api_key.issued", resource_type="api_key", resource_id=row.get("id"))
    return row


@router.post("/enterprise/api-keys/{key_id}/rotate")
async def rotate_partner_key(key_id: str, request: Request, _=Depends(verify_admin)):
    _get_key(key_id)
    raw_key = _generate_raw_key()
    res = get_supabase_client().table("tenant_api_keys").update({
        "key_hash": _hash_key(raw_key),
        "key_prefix": _key_prefix(raw_key),
        "last_used_at": None,
    }).eq("id", key_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Không đổi được API key")
    row = _strip_hash(res.data[0])
    row["raw_key"] = raw_key  # shown exactly once
    log_admin_action(request, "admin.api_key.rotated", resource_type="api_key", resource_id=key_id)
    return row


@router.patch("/enterprise/api-keys/{key_id}")
async def update_partner_key(key_id: str, body: UpdateKeyBody, request: Request,
                             _=Depends(verify_admin)):
    _get_key(key_id)
    sent = body.model_fields_set
    updates: Dict[str, Any] = {}
    for f in ("label", "rate_limit_per_min", "rate_limit_per_day"):
        if f in sent and getattr(body, f) is not None:
            updates[f] = getattr(body, f)
    # null is meaningful for these two: any IP / never expires
    if "ip_allowlist" in sent:
        updates["ip_allowlist"] = body.ip_allowlist
    if "expires_at" in sent:
        updates["expires_at"] = body.expires_at.isoformat() if body.expires_at else None
    if not updates:
        raise HTTPException(status_code=422, detail="Không có trường nào để cập nhật")
    res = get_supabase_client().table("tenant_api_keys").update(updates).eq("id", key_id).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Cập nhật thất bại")
    log_admin_action(request, "admin.api_key.updated", resource_type="api_key", resource_id=key_id)
    return _strip_hash(res.data[0])


@router.delete("/enterprise/api-keys/{key_id}")
async def delete_partner_key(key_id: str, request: Request, _=Depends(verify_admin)):
    """Delete a key for good (the toggle only disables it). Its past jobs stay,
    with api_key_id set to NULL (FK ON DELETE SET NULL)."""
    _get_key(key_id)
    get_supabase_client().table("tenant_api_keys").delete().eq("id", key_id).execute()
    log_admin_action(request, "admin.api_key.deleted", resource_type="api_key", resource_id=key_id)
    return {"success": True, "key_id": key_id}


@router.delete("/enterprise/tenants/{tenant_id}")
async def delete_partner_tenant(tenant_id: str, request: Request, _=Depends(verify_admin)):
    """Delete a partner customer with its keys, jobs, settings, usage and
    webhooks (all ON DELETE CASCADE). A tenant linked to a website workspace
    is refused: it belongs to a web account, not to a partner created here."""
    res = (
        get_supabase_client().table("tenants")
        .select("id, workspace_id")
        .eq("id", tenant_id)
        .maybe_single()
        .execute()
    )
    if not res or not res.data:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách hàng")
    if res.data.get("workspace_id"):
        # wording: BA review
        raise HTTPException(
            status_code=409,
            detail="Khách này gắn với một tài khoản trên web nên không xoá được ở đây. Hãy đổi trạng thái sang suspended.",
        )
    get_supabase_client().table("tenants").delete().eq("id", tenant_id).execute()
    log_admin_action(request, "admin.tenant.deleted", resource_type="tenant", resource_id=tenant_id)
    return {"success": True, "tenant_id": tenant_id}
