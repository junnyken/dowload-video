"""
Webhook Callback (Pro only)
============================
POST   /api/v1/user/webhook/register  — register webhook URL (Pro only)
DELETE /api/v1/user/webhook/revoke    — remove webhook
GET    /api/v1/user/webhook/logs      — last 50 delivery logs (from Redis)

Delivery: Celery task with 3 retries, exponential backoff.
Payload signed with HMAC-SHA256.
Table: user_webhooks (user_id PK, webhook_url, secret_hash, is_active, created_at)
Logs: Redis list "webhook:logs:{user_id}" (max 50 entries)
"""

import hashlib
import json
import secrets
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.quotas import check_feature_permission

router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────

class WebhookRegisterRequest(BaseModel):
    webhook_url: str  # must start with https://


# ── Helpers ───────────────────────────────────────────────────────────

def _require_pro(user: Dict[str, Any]) -> None:
    """Raise 403 if user is not Pro (uses api_key feature as the Pro gate)."""
    perm = check_feature_permission(user["id"], "api_key")
    if not perm["allowed"]:
        raise HTTPException(status_code=403, detail=perm.get("message", "Pro only feature."))


# ── POST /register ────────────────────────────────────────────────────

@router.post("/register")
async def register_webhook(
    body: WebhookRegisterRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Register a webhook URL for job completion callbacks (Pro only).
    Returns the signing secret ONCE — it cannot be retrieved again.
    """
    _require_pro(user)

    if not body.webhook_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="webhook_url must start with https://")

    # Generate a URL-safe secret with whs_ prefix
    raw_secret = f"whs_{secrets.token_urlsafe(32)}"

    # Store a SHA-256 hash of the secret (never store plaintext)
    secret_hash = hashlib.sha256(raw_secret.encode()).hexdigest()

    supabase = get_service_client()
    try:
        supabase.table("user_webhooks").upsert(
            {
                "user_id": user["id"],
                "webhook_url": body.webhook_url,
                "secret_hash": secret_hash,
                "is_active": True,
                "created_at": "now()",
            },
            on_conflict="user_id",
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to register webhook: {e}")

    return {
        "webhook_url": body.webhook_url,
        "secret": raw_secret,
        "message": "Lưu ngay — secret chỉ hiển thị một lần và không thể lấy lại.",
        "show_once": True,
    }


# ── DELETE /revoke ────────────────────────────────────────────────────

@router.delete("/revoke")
async def revoke_webhook(user: Dict[str, Any] = Depends(get_required_user)):
    """Deactivate the user's webhook (Pro only)."""
    _require_pro(user)
    supabase = get_service_client()
    try:
        supabase.table("user_webhooks").update({"is_active": False}).eq(
            "user_id", user["id"]
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to revoke webhook: {e}")

    return {"success": True, "message": "Webhook đã bị thu hồi."}


# ── GET /logs ─────────────────────────────────────────────────────────

@router.get("/logs")
async def get_webhook_logs(user: Dict[str, Any] = Depends(get_required_user)):
    """Return last 50 webhook delivery logs from Redis (Pro only)."""
    _require_pro(user)

    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        list_key = f"webhook:logs:{user['id']}"
        raw_entries = rc.lrange(list_key, 0, 49)
        logs = []
        for entry in raw_entries:
            try:
                logs.append(json.loads(entry))
            except Exception:
                pass
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch webhook logs: {e}")


# ── Delivery helper (called from Celery tasks) ────────────────────────

def deliver_webhook_sync(user_id: str, payload: dict) -> bool:
    """
    Called from process_video_task and create_zip_task on job completion.
    Dispatches a Celery task for async delivery with retries.
    """
    try:
        from app.tasks.webhook_tasks import deliver_webhook_task
        deliver_webhook_task.delay(user_id, payload)
        return True
    except Exception as e:
        print(f"[Webhook] Failed to dispatch delivery task: {e}")
        return False
