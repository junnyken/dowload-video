"""
Video Merge Tool
=================
POST /api/v1/merge-clips

Merges 2–5 completed download jobs into a single MP4 via FFmpeg concat.
- stream copy when all clips share video+audio codecs
- re-encode to H.264+AAC when codecs differ
- max 5 clips, max 30 min total
- output has 60-min expiry
- requires optional auth (get_optional_user) to look up jobs
"""

import json
import os
import subprocess
from app.core.ffmpeg_budget import thread_args as _ffmpeg_threads
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_optional_user
from app.core.database import get_service_client as get_supabase_client
from app.main import limiter

router = APIRouter()


class MergeRequest(BaseModel):
    job_ids: List[str]        # 2–5 UUIDs
    output_name: Optional[str] = None


def _probe(path: str) -> dict:
    """Return duration (seconds) and video codec for a local file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,duration",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    data = json.loads(result.stdout)
    vstream = (data.get("streams") or [{}])[0]
    dur = float(
        data.get("format", {}).get("duration")
        or vstream.get("duration")
        or 0
    )
    vcodec = vstream.get("codec_name", "unknown")
    return {"duration": dur, "vcodec": vcodec}


def _probe_audio_codec(path: str) -> str:
    """Return audio codec name for the first audio stream of a local file."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "json", path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    return streams[0].get("codec_name", "unknown") if streams else "unknown"


@router.post("/merge-clips")
@limiter.limit("5/minute")
def merge_clips(
    request: Request,
    body: MergeRequest,
    user=Depends(get_optional_user),
):
    # Sync def (not async) is intentional: FastAPI/Starlette runs sync route
    # handlers in a worker thread pool. This handler calls blocking
    # subprocess.run() for up to 300s — as `async def` it would block the
    # whole event loop, freezing every other request on this uvicorn worker.
    # ── 1. Validate clip count ───────────────────────────────────────
    if len(body.job_ids) < 2:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "merge_too_few_clips", "message": "At least 2 job IDs are required."},
        )
    if len(body.job_ids) > 5:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "merge_too_many_clips", "message": "At most 5 job IDs are allowed."},
        )

    # ── 2. Fetch jobs from Supabase ──────────────────────────────────
    supabase = get_supabase_client()
    query = (
        supabase.table("download_jobs")
        .select("id, status, local_file_path, title, user_id")
        .in_("id", body.job_ids)
        .eq("status", "success")
    )
    result = query.execute()
    rows = result.data or []

    # Filter by ownership when authenticated
    if user:
        user_id = user["id"]
        rows = [r for r in rows if r.get("user_id") == user_id or r.get("user_id") is None]

    found_ids = {r["id"] for r in rows}
    missing = [jid for jid in body.job_ids if jid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "merge_jobs_not_found", "message": f"Jobs not found or not completed: {missing}"},
        )

    # ── 3. Verify files exist ────────────────────────────────────────
    valid_rows = [r for r in rows if r.get("local_file_path") and os.path.exists(r["local_file_path"])]
    if len(valid_rows) < 2:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "merge_too_few_clips", "message": "Fewer than 2 source files are available on disk."},
        )

    # ── 4. Probe each file ───────────────────────────────────────────
    file_infos = []
    for row in valid_rows:
        path = row["local_file_path"]
        info = _probe(path)
        info["acodec"] = _probe_audio_codec(path)
        info["path"] = path
        info["title"] = row.get("title", "")
        file_infos.append(info)

    # ── 5. Check total duration ──────────────────────────────────────
    total_duration = sum(f["duration"] for f in file_infos)
    if total_duration > 1800:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "merge_duration_exceeded", "message": "Total duration exceeds 30 minutes."},
        )

    # ── 6. Codec compatibility ───────────────────────────────────────
    vcodecs = [f["vcodec"] for f in file_infos]
    acodecs = [f["acodec"] for f in file_infos]
    use_copy = len(set(vcodecs)) == 1 and len(set(acodecs)) == 1

    # ── 7. Write FFmpeg concat list ──────────────────────────────────
    output_path = f"/app/downloads/merged_{uuid.uuid4().hex[:8]}.mp4"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as concat_file:
        concat_file_path = concat_file.name
        for fi in file_infos:
            concat_file.write(f"file '{fi['path']}'\n")

    # ── 8–10. Build & run FFmpeg ─────────────────────────────────────
    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file_path]
        if use_copy:
            cmd += ["-c", "copy"]
        else:
            # Re-encode path only — the -c copy branch above is remux, which
            # is I/O-bound and has no threads to cap.
            cmd += [
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
            ] + _ffmpeg_threads()
        cmd += ["-movflags", "+faststart", output_path]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail={"error_code": "merge_failed", "message": "FFmpeg merge failed.", "stderr": proc.stderr[-500:]},
            )
    finally:
        try:
            os.unlink(concat_file_path)
        except OSError:
            pass

    # ── 11–13. Build response ────────────────────────────────────────
    file_size_mb = os.path.getsize(output_path) / 1024 / 1024
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat().replace("+00:00", "Z")

    return {
        "merged_path": output_path,
        "title": body.output_name or "Merged Video",
        "duration_seconds": round(total_duration, 1),
        "file_size_mb": round(file_size_mb, 2),
        "expires_at": expires_at,
    }
