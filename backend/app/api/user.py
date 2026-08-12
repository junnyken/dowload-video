"""
User API Router
================
/api/v1/user/me                  GET    — profile info
/api/v1/user/preferences         GET    — load preferences
/api/v1/user/preferences         PUT    — save preferences
/api/v1/user/usage               GET    — quota / usage summary
/api/v1/user/history             GET    — personal download history (paginated)
/api/v1/user/history/{id}        DELETE — delete own job record
/api/v1/user/signout             POST   — server-side sign out hint
/api/v1/user/api-key/generate    POST   — Pro: generate API key (shown once)
/api/v1/user/api-key/revoke      DELETE — Pro: revoke active API key
/api/v1/user/api-key/status      GET    — Pro: check if key exists
"""

import csv
import io
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user, get_optional_user
from app.core.database import get_service_client as get_supabase_client
from app.core.quotas import (
    get_user_tier, get_tier_permissions, check_feature_permission, hash_api_key,
    FREE_DAILY_LIMIT, PRO_DAILY_LIMIT,
)

router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────

class PreferencesUpdate(BaseModel):
    default_quality:   Optional[str]  = None
    default_mp3_kbps:  Optional[int]  = None
    remove_watermark:  Optional[bool] = None
    download_subs:     Optional[bool] = None
    bulk_count_preset: Optional[int]  = None
    theme_mode:        Optional[str]  = None


# ── GET /me ───────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(user: Dict[str, Any] = Depends(get_required_user)):
    supabase = get_supabase_client()
    try:
        res = supabase.table("profiles").select("*").eq("id", user["id"]).limit(1).execute()
        profile = res.data[0] if res.data else {}
    except Exception:
        profile = {}

    return {
        "id":           user["id"],
        "email":        user["email"],
        "display_name": profile.get("display_name") or user["email"].split("@")[0],
        "avatar_url":   profile.get("avatar_url"),
        "tier":         profile.get("tier", "free"),
        "created_at":   profile.get("created_at"),
    }


# ── GET /preferences ──────────────────────────────────────────────────

@router.get("/preferences")
async def get_preferences(user: Dict[str, Any] = Depends(get_required_user)):
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("user_preferences")
            .select("*")
            .eq("user_id", user["id"])
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0]
    except Exception:
        pass

    # Return defaults if row not found
    return {
        "user_id":          user["id"],
        "default_quality":  "video",
        "default_mp3_kbps": 320,
        "remove_watermark": True,
        "download_subs":    False,
        "bulk_count_preset": 20,
        "theme_mode":       "dark",
    }


# ── PUT /preferences ──────────────────────────────────────────────────

@router.put("/preferences")
async def update_preferences(
    payload: PreferencesUpdate,
    user: Dict[str, Any] = Depends(get_required_user),
):
    supabase = get_supabase_client()
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Validate allowed values
    if "default_quality" in updates and updates["default_quality"] not in (
        "video", "mp4_4k", "mp3_128", "mp3_320"
    ):
        raise HTTPException(status_code=400, detail="Invalid quality value")

    if "theme_mode" in updates and updates["theme_mode"] not in ("dark", "light"):
        raise HTTPException(status_code=400, detail="Invalid theme_mode")

    updates["updated_at"] = "now()"

    try:
        supabase.table("user_preferences").upsert(
            {"user_id": user["id"], **updates},
            on_conflict="user_id",
        ).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save preferences: {e}")

    return {"success": True}


# ── GET /usage ────────────────────────────────────────────────────────

@router.get("/usage")
async def get_usage(user: Dict[str, Any] = Depends(get_required_user)):
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("user_usage")
            .select("*")
            .eq("user_id", user["id"])
            .limit(1)
            .execute()
        )
        usage = res.data[0] if res.data else {}
    except Exception:
        usage = {}

    try:
        profile_res = (
            supabase.table("profiles")
            .select("tier")
            .eq("id", user["id"])
            .limit(1)
            .execute()
        )
        tier = profile_res.data[0].get("tier", "free") if profile_res.data else "free"
    except Exception:
        tier = "free"

    perms = get_tier_permissions(tier)
    daily_limit = perms["daily_limit"]   # 30 free / 200 pro

    return {
        "plan":                   tier,
        "downloads_today":        usage.get("downloads_today", 0),
        "downloads_this_month":   usage.get("downloads_this_month", 0),
        "bulk_jobs_count":        usage.get("bulk_jobs_count", 0),
        "last_reset_at":          usage.get("last_reset_at"),
        "permissions":            perms,
        "limits": {
            "daily":   daily_limit,
            "monthly": -1 if tier == "pro" else daily_limit * 30,
            "batch":   perms["batch_limit"],
        },
    }


