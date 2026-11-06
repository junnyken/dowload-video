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
  - Return {"success": True, "download_url": "...", "file_id": "<basename>",
             "file_size_mb": ..., "expires_in_seconds": 1200}
    Never an absolute server path: download_url is
    /download-local?file=<basename>, and file_id (the same basename) is what a
    client passes back as local_path / video_path to chain another step.
  - Are rate-limited at 10/minute per IP.
  - Apply path-traversal guard on every local_path input (a bare file_id is
    resolved inside the downloads directory; legacy absolute paths still work).
"""

import ipaddress
import os
import re
import asyncio
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.main import limiter
from app.core.local_download import new_token
from app.core.auth_middleware import get_optional_user as _auth_optional_user

router = APIRouter()


def _enforce_paid_processing_tier(
    request: Request,
    user: Optional[dict],
    allowed_tiers: tuple[str, ...],
    feature_label: str,
) -> str:
    """Enforce paid tier on Pro/paid processing routes (reup-clean, burn-subtitle, package-zip)."""
    from app.core.quotas import get_user_tier, is_admin_request  # noqa: PLC0415
    if is_admin_request(request):
        return "enterprise"
    uid = (user or {}).get("id")
    if not uid:
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "message": f"Tính năng '{feature_label}' đã bị khóa với Khách chưa đăng nhập. Vui lòng đăng nhập và nâng cấp gói để sử dụng.",
                "required_tier": allowed_tiers[0],
            },
        )
    tier = get_user_tier(str(uid)) or "free"
    if tier not in allowed_tiers:
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "message": f"Tính năng '{feature_label}' chỉ dành cho gói {' / '.join(t.title() for t in allowed_tiers if t != 'api')}. Vui lòng nâng cấp gói để sử dụng.",
                "current_tier": tier,
                "required_tier": allowed_tiers[0],
            },
        )
    return tier

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
    Resolve a client-supplied server file (file_id or legacy path) inside
    _DOWNLOADS_DIR via the shared validator (app.core.local_download.
    resolve_local_input). Raises HTTPException 400/404. Returns realpath.
    """
    from app.core.local_download import resolve_local_input
    real_p = resolve_local_input(path, _DOWNLOADS_DIR)
    if real_p is None:
        raise HTTPException(status_code=400, detail="Invalid local_path: path traversal not allowed")
    if not os.path.isfile(real_p):
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
    """The display name of an output file — keeps Vietnamese (27/10), see local_download.display_name."""
    from app.core.local_download import display_name
    return display_name(name, fallback)


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
    """/api/v1/download-local?file=<basename> — the directory is never sent."""
    from app.core.local_download import download_url
    return download_url(output_path, filename)


