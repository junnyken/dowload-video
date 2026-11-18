"""
Dropbox Public Share Link Extractor
====================================
Resolves public Dropbox share links to direct download URLs:
  - https://www.dropbox.com/s/<id>/<filename>?dl=0
  - https://www.dropbox.com/scl/fi/<id>/<filename>?rlkey=...&dl=0
  - https://www.dropbox.com/scl/fo/<id>/<folder>?rlkey=...&dl=0
  - https://dl.dropboxusercontent.com/s/<id>/<filename>

Normalizes share URLs to direct download by setting `dl=1` while preserving
required parameters such as `rlkey`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

from app.core.error_codes import get_error_meta
from app.services.base_extractor import (
    BaseExtractor,
    ExtractResult,
    FormatInfo,
)
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

_DROPBOX_HOSTS = (
    "dropbox.com",
    "www.dropbox.com",
    "dl.dropboxusercontent.com",
    "dl.dropbox.com",
)

_SHARE_PATH_RE = re.compile(
    r"^/(?:s|sh|scl/fi|scl/fo)/([A-Za-z0-9_-]+)(?:/([^/?#]+))?",
    re.I,
)


def _run_sync(coro):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def is_dropbox_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    if not any(host == d or host.endswith("." + d) for d in _DROPBOX_HOSTS):
        return False
    return bool(_SHARE_PATH_RE.search(parts.path))


def normalize_dropbox_url(url: str) -> str:
    """
    Normalize a Dropbox share URL into a direct download URL with `dl=1`.
    Preserves `rlkey` and other query parameters.
    """
    if not is_dropbox_url(url):
        raise ValueError("invalid_url")

    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "www.dropbox.com").lower()
    if host == "dropbox.com":
        host = "www.dropbox.com"

    q_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in ("dl", "raw")]
    q_pairs.append(("dl", "1"))
    new_query = urlencode(q_pairs)
    return urlunsplit(("https", host, parts.path, new_query, ""))


def _extract_dropbox_filename(url: str) -> tuple[str, str, bool]:
    """Return (title, ext, is_folder) from a Dropbox share URL."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    is_folder = bool(re.search(r"^/(?:scl/fo|sh)/", parts.path, re.I))
    m = _SHARE_PATH_RE.search(parts.path)
    if not m:
        return "dropbox_file", "mp4", is_folder
    item_id = m.group(1)
    raw_name = unquote(m.group(2) or "").strip()
    if not raw_name:
        return f"dropbox_{item_id}", "zip" if is_folder else "mp4", is_folder
    if "." in raw_name and not is_folder:
        title, ext = raw_name.rsplit(".", 1)
        return title, ext.lower(), is_folder
    return raw_name, "zip" if is_folder else "mp4", is_folder


async def resolve_dropbox_share(
    url: str,
    quality: str = "video",
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Resolve a public Dropbox share link into a dict compatible with
    extract_video_info() in routes.py.
    """
    dl_url = normalize_dropbox_url(url)
    title, ext, is_folder = _extract_dropbox_filename(url)
    size_mb = 0.0

    if client is not None:
        resp = await client.get(dl_url)
        if resp.status_code == 404:
            raise ValueError("share_expired")
        if resp.status_code in (401, 403):
            raise ValueError("owner_disabled_download")
        if resp.status_code in (429, 509):
            raise ValueError("share_quota_exceeded")
        try:
            size_mb = round(int(resp.headers.get("content-length") or 0) / (1024 * 1024), 2)
        except (ValueError, TypeError):
            size_mb = 0.0

    return {
        "title": title,
        "thumbnail_url": "",
        "direct_mp4_url": dl_url,
        "original_url": url,
        "quality": quality,
        "duration": 0,
        "file_size_mb": size_mb,
        "available_formats": [
            {
                "quality": "original",
                "height": 0,
                "width": 0,
                "url": dl_url,
                "ext": ext,
            }
        ],
        "is_folder": is_folder,
        "max_merge_height": 0,
        "downloaded_height": 0,
        "downloaded_vcodec": "",
        "downloaded_universal": None,
        "downloaded_media": {"measured": False},
        "is_audio_only": ext in ("mp3", "m4a", "wav", "flac", "ogg", "aac"),
        "uploader": "Dropbox",
        "has_subtitles": False,
        "has_auto_captions": False,
        "available_subtitle_languages": [],
        "subtitle_source": "none",
        "chapters": [],
        "tags": [],
        "images": [],
        "audio_url": "",
    }


class DropboxExtractor(BaseExtractor):
    platform_name = "dropbox"
    supported_features = frozenset({"video", "audio", "batch"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [
        r"(?:www\.)?dropbox\.com/(?:s|sh|scl/fi|scl/fo)/[A-Za-z0-9_-]+",
        r"dl\.dropboxusercontent\.com/(?:s|sh|scl/fi|scl/fo)/[A-Za-z0-9_-]+",
    ]

    def detect_url(self, url: str) -> bool:
        return is_dropbox_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        try:
            info = _run_sync(
                resolve_dropbox_share(
                    url,
                    quality=kwargs.get("quality", "video"),
                    client=kwargs.get("client"),
                )
            )
        except ValueError as e:
            code = str(e)
            meta = get_error_meta(code)
            return self._make_error_result(code, meta.get("user_message", code))
        except Exception as e:
            return self._make_error_result("processing_failed", str(e))

        dl_url = info.get("direct_mp4_url", "")
        return ExtractResult(
            platform="dropbox",
            source_type="audio" if info.get("is_audio_only") else "video",
            title=info.get("title", ""),
            author="Dropbox",
            thumbnail=info.get("thumbnail_url", ""),
            direct_url=dl_url,
            formats=[FormatInfo(quality="original", url=dl_url, size_estimate_mb=info.get("file_size_mb", 0.0))],
            raw_metadata=info,
            extraction_method="dropbox_dl1",
        )


REGISTRY.register(DropboxExtractor())
