"""
Flow / Veo Visible-Logo Cleanup
================================
POST /upload          — upload local Flow/Veo video, extract preview frame
GET  /frame/{temp_id} — serve preview JPEG
POST /process         — FFmpeg delogo/crop/blur cleanup

SCOPE: visible on-frame logo/watermark only.
SynthID and other invisible AI watermarks are NOT affected by any operation here.
"""

import json
import logging
import os
import queue as _queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

import cv2
import numpy as np

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.main import limiter
from app.core.auth_middleware import get_optional_user
from app.core.entitlements import get_entitlement, check_feature
from app.core.local_download import file_fields, file_id_for, new_token, resolve_file_id, resolve_local_input, safe_file_id

router = APIRouter()
log = logging.getLogger(__name__)


async def _require_pro(
    _request: Request,
    user=Depends(get_optional_user),
) -> None:
    """FastAPI dependency: gate logo_inpaint behind Pro tier."""
    if user is None:
        raise HTTPException(status_code=401, detail="Đăng nhập để sử dụng tính năng này.")

    # API key users already have tier resolved; JWT users need a DB lookup.
    tier = user.get("tier")
    if not tier:
        try:
            from app.core.database import get_service_client
            db = get_service_client()
            ent = await get_entitlement(user["id"], db)
            tier = ent["tier"]
        except Exception as exc:
            # A lookup that FAILED is not the same fact as a user who IS on the
            # free plan, and `tier = "free"` here erased the difference. Every
            # Supabase blip — or a container booted without SUPABASE_URL — then
            # answered a paying subscriber with 402 "nâng cấp lên Pro": we blamed
            # their plan for our outage and told them to buy what they already
            # own. Deny either way (never fail open on a paid gate), but report
            # which of the two happened, with a status worth retrying.
            log.warning(
                "[flow-cleanup] entitlement lookup failed for user %s: %s",
                user.get("id"), exc,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "entitlement_check_failed",
                    "user_message": "Chưa kiểm tra được gói của bạn. Vui lòng thử lại sau ít phút.",
                    "retryable": True,
                },
            )

    if not check_feature(tier, "logo_inpaint"):
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "feature": "logo_inpaint",
                # Without this the client has no sentence to show: parseApiError
                # falls back to "Lỗi không xác định.", and the callers that did
                # `new Error(detail)` rendered the dict as "[object Object]".
                "user_message": "Xoá Logo / Watermark là tính năng của gói Pro. Nâng cấp để sử dụng.",
                "required_plan": "pro",
                "current_plan": tier,
                "retryable": False,
            },
        )

    try:
        if _request is not None and hasattr(_request, "state"):
            _request.state.flow_user_id = user.get("id")
    except Exception:
        pass


def _caller_user_id(request: Optional[Request] = None, user: Optional[Any] = None) -> Optional[str]:
    """Extract authenticated caller user_id from FastAPI dependency or request.state."""
    if isinstance(user, dict) and user.get("id"):
        return str(user["id"])
    if request is not None and hasattr(request, "state"):
        uid = getattr(request.state, "flow_user_id", None)
        if uid:
            return str(uid)
    return None


def _verify_job_owner(meta: dict, caller_id: Optional[str]) -> None:
    """Ensure the job belongs to the authenticated user; return 404 to avoid leaking job existence."""
    owner_id = meta.get("owner_user_id")
    if owner_id:
        if not caller_id or str(caller_id) != str(owner_id):
            raise HTTPException(status_code=404, detail="Job không còn tồn tại hoặc đã hết hạn.")


_DOWNLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)
_MAX_UPLOAD_MB = 500
_MAX_VIDEO_WIDTH = int(os.getenv("FLOW_MAX_VIDEO_WIDTH", "3840"))
_MAX_VIDEO_HEIGHT = int(os.getenv("FLOW_MAX_VIDEO_HEIGHT", "3840"))
_MAX_VIDEO_PIXELS = int(os.getenv("FLOW_MAX_VIDEO_PIXELS", str(3840 * 2160)))
_MAX_VIDEO_DURATION_SEC = float(os.getenv("FLOW_MAX_DURATION_SEC", "600"))
_MAX_INPAINT_DURATION_SEC = float(os.getenv("FLOW_MAX_INPAINT_DURATION_SEC", "60"))
_SEC_PER_MPX_FRAME = float(os.getenv("FLOW_SEC_PER_MPX_FRAME", "0.45"))
_MAX_RETRY_COUNT = int(os.getenv("FLOW_MAX_RETRY_COUNT", "3"))
_FLOW_TTL_SEC = int(os.getenv("FLOW_CLEANUP_TTL_SEC", "1200"))
_JOB_TIMEOUT_SEC = int(os.getenv("FLOW_JOB_TIMEOUT_SEC", "300"))
_FLOW_PREFIX = "flow_"
_META_LOCK = threading.RLock()

# Maps suitability level → self-documenting outcome label for metadata/admin
_SUITABILITY_OUTCOME = {
    "good":   "auto_clean_suitable",
    "manual": "manual_confirm_recommended",
    "crop":   "crop_fallback_recommended",
    "low":    "quality_risk_detected",
}

# Maps exception → structured error_code bucket
def _error_code(e: Exception) -> str:
    msg = str(e).lower()
    if isinstance(e, subprocess.TimeoutExpired) or "timeout" in msg or "time limit" in msg:
        return "processing_timeout"
    if "cancelled" in msg or "canceled" in msg:
        return "job_cancelled"
    if "ffmpeg" in msg or "returncode" in msg:
        return "ffmpeg_processing_failed"
    if "output file" in msg:
        return "output_render_failed"
    if "codec" in msg or "unsupported" in msg:
        return "unsupported_codec"
    if isinstance(e, OSError):
        return "source_ingest_failed"
    return "processing_failed"


