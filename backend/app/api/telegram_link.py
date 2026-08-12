"""
Telegram Account Linking — Phase 10
=====================================
Endpoints for linking a Telegram user ID to a VidGrab account.

Flow:
  1. Bot calls POST /link-request  → gets a short-lived token (stored in Redis, 15 min TTL)
  2. Bot sends user link: WEB_URL/link-bot?token=TOKEN
  3. User opens web app, sees confirm UI, clicks Confirm
  4. Frontend calls POST /link-confirm  (with user JWT + token query param)
  5. Backend looks up token → saves telegram_links row
  6. Bot calls GET /user-info?telegram_id=XXX to check tier/quota

Bot authentication: X-Bot-Secret header (shared secret: TELEGRAM_BOT_SECRET env var).
"""

import json
import os
import secrets
from typing import Dict, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user, get_optional_user
from app.core.database import get_supabase_client
from app.core.redis_client import get_redis

router = APIRouter()

_BOT_SECRET = os.getenv("TELEGRAM_BOT_SECRET", "")
_LINK_TTL   = 15 * 60   # 15 minutes


def _check_bot_secret(x_bot_secret: str = Header(None, alias="X-Bot-Secret")):
    if not _BOT_SECRET:
        raise HTTPException(status_code=500, detail="Bot secret not configured.")
    if x_bot_secret != _BOT_SECRET:
        raise HTTPException(status_code=403, detail="Invalid bot secret.")


# ── POST /link-request  (called by the bot) ────────────────────────────────

class LinkRequestBody(BaseModel):
    telegram_user_id: int
    telegram_username: str = ""


@router.post("/link-request")
def create_link_request(
    body: LinkRequestBody,
    _: None = Depends(_check_bot_secret),
):
    """
    Bot calls this to get a token the user can use to link their account.
    Returns {token, link_url, expires_in_seconds}.
    """
    token = secrets.token_urlsafe(12)      # ~16 URL-safe chars
    redis_key = f"tg:link:{token}"

    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable.")

    r.set(
        redis_key,
        json.dumps({
            "telegram_user_id": body.telegram_user_id,
            "telegram_username": body.telegram_username,
        }),
        ex=_LINK_TTL,
    )

    web_url = os.getenv("VIDGRAB_WEB_URL", "https://vidgrab.io")
    return {
        "token":             token,
        "link_url":          f"{web_url}/link-bot?token={token}",
        "expires_in_seconds": _LINK_TTL,
    }


# ── POST /link-confirm  (called by the frontend with user JWT) ─────────────

@router.post("/link-confirm")
def confirm_link(
    token: str = Query(...),
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Frontend confirms the link after user authentication.
    Consumes the one-time token and writes a telegram_links row.
    """
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable.")

    redis_key = f"tg:link:{token}"
    raw = r.get(redis_key)
    if not raw:
        raise HTTPException(
            status_code=410,
            detail={"error_code": "link_token_expired", "user_message": "Link đã hết hạn. Vui lòng thử lại."},
        )

    data = json.loads(raw)
    telegram_user_id = data["telegram_user_id"]
    telegram_username = data.get("telegram_username", "")

    # Consume the token (one-time use)
    r.delete(redis_key)

    supabase = get_supabase_client()
    # Upsert: one Telegram account can only link to one VidGrab account
    supabase.table("telegram_links").upsert({
        "telegram_user_id":  telegram_user_id,
        "vidgrab_user_id":   user["id"],
        "telegram_username": telegram_username,
    }, on_conflict="telegram_user_id").execute()

    return {"linked": True, "telegram_user_id": telegram_user_id}


# ── GET /link-status  (called by the frontend) ─────────────────────────────

@router.get("/link-status")
def link_status(user: Dict[str, Any] = Depends(get_optional_user)):
    """Returns whether the current user has a linked Telegram account."""
    if not user:
        return {"linked": False}

    supabase = get_supabase_client()
    res = (
        supabase.table("telegram_links")
        .select("telegram_user_id, telegram_username, linked_at")
        .eq("vidgrab_user_id", user["id"])
        .limit(1)
        .execute()
    )
    if res.data:
        return {"linked": True, **res.data[0]}
    return {"linked": False}


# ── GET /user-info  (called by the bot) ───────────────────────────────────

@router.get("/user-info")
def get_tg_user_info(
    telegram_id: int = Query(...),
    _: None = Depends(_check_bot_secret),
):
    """
    Bot calls this before a download to check tier + quota for a Telegram user.
    Returns {linked, tier, downloads_today, daily_limit, user_id}.
    """
    supabase = get_supabase_client()

    link_res = (
        supabase.table("telegram_links")
        .select("vidgrab_user_id")
        .eq("telegram_user_id", telegram_id)
        .limit(1)
        .execute()
    )

    if not link_res.data:
        return {"linked": False}

    user_id = link_res.data[0]["vidgrab_user_id"]

    # Fetch profile for tier
    try:
        profile_res = (
            supabase.table("profiles")
            .select("tier, billing_status, subscription_expiry")
            .eq("id", user_id)
            .single()
            .execute()
        )
        profile = profile_res.data or {}
    except Exception:
        profile = {}

    # Compute effective tier (same grace-period logic as quotas.py)
    from app.core.quotas import _effective_tier
    tier = _effective_tier(profile)

    # Fetch today's quota
    try:
        usage_res = (
            supabase.table("user_usage")
            .select("downloads_today")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        downloads_today = (usage_res.data or [{}])[0].get("downloads_today", 0)
    except Exception:
        downloads_today = 0

    from app.core.quotas import PRO_DAILY_LIMIT, FREE_DAILY_LIMIT
    daily_limit = PRO_DAILY_LIMIT if tier == "pro" else FREE_DAILY_LIMIT

    return {
        "linked":          True,
        "user_id":         user_id,
        "tier":            tier,
        "downloads_today": downloads_today,
        "daily_limit":     daily_limit,
    }


# ── DELETE /unlink  (called by the frontend) ──────────────────────────────

@router.delete("/unlink")
def unlink_telegram(user: Dict[str, Any] = Depends(get_required_user)):
    """Remove the Telegram link for the current user."""
    supabase = get_supabase_client()
    supabase.table("telegram_links").delete().eq("vidgrab_user_id", user["id"]).execute()
    return {"unlinked": True}
