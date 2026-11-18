"""
PikPak Share Link Resolver (PikPak v2)
======================================
Resolves PikPak share links (https://mypikpak.com/s/<share_id>) to direct
download URLs using PikPak's REST API.

PikPak v2 capabilities:
  - Password-protected shares (`?pwd=xxxx`, `#pwd=xxxx`, or `pass_code` arg);
    raises `ValueError("share_password_required")` when required and missing/invalid.
  - Recursive folder traversal (up to depth 3 / max 200 items) in `extract_batch`
    and `resolve_pikpak_share` so nested folders inside a share resolve all files.
  - All file types supported (video, audio, archive, document) when `web_content_link`,
    `medias`, or `links` is present, or when an optional user PikPak token is configured
    via `PIKPAK_USER_TOKEN` or passed via `user_token`.
"""

from __future__ import annotations

import logging
import os
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

# ── Constants ──────────────────────────────────────────────────────────────

_API_BASE = "https://api-drive.mypikpak.com"
_CLIENT_ID = "YNxT9w7GMdWvEOKa"
_CLIENT_VERSION = "1.0.0"
_TIMEOUT = 20  # seconds per API call
_MAX_DEPTH = 3
_MAX_ITEMS = 200

_SHARE_URL_RE = re.compile(
    r"mypikpak\.(?:com|net)/s/([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)

_PWD_RE = re.compile(r"(?:[?&#]|^)(?:pwd|pass_code)=([A-Za-z0-9_-]+)", re.I)

# File extensions considered "video"
_VIDEO_EXTS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".mpg", ".mpeg", ".3gp", ".vob", ".rmvb",
})

_AUDIO_EXTS = frozenset({
    ".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus",
})


# ── Helpers ────────────────────────────────────────────────────────────────

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


