"""
Terabox Public Share Link Extractor
====================================
Resolves public Terabox share links across Terabox mirror domains:
  - terabox.com/s/<surl>
  - 1024tera.com, 1024terabox.com, teraboxapp.com, freeterabox.com,
    mirrobox.com, nephobox.com, 4funbox.com, momerybox.com, tibibox.com

Features:
  - Queries /api/shorturlinfo for share file list, metadata, and dlink
  - Surfaces share_password_required when password-protected and no/invalid pwd provided
  - Supports optional user-supplied `ndus` cookie in options
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

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

_API_BASE = "https://www.terabox.com"
_TIMEOUT = 20.0
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_TERABOX_DOMAINS = (
    "terabox.com",
    "1024tera.com",
    "1024terabox.com",
    "teraboxapp.com",
    "freeterabox.com",
    "mirrobox.com",
    "nephobox.com",
    "4funbox.com",
    "momerybox.com",
    "tibibox.com",
)

_S_PATH_RE = re.compile(r"/s/([A-Za-z0-9_-]+)", re.I)
_PWD_RE = re.compile(r"(?:[?&#]|^)(?:pwd|pass_code)=([A-Za-z0-9_-]+)", re.I)


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


def is_terabox_url(url: str) -> bool:
    return parse_terabox_url(url) is not None


def parse_terabox_url(url: str) -> Optional[dict[str, str]]:
    """
    Extract {'surl': ..., 'pwd': ...} from a Terabox share link.
    Returns None if not a valid Terabox share URL.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None

    if not any(host == d or host.endswith("." + d) for d in _TERABOX_DOMAINS):
        return None

    surl = ""
    m = _S_PATH_RE.search(parts.path)
    if m:
        surl = m.group(1)
    else:
        qs = parse_qs(parts.query)
        surl = (qs.get("surl") or qs.get("shorturl") or [""])[0]
        if surl and not surl.startswith("1") and "surl=" in parts.query:
            surl = "1" + surl

    if not surl:
        return None

    pwd = ""
    pm = _PWD_RE.search(parts.query) or _PWD_RE.search(parts.fragment)
    if pm:
        pwd = pm.group(1)

    return {"surl": surl, "pwd": pwd}


def _check_terabox_errno(data: dict, has_pwd: bool) -> None:
    errno = data.get("errno")
    if errno is None or errno == 0:
        return

    errmsg = str(data.get("errmsg") or data.get("show_msg") or "").lower()
    if (
        errno in (-9, -12, 2, 105, 400141)
        or "pwd" in errmsg
        or "password" in errmsg
        or "verify" in errmsg
        or "extract code" in errmsg
    ):
        raise ValueError("share_password_required")

    if errno in (-6, -7, 110, 429) or "quota" in errmsg or "limit" in errmsg:
        raise ValueError("share_quota_exceeded")

    if errno in (-1, 1, -3, 404, 115) or "expired" in errmsg or "not found" in errmsg:
        raise ValueError("share_expired")

    if not has_pwd and data.get("need_verify"):
        raise ValueError("share_password_required")

    raise ValueError("share_expired")