def _validate_job_limits(input_path: str, info: dict, method: str = "natural") -> None:
    """Reject videos that exceed size, resolution, duration, or estimated processing time BEFORE dispatching to worker."""
    if os.path.isfile(input_path):
        size_bytes = os.path.getsize(input_path)
        if size_bytes > _MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail={
                    "error_code": "file_too_large",
                    "user_message": f"File vượt quá {_MAX_UPLOAD_MB}MB.",
                    "retryable": False,
                },
            )

    vw = int(info.get("width", 0) or 0)
    vh = int(info.get("height", 0) or 0)
    if vw <= 0 or vh <= 0:
        raise HTTPException(status_code=422, detail="Không đọc được video. Kiểm tra lại file.")
    if vw > _MAX_VIDEO_WIDTH or vh > _MAX_VIDEO_HEIGHT or (vw * vh) > _MAX_VIDEO_PIXELS:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "resolution_limit_exceeded",
                "user_message": "Độ phân giải video vượt quá giới hạn hỗ trợ (tối đa 4K 3840×2160).",
                "retryable": False,
            },
        )

    dur = float(info.get("duration", 0.0) or 0.0)
    fps = float(info.get("fps", 30.0) or 30.0)
    if method in ("natural", "telea", "telea_soft"):
        mpx_per_sec = (vw * vh / 1_000_000.0) * max(1.0, fps)
        est_processing_sec = dur * mpx_per_sec * _SEC_PER_MPX_FRAME
        max_dur_by_timeout = _JOB_TIMEOUT_SEC / max(0.05, mpx_per_sec * _SEC_PER_MPX_FRAME)
        effective_max_dur = max(5, int(min(_MAX_INPAINT_DURATION_SEC, max_dur_by_timeout)))
        if dur > _MAX_INPAINT_DURATION_SEC or est_processing_sec > _JOB_TIMEOUT_SEC:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "duration_limit_exceeded",
                    "user_message": (
                        f"Thời lượng video ({dur:.0f}s ở {vw}×{vh}@{fps:.0f}fps) ước tính cần "
                        f"~{int(round(est_processing_sec))}s xử lý, vượt giới hạn cho phép "
                        f"(tối đa ~{effective_max_dur}s / {int(_JOB_TIMEOUT_SEC)}s xử lý). "
                        f"Vui lòng cắt ngắn video hoặc chọn chế độ Cắt viền (Crop) / Làm mờ (Blur)."
                    ),
                    "estimated_processing_sec": round(est_processing_sec, 1),
                    "max_duration_sec": effective_max_dur,
                    "retryable": False,
                },
            )
    else:
        if dur > _MAX_VIDEO_DURATION_SEC:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "duration_limit_exceeded",
                    "user_message": f"Thời lượng video ({dur:.0f}s) vượt quá giới hạn cho phép ({int(_MAX_VIDEO_DURATION_SEC)}s).",
                    "max_duration_sec": int(_MAX_VIDEO_DURATION_SEC),
                    "retryable": False,
                },
            )


def _read_meta(work_dir: str) -> dict:
    meta_path = os.path.join(work_dir, "metadata.json")
    with _META_LOCK:
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                return json.load(mf)
        except Exception:
            return {}


def _write_meta(work_dir: str, updates: dict) -> dict:
    meta_path = os.path.join(work_dir, "metadata.json")
    with _META_LOCK:
        meta = _read_meta(work_dir)
        meta.update(updates)
        try:
            os.makedirs(work_dir, exist_ok=True)
            tmp_path = f"{meta_path}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp_path, "w", encoding="utf-8") as mf:
                json.dump(meta, mf)
            os.replace(tmp_path, meta_path)
        except Exception:
            try:
                with open(meta_path, "w", encoding="utf-8") as mf:
                    json.dump(meta, mf)
            except Exception:
                pass
        return meta


def _is_work_dir_expired(work_dir: str, ttl_sec: Optional[int] = None) -> bool:
    """Return True if a flow_* work_dir has exceeded its TTL and is not actively running."""
    if not os.path.isdir(work_dir):
        return True
    ttl = _FLOW_TTL_SEC if ttl_sec is None else ttl_sec
    if ttl <= 0:
        return True
    meta = _read_meta(work_dir)
    status = meta.get("status")
    now = time.time()
    # Active jobs have a longer safety window so a running worker isn't wiped mid-encode.
    if status in ("queued", "running"):
        try:
            mtime = os.path.getmtime(work_dir)
            return (now - mtime) > max(ttl, _JOB_TIMEOUT_SEC * 2)
        except OSError:
            return False

    ref_ts: Optional[float] = None
    for key in ("processed_at", "failed_at", "cancelled_at", "created_at"):
        val = meta.get(key)
        if isinstance(val, str) and val:
            try:
                ref_ts = datetime.fromisoformat(val).timestamp()
                break
            except Exception:
                pass
    if ref_ts is None:
        try:
            ref_ts = os.path.getmtime(work_dir)
        except OSError:
            return True
    return (now - ref_ts) > ttl


def _ensure_work_dir_fresh(work_dir: str, detail: str = "File không còn tồn tại hoặc đã hết hạn.") -> dict:
    """Verify work_dir exists and has not expired; delete and raise 404 if expired."""
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail=detail)
    if _is_work_dir_expired(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail=detail)
    return _read_meta(work_dir)


def _find_input_path(work_dir: str) -> Optional[str]:
    for ext in (".mp4", ".mov", ".webm", ".mkv"):
        c = os.path.join(work_dir, f"input{ext}")
        if os.path.exists(c):
            return c
    return None


def _result_url(temp_id: str, output_path: str, filename: str) -> str:
    from urllib.parse import quote
    return (f"/api/v1/flow-cleanup/result/{re.sub(r'[^a-f0-9]', '', temp_id)}/"
            f"{quote(file_id_for(output_path), safe='')}?filename={quote(filename, safe='')}")


def _safe_work_dir(temp_id: str) -> str:
    safe = re.sub(r"[^a-f0-9]", "", temp_id or "")
    if len(safe) < 8:
        raise HTTPException(status_code=400, detail="Invalid temp_id")
    return os.path.join(_DOWNLOAD_DIR, f"{_FLOW_PREFIX}{safe}")


def _probe(path: str) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True,
            timeout=30,
        )
        if r.returncode != 0:
            return {}
        for s in json.loads(r.stdout).get("streams", []):
            if s.get("codec_type") == "video":
                fps_raw = s.get("r_frame_rate", "30/1")
                try:
                    num, den = fps_raw.split("/")
                    fps = round(int(num) / max(int(den), 1), 2)
                except Exception:
                    fps = 30.0
                return {
                    "width": int(s.get("width", 0)),
                    "height": int(s.get("height", 0)),
                    "duration": round(float(s.get("duration", 0)), 2),
                    "fps": fps,
                }
    except Exception:
        pass
    return {}