# ── GET /history ──────────────────────────────────────────────────────

@router.get("/history")
async def get_user_history(
    user: Dict[str, Any] = Depends(get_required_user),
    page:      int            = Query(default=1, ge=1),
    per_page:  int            = Query(default=20, ge=1, le=100),
    search:    Optional[str]  = Query(default=None),
    platform:  Optional[str]  = Query(default=None),
    creator:   Optional[str]  = Query(default=None),
    language:  Optional[str]  = Query(default=None),
    has_notes: bool           = Query(default=False),
    sort_by:   str            = Query(default="newest", pattern="^(newest|oldest|most_viewed|longest)$"),
    date_from: Optional[str]  = Query(default=None),
    date_to:   Optional[str]  = Query(default=None),
    tag:       Optional[str]  = Query(default=None),
):
    supabase = get_supabase_client()

    # Enforce tier-based history cap for free users
    tier  = get_user_tier(user["id"])
    perms = get_tier_permissions(tier)
    history_cap = perms["history_limit"]  # None = unlimited (Pro), 20 (Free)

    offset = (page - 1) * per_page

    # Free users: cap total items visible (cap applies to DB results, before post-filters)
    if history_cap is not None:
        max_offset = max(0, history_cap - per_page)
        if offset >= history_cap:
            return {"items": [], "page": page, "per_page": per_page, "has_more": False,
                    "history_limit": history_cap, "sort_by": sort_by, "active_filters": 0}
        offset = min(offset, max_offset)

    try:
        q = (
            supabase.table("download_jobs")
            .select(
                "id, batch_id, original_url, platform, title, thumbnail_url, "
                "status, file_size_mb, downloaded_height, is_audio_only, "
                "selected_quality, source_surface, created_at, error_message, "
                "job_metadata(creator_handle, creator_name, duration_seconds, "
                "view_count, like_count, upload_date, language_detected, hashtags, "
                "categories, description_snippet, thumbnail_url), "
                "clip_notes(id)"
            )
            .eq("user_id", user["id"])
            .range(offset, offset + per_page - 1)
        )

        # DB-side filters
        if search:
            q = q.ilike("title", f"%{search}%")
        if platform:
            q = q.eq("platform", platform.lower())
        if date_from:
            q = q.gte("created_at", date_from)
        if date_to:
            q = q.lte("created_at", date_to + "T23:59:59Z")

        # DB-side sort (newest/oldest only; most_viewed/longest sorted in Python)
        if sort_by == "oldest":
            q = q.order("created_at", desc=False)
        else:
            q = q.order("created_at", desc=True)

        res = q.execute()
        items = res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {e}")

    # Normalise job_metadata: PostgREST may return list or object
    for item in items:
        meta = item.get("job_metadata")
        if isinstance(meta, list):
            meta = meta[0] if meta else {}
        item["job_metadata"] = meta or {}

    # ── Python-side filters ───────────────────────────────────────────
    if creator:
        creator_lower = creator.lower()
        items = [i for i in items if
                 (i.get("job_metadata") or {}).get("creator_name", "").lower().find(creator_lower) >= 0
                 or (i.get("job_metadata") or {}).get("creator_handle", "").lower().find(creator_lower) >= 0]

    if language:
        items = [i for i in items if
                 (i.get("job_metadata") or {}).get("language_detected", "").lower() == language.lower()]

    if tag:
        tag_lower = tag.lower()
        items = [i for i in items if
                 tag_lower in [h.lower() for h in ((i.get("job_metadata") or {}).get("hashtags") or [])]]

    if has_notes:
        items = [i for i in items if len(i.get("clip_notes") or []) > 0]

    # ── Python-side sort for view/duration ───────────────────────────
    if sort_by == "most_viewed":
        items.sort(key=lambda i: (i.get("job_metadata") or {}).get("view_count") or 0, reverse=True)
    elif sort_by == "longest":
        items.sort(key=lambda i: (i.get("job_metadata") or {}).get("duration_seconds") or 0, reverse=True)

    # ── Computed fields ───────────────────────────────────────────────
    for item in items:
        item["notes_count"] = len(item.get("clip_notes") or [])

    # Count active filters for the response
    active_filters = sum([
        bool(platform), bool(creator), bool(language),
        has_notes, bool(date_from), bool(date_to), bool(tag), bool(search),
    ])

    return {
        "items":          items,
        "page":           page,
        "per_page":       per_page,
        "has_more":       len(items) == per_page,
        "history_limit":  history_cap,
        "sort_by":        sort_by,
        "active_filters": active_filters,
    }


# ── DELETE /history/{job_id} ──────────────────────────────────────────

