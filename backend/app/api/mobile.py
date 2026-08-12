"""
Mobile / PWA API
================
Lightweight endpoints optimised for mobile clients and PWA shell.

Routes:
  GET  /api/v1/mobile/recent-jobs           — last N download jobs (no auth, session-based)
  GET  /api/v1/mobile/status                — quick health ping
  POST /api/v1/push-subscriptions           — register Web Push subscription
  DELETE /api/v1/push-subscriptions         — unregister subscription
  GET  /api/v1/notification-preferences     — get user notification settings
  PUT  /api/v1/notification-preferences     — update notification settings
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Mobile"])


# =============================================================================
# Models
# =============================================================================


class RecentJob(BaseModel):
    id: str
    url: Optional[str] = None
    title: Optional[str] = None
    thumbnail: Optional[str] = None
    platform: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None


class MobileStatusResponse(BaseModel):
    online: bool = True
    backend_healthy: bool = True
    server_time: str


class PushSubscriptionIn(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: Optional[str] = None


class PushSubscriptionDelete(BaseModel):
    endpoint: str


class NotificationPreferences(BaseModel):
    job_completed: bool = True
    job_failed: bool = True
    batch_completed: bool = True
    storage_warning: bool = True
    browser_push_enabled: bool = False


class NotificationPreferencesIn(BaseModel):
    job_completed: Optional[bool] = None
    job_failed: Optional[bool] = None
    batch_completed: Optional[bool] = None
    storage_warning: Optional[bool] = None
    browser_push_enabled: Optional[bool] = None


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/mobile/recent-jobs", response_model=List[RecentJob])
async def get_recent_jobs(request: Request, limit: int = 5):
    """
    Return the last N download jobs for the current session.
    No auth required — uses session_id cookie or query param for filtering.
    Designed to be fast and lightweight for mobile polling.
    """
    limit = min(limit, 20)  # cap at 20
    session_id = (
        request.cookies.get("vg_session_id")
        or request.query_params.get("session_id")
    )

    # download_jobs has no session_id column (no migration ever added one), so
    # a per-session filter can't be applied — return nothing rather than
    # leaking every user's recent jobs to an unscoped caller.
    if session_id:
        return []

    try:
        db = get_service_client()
        result = (
            db.table("download_jobs")
            .select("id,original_url,title,thumbnail_url,platform,status,created_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = result.data or []
        return [
            RecentJob(
                id=row["id"],
                url=row.get("original_url"),
                title=row.get("title"),
                thumbnail=row.get("thumbnail_url"),
                platform=row.get("platform"),
                status=row.get("status"),
                created_at=row.get("created_at"),
            )
            for row in rows
        ]
    except Exception as exc:
        logger.warning("recent-jobs query failed: %s", exc)
        return []


@router.get("/mobile/status", response_model=MobileStatusResponse)
async def get_mobile_status():
    """Quick health ping for mobile status bar / connectivity check."""
    return MobileStatusResponse(
        online=True,
        backend_healthy=True,
        server_time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


@router.post("/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def save_push_subscription(request: Request, body: PushSubscriptionIn):
    """Store (upsert) a Web Push subscription for the authenticated user."""
    user = await get_required_user(request)
    user_id = user.get("id") or user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")

    try:
        db = get_service_client()
        db.table("push_subscriptions").upsert(
            {
                "user_id": user_id,
                "endpoint": body.endpoint,
                "p256dh": body.p256dh,
                "auth_key": body.auth,
                "user_agent": body.user_agent,
                "is_active": True,
                "last_used_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
            on_conflict="endpoint",
        ).execute()
    except Exception as exc:
        logger.error("Failed to upsert push subscription: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save subscription")


@router.delete("/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def delete_push_subscription(request: Request, body: PushSubscriptionDelete):
    """Deactivate a Web Push subscription."""
    user = await get_required_user(request)
    user_id = user.get("id") or user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")

    try:
        db = get_service_client()
        db.table("push_subscriptions").update({"is_active": False}).eq(
            "endpoint", body.endpoint
        ).eq("user_id", user_id).execute()
    except Exception as exc:
        logger.error("Failed to delete push subscription: %s", exc)


@router.get("/notification-preferences", response_model=NotificationPreferences)
async def get_notification_preferences(request: Request):
    """Retrieve the authenticated user's notification preferences."""
    user = await get_required_user(request)
    user_id = user.get("id") or user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")

    try:
        db = get_service_client()
        result = (
            db.table("notification_preferences")
            .select("*")
            .eq("user_id", user_id)
            .maybe_single()
            .execute()
        )
        if result.data:
            return NotificationPreferences(**result.data)
    except Exception as exc:
        logger.warning("Failed to load notification preferences: %s", exc)

    return NotificationPreferences()  # defaults


@router.put("/notification-preferences", response_model=NotificationPreferences)
async def update_notification_preferences(
    request: Request, body: NotificationPreferencesIn
):
    """Upsert notification preferences for the authenticated user."""
    user = await get_required_user(request)
    user_id = user.get("id") or user.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")

    updates: Dict[str, Any] = {"user_id": user_id}
    for field, value in body.model_dump(exclude_none=True).items():
        updates[field] = value
    updates["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        db = get_service_client()
        result = (
            db.table("notification_preferences")
            .upsert(updates, on_conflict="user_id")
            .execute()
        )
        if result.data:
            return NotificationPreferences(**result.data[0])
    except Exception as exc:
        logger.error("Failed to update notification preferences: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update preferences")

    return NotificationPreferences(**{k: v for k, v in updates.items() if k != "user_id" and k != "updated_at"})