def _common_headers(user_token: str = "") -> dict[str, str]:
    headers = {
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
    token = (user_token or os.getenv("PIKPAK_USER_TOKEN") or "").strip()
    if token:
        if not token.lower().startswith("bearer "):
            token = f"Bearer {token}"
        headers["Authorization"] = token
    return headers


def parse_share_url(url: str) -> Optional[tuple[str, str]]:
    """Extract (share_id, parent_id) from a PikPak share URL.
    Returns None if the URL is not a PikPak share link."""
    m = _SHARE_URL_RE.search(url or "")
    if not m:
        return None
    return m.group(1), m.group(2) or ""


def extract_pass_code(url: str, pass_code: str = "") -> str:
    """Extract pass_code from explicit argument or URL query/fragment (?pwd=... or #pwd=...)."""
    if pass_code and pass_code.strip():
        return pass_code.strip()
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        for seg in (parts.query, parts.fragment):
            if seg:
                m = _PWD_RE.search(seg)
                if m:
                    return m.group(1)
        qs = parse_qs(parts.query)
        val = (qs.get("pwd") or qs.get("pass_code") or [""])[0]
        if val:
            return val.strip()
    except ValueError:
        pass
    return ""


def is_pikpak_url(url: str) -> bool:
    u = (url or "").lower()
    return "mypikpak.com" in u or "mypikpak.net" in u


def _is_video_file(name: str) -> bool:
    """Check if a filename is a video by extension."""
    name_lower = (name or "").lower()
    return any(name_lower.endswith(ext) for ext in _VIDEO_EXTS)


def _is_audio_file(name: str) -> bool:
    name_lower = (name or "").lower()
    return any(name_lower.endswith(ext) for ext in _AUDIO_EXTS)


def _is_folder(file_info: dict) -> bool:
    """Return True if a PikPak item represents a folder."""
    kind = str(file_info.get("kind") or file_info.get("type") or "").lower()
    mime = str(file_info.get("mime_type") or "").lower()
    return (
        kind in ("drive#folder", "folder")
        or bool(file_info.get("isdir"))
        or "folder" in mime
    )


def _best_media_url(file_info: dict) -> Optional[str]:
    """Extract the best download URL from a PikPak file object.

    Priority:
      1. web_content_link (original quality direct download)
      2. links["application/octet-stream"].url (or any entry in links dict)
      3. medias[].link.url (highest resolution first)
      4. download_url / url
    """
    wcl = (file_info.get("web_content_link") or "").strip()
    if wcl:
        return wcl

    links = file_info.get("links")
    if isinstance(links, dict):
        for _, link_obj in links.items():
            if isinstance(link_obj, dict):
                lu = (link_obj.get("url") or "").strip()
                if lu:
                    return lu

    medias = file_info.get("medias") or []
    if medias:
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

    direct = (file_info.get("download_url") or file_info.get("url") or "").strip()
    if direct:
        return direct

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

def _check_share_status(share_info: dict, pass_code: str) -> None:
    status = str(share_info.get("share_status") or "").upper()
    err = str(share_info.get("error") or share_info.get("error_description") or "").lower()

    if (
        status in ("PASS_CODE_EMPTY", "PASS_CODE_ERROR", "PASS_CODE_INVALID", "NEED_PASS_CODE")
        or share_info.get("need_pass_code") is True
        or share_info.get("pass_code_required") is True
        or "pass_code" in err
        or "password" in err
    ):
        if not pass_code or not share_info.get("pass_code_token"):
            raise ValueError("share_password_required")

    if status in ("DELETED", "EXPIRED", "NOT_FOUND", "FILE_DELETED"):
        raise ValueError("share_expired")


async def _get_share_info(client: httpx.AsyncClient, share_id: str, pass_code: str = "") -> dict:
    """GET /drive/v1/share — share metadata."""
    params: dict[str, str] = {"share_id": share_id, "thumbnail_size": "SIZE_LARGE"}
    if pass_code:
        params["pass_code"] = pass_code
    resp = await client.get(f"{_API_BASE}/drive/v1/share", params=params)
    if resp.status_code in (401, 403):
        raise ValueError("share_password_required")
    if resp.status_code == 400:
        body_lower = (resp.text or "").lower()
        if "pass_code" in body_lower or "password" in body_lower:
            raise ValueError("share_password_required")
    if resp.status_code == 404:
        raise ValueError("share_expired")
    resp.raise_for_status()
    data = resp.json()
    _check_share_status(data, pass_code)
    return data


async def _list_share_files(
    client: httpx.AsyncClient,
    share_id: str,
    parent_id: str = "",
    pass_code_token: str = "",
    limit: int = 100,
) -> List[dict]:
    """GET /drive/v1/share/list — list items in a single share folder (paginated)."""
    all_files: list[dict] = []
    page_token = ""

    for _ in range(10):  # max 10 pages = 1000 items per folder
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
        if resp.status_code in (401, 403):
            raise ValueError("share_password_required")
        resp.raise_for_status()
        data = resp.json()

        files = data.get("files") or []
        all_files.extend(files)

        page_token = data.get("next_page_token") or ""
        if not page_token or not files:
            break

    return all_files


async def _list_share_files_recursive(
    client: httpx.AsyncClient,
    share_id: str,
    parent_id: str = "",
    pass_code_token: str = "",
    max_depth: int = _MAX_DEPTH,
    max_items: int = _MAX_ITEMS,
) -> List[dict]:
    """
    Recursively traverse nested folders in a PikPak share up to `max_depth`
    levels deep, collecting at most `max_items` file entries.
    """
    collected: list[dict] = []
    visited_folders: set[str] = set()

    async def _walk(curr_parent: str, depth: int, path_prefix: str) -> None:
        if len(collected) >= max_items:
            return
        folder_key = curr_parent or "__root__"
        if folder_key in visited_folders:
            return
        visited_folders.add(folder_key)

        items = await _list_share_files(
            client,
            share_id=share_id,
            parent_id=curr_parent,
            pass_code_token=pass_code_token,
        )
        subfolders: list[tuple[str, str]] = []
        for item in items:
            if len(collected) >= max_items:
                break
            name = item.get("name") or ""
            rel_path = f"{path_prefix}/{name}".lstrip("/") if path_prefix else name
            if _is_folder(item):
                folder_id = (item.get("id") or "").strip()
                if folder_id and depth < max_depth:
                    subfolders.append((folder_id, rel_path))
            else:
                enriched = dict(item)
                enriched.setdefault("path", rel_path)
                collected.append(enriched)

        for folder_id, folder_path in subfolders:
            if len(collected) >= max_items:
                break
            await _walk(folder_id, depth + 1, folder_path)

    await _walk(parent_id, 0, "")
    return collected[:max_items]


# ── Public Entry Point ─────────────────────────────────────────────────────

async def resolve_pikpak_share(
    url: str,
    quality: str = "video",
    pass_code: str = "",
    user_token: str = "",
    client: Optional[httpx.AsyncClient] = None,
    max_depth: int = _MAX_DEPTH,
    max_items: int = _MAX_ITEMS,
) -> Dict[str, Any]:
    """
    Resolve a PikPak share link to a dict compatible with extract_video_info().

    Supports password-protected shares, nested folders (up to depth 3 / 200 items),
    and all file types (video, audio, archives, documents).
    """
    parsed = parse_share_url(url)
    if not parsed:
        raise ValueError("Liên kết PikPak không hợp lệ. Hãy dán liên kết chia sẻ PikPak (mypikpak.com/s/...).")

    share_id, parent_id = parsed
    effective_pass_code = extract_pass_code(url, pass_code)

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers=_common_headers(user_token=user_token),
            timeout=httpx.Timeout(_TIMEOUT),
            follow_redirects=True,
        )

    try:
        # 1. Get share info
        try:
            share_info = await _get_share_info(client, share_id, pass_code=effective_pass_code)
        except ValueError:
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise ValueError("share_expired")
            if e.response.status_code in (401, 403):
                raise ValueError("share_password_required")
            raise ValueError(f"Không truy cập được liên kết PikPak (HTTP {e.response.status_code}).")
        except httpx.TimeoutException:
            raise ValueError("PikPak API không phản hồi. Hãy thử lại sau.")
        except Exception as e:
            raise ValueError(f"Lỗi kết nối PikPak: {e}")

        pass_code_token = share_info.get("pass_code_token") or ""
        share_title = share_info.get("title") or share_info.get("name") or "PikPak Share"

        # 2. Recursively list files in the share (up to depth 3 / max 200 items)
        try:
            files = await _list_share_files_recursive(
                client,
                share_id=share_id,
                parent_id=parent_id,
                pass_code_token=pass_code_token,
                max_depth=max_depth,
                max_items=max_items,
            )
        except ValueError:
            raise
        except httpx.HTTPStatusError as e:
            raise ValueError(f"Không lấy được danh sách file PikPak (HTTP {e.response.status_code}).")
        except Exception as e:
            raise ValueError(f"Lỗi lấy danh sách file PikPak: {e}")

        if not files:
            raise ValueError("Liên kết PikPak trống — không có file nào.")

        # 3. Prefer video files when present, otherwise support any file type
        video_files = [f for f in files if _is_video_file(f.get("name", ""))]
        candidates = video_files if video_files else list(files)

        # Pick the largest candidate with a downloadable link (or largest overall)
        candidates.sort(
            key=lambda f: (1 if _best_media_url(f) else 0, int(f.get("size") or 0)),
            reverse=True,
        )
        target = candidates[0]

        # 4. Get download URL
        download_url = _best_media_url(target)
        if not download_url:
            raise ValueError("owner_disabled_download")

        file_name = target.get("name") or "video.mp4"
        file_title = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
        ext = file_name.rsplit(".", 1)[1].lower() if "." in file_name else "mp4"
        thumbnail = _file_thumbnail(target)
        size_mb = _file_size_mb(target)
        is_audio = _is_audio_file(file_name)

        # 5. Build format list from medias[] or original link
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

        formats.sort(key=lambda f: f.get("height", 0), reverse=True)
        if not formats and download_url:
            formats.append({
                "quality": "original",
                "height": 0,
                "width": 0,
                "url": download_url,
                "ext": ext,
            })

        resolved_files = [
            {
                "id": f.get("id", ""),
                "name": f.get("name", ""),
                "path": f.get("path", f.get("name", "")),
                "size": int(f.get("size") or 0),
                "url": _best_media_url(f) or f"https://mypikpak.com/s/{share_id}/{f.get('id', '')}",
                "is_video": _is_video_file(f.get("name", "")),
            }
            for f in files
        ]

        logger.info(
            "[PikPak] Resolved share=%s file=%s total_files=%d size=%.1fMB formats=%d",
            share_id, file_name, len(files), size_mb, len(formats),
        )

        return {
            "title": file_title,
            "filename": file_name,
            "thumbnail_url": thumbnail,
            "direct_mp4_url": download_url,
            "original_url": url,
            "quality": quality,
            "duration": 0,
            "file_size_mb": round(size_mb, 2),
            "available_formats": formats,
            "files": resolved_files,
            "max_merge_height": 0,
            "downloaded_height": 0,
            "downloaded_vcodec": "",
            "downloaded_universal": None,
            "downloaded_media": {"measured": False},
            "is_audio_only": is_audio,
            "uploader": share_title if share_title != file_title else None,
            "has_subtitles": False,
            "has_auto_captions": False,
            "available_subtitle_languages": [],
            "subtitle_source": "none",
            "chapters": [],
            "tags": [],
            "images": [],
            "audio_url": download_url if is_audio else "",
            "http_headers": {
                "Referer": "https://mypikpak.com/",
            },
        }
    finally:
        if own_client:
            await client.aclose()


