"""
API Key Management — Phase 10
==============================
Self-service multi-key API management for Pro users.

Table: api_keys (id, user_id, key_hash, key_prefix, label, is_active,
                 created_at, last_used_at, requests_today, requests_total)

Key format: vidgrab_<32 hex chars>
Key is shown ONCE on creation, then only the prefix is exposed.
Auth: uses X-API-Key header in requests (NOT Bearer).
Quota: shared with the owning user's web quota (no separate counter).
"""

import hashlib
import secrets
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth_middleware import get_required_user
from app.core.database import get_supabase_client
from app.core.quotas import _get_profile as get_user_profile

router = APIRouter()

MAX_KEYS_PER_USER = 3


# ── helpers ──────────────────────────────────────────────────────────────────

def _generate_raw_key() -> str:
    return "vidgrab_" + secrets.token_hex(16)   # 8+32 = 40 chars total


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _prefix_of(raw: str) -> str:
    """First 16 chars: 'vidgrab_XXXXXXXX'."""
    return raw[:16]


def _assert_pro(user_id: str):
    """Raise 403 if the user is not on Pro tier."""
    try:
        profile = get_user_profile(user_id)
    except Exception:
        profile = None

    if not profile or profile.get("tier", "free") != "pro":
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "tier_required_feature",
                "user_message": "API key management chỉ dành cho Pro.",
                "upgrade_url": "/upgrade",
            },
        )


# ── models ───────────────────────────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    label: str = Field(default="My API Key", max_length=64)


class UpdateKeyRequest(BaseModel):
    label: str = Field(..., max_length=64)


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_keys(user: Dict[str, Any] = Depends(get_required_user)):
    """List all active + inactive API keys for the authenticated user."""
    _assert_pro(user["id"])

    supabase = get_supabase_client()
    res = (
        supabase.table("api_keys")
        .select("id, key_prefix, label, is_active, created_at, last_used_at, requests_today, requests_total")
        .eq("user_id", user["id"])
        .order("created_at", desc=False)
        .execute()
    )
    return {"keys": res.data or []}


@router.post("", status_code=201)
async def create_key(
    body: CreateKeyRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Create a new API key (max 3 per user, Pro only).
    Returns the raw key ONCE — it cannot be retrieved again.
    """
    _assert_pro(user["id"])

    supabase = get_supabase_client()

    # Check current key count
    count_res = (
        supabase.table("api_keys")
        .select("id", count="exact")
        .eq("user_id", user["id"])
        .eq("is_active", True)
        .execute()
    )
    active_count = count_res.count or 0
    if active_count >= MAX_KEYS_PER_USER:
        raise HTTPException(
            status_code=429,
            detail={
                "error_code": "api_key_limit",
                "user_message": f"Tối đa {MAX_KEYS_PER_USER} API key mỗi tài khoản.",
            },
        )

    raw_key = _generate_raw_key()
    key_hash = _hash_key(raw_key)
    key_prefix = _prefix_of(raw_key)

    insert_res = (
        supabase.table("api_keys")
        .insert({
            "user_id":    user["id"],
            "key_hash":   key_hash,
            "key_prefix": key_prefix,
            "label":      body.label.strip() or "My API Key",
        })
        .execute()
    )

    row = (insert_res.data or [{}])[0]
    return {
        "id":         row.get("id"),
        "key_prefix": key_prefix,
        "label":      row.get("label"),
        "api_key":    raw_key,          # shown ONCE
        "created_at": row.get("created_at"),
        "note":       "Lưu API key này ngay — sẽ không hiển thị lại.",
    }


@router.delete("/{key_id}", status_code=200)
async def revoke_key(
    key_id: str,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Revoke (deactivate) a specific API key by ID."""
    _assert_pro(user["id"])

    supabase = get_supabase_client()
    res = (
        supabase.table("api_keys")
        .update({"is_active": False})
        .eq("id", key_id)
        .eq("user_id", user["id"])          # ownership check
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Key không tìm thấy.")
    return {"revoked": True, "id": key_id}


@router.patch("/{key_id}", status_code=200)
async def update_key_label(
    key_id: str,
    body: UpdateKeyRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Update the display label of an API key."""
    _assert_pro(user["id"])

    supabase = get_supabase_client()
    res = (
        supabase.table("api_keys")
        .update({"label": body.label.strip()})
        .eq("id", key_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail="Key không tìm thấy.")
    return {"updated": True, "id": key_id, "label": body.label.strip()}