def _file_size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _success_response(output_path: str, filename: str, extra: Optional[dict] = None) -> dict:
    resp = {
        "success": True,
        "download_url": _build_download_url(output_path, filename),
        "file_id": os.path.basename(output_path),
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


# A concrete language code picked from the video's own list (e.g. "ja", "zh-Hans",
# "en-orig"). Strictly validated: yt-dlp treats subtitleslangs entries as regexes.
_LANG_CODE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def _subtitle_langs_for(language: Optional[str]) -> list:
    lang  = language or "auto"
    if lang not in _SUBTITLE_LANG_MAP and _LANG_CODE_RE.match(lang):
        return [lang]
    langs = _SUBTITLE_LANG_MAP.get(lang, _SUBTITLE_LANG_MAP["auto"])
    return langs if langs else ["vi", "vi-VN", "vi-VIE", "en", "en-US"]


def _vtt_file_to_srt(vtt_path: str, srt_path: str) -> bool:
    """Convert a WebVTT file to SRT. Returns True when a non-empty .srt was written."""
    from app.services.subtitle_format import (
        parse_vtt, serialize_srt, timestamp_to_seconds, seconds_to_srt_timestamp,
    )
    try:
        with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
            cues = parse_vtt(f.read())
        if not cues:
            return False
        # SRT needs "HH:MM:SS,mmm" (the parser keeps VTT's "." as-is).
        for i, c in enumerate(cues, 1):
            c.index = i
            c.start = seconds_to_srt_timestamp(timestamp_to_seconds(c.start))
            c.end = seconds_to_srt_timestamp(timestamp_to_seconds(c.end))
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(serialize_srt(cues))
        return True
    except Exception:
        return False


_BURN_FFMPEG_TIMEOUT_SEC = int(os.getenv("BURN_FFMPEG_TIMEOUT_SEC", "300"))


def _run_burn_ffmpeg(video_path: str, subtitle_path: str, output_path: str,
                     timeout: float) -> bool:
    """
    Blocking. Hardcode subtitle into video. Returns True on success; lets
    subprocess.TimeoutExpired through (subprocess.run kills ffmpeg first).
    Thread count capped by ffmpeg_budget like every other re-encode.
    """
    from app.core.ffmpeg_budget import thread_args
    escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", (
                f"subtitles={escaped}:force_style="
                "'FontSize=20,PrimaryColour=&H00FFFFFF&,OutlineColour=&H00000000&,Outline=1'"
            ),
            *thread_args(),
            "-c:a", "copy", "-c:v", "libx264", "-crf", "23", "-preset", "fast",
            output_path,
        ],
        capture_output=True, timeout=timeout,
    )
    if result.returncode != 0:
        print(f"[processing] burn-subtitle ffmpeg rc={result.returncode} "
              f"err={result.stderr.decode(errors='replace')[-300:]}")
    return result.returncode == 0 and os.path.exists(output_path)


def _burn_subtitle(video_path: str, subtitle_path: str, output_path: str) -> bool:
    """Hardcode subtitle into video using FFmpeg. Returns True on success."""
    try:
        return _run_burn_ffmpeg(video_path, subtitle_path, output_path,
                                _BURN_FFMPEG_TIMEOUT_SEC)
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
    translate_to_vi: bool = False
    filename: Optional[str] = "burned"


class ReupCleanRequest(BaseModel):
    url: Optional[str] = None
    local_path: Optional[str] = None
    strip_metadata: bool = True
    change_md5: bool = True
    mirror_flip: bool = False
    speed_105x: bool = False
    filename: Optional[str] = "video"


class FrameThumbRequest(BaseModel):
    local_path: str
    timestamp: float = 0
    filename: Optional[str] = "thumb"


class PackageZipRequest(BaseModel):
    paths: Optional[List[str]] = []
    image_urls: Optional[List[str]] = []
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
    uid = new_token()  # 128 bits: the file id is the capability
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
        result = await asyncio.to_thread(subprocess.run, ffmpeg_cmd, capture_output=True, timeout=300)
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
    uid = new_token()  # 128 bits: the file id is the capability
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
        result = await asyncio.to_thread(subprocess.run, ffmpeg_cmd, capture_output=True, timeout=180)
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


# Bounds for the subtitle-only fetch. One attempt may not exceed the per-attempt
# timeout; all attempts together (direct, then proxy/cookies) share the budget.
_SUBTITLE_ATTEMPT_TIMEOUT_SEC = int(os.getenv("SUBTITLE_ATTEMPT_TIMEOUT_SEC", "40"))
_SUBTITLE_TOTAL_BUDGET_SEC = int(os.getenv("SUBTITLE_TOTAL_BUDGET_SEC", "75"))
_SUBTITLE_MAX_BYTES = int(os.getenv("SUBTITLE_MAX_BYTES", str(5 * 1024 * 1024)))


class _NoSubtitles(Exception):
    """The video answered but has no subtitle track for the requested language(s)."""

    def __init__(self, code: str = "subtitle_language_unavailable"):
        super().__init__(code)
        self.code = code


class _SubtitleFetchFailed(Exception):
    def __init__(self, status: int, code: str, raw: str):
        super().__init__(raw)
        self.status = status
        self.code = code
        self.raw = raw


