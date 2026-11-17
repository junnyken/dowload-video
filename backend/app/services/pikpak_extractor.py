"""
PikPak Share Link Resolver
===========================
Resolves PikPak share links (https://mypikpak.com/s/<share_id>) to direct
download URLs using PikPak's REST API.

Video files in public shares can be accessed WITHOUT a PikPak account (guest
access). Non-video files require authentication (save-to-drive flow) which is
NOT implemented here — only video downloads are supported.

API endpoints (reverse-engineered, no official docs):
  - GET /drive/v1/share           → share metadata + pass_code_token
  - GET /drive/v1/share/list      → file listing (video URLs in medias[])

Owner: 2026-11-17 — initial PikPak support for VidGrab server /fetch-link.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from app.services.base_extractor import BaseExtractor, ExtractResult, FormatInfo
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

_API_BASE = "https://api-drive.mypikpak.com"
_CLIENT_ID = "YNxT9w7GMdWvEOKa"
_CLIENT_VERSION = "1.0.0"
_TIMEOUT = 20  # seconds per API call

_SHARE_URL_RE = re.compile(
    r"mypikpak\.(?:com|net)/s/([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)

# File extensions considered "video" (guest-downloadable from share).
_VIDEO_EXTS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".mpg", ".mpeg", ".3gp", ".vob", ".rmvb",
})


# ── Helpers ────────────────────────────────────────────────────────────────

def _common_headers() -> dict:
    return {
        "X-Client-Id": _CLIENT_ID,
        "X-Client-Version": _CLIENT_VERSION,
        "X-Device-Name": "VidGrab Server",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }


def parse_share_url(url: str) -> Optional[tuple[str, str]]:
    """Extract (share_id, parent_id) from a PikPak share URL.
    Returns None if the URL is not a PikPak share link."""
    m = _SHARE_URL_RE.search(url or "")
    if not m:
        return None
    return m.group(1), m.group(2) or ""


def is_pikpak_url(url: str) -> bool:
    u = (url or "").lower()
    return "mypikpak.com" in u or "mypikpak.net" in u


def _is_video_file(name: str) -> bool:
    """Check if a filename is a video by extension."""
    name_lower = name.lower()
    return any(name_lower.endswith(ext) for ext in _VIDEO_EXTS)


def _best_media_url(file_info: dict) -> Optional[str]:
    """Extract the best download URL from a PikPak file object.

    Priority: web_content_link > medias[].link (highest resolution first).
    """
    # web_content_link is the original quality download
    wcl = (file_info.get("web_content_link") or "").strip()
    if wcl:
        return wcl

    # medias[] contains transcoded versions; pick the best quality
    medias = file_info.get("medias") or []
    if medias:
        # Sort by resolution height descending (if available)
        def _res_key(m: dict) -> int:
            vr = m.get("video", {}) if isinstance(m.get("video"), dict) else {}
            return int(vr.get("height") or 0)

        medias_sorted = sorted(medias, key=_res_key, reverse=True)
        for m in medias_sorted:
            link = (m.get("link") or {}).get("url") if isinstance(m.get("link"), dict) else ""
            if not link:
                link = m.get("url") or ""
            if link:
                return link

    return None


def _file_thumbnail(file_info: dict) -> str:
    """Extract thumbnail URL from a PikPak file object."""
    return (file_info.get("thumbnail_link") or file_info.get("icon_link") or "").strip()


def _file_size_mb(file_info: dict) -> float:
    """Extract file size in MB."""
    try:
        return int(file_info.get("size") or 0) / (1024 * 1024)
    except (ValueError, TypeError):
        return 0.0


# ── Core API Calls ─────────────────────────────────────────────────────────

async def _get_share_info(client: httpx.AsyncClient, share_id: str, pass_code: str = "") -> dict:
    """GET /drive/v1/share — share metadata."""
    params: dict[str, str] = {"share_id": share_id, "thumbnail_size": "SIZE_LARGE"}
    if pass_code:
        params["pass_code"] = pass_code
    resp = await client.get(f"{_API_BASE}/drive/v1/share", params=params)
    resp.raise_for_status()
    return resp.json()


async def _list_share_files(
    client: httpx.AsyncClient,
    share_id: str,
    parent_id: str = "",
    pass_code_token: str = "",
    limit: int = 100,
) -> List[dict]:
    """GET /drive/v1/share/list — list files in a share (paginated)."""
    all_files: list[dict] = []
    page_token = ""

    for _ in range(10):  # max 10 pages = 1000 files
        params: dict[str, Any] = {
            "share_id": share_id,
            "parent_id": parent_id,
            "limit": str(limit),
        }
        if pass_code_token:
            params["pass_code_token"] = pass_code_token
        if page_token:
            params["page_token"] = page_token

        resp = await client.get(f"{_API_BASE}/drive/v1/share/list", params=params)
        resp.raise_for_status()
        data = resp.json()

        files = data.get("files") or []
        all_files.extend(files)

        page_token = data.get("next_page_token") or ""
        if not page_token or not files:
            break

    return all_files


# ── Public Entry Point ─────────────────────────────────────────────────────

async def resolve_pikpak_share(url: str, quality: str = "video") -> Dict[str, Any]:
    """
    Resolve a PikPak share link to a dict compatible with extract_video_info().

    Returns a dict with keys: title, thumbnail_url, direct_mp4_url,
    available_formats, duration, file_size_mb, etc.

    Raises ValueError on failure (the caller in routes.py catches it and
    returns the appropriate HTTP error).
    """
    parsed = parse_share_url(url)
    if not parsed:
        raise ValueError("Liên kết PikPak không hợp lệ. Hãy dán liên kết chia sẻ PikPak (mypikpak.com/s/...).")

    share_id, parent_id = parsed

    async with httpx.AsyncClient(
        headers=_common_headers(),
        timeout=httpx.Timeout(_TIMEOUT),
        follow_redirects=True,
    ) as client:
        # 1. Get share info
        try:
            share_info = await _get_share_info(client, share_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError("Liên kết chia sẻ PikPak không tồn tại hoặc đã hết hạn.")
            if e.response.status_code == 401:
                raise ValueError("Liên kết PikPak cần mật khẩu (chưa hỗ trợ).")
            raise ValueError(f"Không truy cập được liên kết PikPak (HTTP {e.response.status_code}).")
        except httpx.TimeoutException:
            raise ValueError("PikPak API không phản hồi. Hãy thử lại sau.")
        except Exception as e:
            raise ValueError(f"Lỗi kết nối PikPak: {e}")

        pass_code_token = share_info.get("pass_code_token") or ""
        share_title = share_info.get("title") or share_info.get("name") or "PikPak Share"

        # 2. List files in the share
        try:
            files = await _list_share_files(
                client, share_id, parent_id, pass_code_token
            )
        except httpx.HTTPStatusError as e:
            raise ValueError(f"Không lấy được danh sách file PikPak (HTTP {e.response.status_code}).")
        except Exception as e:
            raise ValueError(f"Lỗi lấy danh sách file PikPak: {e}")

        if not files:
            raise ValueError("Liên kết PikPak trống — không có file nào.")

        # 3. Find video files
        video_files = [f for f in files if _is_video_file(f.get("name", ""))]

        if not video_files:
            # Check if there are only non-video files
            non_video_names = [f.get("name", "?") for f in files[:5]]
            raise ValueError(
                f"PikPak share này chỉ chứa file không phải video ({', '.join(non_video_names)}). "
                "VidGrab chỉ hỗ trợ tải video từ PikPak."
            )

        # 4. Pick the best/first video file
        # If multiple videos, pick the largest one (usually the main content)
        video_files.sort(key=lambda f: int(f.get("size") or 0), reverse=True)
        target = video_files[0]

        # 5. Get download URL
        download_url = _best_media_url(target)
        if not download_url:
            raise ValueError(
                "PikPak không cung cấp liên kết tải trực tiếp cho video này. "
                "Có thể cần đăng nhập tài khoản PikPak (chưa hỗ trợ)."
            )

        file_name = target.get("name") or "video.mp4"
        file_title = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
        thumbnail = _file_thumbnail(target)
        size_mb = _file_size_mb(target)

        # 6. Build format list from medias[]
        formats: list[dict] = []
        medias = target.get("medias") or []
        for m in medias:
            vr = m.get("video", {}) if isinstance(m.get("video"), dict) else {}
            height = int(vr.get("height") or 0)
            width = int(vr.get("width") or 0)
            link = (m.get("link") or {}).get("url") if isinstance(m.get("link"), dict) else ""
            if not link:
                link = m.get("url") or ""
            if height and link:
                formats.append({
                    "quality": f"{height}p",
                    "height": height,
                    "width": width,
                    "url": link,
                    "ext": "mp4",
                })

        # Sort formats by height descending
        formats.sort(key=lambda f: f.get("height", 0), reverse=True)

        logger.info(
            "[PikPak] Resolved share=%s file=%s size=%.1fMB formats=%d",
            share_id, file_name, size_mb, len(formats),
        )

        # 7. Return in extract_video_info() compatible format
        return {
            "title": file_title,
            "thumbnail_url": thumbnail,
            "direct_mp4_url": download_url,
            "original_url": url,
            "quality": quality,
            "duration": 0,
            "file_size_mb": round(size_mb, 2),
            "available_formats": formats,
            "max_merge_height": 0,
            "downloaded_height": 0,
            "downloaded_vcodec": "",
            "downloaded_universal": None,
            "downloaded_media": {"measured": False},
            "is_audio_only": False,
            "uploader": share_title if share_title != file_title else None,
            "has_subtitles": False,
            "has_auto_captions": False,
            "available_subtitle_languages": [],
            "subtitle_source": "none",
            "chapters": [],
            "tags": [],
            "images": [],
            "audio_url": "",
            "http_headers": {
                "Referer": "https://mypikpak.com/",
            },
        }


# ── Extractor class (for the registry) ────────────────────────────────────

class PikPakExtractor(BaseExtractor):
    platform_name = "pikpak"
    supported_features = frozenset({"video"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [
        r"mypikpak\.(?:com|net)/s/[A-Za-z0-9_-]+",
    ]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        """Sync wrapper — the async resolve_pikpak_share is called from routes.py."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in async context — can't run_until_complete
                raise RuntimeError("Use resolve_pikpak_share directly in async context")
            info = loop.run_until_complete(resolve_pikpak_share(url, kwargs.get("quality", "video")))
        except ValueError as e:
            return self._make_error_result("unsupported_url", str(e))
        except Exception as e:
            return self._make_error_result("processing_failed", str(e))

        return ExtractResult(
            platform="pikpak",
            source_type="video",
            title=info.get("title", ""),
            author=info.get("uploader", ""),
            thumbnail=info.get("thumbnail_url", ""),
            direct_url=info.get("direct_mp4_url", ""),
            extraction_method="pikpak_api",
        )


# Register with the global registry
REGISTRY.register(PikPakExtractor())
