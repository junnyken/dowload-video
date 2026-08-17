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
import os
import queue as _queue
import re
import subprocess
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.main import limiter
from app.core.auth_middleware import get_optional_user
from app.core.entitlements import get_entitlement, check_feature

router = APIRouter()


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
        except Exception:
            tier = "free"

    if not check_feature(tier, "logo_inpaint"):
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "feature": "logo_inpaint",
                "required_plan": "pro",
                "current_plan": tier,
            },
        )


_DOWNLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)
_MAX_UPLOAD_MB = 500
_FLOW_PREFIX = "flow_"

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
    if "ffmpeg" in msg or "returncode" in msg:
        return "ffmpeg_processing_failed"
    if "output file" in msg:
        return "output_render_failed"
    if "codec" in msg or "unsupported" in msg:
        return "unsupported_codec"
    if isinstance(e, OSError):
        return "source_ingest_failed"
    return "processing_failed"


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
) -> None:
    """
    Stream all frames through OpenCV TELEA inpainting and encode to output.
    Reader runs in a thread so the reader/writer pipes never deadlock.
    Audio is muxed from the original file in a second pass.
    """
    frame_size = vw * vh * 3
    fps_str = f"{fps:.3f}"
    video_only = output_path + ".noaudio.mp4"
    q: _queue.Queue = _queue.Queue(maxsize=8)
    reader_errors: list = []

    def _reader() -> None:
        proc = subprocess.Popen(
            ["ffmpeg", "-i", input_path,
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{vw}x{vh}", "pipe:1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            while True:
                chunk = proc.stdout.read(frame_size)  # type: ignore[union-attr]
                if len(chunk) < frame_size:
                    break
                q.put(chunk)
        except Exception as exc:
            reader_errors.append(exc)
        finally:
            proc.stdout.close()  # type: ignore[union-attr]
            proc.wait()
            q.put(None)  # sentinel

    write_proc = subprocess.Popen(
        [
            "ffmpeg", "-y",
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

    write_errors: list = []
    try:
        while True:
            raw = q.get(timeout=120)
            if raw is None:
                break
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
        write_proc.stdin.close()  # type: ignore[union-attr]

    reader_thread.join(timeout=30)
    write_proc.wait(timeout=180)

    if reader_errors:
        raise reader_errors[0]
    if write_errors:
        raise write_errors[0]
    if write_proc.returncode != 0:
        err = write_proc.stderr.read().decode(errors="replace")[-400:]  # type: ignore[union-attr]
        raise ValueError(f"TELEA encode failed: {err}")

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
    temp_id = uuid.uuid4().hex
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
    if not info.get("width"):
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Không đọc được video. Kiểm tra lại file.")

    _extract_frame(input_path, frame_path)

    file_size_mb = round(written / (1024 * 1024), 2)

    # Write initial metadata for admin/QA inspection
    try:
        with open(os.path.join(work_dir, "metadata.json"), "w") as _mf:
            json.dump({
                "temp_id": temp_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "uploaded",
                "video_info": info,
                "file_size_mb": file_size_mb,
            }, _mf)
    except Exception:
        pass

    return {
        "success": True,
        "temp_id": temp_id,
        "preview_url": f"/api/v1/flow-cleanup/frame/{temp_id}",
        "video_info": info,
        "file_size_mb": file_size_mb,
    }


@router.get("/frame/{temp_id}")
async def get_preview_frame(temp_id: str):
    """Serve the extracted first-frame JPEG for the uploaded video."""
    work_dir = _safe_work_dir(temp_id)
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
    input_path = None
    for ext in (".mp4", ".mov", ".webm", ".mkv"):
        c = os.path.join(work_dir, f"input{ext}")
        if os.path.exists(c):
            input_path = c
            break
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


@router.post("/process")
@limiter.limit("5/minute")
def process_flow_cleanup(
    payload: ProcessRequest,
    request: Request,
    _: None = Depends(_require_pro),
):
    # Sync def (not async) is intentional: FastAPI/Starlette runs sync route
    # handlers in a worker thread pool. This handler runs blocking FFmpeg/
    # OpenCV work for up to 300s — as `async def` it would block the whole
    # event loop, freezing every other request on this uvicorn worker.
    """
    Visible-logo cleanup on an uploaded Flow/Veo video.

    Methods:
      telea      — OpenCV TELEA inpainting (default; best for corner logos on clean/gradient bg)
      telea_soft — TELEA + Gaussian edge softening for smoother patch boundary
      crop       — trim the edge containing the logo (changes video dimensions; cleanest result)
      blur       — boxblur cover (fast but leaves a visible patch)
      delogo / delogo_soft — legacy aliases → routed to telea / telea_soft

    SCOPE: visible on-frame logo only.
    SynthID and invisible AI watermarks are NOT affected.
    """
    work_dir = _safe_work_dir(payload.temp_id)

    # Read existing metadata for retry_count tracking
    _meta_path = os.path.join(work_dir, "metadata.json")
    _prev_meta: dict = {}
    try:
        with open(_meta_path) as _mf:
            _prev_meta = json.load(_mf)
    except Exception:
        pass
    retry_count = _prev_meta.get("retry_count", -1) + 1  # 0 = first attempt

    input_path = None
    for ext in (".mp4", ".mov", ".webm", ".mkv"):
        c = os.path.join(work_dir, f"input{ext}")
        if os.path.exists(c):
            input_path = c
            break
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    info = _probe(input_path)
    vw = info.get("width", 1920)
    vh = info.get("height", 1080)

    # Resolve region — shared helper used by /preview-frame and /process
    raw_region = _resolve_region(payload.preset, payload.region, vw, vh)
    x, y, w, h = int(raw_region["x"]), int(raw_region["y"]), int(raw_region["w"]), int(raw_region["h"])
    if w <= 0 or h <= 0:
        raise HTTPException(status_code=400, detail="Kích thước vùng không hợp lệ.")
    if x < 0 or y < 0 or x + w > vw or y + h > vh:
        raise HTTPException(status_code=400, detail="Vùng nằm ngoài khung hình.")

    # Normalise method. The natural (SHIFTMAP/temporal) engine is the new
    # default; telea/telea_soft kept as a fast fallback. delogo* are legacy
    # aliases, now pointed at the natural engine for a better result.
    _ALIAS = {"delogo": "natural", "delogo_soft": "natural",
              "telea": "telea", "telea_soft": "telea_soft"}
    method = payload.method if payload.method in ("natural", "telea", "telea_soft", "crop", "blur") else \
             _ALIAS.get(payload.method, "natural")

    # Expand region by expand_padding (clamp to frame) — not applied to crop
    if payload.expand_padding > 0 and method != "crop":
        ep = max(0, int(payload.expand_padding))
        x = max(0, x - ep);  y = max(0, y - ep)
        w = min(vw - x, w + ep * 2);  h = min(vh - y, h + ep * 2)

    uid = uuid.uuid4().hex[:8]
    output_path = os.path.join(work_dir, f"cleaned_{uid}.mp4")
    mask_path = os.path.join(work_dir, "mask.png")
    natural_stats = None

    try:
        if method == "natural":
            from app.api import flow_inpaint as _fi
            mask = _build_mask(x, y, w, h, vw, vh)
            _save_mask_png(mask, mask_path)
            natural_stats = _fi.process_video_natural(
                input_path, output_path,
                x, y, w, h, vw, vh,
                info.get("fps", 30.0),
                duration=info.get("duration", 0.0) or 0.0,
            )
        elif method in ("telea", "telea_soft"):
            mask = _build_mask(x, y, w, h, vw, vh)
            _save_mask_png(mask, mask_path)
            _telea_video(
                input_path, output_path, mask,
                x, y, w, h, vw, vh,
                info.get("fps", 30.0),
                soft=(method == "telea_soft"),
            )
        elif method == "crop":
            cx, cy, cw, ch = 0, 0, vw, vh
            if x + w >= vw * 0.6:   cw = x - 4
            elif x <= vw * 0.4:     cx = x + w + 4; cw = vw - cx
            if y + h >= vh * 0.6:   ch = y - 4
            elif y <= vh * 0.4:     cy = y + h + 4; ch = vh - cy
            cw = max(int(cw) - int(cw) % 2, 2)
            ch = max(int(ch) - int(ch) % 2, 2)
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", input_path,
                 "-vf", f"crop={cw}:{ch}:{int(cx)}:{int(cy)}",
                 "-c:a", "copy", output_path],
                capture_output=True, timeout=300,
            )
            if result.returncode != 0:
                raise ValueError(f"FFmpeg crop: {result.stderr.decode(errors='replace')[-400:]}")
        else:  # blur
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", input_path,
                 "-filter_complex",
                 f"[0:v]split[orig][dup];"
                 f"[dup]crop={w}:{h}:{x}:{y},boxblur=20:5[blurred];"
                 f"[orig][blurred]overlay={x}:{y}",
                 "-c:a", "copy", output_path],
                capture_output=True, timeout=300,
            )
            if result.returncode != 0:
                raise ValueError(f"FFmpeg blur: {result.stderr.decode(errors='replace')[-400:]}")

        if not os.path.exists(output_path):
            raise ValueError("Output file không được tạo.")

        file_size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 2)

        # Slug filename from input base name
        base = os.path.splitext(os.path.basename(input_path))[0]
        normalized = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
        slug = re.sub(r"[^\w\-]", "_", normalized).strip("_") or "flow_veo"
        suffix = "cropped" if method == "crop" else "logo_cleaned"
        output_filename = f"{slug}_{suffix}.mp4"

        # ── Structured result metadata ────────────────────────────
        result_type = "cropped_export" if method == "crop" else "cleaned_visible_logo"
        selected_mode = "crop_fallback" if method == "crop" else "visible_logo_cleanup"
        if payload.preset != "custom":
            region_source = "user_selected"
        elif payload.region_adjusted:
            region_source = "user_adjusted"
        else:
            region_source = "preset_default"

        # Warning flags from video metadata (mirrors frontend suitability model)
        dur = info.get("duration", 0)
        fps_val = info.get("fps", 30)
        warning_flags = []
        if dur > 300:
            warning_flags.append("long_clip")
        elif dur > 120:
            warning_flags.append("medium_length_clip")
        if fps_val > 48:
            warning_flags.append("high_fps")
        if info.get("width", 1920) < 640 or info.get("height", 1080) < 360:
            warning_flags.append("low_res")
        # Delogo on long clip → crop would have been cleaner
        if method != "crop" and dur > 120 and fps_val <= 30:
            warning_flags.append("crop_preferred")

        suitability_outcome = _SUITABILITY_OUTCOME.get(payload.suitability_level or "", None)

        # Update metadata.json with process result for admin/QA inspection
        try:
            _meta = dict(_prev_meta)
            _meta.update({
                "status": "completed",
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "job_kind": "visible_logo_cleanup",
                "method": method,
                "preset": payload.preset,
                "region": {"x": x, "y": y, "w": w, "h": h},
                "result_type": result_type,
                "selected_mode": selected_mode,
                "requested_mode": payload.requested_mode,
                "region_source": region_source,
                "region_adjusted": payload.region_adjusted,
                "expand_padding": payload.expand_padding,
                "cleanup_mode": method,
                "mask_path": mask_path if method in ("natural", "telea", "telea_soft") else None,
                "natural_stats": natural_stats,
                "suitability_level": payload.suitability_level,
                "suitability_outcome": suitability_outcome,
                "warning_flags": warning_flags,
                "output_file_size_mb": file_size_mb,
                "output_filename": output_filename,
                "retry_count": retry_count,
                "error_code": None,
            })
            with open(_meta_path, "w") as _mf:
                json.dump(_meta, _mf)
        except Exception:
            pass

        return {
            "success": True,
            "job_kind": "visible_logo_cleanup",
            "result_type": result_type,
            "selected_mode": selected_mode,
            "requested_mode": payload.requested_mode,
            "region_source": region_source,
            "warning_flags": warning_flags,
            "suitability_level": payload.suitability_level,
            "suitability_outcome": suitability_outcome,
            "retry_count": retry_count,
            "cleaned_path": output_path,
            "filename": output_filename,
            "file_size_mb": file_size_mb,
            "method_used": method,
            "region": {"x": x, "y": y, "w": w, "h": h},
            # kept for backward compat
            "source_type": "flow_veo_cleanup",
            "visible_logo_cleanup": True,
        }

    except HTTPException:
        raise
    except Exception as e:
        error_code = _error_code(e)
        # Update metadata with failure info
        try:
            _meta = dict(_prev_meta)
            _meta.update({
                "status": "failed",
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e)[:300],
                "error_code": error_code,
                "retry_count": retry_count,
                "requested_mode": payload.requested_mode,
                "method": method,
            })
            with open(_meta_path, "w") as _mf:
                json.dump(_meta, _mf)
        except Exception:
            pass
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Xử lý thất bại: {str(e)}")


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
    import shutil as _shutil

    # Validate path is inside downloads dir (path traversal guard)
    abs_path = os.path.realpath(payload.local_path)
    abs_dl   = os.path.realpath(_DOWNLOAD_DIR)
    if not abs_path.startswith(abs_dl + os.sep) and abs_path != abs_dl:
        raise HTTPException(status_code=403, detail="Đường dẫn không hợp lệ.")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File không còn tồn tại hoặc đã hết hạn. Tải lại video trước.")

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ MP4, MOV, WebM, MKV.")

    temp_id = uuid.uuid4().hex
    work_dir = _safe_work_dir(temp_id)
    os.makedirs(work_dir, exist_ok=True)

    input_path = os.path.join(work_dir, f"input{ext}")
    frame_path  = os.path.join(work_dir, "preview.jpg")

    try:
        _shutil.copy2(abs_path, input_path)
    except Exception as e:
        _shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Không thể copy file: {e}")

    info = _probe(input_path)
    if not info.get("width"):
        _shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail="Không đọc được video.")

    _extract_frame(input_path, frame_path)
    file_size_mb = round(os.path.getsize(input_path) / (1024 * 1024), 2)

    try:
        with open(os.path.join(work_dir, "metadata.json"), "w") as mf:
            json.dump({
                "temp_id": temp_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "uploaded",
                "source": "from_local",
                "source_path": abs_path,
                "title": payload.title or "",
                "video_info": info,
                "file_size_mb": file_size_mb,
            }, mf)
    except Exception:
        pass

    return {
        "success": True,
        "temp_id": temp_id,
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