@router.delete("/history/{job_id}")
async def delete_history_item(
    job_id: str,
    user: Dict[str, Any] = Depends(get_required_user),
):
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("download_jobs")
            .delete()
            .eq("id", job_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Job not found or not yours")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True}


# ── GET /history/export ───────────────────────────────────────────────

@router.get("/history/export")
async def export_history(
    user: Dict[str, Any] = Depends(get_required_user),
    format: str     = Query(default="csv", pattern="^(csv|json)$"),
    range_days: int = Query(default=30, ge=1, le=365),
):
    """
    Export download history with metadata as CSV or JSON.
    Pro users get up to 365 days, free users up to 30 days.
    """
    import json

    supabase = get_supabase_client()
    tier  = get_user_tier(user["id"])
    perms = get_tier_permissions(tier)

    # Cap range_days for free users
    max_days = 365 if perms.get("history_limit") is None else 30
    range_days = min(range_days, max_days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=range_days)).isoformat()

    try:
        res = (
            supabase.table("download_jobs")
            .select(
                "id, title, platform, original_url, file_size_mb, downloaded_height, created_at, "
                "job_metadata(creator_name, duration_seconds, view_count, upload_date, language_detected, hashtags), "
                "clip_notes(note_text, timestamp_seconds, created_at)"
            )
            .eq("user_id", user["id"])
            .eq("status", "success")
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .limit(500)
            .execute()
        )
        items = res.data or []
    except Exception as e:
        raise HTTPException(500, detail=f"Export failed: {e}")

    def _meta(item):
        m = item.get("job_metadata")
        if isinstance(m, list):
            m = m[0] if m else {}
        return m or {}

    def _notes_text(item):
        notes = item.get("clip_notes") or []
        return " | ".join(f"[{n.get('timestamp_seconds', '')}s] {n['note_text']}" for n in notes)

    if format == "json":
        output = []
        for item in items:
            m = _meta(item)
            output.append({
                "id":               item["id"],
                "title":            item.get("title"),
                "platform":         item.get("platform"),
                "url":              item.get("original_url"),
                "creator":          m.get("creator_name"),
                "duration_seconds": m.get("duration_seconds"),
                "view_count":       m.get("view_count"),
                "upload_date":      m.get("upload_date"),
                "language":         m.get("language_detected"),
                "hashtags":         m.get("hashtags") or [],
                "file_size_mb":     item.get("file_size_mb"),
                "quality":          item.get("downloaded_height"),
                "downloaded_at":    item.get("created_at"),
                "notes":            _notes_text(item),
            })
        content = json.dumps(output, ensure_ascii=False, indent=2)
        return StreamingResponse(
            iter([content]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=vidgrab-history.json"},
        )
    else:
        # CSV
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "title", "platform", "creator", "duration_s", "upload_date",
            "view_count", "language", "hashtags", "file_size_mb",
            "quality_height", "downloaded_at", "notes", "url"
        ])
        for item in items:
            m = _meta(item)
            writer.writerow([
                item.get("title", ""),
                item.get("platform", ""),
                m.get("creator_name", ""),
                m.get("duration_seconds", ""),
                m.get("upload_date", ""),
                m.get("view_count", ""),
                m.get("language_detected", ""),
                "|".join(m.get("hashtags") or []),
                item.get("file_size_mb", ""),
                item.get("downloaded_height", ""),
                item.get("created_at", ""),
                _notes_text(item),
                item.get("original_url", ""),
            ])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=vidgrab-history.csv"},
        )


# ── POST /signout ─────────────────────────────────────────────────────

@router.post("/signout")
async def signout(_user: Dict[str, Any] = Depends(get_optional_user)):
    # Supabase stateless JWT — actual sign-out happens on client
    # This endpoint exists so extension/web can call it for cleanup
    return {"success": True}


# ══════════════════════════════════════════════════════════════════════
# API Key Self-Service (Pro only)
# ══════════════════════════════════════════════════════════════════════

def _require_pro(user: Dict[str, Any]) -> str:
    """Raise 403 if user is not Pro. Returns tier string."""
    perm = check_feature_permission(user["id"], "api_key")
    if not perm["allowed"]:
        raise HTTPException(status_code=403, detail=perm["message"])
    return perm.get("tier", "pro")


# ── GET /api-key/status ───────────────────────────────────────────────

@router.get("/api-key/status")
async def api_key_status(user: Dict[str, Any] = Depends(get_required_user)):
    """Return whether a Pro user currently has an active API key (no plaintext exposed)."""
    _require_pro(user)
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("user_api_keys")
            .select("created_at, last_used_at")
            .eq("user_id", user["id"])
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        row = res.data[0] if res.data else None
    except Exception:
        row = None

    return {
        "has_key":      bool(row),
        "created_at":   row["created_at"] if row else None,
        "last_used_at": row["last_used_at"] if row else None,
    }


