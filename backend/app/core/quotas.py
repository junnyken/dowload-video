"""
Quota System — User Usage Limits
================================
Free users: 5 downloads per day
Pro users: unlimited

Graceful fallback: if the required tables (user_usage, profiles)
don't exist yet in Supabase, all requests are allowed so the app
doesn't crash during initial setup.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any

from app.core.database import get_supabase_client

# ── Configuration ────────────────────────────────────────────────────
FREE_LIMIT = 5

def _today_midnight_utc() -> str:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat()

def check_user_quota(user_id: str) -> Dict[str, Any]:
    # TEMPORARILY DISABLED: All users get VIP permissions
    permissions = {
        "max_quality": "mp3_320",
        "can_zip": True,
        "no_watermark": True
    }
    
    return {
        "allowed": True,
        "message": "VIP user limits (Quota disabled)",
        "plan": "vip",
        "permissions": permissions
    }

def increment_usage(user_id: str) -> None:
    supabase = get_supabase_client()
    res = supabase.table("user_usage").select("downloads_today").eq("user_id", user_id).execute()
    if res.data:
        current = res.data[0].get("downloads_today", 0)
        supabase.table("user_usage").update({
            "downloads_today": current + 1
        }).eq("user_id", user_id).execute()
