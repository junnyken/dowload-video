"""
Watermark Embed Tool
=====================
POST /api/v1/watermark-embed

Embeds a text or image watermark into a local video file using FFmpeg.
Supports preview (first 3 seconds) and full render.
"""

import base64
import os
import subprocess
from app.core.ffmpeg_budget import thread_args as _ffmpeg_threads
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.main import limiter

router = APIRouter()

# ── Path security ────────────────────────────────────────────────────
_DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)


def _safe_path(path: str) -> str:
    """Resolve path and confirm it is inside _DOWNLOADS_DIR. Returns abs path."""
    abs_path = os.path.realpath(path)
    downloads_real = os.path.realpath(_DOWNLOADS_DIR)
    if not abs_path.startswith(downloads_real):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "watermark_source_not_found"},
        )
    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "watermark_source_not_found"},
        )
    return abs_path


# ── FFmpeg position map ──────────────────────────────────────────────
_POSITIONS: dict[str, tuple[str, str]] = {
    "top-left":     ("10", "10"),
    "top-right":    ("W-w-10", "10"),
    "bottom-left":  ("10", "H-h-10"),
    "bottom-right": ("W-w-10", "H-h-10"),
    "center":       ("(W-w)/2", "(H-h)/2"),
}


# ── Helpers ──────────────────────────────────────────────────────────

def _get_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    try:
        return float(r.stdout.strip())
    except (ValueError, TypeError):
        return 0.0


def _get_output_duration(path: str) -> float:
    """Get duration of the rendered output file (may be preview-clipped)."""
    return _get_duration(path)


# ── Request schema ───────────────────────────────────────────────────

class WatermarkRequest(BaseModel):
    source_path: str
    watermark_type: str          # "text" or "image"

    # Text options
    text: Optional[str] = None
    font_size: int = 28
    font_color: str = "white"

    # Shared
    position: str = "bottom-right"
    opacity: float = 0.8

    # Image options
    image_base64: Optional[str] = None   # base64-encoded PNG, no data URL prefix

    # Mode
    preview_only: bool = False            # True → render first 3 s only


# ── Endpoint ─────────────────────────────────────────────────────────

@router.post("/watermark-embed")
@limiter.limit("5/minute")
def watermark_embed(
    request: Request,
    body: WatermarkRequest,
):
    # Sync def (not async) is intentional: FastAPI/Starlette runs sync route
    # handlers in a worker thread pool. The encode itself now runs on the media
    # worker, but this handler still blocks while waiting for that result (and
    # on the short ffprobe reads) — as `async def` it would block the whole
    # event loop, freezing every other request on this uvicorn worker.
    # ── Validate source path ─────────────────────────────────────────
    source_abs = _safe_path(body.source_path)

    # ── Validate watermark type ──────────────────────────────────────
    if body.watermark_type == "text":
        if not body.text:
            raise HTTPException(
                status_code=400,
                detail={"error_code": "watermark_missing_text"},
            )
    elif body.watermark_type == "image":
        if not body.image_base64:
            raise HTTPException(
                status_code=400,
                detail={"error_code": "watermark_missing_image"},
            )
        # Validate image size (max 200 KB)
        try:
            image_bytes = base64.b64decode(body.image_base64)
        except Exception:
            raise HTTPException(
                status_code=400,
                detail={"error_code": "watermark_missing_image", "message": "Invalid base64 data."},
            )
        if len(image_bytes) > 200 * 1024:
            raise HTTPException(
                status_code=400,
                detail={"error_code": "watermark_image_too_large", "message": "Image exceeds 200 KB."},
            )
    else:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "watermark_missing_text", "message": "watermark_type must be 'text' or 'image'."},
        )

    # ── Duration check (max 10 min for full render) ──────────────────
    duration = _get_duration(source_abs)
    if not body.preview_only and duration > 600:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "watermark_duration_exceeded", "message": "Video exceeds 10 minutes."},
        )

    # ── Resolve position ─────────────────────────────────────────────
    pos = body.position if body.position in _POSITIONS else "bottom-right"
    x, y = _POSITIONS[pos]

    # ── Output path ──────────────────────────────────────────────────
    stem = Path(source_abs).stem
    hex8 = uuid.uuid4().hex[:8]
    if body.preview_only:
        output_filename = f"wm_{stem}_{hex8}_preview.mp4"
    else:
        output_filename = f"wm_{stem}_{hex8}.mp4"
    output_path = os.path.join(_DOWNLOADS_DIR, output_filename)

    # ── Build FFmpeg command ─────────────────────────────────────────
    tmp_image_path: Optional[str] = None

    try:
        if body.watermark_type == "text":
            # Escape single-quotes in text for FFmpeg drawtext
            escaped_text = body.text.replace("'", r"\'").replace(":", r"\:")
            vf = (
                f"drawtext=text='{escaped_text}'"
                f":fontsize={body.font_size}"
                f":fontcolor={body.font_color}@{body.opacity}"
                f":x={x}:y={y}"
                f":shadowcolor=black@0.5:shadowx=2:shadowy=2"
            )
            cmd = ["ffmpeg", "-y"]
            if body.preview_only:
                cmd += ["-ss", "0", "-t", "3"]
            cmd += ["-i", source_abs, "-vf", vf, "-c:a", "copy"]

        else:
            # Write image to temp file
            tmp_image_path = os.path.join(_DOWNLOADS_DIR, f"wm_img_{hex8}.png")
            with open(tmp_image_path, "wb") as f:
                f.write(image_bytes)

            filter_complex = (
                f"[1:v]scale=200:-1,format=rgba,"
                f"colorchannelmixer=aa={body.opacity}[wm];"
                f"[0:v][wm]overlay={x}:{y}"
            )
            cmd = ["ffmpeg", "-y"]
            if body.preview_only:
                cmd += ["-ss", "0", "-t", "3"]
            cmd += [
                "-i", source_abs,
                "-i", tmp_image_path,
                "-filter_complex", filter_complex,
                "-c:a", "copy",
            ]

        # Both branches above re-encode video (drawtext / overlay filters), so
        # bound the thread count before handing off.
        cmd += _ffmpeg_threads()
        cmd += ["-movflags", "+faststart", output_path]

        # Hand the encode to the media worker instead of burning CPU inside
        # this API process. Validation, paths and the response below are
        # unchanged — only the place ffmpeg runs moved.
        from app.tasks.media_tasks import watermark_task, run_on_worker
        outcome = run_on_worker(watermark_task, cmd, timeout=300)
        if outcome["returncode"] != 0:
            raise HTTPException(
                status_code=500,
                detail={
                    "error_code": "watermark_render_failed",
                    "message": "FFmpeg watermark render failed.",
                    "stderr": outcome["stderr"],
                },
            )

    finally:
        if tmp_image_path and os.path.exists(tmp_image_path):
            try:
                os.unlink(tmp_image_path)
            except OSError:
                pass

    # ── Build response ───────────────────────────────────────────────
    output_duration = _get_output_duration(output_path)
    file_size_mb = os.path.getsize(output_path) / 1024 / 1024
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat().replace("+00:00", "Z")

    return {
        "output_path": output_path,
        "preview": body.preview_only,
        "duration_seconds": round(output_duration, 2),
        "file_size_mb": round(file_size_mb, 2),
        "expires_at": expires_at,
    }
