"""
Live Stream Recording API — Phase B5
====================================
Endpoints (mounted at `/api/v1/live`):
  - POST /start            — start recording an ongoing live stream
  - POST /{job_id}/stop    — gracefully stop recording and finalize MP4
  - GET  /{job_id}/status  — poll recording status, elapsed time, and download link
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth_middleware import get_optional_user
from app.services.live_recorder import (
    detect_live_platform,
    get_live_recording_status,
    max_duration_for_plan,
    start_live_recording,
    stop_live_recording,
)

router = APIRouter()


class StartLiveRecordRequest(BaseModel):
    url: str
    duration_sec: Optional[int] = None
    live_from_start: bool = False
    plan: Optional[str] = None


def _resolve_user_plan(user: Optional[Dict[str, Any]], body_plan: Optional[str] = None) -> str:
    if user and user.get("tier"):
        return str(user["tier"]).lower()
    if user and user.get("id"):
        try:
            from app.core.quotas import get_user_tier
            return (get_user_tier(user["id"]) or "free").lower()
        except Exception:
            return "free"
    if body_plan:
        return str(body_plan).lower()
    return "free"


@router.post("/start")
async def api_start_live_recording(
    body: StartLiveRecordRequest,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    """Start recording an ongoing live stream with plan-based duration cap."""
    url = (body.url or "").strip()
    if not url or not detect_live_platform(url):
        raise HTTPException(
            status_code=400,
            detail="URL livestream không hợp lệ hoặc nền tảng chưa hỗ trợ (hỗ trợ TikTok Live, YouTube Live, Twitch, Kick, Facebook Live).",
        )

    plan = _resolve_user_plan(user, body.plan)
    user_id = str(user["id"]) if (user and user.get("id")) else None

    try:
        job = await start_live_recording(
            url,
            plan=plan,
            user_id=user_id,
            duration_sec=body.duration_sec,
            live_from_start=body.live_from_start,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Không thể bắt đầu ghi livestream: {exc}")

    return {
        **job.to_dict(),
        "plan_max_duration_sec": max_duration_for_plan(plan),
    }


@router.post("/{job_id}/stop")
async def api_stop_live_recording(job_id: str):
    """Gracefully stop an active live recording and return finalized status."""
    job = await stop_live_recording(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên ghi livestream.")
    return job.to_dict()


@router.get("/{job_id}/status")
async def api_live_recording_status(job_id: str):
    """Get live recording status, elapsed duration, and download URL when completed."""
    job = get_live_recording_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên ghi livestream.")
    return job.to_dict()
