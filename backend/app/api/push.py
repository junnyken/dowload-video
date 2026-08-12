"""
Web Push Notifications — Phase 23
===================================
Supabase-backed push subscription management.

Endpoints (all prefixed /api/v1/push by main.py):
  POST   /subscribe    — upsert push subscription (endpoint + keys)
  DELETE /unsubscribe  — remove subscription by endpoint
  POST   /test         — send a test notification to current user's subscriptions
  GET    /status       — check whether current user has any active subscriptions
  GET    /vapid-key    — return VAPID public key for browser subscription setup

Push subscriptions are persisted in the `push_subscriptions` Supabase table
(see database/migrations/018_phase23_mobile.sql).

For real encrypted Web Push, set PUSH_VAPID_PRIVATE_KEY / PUSH_VAPID_PUBLIC_KEY
env vars and install pywebpush.  The /test endpoint currently uses a plain HTTP
POST stub so it works without VAPID keys.
"""

import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.database import get_service_client
from app.main import limiter

router = APIRouter()


# ── Pydantic models ──────────────────────────────────────────────────

class PushSubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str = ""


class PushTestRequest(BaseModel):
    message: str = "Thử nghiệm thông báo từ VidGrab 🎉"


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


# ── Push send helper ─────────────────────────────────────────────────

async def _send_push_notification(endpoint: str, payload: dict) -> bool:
    """
    Simple push without VAPID encryption.
    Real push requires pywebpush + VAPID keys (see PUSH_VAPID_PRIVATE_KEY env).
    This stub sends a plain POST and returns success/fail.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(endpoint, json=payload)
            return r.status_code in (200, 201, 202)
    except Exception:
        return False


# ── Public helper (importable from other modules) ────────────────────

async def notify_user_job_done(
    user_id: str,
    job_id: str,
    title: str,
    download_url: str,
) -> None:
    """
    Send a 'job done' push notification to all subscriptions for `user_id`.

    Looks up push_subscriptions in Supabase, then fires a plain HTTP POST to
    each endpoint.  Failures are silently swallowed so the caller is never
    blocked by push errors.
    """
    payload = {
        "title": "VidGrab ✅",
        "body": f"Đã tải xong: {title[:60]}",
        "url": f"/?job={job_id}",
        "icon": "/icons/icon-192.svg",
        "download_url": download_url,
    }
    try:
        sb = get_service_client()
        res = (
            sb.table("push_subscriptions")
            .select("endpoint")
            .eq("user_id", user_id)
            .execute()
        )
        rows = res.data or []
    except Exception as err:
        print(f"[Push] DB lookup failed for user {user_id}: {err}")
        return

    for row in rows:
        endpoint = row.get("endpoint", "")
        if endpoint:
            try:
                await _send_push_notification(endpoint, payload)
            except Exception:
                pass


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/subscribe")
@limiter.limit("10/minute")
async def subscribe(payload: PushSubscribeRequest, request: Request):
    """Upsert a Web Push subscription for the current authenticated user."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        sb = get_service_client()
        sb.table("push_subscriptions").upsert(
            {
                "user_id":    user_id,
                "endpoint":   payload.endpoint,
                "p256dh":     payload.p256dh,
                "auth_key":   payload.auth,
                "user_agent": payload.user_agent,
            },
            on_conflict="user_id,endpoint",
        ).execute()
    except Exception as err:
        print(f"[Push] Subscribe error for user {user_id}: {err}")
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
            .select("endpoint")
            .eq("user_id", user_id)
            .execute()
        )
        rows = res.data or []
    except Exception as err:
        print(f"[Push] Test: DB lookup failed for user {user_id}: {err}")
        raise HTTPException(status_code=500, detail="Database error")

    if not rows:
        return {"success": False, "detail": "No active subscriptions found"}

    test_payload = {
        "title": "VidGrab 🔔",
        "body": payload.message,
        "icon": "/icons/icon-192.svg",
    }

    sent = 0
    for row in rows:
        endpoint = row.get("endpoint", "")
        if endpoint:
            ok = await _send_push_notification(endpoint, test_payload)
            if ok:
                sent += 1

    return {
        "success": True,
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
    """Return the VAPID public key that the browser needs to create a push subscription."""
    return {"public_key": os.getenv("PUSH_VAPID_PUBLIC_KEY", os.getenv("VAPID_PUBLIC_KEY", ""))}