def _is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d)
               for d in ("youtube.com", "youtu.be", "youtube-nocookie.com"))


def _find_subtitle_output(work_dir: str, ydl_fmt: str) -> Optional[str]:
    """yt-dlp writes <outtmpl>.<lang>.<ext>; prefer the wanted ext, else a .vtt."""
    names = sorted(os.listdir(work_dir))
    for ext in (ydl_fmt, "vtt"):
        for n in names:
            if n.startswith("sub.") and n.endswith(f".{ext}"):
                return os.path.join(work_dir, n)
    return None


def _fetch_subtitle_file(url: str, langs: list, subtitles_format: str,
                         ydl_fmt: str, work_dir: str) -> str:
    """
    Blocking. Runs the subtitle attempts from the shared option builder and
    returns the path of the written subtitle file inside work_dir.
    Raises _NoSubtitles or _SubtitleFetchFailed.
    """
    import time
    import yt_dlp
    from app.services.downloader import build_subtitle_attempts, _run_with_timeout
    from app.core.extraction_errors import classify_extraction_error

    sink: list = []
    attempts = build_subtitle_attempts(
        url, langs, subtitles_format, os.path.join(work_dir, "sub"), error_sink=sink,
    )
    deadline = time.monotonic() + _SUBTITLE_TOTAL_BUDGET_SEC
    last = _SubtitleFetchFailed(504, "extraction_timeout", "subtitle budget exhausted")

    for label, opts in attempts:
        remaining = deadline - time.monotonic()
        if remaining < 3:
            break

        def _one(o=opts):
            with yt_dlp.YoutubeDL(o) as ydl:
                return ydl.extract_info(url, download=True)

        try:
            info = _run_with_timeout(
                _one, timeout=min(_SUBTITLE_ATTEMPT_TIMEOUT_SEC, remaining))
        except Exception as e:
            raw = str(e)
            status, code = classify_extraction_error(raw, e)
            print(f"[processing] subtitle attempt={label} failed status={status} "
                  f"code={code} err={raw[:200]}")
            last = _SubtitleFetchFailed(status, code, raw)
            # The link itself is the problem (removed, unsupported, geo) —
            # another route will not change that. Private/login and anything
            # server-side get one more try with proxy + cookies.
            if status < 500 and code != "private_or_login_required":
                break
            continue

        if not info:
            last = _SubtitleFetchFailed(502, "provider_unavailable", "empty extraction result")
            continue
        if not info.get("requested_subtitles"):
            raise _NoSubtitles()
        found = _find_subtitle_output(work_dir, ydl_fmt)
        if found:
            print(f"[processing] subtitle attempt={label} ok")
            return found
        last = _SubtitleFetchFailed(502, "provider_unavailable", "subtitle file not written")

    raise last


def _subtitle_error_message(code: str) -> str:
    from app.core.error_codes import get_error_meta
    return f"Không tải được phụ đề: {get_error_meta(code)['user_message']}"


def _youtube_gate_or_503(url: str) -> None:
    """YouTube gate (flag / circuit / cost) — 503 {error, error_code, message}."""
    if not _is_youtube_url(url):
        return
    from app.core import youtube_gate as _ytg
    try:
        _ytg.availability_check()
    except _ytg.YouTubeBlocked as _yb:
        raise HTTPException(status_code=_yb.http, detail={
            "error": _yb.code, "error_code": _yb.code,
            "message": _yb.message, **_yb.payload,
        })


def _no_subtitles_response(language: Optional[str], code: str):
    from fastapi.responses import JSONResponse
    lang = language or "auto"
    msg = (f"Video không có phụ đề cho ngôn ngữ '{lang}'."
           if code == "subtitle_language_unavailable"
           and _LANG_CODE_RE.match(lang) and lang not in _SUBTITLE_LANG_MAP
           else "Nguồn này không có phụ đề/captions")
    return JSONResponse(status_code=404, content={
        "success": False, "error": "no_subtitles",
        "error_code": code, "message": msg,
    })


