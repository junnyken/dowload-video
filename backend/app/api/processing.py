"""
Post-Processing Suite 2.0 — Phase 22
======================================
Unified post-processing endpoints that operate on either a remote URL or a
server-side local file path already present in the downloads directory.

Endpoints:
  POST /process/extract-audio   — extract audio track as MP3/M4A
  POST /process/mp4-loop        — silent loopable MP4 clip (trim + scale, no audio)
  POST /process/subtitle        — download subtitles only (SRT/VTT/TXT)
  POST /process/burn-subtitle   — burn-in subtitles onto a local video
  POST /process/frame-thumb     — extract a single JPEG frame at a timestamp
  POST /process/package-zip     — package multiple local files into a ZIP archive

All outputs:
  - Are placed in the downloads directory.
  - Are scheduled for cleanup after 20 minutes via delete_local_file Celery task.
  - Return {"success": True, "download_url": "...", "output_path": "...",
             "file_size_mb": ..., "expires_in_seconds": 1200}
  - Are rate-limited at 10/minute per IP.
  - Apply path-traversal guard on every local_path input.
"""

import ipaddress
import os
import re
import subprocess
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.main import limiter

router = APIRouter()

# ── Shared constants ─────────────────────────────────────────────────

_DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)
_CLEANUP_COUNTDOWN = 20 * 60  # 20 minutes in seconds
_EXPIRES_IN_SECONDS = 1200     # 20 minutes (returned to client)

# ── Helpers ──────────────────────────────────────────────────────────

def _safe_download_dir() -> str:
    """Ensure downloads dir exists and return it."""
    os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
    return _DOWNLOADS_DIR


def _guard_local_path(path: str) -> str:
    """
    Resolve path and confirm it is inside _DOWNLOADS_DIR.
    Raises HTTPException 400/404 on failure. Returns realpath.
    """
    real_dl = os.path.realpath(_DOWNLOADS_DIR)
    real_p  = os.path.realpath(path)
    if not real_p.startswith(real_dl):
        raise HTTPException(status_code=400, detail="Invalid local_path: path traversal not allowed")
    if not os.path.exists(real_p):
        raise HTTPException(status_code=404, detail="Local file not found or expired")
    return real_p


def _assert_safe_url(url: str) -> None:
    """
    Reject non-http(s) schemes and any URL reaching a non-public address.
    Shared implementation — see app.core.ssrf_guard.
    """
    from app.core.ssrf_guard import assert_safe_url as _guard
    _guard(url)


def _slugify(name: str, fallback: str = "output") -> str:
    """Normalise a filename to ASCII slug."""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^\w\s-]", "", normalized).strip()
    return re.sub(r"[\s]+", "-", cleaned) or fallback


def _schedule_cleanup(output_path: str) -> None:
    """Schedule a Celery task to delete the output file after _CLEANUP_COUNTDOWN seconds."""
    try:
        from app.tasks.video_tasks import delete_local_file
        delete_local_file.apply_async((output_path,), countdown=_CLEANUP_COUNTDOWN)
    except Exception as e:
        print(f"[processing] Warning: could not schedule cleanup for {output_path}: {e}")


def _download_url_to_path(url: str, dest_path: str) -> None:
    """Stream a remote URL into dest_path. Raises HTTPException on failure."""
    import asyncio
    _assert_safe_url(url)
    async def _fetch():
        from app.core.ssrf_guard import safe_stream
        async with httpx.AsyncClient(
            timeout=120.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://www.tiktok.com/",
            },
        ) as client:
            async with safe_stream(client, "GET", url) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
    try:
        asyncio.get_event_loop().run_until_complete(_fetch())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to download source URL: {e}")


async def _download_url_async(url: str, dest_path: str) -> None:
    """Async version: stream a remote URL into dest_path."""
    from app.core.ssrf_guard import safe_stream
    async with httpx.AsyncClient(
        timeout=120.0,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.tiktok.com/",
        },
    ) as client:
        async with safe_stream(client, "GET", url) as resp:
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)


def _build_download_url(output_path: str, filename: str) -> str:
    """Build the /api/v1/download-local URL for a given output path + filename."""
    from urllib.parse import quote as _quote
    return f"/api/v1/download-local?filepath={_quote(output_path)}&filename={_quote(filename)}"


