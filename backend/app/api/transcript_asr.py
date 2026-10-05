"""
Transcript ASR API
=====================
Routes:
  POST   /transcript-asr/jobs                    — create an ASR job from an already-downloaded video
  GET    /transcript-asr/jobs                     — list jobs for resolved identity
  GET    /transcript-asr/quota                    — remaining daily minutes + whether creating is currently possible
  GET    /transcript-asr/jobs/{job_id}/download    — ?format=srt|vtt|txt|json (default srt)
  POST   /transcript-asr/jobs/{job_id}/translate   — chain into transcript-translate (creates a translation job from this ASR result)
  DELETE /transcript-asr/jobs/{job_id}             — remove job + result file (never the source video);
                                                    cancels + refunds a job that has not finished

Auto-generates a subtitle file from a downloaded video's audio via the
configured ASR provider (app.services.asr — Gemini or Whisper) — the
HappyScribe-style "Transcribe files" feature.

Phase 32A: everything behind ASR_ENABLED (default off). POST /jobs check
order, no paid call before all pass:
  flag -> (auth, path, ffprobe, 45-min cap) -> per-user quota -> global
  kill switch / spend ceiling / minutes cap -> disk guard -> provider key
Errors added in 32A answer {"detail": "<Vietnamese message>", "error_code": "..."}
so the current frontend (which renders `detail`) keeps working. Reuses the exact identity/quota/cleanup conventions already
established in app.api.transcript_translate rather than inventing new ones.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app.services.asr import budget as asr_budget
from app.services.asr import config as asr_config
from app.services.asr.jobs import NON_TERMINAL, fail_job, parse_error, refund_quota
from app.services.asr.types import ERROR_MESSAGES_VI, AsrError

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


class AsrApiError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str | None = None):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message or ERROR_MESSAGES_VI.get(error_code, error_code)
        super().__init__(self.message)


def _error_response(exc: AsrApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "error_code": exc.error_code},
    )


_BUDGET_STATUS = {"asr_paused": 503, "spend_ceiling_reached": 503, "budget_unavailable": 503}


def _job_to_dict(row: dict) -> dict[str, Any]:
    code, message = parse_error(row.get("error_message"))
    return {
        "id": row["id"],
        "video_title": row.get("video_title"),
        "status": row.get("status"),
        "duration_sec": row.get("duration_sec"),
        "detected_language": row.get("detected_language"),
        "progress_pct": row.get("progress_pct", 0),
        "error": message,
        "error_code": code,
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
    video's audio. See the module docstring for the check order."""
    try:
        return await _create_asr_job(payload, identity, quota_subject)
    except AsrApiError as exc:
        return _error_response(exc)


