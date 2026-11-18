"""
AI Viral Clipper — Cắt clip ngắn viral tự động
=================================================
Takes a long YouTube/TikTok video and automatically cuts 3-5 short vertical
clips (30-60s) optimized for TikTok/Reels/Shorts.

Pipeline:
  1. Download source video (via yt-dlp)
  2. Run smart_clips.py heuristic analysis (audio peaks, motion, scenes)
  3. (Optional) AI transcript analysis to find hooks & punchlines
  4. FFmpeg: cut segments, crop to 9:16, burn subtitle overlay
  5. Package and return clips (ZIP or individual)

Endpoint:
  POST /process/viral-clips — accepts URL, returns 3-5 vertical clips

Tier: Pro / Team / Enterprise
Env:
  VIRAL_CLIPS_TIMEOUT_SEC (default 1200 = 20 min)
  VIRAL_CLIPS_MAX_DURATION_MIN (default 60)
  VIRAL_CLIPS_MAX_CLIPS (default 5)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_optional_user as _auth_optional_user
from app.core.local_download import new_token
from app.main import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

_DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)
_CLEANUP_COUNTDOWN = 30 * 60  # 30 min for clip packages
_EXPIRES_IN_SECONDS = 1800
_TIMEOUT_SEC = int(os.getenv("VIRAL_CLIPS_TIMEOUT_SEC", "1200"))
_MAX_DURATION_MIN = int(os.getenv("VIRAL_CLIPS_MAX_DURATION_MIN", "60"))
_MAX_CLIPS = int(os.getenv("VIRAL_CLIPS_MAX_CLIPS", "5"))


def _safe_download_dir() -> str:
    os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
    return _DOWNLOADS_DIR


def _schedule_cleanup(path: str) -> None:
    try:
        from app.tasks.video_tasks import delete_local_file
        delete_local_file.apply_async((path,), countdown=_CLEANUP_COUNTDOWN)
    except Exception:
        pass


def _build_download_url(output_path: str, filename: str) -> str:
    from app.core.local_download import download_url
    return download_url(output_path, filename)


def _slugify(name: str, fallback: str = "output") -> str:
    from app.core.local_download import display_name
    return display_name(name, fallback)


def _file_size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _enforce_tier(request: Request, user: Optional[dict]) -> str:
    from app.core.quotas import get_user_tier, is_admin_request
    if is_admin_request(request):
        return "enterprise"
    uid = (user or {}).get("id")
    if not uid:
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "message": "Tính năng 'AI Viral Clipper' yêu cầu đăng nhập và gói Pro trở lên.",
                "required_tier": "pro",
            },
        )
    tier = get_user_tier(str(uid)) or "free"
    if tier not in ("pro", "team", "enterprise", "api"):
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "message": "Tính năng 'AI Viral Clipper' chỉ dành cho gói Pro / Team.",
                "current_tier": tier,
                "required_tier": "pro",
            },
        )
    return tier


class ViralClipsRequest(BaseModel):
    url: str
    max_clips: int = 5
    clip_duration: int = 45         # target clip duration (30-60s)
    aspect_ratio: str = "9:16"      # "9:16" (vertical) | "16:9" (horizontal) | "1:1" (square)
    burn_subtitles: bool = True     # burn ASR subtitles onto clips
    font_size: int = 28             # subtitle font size for viral style
    filename: Optional[str] = "clips"


async def _download_video(url: str, work_dir: str) -> tuple[str, dict]:
    """Download video for clip analysis."""
    import yt_dlp

    output_template = os.path.join(work_dir, "source.%(ext)s")
    ydl_opts = {
        "outtmpl": output_template,
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
    }

    def _do():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    info = await asyncio.to_thread(_do)
    if not info:
        raise HTTPException(status_code=502, detail="Không tải được video từ URL này")

    for f in os.listdir(work_dir):
        if f.startswith("source.") and not f.endswith(".part"):
            return os.path.join(work_dir, f), info

    raise HTTPException(status_code=500, detail="Video đã tải nhưng không tìm thấy file")


async def _analyze_video(video_path: str) -> dict:
    """Run ffprobe + heuristic analysis on the video."""
    # Get video info via ffprobe
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path,
    ]
    result = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, timeout=30
    )
    if result.returncode != 0:
        return {"probe": {"duration_s": 0}}

    import json
    try:
        probe = json.loads(result.stdout)
        duration = float(probe.get("format", {}).get("duration", 0))
        # Find video stream dimensions
        width, height = 1920, 1080
        for stream in probe.get("streams", []):
            if stream.get("codec_type") == "video":
                width = int(stream.get("width", 1920))
                height = int(stream.get("height", 1080))
                break
        return {
            "probe": {"duration_s": duration, "width": width, "height": height},
        }
    except Exception:
        return {"probe": {"duration_s": 0}}


async def _detect_highlights_ai(
    video_path: str, analysis: dict, transcript: Optional[str], max_clips: int, clip_duration: int
) -> list[dict]:
    """
    Use heuristic smart_clips + optional AI transcript analysis to find
    the best highlight segments.
    """
    from app.services.smart_clips import detect_highlights

    # Heuristic detection
    clips = detect_highlights(
        analysis,
        max_clips=max_clips,
        min_duration=max(15, clip_duration - 15),
        max_duration=min(90, clip_duration + 15),
        min_video_length=30,
    )

    # If transcript available, try AI enhancement
    if transcript and len(transcript) > 50:
        ai_clips = await _ai_find_hooks(transcript, max_clips, clip_duration)
        if ai_clips:
            # Merge AI-suggested timestamps with heuristic clips
            clips = _merge_clip_sources(clips, ai_clips, max_clips)

    # If no clips found, fall back to evenly-spaced segments
    if not clips:
        duration = analysis.get("probe", {}).get("duration_s", 0)
        if duration > clip_duration:
            clips = _fallback_even_clips(duration, max_clips, clip_duration)

    return clips[:max_clips]


async def _ai_find_hooks(transcript: str, max_clips: int, clip_duration: int) -> Optional[list[dict]]:
    """Use AI to find hook/punchline moments in transcript."""
    try:
        from app.services.llm_client import call_llm

        prompt = f"""Phân tích transcript phụ đề video sau và tìm {max_clips} đoạn hay nhất (~{clip_duration} giây mỗi đoạn) phù hợp làm clip viral ngắn cho TikTok/Reels.