def _file_size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _success_response(output_path: str, filename: str, extra: Optional[dict] = None) -> dict:
    resp = {
        "success": True,
        "download_url": _build_download_url(output_path, filename),
        "output_path": output_path,
        "file_size_mb": _file_size_mb(output_path),
        "expires_in_seconds": _EXPIRES_IN_SECONDS,
    }
    if extra:
        resp.update(extra)
    return resp


# ── Subtitle helpers (mirrored from routes.py) ────────────────────────

_SUBTITLE_LANG_MAP = {
    "vi":   ["vi", "vi-VN", "vi-VIE"],
    "en":   ["en", "en-US", "en-GB"],
    "auto": ["vi", "vi-VN", "vi-VIE", "en", "en-US"],
    "all":  None,
}


def _subtitle_langs_for(language: Optional[str]) -> list:
    lang  = language or "auto"
    langs = _SUBTITLE_LANG_MAP.get(lang, _SUBTITLE_LANG_MAP["auto"])
    return langs if langs else ["vi", "vi-VN", "vi-VIE", "en", "en-US"]


def _burn_subtitle(video_path: str, subtitle_path: str, output_path: str) -> bool:
    """Hardcode subtitle into video using FFmpeg. Returns True on success."""
    try:
        escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", video_path,
                "-vf", (
                    f"subtitles={escaped}:force_style="
                    "'FontSize=20,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,Outline=1'"
                ),
                "-c:a", "copy", "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                output_path,
            ],
            capture_output=True, timeout=300,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        print(f"[processing] burn-subtitle failed: {e}")
        return False


def _strip_srt_timestamps(srt_content: str) -> str:
    """Convert SRT content to plain text by stripping sequence numbers and timestamps."""
    lines = srt_content.splitlines()
    text_lines = []
    skip_next = False
    for line in lines:
        line = line.strip()
        if not line:
            skip_next = False
            continue
        if line.isdigit():
            skip_next = True  # next line will be timestamp
            continue
        if skip_next and "-->" in line:
            skip_next = False
            continue
        text_lines.append(line)
    return "\n".join(text_lines)


# ── Request models ────────────────────────────────────────────────────

class ExtractAudioRequest(BaseModel):
    url: Optional[str] = None
    local_path: Optional[str] = None
    format: str = "mp3"       # "mp3" | "m4a"
    quality: str = "320"      # "128" | "192" | "320"
    filename: Optional[str] = "audio"
    normalize: Optional[bool] = False


class Mp4LoopRequest(BaseModel):
    url: Optional[str] = None
    local_path: Optional[str] = None
    start_time: float = 0
    end_time: float = 10
    width: int = 480
    filename: Optional[str] = "loop"


class SubtitleRequest(BaseModel):
    source_url: str
    language: str = "auto"    # "vi" | "en" | "auto" | "all"
    format: str = "srt"       # "srt" | "vtt" | "txt"
    filename: Optional[str] = "subtitle"


class BurnSubtitleRequest(BaseModel):
    video_path: str           # must be inside download_dir
    source_url: str
    language: str = "auto"
    filename: Optional[str] = "burned"


class FrameThumbRequest(BaseModel):
    local_path: str
    timestamp: float = 0
    filename: Optional[str] = "thumb"


class PackageZipRequest(BaseModel):
    paths: List[str]
    naming_template: str = "{title}"
    titles: Optional[List[str]] = []
    filename: Optional[str] = "package"


# ── Endpoints ─────────────────────────────────────────────────────────

