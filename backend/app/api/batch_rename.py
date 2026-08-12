"""
Batch Rename & Auto-Organize
=============================
POST /api/v1/bulk-zip-rename
Body: { batch_id: str, pattern: str }
Response: { success: True, zip_url: str, filename: str, file_count: int }

Available pattern tokens:
  {platform}       — job.platform or "unknown"
  {creator}        — job_metadata.creator_handle or "unknown"
  {date}           — job_metadata.upload_date or download_date
  {download_date}  — job.created_at[:10]
  {title}          — sanitized job.title
  {index}          — 001, 002, 003...
  {duration}       — HH-MM-SS from duration_seconds
  {resolution}     — e.g., "1080p"
"""
import os, re, uuid, zipfile
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from app.core.auth_middleware import get_optional_user
from app.core.database import get_service_client
from app.main import limiter

router = APIRouter()

_DOWNLOADS_DIR = os.environ.get("DOWNLOAD_DIR", "/app/downloads")
_FORBIDDEN = re.compile(r'[/\\:*?"<>|]')


def _sanitize(s: str, max_len: int = 80) -> str:
    s = _FORBIDDEN.sub('', str(s or ''))
    s = re.sub(r'\s+', '_', s.strip())
    return s[:max_len] or "unknown"


def _fmt_duration(seconds) -> str:
    if not seconds:
        return "00-00-00"
    try:
        s = int(seconds)
        return f"{s//3600:02d}-{(s%3600)//60:02d}-{s%60:02d}"
    except Exception:
        return "00-00-00"


def _apply_pattern(pattern: str, job: dict, meta: dict, index: int) -> str:
    dl_date = (job.get("created_at") or "")[:10]

    upload_date = None
    if meta:
        ud = meta.get("upload_date")
        if ud:
            upload_date = str(ud)[:10]   # already ISO format from job_metadata

    tokens = {
        "{platform}":      _sanitize(job.get("platform") or "unknown"),
        "{creator}":       _sanitize(meta.get("creator_handle") if meta else None) or "unknown",
        "{date}":          upload_date or dl_date or "unknown",
        "{download_date}": dl_date or "unknown",
        "{title}":         _sanitize(job.get("title") or "video", max_len=80),
        "{index}":         f"{index:03d}",
        "{duration}":      _fmt_duration(meta.get("duration_seconds") if meta else None),
        "{resolution}":    f"{job.get('downloaded_height') or 0}p",
    }

    result = pattern
    for token, value in tokens.items():
        result = result.replace(token, value)

    # Strip trailing/leading underscores from segment joins
    result = re.sub(r'_+', '_', result).strip('_')
    return result[:200] or "video"


class RenameRequest(BaseModel):
    batch_id: str
    pattern: str = "{platform}_{creator}_{date}_{title}"


@router.post("/bulk-zip-rename")
@limiter.limit("10/minute")
async def bulk_zip_rename(
    payload: RenameRequest,
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    """
    Apply a naming pattern to all successful jobs in a batch, then create a ZIP.
    """
    pattern = payload.pattern.strip()
    if not pattern:
        raise HTTPException(400, detail="Pattern không được để trống.")

    supabase = get_service_client()

    # 1. Fetch all successful jobs for this batch
    try:
        q = (
            supabase.table("download_jobs")
            .select("id, batch_id, title, platform, created_at, downloaded_height, is_audio_only, local_file_path, original_url")
            .eq("batch_id", payload.batch_id)
            .eq("status", "success")
            .order("created_at")
        )
        if user:
            q = q.eq("user_id", user["id"])
        res = q.execute()
        jobs = res.data or []
    except Exception as e:
        raise HTTPException(500, detail=f"Không thể tải danh sách job: {e}")

    if not jobs:
        raise HTTPException(404, detail="Không tìm thấy job nào trong batch này.")

    # 2. Fetch job_metadata for all jobs
    job_ids = [j["id"] for j in jobs]
    meta_map: dict = {}
    try:
        meta_res = (
            supabase.table("job_metadata")
            .select("job_id, creator_handle, duration_seconds, upload_date")
            .in_("job_id", job_ids)
            .execute()
        )
        for m in (meta_res.data or []):
            meta_map[m["job_id"]] = m
    except Exception:
        pass  # metadata is optional

    # 3. Build list of (local_path, new_name) pairs
    pairs = []
    seen_names: set = set()

    for i, job in enumerate(jobs, start=1):
        path = job.get("local_file_path")
        if not path or not os.path.exists(path):
            continue

        meta = meta_map.get(job["id"])
        ext = ".mp3" if job.get("is_audio_only") else ".mp4"

        new_name = _apply_pattern(pattern, job, meta, i) + ext

        # De-duplicate: append counter if clash
        base_new = new_name
        counter = 2
        while new_name in seen_names:
            stem = base_new[:base_new.rfind('.')]
            new_name = f"{stem}_{counter}{ext}"
            counter += 1
        seen_names.add(new_name)
        pairs.append((path, new_name))

    if not pairs:
        raise HTTPException(400, detail="Không có file nào còn tồn tại trên server để nén.")

    # 4. Create ZIP
    zip_name = f"renamed_{payload.batch_id[:8]}_{uuid.uuid4().hex[:6]}.zip"
    zip_path = os.path.join(_DOWNLOADS_DIR, zip_name)

    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for src_path, arc_name in pairs:
                zf.write(src_path, arc_name)
    except Exception as e:
        raise HTTPException(500, detail=f"Không thể tạo file ZIP: {e}")

    # 5. Schedule expiry (60 min)
    try:
        from app.tasks.video_tasks import delete_local_file
        _EXPIRY_SECONDS = 3600
        delete_local_file.apply_async((zip_path,), countdown=_EXPIRY_SECONDS)
    except Exception:
        pass

    zip_size = os.path.getsize(zip_path) / 1024 / 1024
    zip_url = f"/api/v1/download-local?filepath={zip_path}&filename={zip_name}"

    return {
        "success":      True,
        "zip_url":      zip_url,
        "filename":     zip_name,
        "file_count":   len(pairs),
        "file_size_mb": round(zip_size, 2),
    }
