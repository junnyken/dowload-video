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
    """Run full heuristic media_analyzer first, falling back to ffprobe."""
    try:
        from app.core.media_analyzer import build_analysis

        analysis = await asyncio.to_thread(build_analysis, video_path)
        if analysis and (analysis.get("probe") or {}).get("duration_s", 0) > 0:
            return analysis
    except Exception as exc:
        logger.warning("viral_clips build_analysis failed, falling back to ffprobe: %s", exc)

    # Fallback: Get video info via ffprobe
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


async def _try_fetch_transcript(url: str, work_dir: str) -> Optional[str]:
    """Best-effort subtitle fetch (manual or auto captions) as SRT text."""
    import glob as _glob
    import yt_dlp

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["vi", "vi-VN", "en", "en-US"],
        "subtitlesformat": "srt/vtt/best",
        "outtmpl": os.path.join(work_dir, "cap.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "retries": 1,
    }

    def _do() -> None:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

    try:
        await asyncio.to_thread(_do)
    except Exception as exc:
        logger.debug("viral_clips caption fetch skipped: %s", exc)
        return None

    candidates: list[str] = []
    for ext in ("srt", "vtt"):
        candidates.extend(sorted(_glob.glob(os.path.join(work_dir, f"cap*.{ext}"))))
    if not candidates:
        return None

    # Prefer Vietnamese if available, otherwise first match
    chosen = next(
        (p for p in candidates if ".vi" in os.path.basename(p).lower()),
        candidates[0],
    )
    try:
        with open(chosen, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        if chosen.endswith(".vtt"):
            from app.services.subtitle_format import vtt_to_clean_srt
            return vtt_to_clean_srt(raw)
        return raw if raw.strip() else None
    except Exception:
        return None


def _slice_srt_for_clip(
    srt_content: str, start: float, end: float, out_srt_path: str
) -> Optional[str]:
    """Slice SRT cues falling within [start, end], shift by -start, and write file."""
    from app.services.subtitle_format import (
        Cue,
        parse_srt,
        seconds_to_srt_timestamp,
        serialize_srt,
        timestamp_to_seconds,
    )

    clip_len = max(0.0, end - start)
    if clip_len <= 0:
        return None

    try:
        cues = parse_srt(srt_content)
    except Exception:
        return None

    sliced: list[Cue] = []
    for cue in cues:
        try:
            cs = timestamp_to_seconds(cue.start)
            ce = timestamp_to_seconds(cue.end)
        except ValueError:
            continue
        if ce <= start or cs >= end:
            continue
        ns = max(0.0, cs - start)
        ne = min(clip_len, ce - start)
        if ne <= ns or not cue.text.strip():
            continue
        sliced.append(
            Cue(
                index=len(sliced) + 1,
                start=seconds_to_srt_timestamp(ns),
                end=seconds_to_srt_timestamp(ne),
                text=cue.text.strip(),
            )
        )

    if not sliced:
        return None

    with open(out_srt_path, "w", encoding="utf-8") as f:
        f.write(serialize_srt(sliced))
    return out_srt_path


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
    subtitle_srt: Optional[str] = None, cwd: Optional[str] = None,
) -> bool:
    """Cut a single clip with aspect ratio crop and optional subtitle burn-in."""
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

    if subtitle_srt:
        vf_parts.append(
            f"subtitles={subtitle_srt}:force_style="
            f"'FontSize={int(font_size)},FontName=Arial,"
            f"PrimaryColour=&H00FFFFFF&,"
            f"OutlineColour=&H00000000&,"
            f"BackColour=&H80000000&,"
            f"Outline=2,Shadow=1,"
            f"MarginV=35'"
        )

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-i", os.path.abspath(video_path),
    ]

    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])

    cmd.extend(thread_args())
    cmd.extend([
        "-c:v", "libx264", "-crf", "21", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        os.path.abspath(output_path),
    ])

    result = await asyncio.to_thread(
        subprocess.run, cmd, cwd=cwd, capture_output=True, timeout=120
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

        # Step 2: Analyze video + best-effort captions
        analysis = await _analyze_video(video_path)
        transcript_srt = await _try_fetch_transcript(payload.url, work_dir)

        # Step 3: Detect highlights (heuristic + AI)
        highlights = await _detect_highlights_ai(
            video_path, analysis, transcript_srt, payload.max_clips, payload.clip_duration
        )

        if not highlights:
            raise HTTPException(status_code=422, detail="Không phát hiện được đoạn nổi bật nào trong video.")

        # Step 4: Cut each clip
        clip_paths = []
        clip_info = []
        for idx, hl in enumerate(highlights, 1):
            clip_name = f"clip_{idx:02d}_{uid}.mp4"
            clip_path = os.path.join(work_dir, clip_name)
            clip_start = float(hl.get("start", 0))
            clip_end = float(hl.get("end", 30))

            clip_srt_name: Optional[str] = None
            if payload.burn_subtitles and transcript_srt:
                srt_rel = f"clip_{idx:02d}.srt"
                if _slice_srt_for_clip(
                    transcript_srt,
                    clip_start,
                    clip_end,
                    os.path.join(work_dir, srt_rel),
                ):
                    clip_srt_name = srt_rel

            ok = await _cut_clip(
                video_path,
                clip_start,
                clip_end,
                clip_path,
                payload.aspect_ratio,
                payload.font_size,
                subtitle_srt=clip_srt_name,
                cwd=work_dir if clip_srt_name else None,
            )

            if ok and os.path.exists(clip_path):
                clip_paths.append(clip_path)
                clip_info.append({
                    "index": idx,
                    "start": round(clip_start, 1),
                    "end": round(clip_end, 1),
                    "duration": round(clip_end - clip_start, 1),
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
    except Exception:
        logger.exception("viral_clips failed")
        raise HTTPException(
            status_code=500,
            detail="AI Viral Clipper thất bại do lỗi xử lý nội bộ. Vui lòng thử lại sau.",
        )
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
