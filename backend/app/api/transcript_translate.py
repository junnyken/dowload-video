"""
Transcript Translation API
============================
Routes:
  POST   /transcript-translate/upload                    — upload batch of .srt/.vtt files
  GET    /transcript-translate/jobs                       — list jobs for resolved identity
  GET    /transcript-translate/jobs/{job_id}/download     — stream translated result file
  POST   /transcript-translate/jobs/{job_id}/burn-into-video — burn translated subtitle into an already-downloaded video
  DELETE /transcript-translate/jobs/{job_id}              — remove job + files

Customers upload existing .srt/.vtt transcript files (batch), the system
translates the cue text to a target language via LLM (meaning-preserving,
not literal), and produces a downloadable translated .srt/.vtt with
original timing preserved. Jobs run async via Celery
(app.tasks.transcript_translation_tasks.translate_transcript_task) and are
tracked in transcript_translation_jobs, polled by the frontend.
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.auth_middleware import get_optional_user as _auth_get_optional_user

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALLOWED_EXT = {".srt", ".vtt"}
_MAX_UPLOAD_MB = 2
_MAX_FILES_PER_BATCH = 10
_MAX_CUES_PER_JOB = 4000
# A pathological single cue (still well under the 2MB file cap — e.g. a
# malformed file where blank-line block separators are missing, merging many
# real cues' text into one) would blow the LLM prompt/response budget for its
# chunk, wasting a normal + a strict-retry call before TranslationAlignmentError
# gives up. Reject it at upload time instead — normal subtitle lines are a
# few dozen characters; 2000 is generous headroom for legitimately long ones.
_MAX_CUE_TEXT_LENGTH = 2000
_TARGET_LANGS = {
    "vi": "Vietnamese", "en": "English", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "fr": "French", "de": "German", "es": "Spanish",
    "th": "Thai", "id": "Indonesian",
}

# Cost control: caps how many cues a single identity can queue for translation
# per day. Reserved atomically at upload time (before the Celery task runs) so
# parallel uploads can't race past the cap. Override via env, same convention
# as this app's existing FREE_DAILY_LIMIT.
#
# Must stay comfortably ABOVE _MAX_CUES_PER_JOB (4000, defined above) — a
# limit smaller than one legitimate max-size job would make that ceiling
# unreachable for every user, every day. 6000 = one big job/day plus a bit
# of room, not "1000 vs 4000" which silently blocked the advertised max.
_DAILY_CUE_LIMIT = int(os.environ.get("TRANSCRIPT_TRANSLATE_DAILY_CUE_LIMIT", "6000"))

# Storage layout: {_DOWNLOAD_DIR}/transcript_jobs/{job_id}/input{ext} and
# .../translated{ext}. Reuses the same _DOWNLOAD_DIR base pattern as
# app.api.flow_cleanup (three levels up from this file -> backend/downloads).
_DOWNLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)
_TRANSCRIPT_JOBS_DIR = os.path.join(_DOWNLOAD_DIR, "transcript_jobs")


# ---------------------------------------------------------------------------
# Identity resolution
#
# Real auth: app.core.auth_middleware.get_optional_user decodes the Supabase
# JWT from the Authorization header (same dependency notes.py uses).
# Anonymous fallback: X-Session-ID header, mirroring app.api.notes._get_session_id
# and the frontend's existing `vg_session_id` localStorage convention
# (see src/components/UserHistoryContent.jsx) — NOT X-User-Id/cookie, which
# nothing in this app actually sends.
# ---------------------------------------------------------------------------

def _get_session_id(request: Request) -> str:
    return (request.headers.get("X-Session-ID") or "")[:64]


async def resolve_identity(
    request: Request,
    user: dict | None = Depends(_auth_get_optional_user),
) -> str | None:
    if user:
        return user.get("id")
    session_id = _get_session_id(request)
    return session_id or None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_work_dir(job_id: str) -> str:
    return os.path.join(_TRANSCRIPT_JOBS_DIR, job_id)


def _job_to_dict(row: dict) -> dict[str, Any]:
    """Shape a transcript_translation_jobs DB row into the API contract job object."""
    return {
        "id": row["id"],
        "filename": row.get("source_filename"),
        "status": row.get("status"),
        "source_lang": row.get("source_lang"),
        "target_lang": row.get("target_lang"),
        "cue_count": row.get("cue_count"),
        "translated_cue_count": row.get("translated_cue_count", 0),
        "progress_pct": row.get("progress_pct", 0),
        "error": row.get("error_message"),
        "skipped_block_count": row.get("skipped_block_count", 0),
        "reading_speed_warning_count": row.get("reading_speed_warning_count", 0),
    }


def _get_db():
    from app.core.database import get_service_client  # noqa: PLC0415
    return get_service_client()


def _get_translate_task():
    try:
        from app.tasks.transcript_translation_tasks import translate_transcript_task  # noqa: PLC0415
        return translate_transcript_task
    except ImportError:
        return None


def _load_owned_job(db, job_id: str, user_id: str | None, *, require_done: bool = False) -> dict:
    """
    Fetch a transcript_translation_jobs row, scoped to the caller's identity.
    Raises 404 (not found/not owned, or not done if require_done) — shared
    by every endpoint that operates on a specific job so the ownership check
    can't drift between them.
    """
    try:
        resp = (
            db.table("transcript_translation_jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        row = resp.data
    except Exception:
        row = None

    if not row or row.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy job dịch phụ đề.")

    if require_done and row.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job chưa hoàn tất.")

    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_transcripts(
    files: list[UploadFile] = File(...),
    target_lang: str = Form(...),
    identity: str | None = Depends(resolve_identity),
):
    """
    Upload a batch of .srt/.vtt transcript files for translation.

    Validates each file independently (extension, size, cue count) — a bad
    file is rejected without failing the whole batch.
    """
    if target_lang not in _TARGET_LANGS:
        raise HTTPException(
            status_code=422,
            detail=f"Ngôn ngữ đích không hợp lệ. Chỉ hỗ trợ: {', '.join(sorted(_TARGET_LANGS))}.",
        )

    if not files:
        raise HTTPException(status_code=422, detail="Chưa có file nào được tải lên.")

    if len(files) > _MAX_FILES_PER_BATCH:
        raise HTTPException(
            status_code=422,
            detail=f"Tối đa {_MAX_FILES_PER_BATCH} file mỗi lần tải lên.",
        )

    user_id = identity
    if not user_id:
        # No resolvable identity (not logged in, no X-Session-ID) — refuse
        # rather than let anonymous requests bypass the daily cue quota below.
        raise HTTPException(
            status_code=401,
            detail="Cần đăng nhập để sử dụng tính năng dịch phụ đề.",
        )

    from app.services.subtitle_format import parse_srt_with_skip_count, parse_vtt_with_skip_count

    max_bytes = _MAX_UPLOAD_MB * 1024 * 1024
    db = _get_db()
    translate_task = _get_translate_task()

    jobs: list[dict] = []
    rejected: list[dict] = []

    os.makedirs(_TRANSCRIPT_JOBS_DIR, exist_ok=True)

    for upload in files:
        fname = upload.filename or "transcript"
        ext = os.path.splitext(fname)[1].lower()

        if ext not in _ALLOWED_EXT:
            rejected.append({
                "filename": fname,
                "reason": "Định dạng không hỗ trợ. Chỉ nhận .srt, .vtt.",
            })
            continue

        # Streamed read with size cap (mirrors flow_cleanup.upload_flow_video)
        written = 0
        chunks: list[bytes] = []
        too_large = False
        try:
            while True:
                chunk = await upload.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    too_large = True
                    break
                chunks.append(chunk)
        except Exception as exc:
            rejected.append({"filename": fname, "reason": f"Lỗi đọc file: {exc}"})
            continue

        if too_large:
            rejected.append({
                "filename": fname,
                "reason": f"File vượt quá {_MAX_UPLOAD_MB}MB.",
            })
            continue

        raw_bytes = b"".join(chunks)
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            rejected.append({
                "filename": fname,
                "reason": "File không phải văn bản UTF-8 hợp lệ.",
            })
            continue

        source_format = "vtt" if ext == ".vtt" else "srt"
        try:
            cues, skipped_block_count = (
                parse_vtt_with_skip_count(content)
                if source_format == "vtt"
                else parse_srt_with_skip_count(content)
            )
        except Exception as exc:
            rejected.append({"filename": fname, "reason": f"Không đọc được phụ đề: {exc}"})
            continue

        cue_count = len(cues)
        if cue_count == 0:
            rejected.append({
                "filename": fname,
                "reason": "Không tìm thấy dòng phụ đề hợp lệ nào trong file.",
            })
            continue

        if cue_count > _MAX_CUES_PER_JOB:
            rejected.append({
                "filename": fname,
                "reason": f"File có {cue_count} dòng phụ đề, vượt quá giới hạn {_MAX_CUES_PER_JOB}.",
            })
            continue

        oversized_cue = next((c for c in cues if len(c.text) > _MAX_CUE_TEXT_LENGTH), None)
        if oversized_cue is not None:
            rejected.append({
                "filename": fname,
                "reason": (
                    f"Dòng phụ đề #{oversized_cue.index} dài {len(oversized_cue.text)} ký tự, "
                    f"vượt quá giới hạn {_MAX_CUE_TEXT_LENGTH}. File có thể bị lỗi định dạng "
                    "(thiếu dòng trống ngăn cách giữa các cue)."
                ),
            })
            continue

        # Atomic reserve-if-it-fits — a single DB round trip that locks this
        # identity's today-row (SELECT ... FOR UPDATE inside the function),
        # checks, and increments in one transaction. Two near-simultaneous
        # requests for the SAME identity now serialize on that row lock
        # instead of both reading a pre-commit total and both passing.
        try:
            reserve_resp = db.rpc(
                "reserve_transcript_translation_usage",
                {"p_user_id": user_id, "p_cues": cue_count, "p_limit": _DAILY_CUE_LIMIT},
            ).execute()
            reserved = bool(reserve_resp.data)
        except Exception as exc:
            logger.error("Failed to call reserve_transcript_translation_usage for %s: %s", user_id, exc)
            # Fail CLOSED — an unreachable quota function must not silently
            # become "unlimited" for this file.
            rejected.append({"filename": fname, "reason": "Không thể kiểm tra hạn mức dịch, vui lòng thử lại sau."})
            continue

        if not reserved:
            rejected.append({
                "filename": fname,
                "reason": (
                    f"Vượt quá hạn mức dịch trong ngày ({_DAILY_CUE_LIMIT} dòng/ngày). "
                    "Thử lại vào ngày mai."
                ),
            })
            continue

        # Passed validation — persist to disk + create job row
        job_id = str(uuid.uuid4())
        work_dir = _job_work_dir(job_id)
        os.makedirs(work_dir, exist_ok=True)
        input_path = os.path.join(work_dir, f"input{ext}")

        try:
            with open(input_path, "wb") as f:
                f.write(raw_bytes)
        except Exception as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            rejected.append({"filename": fname, "reason": f"Lỗi lưu file: {exc}"})
            continue

        job_row = {
            "id": job_id,
            "user_id": user_id,
            "source_filename": fname,
            "source_format": source_format,
            "target_lang": target_lang,
            "cue_count": cue_count,
            "translated_cue_count": 0,
            "status": "queued",
            "progress_pct": 0,
            "input_path": input_path,
            "skipped_block_count": skipped_block_count,
        }
        if skipped_block_count > 0:
            logger.warning(
                "transcript_translate upload: %d malformed block(s) skipped in %s",
                skipped_block_count, fname,
            )

        try:
            db.table("transcript_translation_jobs").insert(job_row).execute()
        except Exception as exc:
            logger.error("Failed to insert transcript_translation_jobs row: %s", exc)
            shutil.rmtree(work_dir, ignore_errors=True)
            rejected.append({"filename": fname, "reason": "Lỗi hệ thống khi tạo job."})
            continue

        if translate_task is not None:
            try:
                # No explicit queue= here — let celery_app.py's task_routes
                # decide (currently 'analysis'; a hardcoded queue name here
                # would silently override that routing and re-send jobs to
                # a queue no worker consumes).
                async_result = translate_task.apply_async(args=[job_id])
                db.table("transcript_translation_jobs").update(
                    {"celery_task_id": async_result.id}
                ).eq("id", job_id).execute()
            except Exception as exc:
                logger.error("Failed to queue translate_transcript_task for %s: %s", job_id, exc)
        else:
            logger.warning(
                "translate_transcript_task not importable — job %s queued in DB only", job_id
            )

        jobs.append({
            "id": job_id,
            "filename": fname,
            "status": "queued",
            "source_lang": None,
            "target_lang": target_lang,
            "cue_count": cue_count,
            "translated_cue_count": 0,
            "progress_pct": 0,
            "error": None,
            "skipped_block_count": skipped_block_count,
            "reading_speed_warning_count": 0,
        })

    return {"jobs": jobs, "rejected": rejected}


@router.get("/jobs")
async def list_transcript_jobs(identity: str | None = Depends(resolve_identity)):
    """List transcript translation jobs for the resolved identity, most recent first."""
    user_id = identity
    db = _get_db()

    query = db.table("transcript_translation_jobs").select("*")
    if user_id:
        query = query.eq("user_id", user_id)
    else:
        query = query.is_("user_id", "null")

    try:
        resp = query.order("created_at", desc=True).limit(50).execute()
        rows = resp.data or []
    except Exception as exc:
        logger.error("Failed to list transcript_translation_jobs: %s", exc)
        rows = []

    return {"jobs": [_job_to_dict(r) for r in rows]}


@router.get("/jobs/{job_id}/download")
async def download_transcript_result(job_id: str, identity: str | None = Depends(resolve_identity)):
    """Stream the translated result file. 404 if not done, not owned, or file missing."""
    db = _get_db()
    row = _load_owned_job(db, job_id, identity, require_done=True)

    result_path = row.get("result_path")
    if not result_path or not os.path.isfile(result_path):
        raise HTTPException(status_code=404, detail="File kết quả không còn tồn tại.")

    source_filename = row.get("source_filename") or "transcript"
    base, ext = os.path.splitext(source_filename)
    if not ext:
        ext = f".{row.get('source_format', 'srt')}"
    download_name = f"{base}_translated{ext}"

    return FileResponse(
        result_path,
        media_type="text/plain; charset=utf-8",
        filename=download_name,
    )


class BurnIntoVideoRequest(BaseModel):
    video_local_path: str  # a download_jobs.local_file_path — must still be on disk


@router.post("/jobs/{job_id}/burn-into-video")
async def burn_translated_subtitle_into_video(
    job_id: str,
    payload: BurnIntoVideoRequest,
    identity: str | None = Depends(resolve_identity),
):
    """
    Burn this job's translated subtitle into a video the user already
    downloaded (referenced by its server-side local_file_path from
    /history). Reuses app.api.processing's existing FFmpeg burn helper and
    path-traversal guard rather than re-implementing them — this endpoint's
    only new logic is resolving *which* subtitle file to burn (the
    translation output) from an identity-scoped job instead of re-fetching
    from a source URL like /process/burn-subtitle does.
    """
    from app.api.processing import (
        _burn_subtitle,
        _guard_local_path,
        _safe_download_dir,
        _schedule_cleanup,
        _success_response,
    )

    db = _get_db()
    row = _load_owned_job(db, job_id, identity, require_done=True)

    subtitle_path = row.get("result_path")
    if not subtitle_path or not os.path.isfile(subtitle_path):
        raise HTTPException(status_code=404, detail="File phụ đề đã dịch không còn tồn tại.")

    # Raises 400 (path traversal) / 404 (expired/missing) — same guard
    # /process/burn-subtitle already relies on, so a video that expired via
    # the normal downloads-cleanup sweep fails with the same clear error
    # message users already see elsewhere in the app.
    video_path = _guard_local_path(payload.video_local_path)

    download_dir = _safe_download_dir()
    out_name = f"burned_{uuid.uuid4().hex[:8]}.mp4"
    output_path = os.path.join(download_dir, out_name)

    success = _burn_subtitle(video_path, subtitle_path, output_path)
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Ghép phụ đề vào video thất bại (lỗi FFmpeg hoặc video không hợp lệ).",
        )

    _schedule_cleanup(output_path)
    return _success_response(output_path, out_name)


def _parse_result_cues(row: dict) -> list:
    """Load + parse a done job's result_path. Raises 404 if missing."""
    from app.services.subtitle_format import parse_srt, parse_vtt

    result_path = row.get("result_path")
    if not result_path or not os.path.isfile(result_path):
        raise HTTPException(status_code=404, detail="File kết quả không còn tồn tại.")

    with open(result_path, "r", encoding="utf-8") as f:
        content = f.read()

    return parse_vtt(content) if row.get("source_format") == "vtt" else parse_srt(content)


