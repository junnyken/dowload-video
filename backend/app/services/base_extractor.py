"""
BaseExtractor — Abstract interface for all VidGrab extractors.

Every platform extractor must subclass BaseExtractor and implement:
  - detect_url(url)      → bool
  - extract_single(url)  → ExtractResult
  - extract_batch(urls)  → BatchResult   (optional, default raises NotImplementedError)

ExtractResult and BatchResult are TypedDicts — no runtime overhead.

Usage:
    from app.services.extractor_registry import REGISTRY
    extractor = REGISTRY.detect(url)
    result = extractor.extract_single(url)
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Literal, Optional


# ─── Canonical result schemas ──────────────────────────────────────────────

class FormatInfo:
    __slots__ = ("quality", "codec", "bitrate", "url", "size_estimate_mb", "ext")

    def __init__(
        self,
        quality: str,
        codec: str = "",
        bitrate: int = 0,
        url: str = "",
        size_estimate_mb: float = 0.0,
        ext: str = "mp4",
    ):
        self.quality = quality
        self.codec = codec
        self.bitrate = bitrate
        self.url = url
        self.size_estimate_mb = size_estimate_mb
        self.ext = ext

    def to_dict(self) -> dict:
        return {
            "quality": self.quality,
            "codec": self.codec,
            "bitrate": self.bitrate,
            "url": self.url,
            "size_estimate_mb": self.size_estimate_mb,
            "ext": self.ext,
        }


class SubtitleTrack:
    __slots__ = ("language", "url", "format")

    def __init__(self, language: str, url: str, format: str = "vtt"):
        self.language = language
        self.url = url
        self.format = format

    def to_dict(self) -> dict:
        return {"language": self.language, "url": self.url, "format": self.format}


class MediaItem:
    """One item inside a carousel/album post."""
    __slots__ = ("url", "media_type", "thumbnail", "filename")

    def __init__(
        self,
        url: str,
        media_type: Literal["video", "image", "audio"] = "video",
        thumbnail: str = "",
        filename: str = "",
    ):
        self.url = url
        self.media_type = media_type
        self.thumbnail = thumbnail
        self.filename = filename

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "media_type": self.media_type,
            "thumbnail": self.thumbnail,
            "filename": self.filename,
        }


class ExtractResult:
    """
    Canonical extraction result.  All extractors return this.
    Serialize to dict via .to_dict() before returning to API.
    """

    def __init__(
        self,
        *,
        platform: str,
        source_type: Literal["video", "audio", "image", "mixed"] = "video",
        title: str = "",
        author: str = "",
        thumbnail: str = "",
        duration_ms: int = 0,
        formats: Optional[list[FormatInfo]] = None,
        subtitle_tracks: Optional[list[SubtitleTrack]] = None,
        media_items: Optional[list[MediaItem]] = None,
        direct_url: str = "",
        local_file_path: str = "",
        local_mp3_path: str = "",
        raw_metadata: Optional[dict] = None,
        extraction_method: str = "ytdlp",
        requires_zip: bool = False,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        self.platform = platform
        self.source_type = source_type
        self.title = title
        self.author = author
        self.thumbnail = thumbnail
        self.duration_ms = duration_ms
        self.formats: list[FormatInfo] = formats or []
        self.subtitle_tracks: list[SubtitleTrack] = subtitle_tracks or []
        self.media_items: list[MediaItem] = media_items or []
        self.direct_url = direct_url
        self.local_file_path = local_file_path
        self.local_mp3_path = local_mp3_path
        self.raw_metadata: dict = raw_metadata or {}
        self.extraction_method = extraction_method
        self.requires_zip = requires_zip
        self.error_code = error_code
        self.error_message = error_message

    @property
    def ok(self) -> bool:
        return self.error_code is None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "platform": self.platform,
            "source_type": self.source_type,
            "title": self.title,
            "author": self.author,
            "thumbnail": self.thumbnail,
            "duration_ms": self.duration_ms,
            "formats": [f.to_dict() for f in self.formats],
            "subtitle_tracks": [s.to_dict() for s in self.subtitle_tracks],
            "media_items": [m.to_dict() for m in self.media_items],
            "direct_url": self.direct_url,
            "local_file_path": self.local_file_path,
            "local_mp3_path": self.local_mp3_path,
            "extraction_method": self.extraction_method,
            "requires_zip": self.requires_zip,
        }
        if self.error_code:
            d["error_code"] = self.error_code
            d["error_message"] = self.error_message
        return d

    @classmethod
    def error(cls, platform: str, code: str, message: str) -> "ExtractResult":
        return cls(platform=platform, error_code=code, error_message=message)


class BatchEntry:
    __slots__ = ("url", "title")

    def __init__(self, url: str, title: str = ""):
        self.url = url
        self.title = title

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title}


class BatchResult:
    def __init__(
        self,
        *,
        platform: str,
        channel_title: str = "",
        entries: Optional[list[BatchEntry]] = None,
        total_found: int = 0,
        total_queued: int = 0,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        self.platform = platform
        self.channel_title = channel_title
        self.entries: list[BatchEntry] = entries or []
        self.total_found = total_found
        self.total_queued = total_queued
        self.error_code = error_code
        self.error_message = error_message

    @property
    def ok(self) -> bool:
        return self.error_code is None

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "channel_title": self.channel_title,
            "entries": [e.to_dict() for e in self.entries],
            "total_found": self.total_found,
            "total_queued": self.total_queued,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }

    @classmethod
    def error(cls, platform: str, code: str, message: str) -> "BatchResult":
        return cls(platform=platform, error_code=code, error_message=message)


# ─── Base class ────────────────────────────────────────────────────────────

class BaseExtractor(ABC):
    """
    Abstract base for all platform extractors.

    Subclasses MUST implement:
      - platform_name  (str property)
      - supported_features  (set property)
      - detect_url(url)  → bool
      - extract_single(url, **kwargs)  → ExtractResult

    Subclasses MAY implement:
      - extract_batch(url, limit)  → BatchResult
    """

    # ── Required class-level attributes ────────────────────────────────

    platform_name: str = ""

    # Features this extractor supports:
    #   video, audio, subtitle, batch, channel, carousel, story, private
    supported_features: frozenset[str] = frozenset()

    # Cost tier: "low" = yt-dlp/free API | "medium" = proxy | "high" = cloud actor
    cost_tier: Literal["low", "medium", "high"] = "low"

    requires_cookie: bool = False
    supports_proxy: bool = True

    # Fixture URLs for CI smoke tests — at least 1 per extractor
    _test_fixtures: list[str] = []

    # ── URL detection ──────────────────────────────────────────────────

    # Subclasses can either implement detect_url() or populate _URL_PATTERNS
    _URL_PATTERNS: list[str] = []

    def detect_url(self, url: str) -> bool:
        """Return True if this extractor can handle the given URL."""
        for pattern in self._URL_PATTERNS:
            if re.search(pattern, url, re.IGNORECASE):
                return True
        return False

    # ── Core interface ─────────────────────────────────────────────────

    @abstractmethod
    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        """
        Extract a single video/audio/image post.
        kwargs may include: quality, remove_watermark, cookies_file
        """

    def extract_batch(self, url: str, limit: int = 50, **kwargs) -> BatchResult:
        """
        Scrape a channel/profile/playlist and return a list of URLs.
        Default raises NotImplementedError — override in extractors that support batch.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support batch extraction"
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _make_error_result(self, code: str, message: str) -> ExtractResult:
        return ExtractResult.error(self.platform_name, code, message)

    def _make_error_batch(self, code: str, message: str) -> BatchResult:
        return BatchResult.error(self.platform_name, code, message)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} platform={self.platform_name!r}>"
