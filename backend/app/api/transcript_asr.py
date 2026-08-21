"""
Transcript ASR API
=====================
Routes:
  POST   /transcript-asr/jobs                    — create an ASR job from an already-downloaded video
  GET    /transcript-asr/jobs                     — list jobs for resolved identity
  GET    /transcript-asr/jobs/{job_id}/download    — stream the generated .srt
  POST   /transcript-asr/jobs/{job_id}/translate   — chain into transcript-translate (creates a translation job from this ASR result)
  DELETE /transcript-asr/jobs/{job_id}             — remove job + result file (never the source video)

Auto-generates a subtitle file from a downloaded video's audio via OpenAI
Whisper (app.services.asr_service) — the HappyScribe-style "Transcribe
files" feature. Reuses the exact identity/quota/cleanup conventions already
established in app.api.transcript_translate rather than inventing new ones.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.transcript_translate import (  # same identity model, reused not duplicated
    resolve_identity,
    resolve_quota_subject,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Must stay in sync with transcript_asr_tasks._MAX_DURATION_SEC — checked
# here too so a too-long video is rejected immediately at upload time
# instead of failing later inside the Celery task.
_MAX_DURATION_MINUTES = 45

# Cost control: mirrors transcript_translate._DAILY_CUE_LIMIT's reasoning,
# just keyed by audio minutes (Whisper bills/limits per duration). One
# max-length job (45 min) plus room for a couple more.
_DAILY_MINUTES_LIMIT = int(os.environ.get("TRANSCRIPT_ASR_DAILY_MINUTES_LIMIT", "120"))

_DOWNLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)


def _get_db():
    from app.core.database import get_service_client  # noqa: PLC0415
    return get_service_client()


def _get_transcribe_task():
    try:
        from app.tasks.transcript_asr_tasks import transcribe_video_task  # noqa: PLC0415
        return transcribe_video_task
    except ImportError:
        return None


def _job_to_dict(row: dict) -> dict[str, Any]:
    return {
        "id": row["id"],
        "video_title": row.get("video_title"),
        "status": row.get("status"),
        "duration_sec": row.get("duration_sec"),
        "detected_language": row.get("detected_language"),
        "progress_pct": row.get("progress_pct", 0),
        "error": row.get("error_message"),
    }


def _load_owned_job(db, job_id: str, user_id: str | None, *, require_done: bool = False) -> dict:
    """Mirrors app.api.transcript_translate._load_owned_job — same shared
    shape, kept local since these are two separate tables."""
    try:
        resp = (
            db.table("transcript_asr_jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        row = resp.data
    except Exception:
        row = None

    if not row or row.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy job tạo phụ đề.")

    if require_done and row.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job chưa hoàn tất.")

    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class CreateAsrJobRequest(BaseModel):
    video_local_path: str
    video_title: str | None = None


@router.post("/jobs")
async def create_asr_job(
    payload: CreateAsrJobRequest,
    identity: str | None = Depends(resolve_identity),
    quota_subject: str | None = Depends(resolve_quota_subject),
):
    """Create a job that auto-generates subtitles from an already-downloaded
    video's audio. Validates the video path, probes its duration to enforce
    the per-job cap and reserve daily quota, then hands off to Celery."""
    from app.api.processing import _guard_local_path  # reuse the same path-traversal guard everywhere else in the app uses

    user_id = identity
    if not user_id:
        raise HTTPException(status_code=401, detail="Cần đăng nhập để sử dụng tính năng tạo phụ đề tự động.")

    # Whisper minutes are billed per minute, so the daily cap cannot hang off a
    # client-supplied X-Session-ID — see resolve_quota_subject.
    quota_key = quota_subject or user_id

    video_path = _guard_local_path(payload.video_local_path)

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, timeout=30, text=True,
        )
        duration_sec = float(result.stdout.strip())
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Không đọc được thời lượng video: {exc}") from exc

    if duration_sec <= 0:
        raise HTTPException(status_code=400, detail="Video không hợp lệ hoặc rỗng.")
    if duration_sec > _MAX_DURATION_MINUTES * 60:
        raise HTTPException(
            status_code=422,
            detail=f"Video dài {duration_sec/60:.1f} phút, vượt giới hạn {_MAX_DURATION_MINUTES} phút.",
        )

    db = _get_db()
    duration_minutes = duration_sec / 60
    try:
        reserve_resp = db.rpc(
            "reserve_transcript_asr_usage",
            {"p_user_id": quota_key, "p_minutes": duration_minutes, "p_limit": _DAILY_MINUTES_LIMIT},
        ).execute()
        reserved = bool(reserve_resp.data)
    except Exception as exc:
        logger.error("Failed to call reserve_transcript_asr_usage for %s: %s", quota_key, exc)
        raise HTTPException(status_code=503, detail="Không thể kiểm tra hạn mức, vui lòng thử lại sau.") from exc

    if not reserved:
        raise HTTPException(
            status_code=422,
            detail=f"Vượt quá hạn mức tạo phụ đề tự động trong ngày ({_DAILY_MINUTES_LIMIT} phút/ngày).",
        )

    job_row = {
        "user_id": user_id,
        "video_local_path": video_path,
        "video_title": payload.video_title,
        "duration_sec": duration_sec,
        "status": "queued",
        "progress_pct": 0,
    }
    try:
        insert_resp = db.table("transcript_asr_jobs").insert(job_row).execute()
        job_id = insert_resp.data[0]["id"]
    except Exception as exc:
        logger.error("Failed to insert transcript_asr_jobs row: %s", exc)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi tạo job.") from exc

    transcribe_task = _get_transcribe_task()
    if transcribe_task is not None:
        try:
            async_result = transcribe_task.apply_async(args=[job_id])
            db.table("transcript_asr_jobs").update({"celery_task_id": async_result.id}).eq("id", job_id).execute()
        except Exception as exc:
            logger.error("Failed to queue transcribe_video_task for %s: %s", job_id, exc)
    else:
        logger.warning("transcribe_video_task not importable — job %s queued in DB only", job_id)

    return {
        "id": job_id,
        "video_title": payload.video_title,
        "status": "queued",
        "duration_sec": duration_sec,
        "detected_language": None,
        "progress_pct": 0,
        "error": None,
    }


@router.get("/jobs")
async def list_asr_jobs(identity: str | None = Depends(resolve_identity)):
    db = _get_db()
    query = db.table("transcript_asr_jobs").select("*")
    if identity:
        query = query.eq("user_id", identity)
    else:
        query = query.is_("user_id", "null")

    try:
        resp = query.order("created_at", desc=True).limit(50).execute()
        rows = resp.data or []
    except Exception as exc:
        logger.error("Failed to list transcript_asr_jobs: %s", exc)
        rows = []

    return {"jobs": [_job_to_dict(r) for r in rows]}


@router.get("/jobs/{job_id}/download")
async def download_asr_result(job_id: str, identity: str | None = Depends(resolve_identity)):
    db = _get_db()
    row = _load_owned_job(db, job_id, identity, require_done=True)

    result_path = row.get("result_path")
    if not result_path or not os.path.isfile(result_path):
        raise HTTPException(status_code=404, detail="File kết quả không còn tồn tại.")

    base = (row.get("video_title") or "transcript").strip() or "transcript"
    return FileResponse(result_path, media_type="text/plain; charset=utf-8", filename=f"{base}.srt")


class ChainTranslateRequest(BaseModel):
    target_lang: str


@router.post("/jobs/{job_id}/translate")
async def translate_asr_result(
    job_id: str,
    payload: ChainTranslateRequest,
    identity: str | None = Depends(resolve_identity),
):
    """
    Convenience action: create a transcript-translate job directly from this
    ASR job's output, instead of making the user download the .srt and
    re-upload it. Reuses the exact same validation/quota path
    transcript_translate.upload_transcripts applies to a real upload — this
    just supplies the file bytes from disk instead of from an HTTP upload.
    """
    import shutil
    import uuid

    from app.api.transcript_translate import (
        _DAILY_CUE_LIMIT,
        _MAX_CUE_TEXT_LENGTH,
        _MAX_CUES_PER_JOB,
        _TARGET_LANGS,
        _TRANSCRIPT_JOBS_DIR,
    )
    from app.services.subtitle_format import parse_srt_with_skip_count

    if payload.target_lang not in _TARGET_LANGS:
        raise HTTPException(
            status_code=422,
            detail=f"Ngôn ngữ đích không hợp lệ. Chỉ hỗ trợ: {', '.join(sorted(_TARGET_LANGS))}.",
        )

    db = _get_db()
    asr_row = _load_owned_job(db, job_id, identity, require_done=True)
    result_path = asr_row.get("result_path")
    if not result_path or not os.path.isfile(result_path):
        raise HTTPException(status_code=404, detail="File phụ đề chưa hoàn tất, chưa thể dịch.")

    with open(result_path, "r", encoding="utf-8") as f:
        content = f.read()
    cues, skipped_block_count = parse_srt_with_skip_count(content)
    cue_count = len(cues)

    if cue_count == 0:
        raise HTTPException(status_code=422, detail="Không tìm thấy dòng phụ đề hợp lệ nào để dịch.")
    if cue_count > _MAX_CUES_PER_JOB:
        raise HTTPException(status_code=422, detail=f"File có {cue_count} dòng, vượt giới hạn {_MAX_CUES_PER_JOB}.")
    oversized = next((c for c in cues if len(c.text) > _MAX_CUE_TEXT_LENGTH), None)
    if oversized is not None:
        raise HTTPException(status_code=422, detail=f"Dòng #{oversized.index} quá dài để dịch.")

    try:
        reserve_resp = db.rpc(
            "reserve_transcript_translation_usage",
            {"p_user_id": identity, "p_cues": cue_count, "p_limit": _DAILY_CUE_LIMIT},
        ).execute()
        reserved = bool(reserve_resp.data)
    except Exception as exc:
        logger.error("Failed to reserve translation quota for ASR-chained job %s: %s", job_id, exc)
        raise HTTPException(status_code=503, detail="Không thể kiểm tra hạn mức, vui lòng thử lại sau.") from exc
    if not reserved:
        raise HTTPException(status_code=422, detail=f"Vượt quá hạn mức dịch trong ngày ({_DAILY_CUE_LIMIT} dòng/ngày).")

    translate_job_id = str(uuid.uuid4())
    work_dir = os.path.join(_TRANSCRIPT_JOBS_DIR, translate_job_id)
    os.makedirs(work_dir, exist_ok=True)
    input_path = os.path.join(work_dir, "input.srt")
    with open(input_path, "w", encoding="utf-8") as f:
        f.write(content)

    translate_job_row = {
        "id": translate_job_id,
        "user_id": identity,
        "source_filename": (asr_row.get("video_title") or "transcript") + ".srt",
        "source_format": "srt",
        "target_lang": payload.target_lang,
        "cue_count": cue_count,
        "translated_cue_count": 0,
        "status": "queued",
        "progress_pct": 0,
        "input_path": input_path,
        "skipped_block_count": skipped_block_count,
    }
    try:
        db.table("transcript_translation_jobs").insert(translate_job_row).execute()
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.error("Failed to insert chained transcript_translation_jobs row: %s", exc)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi tạo job dịch.") from exc

    from app.api.transcript_translate import _get_translate_task
    translate_task = _get_translate_task()
    if translate_task is not None:
        try:
            async_result = translate_task.apply_async(args=[translate_job_id])
            db.table("transcript_translation_jobs").update(
                {"celery_task_id": async_result.id}
            ).eq("id", translate_job_id).execute()
        except Exception as exc:
            logger.error("Failed to queue chained translate_transcript_task for %s: %s", translate_job_id, exc)

    return {
        "id": translate_job_id,
        "filename": translate_job_row["source_filename"],
        "status": "queued",
        "source_lang": None,
        "target_lang": payload.target_lang,
        "cue_count": cue_count,
        "translated_cue_count": 0,
        "progress_pct": 0,
        "error": None,
        "skipped_block_count": skipped_block_count,
        "reading_speed_warning_count": 0,
    }


@router.delete("/jobs/{job_id}")
async def delete_asr_job(job_id: str, identity: str | None = Depends(resolve_identity)):
    db = _get_db()
    row = _load_owned_job(db, job_id, identity)

    result_path = row.get("result_path")
    if result_path:
        try:
            if os.path.isfile(result_path):
                os.remove(result_path)
        except Exception:
            pass

    try:
        db.table("transcript_asr_jobs").delete().eq("id", job_id).execute()
    except Exception as exc:
        logger.error("Failed to delete transcript_asr_jobs row %s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi xoá job.") from exc

    return {"deleted": True}
