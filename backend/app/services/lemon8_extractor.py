"""
Lemon8 Extractor
=================
Custom scraper for Lemon8 (ByteDance lifestyle app).

Supported URLs:
  - Post: lemon8-app.com/post/{id}  |  lemon8-app.com/{user}/{id}
  - Profile: lemon8-app.com/@{username}

Strategy:
  1. Try yt-dlp (partial support via TikTok extractor family).
  2. Fallback: httpx + OG meta tags + JSON-LD parsing.
  3. For image carousel: collect all og:image entries.

Note:
  - Lemon8 uses ByteDance infrastructure, similar to TikTok.
  - Proxy recommended: LEMON8_PROXY env var.
  - Cookie from pool (platform "lemon8") for private posts.

Error codes: lemon8_extract_failed, lemon8_private_post
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_PLATFORM = "Lemon8"

_URL_PATTERNS = [
    r"lemon8-app\.com/",
    r"lemon8\.app/",
]

_POST_ID_RE = re.compile(r"/(?:post/)?(\d{10,25})", re.IGNORECASE)

_L8_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)


# ── yt-dlp attempt ────────────────────────────────────────────────────────

def _try_ytdlp_lemon8(url: str) -> Optional[dict]:
    """Try yt-dlp — may work via embedded TikTok-family extractor."""
    try:
        import yt_dlp
        proxy = os.getenv("LEMON8_PROXY", "")
        opts: dict[str, Any] = {
            "format": "bestvideo+bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 15,
            "retries": 1,
        }
        if proxy:
            opts["proxy"] = proxy
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        if info.get("url") or info.get("formats"):
            return info
    except Exception:
        pass
    return None


# ── OG meta scraping ──────────────────────────────────────────────────────

def _scrape_og_meta(url: str) -> dict:
    """
    Scrape Lemon8 post via OG meta tags.
    Returns a result dict (may include media_items for carousel).
    """
    proxy = os.getenv("LEMON8_PROXY", "")
    headers = {
        "User-Agent": _L8_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    client_kwargs: dict[str, Any] = {
        "headers": headers,
        "follow_redirects": True,
        "timeout": 15.0,
    }
    if proxy:
        client_kwargs["proxies"] = {"all://": proxy}

    try:
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(url)
        html = resp.text
    except Exception as e:
        return {"error_code": "lemon8_extract_failed", "error_message": f"Không thể tải trang Lemon8: {e}"}

    if resp.status_code == 403 or "private" in html[:2000].lower():
        return {"error_code": "lemon8_private_post", "error_message": "Bài đăng Lemon8 này là riêng tư."}

    # Parse OG meta tags
    og: dict[str, str] = {}
    for m in re.finditer(r'<meta[^>]+property=["\']og:(\w+)["\'][^>]+content=["\']([^"\']*)["\']', html, re.IGNORECASE):
        og[m.group(1)] = m.group(2)
    for m in re.finditer(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:(\w+)["\']', html, re.IGNORECASE):
        og[m.group(2)] = m.group(1)

    # Look for video in OG tags
    video_url = og.get("video") or og.get("video:url") or og.get("video:secure_url") or ""

    # Look for JSON-LD data
    json_ld_data: dict = {}
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
        try:
            json_ld_data = json.loads(m.group(1))
            break
        except Exception:
            pass

    if json_ld_data.get("video"):
        v = json_ld_data["video"]
        if isinstance(v, dict):
            video_url = video_url or v.get("contentUrl") or v.get("embedUrl") or ""

    title = og.get("title", "") or json_ld_data.get("name", "Lemon8 Post")
    author = og.get("site_name", "") or ""
    thumbnail = og.get("image", "") or ""

    if video_url:
        return {
            "platform": _PLATFORM,
            "title": title,
            "author": author,
            "thumbnail": thumbnail,
            "duration_ms": 0,
            "formats": [{"quality": "best", "codec": "", "bitrate": 0, "url": video_url, "ext": "mp4"}],
            "direct_url": video_url,
            "media_items": [],
            "requires_zip": False,
            "source_type": "video",
            "error_code": None,
            "error_message": None,
        }

    # Try image carousel from multiple og:image tags
    images = re.findall(r'<meta[^>]+(?:property=["\']og:image["\']|name=["\']og:image["\'])[^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not images and thumbnail:
        images = [thumbnail]

    if images:
        media_items = [
            {"url": img, "media_type": "image", "thumbnail": img, "filename": f"lemon8_{i+1}.jpg"}
            for i, img in enumerate(images)
        ]
        return {
            "platform": _PLATFORM,
            "title": title,
            "author": author,
            "thumbnail": images[0],
            "duration_ms": 0,
            "formats": [],
            "direct_url": "",
            "media_items": media_items,
            "requires_zip": len(media_items) > 1,
            "source_type": "mixed" if len(media_items) > 1 else "image",
            "error_code": None,
            "error_message": None,
        }

    return {
        "error_code": "lemon8_extract_failed",
        "error_message": "Không tìm thấy media trong bài đăng Lemon8. Trang có thể yêu cầu đăng nhập.",
    }


# ── Main extraction ────────────────────────────────────────────────────────

def extract_lemon8(url: str, quality: str = "video") -> dict:
    """Main Lemon8 extraction: try yt-dlp → fallback OG scrape."""
    # Try yt-dlp first
    info = _try_ytdlp_lemon8(url)
    if info and (info.get("url") or info.get("formats")):
        formats = [
            {"quality": str(f.get("format_note") or f.get("height") or "best"),
             "codec": f.get("vcodec", ""), "bitrate": int(f.get("tbr", 0) or 0),
             "url": f.get("url", ""), "ext": f.get("ext", "mp4")}
            for f in info.get("formats", []) if f.get("url")
        ]
        return {
            "platform": _PLATFORM,
            "title": info.get("title", ""),
            "author": info.get("uploader", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration_ms": int((info.get("duration") or 0) * 1000),
            "formats": formats,
            "direct_url": info.get("url", "") or (formats[-1]["url"] if formats else ""),
            "media_items": [],
            "requires_zip": False,
            "source_type": "video",
            "error_code": None,
            "error_message": None,
        }

    # Fallback: OG meta scraping
    return _scrape_og_meta(url)


# ── BaseExtractor wrapper ──────────────────────────────────────────────────

from app.services.base_extractor import (
    BaseExtractor, BatchResult, ExtractResult, FormatInfo, MediaItem,
)


class Lemon8Extractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({"video", "image", "carousel"})
    cost_tier = "medium"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = _URL_PATTERNS
    _test_fixtures = ["https://www.lemon8-app.com/post/7123456789012345678"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        try:
            raw = extract_lemon8(url, quality)
        except Exception as e:
            return self._make_error_result("lemon8_extract_failed", str(e))

        if raw.get("error_code"):
            return ExtractResult.error(self.platform_name, raw["error_code"], raw.get("error_message", ""))

        media_items = [
            MediaItem(m["url"], m.get("media_type", "image"), m.get("thumbnail", ""), m.get("filename", ""))
            for m in raw.get("media_items", [])
        ]
        formats = [
            FormatInfo(str(f.get("quality", "")), f.get("codec", ""), f.get("bitrate", 0), f.get("url", ""), ext=f.get("ext", "mp4"))
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
            extraction_method="custom",
        )


try:
    from app.services.extractor_registry import REGISTRY
    REGISTRY.register(Lemon8Extractor())
except Exception as _e:
    logger.warning("Could not register Lemon8Extractor: %s", _e)