async def _create_asr_job(payload: CreateAsrJobRequest, identity, quota_subject):
    from app.api.processing import _guard_local_path  # reuse the same path-traversal guard everywhere else in the app uses

    # 1. Master flag — before anything else, no provider can be reached.
    if not asr_config.asr_enabled():
        raise AsrApiError(503, "asr_disabled")

    user_id = identity
    if not user_id:
        raise HTTPException(status_code=401, detail="Cần đăng nhập để sử dụng tính năng tạo phụ đề tự động.")

    # ASR minutes are billed per minute, so the daily cap cannot hang off a
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

    # 2. Per-user daily quota.
    db = _get_db()
    duration_minutes = duration_sec / 60
    usage_date = asr_budget.utcnow().date().isoformat()
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

    provider_name = asr_config.provider_name()
    from app.services.asr import estimate_cost_usd, get_provider  # noqa: PLC0415
    est_cost = estimate_cost_usd(provider_name, duration_sec)
    spend_day: str | None = None

    def _undo() -> None:
        refund_quota(db, quota_key, duration_minutes, usage_date)
        if spend_day:
            asr_budget.release(spend_day, est_cost, duration_minutes)

    # 3. Global kill switch / spend ceiling / minutes cap.
    try:
        spend_day = asr_budget.reserve(est_cost, duration_minutes)
    except AsrError as exc:
        _undo()
        raise AsrApiError(_BUDGET_STATUS.get(exc.code, 503), exc.code) from exc

    # 4. Disk guard (same 507 contract as /fetch-link).
    try:
        from app.api.routes import _preflight_disk_check  # noqa: PLC0415
        _preflight_disk_check()
    except HTTPException:
        _undo()
        raise

    # 5. Provider configured (key present). Constructing it makes no call.
    try:
        get_provider(provider_name)
    except AsrError as exc:
        _undo()
        raise AsrApiError(503, "provider_unavailable") from exc

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
        _undo()
        logger.error("Failed to insert transcript_asr_jobs row: %s", exc)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi tạo job.") from exc

    asr_budget.meta_set(
        job_id,
        provider=provider_name,
        quota_key=quota_key,
        minutes=f"{duration_minutes:.4f}",
        usage_date=usage_date,
        spend_day=spend_day,
        est_cost=f"{est_cost:.6f}",
        created_ts=f"{asr_budget.utcnow().timestamp():.3f}",
    )

    # 6. Queue. A queueing failure must not leave a 'queued' row forever (D2).
    transcribe_task = _get_transcribe_task()
    try:
        if transcribe_task is None:
            raise RuntimeError("transcribe_video_task not importable")
        async_result = transcribe_task.apply_async(args=[job_id])
    except Exception as exc:
        logger.error("Failed to queue transcribe_video_task for %s: %s", job_id, exc)
        fail_job(db, job_id, "queue_unavailable", row={**job_row, "id": job_id})
        asr_budget.settle(job_id, 0.0, paid_started=False)
        raise AsrApiError(503, "queue_unavailable") from exc
    try:
        db.table("transcript_asr_jobs").update({"celery_task_id": async_result.id}).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("Failed to store celery_task_id for %s: %s", job_id, exc)

    return {
        "id": job_id,
        "video_title": payload.video_title,
        "status": "queued",
        "duration_sec": duration_sec,
        "detected_language": None,
        "progress_pct": 0,
        "error": None,
        "error_code": None,
    }