# ── Extractor class (for the registry) ────────────────────────────────────

class PikPakExtractor(BaseExtractor):
    platform_name = "pikpak"
    supported_features = frozenset({"video", "audio", "batch", "channel"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [
        r"mypikpak\.(?:com|net)/s/[A-Za-z0-9_-]+",
    ]

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        """Sync wrapper — the async resolve_pikpak_share is called from routes.py."""
        try:
            info = _run_sync(
                resolve_pikpak_share(
                    url,
                    quality=kwargs.get("quality", "video"),
                    pass_code=kwargs.get("pass_code") or kwargs.get("pwd") or "",
                    user_token=kwargs.get("user_token") or "",
                    client=kwargs.get("client"),
                )
            )
        except ValueError as e:
            code = str(e)
            if code in ("share_password_required", "share_expired", "owner_disabled_download", "share_quota_exceeded"):
                meta = get_error_meta(code)
                return self._make_error_result(code, meta.get("user_message", code))
            return self._make_error_result("unsupported_url", code)
        except Exception as e:
            return self._make_error_result("processing_failed", str(e))

        dl_url = info.get("direct_mp4_url", "")
        return ExtractResult(
            platform="pikpak",
            source_type="audio" if info.get("is_audio_only") else "video",
            title=info.get("title", ""),
            author=info.get("uploader", "") or "",
            thumbnail=info.get("thumbnail_url", ""),
            direct_url=dl_url,
            formats=[FormatInfo(quality="original", url=dl_url, size_estimate_mb=info.get("file_size_mb", 0.0))],
            raw_metadata=info,
            extraction_method="pikpak_api",
        )

    def extract_batch(self, url: str, limit: int = _MAX_ITEMS, **kwargs) -> BatchResult:
        """Recursively list all files in a PikPak share (up to depth 3 / max 200 items)."""
        capped_limit = min(int(limit or _MAX_ITEMS), _MAX_ITEMS)
        try:
            info = _run_sync(
                resolve_pikpak_share(
                    url,
                    pass_code=kwargs.get("pass_code") or kwargs.get("pwd") or "",
                    user_token=kwargs.get("user_token") or "",
                    client=kwargs.get("client"),
                    max_depth=int(kwargs.get("max_depth", _MAX_DEPTH)),
                    max_items=capped_limit,
                )
            )
        except ValueError as e:
            code = str(e)
            if code in ("share_password_required", "share_expired", "owner_disabled_download", "share_quota_exceeded"):
                meta = get_error_meta(code)
                return self._make_error_batch(code, meta.get("user_message", code))
            return self._make_error_batch("unsupported_url", code)
        except Exception as e:
            return self._make_error_batch("processing_failed", str(e))

        resolved_files = info.get("files") or []
        entries = [
            BatchEntry(url=f["url"], title=f.get("path") or f.get("name") or "")
            for f in resolved_files[:capped_limit]
        ]
        return BatchResult(
            platform="pikpak",
            channel_title=info.get("uploader") or info.get("title") or "PikPak Share",
            entries=entries,
            total_found=len(resolved_files),
            total_queued=len(entries),
        )


# Register with the global registry
REGISTRY.register(PikPakExtractor())
