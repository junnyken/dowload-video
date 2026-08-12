"""
User Presets API — VidGrab Phase 21
=====================================
CRUD for user-saved download presets.

Routes:
  GET    /api/v1/presets              — list user presets (+ system presets)
  POST   /api/v1/presets              — create preset
  PUT    /api/v1/presets/{id}         — update preset
  DELETE /api/v1/presets/{id}         — delete preset
  POST   /api/v1/presets/{id}/default — set as default for platform
  POST   /api/v1/presets/from-job     — create preset from job's settings
  PUT    /api/v1/platform-prefs/{platform} — update learned pref for platform
  GET    /api/v1/platform-prefs        — get all platform prefs
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Presets"])

# ── System presets (immutable, injected into every user's list) ───────────────

SYSTEM_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "sys_tiktok_clean",
        "user_id": "system",
        "name": "TikTok sạch watermark",
        "platform": "tiktok",
        "settings": {"format": "mp4", "quality": "video", "remove_watermark": True},
        "is_default": False,
        "is_system": True,
        "sort_order": 0,
    },
    {
        "id": "sys_spotify_mp3",
        "user_id": "system",
        "name": "Spotify → MP3",
        "platform": "spotify",
        "settings": {"quality": "mp3_320"},
        "is_default": False,
        "is_system": True,
        "sort_order": 1,
    },
    {
        "id": "sys_hd_video",
        "user_id": "system",
        "name": "Video HD 1080p",
        "platform": None,
        "settings": {"quality": "1080", "format": "mp4"},
        "is_default": False,
        "is_system": True,
        "sort_order": 2,
    },
    {
        "id": "sys_mp3_fast",
        "user_id": "system",
        "name": "Trích âm thanh MP3",
        "platform": None,
        "settings": {"quality": "mp3_128"},
        "is_default": False,
        "is_system": True,
        "sort_order": 3,
    },
    {
        "id": "sys_thumbnail",
        "user_id": "system",
        "name": "Chỉ lấy thumbnail",
        "platform": None,
        "settings": {"quality": "thumbnail_only"},
        "is_default": False,
        "is_system": True,
        "sort_order": 4,
    },
]


# ── Models ────────────────────────────────────────────────────────────────────

class PresetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    platform: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False
    sort_order: int = 0


class PlatformPrefIn(BaseModel):
    quality: Optional[str] = None
    format: Optional[str] = None
    remove_watermark: Optional[bool] = None
    cloud_save: Optional[bool] = None
    last_preset_id: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _auth(request: Request) -> Dict[str, Any]:
    return await get_required_user(request)


def _uid(user: Dict[str, Any]) -> str:
    uid = user.get("id") or user.get("sub", "")
    if not uid:
        raise HTTPException(status_code=401, detail="Missing user id")
    return uid


# ── Preset endpoints ──────────────────────────────────────────────────────────

@router.get("/presets")
async def list_presets(request: Request, platform: Optional[str] = None):
    """
    Return system presets + user presets.
    Optional ?platform=tiktok filters to presets for that platform + universal presets.
    """
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    try:
        q = db.table("user_presets").select("*").eq("user_id", user_id).order("sort_order")
        res = q.execute()
        user_presets = res.data or []
    except Exception as exc:
        logger.warning("list_presets DB error: %s", exc)
        user_presets = []

    # Filter system presets
    sys_presets = [
        p for p in SYSTEM_PRESETS
        if platform is None or p["platform"] in (platform, None)
    ]

    # Filter user presets
    if platform:
        user_presets = [p for p in user_presets if p.get("platform") in (platform, None)]

    return {
        "system": sys_presets,
        "user": user_presets,
    }


@router.post("/presets", status_code=status.HTTP_201_CREATED)
async def create_preset(request: Request, body: PresetIn):
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    # If setting as default, unset any existing default for this platform
    if body.is_default and body.platform:
        try:
            db.table("user_presets").update({"is_default": False}).eq("user_id", user_id).eq("platform", body.platform).eq("is_default", True).execute()
        except Exception:
            pass

    try:
        res = db.table("user_presets").insert({
            "user_id": user_id,
            "name": body.name,
            "platform": body.platform,
            "settings": body.settings,
            "is_default": body.is_default,
            "is_system": False,
            "sort_order": body.sort_order,
        }).execute()
        return res.data[0] if res.data else {}
    except Exception as exc:
        logger.error("create_preset error: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create preset")


@router.put("/presets/{preset_id}")
async def update_preset(request: Request, preset_id: str, body: PresetIn):
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    # Verify ownership
    try:
        chk = db.table("user_presets").select("id,is_system").eq("id", preset_id).eq("user_id", user_id).limit(1).execute()
        if not chk.data:
            raise HTTPException(status_code=404, detail="Preset not found")
        if chk.data[0].get("is_system"):
            raise HTTPException(status_code=403, detail="System presets cannot be modified")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if body.is_default and body.platform:
        try:
            db.table("user_presets").update({"is_default": False}).eq("user_id", user_id).eq("platform", body.platform).eq("is_default", True).neq("id", preset_id).execute()
        except Exception:
            pass

    try:
        res = db.table("user_presets").update({
            "name": body.name,
            "platform": body.platform,
            "settings": body.settings,
            "is_default": body.is_default,
            "sort_order": body.sort_order,
        }).eq("id", preset_id).eq("user_id", user_id).execute()
        return res.data[0] if res.data else {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preset(request: Request, preset_id: str):
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    try:
        chk = db.table("user_presets").select("id,is_system").eq("id", preset_id).eq("user_id", user_id).limit(1).execute()
        if not chk.data:
            raise HTTPException(status_code=404, detail="Preset not found")
        if chk.data[0].get("is_system"):
            raise HTTPException(status_code=403, detail="System presets cannot be deleted")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    db.table("user_presets").delete().eq("id", preset_id).eq("user_id", user_id).execute()


@router.post("/presets/{preset_id}/default")
async def set_default_preset(request: Request, preset_id: str):
    """Set a preset as the default for its platform."""
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    try:
        chk = db.table("user_presets").select("*").eq("id", preset_id).eq("user_id", user_id).limit(1).execute()
        if not chk.data:
            raise HTTPException(status_code=404, detail="Preset not found")
        preset = chk.data[0]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    platform = preset.get("platform")
    if platform:
        db.table("user_presets").update({"is_default": False}).eq("user_id", user_id).eq("platform", platform).execute()

    db.table("user_presets").update({"is_default": True}).eq("id", preset_id).execute()
    return {"success": True, "preset_id": preset_id, "platform": platform}


@router.post("/presets/from-job", status_code=status.HTTP_201_CREATED)
async def create_preset_from_job(request: Request, job_id: str, name: str):
    """Create a preset from an existing download job's settings."""
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    try:
        res = db.table("download_jobs").select("download_settings,platform").eq("id", job_id).eq("user_id", user_id).limit(1).execute()
        job = res.data[0] if res.data else None
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    settings = job.get("download_settings") or {}
    platform = job.get("platform")

    try:
        res = db.table("user_presets").insert({
            "user_id": user_id,
            "name": name,
            "platform": platform,
            "settings": settings,
            "is_default": False,
            "is_system": False,
        }).execute()
        return res.data[0] if res.data else {}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Platform preferences (learned behavior) ───────────────────────────────────

@router.get("/platform-prefs")
async def get_platform_prefs(request: Request):
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    try:
        res = db.table("user_platform_prefs").select("platform,prefs,last_used_at").eq("user_id", user_id).execute()
        return {row["platform"]: {**row["prefs"], "last_used_at": row["last_used_at"]} for row in (res.data or [])}
    except Exception as exc:
        logger.warning("get_platform_prefs error: %s", exc)
        return {}


@router.put("/platform-prefs/{platform}")
async def update_platform_pref(request: Request, platform: str, body: PlatformPrefIn):
    """Upsert learned preference for a specific platform."""
    user = await _auth(request)
    user_id = _uid(user)
    db = get_service_client()

    prefs = {k: v for k, v in body.model_dump().items() if v is not None}
    if not prefs:
        return {"success": True}

    try:
        db.table("user_platform_prefs").upsert({
            "user_id": user_id,
            "platform": platform,
            "prefs": prefs,
            "last_used_at": "now()",
            "updated_at": "now()",
        }, on_conflict="user_id,platform").execute()
        return {"success": True, "platform": platform, "prefs": prefs}
    except Exception as exc:
        logger.error("update_platform_pref error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
