"""
Scheduled Download API — Phase 8
===================================
Endpoints:
  GET    /api/v1/schedule          list user's scheduled jobs
  POST   /api/v1/schedule          create a scheduled job
  PATCH  /api/v1/schedule/{id}     edit schedule or payload
  DELETE /api/v1/schedule/{id}     delete
  POST   /api/v1/schedule/{id}/toggle  pause / resume
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.quotas import get_user_tier

router = APIRouter()

_SCHEDULE_LIMITS: Dict[str, int] = {"free": 3, "pro": 20}


# ── Models ────────────────────────────────────────────────────────────

class CreateScheduleRequest(BaseModel):
    job_type: str                       # 'single' | 'channel' | 'keyword'
    input_payload: Dict[str, Any]
    schedule_type: str                  # 'once' | 'daily' | 'weekly'
    next_run_at: str                    # ISO datetime (UTC)
    run_at: Optional[str] = None        # HH:MM:SS for daily/weekly
    run_on_weekday: Optional[int] = None  # 0=Mon … 6=Sun
    auto_collection_id: Optional[str] = None
    duplicate_suppression: str = "off"   # off | url_24h | canonical_id
    min_interval_hours: int = 6


class PatchScheduleRequest(BaseModel):
    input_payload: Optional[Dict[str, Any]] = None
    schedule_type: Optional[str] = None
    next_run_at: Optional[str] = None
    run_at: Optional[str] = None
    run_on_weekday: Optional[int] = None
    auto_collection_id: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────

def _validate_job_type(jt: str):
    if jt not in ("single", "channel", "keyword"):
        raise HTTPException(400, f"job_type phải là 'single', 'channel', hoặc 'keyword'. Nhận: {jt}")


def _validate_schedule_type(st: str):
    if st not in ("once", "daily", "weekly"):
        raise HTTPException(400, f"schedule_type phải là 'once', 'daily', hoặc 'weekly'. Nhận: {st}")


def _validate_frequency(schedule_type: str, min_interval_hours: int):
    """Minimum recurrence interval is 6 hours."""
    if schedule_type == "once":
        return
    min_h = max(6, min_interval_hours)
    if schedule_type == "daily" and min_h > 24:
        raise HTTPException(400, detail={
            "code": "schedule_rate_too_frequent",
            "message": "Lịch hằng ngày: khoảng cách tối thiểu là 6 giờ.",
        })
    if schedule_type == "weekly" and min_h > 168:
        raise HTTPException(400, detail={
            "code": "schedule_rate_too_frequent",
            "message": "Lịch hằng tuần: khoảng cách tối thiểu là 6 giờ.",
        })


def _check_limit(supabase, user_id: str, tier: str):
    limit = _SCHEDULE_LIMITS.get(tier, 3)
    count_res = (
        supabase.table("scheduled_jobs")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    count = count_res.count or 0
    if count >= limit:
        raise HTTPException(403, detail={
            "code": "schedule_limit",
            "message": f"{'Free' if tier == 'free' else 'Pro'} plan giới hạn {limit} lịch tải. "
                       + ("Nâng cấp Pro để tạo thêm." if tier == "free" else ""),
            "limit": limit,
        })


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/schedule")
async def list_schedules(user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id = str(user["id"])
    res = (
        supabase.table("scheduled_jobs")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return {"schedules": res.data or []}


@router.post("/schedule", status_code=201)
async def create_schedule(body: CreateScheduleRequest, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id = str(user["id"])
    tier = get_user_tier(user_id)

    _validate_job_type(body.job_type)
    _validate_schedule_type(body.schedule_type)
    _validate_frequency(body.schedule_type, body.min_interval_hours)
    _check_limit(supabase, user_id, tier)

    if body.duplicate_suppression not in ("off", "url_24h", "canonical_id"):
        raise HTTPException(400, "duplicate_suppression phải là 'off', 'url_24h', hoặc 'canonical_id'.")

    row = {
        "user_id":               user_id,
        "job_type":              body.job_type,
        "input_payload":         body.input_payload,
        "schedule_type":         body.schedule_type,
        "next_run_at":           body.next_run_at,
        "run_at":                body.run_at,
        "run_on_weekday":        body.run_on_weekday,
        "duplicate_suppression": body.duplicate_suppression,
        "min_interval_hours":    max(6, body.min_interval_hours),
    }
    if body.auto_collection_id:
        row["auto_collection_id"] = body.auto_collection_id

    res = supabase.table("scheduled_jobs").insert(row).execute()
    if not res.data:
        raise HTTPException(500, "Không thể tạo lịch tải.")
    return res.data[0]


@router.patch("/schedule/{schedule_id}")
async def patch_schedule(
    schedule_id: str,
    body: PatchScheduleRequest,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])

    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Không có trường nào để cập nhật.")

    if "schedule_type" in updates:
        _validate_schedule_type(updates["schedule_type"])

    res = (
        supabase.table("scheduled_jobs")
        .update(updates)
        .eq("id", schedule_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Lịch tải không tồn tại.")
    return res.data[0]


@router.delete("/schedule/{schedule_id}", status_code=204)
async def delete_schedule(schedule_id: str, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id = str(user["id"])
    supabase.table("scheduled_jobs").delete().eq("id", schedule_id).eq("user_id", user_id).execute()


@router.post("/schedule/{schedule_id}/run", status_code=202)
async def run_schedule_now(schedule_id: str, user=Depends(get_required_user)):
    """Trigger a scheduled job immediately, regardless of next_run_at."""
    supabase = get_service_client()
    user_id = str(user["id"])

    res = (
        supabase.table("scheduled_jobs")
        .select("*")
        .eq("id", schedule_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Lịch tải không tồn tại.")

    job = res.data[0]
    try:
        from app.tasks.schedule_tasks import _trigger_job
        _trigger_job(job)
        supabase.table("scheduled_jobs").update({
            "last_run_at":     datetime.now(timezone.utc).isoformat(),
            "last_run_status": "running",
        }).eq("id", schedule_id).execute()
    except Exception as e:
        raise HTTPException(500, f"Không thể khởi động job: {e}")

    return {"message": "Đã khởi chạy ngay.", "schedule_id": schedule_id}


@router.post("/schedule/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: str, user=Depends(get_required_user)):
    """Pause if active, resume if paused."""
    supabase = get_service_client()
    user_id = str(user["id"])

    res = (
        supabase.table("scheduled_jobs")
        .select("is_active, schedule_type, run_at, run_on_weekday")
        .eq("id", schedule_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Lịch tải không tồn tại.")

    current = res.data[0]["is_active"]
    new_active = not current

    updates: Dict[str, Any] = {"is_active": new_active}

    # When resuming a paused recurring job, recompute next_run_at
    if new_active and res.data[0].get("schedule_type") != "once":
        from app.tasks.schedule_tasks import _compute_next_run
        next_run = _compute_next_run(res.data[0])
        if next_run:
            updates["next_run_at"] = next_run

    (
        supabase.table("scheduled_jobs")
        .update(updates)
        .eq("id", schedule_id)
        .execute()
    )
    return {
        "is_active": new_active,
        "message": "Đã tiếp tục lịch tải." if new_active else "Đã tạm dừng lịch tải.",
    }
