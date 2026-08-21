"""
Quota & Tier System — VidGrab
==============================
Tier limits (env-tunable):
  Free:      10 downloads/day, batch ≤ 5, YouTube audio-only, history 7 days
  Pro:      100 downloads/day, batch ≤ 100, YouTube video+audio, history 90 days
  Anonymous:  5 downloads/day (IP-based, Redis, auto-TTL)

Grace period: billing_status='canceling' + subscription_expiry > now → still Pro

Error codes (returned in 402/403/429 responses):
  quota_exceeded_daily    — daily download cap hit
  tier_limit_quality      — quality requires Pro (free capped at 1080p)
  tier_limit_batch        — batch size exceeds tier limit
  tier_required_feature   — feature not available on free tier
  youtube_pro_only        — YouTube video download requires Pro
  spotify_artist_full     — Spotify artist full mode (albums/singles/all_tracks) requires Pro
  bulk_zip_pro            — ZIP download requires Pro
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from app.core.database import get_supabase_client

# ── Tier limits (env-tunable) ─────────────────────────────────────────
FREE_DAILY_LIMIT  = int(os.getenv("FREE_DAILY_LIMIT",   "10"))
PRO_DAILY_LIMIT   = int(os.getenv("PRO_DAILY_LIMIT",   "100"))
FREE_BATCH_LIMIT  = int(os.getenv("FREE_BATCH_LIMIT",    "5"))
PRO_BATCH_LIMIT   = int(os.getenv("PRO_BATCH_LIMIT",   "100"))
ANON_DAILY_LIMIT  = int(os.getenv("ANON_DAILY_LIMIT",    "5"))

# Quality strings that explicitly request 4K
PRO_ONLY_QUALITY_STRINGS = {"mp4_4k", "4k", "2160"}

TIER_PERMISSIONS: Dict[str, Dict[str, Any]] = {
    "free": {
        "daily_limit":          FREE_DAILY_LIMIT,
        "batch_limit":          FREE_BATCH_LIMIT,
        "max_height":           1080,
        "youtube_download":     "video",   # video allowed, capped 1080p + 5/day
        "bulk_zip":             False,
        "spotify_artist_full":  False,     # only top_tracks
        "cloud_save":           False,
        "chapters":             False,
        "logo_inpaint":         False,
        "history_limit":        20,
        "history_days":         7,
        "api_key":              False,
    },
    "pro": {
        "daily_limit":          PRO_DAILY_LIMIT,
        "batch_limit":          PRO_BATCH_LIMIT,
        "max_height":           2160,
        "youtube_download":     "video",   # full video allowed
        "bulk_zip":             True,
        "spotify_artist_full":  True,
        "cloud_save":           True,
        "chapters":             True,
        "logo_inpaint":         True,
        "history_limit":        None,      # unlimited
        "history_days":         90,
        "api_key":              True,
    },
    "team": {
        "daily_limit":          int(os.getenv("TEAM_DAILY_LIMIT", "500")),
        "batch_limit":          200,
        "max_height":           2160,
        "youtube_download":     "video",
        "bulk_zip":             True,
        "spotify_artist_full":  True,
        "cloud_save":           True,
        "chapters":             True,
        "logo_inpaint":         True,
        "history_limit":        None,
        "history_days":         90,
        "api_key":              True,
    },
    "enterprise": {
        "daily_limit":          -1,        # unlimited
        "batch_limit":          -1,
        "max_height":           2160,
        "youtube_download":     "video",
        "bulk_zip":             True,
        "spotify_artist_full":  True,
        "cloud_save":           True,
        "chapters":             True,
        "logo_inpaint":         True,
        "history_limit":        None,
        "history_days":         -1,
        "api_key":              True,
    },
}

# ── Error codes ───────────────────────────────────────────────────────
ERR_QUOTA_DAILY    = "quota_exceeded_daily"
ERR_QUALITY        = "tier_limit_quality"
ERR_BATCH          = "tier_limit_batch"
ERR_FEATURE        = "tier_required_feature"
ERR_YOUTUBE        = "youtube_pro_only"
ERR_SPOTIFY_FULL   = "spotify_artist_full"
ERR_BULK_ZIP       = "bulk_zip_pro"


# ── Internal helpers ──────────────────────────────────────────────────

def _today_utc_str() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _get_profile(user_id: str) -> Dict[str, Any]:
    """Fetch tier, billing_status, subscription_expiry in one query."""
    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("profiles")
            # grace_period_ends_at is part of the shared tier rule — omitting it
            # silently turned a past_due account's grace period into a downgrade.
            .select("tier, billing_status, subscription_expiry, grace_period_ends_at")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return res.data or {}
    except Exception:
        return {}


def _effective_tier(profile: Dict[str, Any]) -> str:
    """
    Delegates to the single shared rule in entitlements.resolve_effective_tier.

    This used to implement its own version and disagreed with entitlements.py
    on two points that both mattered:

      - a paid tier with billing_status='none' was honoured here but treated as
        free there, so the account menu said ENTERPRISE while every
        require_feature gate refused
      - during a grace period it returned a hardcoded "pro", quietly demoting a
        Team or Enterprise account for the duration of a payment problem
    """
    from app.core.entitlements import resolve_effective_tier  # noqa: PLC0415
    return resolve_effective_tier(profile)


def _get_tier(user_id: str) -> str:
    """Return effective tier ('free' or 'pro') for authenticated user."""
    return _effective_tier(_get_profile(user_id))


def _get_usage(user_id: str) -> Dict[str, Any]:
    """Fetch current usage row for user (lazy reset on first call of the day)."""
    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("user_usage")
            .select("downloads_today, last_reset_at")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        row = res.data or {}

        # Lazy daily reset: if last_reset_at < today UTC midnight, zero out counter
        last_reset = row.get("last_reset_at")
        today_midnight = _today_utc_str()
        if last_reset and last_reset < today_midnight:
            try:
                supabase.table("user_usage").update({
                    "downloads_today": 0,
                    "last_reset_at":   today_midnight,
                }).eq("user_id", user_id).execute()
                row["downloads_today"] = 0
            except Exception:
                pass

        return row
    except Exception:
        return {}


# ── Anonymous quota (Redis, IP-based) ────────────────────────────────

def _anon_quota_key(ip: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"vidgrab:quota:anon:{ip}:{date}"


def check_anon_quota(ip: str) -> Dict[str, Any]:
    """Check daily quota for an anonymous user identified by IP."""
    from app.core.redis_client import get_redis
    r = get_redis()
    key = _anon_quota_key(ip)
    try:
        used = int(r.get(key) or 0)
    except Exception:
        used = 0
    limit = ANON_DAILY_LIMIT

    if used >= limit:
        try:
            from app.core.metrics import track_quota_denial
            track_quota_denial(ERR_QUOTA_DAILY, platform="anon")
        except Exception:
            pass
        return {
            "allowed":        False,
            "error_code":     ERR_QUOTA_DAILY,
            "downloads_today": used,
            "daily_limit":    limit,
            "message":        (
                f"Khách chỉ tải được {limit} lượt/ngày. "
                "Đăng nhập để tải nhiều hơn."
            ),
        }
    return {"allowed": True, "downloads_today": used, "daily_limit": limit}


def increment_anon_usage(ip: str) -> None:
    """Increment anon daily counter with auto-TTL until next UTC midnight."""
    from app.core.redis_client import get_redis
    r = get_redis()
    key = _anon_quota_key(ip)
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ttl = max(int((midnight - now).total_seconds()) + 60, 60)
    try:
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception as e:
        print(f"[Quota] increment_anon_usage failed for {ip}: {e}")


# ── Public API ────────────────────────────────────────────────────────

def get_user_tier(user_id: str) -> str:
    """Return effective 'free' or 'pro' for the given user_id."""
    return _get_tier(user_id)


def get_tier_permissions(tier: str) -> Dict[str, Any]:
    return TIER_PERMISSIONS.get(tier, TIER_PERMISSIONS["free"])


def _enforced_daily_limit(tier: str, configured: int) -> int:
    """
    The daily limit actually enforced for a tier. -1 means unlimited.

    check_user_quota used to short-circuit on `tier in ("pro","team","enterprise")`
    and return allowed=True without looking at the number at all. Only
    enterprise is configured as unlimited (-1); pro and team have real limits
    (PRO_DAILY_LIMIT, TEAM_DAILY_LIMIT) that were shown in the UI as "x/200"
    and never enforced anywhere. Every one of those downloads can pull bytes
    through the paid residential proxy, so the ceiling that was supposed to
    bound that spend did nothing.

    The guard below exists because enforcing the configured numbers as-is would
    be worse than not enforcing them: this deployment sets FREE_DAILY_LIMIT to
    1000 and never sets PRO_DAILY_LIMIT, which defaults to 100 — so a paying
    Pro account would get one tenth of what a free account gets. A paid tier is
    never meant to allow less than free, so it never does. Fix the environment
    to raise the real ceiling; this only stops a misconfiguration from
    punishing the people who paid.
    """
    if configured == -1:
        return -1
    if tier == "free":
        return configured
    free_limit = TIER_PERMISSIONS["free"]["daily_limit"]
    if free_limit == -1:
        return -1
    return max(configured, free_limit)


def check_user_quota(user_id: str) -> Dict[str, Any]:
    """
    Check daily quota for an authenticated user.
    Respects grace period (canceling/past_due with future expiry → Pro).
    Returns: {allowed, plan, downloads_today, daily_limit, permissions}
    """
    profile = _get_profile(user_id)
    tier    = _effective_tier(profile)
    perms   = get_tier_permissions(tier)
    limit   = _enforced_daily_limit(tier, perms["daily_limit"])
    usage   = _get_usage(user_id)
    used    = usage.get("downloads_today", 0)

    if limit == -1:
        return {
            "allowed":         True,
            "plan":            tier,
            "downloads_today": used,
            "daily_limit":     limit,
            "permissions":     perms,
        }

    if used >= limit:
        try:
            from app.core.metrics import track_quota_denial
            track_quota_denial(ERR_QUOTA_DAILY, platform="user", user_id=user_id)
        except Exception:
            pass
        return {
            "allowed":         False,
            "error_code":      ERR_QUOTA_DAILY,
            "plan":            tier,
            "downloads_today": used,
            "daily_limit":     limit,
            "message":         (
                f"Đã đạt giới hạn {limit} lượt tải hôm nay. "
                "Nâng cấp Pro để tải không giới hạn hơn."
            ),
            "permissions":     perms,
        }

    return {
        "allowed":         True,
        "plan":            tier,
        "downloads_today": used,
        "daily_limit":     limit,
        "message":         f"{used}/{limit} lượt hôm nay",
        "permissions":     perms,
    }


def check_feature_permission(user_id: str, feature: str) -> Dict[str, Any]:
    """
    Check if user is allowed to use a specific feature.
    feature: 'cloud_save' | 'chapters' | 'logo_inpaint' | 'api_key' |
             'bulk_zip' | 'spotify_artist_full'
    """
    tier  = _get_tier(user_id)
    perms = get_tier_permissions(tier)
    val   = perms.get(feature)
    # Boolean or non-False string counts as allowed
    allowed = bool(val) if not isinstance(val, str) else True

    if allowed:
        return {"allowed": True, "tier": tier}

    feature_labels = {
        "cloud_save":           "Lưu Cloud",
        "chapters":             "Chapter Extractor",
        "logo_inpaint":         "Xoá Logo / Watermark",
        "api_key":              "API Key",
        "bulk_zip":             "ZIP Download",
        "spotify_artist_full":  "Spotify Artist (full catalog)",
    }
    label = feature_labels.get(feature, feature)

    error_map = {
        "bulk_zip":            ERR_BULK_ZIP,
        "spotify_artist_full": ERR_SPOTIFY_FULL,
    }
    error_code = error_map.get(feature, ERR_FEATURE)

    return {
        "allowed":    False,
        "error_code": error_code,
        "feature":    feature,
        "tier":       tier,
        "message":    f"Tính năng '{label}' chỉ dành cho Pro. Nâng cấp để sử dụng.",
        "required_tier": "pro",
        "upgrade_url":   "/upgrade",
    }


def check_quality_permission(user_id: str, quality_str: str, height: int = 0) -> Dict[str, Any]:
    """
    Return whether the requested quality is allowed for this user's tier.
    Free users are capped at 1080p.
    """
    tier  = _get_tier(user_id)
    perms = get_tier_permissions(tier)
    max_h = perms["max_height"]

    is_4k_str = quality_str.lower() in PRO_ONLY_QUALITY_STRINGS
    is_4k_h   = height > 1080

    if (is_4k_str or is_4k_h) and max_h < 2160:
        return {
            "allowed":    False,
            "error_code": ERR_QUALITY,
            "tier":       tier,
            "message":    "Chất lượng 4K chỉ dành cho Pro. Nâng cấp hoặc chọn ≤ 1080p.",
        }

    return {"allowed": True, "tier": tier}


def check_youtube_tier(_user_id: Optional[str], _quality_str: str) -> Dict[str, Any]:
    """
    YouTube-specific tier check.
    All registered users (free + pro) may download video, capped at 1080p for free.
    Anonymous users are audio-only (controlled by YT_ANON_AUDIO_ONLY env flag).
    Per-user daily YouTube count is handled separately by yt_quota.reserve().
    Returns: {allowed: True} always for registered users.
    """
    # Anonymous users: audio-only restriction is handled by YT_ANON_AUDIO_ONLY
    # valve in routes.py — not here.
    return {"allowed": True}


def check_batch_limit(user_id: Optional[str], count: int) -> Dict[str, Any]:
    """
    Check whether the batch size is within the user's tier limit.
    user_id: None means guest (free tier).
    """
    tier  = _get_tier(user_id) if user_id else "free"
    perms = get_tier_permissions(tier)
    limit = perms["batch_limit"]

    if count > limit:
        return {
            "allowed":    False,
            "error_code": ERR_BATCH,
            "tier":       tier,
            "limit":      limit,
            "requested":  count,
            "message":    (
                f"Batch tối đa {limit} video cho gói {tier.title()}. "
                f"Bạn đang yêu cầu {count}."
            ),
        }

    return {"allowed": True, "tier": tier, "limit": limit}


def increment_usage(user_id: str) -> None:
    """Increment daily + monthly download counters for an authenticated user."""
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("user_usage")
            .select("downloads_today, downloads_this_month")
            .eq("user_id", user_id)
            .execute()
        )
        if res.data:
            row = res.data[0]
            supabase.table("user_usage").update({
                "downloads_today":      (row.get("downloads_today", 0) or 0) + 1,
                "downloads_this_month": (row.get("downloads_this_month", 0) or 0) + 1,
            }).eq("user_id", user_id).execute()
        else:
            supabase.table("user_usage").insert({
                "user_id":              user_id,
                "downloads_today":      1,
                "downloads_this_month": 1,
                "last_reset_at":        _today_utc_str(),
                "plan":                 "free",
            }).execute()
    except Exception as e:
        print(f"[Quota] increment_usage failed for {user_id}: {e}")


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hash for storing API keys in DB (never store plaintext)."""
    import hashlib
    return hashlib.sha256(raw_key.encode()).hexdigest()