Tiêu chí chọn:
- Hook mạnh (câu mở đầu gây tò mò)
- Punchline/plot twist
- Cảm xúc cao (hài, bất ngờ, cảm động)
- Thông tin giá trị (tips, hướng dẫn)

Transcript (timestamps dạng HH:MM:SS):
{transcript[:3000]}

Trả về JSON array, mỗi phần tử:
[{{"start_sec": 120.5, "end_sec": 165.0, "hook_text": "câu hook ngắn", "reason": "lý do chọn"}}]"""

        result = await asyncio.to_thread(call_llm, prompt, 1024)
        if not result:
            return None

        import json
        import re
        json_match = re.search(r'\[[\s\S]*?\]', result)
        if json_match:
            parsed = json.loads(json_match.group())
            clips = []
            for item in parsed:
                clips.append({
                    "start": float(item.get("start_sec", 0)),
                    "end": float(item.get("end_sec", 0)),
                    "confidence": 0.75,
                    "label": "ai_hook",
                    "reason": item.get("reason", "AI suggested"),
                    "hook_text": item.get("hook_text", ""),
                })
            return clips
    except Exception as e:
        logger.warning("AI hook detection failed: %s", e)

    return None


def _merge_clip_sources(heuristic: list, ai: list, max_clips: int) -> list:
    """Merge heuristic and AI clips, preferring AI for the first slots."""
    merged = []
    used_ranges = []

    # AI clips first
    for clip in ai:
        if len(merged) >= max_clips:
            break
        start, end = clip.get("start", 0), clip.get("end", 0)
        if end - start < 10:
            continue
        overlap = any(start < e + 5 and end > s - 5 for s, e in used_ranges)
        if not overlap:
            merged.append(clip)
            used_ranges.append((start, end))

    # Fill remaining with heuristic
    for clip in heuristic:
        if len(merged) >= max_clips:
            break
        start, end = clip.get("start", 0), clip.get("end", 0)
        overlap = any(start < e + 5 and end > s - 5 for s, e in used_ranges)
        if not overlap:
            merged.append(clip)
            used_ranges.append((start, end))

    return sorted(merged, key=lambda c: c.get("start", 0))


def _fallback_even_clips(duration: float, max_clips: int, clip_duration: int) -> list:
    """Generate evenly-spaced clips as fallback."""
    clips = []
    gap = duration / (max_clips + 1)
    for i in range(1, max_clips + 1):
        center = gap * i
        start = max(0, center - clip_duration / 2)
        end = min(duration, start + clip_duration)
        clips.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "confidence": 0.3,
            "label": "even_split",
            "reason": f"Phân đoạn đều #{i}",
        })
    return clips


async def _cut_clip(
    video_path: str, start: float, end: float, output_path: str,
    aspect: str, font_size: int, subtitle_text: Optional[str] = None,
) -> bool:
    """Cut a single clip with aspect ratio crop and optional subtitle."""
    from app.core.ffmpeg_budget import thread_args

    vf_parts = []

    # Aspect ratio crop
    if aspect == "9:16":
        vf_parts.append("crop=ih*9/16:ih")
    elif aspect == "1:1":
        vf_parts.append("crop=min(iw\\,ih):min(iw\\,ih)")
    # 16:9 = no crop needed (most source videos are already 16:9)

    # Scale to standard vertical resolution
    if aspect == "9:16":
        vf_parts.append("scale=1080:1920:force_original_aspect_ratio=decrease")
        vf_parts.append("pad=1080:1920:(ow-iw)/2:(oh-ih)/2")
    elif aspect == "1:1":
        vf_parts.append("scale=1080:1080:force_original_aspect_ratio=decrease")
        vf_parts.append("pad=1080:1080:(ow-iw)/2:(oh-ih)/2")

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-i", video_path,
    ]

    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])

    cmd.extend(thread_args())
    cmd.extend([
        "-c:v", "libx264", "-crf", "21", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ])

    result = await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, timeout=120
    )

    if result.returncode != 0:
        logger.error("clip cut failed: %s", result.stderr.decode(errors='replace')[-300:])
        return False

    return os.path.exists(output_path)


@router.post("/process/viral-clips")
@limiter.limit("3/minute")
async def viral_clips(
    payload: ViralClipsRequest,
    request: Request,
    user: Optional[dict] = Depends(_auth_optional_user),
):
    """
    AI Viral Clipper — Tự động cắt 3-5 clip ngắn vertical từ video dài.

    Pipeline: Download → Analyze → AI Find Hooks → Cut 9:16 → Package ZIP
    """
    tier = _enforce_tier(request, user)

    from app.core.ssrf_guard import assert_safe_url
    assert_safe_url(payload.url)

    if payload.max_clips < 1 or payload.max_clips > _MAX_CLIPS:
        raise HTTPException(status_code=400, detail=f"max_clips phải từ 1 đến {_MAX_CLIPS}")
    if payload.clip_duration < 15 or payload.clip_duration > 90:
        raise HTTPException(status_code=400, detail="clip_duration phải từ 15 đến 90 giây")
    if payload.aspect_ratio not in ("9:16", "16:9", "1:1"):
        raise HTTPException(status_code=400, detail="aspect_ratio phải là: 9:16, 16:9, 1:1")

    download_dir = _safe_download_dir()
    uid = new_token()
    work_dir = tempfile.mkdtemp(prefix=f".viral_{uid}_", dir=download_dir)

    try:
        # Step 1: Download video
        logger.info("viral_clips.start url=%s", payload.url[:80])
        video_path, info = await _download_video(payload.url, work_dir)

        duration = float((info or {}).get("duration", 0) or 0)
        if duration > _MAX_DURATION_MIN * 60:
            raise HTTPException(
                status_code=400,
                detail=f"Video quá dài ({duration/60:.0f} phút). Giới hạn: {_MAX_DURATION_MIN} phút."
            )
        if duration < 30:
            raise HTTPException(status_code=400, detail="Video quá ngắn (< 30s), không đủ để cắt clip.")

        title = (info or {}).get("title", "video") or "video"

        # Step 2: Analyze video
        analysis = await _analyze_video(video_path)

        # Step 3: Detect highlights (heuristic + AI)
        highlights = await _detect_highlights_ai(
            video_path, analysis, None, payload.max_clips, payload.clip_duration
        )

        if not highlights:
            raise HTTPException(status_code=422, detail="Không phát hiện được đoạn nổi bật nào trong video.")

        # Step 4: Cut each clip
        clip_paths = []
        clip_info = []
        for idx, hl in enumerate(highlights, 1):
            clip_name = f"clip_{idx:02d}_{uid}.mp4"
            clip_path = os.path.join(work_dir, clip_name)

            ok = await _cut_clip(
                video_path,
                hl.get("start", 0),
                hl.get("end", 30),
                clip_path,
                payload.aspect_ratio,
                payload.font_size,
            )

            if ok and os.path.exists(clip_path):
                clip_paths.append(clip_path)
                clip_info.append({
                    "index": idx,
                    "start": round(hl.get("start", 0), 1),
                    "end": round(hl.get("end", 0), 1),
                    "duration": round(hl.get("end", 0) - hl.get("start", 0), 1),
                    "confidence": round(hl.get("confidence", 0.5), 2),
                    "reason": hl.get("reason", ""),
                    "hook_text": hl.get("hook_text", ""),
                })

        if not clip_paths:
            raise HTTPException(status_code=500, detail="Không cắt được clip nào (FFmpeg errors)")

        # Step 5: Package as ZIP
        slug = _slugify(payload.filename or title, "clips")
        zip_path = os.path.join(download_dir, f"viral_{uid}.zip")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, cp in enumerate(clip_paths, 1):
                arc_name = f"{slug}_clip{idx:02d}.mp4"
                zf.write(cp, arc_name)

        out_filename = f"{slug}_viral_clips.zip"
        _schedule_cleanup(zip_path)

        return {
            "success": True,
            "download_url": _build_download_url(zip_path, out_filename),
            "file_id": os.path.basename(zip_path),
            "file_size_mb": _file_size_mb(zip_path),
            "expires_in_seconds": _EXPIRES_IN_SECONDS,
            "type": "viral_clips_zip",
            "clips": clip_info,
            "total_clips": len(clip_info),
            "source_title": title[:200],
            "source_duration_seconds": round(duration, 1),
            "aspect_ratio": payload.aspect_ratio,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("viral_clips failed")
        raise HTTPException(status_code=500, detail=f"AI Viral Clipper thất bại: {e}")
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
