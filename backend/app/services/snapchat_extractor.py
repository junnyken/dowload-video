"""
Snapchat Public Extractor
=========================
Downloads public Spotlight clips and public Stories via yt-dlp.

Supported URLs:
  - Spotlight: snapchat.com/spotlight/<clip_id>
  - Story:     snapchat.com/add/<username>  (public stories)
  - Short:     story.snapchat.com/<username>  (redirects to public story)

Limitations:
  - Private snaps and DMs: not supported.
  - Ephemeral snaps already expired: not supported.
  - yt-dlp Snapchat extractor is partial — some Spotlight clips may fail.

Error codes: snap_private_content, snap_extract_failed
"""

from __future__ import annotations

import logging
import re

import yt_dlp

from app.services.base_extractor import BaseExtractor, ExtractResult, FormatInfo

logger = logging.getLogger(__name__)

_PLATFORM = "Snapchat"

_URL_PATTERNS = [
    r"snapchat\.com/spotlight/",
    r"snapchat\.com/add/",
    r"story\.snapchat\.com/",
    r"snapchat\.com/p/",
]


class SnapchatExtractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({"video", "image"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = _URL_PATTERNS
    _test_fixtures = ["https://www.snapchat.com/spotlight/p/abc123"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")

        # Block private content hints in URL
        if re.search(r"/p/[^/]+/[^/]+/[^/]+/", url):
            return self._make_error_result(
                "snap_private_content",
                "Snap này là nội dung cá nhân không thể tải.",
            )

        fmt = "bestaudio/best" if quality.startswith("mp3") else "best[ext=mp4]/best"
        opts = {
            "format": fmt,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 20,
            "retries": 2,
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except yt_dlp.utils.DownloadError as e:
            msg = str(e).lower()
            if "private" in msg or "login" in msg or "not available" in msg:
                return self._make_error_result(
                    "snap_private_content",
                    "Snap này là riêng tư hoặc đã hết hạn.",
                )
            return self._make_error_result("snap_extract_failed", f"Snapchat trích xuất thất bại: {e}")
        except Exception as e:
            return self._make_error_result("snap_extract_failed", str(e))

        if not info or not (info.get("url") or info.get("formats")):
            return self._make_error_result(
                "snap_extract_failed",
                "Không thể trích xuất từ Snapchat. Clip có thể đã hết hạn hoặc bị giới hạn.",
            )

        formats = [
            FormatInfo(
                quality=str(f.get("format_note") or f.get("height") or "best"),
                codec=f.get("vcodec", ""),
                url=f.get("url", ""),
                ext=f.get("ext", "mp4"),
            )
            for f in info.get("formats", [])
            if f.get("url")
        ]

        return ExtractResult(
            platform=self.platform_name,
            source_type="video",
            title=info.get("title", "Snapchat Spotlight"),
            author=info.get("uploader", ""),
            thumbnail=info.get("thumbnail", ""),
            duration_ms=int((info.get("duration") or 0) * 1000),
            formats=formats,
            direct_url=info.get("url", ""),
            extraction_method="ytdlp",
        )


try:
    from app.services.extractor_registry import REGISTRY
    REGISTRY.register(SnapchatExtractor())
except Exception as _e:
    logger.warning("Could not register SnapchatExtractor: %s", _e)
