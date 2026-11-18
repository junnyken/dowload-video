"""
AI Vietnamize — "Tải & Việt hóa 1 chạm"
==========================================
One-click pipeline: Download → ASR → Translate → Burn Vietnamese subtitles.

Endpoint:
  POST /process/vietnamize  — accepts a video URL, returns video with burned-in
                              Vietnamese subtitles (or downloadable .srt)

Pipeline steps (all server-side):
  1. Download video via yt-dlp (reuses existing downloader)
  2. Extract audio → run ASR (Whisper/Gemini) to get SRT transcript
  3. Translate SRT to Vietnamese (via LLM or Google Translate)
  4. Burn translated subtitles into video (FFmpeg drawtext/subtitles filter)
  5. Return processed video + cleanup after 20 min

Tier: Pro / Team / Enterprise / API
Env:
  ASR_ENABLED=true (required)
  VIETNAMIZE_TIMEOUT_SEC (default 900 = 15 min)
  VIETNAMIZE_MAX_DURATION_MIN (default 30)
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
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
_CLEANUP_COUNTDOWN = 20 * 60
_EXPIRES_IN_SECONDS = 1200
_TIMEOUT_SEC = int(os.getenv("VIETNAMIZE_TIMEOUT_SEC", "900"))
_MAX_DURATION_MIN = int(os.getenv("VIETNAMIZE_MAX_DURATION_MIN", "30"))


def _safe_download_dir() -> str:
    os.makedirs(_DOWNLOADS_DIR, exist_ok=True)
    return _DOWNLOADS_DIR


def _schedule_cleanup(path: str) -> None:
    try:
        from app.tasks.video_tasks import delete_local_file
        delete_local_file.apply_async((path,), countdown=_CLEANUP_COUNTDOWN)
    except Exception as e:
        logger.warning("vietnamize cleanup schedule failed: %s", e)


def _build_download_url(output_path: str, filename: str) -> str:
    from app.core.local_download import download_url
    return download_url(output_path, filename)


def _file_size_mb(path: str) -> float:
    try:
        return round(os.path.getsize(path) / (1024 * 1024), 2)
    except Exception:
        return 0.0


def _slugify(name: str, fallback: str = "output") -> str:
    from app.core.local_download import display_name
    return display_name(name, fallback)


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
                "message": "Tính năng 'AI Việt hóa 1 chạm' yêu cầu đăng nhập và gói Pro trở lên.",
                "required_tier": "pro",
            },
        )
    tier = get_user_tier(str(uid)) or "free"
    if tier not in ("pro", "team", "enterprise", "api"):
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "message": "Tính năng 'AI Việt hóa 1 chạm' chỉ dành cho gói Pro / Team. Vui lòng nâng cấp để sử dụng.",
                "current_tier": tier,
                "required_tier": "pro",
            },
        )
    return tier


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class VietnamizeRequest(BaseModel):
    url: str                            # Video URL (YouTube, TikTok, etc.)
    burn_subtitles: bool = True         # Burn subs into video (False = SRT only)
    font_size: int = 22                 # Subtitle font size
    target_lang: str = "vi"             # Target language (default Vietnamese)
    source_lang: str = "auto"           # Source language (auto-detect)
    filename: Optional[str] = "video"


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

async def _download_video(url: str, work_dir: str) -> tuple[str, dict]:
    """Download video using yt-dlp. Returns (video_path, info_dict)."""
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
        "fragment_retries": 5,
    }

    def _do():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info

    info = await asyncio.to_thread(_do)
    if not info:
        raise HTTPException(status_code=502, detail="Không tải được video từ URL này")

    # Find the downloaded file
    for f in os.listdir(work_dir):
        if f.startswith("source.") and not f.endswith(".part"):
            return os.path.join(work_dir, f), info

    raise HTTPException(status_code=500, detail="Video đã tải nhưng không tìm thấy file")


async def _extract_audio(video_path: str, work_dir: str) -> str:
    """Extract audio track as MP3 (48kbps mono) compatible with ASR providers."""
    from app.services.asr.chunking import extract_audio
    from app.services.asr.types import AsrError

    audio_path = os.path.join(work_dir, "audio.mp3")
    try:
        await asyncio.to_thread(extract_audio, video_path, audio_path, timeout=300)
    except AsrError as e:
        logger.warning("vietnamize audio extract failed: %s", e)
        raise HTTPException(status_code=500, detail="Không trích xuất được audio từ video")
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=500, detail="Không trích xuất được audio từ video")
    return audio_path


async def _run_asr(
    audio_path: str, work_dir: str, source_lang: str, duration_sec: float = 0.0
) -> str:
    """Run ASR on audio file via app.services.asr. Returns path to SRT file."""
    from app.services.asr import (
        AsrError,
        ERROR_MESSAGES_VI,
        NoSpeechError,
        ProviderUnavailable,
        config as asr_config,
        get_provider,
    )
    from app.services.asr.chunking import probe_duration_sec
    from app.services.asr.pipeline import CostTracker, transcribe_chunked
    from app.services.asr_service import segments_to_cues
    from app.services.subtitle_format import serialize_srt

    if not asr_config.asr_enabled():
        raise HTTPException(
            status_code=503,
            detail="Dịch vụ nhận dạng giọng nói (ASR) chưa được bật trên server.",
        )

    srt_path = os.path.join(work_dir, "transcript.srt")
    lang_arg = None if not source_lang or source_lang == "auto" else source_lang

    try:
        if duration_sec <= 0:
            duration_sec = await asyncio.to_thread(probe_duration_sec, audio_path)
        provider = get_provider()
        result = await asyncio.to_thread(
            lambda: transcribe_chunked(
                provider,
                audio_path,
                max(duration_sec, 1.0),
                work_dir=work_dir,
                tracker=CostTracker(),
                language=lang_arg,
            )
        )
    except ProviderUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e) or "Dịch vụ ASR chưa sẵn sàng.")
    except NoSpeechError:
        raise HTTPException(
            status_code=422,
            detail="Video không có giọng nói hoặc ASR không nhận dạng được nội dung.",
        )
    except AsrError as e:
        msg = ERROR_MESSAGES_VI.get(e.code, "Lỗi nhận dạng giọng nói.")
        raise HTTPException(status_code=502, detail=msg)

    if not result.segments:
        raise HTTPException(
            status_code=422,
            detail="Video không có giọng nói hoặc ASR không nhận dạng được nội dung.",
        )

    cues = segments_to_cues(result.segments)
    srt_text = serialize_srt(cues)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_text)

    if not os.path.exists(srt_path) or os.path.getsize(srt_path) < 10:
        raise HTTPException(
            status_code=422,
            detail="Video không có giọng nói hoặc ASR không nhận dạng được nội dung.",
        )

    return srt_path


async def _translate_srt(srt_path: str, target_lang: str) -> str:
    """Translate SRT content to target language. Returns path to translated SRT."""
    translated_path = srt_path.replace(".srt", f"_{target_lang}.srt")

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
        shutil.copy2(srt_path, translated_path)
        return translated_path

    # Try LLM translation first (higher quality), fallback to Google Translate
    translated = await _translate_with_llm(text_lines, target_lang)
    if not translated:
        translated = await _translate_with_google(text_lines, target_lang)

    if translated and len(translated) == len(text_indices):
        for idx, new_txt in zip(text_indices, translated):
            lines[idx] = new_txt

    with open(translated_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return translated_path


async def _translate_with_llm(texts: list[str], target_lang: str) -> Optional[list[str]]:
    """Translate using Gemini/OpenAI LLM."""
    try:
        from app.services.llm_client import call_llm

        lang_names = {"vi": "tiếng Việt", "en": "English", "ja": "日本語", "ko": "한국어"}
        lang_name = lang_names.get(target_lang, target_lang)

        batch_size = 30
        all_translated = []

        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            numbered = "\n".join(f"{j+1}. {t}" for j, t in enumerate(chunk))

            prompt = (
                f"Dịch chính xác {len(chunk)} dòng phụ đề sau sang {lang_name}. "
                f"Giữ nguyên số thứ tự, mỗi dòng dịch trên 1 dòng riêng. "
                f"CHỈ trả về bản dịch, KHÔNG thêm giải thích:\n\n{numbered}"
            )

            result = await asyncio.to_thread(call_llm, prompt, 4096)
            if not result:
                return None

            # Parse numbered lines
            parsed = []
            for line in result.strip().splitlines():
                # Remove leading number and dot
                cleaned = re.sub(r"^\d+\.\s*", "", line.strip())
                if cleaned:
                    parsed.append(cleaned)

            if len(parsed) == len(chunk):
                all_translated.extend(parsed)
            else:
                return None  # Fallback to Google

        return all_translated if len(all_translated) == len(texts) else None

    except Exception as e:
        logger.warning("LLM translation failed, falling back: %s", e)
        return None


async def _translate_with_google(texts: list[str], target_lang: str) -> Optional[list[str]]:
    """Translate using Google Translate API (free tier)."""
    import httpx

    translated = []
    batch_size = 25

    async with httpx.AsyncClient(
        timeout=15.0,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0"},
    ) as client:
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            joined = "\n".join(chunk)

            # Primary: clients5 endpoint
            try:
                r = await client.get(
                    "https://clients5.google.com/translate_a/t",
                    params={"client": "dict-chrome-ex", "sl": "auto", "tl": target_lang, "q": joined},
                )
                if r.status_code == 200:
                    d = r.json()
                    merged = ""
                    if isinstance(d, list) and d:
                        first = d[0]
                        if isinstance(first, list) and first and isinstance(first[0], str):
                            merged = first[0]
                        elif isinstance(first, str):
                            merged = first
                    if merged:
                        parts = [p.strip() for p in merged.split("\n")]
                        if len(parts) == len(chunk):
                            translated.extend(parts)
                            continue
                        else:
                            for j in range(len(chunk)):
                                translated.append(parts[j] if j < len(parts) and parts[j] else chunk[j])
                            continue
            except Exception:
                pass

            # Fallback: gtx endpoint
            try:
                r2 = await client.get(
                    "https://translate.googleapis.com/translate_a/single",
                    params={"client": "gtx", "sl": "auto", "tl": target_lang, "dt": "t", "q": joined},
                )
                if r2.status_code == 200:
                    data = r2.json()
                    merged = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
                    parts = [p.strip() for p in merged.split("\n")]
                    if len(parts) == len(chunk):
                        translated.extend(parts)
                    else:
                        for j in range(len(chunk)):
                            translated.append(parts[j] if j < len(parts) and parts[j] else chunk[j])
                    continue
            except Exception:
                pass

            # Give up on this chunk — keep original
            translated.extend(chunk)

    return translated if len(translated) == len(texts) else None


async def _burn_subtitles(
    video_path: str, srt_path: str, output_path: str, font_size: int = 22
) -> bool:
    """Burn SRT subtitles into video using FFmpeg (cwd-relative SRT path)."""
    from app.core.ffmpeg_budget import thread_args

    sub_dir = os.path.dirname(os.path.abspath(srt_path))
    burn_srt = os.path.join(sub_dir, "burn.srt")
    if os.path.abspath(srt_path) != burn_srt:
        shutil.copy2(srt_path, burn_srt)

    vf = (
        f"subtitles=burn.srt:force_style="
        f"'FontSize={int(font_size)},FontName=Arial,"
        f"PrimaryColour=&H00FFFFFF&,"
        f"OutlineColour=&H00000000&,"
        f"BackColour=&H80000000&,"
        f"Outline=2,Shadow=1,"
        f"MarginV=25'"
    )

    cmd = [
        "ffmpeg", "-y", "-i", os.path.abspath(video_path),
        "-vf", vf,
        *thread_args(),
        "-c:a", "copy",
        "-c:v", "libx264", "-crf", "21", "-preset", "fast",
        "-movflags", "+faststart",
        os.path.abspath(output_path),
    ]

    result = await asyncio.to_thread(
        subprocess.run, cmd, cwd=sub_dir, capture_output=True, timeout=_TIMEOUT_SEC
    )

    if result.returncode != 0:
        logger.error("vietnamize burn failed: %s", result.stderr.decode(errors='replace')[-500:])
        return False

    return os.path.exists(output_path)


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@router.post("/process/vietnamize")
@limiter.limit("5/minute")
async def vietnamize(
    payload: VietnamizeRequest,
    request: Request,
    user: Optional[dict] = Depends(_auth_optional_user),
):
    """
    AI Việt hóa 1 chạm:
    Download → ASR → Translate → Burn Vietnamese subtitles into video.

    Returns the processed video with burned-in subtitles (or just the .srt
    when burn_subtitles=false).
    """
    tier = _enforce_tier(request, user)

    from app.core.ssrf_guard import assert_safe_url
    assert_safe_url(payload.url)

    target_lang = payload.target_lang.strip().lower()
    if target_lang not in ("vi", "en", "ja", "ko", "zh", "fr", "de", "es", "th", "id"):
        raise HTTPException(status_code=400, detail=f"Ngôn ngữ '{target_lang}' chưa được hỗ trợ")

    download_dir = _safe_download_dir()
    uid = new_token()
    work_dir = tempfile.mkdtemp(prefix=f".viet_{uid}_", dir=download_dir)

    output_path: Optional[str] = None

    try:
        # Step 1: Download video
        logger.info("vietnamize.start url=%s lang=%s", payload.url[:80], target_lang)
        video_path, info = await _download_video(payload.url, work_dir)

        # Check duration
        duration = float((info or {}).get("duration", 0) or 0)
        if duration > _MAX_DURATION_MIN * 60:
            raise HTTPException(
                status_code=400,
                detail=f"Video quá dài ({duration/60:.0f} phút). Giới hạn: {_MAX_DURATION_MIN} phút."
            )

        title = (info or {}).get("title", "video") or "video"

        # Step 2: Extract audio
        audio_path = await _extract_audio(video_path, work_dir)

        # Step 3: Run ASR
        srt_path = await _run_asr(audio_path, work_dir, payload.source_lang, duration)

        # Step 4: Translate to target language
        translated_srt = await _translate_srt(srt_path, target_lang)

        if not payload.burn_subtitles:
            # Return just the translated SRT file
            final_srt = os.path.join(download_dir, f"viet_{uid}.srt")
            shutil.copy2(translated_srt, final_srt)
            slug = _slugify(payload.filename or title, "video")
            out_filename = f"{slug}_{target_lang}.srt"
            _schedule_cleanup(final_srt)
            with open(translated_srt, "r", encoding="utf-8", errors="replace") as _f:
                cue_count = sum(1 for line in _f if "-->" in line)
            return {
                "success": True,
                "download_url": _build_download_url(final_srt, out_filename),
                "file_id": os.path.basename(final_srt),
                "file_size_mb": _file_size_mb(final_srt),
                "expires_in_seconds": _EXPIRES_IN_SECONDS,
                "type": "subtitle",
                "language": target_lang,
                "cue_count": cue_count,
            }

        # Step 5: Burn subtitles into video
        output_path = os.path.join(download_dir, f"viet_{uid}.mp4")
        ok = await _burn_subtitles(video_path, translated_srt, output_path, payload.font_size)
        if not ok:
            raise HTTPException(status_code=500, detail="Ghép phụ đề vào video thất bại (FFmpeg error)")

        slug = _slugify(payload.filename or title, "video")
        out_filename = f"{slug}_{target_lang}_sub.mp4"
        _schedule_cleanup(output_path)

        return {
            "success": True,
            "download_url": _build_download_url(output_path, out_filename),
            "file_id": os.path.basename(output_path),
            "file_size_mb": _file_size_mb(output_path),
            "expires_in_seconds": _EXPIRES_IN_SECONDS,
            "type": "video_with_subtitles",
            "language": target_lang,
            "duration_seconds": round(duration, 1),
            "title_original": title[:200],
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("vietnamize failed")
        if output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        raise HTTPException(
            status_code=500,
            detail="AI Việt hóa thất bại do lỗi xử lý nội bộ. Vui lòng thử lại sau.",
        )
    finally:
        # Cleanup work directory (source video, audio, temp SRT)
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