@router.get("/jobs/{job_id}/cues")
async def get_translated_cues(job_id: str, identity: str | None = Depends(resolve_identity)):
    """Return every translated cue (index/start/end/text) for review/editing
    before download — the summary job object elsewhere in this API doesn't
    carry the actual cue text."""
    db = _get_db()
    row = _load_owned_job(db, job_id, identity, require_done=True)
    cues = _parse_result_cues(row)

    return {
        "cues": [
            {"index": c.index, "start": c.start, "end": c.end, "text": c.text}
            for c in cues
        ],
    }


class UpdateCue(BaseModel):
    index: int
    text: str


class UpdateCuesRequest(BaseModel):
    cues: list[UpdateCue]


@router.put("/jobs/{job_id}/cues")
async def update_translated_cues(
    job_id: str,
    payload: UpdateCuesRequest,
    identity: str | None = Depends(resolve_identity),
):
    """
    Apply manual text edits to a subset of a done job's translated cues and
    re-write result_path. Only `text` is editable — index/start/end (cue
    identity and timing) are never touched, so a request can't add, remove,
    reorder, or re-time cues, only fix wording on cues that already exist.
    Unknown indices in the request are ignored (not an error — the file may
    have been re-translated/changed between the editor loading and saving).
    """
    from app.services.subtitle_format import serialize_srt, serialize_vtt
    from app.services.subtitle_qa import check_reading_speed

    if len(payload.cues) > _MAX_CUES_PER_JOB:
        raise HTTPException(status_code=422, detail=f"Quá nhiều dòng chỉnh sửa, vượt giới hạn {_MAX_CUES_PER_JOB}.")
    oversized_edit = next((edit for edit in payload.cues if len(edit.text) > _MAX_CUE_TEXT_LENGTH), None)
    if oversized_edit is not None:
        raise HTTPException(
            status_code=422,
            detail=f"Dòng #{oversized_edit.index} vượt quá giới hạn {_MAX_CUE_TEXT_LENGTH} ký tự.",
        )

    db = _get_db()
    row = _load_owned_job(db, job_id, identity, require_done=True)
    cues = _parse_result_cues(row)

    edits_by_index = {edit.index: edit.text for edit in payload.cues}
    updated_cues = [
        type(cue)(index=cue.index, start=cue.start, end=cue.end,
                   text=edits_by_index.get(cue.index, cue.text))
        for cue in cues
    ]

    output_content = (
        serialize_vtt(updated_cues) if row.get("source_format") == "vtt" else serialize_srt(updated_cues)
    )
    try:
        with open(row["result_path"], "w", encoding="utf-8") as f:
            f.write(output_content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu chỉnh sửa: {exc}") from exc

    warning_count = len(check_reading_speed(updated_cues))
    try:
        db.table("transcript_translation_jobs").update(
            {"reading_speed_warning_count": warning_count}
        ).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning("Failed to update reading_speed_warning_count for %s: %s", job_id, exc)

    return {"success": True, "reading_speed_warning_count": warning_count}


@router.delete("/jobs/{job_id}")
async def delete_transcript_job(job_id: str, identity: str | None = Depends(resolve_identity)):
    """Remove the job row and its files (input + result if present). Identity-scoped."""
    db = _get_db()
    row = _load_owned_job(db, job_id, identity)

    for path_key in ("input_path", "result_path"):
        path = row.get(path_key)
        if path:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except Exception:
                pass

    work_dir = _job_work_dir(job_id)
    shutil.rmtree(work_dir, ignore_errors=True)

    try:
        db.table("transcript_translation_jobs").delete().eq("id", job_id).execute()
    except Exception as exc:
        logger.error("Failed to delete transcript_translation_jobs row %s: %s", job_id, exc)
        raise HTTPException(status_code=500, detail="Lỗi hệ thống khi xoá job.") from exc

    return {"deleted": True}
