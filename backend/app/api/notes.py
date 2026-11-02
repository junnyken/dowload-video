"""
Clip Notes — timestamped notes on download jobs.
POST   /api/v1/jobs/{job_id}/notes
GET    /api/v1/jobs/{job_id}/notes
PATCH  /api/v1/jobs/{job_id}/notes/{note_id}
DELETE /api/v1/jobs/{job_id}/notes/{note_id}
"""
import uuid
from typing import Optional, Any, Dict
import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from app.core.auth_middleware import get_optional_user
from app.core.database import get_service_client

router = APIRouter()
logger = logging.getLogger(__name__)
_MAX_NOTES_PER_JOB = 10


class NoteCreate(BaseModel):
    note_text: str = Field(..., min_length=1, max_length=500)
    timestamp_seconds: Optional[int] = Field(None, ge=0)


class NoteUpdate(BaseModel):
    note_text: str = Field(..., min_length=1, max_length=500)
    timestamp_seconds: Optional[int] = Field(None, ge=0)


def _get_session_id(request: Request) -> str:
    return (request.headers.get("X-Session-ID") or "")[:64]


# ── POST /jobs/{job_id}/notes ─────────────────────────────────────────

@router.post("/jobs/{job_id}/notes", status_code=201)
async def create_note(
    job_id: str,
    body: NoteCreate,
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    user_id = (user or {}).get("id")
    if not user_id:
        raise HTTPException(401, detail="Vui lòng đăng nhập để lưu ghi chú.")
    session_id = None
    supabase = get_service_client()

    # A signed-in user may only annotate their own job.
    if user_id:
        try:
            owned = (
                supabase.table("download_jobs").select("id")
                .eq("id", job_id).eq("user_id", user_id).limit(1).execute()
            )
        except Exception as e:
            logger.warning("note job ownership check failed: %s", type(e).__name__)
            raise HTTPException(500, detail="Không thể lưu ghi chú.")
        if not owned.data:
            raise HTTPException(404, detail="Không tìm thấy video.")

    # Max notes per job, counted per owner so nobody can fill another's quota.
    try:
        count_q = (
            supabase.table("clip_notes")
            .select("id", count="exact")
            .eq("job_id", job_id)
        )
        count_q = count_q.eq("user_id", user_id) if user_id else count_q.eq("session_id", session_id)
        count_res = count_q.execute()
        if (count_res.count or 0) >= _MAX_NOTES_PER_JOB:
            raise HTTPException(400, detail=f"Tối đa {_MAX_NOTES_PER_JOB} ghi chú mỗi video.")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("note quota query failed: %s", type(e).__name__)
        raise HTTPException(500, detail="Không thể lưu ghi chú.")

    row = {
        "id":                 str(uuid.uuid4()),
        "job_id":             job_id,
        "user_id":            user_id,
        "session_id":         session_id,
        "timestamp_seconds":  body.timestamp_seconds,
        "note_text":          body.note_text.strip(),
    }
    row = {k: v for k, v in row.items() if v is not None}
    row["id"] = row.get("id") or str(uuid.uuid4())
    row["job_id"] = job_id

    try:
        res = supabase.table("clip_notes").insert(row).execute()
        return (res.data or [{}])[0]
    except Exception as e:
        logger.warning("API operation failed: %s", type(e).__name__)
        raise HTTPException(500, detail="Không thể lưu ghi chú.")


# ── GET /jobs/{job_id}/notes ──────────────────────────────────────────

@router.get("/jobs/{job_id}/notes")
async def list_notes(
    job_id: str,
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    supabase = get_service_client()
    session_id = _get_session_id(request)
    user_id = user["id"] if user else None

    try:
        q = supabase.table("clip_notes").select("*").eq("job_id", job_id)
        if user_id:
            q = q.eq("user_id", user_id)
        elif session_id:
            q = q.eq("session_id", session_id)
        else:
            return {"notes": []}
        res = q.order("timestamp_seconds", desc=False, nullsfirst=True).order("created_at").execute()
        return {"notes": res.data or []}
    except Exception as e:
        logger.warning("API operation failed: %s", type(e).__name__)
        raise HTTPException(500, detail="Không thể tải ghi chú.")


# ── PATCH /jobs/{job_id}/notes/{note_id} ─────────────────────────────

@router.patch("/jobs/{job_id}/notes/{note_id}")
async def update_note(
    job_id: str,
    note_id: str,
    body: NoteUpdate,
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    supabase = get_service_client()
    session_id = _get_session_id(request)
    user_id = user["id"] if user else None

    try:
        q = supabase.table("clip_notes").select("*").eq("id", note_id).eq("job_id", job_id)
        if user_id:
            q = q.eq("user_id", user_id)
        elif session_id:
            q = q.eq("session_id", session_id)
        else:
            raise HTTPException(404, detail="Ghi chú không tồn tại.")
        res = q.limit(1).execute()
        if not res.data:
            raise HTTPException(404, detail="Ghi chú không tồn tại.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(404, detail="Ghi chú không tồn tại.")

    from datetime import datetime, timezone
    update = {
        "note_text":         body.note_text.strip(),
        "timestamp_seconds": body.timestamp_seconds,
        "updated_at":        datetime.now(timezone.utc).isoformat(),
    }
    try:
        q = supabase.table("clip_notes").update(update).eq("id", note_id).eq("job_id", job_id)
        q = q.eq("user_id", user_id) if user_id else q.eq("session_id", session_id)
        res = q.execute()
        return (res.data or [{}])[0]
    except Exception as e:
        logger.warning("API operation failed: %s", type(e).__name__)
        raise HTTPException(500, detail="Không thể cập nhật ghi chú.")


# ── DELETE /jobs/{job_id}/notes/{note_id} ────────────────────────────

@router.delete("/jobs/{job_id}/notes/{note_id}", status_code=204)
async def delete_note(
    job_id: str,
    note_id: str,
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    supabase = get_service_client()
    session_id = _get_session_id(request)
    user_id = user["id"] if user else None

    if not user_id and not session_id:
        raise HTTPException(404, detail="Ghi chú không tồn tại.")
    q = supabase.table("clip_notes").delete().eq("id", note_id).eq("job_id", job_id)
    q = q.eq("user_id", user_id) if user_id else q.eq("session_id", session_id)
    try:
        q.execute()
    except Exception as e:
        logger.warning("API operation failed: %s", type(e).__name__)
        raise HTTPException(500, detail="Không thể xoá ghi chú.")
    return None
