"""
Bilibili Extractor
==================
Single video, playlist, UP owner batch via yt-dlp native extractor.

Supported URL patterns:
  - Single video:   bilibili.com/video/BV1xx...  /  bilibili.com/video/av123
  - Playlist/series: bilibili.com/medialist/  /  bilibili.com/list/
  - UP owner batch: bilibili.com/space/UID/video  (returns channel entries)
  - Short link:     b23.tv/xxx  (resolved upstream by link_resolver)
  - Bangumi:        bilibili.com/bangumi/play/  (requires login for premium)

Cookie:
  - Bilibili free content: no cookie needed.
  - 18+ / vip-only: SESSDATA + bili_jct + DedeUserID from cookie pool.

Error codes:
  bili_login_required  — 18+ or vip-only content without valid cookie
  bili_vip_only        — 大会员 (premium) content
  bili_geo_blocked     — CN-only content
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any

from app.services.base_extractor import (
    BaseExtractor,
    BatchEntry,
    BatchResult,
    ExtractResult,
    FormatInfo,
    SubtitleTrack,
)

logger = logging.getLogger(__name__)

_PLATFORM = "Bilibili"

_URL_PATTERNS = [
    r"bilibili\.com/video/(BV[\w]+|av\d+)",
    r"bilibili\.com/medialist/",
    r"bilibili\.com/list/",
    r"bilibili\.com/space/\d+/video",
    r"bilibili\.com/bangumi/play/",
    r"b23\.tv/",
]

_ERR_MESSAGES = {
    "bili_login_required": "Video Bilibili yêu cầu đăng nhập (18+ hoặc hội viên).",
    "bili_vip_only": "Video này chỉ dành cho 大会员 (Bilibili Premium).",
    "bili_geo_blocked": "Video Bilibili không khả dụng ngoài Trung Quốc.",
}


def _get_bili_cookies_file() -> str | None:
    """Get Bilibili cookie file from pool → env fallback."""
    try:
        from app.services.downloader import _get_cookies_file
        b64_env = os.getenv("BILIBILI_COOKIES_B64", "")
        return _get_cookies_file("bilibili", b64_env)
    except Exception:
        return None


def _classify_error(exc_msg: str) -> str | None:
    msg = exc_msg.lower()
    if "login" in msg or "sign in" in msg or "-101" in msg or "not logged" in msg:
        return "bili_login_required"
    if "vip" in msg or "premium" in msg or "-403" in msg:
        return "bili_vip_only"
    if "geo" in msg or "unavailable in your country" in msg:
        return "bili_geo_blocked"
    return None


def _build_ydl_opts(quality: str = "video", cookies_file: str | None = None) -> dict:
    if quality.startswith("mp3"):
        fmt = "bestaudio/best"
    elif quality == "video_4k":
        fmt = "bestvideo[height<=2160]+bestaudio/best"
    else:
        try:
            h = int(quality.split("_")[1]) if "_" in quality else 1080
        except (IndexError, ValueError):
            h = 1080
        fmt = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"

    opts: dict[str, Any] = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "writesubtitles": True,
        "subtitleslangs": ["zh-Hans", "zh-Hant", "en"],
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    return opts


def extract_bilibili_single(url: str, quality: str = "video") -> dict:
    """
    Extract a single Bilibili video.
    Returns a dict compatible with the legacy downloader contract.
    """
    import yt_dlp

    cookies_file = _get_bili_cookies_file()
    opts = _build_ydl_opts(quality, cookies_file)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        code = _classify_error(str(e))
        if code:
            return {"error_code": code, "error_message": _ERR_MESSAGES.get(code, str(e))}
        raise ValueError(f"Bilibili extraction failed: {e}") from e

    if not info:
        return {"error_code": "no_media_found", "error_message": "Không tìm thấy nội dung."}

    # Collect formats
    formats: list[dict] = []
    for f in info.get("formats", []):
        if not f.get("url"):
            continue
        formats.append({
            "quality": f.get("format_note") or f.get("height", ""),
            "codec": f.get("vcodec", ""),
            "bitrate": int(f.get("tbr", 0) or 0),
            "url": f["url"],
            "ext": f.get("ext", "mp4"),
        })

    # Subtitles
    subtitle_tracks: list[dict] = []
    for lang, subs in (info.get("subtitles") or {}).items():
        if subs:
            subtitle_tracks.append({"language": lang, "url": subs[0].get("url", ""), "format": "vtt"})

    duration_ms = int((info.get("duration") or 0) * 1000)

    return {
        "title": info.get("title", ""),
        "author": info.get("uploader", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration_ms": duration_ms,
        "duration": info.get("duration", 0),
        "formats": formats,
        "subtitle_tracks": subtitle_tracks,
        "direct_url": info.get("url", ""),
        "platform": _PLATFORM,
        "webpage_url": info.get("webpage_url", url),
    }


def extract_bilibili_batch(url: str, limit: int = 50) -> dict:
    """
    Scrape a Bilibili channel/space or playlist.
    Returns {'channel_title', 'entries', 'total_found', 'total_queued'}.
    """
    import yt_dlp

    cookies_file = _get_bili_cookies_file()
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": min(limit, 200),
        "socket_timeout": 30,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        code = _classify_error(str(e))
        return {
            "channel_title": "",
            "entries": [],
            "total_found": 0,
            "total_queued": 0,
            "error_code": code or "processing_failed",
            "error_message": str(e),
        }

    if not info:
        return {
            "channel_title": "Unknown",
            "entries": [],
            "total_found": 0,
            "total_queued": 0,
            "error_code": "no_media_found",
            "error_message": "Không tìm thấy nội dung.",
        }

    entries_raw = info.get("entries") or []
    entries = []
    for e in entries_raw[:limit]:
        ep_url = e.get("url") or e.get("webpage_url", "")
        if not ep_url:
            continue
        if not ep_url.startswith("http"):
            ep_url = f"https://www.bilibili.com/video/{ep_url}"
        entries.append({"url": ep_url, "title": e.get("title", "")})

    return {
        "channel_title": info.get("title") or info.get("uploader", ""),
        "entries": entries,
        "total_found": len(entries_raw),
        "total_queued": len(entries),
        "error_code": None,
        "error_message": None,
    }


# ── Register with REGISTRY ─────────────────────────────────────────────────

class BilibiliExtractor(BaseExtractor):
    platform_name = "Bilibili"
    supported_features = frozenset({"video", "audio", "subtitle", "batch", "channel"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = _URL_PATTERNS

    _test_fixtures = [
        "https://www.bilibili.com/video/BV1GJ411x7h7",
    ]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        try:
            raw = extract_bilibili_single(url, quality)
        except Exception as e:
            return self._make_error_result("processing_failed", str(e))

        if raw.get("error_code"):
            return ExtractResult.error(self.platform_name, raw["error_code"], raw.get("error_message", ""))

        return ExtractResult(
            platform=self.platform_name,
            source_type="audio" if quality.startswith("mp3") else "video",
            title=raw.get("title", ""),
            author=raw.get("author", ""),
            thumbnail=raw.get("thumbnail", ""),
            duration_ms=raw.get("duration_ms", 0),
            formats=[
                FormatInfo(
                    quality=str(f.get("quality", "")),
                    codec=f.get("codec", ""),
                    bitrate=f.get("bitrate", 0),
                    url=f.get("url", ""),
                    ext=f.get("ext", "mp4"),
                )
                for f in raw.get("formats", [])
            ],
            subtitle_tracks=[
                SubtitleTrack(s["language"], s["url"], s.get("format", "vtt"))
                for s in raw.get("subtitle_tracks", [])
            ],
            direct_url=raw.get("direct_url", ""),
            extraction_method="ytdlp",
        )

    def extract_batch(self, url: str, limit: int = 50, **kwargs) -> BatchResult:
        try:
            raw = extract_bilibili_batch(url, limit)
        except Exception as e:
            return self._make_error_batch("processing_failed", str(e))

        if raw.get("error_code"):
            return BatchResult.error(self.platform_name, raw["error_code"], raw.get("error_message", ""))

        return BatchResult(
            platform=self.platform_name,
            channel_title=raw.get("channel_title", ""),
            entries=[BatchEntry(e["url"], e.get("title", "")) for e in raw.get("entries", [])],
            total_found=raw.get("total_found", 0),
            total_queued=raw.get("total_queued", 0),
        )


try:
    from app.services.extractor_registry import REGISTRY
    REGISTRY.register(BilibiliExtractor())
except Exception as _e:
    logger.warning("Could not register BilibiliExtractor: %s", _e)