# ── POST /api-key/generate ────────────────────────────────────────────

@router.post("/api-key/generate")
async def api_key_generate(user: Dict[str, Any] = Depends(get_required_user)):
    """
    Generate a new API key for a Pro user.
    The plaintext key is returned ONCE — it is never stored and cannot be retrieved again.
    Any previously active key for this user is revoked.
    """
    _require_pro(user)
    supabase = get_supabase_client()

    # Revoke any existing key
    try:
        supabase.table("user_api_keys").update({"is_active": False}).eq("user_id", user["id"]).execute()
    except Exception:
        pass

    raw_key  = f"vg_{secrets.token_urlsafe(32)}"
    key_hash = hash_api_key(raw_key)

    try:
        supabase.table("user_api_keys").upsert({
            "user_id":      user["id"],
            "key_hash":     key_hash,
            "is_active":    True,
            "created_at":   "now()",
            "last_used_at": None,
        }, on_conflict="user_id").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to store API key: {e}")

    return {
        "api_key":  raw_key,
        "message":  "Lưu ngay — key chỉ hiển thị một lần và không thể lấy lại.",
        "show_once": True,
    }


# ── DELETE /api-key/revoke ────────────────────────────────────────────

@router.delete("/api-key/revoke")
async def api_key_revoke(user: Dict[str, Any] = Depends(get_required_user)):
    """Revoke the active API key for a Pro user."""
    _require_pro(user)
    supabase = get_supabase_client()
    try:
        supabase.table("user_api_keys").update({"is_active": False}).eq("user_id", user["id"]).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to revoke API key: {e}")

    return {"success": True, "message": "API key đã bị thu hồi."}


# ── GET /analytics ────────────────────────────────────────────────────

@router.get("/analytics")
async def get_user_analytics(
    user: Dict[str, Any] = Depends(get_required_user),
    range: int = Query(default=30, ge=7, le=90),
):
    """
    Per-user download analytics (Pro only).
    Returns daily counts, platform breakdown, MB per day, stat cards.
    """
    perm = check_feature_permission(user["id"], "api_key")  # api_key = Pro gate
    if not perm["allowed"]:
        raise HTTPException(status_code=403, detail="Analytics is a Pro feature.")

    supabase = get_supabase_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=range)).isoformat()

    try:
        res = (
            supabase.table("download_jobs")
            .select("created_at, status, file_size_mb, selected_quality, original_url, title")
            .eq("user_id", user["id"])
            .eq("status", "success")
            .gte("created_at", cutoff)
            .order("created_at", desc=False)
            .execute()
        )
        jobs = res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch analytics: {e}")

    # ── Aggregate in Python ──────────────────────────────────────────

    def _platform(url: str) -> str:
        u = (url or "").lower()
        for domain, name in [
            ("youtube.com", "YouTube"), ("youtu.be", "YouTube"),
            ("tiktok.com", "TikTok"), ("douyin.com", "Douyin"),
            ("instagram.com", "Instagram"), ("facebook.com", "Facebook"),
            ("threads.com", "Threads"), ("threads.net", "Threads"),
            ("twitter.com", "Twitter"), ("x.com", "Twitter"),
            ("reddit.com", "Reddit"), ("pinterest.com", "Pinterest"),
            ("spotify.com", "Spotify"),
        ]:
            if domain in u:
                return name
        return "Other"

    daily: dict = defaultdict(lambda: {"downloads": 0, "mb": 0.0})
    platforms: dict = defaultdict(int)
    total_mb = 0.0

    for job in jobs:
        day = (job.get("created_at") or "")[:10]  # YYYY-MM-DD
        if not day:
            continue
        daily[day]["downloads"] += 1
        mb = float(job.get("file_size_mb") or 0)
        daily[day]["mb"] += mb
        total_mb += mb
        plat = _platform(job.get("original_url") or "")
        platforms[plat] += 1

    # Fill missing days with zeros
    today = date.today()
    all_days = []
    for i in range(range, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        all_days.append({
            "date": d,
            "downloads": daily[d]["downloads"],
            "mb": round(daily[d]["mb"], 2),
        })

    most_used_platform = max(platforms, key=platforms.get) if platforms else "—"
    avg_per_day = round(len(jobs) / range, 1) if range > 0 else 0

    return {
        "range_days": range,
        "total_downloads": len(jobs),
        "total_mb": round(total_mb, 2),
        "most_used_platform": most_used_platform,
        "avg_per_day": avg_per_day,
        "daily": all_days,
        "platforms": dict(platforms),
    }
