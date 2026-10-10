"""
Web Push Notifications — Phase 23, made real in task #6256.
============================================================
Supabase-backed push subscription management.

Endpoints (all prefixed /api/v1/push by main.py):
  POST   /subscribe    — upsert push subscription (endpoint + keys)
  DELETE /unsubscribe  — remove subscription by endpoint
  POST   /test         — send a test notification to current user's subscriptions
  GET    /status       — check whether current user has any active subscriptions
  GET    /vapid-key    — return VAPID public key for browser subscription setup

Push subscriptions are persisted in the `push_subscriptions` Supabase table
(see database/migrations/018_phase23_mobile.sql), always tied to the signed-in
user (Supabase JWT); guests get 401.

Delivery (encryption, VAPID, endpoint allow-list + public-IP check, dead
subscription cleanup) lives in app.core.push_sender; env vars are documented
there.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core import push_sender
from app.core.database import get_service_client
from app.main import limiter

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────

class _PushKeys(BaseModel):
    p256dh: str = Field("", max_length=256)
    auth: str = Field("", max_length=128)


class PushSubscribeRequest(BaseModel):
    """Accepts both the browser's PushSubscription.toJSON() shape
    ({endpoint, expirationTime, keys: {p256dh, auth}}) — which is what the
    frontend sends — and the older flat {endpoint, p256dh, auth}."""
    endpoint: str = Field(..., max_length=push_sender.MAX_ENDPOINT_LEN)
    keys: Optional[_PushKeys] = None
    p256dh: str = Field("", max_length=256)
    auth: str = Field("", max_length=128)
    user_agent: str = Field("", max_length=512)


class PushTestRequest(BaseModel):
    message: str = Field("Thử nghiệm thông báo từ VidGrab 🎉", max_length=200)


# ── Auth helper ──────────────────────────────────────────────────────

def _get_user_id(request: Request) -> Optional[str]:
    """Extract user_id from a Supabase JWT Bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1]
    try:
        sb = get_service_client()
        user = sb.auth.get_user(token)
        return user.user.id if user and user.user else None
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/subscribe")
@limiter.limit("10/minute")
async def subscribe(payload: PushSubscribeRequest, request: Request):
    """Upsert a Web Push subscription for the current authenticated user.

    The endpoint must be https on a known browser push service and resolve
    to public addresses only — the server POSTs to it later, so anything
    else would let a client aim the worker at internal hosts."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    p256dh = (payload.keys.p256dh if payload.keys else "") or payload.p256dh
    auth_key = (payload.keys.auth if payload.keys else "") or payload.auth
    if not p256dh or not auth_key:
        raise HTTPException(status_code=422, detail="keys.p256dh and keys.auth are required")

    endpoint = payload.endpoint.strip()
    try:
        await asyncio.to_thread(push_sender.validate_push_endpoint, endpoint)
    except push_sender.PushEndpointError as e:
        raise HTTPException(status_code=400, detail=f"Invalid push endpoint: {e}")

    try:
        sb = get_service_client()
        sb.table("push_subscriptions").upsert(
            {
                "user_id":    user_id,
                "endpoint":   endpoint,
                "p256dh":     p256dh,
                "auth_key":   auth_key,
                "user_agent": payload.user_agent or request.headers.get("User-Agent", "")[:512],
            },
            on_conflict="user_id,endpoint",
        ).execute()
    except Exception as err:
        print(f"[Push] Subscribe error for user {user_id}: {type(err).__name__}")
        raise HTTPException(status_code=500, detail="Failed to save subscription")

    return {"success": True}


@router.delete("/unsubscribe")
async def unsubscribe(request: Request, endpoint: Optional[str] = None):
    """Remove a push subscription by endpoint for the current authenticated user."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not endpoint:
        raise HTTPException(status_code=400, detail="endpoint query param is required")

    try:
        sb = get_service_client()
        sb.table("push_subscriptions").delete().eq("user_id", user_id).eq("endpoint", endpoint).execute()
    except Exception as err:
        print(f"[Push] Unsubscribe error for user {user_id}: {err}")
        raise HTTPException(status_code=500, detail="Failed to remove subscription")

    return {"success": True}


@router.post("/test")
@limiter.limit("2/minute")
async def test_push(payload: PushTestRequest, request: Request):
    """Send a test push notification to all subscriptions of the current user."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        sb = get_service_client()
        res = (
            sb.table("push_subscriptions")
            .select("id")
            .eq("user_id", user_id)
            .execute()
        )
        rows = res.data or []
    except Exception as err:
        print(f"[Push] Test: DB lookup failed for user {user_id}: {type(err).__name__}")
        raise HTTPException(status_code=500, detail="Database error")

    if not rows:
        return {"success": False, "detail": "No active subscriptions found"}
    if not push_sender.is_enabled():
        return {"success": False, "detail": "Push notifications are not configured on the server",
                "subscriptions_found": len(rows), "notifications_sent": 0}

    sent = await asyncio.to_thread(push_sender.send_push, user_id, {
        "title": "VidGrab 🔔",
        "body": payload.message,
        "url": "/",
        "tag": "vidgrab-test",
    })
    return {
        "success": sent > 0,
        "subscriptions_found": len(rows),
        "notifications_sent": sent,
    }


@router.get("/status")
async def push_status(request: Request):
    """Return whether the current user has any active push subscriptions."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        sb = get_service_client()
        res = (
            sb.table("push_subscriptions")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        count = res.count if hasattr(res, "count") and res.count is not None else len(res.data or [])
    except Exception as err:
        print(f"[Push] Status check failed for user {user_id}: {err}")
        raise HTTPException(status_code=500, detail="Database error")

    return {
        "active": count > 0,
        "subscription_count": count,
    }


@router.get("/vapid-key")
async def get_vapid_key():
    """Return the VAPID public key that the browser needs to create a push
    subscription — derived from the server's private key, so rotating keys
    needs no frontend rebuild. '' when push is not configured (the frontend
    then skips subscribing)."""
    return {"public_key": push_sender.public_key()}
