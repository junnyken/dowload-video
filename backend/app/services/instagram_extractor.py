"""
Instagram Extractor — Phase 15 Upgrade
========================================
Full-featured Instagram extractor: single post, carousel, stories, reels batch.

Supported URL types:
  - Single post/reel:   instagram.com/p/XXX  |  instagram.com/reel/XXX
  - IGTV:               instagram.com/tv/XXX
  - Story:              instagram.com/stories/{username}/{story_id}
  - Highlight:          instagram.com/stories/highlights/{id}
  - Profile Reels:      instagram.com/{username}/reels/  (batch)
  - Profile (batch):    instagram.com/{username}/  (batch, max 100)
  - Carousel:           same URL as single post — detected by media_items count

Authentication:
  - Cookie pool: add via admin dashboard (platform "instagram")
  - Without cookie: public posts only
  - With cookie: stories, private posts (if your account follows), batch

Error codes: ig_login_required, ig_rate_limited, ig_content_removed,
             ig_private_account, ig_story_expired, ig_carousel_partial
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_PLATFORM = "Instagram"
_HARD_MAX_BATCH = 100

# Error codes
ERR_LOGIN  = "ig_login_required"
ERR_RATE   = "ig_rate_limited"
ERR_GONE   = "ig_content_removed"
ERR_PRIV   = "ig_private_account"
ERR_STORY  = "ig_story_expired"
ERR_FAILED = "processing_failed"

_ERR_MSGS = {
    ERR_LOGIN:  "Instagram yêu cầu đăng nhập để xem nội dung này.",
    ERR_RATE:   "Instagram đang giới hạn tốc độ. Thử lại sau 1-2 phút.",
    ERR_GONE:   "Bài đăng Instagram đã bị xoá hoặc không còn khả dụng.",
    ERR_PRIV:   "Tài khoản Instagram này là riêng tư.",
    ERR_STORY:  "Story Instagram đã hết hạn (tồn tại 24 giờ).",
    ERR_FAILED: "Không thể trích xuất nội dung Instagram.",
}

# URL patterns
_SINGLE_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv|reels)/([A-Za-z0-9_-]+)", re.IGNORECASE
)
_STORY_RE = re.compile(
    r"instagram\.com/stories/(?!highlights/)([^/?]+)(?:/(\d+))?", re.IGNORECASE
)
_HIGHLIGHT_RE = re.compile(
    r"instagram\.com/stories/highlights/(\d+)", re.IGNORECASE
)
_PROFILE_RE = re.compile(
    r"instagram\.com/([^/?#@]+)/?(?:reels/?)?$", re.IGNORECASE
)


# ── Cookie helpers ─────────────────────────────────────────────────────────

def _get_ig_cookies_file() -> str | None:
    """Cookie pool → env fallback."""
    try:
        from app.services.downloader import _get_cookies_file
        b64_env = os.getenv("INSTAGRAM_COOKIES_B64", "")
        return _get_cookies_file("instagram", b64_env)
    except Exception:
        return None


# ── Error classifier ───────────────────────────────────────────────────────

def _classify_error(exc_msg: str) -> str:
    msg = exc_msg.lower()
    if any(x in msg for x in ("login", "sign in", "checkpoint", "not logged")):
        return ERR_LOGIN
    if "private" in msg or "not public" in msg:
        return ERR_PRIV
    if "rate" in msg or "429" in msg or "please wait" in msg:
        return ERR_RATE
    if any(x in msg for x in ("removed", "deleted", "no longer available", "doesn't exist")):
        return ERR_GONE
    if "story" in msg and any(x in msg for x in ("expired", "unavailable")):
        return ERR_STORY
    return ERR_FAILED


# ── yt-dlp extraction ──────────────────────────────────────────────────────

def _ydl_opts(cookies_file: str | None, quality: str = "video") -> dict:
    fmt = "bestaudio/best" if quality.startswith("mp3") else "bestvideo+bestaudio/best[ext=mp4]/best"
    opts: dict[str, Any] = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
        "extract_flat": False,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
        opts["extractor_args"] = {"instagram": {"api": ["1"]}}
    return opts


def _extract_with_ydl(url: str, quality: str = "video") -> tuple[dict, str | None]:
    """
    Extract via yt-dlp. Returns (info_dict, error_code_or_None).
    error_code is set on failure.
    """
    import yt_dlp

    cookies_file = _get_ig_cookies_file()
    opts = _ydl_opts(cookies_file, quality)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        return info, None
    except yt_dlp.utils.DownloadError as e:
        code = _classify_error(str(e))
        return {}, code
    except Exception as e:
        return {}, ERR_FAILED


# ── Single post ────────────────────────────────────────────────────────────

def extract_instagram_single(url: str, quality: str = "video") -> dict:
    """
    Download a single Instagram post (video, image, or carousel).
    For carousel posts, returns media_items list + requires_zip flag.
    """
    info, err_code = _extract_with_ydl(url, quality)

    if err_code:
        return {
            "error_code": err_code,
            "error_message": _ERR_MSGS.get(err_code, "Lỗi không xác định."),
        }

    # Detect carousel: multiple entries under "entries" or type == "playlist"
    entries = info.get("entries") or []
    if entries:
        return _build_carousel_result(info, entries, quality)

    return _build_single_result(info, quality)


def _build_single_result(info: dict, quality: str) -> dict:
    formats = [
        {
            "quality": str(f.get("format_note") or f.get("height") or "best"),
            "codec": f.get("vcodec", ""),
            "bitrate": int(f.get("tbr", 0) or 0),
            "url": f.get("url", ""),
            "ext": f.get("ext", "mp4"),
        }
        for f in info.get("formats", [])
        if f.get("url")
    ]
    return {
        "platform": _PLATFORM,
        "title": info.get("title", ""),
        "author": info.get("uploader", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration_ms": int((info.get("duration") or 0) * 1000),
        "duration": info.get("duration", 0),
        "formats": formats,
        "direct_url": info.get("url", "") or (formats[0]["url"] if formats else ""),
        "media_items": [],
        "requires_zip": False,
        "source_type": "audio" if quality.startswith("mp3") else "video",
        "error_code": None,
        "error_message": None,
    }


def _build_carousel_result(info: dict, entries: list, quality: str) -> dict:
    """Build result for carousel (multiple media items in one post)."""
    media_items = []
    for e in entries:
        item_url = e.get("url") or ""
        if not item_url and e.get("formats"):
            # Pick best format
            item_url = max(
                (f for f in e["formats"] if f.get("url")),
                key=lambda f: int(f.get("tbr", 0) or 0),
                default={},
            ).get("url", "")

        if not item_url:
            continue

        vcodec = e.get("vcodec", "none")
        media_type = "image" if vcodec in ("none", "", None) else "video"
        media_items.append({
            "url": item_url,
            "media_type": media_type,
            "thumbnail": e.get("thumbnail", ""),
            "filename": f"{e.get('id', '') or 'item'}_{len(media_items)+1}.{'jpg' if media_type == 'image' else 'mp4'}",
        })

    return {
        "platform": _PLATFORM,
        "title": info.get("title", "Instagram Carousel"),
        "author": info.get("uploader", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration_ms": 0,
        "formats": [],
        "direct_url": "",
        "media_items": media_items,
        "requires_zip": len(media_items) > 1,
        "source_type": "mixed",
        "error_code": None,
        "error_message": None,
    }


# ── Story extraction ───────────────────────────────────────────────────────

def extract_instagram_story(url: str) -> dict:
    """
    Extract public Instagram story or highlight.
    Requires cookie for most stories (they gate on login).
    """
    info, err_code = _extract_with_ydl(url, "video")

    if err_code:
        if err_code == ERR_LOGIN:
            return {
                "error_code": ERR_LOGIN,
                "error_message": "Story Instagram yêu cầu đăng nhập. Thêm cookie Instagram vào admin.",
            }
        return {
            "error_code": err_code,
            "error_message": _ERR_MSGS.get(err_code, "Story không khả dụng."),
        }

    entries = info.get("entries") or []
    if entries:
        return _build_carousel_result(info, entries, "video")

    if not info.get("url") and not info.get("formats"):
        return {"error_code": ERR_STORY, "error_message": _ERR_MSGS[ERR_STORY]}

    return _build_single_result(info, "video")


# ── Profile / Reels batch ──────────────────────────────────────────────────

def scrape_instagram_profile(url: str, max_posts: int = 50) -> dict:
    """
    Scrape Instagram profile reels/videos for batch download.
    Returns bulk-channel compatible result.
    """
    import yt_dlp

    max_posts = min(max_posts, _HARD_MAX_BATCH)
    cookies_file = _get_ig_cookies_file()

    # Try /reels/ suffix first (more reliable for video-only)
    profile_match = _PROFILE_RE.search(url)
    if profile_match:
        username = profile_match.group(1)
        # Skip reserved paths
        if username.lower() in ("p", "reel", "tv", "stories", "explore", "reels"):
            username = None
    else:
        username = None

    # Build canonical URL: prefer /reels/ endpoint
    if username and not url.rstrip("/").endswith("/reels"):
        canonical = f"https://www.instagram.com/{username}/reels/"
    else:
        canonical = url

    opts: dict[str, Any] = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "playlistend": max_posts + 10,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
        opts["extractor_args"] = {"instagram": {"api": ["1"]}}

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(canonical, download=False) or {}
    except Exception as e:
        code = _classify_error(str(e))
        return {
            "channel_title": username or "",
            "entries": [],
            "total_found": 0,
            "total_queued": 0,
            "error_code": code,
            "error_message": str(e),
        }

    entries_raw = info.get("entries") or []
    entries = []
    for e in entries_raw[:max_posts]:
        if not isinstance(e, dict):
            continue
        ep_url = e.get("url") or e.get("webpage_url", "")
        if not ep_url:
            continue
        entries.append({"url": ep_url, "title": e.get("title") or e.get("id", "")})

    return {
        "channel_title": username or info.get("title", ""),
        "entries": entries,
        "total_found": len(entries_raw),
        "total_queued": len(entries),
        "error_code": None,
        "error_message": None,
    }


# ── BaseExtractor wrapper ──────────────────────────────────────────────────

from app.services.base_extractor import (
    BaseExtractor,
    BatchEntry,
    BatchResult,
    ExtractResult,
    FormatInfo,
    MediaItem,
)


class InstagramExtractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({
        "video", "audio", "image", "carousel", "story", "batch", "channel", "private",
    })
    cost_tier = "medium"
    requires_cookie = True
    supports_proxy = True

    _URL_PATTERNS = [
        r"instagram\.com/",
        r"instagr\.am/",
    ]
    _test_fixtures = [
        "https://www.instagram.com/p/CxYZABCDEFG/",
    ]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")

        # Route story URLs
        if "stories/" in url.lower():
            raw = extract_instagram_story(url)
        else:
            raw = extract_instagram_single(url, quality)

        if raw.get("error_code"):
            return ExtractResult.error(self.platform_name, raw["error_code"], raw.get("error_message", ""))

        media_items = [
            MediaItem(
                url=m["url"],
                media_type=m.get("media_type", "video"),
                thumbnail=m.get("thumbnail", ""),
                filename=m.get("filename", ""),
            )
            for m in raw.get("media_items", [])
        ]

        formats = [
            FormatInfo(
                quality=str(f.get("quality", "best")),
                codec=f.get("codec", ""),
                bitrate=f.get("bitrate", 0),
                url=f.get("url", ""),
                ext=f.get("ext", "mp4"),
            )
            for f in raw.get("formats", [])
        ]

        return ExtractResult(
            platform=self.platform_name,
            source_type=raw.get("source_type", "video"),
            title=raw.get("title", ""),
            author=raw.get("author", ""),
            thumbnail=raw.get("thumbnail", ""),
            duration_ms=raw.get("duration_ms", 0),
            formats=formats,
            media_items=media_items,
            direct_url=raw.get("direct_url", ""),
            requires_zip=raw.get("requires_zip", False),
            extraction_method="ytdlp",
        )

    def extract_batch(self, url: str, limit: int = 50, **kwargs) -> BatchResult:
        try:
            raw = scrape_instagram_profile(url, max_posts=limit)
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
    REGISTRY.register(InstagramExtractor())
except Exception as _e:
    logger.warning("Could not register InstagramExtractor: %s", _e)