async def _fetch_subtitle_into(work_dir: str, url: str, language: Optional[str],
                               ydl_fmt: str) -> str:
    """
    Fetch one subtitle track into work_dir through the shared yt-dlp builder
    (direct, then proxy + cookies + PO token), off the event loop, bounded by
    the subtitle budget and size cap. When ydl_fmt is "srt" and only a .vtt was
    offered, it is converted. Returns the file path.

    Raises _NoSubtitles(code) or ExtractionHTTPException (classified 4xx/5xx).
    """
    from fastapi.concurrency import run_in_threadpool
    from app.core.extraction_errors import ExtractionHTTPException

    langs = _subtitle_langs_for(language)
    # YouTube etc. only offer vtt: accept it and convert to .srt below.
    subtitles_format = "srt/vtt/best" if ydl_fmt == "srt" else ydl_fmt
    try:
        sub_file = await run_in_threadpool(
            _fetch_subtitle_file, url, langs, subtitles_format, ydl_fmt, work_dir,
        )
    except _SubtitleFetchFailed as f:
        raise ExtractionHTTPException(f.status, _subtitle_error_message(f.code), f.code)

    if os.path.getsize(sub_file) > _SUBTITLE_MAX_BYTES:
        raise ExtractionHTTPException(
            413, "Không tải được phụ đề: tệp phụ đề quá lớn.", "subtitle_too_large")

    if ydl_fmt == "srt" and sub_file.endswith(".vtt"):
        # Only a .vtt was available — convert it so the user still gets an .srt.
        srt = sub_file[:-4] + ".srt"
        if not _vtt_file_to_srt(sub_file, srt):
            raise _NoSubtitles("subtitle_empty")
        sub_file = srt
    return sub_file


