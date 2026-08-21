"""
Auth Middleware
===============
Optional JWT validation via Supabase Auth.
Accepts three auth patterns:
  1. Bearer JWT  (Supabase user login)
  2. Bearer API key  (legacy single-key via user_api_keys table, vg_ prefix)
  3. X-API-Key header  (new multi-key via api_keys table, vidgrab_ prefix)

Guest requests (no token) are allowed through — user_id will be None.

Usage in endpoints:
    @router.post("/fetch-link")
    async def fetch_link(payload: ..., user=Depends(get_optional_user)):
        user_id = user["id"] if user else None
"""

import hashlib
from typing import Optional, Dict, Any

from fastapi import Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from fastapi import Depends

from app.core.database import get_supabase_client

_bearer     = HTTPBearer(auto_error=False)
_api_key_hdr = APIKeyHeader(name="X-API-Key", auto_error=False)


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _lookup_api_key(token: str) -> Optional[Dict[str, Any]]:
    """
    Check Bearer token against the legacy user_api_keys table.
    Returns user dict or None.
    """
    key_hash = _hash_key(token)
    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("user_api_keys")
            .select("user_id, is_active")
            .eq("key_hash", key_hash)
            .single()
            .execute()
        )
        row = res.data
        if not row or not row.get("is_active"):
            return None

        user_id = row["user_id"]

        # Update last_used_at (non-blocking best-effort)
        try:
            supabase.table("user_api_keys").update({
                "last_used_at": "now()",
            }).eq("key_hash", key_hash).execute()
        except Exception:
            pass

        try:
            profile = supabase.table("profiles").select("tier").eq("id", user_id).single().execute()
            tier = (profile.data or {}).get("tier") or "free"
        except Exception:
            # Fail closed. This defaulted to 'pro', so any failure to read the
            # profile handed out paid entitlements — and it failed on every
            # request, because the lookup ran on an RLS-filtered client that
            # could never see a profiles row. Presenting an API key was enough
            # to be billed as Pro.
            tier = "free"

        return {
            "id":          user_id,
            "email":       "",
            "tier":        tier,
            "token":       token,
            "via_api_key": True,
        }
    except Exception:
        return None


def _lookup_new_api_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """
    Check X-API-Key header against the new multi-key api_keys table.
    Key must start with 'vidgrab_'. Returns user dict or None.
    """
    if not raw_key.startswith("vidgrab_"):
        return None

    key_hash = _hash_key(raw_key)
    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("api_keys")
            .select("id, user_id, is_active")
            .eq("key_hash", key_hash)
            .single()
            .execute()
        )
        row = res.data
        if not row or not row.get("is_active"):
            return None

        user_id = row["user_id"]
        key_id  = row["id"]

        # Update last_used_at + requests_total via RPC (best-effort)
        try:
            supabase.rpc(
                "increment_api_key_usage",
                {"key_id_arg": key_id},
            ).execute()
        except Exception:
            # Fallback: just update last_used_at if RPC not available
            try:
                supabase.table("api_keys").update({"last_used_at": "now()"}).eq("id", key_id).execute()
            except Exception:
                pass

        try:
            profile = supabase.table("profiles").select("tier").eq("id", user_id).single().execute()
            tier = (profile.data or {}).get("tier", "free")
        except Exception:
            tier = "free"

        return {
            "id":          user_id,
            "email":       "",
            "tier":        tier,
            "token":       raw_key,
            "via_api_key": True,
            "api_key_id":  key_id,
        }
    except Exception:
        return None


async def get_optional_user(
    _request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    x_api_key: Optional[str] = Depends(_api_key_hdr),
) -> Optional[Dict[str, Any]]:
    """
    Extract and validate identity from Authorization or X-API-Key headers.
    Accepts:
      1. Supabase JWT Bearer token
      2. Legacy API key Bearer token (user_api_keys table)
      3. X-API-Key header (api_keys table, vidgrab_ prefix)
    Returns user dict on success, None for guest.
    Never raises — guest flow must always work.
    """
    # ── Path 3: X-API-Key header (new multi-key) ─────────────────────
    if x_api_key:
        result = _lookup_new_api_key(x_api_key)
        if result:
            return result

    if not credentials or not credentials.credentials:
        return None

    token = credentials.credentials

    # ── Path 1: Supabase JWT ─────────────────────────────────────────
    try:
        supabase = get_supabase_client()
        response = supabase.auth.get_user(token)
        if response and response.user:
            u = response.user
            return {
                "id":    str(u.id),
                "email": u.email,
                "token": token,
            }
    except Exception:
        pass

    # ── Path 2: Legacy Bearer API key ────────────────────────────────
    return _lookup_api_key(token)


async def get_required_user(
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
) -> Dict[str, Any]:
    """
    Like get_optional_user but raises 401 if not authenticated.
    Use on endpoints that require login (preferences, usage, etc.)
    """
    from fastapi import HTTPException
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