async def resolve_terabox_share(
    url: str,
    quality: str = "video",
    pwd: str = "",
    ndus: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Resolve a public Terabox share link into a dict compatible with
    extract_video_info() in routes.py.
    """
    parsed = parse_terabox_url(url)
    if not parsed:
        raise ValueError("invalid_url")

    surl = parsed["surl"]
    effective_pwd = (pwd or parsed["pwd"] or "").strip()

    headers = {
        "User-Agent": _UA,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.terabox.com/",
    }
    if ndus:
        headers["Cookie"] = f"ndus={ndus.strip()}"

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(_TIMEOUT),
            follow_redirects=True,
        )

    try:
        params: dict[str, str] = {"shorturl": surl, "root": "1"}
        if effective_pwd:
            params["pwd"] = effective_pwd

        resp = await client.get(f"{_API_BASE}/api/shorturlinfo", params=params)
        if resp.status_code == 404:
            raise ValueError("share_expired")
        if resp.status_code in (401, 403):
            raise ValueError("share_password_required" if not effective_pwd else "owner_disabled_download")
        if resp.status_code == 429:
            raise ValueError("share_quota_exceeded")
        resp.raise_for_status()

        data = resp.json()
        _check_terabox_errno(data, bool(effective_pwd))

        if data.get("need_verify") and not effective_pwd:
            raise ValueError("share_password_required")

        file_list: List[dict] = data.get("list") or []
        if not file_list:
            raise ValueError("no_media_found")

        # Flatten files (skip bare directory rows if file rows exist, or inspect nested list)
        files: List[dict] = []
        for item in file_list:
            if str(item.get("isdir", "0")) in ("0", "False", "false"):
                files.append(item)
            elif isinstance(item.get("list"), list):
                files.extend(item["list"])
        if not files:
            files = file_list

        # Pick largest file
        files.sort(key=lambda f: int(f.get("size") or 0), reverse=True)
        target = files[0]

        filename = target.get("server_filename") or target.get("filename") or f"terabox_{surl}.mp4"
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        ext = filename.rsplit(".", 1)[1].lower() if "." in filename else "mp4"
        size_mb = round(int(target.get("size") or 0) / (1024 * 1024), 2)

        dlink = (target.get("dlink") or target.get("download_link") or "").strip()
        if not dlink:
            raise ValueError("owner_disabled_download")

        thumbs = target.get("thumbs") or {}
        thumb_url = (
            thumbs.get("url3") or thumbs.get("url2") or thumbs.get("url1") or thumbs.get("icon") or ""
        ) if isinstance(thumbs, dict) else ""

        return {
            "title": title,
            "filename": filename,
            "thumbnail_url": thumb_url,
            "direct_mp4_url": dlink,
            "original_url": url,
            "quality": quality,
            "duration": 0,
            "file_size_mb": size_mb,
            "available_formats": [
                {
                    "quality": "original",
                    "height": 0,
                    "width": 0,
                    "url": dlink,
                    "ext": ext,
                }
            ],
            "files": [
                {
                    "fs_id": f.get("fs_id"),
                    "filename": f.get("server_filename") or f.get("filename") or "",
                    "size": int(f.get("size") or 0),
                    "dlink": f.get("dlink") or "",
                }
                for f in files
            ],
            "max_merge_height": 0,
            "downloaded_height": 0,
            "downloaded_vcodec": "",
            "downloaded_universal": None,
            "downloaded_media": {"measured": False},
            "is_audio_only": ext in ("mp3", "m4a", "wav", "flac", "ogg", "aac"),
            "uploader": data.get("title") or "Terabox",
            "has_subtitles": False,
            "has_auto_captions": False,
            "available_subtitle_languages": [],
            "subtitle_source": "none",
            "chapters": [],
            "tags": [],
            "images": [],
            "audio_url": "",
            "http_headers": headers,
        }
    finally:
        if own_client:
            await client.aclose()


class TeraboxExtractor(BaseExtractor):
    platform_name = "terabox"
    supported_features = frozenset({"video", "audio", "batch"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [
        r"(?:terabox|1024tera|1024terabox|teraboxapp|freeterabox|mirrobox|nephobox|4funbox|momerybox|tibibox)\.com/(?:s/[A-Za-z0-9_-]+|sharing/link\?.*surl=[A-Za-z0-9_-]+)",
    ]

    def detect_url(self, url: str) -> bool:
        return is_terabox_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        try:
            info = _run_sync(
                resolve_terabox_share(
                    url,
                    quality=kwargs.get("quality", "video"),
                    pwd=kwargs.get("pwd") or kwargs.get("pass_code") or "",
                    ndus=kwargs.get("ndus") or "",
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
            platform="terabox",
            source_type="audio" if info.get("is_audio_only") else "video",
            title=info.get("title", ""),
            author="Terabox",
            thumbnail=info.get("thumbnail_url", ""),
            direct_url=dl_url,
            formats=[FormatInfo(quality="original", url=dl_url, size_estimate_mb=info.get("file_size_mb", 0.0))],
            raw_metadata=info,
            extraction_method="terabox_shorturlinfo",
        )

    def extract_batch(self, url: str, limit: int = 50, **kwargs) -> BatchResult:
        try:
            info = _run_sync(
                resolve_terabox_share(
                    url,
                    pwd=kwargs.get("pwd") or kwargs.get("pass_code") or "",
                    ndus=kwargs.get("ndus") or "",
                    client=kwargs.get("client"),
                )
            )
        except ValueError as e:
            code = str(e)
            meta = get_error_meta(code)
            return self._make_error_batch(code, meta.get("user_message", code))
        except Exception as e:
            return self._make_error_batch("processing_failed", str(e))

        files = info.get("files") or []
        entries = [
            BatchEntry(url=f.get("dlink") or url, title=f.get("filename") or "")
            for f in files[:limit]
        ]
        return BatchResult(
            platform="terabox",
            channel_title=info.get("title", "Terabox Share"),
            entries=entries,
            total_found=len(files),
            total_queued=len(entries),
        )


REGISTRY.register(TeraboxExtractor())
