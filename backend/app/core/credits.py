"""
Credit System — User Rate Limiting (Cost Protection)
=====================================================
Each user (identified by IP or user_id) gets a daily credit
allowance.  Every successful download costs 1 credit.
When credits reach 0 the API returns a friendly rejection.

Table: user_credits (see schema migration below)
Columns:
  user_id       TEXT PRIMARY KEY   — IP address or auth UID
  credits       INT  DEFAULT 10
  last_reset_at TIMESTAMPTZ        — midnight of the current day
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from app.core.database import get_supabase_client

# ── Configuration ────────────────────────────────────────────────────
DAILY_CREDIT_LIMIT: int = 10
CREDIT_COST_PER_DOWNLOAD: int = 1


# ── Helpers ──────────────────────────────────────────────────────────

def _today_midnight_utc() -> str:
    """Return today's midnight in UTC as ISO string."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat()


def _should_reset(last_reset_at: Optional[str]) -> bool:
    """Check if credits should be reset (new day)."""
    if not last_reset_at:
        return True
    try:
        last_reset = datetime.fromisoformat(last_reset_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        # Reset if last_reset was before today's midnight
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return last_reset < today_midnight
    except (ValueError, TypeError):
        return True


# ── Public API ───────────────────────────────────────────────────────

def get_or_create_credits(user_id: str) -> Dict[str, Any]:
    """
    Fetch the user's credit record.  If it doesn't exist, create one
    with the default daily limit.  Auto-resets if a new day has begun.

    Returns:
        {
            "user_id": str,
            "credits": int,
            "daily_limit": int,
            "reset_today": bool,    # whether we just reset
        }
    """
    supabase = get_supabase_client()

    # 1. Try to fetch existing record
    response = (
        supabase.table("user_credits")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )

    if response.data:
        record = response.data[0]
        # Check for daily reset
        if _should_reset(record.get("last_reset_at")):
            supabase.table("user_credits").update({
                "credits": DAILY_CREDIT_LIMIT,
                "last_reset_at": _today_midnight_utc(),
            }).eq("user_id", user_id).execute()

            return {
                "user_id": user_id,
                "credits": DAILY_CREDIT_LIMIT,
                "daily_limit": DAILY_CREDIT_LIMIT,
                "reset_today": True,
            }

        return {
            "user_id": user_id,
            "credits": record["credits"],
            "daily_limit": DAILY_CREDIT_LIMIT,
            "reset_today": False,
        }

    # 2. First-time user -> insert with defaults
    supabase.table("user_credits").insert({
        "user_id": user_id,
        "credits": DAILY_CREDIT_LIMIT,
        "last_reset_at": _today_midnight_utc(),
    }).execute()

    return {
        "user_id": user_id,
        "credits": DAILY_CREDIT_LIMIT,
        "daily_limit": DAILY_CREDIT_LIMIT,
        "reset_today": True,
    }


def check_credits(user_id: str) -> Dict[str, Any]:
    """
    Check if the user has enough credits for a download.

    Returns:
        {
            "allowed": bool,
            "credits_remaining": int,
            "message": str,
        }
    """
    info = get_or_create_credits(user_id)
    credits = info["credits"]

    if credits >= CREDIT_COST_PER_DOWNLOAD:
        return {
            "allowed": True,
            "credits_remaining": credits,
            "message": f"You have {credits} credits remaining today.",
        }

    # Calculate time until reset
    now = datetime.now(timezone.utc)
    tomorrow_midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    hours_left = int((tomorrow_midnight - now).total_seconds() / 3600)

    return {
        "allowed": False,
        "credits_remaining": 0,
        "message": (
            f"Daily download limit reached (0/{DAILY_CREDIT_LIMIT}). "
            f"Credits reset in ~{hours_left}h. "
            "Buy more credits or try again tomorrow!"
        ),
    }


def deduct_credit(user_id: str) -> Dict[str, Any]:
    """
    Deduct 1 credit after a successful download.

    Returns:
        {
            "success": bool,
            "credits_remaining": int,
        }
    """
    supabase = get_supabase_client()

    # Ensure credits exist and are current
    info = get_or_create_credits(user_id)
    new_credits = max(0, info["credits"] - CREDIT_COST_PER_DOWNLOAD)

    supabase.table("user_credits").update({
        "credits": new_credits,
    }).eq("user_id", user_id).execute()

    return {
        "success": True,
        "credits_remaining": new_credits,
    }
