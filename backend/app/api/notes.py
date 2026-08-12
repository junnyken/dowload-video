"""
Clip Notes — timestamped notes on download jobs.
POST   /api/v1/jobs/{job_id}/notes
GET    /api/v1/jobs/{job_id}/notes
PATCH  /api/v1/jobs/{job_id}/notes/{note_id}
DELETE /api/v1/jobs/{job_id}/notes/{note_id}
"""
import uuid
from typing import Optional, Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from app.core.auth_middleware import get_optional_user
from app.core.database import get_service_client

router = APIRouter()
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
    supabase = get_service_client()
    session_id = _get_session_id(request) if not user else None
    user_id = user["id"] if user else None

    # Check max notes per job
    try:
        count_res = (
            supabase.table("clip_notes")
            .select("id", count="exact")
            .eq("job_id", job_id)
            .execute()
        )
        if (count_res.count or 0) >= _MAX_NOTES_PER_JOB:
            raise HTTPException(400, detail=f"Tối đa {_MAX_NOTES_PER_JOB} ghi chú mỗi video.")
    except HTTPException:
        raise
    except Exception:
        pass

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
        raise HTTPException(500, detail=f"Không thể lưu ghi chú: {e}")


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
        raise HTTPException(500, detail=f"Không thể tải ghi chú: {e}")


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
        res = supabase.table("clip_notes").update(update).eq("id", note_id).execute()
        return (res.data or [{}])[0]
    except Exception as e:
        raise HTTPException(500, detail=f"Không thể cập nhật ghi chú: {e}")


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

    q = supabase.table("clip_notes").delete().eq("id", note_id).eq("job_id", job_id)
    if user_id:
        q = q.eq("user_id", user_id)
    elif session_id:
        q = q.eq("session_id", session_id)
    try:
        q.execute()
    except Exception as e:
        raise HTTPException(500, detail=f"Không thể xoá ghi chú: {e}")
    return None