@router.post("/process/extract-audio")
@limiter.limit("10/minute")
async def extract_audio(payload: ExtractAudioRequest, request: Request):
    """
    Extract audio from a video file (remote URL or local path).
    Outputs MP3 or M4A with configurable bitrate and optional loudness normalisation.
    """
    if not payload.url and not payload.local_path:
        raise HTTPException(status_code=400, detail="url or local_path is required")

    fmt = payload.format.lower()
    if fmt not in ("mp3", "m4a"):
        raise HTTPException(status_code=400, detail="format must be 'mp3' or 'm4a'")
    if payload.quality not in ("128", "192", "320"):
        raise HTTPException(status_code=400, detail="quality must be '128', '192', or '320'")

    download_dir = _safe_download_dir()
    uid = uuid.uuid4().hex[:8]
    output_path = os.path.join(download_dir, f"audio_{uid}.{fmt}")
    input_path: Optional[str] = None
    _downloaded = False

    try:
        if payload.local_path:
            input_path = _guard_local_path(payload.local_path)
        else:
            _assert_safe_url(payload.url)
            input_path = os.path.join(download_dir, f"audio_src_{uid}.tmp")
            await _download_url_async(payload.url, input_path)
            _downloaded = True

        # Build FFmpeg command
        if fmt == "mp3":
            codec_args = ["-vn", "-acodec", "libmp3lame", "-b:a", f"{payload.quality}k",
                          "-id3v2_version", "3", "-write_id3v1", "1"]
        else:  # m4a
            codec_args = ["-vn", "-acodec", "aac", "-b:a", f"{payload.quality}k",
                          "-movflags", "+faststart"]

        if payload.normalize:
            codec_args = codec_args + ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]

        ffmpeg_cmd = ["ffmpeg", "-y", "-i", input_path] + codec_args + [output_path]
        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            raise ValueError(f"FFmpeg error: {result.stderr.decode()[-300:]}")
        if not os.path.exists(output_path):
            raise ValueError("Output audio file was not created")

        slug = _slugify(payload.filename or "audio", "audio")
        out_filename = f"{slug}.{fmt}"
        _schedule_cleanup(output_path)
        return _success_response(output_path, out_filename)

    except HTTPException:
        raise
    except Exception as e:
        if output_path and os.path.exists(output_path):
            try: os.remove(output_path)
            except: pass
        raise HTTPException(status_code=500, detail=f"Audio extraction failed: {e}")
    finally:
        if _downloaded and input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


@router.post("/process/mp4-loop")
@limiter.limit("10/minute")
async def mp4_loop(payload: Mp4LoopRequest, request: Request):
    """
    Create a silent loopable MP4 clip: trim + scale, no audio.
    Duration limit: 30 seconds. Width clamped to [64, 1920].
    """
    if not payload.url and not payload.local_path:
        raise HTTPException(status_code=400, detail="url or local_path is required")

    duration = payload.end_time - payload.start_time
    if payload.start_time < 0 or duration <= 0:
        raise HTTPException(status_code=400, detail="Invalid time range")
    if duration > 30:
        raise HTTPException(status_code=400, detail="Maximum duration for MP4 loop is 30 seconds")
    width = max(64, min(payload.width, 1920))

    download_dir = _safe_download_dir()
    uid = uuid.uuid4().hex[:8]
    output_path = os.path.join(download_dir, f"loop_{uid}.mp4")
    input_path: Optional[str] = None
    _downloaded = False

    try:
        if payload.local_path:
            input_path = _guard_local_path(payload.local_path)
        else:
            _assert_safe_url(payload.url)
            input_path = os.path.join(download_dir, f"loop_src_{uid}.tmp")
            await _download_url_async(payload.url, input_path)
            _downloaded = True

        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-ss", f"{payload.start_time:.2f}",
            "-to", f"{payload.end_time:.2f}",
            "-i", input_path,
            "-vf", f"scale={width}:-2",
            "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            "-an", "-movflags", "+faststart",
            output_path,
        ]
        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=180)
        if result.returncode != 0:
            raise ValueError(f"FFmpeg error: {result.stderr.decode()[-300:]}")
        if not os.path.exists(output_path):
            raise ValueError("Output MP4 loop file was not created")

        slug = _slugify(payload.filename or "loop", "loop")
        out_filename = f"{slug}_loop.mp4"
        size_bytes = os.path.getsize(output_path)
        _schedule_cleanup(output_path)
        return {
            **_success_response(output_path, out_filename),
            "estimated_size_kb": round(size_bytes / 1024, 1),
            "duration_seconds": round(duration, 2),
            "width": width,
        }

    except HTTPException:
        raise
    except Exception as e:
        if output_path and os.path.exists(output_path):
            try: os.remove(output_path)
            except: pass
        raise HTTPException(status_code=500, detail=f"MP4 loop creation failed: {e}")
    finally:
        if _downloaded and input_path and os.path.exists(input_path):
            try: os.remove(input_path)
            except: pass


