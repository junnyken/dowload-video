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

_NEW_KEY_PREFIX = "vidgrab_"
_LEGACY_KEY_PREFIX = "vg_"


def _metered(request, user: Dict[str, Any]) -> Dict[str, Any]:
    """Count one call against the key owner's monthly API quota (owner 14/10,
    app.core.api_quota); 429 when it is used up. Only API-key callers get
    here — the site's own session traffic is never metered."""
    from fastapi import HTTPException  # noqa: PLC0415
    from app.core import api_quota  # noqa: PLC0415
    try:
        from app.core.quotas import _get_tier  # noqa: PLC0415
        tier = _get_tier(str(user["id"])) or "free"
    except Exception:  # noqa: BLE001
        tier = user.get("tier") or "free"
    q = api_quota.consume(str(user["id"]), tier)
    try:
        if request is not None:
            request.state.api_quota = q
    except Exception:  # noqa: BLE001
        pass
    if not q["allowed"]:
        raise HTTPException(
            status_code=429,
            detail={
                "error_code": "api_monthly_quota_exceeded",
                "user_message": f"Đã dùng hết {q['limit']} lượt gọi API của tháng này.",
                "limit": q["limit"],
                "reset_at": q["reset_at"],
            },
            headers=api_quota.headers(q),
        )
    return {**user, "api_quota": q}


def _invalid_key(request=None):
    from fastapi import HTTPException  # noqa: PLC0415
    # Counted per IP: keyed requests get their own limiter bucket, so an IP
    # spraying bad keys must drop back to its IP bucket (bad_key_guard).
    from app.core.bad_key_guard import note_bad_key  # noqa: PLC0415
    note_bad_key(request)
    raise HTTPException(status_code=401, detail={
        "error_code": "invalid_api_key",
        "user_message": "API key không hợp lệ hoặc đã bị thu hồi.",
    })


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
    Raises 401 invalid_api_key only when the caller presented an API key
    (X-API-Key, or Bearer vidgrab_/vg_) that does not resolve; no
    credentials or an unusable web session is a guest.
    """
    # An API key the caller presented and that does not resolve is an error,
    # not a guest (14/10): a mistyped or revoked key used to fall through to
    # the anonymous path, so the developer got guest quotas and odd 400/429s
    # instead of being told the key is wrong. A stale web session (JWT) still
    # degrades to guest — the guest flow must keep working for the site.
    # ── Path 3: X-API-Key header (new multi-key) ─────────────────────
    if x_api_key:
        result = _lookup_new_api_key(x_api_key)
        if result:
            return _metered(_request, result)
        _invalid_key(_request)

    if not credentials or not credentials.credentials:
        return None

    token = credentials.credentials

    # The public docs said "Authorization: Bearer <key>" while vidgrab_ keys
    # only worked as X-API-Key; accept both.
    if token.startswith(_NEW_KEY_PREFIX):
        result = _lookup_new_api_key(token)
        if result:
            return _metered(_request, result)
        _invalid_key(_request)

    # ── Path 1: Supabase JWT ─────────────────────────────────────────
    try:
        supabase = get_supabase_client()
        response = supabase.auth.get_user(token)
        if response and response.user:
            u = response.user
            # email_confirmed_at: None = the account never confirmed its
            # email (Channel Watch requires it for Free accounts, task #6257).
            confirmed = getattr(u, "email_confirmed_at", None)
            return {
                "id":    str(u.id),
                "email": u.email,
                "token": token,
                "email_confirmed_at": str(confirmed) if confirmed else None,
            }
    except Exception:
        pass

    # ── Path 2: Legacy Bearer API key ────────────────────────────────
    legacy = _lookup_api_key(token)
    if legacy is None and token.startswith(_LEGACY_KEY_PREFIX):
        _invalid_key(_request)
    return _metered(_request, legacy) if legacy else None


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