@router.post("/process/subtitle")
@limiter.limit("10/minute")
async def download_subtitle(payload: SubtitleRequest, request: Request):
    """
    Download subtitles/captions only (no video) from a supported URL.
    Outputs SRT, VTT, or TXT.

    Uses the same yt-dlp option builder as video extraction (proxy pool,
    cookie pool, YouTube client + PO token), honours the YouTube gate, and
    classifies failures like /fetch-link (4xx = the link, 5xx = us/upstream).
    """
    import shutil
    import tempfile

    _assert_safe_url(payload.source_url)

    fmt = payload.format.lower()
    if fmt not in ("srt", "vtt", "txt"):
        raise HTTPException(status_code=400, detail="format must be 'srt', 'vtt', or 'txt'")

    _youtube_gate_or_503(payload.source_url)

    ydl_fmt = fmt if fmt != "txt" else "srt"  # txt = post-process from srt

    download_dir = _safe_download_dir()
    uid = new_token()  # 128 bits: the file id is the capability
    work_dir = tempfile.mkdtemp(prefix=f".subtmp_{uid}_", dir=download_dir)
    try:
        try:
            sub_file = await _fetch_subtitle_into(
                work_dir, payload.source_url, payload.language, ydl_fmt)
        except _NoSubtitles as e:
            return _no_subtitles_response(payload.language, e.code)

        if fmt == "txt":
            with open(sub_file, "r", encoding="utf-8", errors="replace") as f:
                plain_text = _strip_srt_timestamps(f.read())
            txt = os.path.join(work_dir, "sub.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write(plain_text)
            sub_file = txt

        # work_dir/sub.<lang>.<ext> → downloads/sub_<uid>.<lang>.<ext>
        # (lang comes from the provider: keep it to file-id-safe characters)
        tail = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(sub_file)[3:])
        output_path = os.path.join(download_dir, f"sub_{uid}{tail}")
        shutil.move(sub_file, output_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    out_filename = f"{_slugify(payload.filename or 'subtitle', 'subtitle')}.{fmt}"
    _schedule_cleanup(output_path)
    return _success_response(output_path, out_filename)


@router.post("/process/burn-subtitle")
@limiter.limit("10/minute")
async def burn_subtitle(
    payload: BurnSubtitleRequest,
    request: Request,
    user: Optional[dict] = Depends(_auth_optional_user),
):
    """
    Download subtitles from source_url and burn them into an existing local video.
    video_path must be inside the downloads directory (file_id or legacy path).

    The subtitle fetch is the one /process/subtitle uses (shared yt-dlp builder,
    YouTube gate, classified errors, time + size bounds). The video is not
    downloaded here — it is the caller's already-downloaded file. ffmpeg runs
    off the event loop with the shared thread cap and a hard timeout.
    """
    import shutil
    import tempfile
    from fastapi.concurrency import run_in_threadpool
    from app.core.extraction_errors import ExtractionHTTPException

    _enforce_paid_processing_tier(
        request, user, ("pro", "team", "enterprise", "api"), "Ghép cứng Phụ đề vào Video (Burn Subtitle)"
    )

    video_path = _guard_local_path(payload.video_path)
    _assert_safe_url(payload.source_url)
    _youtube_gate_or_503(payload.source_url)

    download_dir = _safe_download_dir()
    uid = new_token()  # 128 bits: the file id is the capability
    # Preserve the original container; anything odd falls back to .mp4.
    orig_ext = os.path.splitext(video_path)[1]
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", orig_ext or ""):
        orig_ext = ".mp4"
    output_path = os.path.join(download_dir, f"burned_{uid}{orig_ext}")
    work_dir = tempfile.mkdtemp(prefix=f".bsubtmp_{uid}_", dir=download_dir)
    ok = False
    try:
        try:
            sub_file = await _fetch_subtitle_into(
                work_dir, payload.source_url, payload.language, "srt")
        except _NoSubtitles as e:
            return _no_subtitles_response(payload.language, e.code)

        # Fixed name: nothing provider-controlled reaches ffmpeg's filter syntax.
        burn_srt = os.path.join(work_dir, "burn.srt")
        os.replace(sub_file, burn_srt)

        if payload.translate_to_vi:
            await _translate_srt_file_to_vi(burn_srt)

        try:
            ok = await run_in_threadpool(
                _run_burn_ffmpeg, video_path, burn_srt, output_path,
                _BURN_FFMPEG_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            raise ExtractionHTTPException(
                504, "Ghép phụ đề quá thời gian cho phép. Thử video ngắn hơn.",
                "processing_timeout")
        except OSError as e:
            print(f"[processing] burn-subtitle could not start ffmpeg: {e}")
            ok = False
        if not ok:
            raise HTTPException(status_code=500, detail="FFmpeg burn-in failed")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        if not ok and os.path.exists(output_path):
            try: os.remove(output_path)
            except OSError: pass

    slug = _slugify(payload.filename or "burned", "burned")
    out_filename = f"{slug}_subbed{orig_ext}"
    _schedule_cleanup(output_path)
    return _success_response(output_path, out_filename)


async def _translate_srt_file_to_vi(srt_path: str) -> None:
    """
    Translate non-timestamp text lines of an SRT file into Vietnamese using
    Google Translate's lightweight public endpoint while preserving exact SRT
    index & timing lines. Falls back gracefully to original text on network error.
    """
    try:
        with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

        text_indices = []
        text_lines = []
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.isdigit() or "-->" in stripped:
                continue
            text_indices.append(idx)
            text_lines.append(stripped)

        if not text_lines:
            return

        translated_lines: List[str] = []
        batch_size = 25
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"},
        ) as client:
            for i in range(0, len(text_lines), batch_size):
                chunk = text_lines[i : i + batch_size]
                joined = "\n".join(chunk)
                merged = ""
                # Primary: clients5 Chrome extension translate endpoint (high rate limit)
                try:
                    r1 = await client.get(
                        "https://clients5.google.com/translate_a/t",
                        params={"client": "dict-chrome-ex", "sl": "auto", "tl": "vi", "q": joined},
                    )
                    if r1.status_code == 200:
                        d1 = r1.json()
                        if isinstance(d1, list) and d1:
                            first = d1[0]
                            if isinstance(first, list) and first and isinstance(first[0], str):
                                merged = first[0]
                            elif isinstance(first, str):
                                merged = first
                except Exception:
                    merged = ""

                # Fallback: translate.googleapis.com gtx endpoint
                if not merged:
                    resp = await client.get(
                        "https://translate.googleapis.com/translate_a/single",
                        params={
                            "client": "gtx",
                            "sl": "auto",
                            "tl": "vi",
                            "dt": "t",
                            "q": joined,
                        },
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        merged = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])

                if merged:
                    parts = [p.strip() for p in merged.split("\n")]
                    if len(parts) == len(chunk):
                        translated_lines.extend(parts)
                    else:
                        for j in range(len(chunk)):
                            translated_lines.append(parts[j] if j < len(parts) and parts[j] else chunk[j])
                else:
                    translated_lines.extend(chunk)

        for idx, new_txt in zip(text_indices, translated_lines):
            lines[idx] = new_txt

        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print(f"[processing] _translate_srt_file_to_vi warning (keeping original): {e}")


