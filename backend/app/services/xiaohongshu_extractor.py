"""
Xiaohongshu (RedNote / 小红书) Extractor
==========================================
Custom httpx scraper for Xiaohongshu video and image posts.

Xiaohongshu (XHS) is a Chinese social platform. yt-dlp support is partial
and often breaks. This extractor uses a direct API approach.

Supported URLs:
  - Video post: xiaohongshu.com/explore/{noteId}  |  xhslink.com/xxx (short)
  - Image carousel: same URL — detected by response content type
  - Profile (batch): xiaohongshu.com/user/profile/{userId}

Strategy:
  1. Extract noteId from URL or resolve short link.
  2. Try yt-dlp first (handles some public XHS without cookie).
  3. Fallback: XHS mobile API v1 (requires cookie from pool).
  4. For image posts: collect all image URLs and return as carousel.

Cookie:
  - Required for most content (XHS gates heavily on login).
  - Add XHS cookie to admin: platform "xiaohongshu".
  - Required fields: a1, web_session, webId.

Geo-note:
  - Some content is CN-only. A CN proxy (XIAOHONGSHU_PROXY_CN env) helps.

Error codes: xhs_login_required, xhs_geo_restricted, xhs_extract_failed
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs, urlencode

import httpx

logger = logging.getLogger(__name__)

_PLATFORM = "Xiaohongshu"

_URL_PATTERNS = [
    r"xiaohongshu\.com/explore/",
    r"xiaohongshu\.com/discovery/item/",
    r"xiaohongshu\.com/user/profile/",
    r"xhslink\.com/",
    r"redbook\.com/",
]

_NOTE_ID_RE = re.compile(r"/explore/([a-f0-9]{24})", re.IGNORECASE)
_USER_ID_RE = re.compile(r"/user/profile/([a-f0-9]{24})", re.IGNORECASE)

_XHS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 MicroMessenger/8.0.38 miniProgram xhsShareTargetVersion/2.0"
)

_XHS_WEB_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


# ── Cookie helpers ─────────────────────────────────────────────────────────

def _get_xhs_cookies() -> dict:
    """Get XHS cookies from pool, return as dict for httpx."""
    try:
        from app.core.cookie_pool import get_cookie_from_pool
        import base64
        b64 = get_cookie_from_pool("xiaohongshu")
        if not b64:
            return {}
        decoded = base64.b64decode(b64).decode("utf-8", errors="ignore")
        # Parse Netscape format → dict
        cookies = {}
        for line in decoded.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
        return cookies
    except Exception:
        return {}


# ── yt-dlp attempt ────────────────────────────────────────────────────────

def _try_ytdlp(url: str) -> Optional[dict]:
    """Try yt-dlp first — works for some public XHS posts."""
    try:
        import yt_dlp
        opts = {
            "format": "bestvideo+bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 15,
            "retries": 1,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
        if info.get("url") or info.get("formats"):
            return info
    except Exception:
        pass
    return None


# ── XHS Web API ───────────────────────────────────────────────────────────

def _build_xhs_headers(cookies: dict) -> dict:
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return {
        "User-Agent": _XHS_WEB_UA,
        "Referer": "https://www.xiaohongshu.com/",
        "Origin": "https://www.xiaohongshu.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cookie": cookie_str,
        "X-Requested-With": "XMLHttpRequest",
    }


def _extract_note_id(url: str) -> Optional[str]:
    m = _NOTE_ID_RE.search(url)
    if m:
        return m.group(1)
    # Try query param
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "noteId" in qs:
        return qs["noteId"][0]
    return None


def _fetch_note_api(note_id: str, cookies: dict) -> Optional[dict]:
    """
    Fetch XHS note metadata via the web API.
    Returns parsed JSON data or None on failure.
    """
    endpoint = f"https://www.xiaohongshu.com/api/sns/web/v1/feed"
    payload = {
        "source_note_id": note_id,
        "image_formats": ["jpg", "webp", "avif"],
        "extra": {"need_body_topic": "1"},
    }
    headers = _build_xhs_headers(cookies)
    cn_proxy = os.getenv("XIAOHONGSHU_PROXY_CN", "")

    try:
        client_kwargs: dict[str, Any] = {
            "headers": headers,
            "follow_redirects": True,
            "timeout": 15.0,
        }
        if cn_proxy:
            client_kwargs["proxies"] = {"all://": cn_proxy}

        with httpx.Client(**client_kwargs) as client:
            resp = client.post(endpoint, json=payload)

        if resp.status_code == 461:
            logger.warning("[XHS] 461 = login required or geo-blocked")
            return None
        if resp.status_code == 429:
            logger.warning("[XHS] 429 rate limited")
            return None
        if resp.status_code != 200:
            logger.warning("[XHS] API returned %s", resp.status_code)
            return None

        data = resp.json()
        if data.get("success") is False:
            code = data.get("code", "")
            logger.warning("[XHS] API error code %s: %s", code, data.get("msg", ""))
            return None

        return data.get("data", {})

    except Exception as e:
        logger.warning("[XHS] fetch_note_api error: %s", e)
        return None


def _parse_note_data(data: dict, note_id: str) -> dict:
    """Parse XHS API note response into standard result dict."""
    items = data.get("items", []) or []
    if not items:
        return {"error_code": "xhs_extract_failed", "error_message": "Không tìm thấy nội dung XHS."}

    note = items[0].get("note_card", {}) or {}
    note_type = note.get("type", "normal")  # "video" or "normal" (image)
    title = note.get("title", "") or note.get("desc", "")
    author = (note.get("user", {}) or {}).get("nickname", "")
    thumbnail = ""

    # Image carousel
    image_list = note.get("image_list", []) or []
    if note_type != "video" and image_list:
        media_items = []
        for img in image_list:
            # XHS image URL: try info_list first, fall back to url_default
            url_candidates = []
            for info in img.get("info_list", []):
                url_candidates.append(info.get("url", ""))
            url_candidates.append(img.get("url_default", ""))
            img_url = next((u for u in url_candidates if u), "")
            if img_url:
                media_items.append({
                    "url": img_url,
                    "media_type": "image",
                    "thumbnail": img_url,
                    "filename": f"xhs_{note_id}_{len(media_items)+1}.jpg",
                })
        if image_list and image_list[0]:
            thumbnail = image_list[0].get("url_default", "")
        return {
            "platform": _PLATFORM,
            "title": title,
            "author": author,
            "thumbnail": thumbnail,
            "duration_ms": 0,
            "formats": [],
            "direct_url": "",
            "media_items": media_items,
            "requires_zip": len(media_items) > 1,
            "source_type": "mixed" if len(media_items) > 1 else "image",
            "error_code": None,
            "error_message": None,
        }

    # Video post
    video = note.get("video", {}) or {}
    media_info = video.get("media", {}) or {}
    stream_list = media_info.get("stream", {}) or {}

    best_url = ""
    best_bitrate = 0
    formats = []

    for quality_key in ("h264", "av1", "h265"):
        streams = stream_list.get(quality_key, []) or []
        for s in streams:
            m_url = s.get("master_url") or s.get("backup_urls", [None])[0] or ""
            if not m_url:
                continue
            bitrate = int(s.get("average_bitrate", 0) or 0)
            height = int(s.get("video_codec", {}).get("height", 0) or 0) if isinstance(s.get("video_codec"), dict) else 0
            formats.append({
                "quality": f"{height}p" if height else quality_key,
                "codec": quality_key,
                "bitrate": bitrate,
                "url": m_url,
                "ext": "mp4",
            })
            if bitrate > best_bitrate:
                best_bitrate = bitrate
                best_url = m_url

    cover_info = video.get("image", {}) or {}
    if cover_info:
        thumbnail = cover_info.get("url_default") or cover_info.get("first_frame_fileid", "")

    duration_ms = int((video.get("duration", 0) or 0) * 1000)

    return {
        "platform": _PLATFORM,
        "title": title,
        "author": author,
        "thumbnail": thumbnail,
        "duration_ms": duration_ms,
        "formats": formats,
        "direct_url": best_url,
        "media_items": [],
        "requires_zip": False,
        "source_type": "video",
        "error_code": None,
        "error_message": None,
    }


# ── Main extraction ────────────────────────────────────────────────────────

def extract_xiaohongshu(url: str, quality: str = "video") -> dict:
    """
    Main XHS extraction entry point.
    Tries yt-dlp first, then XHS API with cookie.
    """
    # 1. Try yt-dlp (fast, no cookie needed for some public posts)
    ydl_info = _try_ytdlp(url)
    if ydl_info and (ydl_info.get("url") or ydl_info.get("formats")):
        formats = [
            {
                "quality": str(f.get("format_note") or f.get("height") or "best"),
                "codec": f.get("vcodec", ""),
                "bitrate": int(f.get("tbr", 0) or 0),
                "url": f.get("url", ""),
                "ext": f.get("ext", "mp4"),
            }
            for f in ydl_info.get("formats", [])
            if f.get("url")
        ]
        return {
            "platform": _PLATFORM,
            "title": ydl_info.get("title", ""),
            "author": ydl_info.get("uploader", ""),
            "thumbnail": ydl_info.get("thumbnail", ""),
            "duration_ms": int((ydl_info.get("duration") or 0) * 1000),
            "formats": formats,
            "direct_url": ydl_info.get("url", "") or (formats[-1]["url"] if formats else ""),
            "media_items": [],
            "requires_zip": False,
            "source_type": "video",
            "error_code": None,
            "error_message": None,
        }

    # 2. Extract note_id and use XHS API
    note_id = _extract_note_id(url)
    if not note_id:
        return {
            "error_code": "xhs_extract_failed",
            "error_message": "Không thể tìm thấy Note ID từ URL Xiaohongshu. Kiểm tra định dạng URL.",
        }

    cookies = _get_xhs_cookies()
    if not cookies:
        return {
            "error_code": "xhs_login_required",
            "error_message": "Xiaohongshu yêu cầu cookie. Thêm cookie XHS vào Admin → Cookie Pool (platform: xiaohongshu).",
        }

    data = _fetch_note_api(note_id, cookies)
    if data is None:
        return {
            "error_code": "xhs_extract_failed",
            "error_message": "Xiaohongshu API không phản hồi. Có thể cần proxy CN hoặc cookie đã hết hạn.",
        }

    return _parse_note_data(data, note_id)


def scrape_xiaohongshu_profile(user_url: str, max_posts: int = 30) -> dict:
    """
    Scrape XHS user profile video posts.
    Returns bulk-channel compatible result.
    Note: XHS profile API is heavily rate-limited; max_posts capped at 30.
    """
    max_posts = min(max_posts, 30)
    user_m = _USER_ID_RE.search(user_url)
    if not user_m:
        return {
            "channel_title": "",
            "entries": [],
            "total_found": 0,
            "total_queued": 0,
            "error_code": "xhs_extract_failed",
            "error_message": "Không thể tìm thấy User ID từ URL Xiaohongshu.",
        }

    user_id = user_m.group(1)
    cookies = _get_xhs_cookies()
    if not cookies:
        return {
            "channel_title": "",
            "entries": [],
            "total_found": 0,
            "total_queued": 0,
            "error_code": "xhs_login_required",
            "error_message": "Profile XHS yêu cầu cookie đăng nhập.",
        }

    headers = _build_xhs_headers(cookies)
    endpoint = "https://www.xiaohongshu.com/api/sns/web/v1/user_posted"
    cn_proxy = os.getenv("XIAOHONGSHU_PROXY_CN", "")

    entries = []
    cursor = ""

    try:
        client_kwargs: dict[str, Any] = {
            "headers": headers,
            "follow_redirects": True,
            "timeout": 15.0,
        }
        if cn_proxy:
            client_kwargs["proxies"] = {"all://": cn_proxy}

        with httpx.Client(**client_kwargs) as client:
            for _ in range(3):  # max 3 pages
                params: dict[str, Any] = {"user_id": user_id, "cursor": cursor, "num": 30, "image_formats": "jpg,webp"}
                resp = client.get(endpoint, params=params)
                if resp.status_code != 200:
                    break
                data = resp.json().get("data", {}) or {}
                notes = data.get("notes", []) or []
                for note in notes:
                    if len(entries) >= max_posts:
                        break
                    note_id = note.get("note_id", "")
                    if not note_id:
                        continue
                    note_type = note.get("type", "normal")
                    if note_type not in ("video",):
                        continue  # skip image-only posts in batch
                    entries.append({
                        "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                        "title": note.get("display_title", ""),
                    })
                if not data.get("has_more", False) or len(entries) >= max_posts:
                    break
                cursor = data.get("cursor", "")

    except Exception as e:
        logger.warning("[XHS] profile scrape error: %s", e)

    return {
        "channel_title": f"XHS/{user_id}",
        "entries": entries,
        "total_found": len(entries),
        "total_queued": len(entries),
        "error_code": None if entries else "xhs_extract_failed",
        "error_message": None if entries else "Không tìm thấy video nào trên profile XHS.",
    }


# ── BaseExtractor wrapper ──────────────────────────────────────────────────

from app.services.base_extractor import (
    BaseExtractor, BatchEntry, BatchResult, ExtractResult, FormatInfo, MediaItem,
)


class XiaohongshuExtractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({"video", "image", "carousel", "batch"})
    cost_tier = "high"
    requires_cookie = True
    supports_proxy = True

    _URL_PATTERNS = _URL_PATTERNS
    _test_fixtures = ["https://www.xiaohongshu.com/explore/64ab1234567890abcdef1234"]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        quality = kwargs.get("quality", "video")
        try:
            raw = extract_xiaohongshu(url, quality)
        except Exception as e:
            return self._make_error_result("xhs_extract_failed", str(e))

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

    def extract_batch(self, url: str, limit: int = 30, **kwargs) -> BatchResult:
        try:
            raw = scrape_xiaohongshu_profile(url, max_posts=limit)
        except Exception as e:
            return self._make_error_batch("xhs_extract_failed", str(e))
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
    REGISTRY.register(XiaohongshuExtractor())
except Exception as _e:
    logger.warning("Could not register XiaohongshuExtractor: %s", _e)