def _extract_frame(input_path: str, frame_path: str) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", "0", "-i", input_path, "-vframes", "1", "-q:v", "2", frame_path],
        capture_output=True,
        timeout=30,
    )
    return r.returncode == 0 and os.path.exists(frame_path)


# ── TELEA inpainting helpers ─────────────────────────────────────────────────

def _build_mask(x: int, y: int, w: int, h: int, vw: int, vh: int) -> "np.ndarray":
    """Grayscale mask: white (255) = region to inpaint, black (0) = untouched."""
    mask = np.zeros((vh, vw), dtype=np.uint8)
    mask[y : y + h, x : x + w] = 255
    return mask


def _save_mask_png(mask: "np.ndarray", path: str) -> None:
    cv2.imwrite(path, mask)


def _telea_single_frame(input_path: str, mask: "np.ndarray", vw: int, vh: int) -> bytes:
    """Extract first frame and apply TELEA — used for fast frame-preview endpoint."""
    frame_size = vw * vh * 3
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", "0", "-i", input_path,
            "-vframes", "1",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}",
            "pipe:1",
        ],
        capture_output=True,
        timeout=20,
    )
    if len(r.stdout) < frame_size:
        raise ValueError("Frame extraction failed for preview.")
    frame = np.frombuffer(r.stdout[:frame_size], dtype=np.uint8).reshape((vh, vw, 3)).copy()
    inpainted = cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
    _, buf = cv2.imencode(".jpg", inpainted, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return bytes(buf)


def _telea_video(
    input_path: str,
    output_path: str,
    mask: "np.ndarray",
    x: int, y: int, w: int, h: int,
    vw: int, vh: int,
    fps: float,
    soft: bool = False,
    duration: float = 0.0,
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
) -> None:
    """
    Stream all frames through OpenCV TELEA inpainting and encode to output.
    Reader runs in a thread so the reader/writer pipes never deadlock.
    Audio is muxed from the original file in a second pass.
    """
    frame_size = vw * vh * 3
    fps_str = f"{fps:.3f}"
    output_path = os.path.abspath(output_path)
    video_only = output_path + ".noaudio.mp4"
    q: _queue.Queue = _queue.Queue(maxsize=8)
    reader_errors: list = []
    reader_proc_ref: list = [None]
    est_frames = max(1, int(round((duration or 0.0) * (fps or 30.0)))) if duration else 100

    def _reader() -> None:
        proc = subprocess.Popen(
            ["ffmpeg", "-i", input_path,
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{vw}x{vh}", "pipe:1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        reader_proc_ref[0] = proc
        try:
            while True:
                chunk = proc.stdout.read(frame_size)  # type: ignore[union-attr]
                if len(chunk) < frame_size:
                    break
                q.put(chunk)
        except Exception as exc:
            reader_errors.append(exc)
        finally:
            try:
                proc.stdout.close()  # type: ignore[union-attr]
            except Exception:
                pass
            proc.wait()
            q.put(None)  # sentinel

    write_proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}", "-r", fps_str,
            "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            video_only,
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    done_frames = 0
    write_errors: list = []
    try:
        while True:
            raw = q.get(timeout=120)
            if raw is None:
                break
            done_frames += 1
            if progress_cb is not None and (done_frames == 1 or done_frames % 5 == 0):
                if progress_cb(done_frames, max(est_frames, done_frames)) is False:
                    raise RuntimeError("Job cancelled by user")

            frame = np.frombuffer(raw, dtype=np.uint8).reshape((vh, vw, 3)).copy()
            inpainted = cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
            if soft:
                patch = inpainted[y : y + h, x : x + w].copy()
                blurred = cv2.GaussianBlur(patch, (5, 5), 1.5)
                inpainted[y : y + h, x : x + w] = cv2.addWeighted(blurred, 0.4, patch, 0.6, 0)
            write_proc.stdin.write(inpainted.tobytes())  # type: ignore[union-attr]
    except Exception as exc:
        write_errors.append(exc)
    finally:
        try:
            write_proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        if write_errors:
            if reader_proc_ref[0] is not None:
                try:
                    reader_proc_ref[0].terminate()
                except Exception:
                    pass
            try:
                while not q.empty():
                    q.get_nowait()
            except Exception:
                pass

    reader_thread.join(timeout=30)
    write_proc.wait(timeout=180)

    if reader_errors:
        if os.path.exists(video_only):
            try:
                os.remove(video_only)
            except Exception:
                pass
        raise reader_errors[0]
    if write_errors:
        if os.path.exists(video_only):
            try:
                os.remove(video_only)
            except Exception:
                pass
        raise write_errors[0]
    if write_proc.returncode != 0:
        err = write_proc.stderr.read().decode(errors="replace")[-400:]  # type: ignore[union-attr]
        raise ValueError(f"TELEA encode failed: {err}")
    if not os.path.isfile(video_only) or os.path.getsize(video_only) == 0:
        raise ValueError(f"TELEA inpaint output missing: {video_only}")

    # Pass 2: mux original audio stream back in
    r2 = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_only,
            "-i", input_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ],
        capture_output=True,
        timeout=120,
    )
    try:
        os.remove(video_only)
    except Exception:
        pass
    if r2.returncode != 0:
        raise ValueError(f"Audio mux failed: {r2.stderr.decode(errors='replace')[-300:]}")


# ── Standalone crop & blur helpers (extracted for testability & worker use) ──

def compute_crop_rect(x: int, y: int, w: int, h: int, vw: int, vh: int) -> Tuple[int, int, int, int]:
    """Compute even-dimensioned (cx, cy, cw, ch) crop box that trims the edge containing (x, y, w, h)."""
    cx, cy, cw, ch = 0, 0, vw, vh
    if x + w >= vw * 0.6:
        cw = x - 4
    elif x <= vw * 0.4:
        cx = x + w + 4
        cw = vw - cx
    if y + h >= vh * 0.6:
        ch = y - 4
    elif y <= vh * 0.4:
        cy = y + h + 4
        ch = vh - cy
    cw = max(int(cw) - int(cw) % 2, 2)
    ch = max(int(ch) - int(ch) % 2, 2)
    cx = max(0, min(int(cx), vw - cw))
    cy = max(0, min(int(cy), vh - ch))
    return cx, cy, cw, ch


