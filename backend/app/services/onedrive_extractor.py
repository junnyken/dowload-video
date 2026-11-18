"""
OneDrive / SharePoint Public Share Link Extractor
==================================================
Resolves public OneDrive and SharePoint share links:
  - https://1drv.ms/v/s!...
  - https://onedrive.live.com/?resid=...
  - https://<tenant>.sharepoint.com/:v:/g/...
  - https://<tenant>-my.sharepoint.com/:v:/g/personal/...

Features:
  - Encodes share URL into Microsoft Graph / OneDrive base64url sharing token (u!<base64url>)
  - Queries https://api.onedrive.com/v1.0/shares/{token}/driveItem for @content.downloadUrl / @microsoft.graph.downloadUrl
  - Supports download=1 query parameter fallback for SharePoint / OneDrive links
  - Detects view-only / disabled download -> raises ValueError("owner_disabled_download")
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.core.error_codes import get_error_meta
from app.services.base_extractor import (
    BaseExtractor,
    BatchEntry,
    BatchResult,
    ExtractResult,
    FormatInfo,
)
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

_TIMEOUT = 20.0
_API_BASE = "https://api.onedrive.com/v1.0"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_DISABLED_KEYWORDS = (
    "download is disabled",
    "disabled download",
    "view-only",
    "view only",
    "blockdownload",
    "candownload\":false",
    "candownload\": false",
    "preventdownload",
    "access denied",
    "you don't have permission",
    "sign in to your account",
    "login.microsoftonline.com",
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


def is_onedrive_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return (
        host == "1drv.ms"
        or host == "onedrive.live.com"
        or host.endswith(".onedrive.live.com")
        or host.endswith(".sharepoint.com")
    )


def encode_onedrive_share_token(url: str) -> str:
    """
    Encode a sharing URL into a OneDrive / Microsoft Graph share token:
    'u!' + base64url(url) without '=' padding.
    """
    clean = (url or "").strip()
    b64 = base64.urlsafe_b64encode(clean.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{b64}"


def append_download_param(url: str) -> str:
    """Append or overwrite download=1 query parameter on a OneDrive/SharePoint URL."""
    parts = urlsplit((url or "").strip())
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["download"] = "1"
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path, urlencode(q), parts.fragment))


def _check_onedrive_error_payload(data: dict, status_code: int) -> None:
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        code = (err.get("code") or "").lower()
        msg = (err.get("message") or "").lower()
        if code in ("accessdenied", "unauthenticated", "forbidden", "notallowed") or any(
            k in msg for k in ("access denied", "permission", "disabled", "view only")
        ):
            raise ValueError("owner_disabled_download")
        if code in ("itemnotfound", "notfound", "resourcenotfound"):
            raise ValueError("share_expired")
        if code in ("activitylimitreached", "quotalimitreached", "throttledrequest"):
            raise ValueError("share_quota_exceeded")

    if status_code in (401, 403):
        raise ValueError("owner_disabled_download")
    if status_code == 404:
        raise ValueError("share_expired")
    if status_code in (429, 509):
        raise ValueError("share_quota_exceeded")


def _load_netscape_cookies(cookies_file: Optional[str]) -> dict[str, str]:
    """Parse a Netscape cookies.txt file into a name->value dict for OneDrive/SharePoint."""
    if not cookies_file:
        return {}
    out: dict[str, str] = {}
    try:
        with open(cookies_file, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                parts = s.split("\t")
                if len(parts) >= 7:
                    dom, name, val = parts[0].lower(), parts[5].strip(), parts[6].strip()
                    if any(d in dom for d in ("live.com", "onedrive.", "sharepoint.", "microsoft.", "1drv.ms")) and name:
                        out[name] = val
    except Exception:
        pass
    return out


async def resolve_onedrive_share(
    url: str,
    quality: str = "video",
    client: Optional[httpx.AsyncClient] = None,
    cookies_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve a OneDrive / SharePoint public share link into a dict compatible with
    extract_video_info() in routes.py.
    """
    if not is_onedrive_url(url):
        raise ValueError("invalid_url")

    token = encode_onedrive_share_token(url)
    api_url = f"{_API_BASE}/shares/{token}/driveItem"

    user_cookies = _load_netscape_cookies(cookies_file)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": _UA, "Accept": "application/json"},
            cookies=user_cookies or None,
            timeout=httpx.Timeout(_TIMEOUT),
            follow_redirects=True,
        )

    try:
        resp = await client.get(api_url)
        ct = (resp.headers.get("content-type") or "").lower()

        if "application/json" in ct or (resp.text or "").lstrip().startswith("{"):
            try:
                data = resp.json()
            except Exception:
                data = {}
            _check_onedrive_error_payload(data, resp.status_code)

            # Check capabilities / view-only flags
            caps = data.get("capabilities") or {}
            shared = data.get("shared") or {}
            if (
                caps.get("canDownload") is False
                or data.get("viewOnly") is True
                or shared.get("preventDownload") is True
                or data.get("blockDownload") is True
            ):
                raise ValueError("owner_disabled_download")

            dl_url = (
                data.get("@content.downloadUrl")
                or data.get("@microsoft.graph.downloadUrl")
                or data.get("downloadUrl")
                or ""
            ).strip()

            if not dl_url:
                # If driveItem returned without downloadUrl, check if SharePoint fallback or disabled
                if "sharepoint.com" in url.lower() and resp.status_code < 400 and not data.get("id"):
                    dl_url = append_download_param(url)
                else:
                    raise ValueError("owner_disabled_download")

            name = data.get("name") or "onedrive_file.mp4"
            title = name.rsplit(".", 1)[0] if "." in name else name
            ext = name.rsplit(".", 1)[1].lower() if "." in name else "mp4"
            try:
                size_mb = round(int(data.get("size") or 0) / (1024 * 1024), 2)
            except (ValueError, TypeError):
                size_mb = 0.0

            video_meta = data.get("video") or {}
            height = int(video_meta.get("height") or 0)
            width = int(video_meta.get("width") or 0)
            duration_sec = int(round(float(video_meta.get("duration") or 0) / 1000.0))

            thumbnails = data.get("thumbnails") or []
            thumb_url = ""
            if thumbnails and isinstance(thumbnails[0], dict):
                thumb_url = (thumbnails[0].get("large") or {}).get("url") or ""

            formats = []
            if dl_url:
                formats.append({
                    "quality": f"{height}p" if height else "original",
                    "height": height,
                    "width": width,
                    "url": dl_url,
                    "ext": ext,
                })

            return {
                "title": title,
                "thumbnail_url": thumb_url,
                "direct_mp4_url": dl_url,
                "original_url": url,
                "quality": quality,
                "duration": duration_sec,
                "file_size_mb": size_mb,
                "available_formats": formats,
                "share_token": token,
                "max_merge_height": 0,
                "downloaded_height": height,
                "downloaded_vcodec": "",
                "downloaded_universal": None,
                "downloaded_media": {"measured": False},
                "is_audio_only": ext in ("mp3", "m4a", "wav", "flac", "ogg", "aac"),
                "uploader": "OneDrive",
                "has_subtitles": False,
                "has_auto_captions": False,
                "available_subtitle_languages": [],
                "subtitle_source": "none",
                "chapters": [],
                "tags": [],
                "images": [],
                "audio_url": "",
            }

        # Non-JSON response (HTML / redirect / SharePoint fallback)
        text_lower = (resp.text or "").lower()
        final_url_lower = str(resp.url).lower()
        if (
            resp.status_code in (401, 403)
            or any(k in text_lower for k in _DISABLED_KEYWORDS)
            or "login.microsoftonline.com" in final_url_lower
            or "login.live.com" in final_url_lower
        ):
            raise ValueError("owner_disabled_download")
        if resp.status_code == 404:
            raise ValueError("share_expired")
        if resp.status_code in (429, 509):
            raise ValueError("share_quota_exceeded")

        dl_url = append_download_param(url)
        return {
            "title": "OneDrive File",
            "thumbnail_url": "",
            "direct_mp4_url": dl_url,
            "original_url": url,
            "quality": quality,
            "duration": 0,
            "file_size_mb": 0.0,
            "available_formats": [
                {"quality": "original", "height": 0, "width": 0, "url": dl_url, "ext": "mp4"}
            ],
            "share_token": token,
            "max_merge_height": 0,
            "downloaded_height": 0,
            "downloaded_vcodec": "",
            "downloaded_universal": None,
            "downloaded_media": {"measured": False},
            "is_audio_only": False,
            "uploader": "OneDrive",
            "has_subtitles": False,
            "has_auto_captions": False,
            "available_subtitle_languages": [],
            "subtitle_source": "none",
            "chapters": [],
            "tags": [],
            "images": [],
            "audio_url": "",
        }
    finally:
        if own_client:
            await client.aclose()


class OneDriveExtractor(BaseExtractor):
    platform_name = "onedrive"
    supported_features = frozenset({"video", "audio", "batch"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [
        r"1drv\.ms/",
        r"onedrive\.live\.com/",
        r"[A-Za-z0-9-]+\.sharepoint\.com/",
    ]

    def detect_url(self, url: str) -> bool:
        return is_onedrive_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        try:
            info = _run_sync(
                resolve_onedrive_share(
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
            platform="onedrive",
            source_type="audio" if info.get("is_audio_only") else "video",
            title=info.get("title", ""),
            author="OneDrive",
            thumbnail=info.get("thumbnail_url", ""),
            direct_url=dl_url,
            formats=[FormatInfo(quality="original", url=dl_url, size_estimate_mb=info.get("file_size_mb", 0.0))],
            raw_metadata=info,
            extraction_method="onedrive_shares_api",
        )


REGISTRY.register(OneDriveExtractor())