@router.get("/quota")
async def get_asr_quota(
    identity: str | None = Depends(resolve_identity),
    quota_subject: str | None = Depends(resolve_quota_subject),
):
    """What the UI needs to decide whether to offer the create button.

    ``enabled`` is False when ASR_ENABLED is off OR the admin kill switch is on
    OR the kill switch cannot be read (POST /jobs fails closed in that case, so
    offering the button would only lead to an error); ``unavailable_reason``
    says which. ``minutes_used`` is read straight from transcript_asr_usage
    (the table reserve_transcript_asr_usage writes) with a plain select — no new
    RPC. It is ``null`` when the row cannot be read, never a guessed 0.
    """
    from datetime import timedelta  # noqa: PLC0415

    now = asr_budget.utcnow()
    enabled = asr_config.asr_enabled()
    reason: str | None = None if enabled else "asr_disabled"
    if enabled:
        try:
            if asr_budget.killswitch_on():
                enabled, reason = False, "asr_paused"
        except Exception as exc:  # noqa: BLE001 — fail closed, same as POST /jobs
            logger.warning("asr quota: kill switch unreadable: %s", exc)
            enabled, reason = False, "budget_unavailable"

    minutes_used: float | None = None
    quota_key = quota_subject or identity
    if quota_key:
        try:
            resp = (
                _get_db().table("transcript_asr_usage")
                .select("minutes_used")
                .eq("user_id", quota_key)
                .eq("usage_date", now.date().isoformat())
                .execute()
            )
            rows = resp.data or []
            # No row yet today = nothing reserved yet = a true 0.
            minutes_used = round(float(rows[0].get("minutes_used") or 0), 2) if rows else 0.0
        except Exception as exc:  # noqa: BLE001
            logger.warning("asr quota: usage unreadable for %s: %s", quota_key, exc)

    reset_at = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    return {
        "enabled": enabled,
        "unavailable_reason": reason,
        "minutes_used": minutes_used,
        "minutes_limit": _DAILY_MINUTES_LIMIT,
        "per_job_max_minutes": _MAX_DURATION_MINUTES,
        "reset_at_utc": reset_at.isoformat(),
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


_DOWNLOAD_FORMATS = ("srt", "vtt", "txt", "json")


@router.get("/jobs/{job_id}/download")
async def download_asr_result(
    job_id: str,
    format: str = "srt",  # noqa: A002 — public query parameter name
    identity: str | None = Depends(resolve_identity),
):
    fmt = (format or "srt").lower()
    if fmt not in _DOWNLOAD_FORMATS:
        raise HTTPException(status_code=422, detail=f"Định dạng không hỗ trợ. Chỉ hỗ trợ: {', '.join(_DOWNLOAD_FORMATS)}.")

    db = _get_db()
    row = _load_owned_job(db, job_id, identity, require_done=True)

    result_path = row.get("result_path")
    if not result_path or not os.path.isfile(result_path):
        raise HTTPException(status_code=404, detail="File kết quả không còn tồn tại.")

    base = (row.get("video_title") or "transcript").strip() or "transcript"
    if fmt == "srt":
        return FileResponse(result_path, media_type="text/plain; charset=utf-8", filename=f"{base}.srt")

    from app.core.local_download import content_disposition  # noqa: PLC0415
    from app.services.subtitle_format import parse_srt  # noqa: PLC0415

    with open(result_path, "r", encoding="utf-8") as f:
        cues = parse_srt(f.read())

    if fmt == "vtt":
        parts = ["WEBVTT"]
        for c in cues:
            parts.append(f"{c.index}\n{c.start.replace(',', '.')} --> {c.end.replace(',', '.')}\n{c.text}")
        body, media = "\n\n".join(parts) + "\n", "text/vtt; charset=utf-8"
    elif fmt == "txt":
        body, media = "\n".join(c.text for c in cues) + "\n", "text/plain; charset=utf-8"
    else:
        import json as _json  # noqa: PLC0415
        from app.services.subtitle_format import timestamp_to_seconds  # noqa: PLC0415
        from app.tasks.transcript_asr_tasks import sidecar_json_path  # noqa: PLC0415

        sidecar = sidecar_json_path(result_path)
        if os.path.isfile(sidecar):
            with open(sidecar, "r", encoding="utf-8") as f:
                data = _json.load(f)
        else:  # jobs created before 32A have no sidecar — derive from the SRT
            data = {
                "language": row.get("detected_language"),
                "duration_sec": row.get("duration_sec"),
                "segments": [
                    {"start": timestamp_to_seconds(c.start), "end": timestamp_to_seconds(c.end), "text": c.text}
                    for c in cues
                ],
            }
        body, media = _json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8"

    return Response(
        content=body.encode("utf-8"),
        media_type=media,
        headers={"Content-Disposition": content_disposition(f"{base}.{fmt}")},
    )


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

    # Deleting a job that has not finished is a cancel: fail it first so the
    # reserved minutes are refunded exactly once, and revoke the queued task
    # (best effort — a task already running finds the row gone or failed).
    if row.get("status") in NON_TERMINAL:
        fail_job(db, job_id, "cancelled", "Đã huỷ bởi người dùng.", row=row)
        if row.get("status") == "queued":
            asr_budget.settle(job_id, 0.0, paid_started=False)  # never reached a provider
        if row.get("celery_task_id"):
            try:
                from app.core.celery_app import celery_app  # noqa: PLC0415
                celery_app.control.revoke(row["celery_task_id"])
            except Exception:
                pass

    result_path = row.get("result_path")
    if result_path:
        from app.tasks.transcript_asr_tasks import sidecar_json_path  # noqa: PLC0415
        for p in (result_path, sidecar_json_path(result_path)):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass

    try:
        db.table("transcript_asr_jobs").delete().eq("id", job_id).execute()
    except Exception as exc:
        logger.error("Failed to delete transcript_asr_jobs row %s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi xoá job.") from exc

    return {"deleted": True}