def _crop_video(
    input_path: str,
    output_path: str,
    x: int, y: int, w: int, h: int,
    vw: int, vh: int,
    timeout: int = _JOB_TIMEOUT_SEC,
) -> Dict[str, int]:
    """Trim the edge containing the watermark via FFmpeg crop filter."""
    cx, cy, cw, ch = compute_crop_rect(x, y, w, h, vw, vh)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"crop={cw}:{ch}:{int(cx)}:{int(cy)}",
            "-c:a", "copy", output_path,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        err = (result.stderr or b"").decode(errors="replace")[-400:]
        raise ValueError(f"FFmpeg crop: {err}")
    return {"cx": cx, "cy": cy, "cw": cw, "ch": ch}


def _blur_video(
    input_path: str,
    output_path: str,
    x: int, y: int, w: int, h: int,
    timeout: int = _JOB_TIMEOUT_SEC,
) -> Dict[str, int]:
    """Cover the watermark region with FFmpeg boxblur overlay."""
    lr = max(1, min(20, min(int(w), int(h)) // 4 - 1))
    cr = max(1, min(10, min(int(w), int(h)) // 8 - 1))
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex",
            f"[0:v]split[orig][dup];"
            f"[dup]crop={w}:{h}:{x}:{y},boxblur={lr}:5:{cr}:5[blurred];"
            f"[orig][blurred]overlay={x}:{y}",
            "-c:a", "copy", output_path,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        err = (result.stderr or b"").decode(errors="replace")[-400:]
        raise ValueError(f"FFmpeg blur: {err}")
    return {"x": x, "y": y, "w": w, "h": h}


@router.post("/upload")
@limiter.limit("10/minute")
async def upload_flow_video(request: Request, file: UploadFile = File(...), _: None = Depends(_require_pro)):
    """
    Upload a local Flow/Veo video file for visible-logo cleanup.
    Returns temp_id, preview frame URL, and video metadata.

    Only visible on-frame logos are targeted.
    SynthID watermarking is NOT removed by this service.
    """
    fname = file.filename or "video.mp4"
    ext = os.path.splitext(fname)[1].lower()
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ MP4, MOV, WebM, MKV.")

    ct = (file.content_type or "").lower()
    if ct and not any(t in ct for t in ("video/", "application/octet-stream")):
        raise HTTPException(status_code=400, detail="Loại file không hợp lệ.")

    os.makedirs(_DOWNLOAD_DIR, exist_ok=True)
    temp_id = new_token()  # 128 bits — the work dir name is the capability
    work_dir = _safe_work_dir(temp_id)
    os.makedirs(work_dir, exist_ok=True)

    input_path = os.path.join(work_dir, f"input{ext}")
    frame_path = os.path.join(work_dir, "preview.jpg")
    written = 0
    max_bytes = _MAX_UPLOAD_MB * 1024 * 1024

    try:
        with open(input_path, "wb") as f:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File vượt quá {_MAX_UPLOAD_MB}MB.")
                f.write(chunk)
    except HTTPException:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as e:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Upload lỗi: {e}")

    info = _probe(input_path)
    try:
        _validate_job_limits(input_path, info, method="crop")
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    _extract_frame(input_path, frame_path)

    file_size_mb = round(written / (1024 * 1024), 2)

    # Write initial metadata for admin/QA inspection
    _write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": _caller_user_id(request),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "video_info": info,
        "file_size_mb": file_size_mb,
    })

    return {
        "success": True,
        "temp_id": temp_id,
        "job_id": temp_id,
        "preview_url": f"/api/v1/flow-cleanup/frame/{temp_id}",
        "video_info": info,
        "file_size_mb": file_size_mb,
    }


@router.get("/frame/{temp_id}")
async def get_preview_frame(temp_id: str):
    """Serve the extracted first-frame JPEG for the uploaded video."""
    work_dir = _safe_work_dir(temp_id)
    _ensure_work_dir_fresh(work_dir, detail="Preview không còn. Upload lại.")
    frame_path = os.path.join(work_dir, "preview.jpg")
    if not os.path.exists(frame_path):
        raise HTTPException(status_code=404, detail="Preview không còn. Upload lại.")
    return FileResponse(frame_path, media_type="image/jpeg")


class PreviewFrameRequest(BaseModel):
    temp_id: str
    preset: Optional[str] = None
    region: Optional[dict] = None   # {x,y,w,h} pixels
    expand_padding: int = 8


def _resolve_region(preset: Optional[str], region: Optional[dict], vw: int, vh: int) -> dict:
    """Shared region-resolution logic for both /preview-frame and /process."""
    if preset and preset != "custom":
        mx = max(int(vw * 0.16), 80)
        my = max(int(vh * 0.10), 40)
        px = max(int(vw * 0.02), 8)
        py = max(int(vh * 0.02), 8)
        preset_map = {
            "lower-right": {"x": vw - mx - px, "y": vh - my - py, "w": mx, "h": my},
            "lower-left":  {"x": px,            "y": vh - my - py, "w": mx, "h": my},
            "upper-right": {"x": vw - mx - px,  "y": py,           "w": mx, "h": my},
            "upper-left":  {"x": px,            "y": py,           "w": mx, "h": my},
        }
        if preset not in preset_map:
            raise HTTPException(status_code=400, detail=f"Preset không hợp lệ: {preset}")
        return preset_map[preset]
    if region:
        return region
    raise HTTPException(status_code=400, detail="Cần chọn vị trí logo.")


@router.post("/preview-frame")
@limiter.limit("30/minute")
async def preview_frame_cleanup(payload: PreviewFrameRequest, request: Request, _: None = Depends(_require_pro)):
    """
    TELEA-inpaint the first frame only and return a preview JPEG URL.
    Fast (<2s) — used for preview-first UX before committing to full processing.
    """
    work_dir = _safe_work_dir(payload.temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    _verify_job_owner(meta, _caller_user_id(request))
    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    info = _probe(input_path)
    vw = info.get("width", 1920)
    vh = info.get("height", 1080)

    raw = _resolve_region(payload.preset, payload.region, vw, vh)
    x, y, w, h = int(raw["x"]), int(raw["y"]), int(raw["w"]), int(raw["h"])
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > vw or y + h > vh:
        raise HTTPException(status_code=400, detail="Kích thước vùng không hợp lệ.")

    ep = max(0, int(payload.expand_padding))
    if ep > 0:
        x = max(0, x - ep);  y = max(0, y - ep)
        w = min(vw - x, w + ep * 2);  h = min(vh - y, h + ep * 2)

    try:
        from app.api import flow_inpaint as _fi
        # Same branch the FINAL will use (static SHIFTMAP vs moving temporal),
        # rendered on frame 0 → preview reflects final quality + crop advice.
        preview_bgr, pinfo = _fi.decide_and_preview(
            input_path, x, y, w, h, vw, vh,
            info.get("duration", 0) or 0.0, info.get("fps", 30.0) or 30.0,
        )
        _, buf = cv2.imencode(".jpg", preview_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        clean_path = os.path.join(work_dir, "preview_clean.jpg")
        with open(clean_path, "wb") as f:
            f.write(buf.tobytes())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview thất bại: {str(e)[:200]}")

    return {
        "preview_clean_url": f"/api/v1/flow-cleanup/preview-clean/{payload.temp_id}",
        "region": {"x": x, "y": y, "w": w, "h": h},
        **pinfo,   # strategy, motion, fill_method, quality, recommend_crop, reason
    }


@router.get("/preview-clean/{temp_id}")
async def get_preview_clean(temp_id: str):
    """Serve the TELEA-cleaned first-frame JPEG for preview-first UX."""
    work_dir = _safe_work_dir(temp_id)
    _ensure_work_dir_fresh(work_dir, detail="Preview chưa sẵn sàng. Upload và chọn vùng trước.")
    path = os.path.join(work_dir, "preview_clean.jpg")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Preview chưa sẵn sàng. Upload và chọn vùng trước.")
    return FileResponse(path, media_type="image/jpeg")


class ProcessRequest(BaseModel):
    temp_id: str
    preset: Optional[str] = "lower-right"      # lower-right|lower-left|upper-right|upper-left|custom
    region: Optional[dict] = None              # {x,y,w,h} pixels; used when preset="custom"
    method: str = "telea"                      # telea|telea_soft|crop|blur (delogo|delogo_soft = aliases)
    expand_padding: int = 0                    # px to expand region on all sides
    region_adjusted: bool = False              # user manually dragged the region handles
    suitability_level: Optional[str] = None    # echoed back for traceability
    requested_mode: Optional[str] = None       # mode system originally recommended before user override


def _mark_job_failed(
    temp_id: str,
    error: str,
    error_code: str,
    job_params: Optional[Dict[str, Any]] = None,
) -> dict:
    work_dir = _safe_work_dir(temp_id)
    prev = _read_meta(work_dir)
    updates = {
        "status": "failed",
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "error": str(error)[:300],
        "error_code": error_code,
        "retry_count": int(prev.get("retry_count", 0)),
    }
    if job_params:
        updates["requested_mode"] = job_params.get("requested_mode")
        updates["method"] = job_params.get("method")
        updates["job_params"] = job_params
    return _write_meta(work_dir, updates)


def _remove_partial_outputs(work_dir: str) -> None:
    if not os.path.isdir(work_dir):
        return
    for name in os.listdir(work_dir):
        if name.startswith("cleaned_") or name.endswith(".noaudio.mp4"):
            try:
                os.remove(os.path.join(work_dir, name))
            except Exception:
                pass


def _execute_flow_cleanup_job(
    temp_id: str,
    job_params: Dict[str, Any],
    celery_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Core video cleanup execution run either by Celery worker ('media' queue) or in-process fallback."""
    work_dir = _safe_work_dir(temp_id)
    prev_meta = _read_meta(work_dir)
    if prev_meta.get("status") == "cancelled":
        return {
            "success": False,
            "job_id": temp_id,
            "temp_id": temp_id,
            "status": "cancelled",
            "progress": int(prev_meta.get("progress", 0)),
        }

    input_path = _find_input_path(work_dir)
    if not input_path:
        _mark_job_failed(temp_id, "Upload không còn. Vui lòng upload lại.", "source_ingest_failed", job_params)
        raise FileNotFoundError("Upload không còn. Vui lòng upload lại.")

    info = job_params.get("video_info") or _probe(input_path)
    vw = int(info.get("width", 1920) or 1920)
    vh = int(info.get("height", 1080) or 1080)
    fps_val = float(info.get("fps", 30.0) or 30.0)
    dur = float(info.get("duration", 0.0) or 0.0)

    method = job_params["method"]
    region = job_params["region"]
    x, y, w, h = int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])
    preset = job_params.get("preset", "custom")
    region_adjusted = bool(job_params.get("region_adjusted", False))
    expand_padding = int(job_params.get("expand_padding", 0))
    suitability_level = job_params.get("suitability_level")
    requested_mode = job_params.get("requested_mode")
    retry_count = int(job_params.get("retry_count", prev_meta.get("retry_count", 0)))

    uid = new_token()
    output_path = os.path.join(work_dir, f"cleaned_{uid}.mp4")
    mask_path = os.path.join(work_dir, "mask.png")
    natural_stats = None

    _write_meta(work_dir, {
        "job_id": temp_id,
        "temp_id": temp_id,
        "status": "running",
        "progress": 5,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "celery_task_id": celery_task_id or prev_meta.get("celery_task_id"),
        "method": method,
        "preset": preset,
        "region": {"x": x, "y": y, "w": w, "h": h},
        "retry_count": retry_count,
        "job_params": job_params,
        "error": None,
        "error_code": None,
    })

    def _progress_cb(done_frames: int, total_frames: int) -> bool:
        cur = _read_meta(work_dir)
        if cur.get("status") == "cancelled":
            return False
        pct = min(95, max(5, int(5 + 90 * (done_frames / max(1, total_frames)))))
        if pct != cur.get("progress"):
            _write_meta(work_dir, {"progress": pct})
        return True

    try:
        if method == "natural":
            from app.api import flow_inpaint as _fi
            mask = _build_mask(x, y, w, h, vw, vh)
            _save_mask_png(mask, mask_path)
            natural_stats = _fi.process_video_natural(
                input_path, output_path,
                x, y, w, h, vw, vh,
                fps_val,
                duration=dur,
                progress_cb=_progress_cb,
            )
        elif method in ("telea", "telea_soft"):
            mask = _build_mask(x, y, w, h, vw, vh)
            _save_mask_png(mask, mask_path)
            _telea_video(
                input_path, output_path, mask,
                x, y, w, h, vw, vh,
                fps_val,
                soft=(method == "telea_soft"),
                duration=dur,
                progress_cb=_progress_cb,
            )
        elif method == "crop":
            _progress_cb(1, 2)
            _crop_video(input_path, output_path, x, y, w, h, vw, vh, timeout=_JOB_TIMEOUT_SEC)
            _progress_cb(2, 2)
        else:  # blur
            _progress_cb(1, 2)
            _blur_video(input_path, output_path, x, y, w, h, timeout=_JOB_TIMEOUT_SEC)
            _progress_cb(2, 2)

        if _read_meta(work_dir).get("status") == "cancelled":
            raise RuntimeError("Job cancelled by user")

        if not os.path.exists(output_path):
            raise ValueError("Output file không được tạo.")

        file_size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 2)

        # Slug filename from input base name
        base = os.path.splitext(os.path.basename(input_path))[0]
        from app.core.local_download import display_name
        slug = display_name(base, "flow_veo")   # keeps Vietnamese (27/10)
        suffix = "cropped" if method == "crop" else "logo_cleaned"
        output_filename = f"{slug}_{suffix}.mp4"

        # ── Structured result metadata ────────────────────────────
        result_type = "cropped_export" if method == "crop" else "cleaned_visible_logo"
        selected_mode = "crop_fallback" if method == "crop" else "visible_logo_cleanup"
        if preset != "custom":
            region_source = "user_selected"
        elif region_adjusted:
            region_source = "user_adjusted"
        else:
            region_source = "preset_default"

        warning_flags = []
        if dur > 300:
            warning_flags.append("long_clip")
        elif dur > 120:
            warning_flags.append("medium_length_clip")
        if fps_val > 48:
            warning_flags.append("high_fps")
        if vw < 640 or vh < 360:
            warning_flags.append("low_res")
        if method != "crop" and dur > 120 and fps_val <= 30:
            warning_flags.append("crop_preferred")

        suitability_outcome = _SUITABILITY_OUTCOME.get(suitability_level or "", None)
        ff = file_fields(output_path, "cleaned_file_id", "cleaned_path")
        dl_url = _result_url(temp_id, output_path, output_filename)

        result_payload = {
            "success": True,
            "job_id": temp_id,
            "temp_id": temp_id,
            "status": "done",
            "progress": 100,
            "job_kind": "visible_logo_cleanup",
            "result_type": result_type,
            "selected_mode": selected_mode,
            "requested_mode": requested_mode,
            "region_source": region_source,
            "warning_flags": warning_flags,
            "suitability_level": suitability_level,
            "suitability_outcome": suitability_outcome,
            "retry_count": retry_count,
            **ff,
            "download_url": dl_url,
            "filename": output_filename,
            "file_size_mb": file_size_mb,
            "method_used": method,
            "region": {"x": x, "y": y, "w": w, "h": h},
            "source_type": "flow_veo_cleanup",
            "visible_logo_cleanup": True,
        }

        _write_meta(work_dir, {
            "status": "completed",
            "job_status": "done",
            "progress": 100,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "job_kind": "visible_logo_cleanup",
            "method": method,
            "preset": preset,
            "region": {"x": x, "y": y, "w": w, "h": h},
            "result_type": result_type,
            "selected_mode": selected_mode,
            "requested_mode": requested_mode,
            "region_source": region_source,
            "region_adjusted": region_adjusted,
            "expand_padding": expand_padding,
            "cleanup_mode": method,
            "mask_file": os.path.basename(mask_path) if method in ("natural", "telea", "telea_soft") else None,
            "natural_stats": natural_stats,
            "suitability_level": suitability_level,
            "suitability_outcome": suitability_outcome,
            "warning_flags": warning_flags,
            "output_file_size_mb": file_size_mb,
            "output_filename": output_filename,
            "cleaned_file_id": ff.get("cleaned_file_id"),
            "download_url": dl_url,
            "retry_count": retry_count,
            "error": None,
            "error_code": None,
            "result_payload": result_payload,
        })

        return result_payload

    except Exception as e:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        if _read_meta(work_dir).get("status") == "cancelled" or "cancelled" in str(e).lower():
            _write_meta(work_dir, {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
                "error": "Đã hủy xử lý video.",
                "error_code": "job_cancelled",
            })
            return {
                "success": False,
                "job_id": temp_id,
                "temp_id": temp_id,
                "status": "cancelled",
                "progress": _read_meta(work_dir).get("progress", 0),
            }
        err_code = _error_code(e)
        err_msg = "Quá thời gian xử lý video." if err_code == "processing_timeout" else str(e)[:300]
        _mark_job_failed(temp_id, err_msg, err_code, job_params)
        raise


def _enqueue_cleanup_task(temp_id: str, job_params: Dict[str, Any]) -> Optional[str]:
    """Dispatch `inpaint_logo_task` onto the Celery 'media' queue if a worker is reachable or mocked."""
    from app.tasks.media_tasks import inpaint_logo_task
    from app.core.celery_app import celery_app

    apply_fn = getattr(inpaint_logo_task, "apply_async", None)
    is_mocked = (
        hasattr(apply_fn, "_mock_name")
        or getattr(apply_fn, "__qualname__", "Task.apply_async") != "Task.apply_async"
        or os.getenv("FLOW_CLEANUP_FORCE_ASYNC") == "1"
    )
    if is_mocked:
        res = inpaint_logo_task.apply_async(args=[temp_id, job_params], queue="media")
        return str(getattr(res, "id", temp_id))

    try:
        insp = celery_app.control.inspect(timeout=0.3)
        active = insp.ping() if insp else None
        if not active:
            return None
        res = inpaint_logo_task.apply_async(args=[temp_id, job_params], queue="media", retry=False)
        return str(getattr(res, "id", temp_id))
    except Exception as exc:
        log.warning("[flow-cleanup] media queue unavailable, falling back to in-process execution: %s", exc)
        return None


def _start_or_queue_cleanup_job(temp_id: str, job_params: Dict[str, Any], retry_count: int = 0) -> Dict[str, Any]:
    work_dir = _safe_work_dir(temp_id)
    job_params = dict(job_params)
    job_params["retry_count"] = retry_count

    _remove_partial_outputs(work_dir)
    _write_meta(work_dir, {
        "job_id": temp_id,
        "temp_id": temp_id,
        "status": "queued",
        "progress": 0,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "retry_count": retry_count,
        "method": job_params.get("method"),
        "preset": job_params.get("preset"),
        "region": job_params.get("region"),
        "requested_mode": job_params.get("requested_mode"),
        "suitability_level": job_params.get("suitability_level"),
        "job_params": job_params,
        "error": None,
        "error_code": None,
    })

    task_id = _enqueue_cleanup_task(temp_id, job_params)
    if task_id is not None:
        _write_meta(work_dir, {"celery_task_id": task_id})
        return {
            "success": True,
            "job_id": temp_id,
            "temp_id": temp_id,
            "celery_task_id": task_id,
            "status": "queued",
            "progress": 0,
            "retry_count": retry_count,
            "status_url": f"/api/v1/flow-cleanup/status/{temp_id}",
            "cancel_url": f"/api/v1/flow-cleanup/cancel/{temp_id}",
            "retry_url": f"/api/v1/flow-cleanup/retry/{temp_id}",
            "method_used": job_params.get("method"),
            "region": job_params.get("region"),
        }

    # In-process fallback is ONLY enabled when FLOW_CLEANUP_ALLOW_INPROCESS=1 (dev/unit tests).
    # In production, fail fast with HTTP 503 retryable instead of blocking the HTTP worker synchronously.
    if os.getenv("FLOW_CLEANUP_ALLOW_INPROCESS", "0") != "1":
        _mark_job_failed(
            temp_id,
            "Hàng đợi xử lý video tạm thời không khả dụng.",
            "queue_unavailable",
            job_params,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "queue_unavailable",
                "user_message": "Hệ thống xử lý video đang bận hoặc tạm thời gián đoạn. Vui lòng thử lại sau ít phút.",
                "retryable": True,
            },
        )

    try:
        return _execute_flow_cleanup_job(temp_id, job_params)
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Quá thời gian xử lý video.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xử lý thất bại: {str(e)}")


@router.post("/process")
@limiter.limit("5/minute")
def process_flow_cleanup(
    payload: ProcessRequest,
    request: Request,
    _: None = Depends(_require_pro),
):
    """
    Queue or run visible-logo cleanup on an uploaded Flow/Veo video via Celery 'media' queue.

    SCOPE: visible on-frame logo only.
    SynthID and invisible AI watermarks are NOT affected.
    """
    work_dir = _safe_work_dir(payload.temp_id)
    _prev_meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    _verify_job_owner(_prev_meta, _caller_user_id(request))
    retry_count = _prev_meta.get("retry_count", -1) + 1  # 0 = first attempt

    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    info = _probe(input_path)
    vw = info.get("width", 1920)
    vh = info.get("height", 1080)

    # Normalise method before limit check so duration cap matches method type
    _ALIAS = {"delogo": "natural", "delogo_soft": "natural",
              "telea": "telea", "telea_soft": "telea_soft"}
    method = payload.method if payload.method in ("natural", "telea", "telea_soft", "crop", "blur") else \
             _ALIAS.get(payload.method, "natural")

    # Reject jobs exceeding file size, resolution, or duration limits BEFORE entering worker
    _validate_job_limits(input_path, info, method=method)

    # Resolve region — shared helper used by /preview-frame and /process
    raw_region = _resolve_region(payload.preset, payload.region, vw, vh)
    x, y, w, h = int(raw_region["x"]), int(raw_region["y"]), int(raw_region["w"]), int(raw_region["h"])
    if w <= 0 or h <= 0:
        raise HTTPException(status_code=400, detail="Kích thước vùng không hợp lệ.")
    if x < 0 or y < 0 or x + w > vw or y + h > vh:
        raise HTTPException(status_code=400, detail="Vùng nằm ngoài khung hình.")

    # Expand region by expand_padding (clamp to frame) — not applied to crop
    if payload.expand_padding > 0 and method != "crop":
        ep = max(0, int(payload.expand_padding))
        x = max(0, x - ep);  y = max(0, y - ep)
        w = min(vw - x, w + ep * 2);  h = min(vh - y, h + ep * 2)

    job_params = {
        "method": method,
        "preset": payload.preset,
        "region": {"x": x, "y": y, "w": w, "h": h},
        "expand_padding": payload.expand_padding,
        "region_adjusted": payload.region_adjusted,
        "suitability_level": payload.suitability_level,
        "requested_mode": payload.requested_mode,
        "video_info": info,
    }
    return _start_or_queue_cleanup_job(payload.temp_id, job_params, retry_count=retry_count)


@router.get("/status/{job_id}")
async def get_flow_cleanup_status(job_id: str, request: Request = None, _: None = Depends(_require_pro)):
    """Return status, progress (0-100), and result fields for a queued/running/done/failed/cancelled job."""
    work_dir = _safe_work_dir(job_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Job không còn tồn tại hoặc đã hết hạn.")
    if not meta:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông tin job.")
    _verify_job_owner(meta, _caller_user_id(request))

    raw_status = meta.get("status", "uploaded")
    norm_status = "done" if raw_status in ("completed", "done") else raw_status
    progress = int(meta.get("progress", 100 if norm_status == "done" else 0))

    resp: Dict[str, Any] = {
        "success": norm_status not in ("failed", "cancelled"),
        "job_id": job_id,
        "temp_id": job_id,
        "status": norm_status,
        "progress": progress,
        "retry_count": int(meta.get("retry_count", 0)),
        "method_used": meta.get("method"),
        "region": meta.get("region"),
        "error": meta.get("error"),
        "error_code": meta.get("error_code"),
    }

    if norm_status == "done":
        saved_payload = meta.get("result_payload")
        if isinstance(saved_payload, dict):
            resp.update(saved_payload)
            resp["status"] = "done"
            resp["progress"] = 100
        else:
            cleaned_id = meta.get("cleaned_file_id")
            if cleaned_id:
                real = resolve_file_id(cleaned_id, work_dir)
                if real and os.path.isfile(real):
                    resp.update(file_fields(real, "cleaned_file_id", "cleaned_path"))
            resp.update({
                "download_url": meta.get("download_url"),
                "filename": meta.get("output_filename"),
                "file_size_mb": meta.get("output_file_size_mb"),
                "result_type": meta.get("result_type"),
                "selected_mode": meta.get("selected_mode"),
                "requested_mode": meta.get("requested_mode"),
                "warning_flags": meta.get("warning_flags", []),
                "suitability_level": meta.get("suitability_level"),
                "suitability_outcome": meta.get("suitability_outcome"),
            })
    return resp


@router.post("/cancel/{job_id}")
async def cancel_flow_cleanup_job(job_id: str, request: Request = None, _: None = Depends(_require_pro)):
    """Cancel a queued or running Flow cleanup job and clean up partial outputs."""
    work_dir = _safe_work_dir(job_id)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Job không tồn tại hoặc đã hết hạn.")

    meta = _read_meta(work_dir)
    _verify_job_owner(meta, _caller_user_id(request))
    task_id = meta.get("celery_task_id")
    if task_id:
        try:
            from app.core.celery_app import celery_app
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            pass

    _remove_partial_outputs(work_dir)
    _write_meta(work_dir, {
        "status": "cancelled",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "error": "Đã hủy xử lý video.",
        "error_code": "job_cancelled",
    })
    return {
        "success": True,
        "job_id": job_id,
        "temp_id": job_id,
        "status": "cancelled",
        "progress": int(meta.get("progress", 0)),
    }


@router.post("/retry/{job_id}")
@limiter.limit("5/minute")
def retry_flow_cleanup_job(job_id: str, request: Request, _: None = Depends(_require_pro)):
    """Retry a failed or cancelled Flow cleanup job using its stored job_params."""
    work_dir = _safe_work_dir(job_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Job không còn tồn tại hoặc đã hết hạn.")
    _verify_job_owner(meta, _caller_user_id(request))
    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="File gốc không còn tồn tại. Vui lòng upload lại.")

    current_retries = int(meta.get("retry_count", 0))
    if current_retries >= _MAX_RETRY_COUNT:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "max_retries_exceeded",
                "user_message": f"Đã vượt quá số lần thử lại tối đa ({_MAX_RETRY_COUNT} lần).",
                "retry_count": current_retries,
                "max_retries": _MAX_RETRY_COUNT,
                "retryable": False,
            },
        )

    job_params = meta.get("job_params")
    if not isinstance(job_params, dict) or not job_params.get("region"):
        raise HTTPException(status_code=400, detail="Không có cấu hình job trước đó để thử lại.")

    info = job_params.get("video_info") or _probe(input_path)
    _validate_job_limits(input_path, info, method=job_params.get("method", "natural"))

    retry_count = current_retries + 1
    return _start_or_queue_cleanup_job(job_id, job_params, retry_count=retry_count)


@router.get("/result/{temp_id}/{file_id}")
async def get_cleaned_result(
    temp_id: str,
    file_id: str,
    filename: str = "cleaned.mp4",
    request: Request = None,
    user=Depends(get_optional_user),
):
    """Serve a cleaned output of a session by its file id (cleaned_*.mp4)."""
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="File không còn tồn tại hoặc đã hết hạn.")
    _verify_job_owner(meta, _caller_user_id(request, user))
    if not file_id.startswith("cleaned_"):
        raise HTTPException(status_code=400, detail="Invalid file.")
    real = resolve_file_id(file_id, work_dir)
    if real is None:
        raise HTTPException(status_code=400, detail="Invalid file.")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="File không còn tồn tại hoặc đã hết hạn.")
    return FileResponse(real, media_type="video/mp4", filename=filename)


class FromLocalRequest(BaseModel):
    local_path: str
    title: Optional[str] = None


@router.post("/from-local")
@limiter.limit("10/minute")
def cleanup_from_local_path(
    payload: FromLocalRequest,
    request: Request,
    _: None = Depends(_require_pro),
):
    # Sync def (not async) is intentional — see process_flow_cleanup above.
    """
    Start a logo-cleanup session from an already-downloaded video file.
    Accepts a server-side local_path (must be inside downloads dir).
    """
    # file_id or legacy path → shared validator (app.core.local_download)
    abs_path = resolve_local_input(payload.local_path, _DOWNLOAD_DIR)
    if abs_path is None:
        raise HTTPException(status_code=403, detail="Đường dẫn không hợp lệ.")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File không còn tồn tại hoặc đã hết hạn. Tải lại video trước.")

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ MP4, MOV, WebM, MKV.")

    if os.path.getsize(abs_path) > _MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File vượt quá {_MAX_UPLOAD_MB}MB.")

    temp_id = new_token()  # 128 bits — the work dir name is the capability
    work_dir = _safe_work_dir(temp_id)
    os.makedirs(work_dir, exist_ok=True)

    input_path = os.path.join(work_dir, f"input{ext}")
    frame_path  = os.path.join(work_dir, "preview.jpg")

    try:
        shutil.copy2(abs_path, input_path)
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Không thể copy file: {e}")

    info = _probe(input_path)
    try:
        _validate_job_limits(input_path, info, method="crop")
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    _extract_frame(input_path, frame_path)
    file_size_mb = round(os.path.getsize(input_path) / (1024 * 1024), 2)

    _write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": _caller_user_id(request),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "source": "from_local",
        "source_file_id": safe_file_id(abs_path),  # never the server path
        "title": payload.title or "",
        "video_info": info,
        "file_size_mb": file_size_mb,
    })

    return {
        "success": True,
        "temp_id": temp_id,
        "job_id": temp_id,
        "preview_url": f"/api/v1/flow-cleanup/frame/{temp_id}",
        "video_info": info,
        "file_size_mb": file_size_mb,
    }


class NoLogoRequest(BaseModel):
    temp_id: str
    suitability_level: Optional[str] = None


@router.post("/no-logo")
async def record_no_logo(payload: NoLogoRequest):
    """
    Record that user confirmed no visible on-frame logo was present.
    Best-effort — never blocks the user flow.

    SCOPE: visible logo only. Does not imply absence of SynthID or any invisible watermark.
    """
    try:
        work_dir = _safe_work_dir(payload.temp_id)
        if not os.path.isdir(work_dir):
            return {"recorded": False, "reason": "job_not_found"}
        meta_path = os.path.join(work_dir, "metadata.json")
        _meta: dict = {}
        try:
            with open(meta_path) as mf:
                _meta = json.load(mf)
        except Exception:
            pass
        _meta.update({
            "status": "no_visible_logo",
            "result_type": "original_passthrough_no_logo",
            "job_kind": "visible_logo_cleanup",
            "no_logo_confirmed_at": datetime.now(timezone.utc).isoformat(),
            "suitability_level": payload.suitability_level,
            "suitability_outcome": _SUITABILITY_OUTCOME.get(payload.suitability_level or "", None),
        })
        with open(meta_path, "w") as mf:
            json.dump(_meta, mf)
    except Exception:
        pass
    return {"recorded": True, "result_type": "original_passthrough_no_logo"}
