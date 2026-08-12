"""
Alt Platforms Extractor
=======================
Groups low-effort yt-dlp-native platforms into one module:
  - VK (VKontakte)
  - Twitch (VOD + Clips, no live stream)
  - Rumble
  - Odysee (LBRY)
  - Dailymotion

All use yt-dlp with minimal custom logic. Each platform has its own
BaseExtractor subclass so the registry can route accurately.

Error codes: vk_login_required, twitch_vod_unavailable, platform_not_supported
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import yt_dlp

from app.services.base_extractor import (
    BaseExtractor,
    BatchEntry,
    BatchResult,
    ExtractResult,
    FormatInfo,
    SubtitleTrack,
)

logger = logging.getLogger(__name__)


# ─── Shared yt-dlp wrapper ─────────────────────────────────────────────────

def _ytdlp_extract(url: str, quality: str = "video", cookies_file: str | None = None) -> dict:
    """Generic yt-dlp single-video extraction returning a raw info dict."""
    if quality.startswith("mp3"):
        fmt = "bestaudio/best"
    else:
        try:
            h = int(quality.split("_")[1]) if "_" in quality and quality != "video" else 1080
        except (IndexError, ValueError):
            h = 1080
        fmt = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"

    opts: dict[str, Any] = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}
    except yt_dlp.utils.DownloadError as e:
        raise ValueError(str(e)) from e


def _build_extract_result(platform: str, info: dict, quality: str) -> ExtractResult:
    formats = [
        FormatInfo(
            quality=str(f.get("format_note") or f.get("height") or ""),
            codec=f.get("vcodec", ""),
            bitrate=int(f.get("tbr", 0) or 0),
            url=f.get("url", ""),
            ext=f.get("ext", "mp4"),
        )
        for f in info.get("formats", [])
        if f.get("url")
    ]
    subtitle_tracks = [
        SubtitleTrack(lang, subs[0].get("url", ""), "vtt")
        for lang, subs in (info.get("subtitles") or {}).items()
        if subs
    ]
    return ExtractResult(
        platform=platform,
        source_type="audio" if quality.startswith("mp3") else "video",
        title=info.get("title", ""),
        author=info.get("uploader") or info.get("channel", ""),
        thumbnail=info.get("thumbnail", ""),
        duration_ms=int((info.get("duration") or 0) * 1000),
        formats=formats,
        subtitle_tracks=subtitle_tracks,
        direct_url=info.get("url", ""),
        extraction_method="ytdlp",
    )


# ─── VK ────────────────────────────────────────────────────────────────────

class VKExtractor(BaseExtractor):
    platform_name = "VK"
    supported_features = frozenset({"video", "audio", "batch"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [
        r"vk\.com/video",
        r"vk\.com/clip",
        r"vkvideo\.ru/video",
    ]
    _test_fixtures = ["https://vk.com/video-76982440_456239018"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        try:
            info = _ytdlp_extract(url, quality)
        except ValueError as e:
            msg = str(e).lower()
            if "login" in msg or "private" in msg:
                return self._make_error_result("vk_login_required", "VK yêu cầu đăng nhập để xem video này.")
            return self._make_error_result("processing_failed", str(e))

        if not info:
            return self._make_error_result("no_media_found", "Không tìm thấy nội dung VK.")

        return _build_extract_result(self.platform_name, info, quality)

    def extract_batch(self, url: str, limit: int = 30, **kwargs) -> BatchResult:
        opts = {
            "quiet": True, "no_warnings": True,
            "extract_flat": True, "playlistend": min(limit, 100),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as e:
            return self._make_error_batch("processing_failed", str(e))

        entries_raw = info.get("entries") or []
        entries = [
            BatchEntry(e.get("url") or e.get("webpage_url", ""), e.get("title", ""))
            for e in entries_raw[:limit]
            if e.get("url") or e.get("webpage_url")
        ]
        return BatchResult(
            platform=self.platform_name,
            channel_title=info.get("title", ""),
            entries=entries,
            total_found=len(entries_raw),
            total_queued=len(entries),
        )


# ─── Twitch ────────────────────────────────────────────────────────────────

class TwitchExtractor(BaseExtractor):
    platform_name = "Twitch"
    supported_features = frozenset({"video", "audio"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [
        r"twitch\.tv/videos/\d+",           # VOD
        r"twitch\.tv/\w+/clip/",            # Clip
        r"clips\.twitch\.tv/",              # Clip short URL
    ]
    _test_fixtures = ["https://www.twitch.tv/videos/2040070207"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")

        # Block live stream attempts
        if re.search(r"twitch\.tv/[\w]+$", url, re.IGNORECASE) and "videos" not in url and "clip" not in url.lower():
            return self._make_error_result(
                "platform_not_supported",
                "Live stream Twitch không được hỗ trợ trong Phase 15. Chỉ hỗ trợ VOD và Clip.",
            )

        try:
            info = _ytdlp_extract(url, quality)
        except ValueError as e:
            msg = str(e).lower()
            if "subscriber" in msg or "sub-only" in msg:
                return self._make_error_result("twitch_vod_unavailable", "VOD Twitch chỉ dành cho subscriber hoặc đã bị xoá.")
            if "deleted" in msg or "removed" in msg or "unavailable" in msg:
                return self._make_error_result("twitch_vod_unavailable", _ERR_TWITCH_VOD)
            return self._make_error_result("processing_failed", str(e))

        if not info:
            return self._make_error_result("twitch_vod_unavailable", _ERR_TWITCH_VOD)

        return _build_extract_result(self.platform_name, info, quality)


_ERR_TWITCH_VOD = "VOD Twitch không còn khả dụng (đã bị xoá hoặc hết hạn)."


# ─── Rumble ────────────────────────────────────────────────────────────────

class RumbleExtractor(BaseExtractor):
    platform_name = "Rumble"
    supported_features = frozenset({"video", "audio"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [r"rumble\.com/"]
    _test_fixtures = ["https://rumble.com/v2fsi6w-test-video.html"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        try:
            info = _ytdlp_extract(url, quality)
        except ValueError as e:
            return self._make_error_result("processing_failed", str(e))

        if not info:
            return self._make_error_result("no_media_found", "Không tìm thấy video Rumble.")

        return _build_extract_result(self.platform_name, info, quality)


# ─── Odysee ────────────────────────────────────────────────────────────────

class OdyseeExtractor(BaseExtractor):
    platform_name = "Odysee"
    supported_features = frozenset({"video", "audio"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [r"odysee\.com/", r"lbry\.tv/"]
    _test_fixtures = ["https://odysee.com/@veritasium:f/the-big-misconception-about-electricity:a"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        try:
            info = _ytdlp_extract(url, quality)
        except ValueError as e:
            return self._make_error_result("processing_failed", str(e))

        if not info:
            return self._make_error_result("no_media_found", "Không tìm thấy video Odysee.")

        return _build_extract_result(self.platform_name, info, quality)


# ─── Dailymotion ────────────────────────────────────────────────────────────

class DailymotionExtractor(BaseExtractor):
    platform_name = "Dailymotion"
    supported_features = frozenset({"video", "audio", "batch"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [r"dailymotion\.com/"]
    _test_fixtures = ["https://www.dailymotion.com/video/x8ndjq1"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        try:
            info = _ytdlp_extract(url, quality)
        except ValueError as e:
            return self._make_error_result("processing_failed", str(e))

        if not info:
            return self._make_error_result("no_media_found", "Không tìm thấy video Dailymotion.")

        return _build_extract_result(self.platform_name, info, quality)

    def extract_batch(self, url: str, limit: int = 30, **kwargs) -> BatchResult:
        opts = {
            "quiet": True, "no_warnings": True,
            "extract_flat": True, "playlistend": min(limit, 100),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except Exception as e:
            return self._make_error_batch("processing_failed", str(e))

        entries_raw = info.get("entries") or []
        entries = [
            BatchEntry(e.get("url") or e.get("webpage_url", ""), e.get("title", ""))
            for e in entries_raw[:limit]
            if e.get("url") or e.get("webpage_url")
        ]
        return BatchResult(
            platform=self.platform_name,
            channel_title=info.get("title", ""),
            entries=entries,
            total_found=len(entries_raw),
            total_queued=len(entries),
        )


class LinkedInExtractor(BaseExtractor):
    """LinkedIn public video posts via yt-dlp."""
    platform_name = "LinkedIn"
    supported_features = frozenset({"video"})
    cost_tier = "medium"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [r"linkedin\.com/"]
    _test_fixtures = ["https://www.linkedin.com/posts/"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        if not re.search(r"linkedin\.com/(?:posts|feed/update|learning)/", url, re.IGNORECASE):
            return self._make_error_result(
                "linkedin_unsupported_url",
                "Chỉ hỗ trợ link bài đăng LinkedIn công khai (linkedin.com/posts/...).",
            )
        try:
            info = _ytdlp_extract(url, quality)
        except Exception as e:
            return self._make_error_result("linkedin_extraction_failed", str(e))

        if not info:
            return self._make_error_result("no_media_found", "Không tìm thấy video LinkedIn.")

        return _build_extract_result(self.platform_name, info, quality)


class TwitterSpacesExtractor(BaseExtractor):
    """Twitter/X Spaces audio extraction via yt-dlp."""
    platform_name = "Twitter Spaces"
    supported_features = frozenset({"audio"})
    cost_tier = "medium"
    requires_cookie = True
    supports_proxy = False

    _URL_PATTERNS = [r"(?:twitter|x)\.com/i/spaces/"]
    _test_fixtures = []

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "mp3_128")
        cookies_file = kwargs.get("cookies_file")

        _tw_cookies = os.getenv("TWITTER_COOKIES_B64", "")
        _ck_file = cookies_file
        if not _ck_file and _tw_cookies:
            import base64, tempfile
            try:
                _ck_file = tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".txt", delete=False, prefix="tw_sp_"
                ).name
                with open(_ck_file, "wb") as _f:
                    _f.write(base64.b64decode(_tw_cookies))
            except Exception:
                _ck_file = None

        try:
            info = _ytdlp_extract(url, quality="mp3_128", cookies_file=_ck_file)
        except Exception as e:
            return self._make_error_result("spaces_extraction_failed", str(e))

        if not info:
            return self._make_error_result("no_audio_found", "Không tìm thấy audio Spaces.")

        return _build_extract_result(self.platform_name, info, quality)


# ── Register all ───────────────────────────────────────────────────────────

try:
    from app.services.extractor_registry import REGISTRY
    REGISTRY.register(VKExtractor())
    REGISTRY.register(TwitchExtractor())
    REGISTRY.register(RumbleExtractor())
    REGISTRY.register(OdyseeExtractor())
    REGISTRY.register(DailymotionExtractor())
    REGISTRY.register(LinkedInExtractor())
    REGISTRY.register(TwitterSpacesExtractor())
except Exception as _e:
    logger.warning("Could not register alt platform extractors: %s", _e)
