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

    # 1b. The Partner API is exempt from the per-IP limiter, so an IP that
    # keeps presenting invalid keys is stopped here, before the DB lookup.
    from app.core.bad_key_guard import note_bad_key, too_many_bad_keys  # noqa: PLC0415
    if too_many_bad_keys(request):
        raise HTTPException(
            status_code=429,
            detail={
                "code":    "too_many_invalid_keys",
                "message": "Too many invalid API keys from this IP; retry in a minute",
                "retry_after_seconds": 60,
            },
            headers={"Retry-After": "60"},
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
        note_bad_key(request)
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
        # get_client_ip, not the ASGI client host: uvicorn runs with
        # --forwarded-allow-ips "*", so that host is the FIRST
        # X-Forwarded-For entry — whatever the caller typed. A leaked key plus
        # "X-Forwarded-For: <allowed ip>" walked through the allowlist
        # (task #6398).
        from app.core.client_ip import get_client_ip  # noqa: PLC0415
        client_host = get_client_ip(request)
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

    # 6. Fetch tenant plan, status and monthly cap
    monthly_cap = None
    tenant_status = "active"
    try:
        tenant_res = (
            supabase.table("tenants")
            .select("plan, workspace_id, status, plan_api_calls_per_month")
            .eq("id", tenant_id)
            .single()
            .execute()
        )
        tenant_row   = tenant_res.data or {}
        plan_str     = tenant_row.get("plan", "starter")
        # Use workspace_id from key first; fall back to tenant row
        workspace_id = workspace_id or tenant_row.get("workspace_id", "")
        tenant_status = tenant_row.get("status") or "active"
        monthly_cap   = tenant_row.get("plan_api_calls_per_month")
    except Exception:
        plan_str = "starter"
    try:
        plan = TenantPlan(plan_str)
    except ValueError:
        plan = TenantPlan.starter

    # 6b. A suspended or canceled customer keeps its keys but cannot use them
    # (admin → Tenants → status).
    if tenant_status != "active":
        raise HTTPException(
            status_code=403,
            detail={
                "code":    "tenant_inactive",
                "message": f"This account is {tenant_status}",
            },
        )

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

    # 7b. Per-key requests-per-minute limit (task #6398). The column was
    # stored, shown on /usage and editable, but nothing enforced it.
    per_min = key_row.get("rate_limit_per_min") or 60
    retry_after = _per_minute_exceeded(key_id, per_min)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail={
                "code":    "rate_limited",
                "message": f"Rate limit of {per_min} requests per minute exceeded for this API key",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    # 8. Fire-and-forget usage counter
    asyncio.create_task(_update_key_usage(key_id))

    from app.core.bad_key_guard import note_valid_key  # noqa: PLC0415
    note_valid_key(raw_key)

    # 9. Build and return TenantContext
    scopes = key_row.get("scopes") or ["read", "write", "batch", "webhook"]

    return TenantContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        plan=plan,
        scopes=scopes,
        api_key_id=key_id,
        is_partner_request=True,
        features=features,
        rate_limit_per_min=key_row.get("rate_limit_per_min") or 60,
        rate_limit_per_day=key_row.get("rate_limit_per_day") or 10_000,
        monthly_cap=monthly_cap,
    )


def _per_minute_exceeded(key_id: str, limit: int) -> Optional[int]:
    """Count one request for `key_id` in the current minute window. Returns the
    seconds left in the window when `limit` is exceeded, else None.

    Fails OPEN on Redis errors (returns None): the partner API kept working
    before this check existed, and a Redis outage must not take it down."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    try:
        import time  # noqa: PLC0415
        from app.core.redis_client import get_redis  # noqa: PLC0415
        r = get_redis()
        window = int(time.time() // 60)
        key = f"partner:rpm:{key_id}:{window}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, 120)
        _note_rpm(r, key_id, window, int(count), int(count) > limit)
        if count > limit:
            return max(1, 60 - int(time.time() % 60))
    except Exception:
        return None
    return None


_RPM_PEAK = "partner:rpm_peak:{key_id}:{hour}"      # zset "p" → busiest minute of the hour
_RPM_REFUSED = "partner:rpm_refused:{key_id}:{hour}"
_RPM_KEEP_S = 25 * 3600


def _note_rpm(r, key_id: str, window: int, count: int, refused: bool) -> None:
    """Per hour: the busiest minute and how many requests were refused — the
    admin sees when a partner's key needs a higher requests/minute (19/10)."""
    try:
        hour = window // 60
        pipe = r.pipeline(transaction=False)
        pk = _RPM_PEAK.format(key_id=key_id, hour=hour)
        pipe.zadd(pk, {"p": count}, gt=True)
        pipe.expire(pk, _RPM_KEEP_S)
        if refused:
            rk = _RPM_REFUSED.format(key_id=key_id, hour=hour)
            pipe.incr(rk)
            pipe.expire(rk, _RPM_KEEP_S)
        pipe.execute()
    except Exception:
        pass


def rpm_stats(key_id: str, now: Optional[float] = None) -> dict:
    """{now, peak_24h, refused_24h} for one partner key. Never raises."""
    import time  # noqa: PLC0415
    t = now or time.time()
    window, hour = int(t // 60), int(t // 3600)
    out = {"now": 0, "peak_24h": 0, "refused_24h": 0}
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        r = get_redis()
        pipe = r.pipeline(transaction=False)
        pipe.get(f"partner:rpm:{key_id}:{window}")
        hours = [hour - i for i in range(24)]
        for h in hours:
            pipe.zscore(_RPM_PEAK.format(key_id=key_id, hour=h), "p")
        for h in hours:
            pipe.get(_RPM_REFUSED.format(key_id=key_id, hour=h))
        res = pipe.execute()
        out["now"] = int(res[0] or 0)
        out["peak_24h"] = int(max((float(x) for x in res[1:25] if x is not None), default=0))
        out["refused_24h"] = sum(int(x) for x in res[25:] if x)
    except Exception:
        pass
    return out


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