@router.post("/process/subtitle")
@limiter.limit("10/minute")
async def download_subtitle(payload: SubtitleRequest, request: Request):
    """
    Download subtitles/captions only (no video) from a supported URL.
    Outputs SRT, VTT, or TXT. Returns error if no subtitles are available.
    """
    import yt_dlp

    _assert_safe_url(payload.source_url)

    fmt = payload.format.lower()
    if fmt not in ("srt", "vtt", "txt"):
        raise HTTPException(status_code=400, detail="format must be 'srt', 'vtt', or 'txt'")

    download_dir = _safe_download_dir()
    uid = uuid.uuid4().hex[:8]
    outtmpl_base = os.path.join(download_dir, f"sub_{uid}")

    # yt-dlp downloads subtitles as <base>.<lang>.<ext>
    ydl_fmt = fmt if fmt != "txt" else "srt"  # txt = post-process from srt
    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": _subtitle_langs_for(payload.language),
        "subtitlesformat": ydl_fmt,
        "outtmpl": outtmpl_base,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([payload.source_url])
    except Exception as e:
        return {
            "success": False,
            "error": "download_failed",
            "message": f"Không thể tải phụ đề: {e}",
        }

    # Find the downloaded subtitle file (yt-dlp may produce e.g. sub_uid.vi.srt)
    sub_file: Optional[str] = None
    for entry in os.listdir(download_dir):
        if entry.startswith(f"sub_{uid}") and entry.endswith(f".{ydl_fmt}"):
            candidate = os.path.join(download_dir, entry)
            if os.path.exists(candidate):
                sub_file = candidate
                break

    if not sub_file:
        return {
            "success": False,
            "error": "no_subtitles",
            "message": "Nguồn này không có phụ đề/captions",
        }

    output_path = sub_file
    out_filename_base = _slugify(payload.filename or "subtitle", "subtitle")

    if fmt == "txt":
        # Convert SRT → plain text
        txt_path = os.path.join(download_dir, f"sub_{uid}.txt")
        try:
            with open(sub_file, "r", encoding="utf-8", errors="replace") as f:
                srt_content = f.read()
            plain_text = _strip_srt_timestamps(srt_content)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(plain_text)
            try: os.remove(sub_file)
            except: pass
            output_path = txt_path
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"TXT conversion failed: {e}")

    out_filename = f"{out_filename_base}.{fmt}"
    _schedule_cleanup(output_path)
    return _success_response(output_path, out_filename)


@router.post("/process/burn-subtitle")
@limiter.limit("10/minute")
async def burn_subtitle(payload: BurnSubtitleRequest, request: Request):
    """
    Download subtitles from source_url and burn them into an existing local video.
    video_path must be inside the downloads directory.
    """
    import yt_dlp

    video_path = _guard_local_path(payload.video_path)
    _assert_safe_url(payload.source_url)

    download_dir = _safe_download_dir()
    uid = uuid.uuid4().hex[:8]
    outtmpl_base = os.path.join(download_dir, f"bsub_{uid}")

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": _subtitle_langs_for(payload.language),
        "subtitlesformat": "srt",
        "outtmpl": outtmpl_base,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([payload.source_url])
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Subtitle download failed: {e}")

    # Find the downloaded subtitle
    sub_file: Optional[str] = None
    for entry in os.listdir(download_dir):
        if entry.startswith(f"bsub_{uid}") and entry.endswith(".srt"):
            candidate = os.path.join(download_dir, entry)
            if os.path.exists(candidate):
                sub_file = candidate
                break

    if not sub_file:
        raise HTTPException(
            status_code=404,
            detail="No subtitles found for this source URL",
        )

    # Determine output extension (preserve original video extension)
    orig_ext = os.path.splitext(video_path)[1] or ".mp4"
    output_path = os.path.join(download_dir, f"burned_{uid}{orig_ext}")

    try:
        success = _burn_subtitle(video_path, sub_file, output_path)
        if not success:
            raise HTTPException(status_code=500, detail="FFmpeg burn-in failed")
    finally:
        try: os.remove(sub_file)
        except: pass

    slug = _slugify(payload.filename or "burned", "burned")
    out_filename = f"{slug}_subbed{orig_ext}"
    _schedule_cleanup(output_path)
    return _success_response(output_path, out_filename)


