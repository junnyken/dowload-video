"""
Playlist Manager
================
POST   /api/v1/playlists/save          — save a YouTube or Spotify playlist
GET    /api/v1/playlists               — list user's saved playlists
POST   /api/v1/playlists/{id}/refresh  — refresh playlist metadata (item count)
DELETE /api/v1/playlists/{id}          — delete a saved playlist

Limits: Free = 3 playlists, Pro = 20 playlists
Table: saved_playlists (id, user_id, platform, url, title, item_count, last_refreshed_at, created_at)
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.quotas import get_user_tier, get_tier_permissions, check_feature_permission

router = APIRouter()

# ── Rate limiter (optional — graceful if slowapi not present) ─────────
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    limiter = Limiter(key_func=get_remote_address)
    _has_limiter = True
except ImportError:
    _has_limiter = False


# ── Constants ─────────────────────────────────────────────────────────

_PLAYLIST_LIMITS: Dict[str, int] = {"free": 3, "pro": 20}


# ── Models ────────────────────────────────────────────────────────────

class SavePlaylistRequest(BaseModel):
    url: str
    title: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────

def _detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "open.spotify.com" in u:
        return "spotify"
    if "tiktok.com" in u:
        return "tiktok"
    return "other"


def _quick_playlist_meta(url: str) -> dict:
    try:
        import yt_dlp
        opts = {
            "extract_flat": True,
            "quiet": True,
            "ignoreerrors": True,
            "playlistend": 1,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            if info:
                return {
                    "title": info.get("title") or info.get("uploader") or "",
                    "item_count": info.get("playlist_count") or len(info.get("entries") or []),
                }
    except Exception:
        pass
    return {"title": "", "item_count": 0}


# ── POST /save ────────────────────────────────────────────────────────

@router.post("/save")
async def save_playlist(
    body: SavePlaylistRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Save a playlist URL. Enforces per-tier limits (Free: 3, Pro: 20)."""
    supabase = get_service_client()
    user_id = user["id"]

    # Determine tier + limit
    tier = get_user_tier(user_id)
    limit = _PLAYLIST_LIMITS.get(tier, _PLAYLIST_LIMITS["free"])

    # Count existing playlists
    try:
        count_res = (
            supabase.table("saved_playlists")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        current_count = count_res.count or 0
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check playlist count: {e}")

    if current_count >= limit:
        raise HTTPException(
            status_code=403,
            detail=f"Bạn đã đạt giới hạn danh sách phát (Free: 3, Pro: 20).",
        )

    # Detect platform
    platform = _detect_platform(body.url)

    # Auto-fetch metadata (best-effort, max ~5s)
    meta = _quick_playlist_meta(body.url)
    title = body.title or meta.get("title") or body.url
    item_count = meta.get("item_count", 0)

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "platform": platform,
        "url": body.url,
        "title": title,
        "item_count": item_count,
        "last_refreshed_at": now,
        "created_at": now,
    }

    try:
        # Upsert: on conflict (url + user_id) update title + refreshed_at
        res = supabase.table("saved_playlists").upsert(
            row,
            on_conflict="user_id,url",
        ).execute()
        saved = res.data[0] if res.data else row
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save playlist: {e}")

    return saved


# ── GET / ─────────────────────────────────────────────────────────────

@router.get("")
async def list_playlists(
    user: Dict[str, Any] = Depends(get_required_user),
):
    """List all saved playlists for the current user (newest first)."""
    supabase = get_service_client()
    try:
        res = (
            supabase.table("saved_playlists")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .execute()
        )
        return {"playlists": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlists: {e}")


# ── POST /{id}/refresh ────────────────────────────────────────────────

@router.post("/{playlist_id}/refresh")
async def refresh_playlist(
    playlist_id: str,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Re-fetch item_count + last_refreshed_at for a saved playlist."""
    supabase = get_service_client()

    # Verify ownership
    try:
        res = (
            supabase.table("saved_playlists")
            .select("*")
            .eq("id", playlist_id)
            .eq("user_id", user["id"])
            .single()
            .execute()
        )
        playlist = res.data
    except Exception:
        playlist = None

    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found or not yours")

    # Re-fetch metadata
    meta = _quick_playlist_meta(playlist["url"])
    now = datetime.now(timezone.utc).isoformat()

    updates = {
        "item_count": meta.get("item_count", playlist.get("item_count", 0)),
        "last_refreshed_at": now,
    }
    if meta.get("title"):
        updates["title"] = meta["title"]

    try:
        upd_res = (
            supabase.table("saved_playlists")
            .update(updates)
            .eq("id", playlist_id)
            .eq("user_id", user["id"])
            .execute()
        )
        updated = upd_res.data[0] if upd_res.data else {**playlist, **updates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to refresh playlist: {e}")

    return updated


# ── DELETE /{id} ──────────────────────────────────────────────────────

@router.delete("/{playlist_id}")
async def delete_playlist(
    playlist_id: str,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Delete a saved playlist (verifies ownership)."""
    supabase = get_service_client()
    try:
        res = (
            supabase.table("saved_playlists")
            .delete()
            .eq("id", playlist_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Playlist not found or not yours")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"success": True}