@router.post("/process/reup-clean")
@limiter.limit("10/minute")
async def reup_clean(
    payload: ReupCleanRequest,
    request: Request,
    user: Optional[dict] = Depends(_auth_optional_user),
):
    """
    Anti-Detect / Reup Video Cleaner (Pro feature):
    - Strips all source container/stream metadata (-map_metadata -1 -map_chapters -1)
    - Injects a unique random comment atom so the file's MD5/SHA256 hash is 100% unique
    - Optional horizontal mirror flip (hflip)
    - Optional 1.05x speed shift (video + audio) to evade fingerprint matching
    """
    import hashlib
    from app.core.ffmpeg_budget import thread_args

    _enforce_paid_processing_tier(
        request, user, ("pro", "team", "enterprise", "api"), "Reup Anti-Detect Cleaner (Lách bản quyền)"
    )

    if not payload.url and not payload.local_path:
        raise HTTPException(status_code=400, detail="url or local_path is required")

    download_dir = _safe_download_dir()
    uid = new_token()
    output_path = os.path.join(download_dir, f"clean_{uid}.mp4")
    input_path: Optional[str] = None
    _downloaded = False

    try:
        if payload.local_path:
            input_path = _guard_local_path(payload.local_path)
        else:
            _assert_safe_url(payload.url)
            input_path = os.path.join(download_dir, f"clean_src_{uid}.tmp")
            await _download_url_async(payload.url, input_path)
            _downloaded = True

        v_filters = []
        a_filters = []
        if payload.mirror_flip:
            v_filters.append("hflip")
        if payload.speed_105x:
            v_filters.append("setpts=PTS/1.05")
            a_filters.append("atempo=1.05")

        meta_args = []
        if payload.strip_metadata:
            meta_args.extend(["-map_metadata", "-1", "-map_chapters", "-1"])
        if payload.change_md5:
            meta_args.extend([
                "-metadata", f"comment=vg_clean_{uuid.uuid4().hex}",
                "-metadata", f"creation_time={datetime.now(timezone.utc).isoformat()}",
            ])

        if v_filters or a_filters:
            filter_args = []
            if v_filters:
                filter_args.extend(["-vf", ",".join(v_filters)])
            if a_filters:
                filter_args.extend(["-af", ",".join(a_filters)])
            codec_args = [
                *filter_args,
                *thread_args(),
                "-c:v", "libx264", "-crf", "22", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
            ]
        else:
            # Ultra-fast stream copy (< 1 second, zero quality loss)
            codec_args = ["-c", "copy", "-movflags", "+faststart"]

        ffmpeg_cmd = ["ffmpeg", "-y", "-i", input_path, *meta_args, *codec_args, output_path]
        result = await asyncio.to_thread(subprocess.run, ffmpeg_cmd, capture_output=True, timeout=240)
        if result.returncode != 0:
            raise ValueError(f"FFmpeg error: {result.stderr.decode(errors='replace')[-300:]}")
        if not os.path.exists(output_path):
            raise ValueError("Output cleaned video file was not created")

        # Compute new MD5 hash (first 8MB + size for speed on huge files, or full file if <= 50MB)
        md5_hash = hashlib.md5()
        with open(output_path, "rb") as f_out:
            while True:
                chunk = f_out.read(1024 * 1024)
                if not chunk:
                    break
                md5_hash.update(chunk)

        slug = _slugify(payload.filename or "video", "video")
        out_filename = f"{slug}_clean.mp4"
        _schedule_cleanup(output_path)
        return _success_response(
            output_path,
            out_filename,
            extra={
                "md5": md5_hash.hexdigest(),
                "stripped_metadata": bool(payload.strip_metadata),
                "mirror_flip": bool(payload.mirror_flip),
                "speed_105x": bool(payload.speed_105x),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        if output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"Reup clean processing failed: {e}")
    finally:
        if _downloaded and input_path and os.path.exists(input_path):
            try:
                os.remove(input_path)
            except Exception:
                pass


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
    uid = new_token()  # 128 bits: the file id is the capability
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
        result = await asyncio.to_thread(subprocess.run, ffmpeg_cmd, capture_output=True, timeout=60)
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
async def package_zip(
    payload: PackageZipRequest,
    request: Request,
    user: Optional[dict] = Depends(_auth_optional_user),
):
    """
    Package multiple local files into a single ZIP archive.
    Each path must be inside the downloads directory.
    Filenames inside the ZIP follow naming_template substitutions.

    Supported template tokens: {title}, {platform}_{title}, {index:02d}_{title}, {date}_{title}
    """
    _enforce_paid_processing_tier(
        request, user, ("pro", "team", "enterprise", "api"), "Đóng gói tải trọn bộ Album/Batch .ZIP"
    )
    paths_list = list(payload.paths or [])
    image_urls_list = list(payload.image_urls or [])
    if not paths_list and not image_urls_list:
        raise HTTPException(status_code=400, detail="paths or image_urls list must not be empty")
    if len(paths_list) + len(image_urls_list) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 files per ZIP")

    # Validate all local paths first
    real_paths = []
    for p in paths_list:
        real_paths.append(_guard_local_path(p))

    download_dir = _safe_download_dir()
    uid = new_token()  # 128 bits: the file id is the capability
    temp_img_paths = []

    try:
        for img_idx, img_url in enumerate(image_urls_list):
            _assert_safe_url(img_url)
            parsed_path = urlparse(img_url).path.lower()
            ext = ".webp" if parsed_path.endswith(".webp") else (".png" if parsed_path.endswith(".png") else ".jpg")
            tmp_img = os.path.join(download_dir, f"slide_{uid}_{img_idx + 1:02d}{ext}")
            await _download_url_async(img_url, tmp_img)
            temp_img_paths.append(tmp_img)
            real_paths.append(tmp_img)

        titles = list(payload.titles or [])
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        template = payload.naming_template or "{title}"
        zip_path = os.path.join(download_dir, f"pkg_{uid}.zip")
        total_size = 0

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, real_p in enumerate(real_paths):
                # Derive title for this file
                if idx < len(titles) and titles[idx]:
                    raw_title = titles[idx]
                else:
                    raw_title = ""  # stored names are random tokens, not titles

                title_slug = _slugify(raw_title, f"file_{idx + 1:02d}")
                orig_ext   = os.path.splitext(real_p)[1] or ".jpg"

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
        if 'zip_path' in locals() and zip_path and os.path.exists(zip_path):
            try: os.remove(zip_path)
            except: pass
        raise HTTPException(status_code=500, detail=f"ZIP packaging failed: {e}")
    finally:
        for tp in temp_img_paths:
            if os.path.exists(tp):
                try: os.remove(tp)
                except: pass