@router.post("/process/frame-thumb")
@limiter.limit("10/minute")
async def frame_thumb(payload: FrameThumbRequest, request: Request):
    """
    Extract a single JPEG frame from a local video at the given timestamp.
    """
    input_path = _guard_local_path(payload.local_path)

    if payload.timestamp < 0:
        raise HTTPException(status_code=400, detail="timestamp must be >= 0")

    download_dir = _safe_download_dir()
    uid = uuid.uuid4().hex[:8]
    output_path = os.path.join(download_dir, f"thumb_{uid}.jpg")

    try:
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-ss", f"{payload.timestamp:.2f}",
            "-i", input_path,
            "-frames:v", "1",
            "-q:v", "2",
            output_path,
        ]
        result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            raise ValueError(f"FFmpeg error: {result.stderr.decode()[-300:]}")
        if not os.path.exists(output_path):
            raise ValueError("Thumbnail file was not created")

        slug = _slugify(payload.filename or "thumb", "thumb")
        out_filename = f"{slug}.jpg"
        _schedule_cleanup(output_path)
        return _success_response(output_path, out_filename)

    except HTTPException:
        raise
    except Exception as e:
        if output_path and os.path.exists(output_path):
            try: os.remove(output_path)
            except: pass
        raise HTTPException(status_code=500, detail=f"Frame extraction failed: {e}")


@router.post("/process/package-zip")
@limiter.limit("10/minute")
async def package_zip(payload: PackageZipRequest, request: Request):
    """
    Package multiple local files into a single ZIP archive.
    Each path must be inside the downloads directory.
    Filenames inside the ZIP follow naming_template substitutions.

    Supported template tokens: {title}, {platform}_{title}, {index:02d}_{title}, {date}_{title}
    """
    if not payload.paths:
        raise HTTPException(status_code=400, detail="paths list must not be empty")
    if len(payload.paths) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per ZIP")

    # Validate all paths first
    real_paths = []
    for p in payload.paths:
        real_paths.append(_guard_local_path(p))

    titles = list(payload.titles or [])
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    template = payload.naming_template or "{title}"

    download_dir = _safe_download_dir()
    uid = uuid.uuid4().hex[:8]
    zip_path = os.path.join(download_dir, f"pkg_{uid}.zip")

    total_size = 0

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, real_p in enumerate(real_paths):
                # Derive title for this file
                if idx < len(titles) and titles[idx]:
                    raw_title = titles[idx]
                else:
                    raw_title = os.path.splitext(os.path.basename(real_p))[0]

                title_slug = _slugify(raw_title, f"file_{idx + 1:02d}")
                orig_ext   = os.path.splitext(real_p)[1]  # e.g. ".mp4"

                # Apply template
                try:
                    arc_name_base = template.format(
                        title=title_slug,
                        platform="video",       # generic fallback; caller can override via titles
                        index=idx + 1,
                        date=today_str,
                    )
                except (KeyError, ValueError):
                    arc_name_base = title_slug

                arc_name = _slugify(arc_name_base, f"file_{idx + 1:02d}") + orig_ext
                zf.write(real_p, arc_name)
                total_size += os.path.getsize(real_p)

        if not os.path.exists(zip_path):
            raise ValueError("ZIP file was not created")

        slug = _slugify(payload.filename or "package", "package")
        out_filename = f"{slug}.zip"
        _schedule_cleanup(zip_path)
        return {
            **_success_response(zip_path, out_filename),
            "file_count": len(real_paths),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }

    except HTTPException:
        raise
    except Exception as e:
        if zip_path and os.path.exists(zip_path):
            try: os.remove(zip_path)
            except: pass
        raise HTTPException(status_code=500, detail=f"ZIP packaging failed: {e}")
