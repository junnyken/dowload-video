"""
Google Drive Public Share Link Extractor
=========================================
Resolves public Google Drive file and folder share links:
  - https://drive.google.com/file/d/<id>/view
  - https://drive.google.com/open?id=<id>
  - https://drive.google.com/uc?id=<id>&export=download
  - https://drive.google.com/drive/folders/<id>

Features:
  - Resolves direct download URL (uc?export=download&id=<id>)
  - Parses large-file virus-scan confirmation page (confirm= / uuid= params)
  - Parses public folders (/drive/folders/<id> or embeddedfolderview) for batch extraction
  - Detects "download quota exceeded" -> raises ValueError("share_quota_exceeded")
  - Detects owner-disabled download / private access -> raises ValueError("owner_disabled_download")
"""

from __future__ import annotations

import html as _html
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

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
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_FILE_D_RE = re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]+)", re.I)
_FOLDER_RE = re.compile(
    r"drive\.google\.com/drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]+)", re.I
)
_EMBED_FOLDER_RE = re.compile(
    r"drive\.google\.com/embeddedfolderview\?[^#]*\bid=([A-Za-z0-9_-]+)", re.I
)

_QUOTA_PATTERNS = (
    "too many users have viewed or downloaded this file recently",
    "download quota exceeded",
    "quota exceeded for this file",
    "quota exceeded",
    "you cannot view or download this file at this time",
    "bạn không thể xem hoặc tải xuống tệp này vào lúc này",
    "quá nhiều người dùng đã xem hoặc tải xuống tệp này gần đây",
)

_DISABLED_PATTERNS = (
    "you need access",
    "request access",
    "sign in to continue to google drive",
    "owner has disabled",
    "owner hasn't given you permission to download",
    "only the owner and editors can download",
    "can't download file",
    "viewer cannot download",
    "viewers cannot download",
    '"candownload":false',
    '"candownload": false',
    "candownload=false",
    "this file is private",
    "permission denied",
    "chủ sở hữu đã tắt",
    "chủ sở hữu chưa cấp cho bạn quyền tải xuống",
    "chỉ chủ sở hữu và người chỉnh sửa mới có thể tải xuống",
    "bạn cần quyền truy cập",
)

_EXPIRED_PATTERNS = (
    "sorry, the file you have requested does not exist",
    "the file you have requested does not exist",
    "file not found",
    "tệp bạn yêu cầu không tồn tại",
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


def is_gdrive_url(url: str) -> bool:
    return parse_gdrive_url(url) is not None


def parse_gdrive_url(url: str) -> Optional[tuple[str, str]]:
    """Return ('file' | 'folder', item_id) or None if not a supported GDrive URL."""
    raw = (url or "").strip()
    if not raw:
        return None

    m = _FOLDER_RE.search(raw)
    if m:
        return "folder", m.group(1)

    m = _EMBED_FOLDER_RE.search(raw)
    if m:
        return "folder", m.group(1)

    m = _FILE_D_RE.search(raw)
    if m:
        return "file", m.group(1)

    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
        host = (parts.hostname or "").lower()
        if host in ("drive.google.com", "docs.google.com", "drive.usercontent.google.com"):
            qs = parse_qs(parts.query)
            item_id = (qs.get("id") or [None])[0]
            if item_id and re.fullmatch(r"[A-Za-z0-9_-]{5,}", item_id):
                if "folder" in parts.path.lower():
                    return "folder", item_id
                return "file", item_id
    except ValueError:
        return None

    return None


def build_uc_download_url(
    file_id: str,
    confirm: Optional[str] = None,
    uuid: Optional[str] = None,
    base_url: str = "https://drive.google.com/uc",
) -> str:
    params: dict[str, str] = {"export": "download", "id": file_id}
    if confirm:
        params["confirm"] = confirm
    if uuid:
        params["uuid"] = uuid
    return f"{base_url}?{urlencode(params)}"


def _check_html_errors(html_text: str, final_url: str = "", status_code: int = 200) -> None:
    lower = _html.unescape(html_text or "").lower()
    url_lower = (final_url or "").lower()

    if any(p in lower for p in _QUOTA_PATTERNS):
        raise ValueError("share_quota_exceeded")

    if status_code == 429:
        raise ValueError("share_quota_exceeded")

    if "accounts.google.com" in url_lower or "servicelogin" in url_lower:
        raise ValueError("owner_disabled_download")

    if any(p in lower for p in _DISABLED_PATTERNS):
        raise ValueError("owner_disabled_download")

    if status_code in (401, 403):
        raise ValueError("owner_disabled_download")

    if status_code == 404 or any(p in lower for p in _EXPIRED_PATTERNS):
        raise ValueError("share_expired")


def _parse_confirm_page(html_text: str, file_id: str, cookies: Optional[dict] = None) -> Optional[dict]:
    """
    Parse Google Drive's large-file virus-scan confirmation HTML.
    Returns dict with {download_url, title, size_mb} if confirmation tokens are found.
    """
    inputs: dict[str, str] = {}
    for tag in re.finditer(r"<input\b[^>]*>", html_text, re.I):
        tag_str = tag.group(0)
        n_m = re.search(r'\bname=["\']([^"\']+)["\']', tag_str, re.I)
        v_m = re.search(r'\bvalue=["\']([^"\']*)["\']', tag_str, re.I)
        if n_m and v_m:
            inputs[n_m.group(1)] = _html.unescape(v_m.group(1))

    form_m = re.search(
        r'<form\b[^>]*id=["\']download-form["\'][^>]*action=["\']([^"\']+)["\']'
        r'|<form\b[^>]*action=["\']([^"\']+)["\'][^>]*id=["\']download-form["\']'
        r'|<form\b[^>]*action=["\'](https://drive\.usercontent\.google\.com/download[^"\']*|https://drive\.google\.com/uc[^"\']*)["\']',
        html_text,
        re.I,
    )
    action_url = ""
    if form_m:
        action_url = _html.unescape(next(g for g in form_m.groups() if g))

    confirm = inputs.get("confirm") or ""
    uuid = inputs.get("uuid") or ""

    if not confirm:
        c_m = re.search(r"[?&]confirm=([0-9A-Za-z_-]+)", html_text)
        if c_m:
            confirm = c_m.group(1)

    if not uuid:
        u_m = re.search(r"[?&]uuid=([0-9A-Za-z_-]+)", html_text)
        if u_m:
            uuid = u_m.group(1)

    if not confirm and cookies:
        for k, v in cookies.items():
            if k.startswith("download_warning") and v:
                confirm = v
                break

    if not confirm and not uuid and not action_url:
        return None

    base = action_url.split("?")[0] if action_url else (
        "https://drive.usercontent.google.com/download" if uuid else "https://drive.google.com/uc"
    )
    params: dict[str, str] = {
        "id": inputs.get("id") or file_id,
        "export": inputs.get("export") or "download",
    }
    if confirm:
        params["confirm"] = confirm
    if uuid:
        params["uuid"] = uuid
    if inputs.get("at"):
        params["at"] = inputs["at"]

    dl_url = f"{base}?{urlencode(params)}"

    # Extract filename and optional size from <span class="uc-name-size"><a ...>name</a> (1.5G)</span>
    title = ""
    size_mb = 0.0
    ns_m = re.search(
        r'class=["\']uc-name-size["\'][^>]*>\s*<a[^>]*>([^<]+)</a>(?:\s*\(([^)]+)\))?',
        html_text,
        re.I | re.S,
    )
    if ns_m:
        title = _html.unescape(ns_m.group(1).strip())
        size_str = (ns_m.group(2) or "").strip().upper()
        s_m = re.match(r"([\d.]+)\s*([KMGT])B?", size_str)
        if s_m:
            val = float(s_m.group(1))
            unit = s_m.group(2)
            mult = {"K": 1 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024.0}.get(unit, 1.0)
            size_mb = round(val * mult, 2)

    if not title:
        t_m = re.search(r"<title>([^<]+)</title>", html_text, re.I)
        if t_m:
            raw_t = _html.unescape(t_m.group(1).strip())
            raw_t = re.sub(r"\s*-\s*Google Drive\s*$", "", raw_t, flags=re.I)
            if raw_t and "virus" not in raw_t.lower():
                title = raw_t

    return {
        "download_url": dl_url,
        "title": title,
        "size_mb": size_mb,
        "confirm": confirm,
        "uuid": uuid,
    }


def _parse_folder_html(html_text: str) -> tuple[str, list[dict]]:
    """Extract folder title and file entries from public folder or embeddedfolderview HTML."""
    title = "Google Drive Folder"
    t_m = re.search(r"<title>([^<]+)</title>", html_text, re.I)
    if t_m:
        cleaned = _html.unescape(t_m.group(1).strip())
        cleaned = re.sub(r"\s*-\s*Google Drive\s*$", "", cleaned, flags=re.I)
        if cleaned:
            title = cleaned

    entries: list[dict] = []
    seen_ids: set[str] = set()

    # 1. embeddedfolderview pattern: <div class="flip-entry" id="entry-<id>">...<div class="flip-entry-title">...</div>
    for m in re.finditer(
        r'id=["\']entry-([A-Za-z0-9_-]+)["\'][^>]*>.*?class=["\'][^"\']*flip-entry-title["\'][^>]*>([^<]+)<',
        html_text,
        re.I | re.S,
    ):
        fid, fname = m.group(1), _html.unescape(m.group(2).strip())
        if fid not in seen_ids:
            seen_ids.add(fid)
            entries.append({
                "id": fid,
                "title": fname or fid,
                "url": build_uc_download_url(fid),
            })

    # 2. Public folder page data-id + title/tooltip attributes
    for m in re.finditer(
        r'data-id=["\']([A-Za-z0-9_-]{6,})["\'][^>]*>.*?(?:data-tooltip=["\']([^"\']+)["\']|<div[^>]+class=["\'][^"\']*KL4NAf[^"\']*["\'][^>]*>([^<]+)<)',
        html_text,
        re.I | re.S,
    ):
        fid = m.group(1)
        fname = _html.unescape((m.group(2) or m.group(3) or "").strip())
        if fid not in seen_ids:
            seen_ids.add(fid)
            entries.append({
                "id": fid,
                "title": fname or fid,
                "url": build_uc_download_url(fid),
            })

    # 3. Direct /file/d/<id> links in HTML
    for m in re.finditer(
        r'href=["\'](?:https://drive\.google\.com)?/file/d/([A-Za-z0-9_-]+)[^"\']*["\'][^>]*>([^<]*)<',
        html_text,
        re.I,
    ):
        fid = m.group(1)
        fname = _html.unescape(m.group(2).strip())
        if fid not in seen_ids:
            seen_ids.add(fid)
            entries.append({
                "id": fid,
                "title": fname or f"gdrive_{fid}",
                "url": build_uc_download_url(fid),
            })

    # 4. Embedded JSON array items: ["<id>",["<parent>"],"<filename>",
    for m in re.finditer(
        r'\["([A-Za-z0-9_-]{10,})"\s*,\s*\[[^\]]*\]\s*,\s*"([^"]+)"',
        html_text,
    ):
        fid, fname = m.group(1), m.group(2)
        if fid not in seen_ids:
            seen_ids.add(fid)
            entries.append({
                "id": fid,
                "title": fname,
                "url": build_uc_download_url(fid),
            })

    return title, entries


def _load_netscape_cookies(cookies_file: Optional[str]) -> dict[str, str]:
    """Parse a Netscape cookies.txt file into a name->value dict for google.com."""
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
                    if ("google." in dom or "googleusercontent." in dom) and name:
                        out[name] = val
    except Exception:
        pass
    return out


_ITAG_MAP: dict[int, tuple[int, str, str, bool]] = {
    # itag: (height, quality_label, ext, is_muxed)
    37: (1080, "1080p (Full HD)", "mp4", True),
    22: (720, "720p (HD)", "mp4", True),
    59: (480, "480p", "mp4", True),
    18: (360, "360p", "mp4", True),
    137: (1080, "1080p (Full HD)", "mp4", False),
    136: (720, "720p (HD)", "mp4", False),
    135: (480, "480p", "mp4", False),
    134: (360, "360p", "mp4", False),
    140: (0, "128k (Audio)", "m4a", False),
}


async def _extract_drive_player_stream(
    item_id: str,
    client: httpx.AsyncClient,
    quality: str = "video",
    original_url: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Fallback extractor for view-only Google Drive videos where downloads are disabled by owner.
    Extracts HTML5 videoplayback streams from Google Drive player preview/view pages.
    """
    for endpoint in (
        f"https://drive.google.com/file/d/{item_id}/preview",
        f"https://drive.google.com/file/d/{item_id}/view",
    ):
        try:
            resp = await client.get(
                endpoint,
                headers={
                    "Referer": "https://drive.google.com/",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
        except Exception as e:
            logger.warning("Failed to fetch Google Drive player page %s: %s", endpoint, e)
            continue

        if resp.status_code >= 400:
            continue

        html_text = resp.text or ""
        lower = html_text.lower()
        if "you need access" in lower or "request access" in lower or "servicelogin" in lower:
            raise ValueError("owner_disabled_download")

        # Extract title
        title = ""
        og_m = re.search(r'<meta\b[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']', html_text, re.I)
        if not og_m:
            og_m = re.search(r'<meta\b[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']', html_text, re.I)
        if og_m:
            title = _html.unescape(og_m.group(1).strip())

        if not title:
            t_m = re.search(r"<title>([^<]+)</title>", html_text, re.I)
            if t_m:
                raw_t = _html.unescape(t_m.group(1).strip())
                title = re.sub(r"\s*-\s*Google\s*(?:Drive|Tài liệu|Docs|Disk).*$", "", raw_t, flags=re.I).strip()

        if not title:
            title_m = re.search(r'["\']title["\']\s*:\s*["\']([^"\']+)["\']', html_text)
            if title_m:
                title = title_m.group(1).encode().decode("unicode-escape", errors="ignore")

        if not title or title.lower() in ("google drive", "google drive - file preview"):
            title = f"gdrive_{item_id}"

        unescaped = (
            html_text
            .replace("\\u0026", "&")
            .replace("\\u003d", "=")
            .replace("\\u0025", "%")
            .replace("\\/", "/")
            .replace(r"\/", "/")
        )

        streams_by_itag: dict[int, str] = {}

        # 1. Parse fmt_stream_map: e.g. "22|https://...|...,18|https://..."
        fmt_m = re.search(r'["\']?fmt_stream_map["\']?\s*[:=]\s*["\']([^"\']+)["\']', unescaped)
        if fmt_m:
            raw_map = fmt_m.group(1)
            for part in raw_map.split(","):
                part = part.strip()
                if not part:
                    continue
                tokens = part.split("|")
                if len(tokens) >= 2 and tokens[0].isdigit():
                    itag = int(tokens[0])
                    url = tokens[1]
                    if url.startswith("http") and itag not in streams_by_itag:
                        streams_by_itag[itag] = url

        # 2. Parse url_encoded_fmt_stream_map or adaptive_fmts
        for field in ("url_encoded_fmt_stream_map", "adaptive_fmts"):
            m = re.search(rf'["\']?{field}["\']?\s*[:=]\s*["\']([^"\']+)["\']', unescaped)
            if m:
                for entry in m.group(1).split(","):
                    qs = parse_qs(entry)
                    itag_list = qs.get("itag") or []
                    url_list = qs.get("url") or []
                    if itag_list and url_list and itag_list[0].isdigit():
                        itag = int(itag_list[0])
                        url = url_list[0]
                        if url.startswith("http") and itag not in streams_by_itag:
                            streams_by_itag[itag] = url

        # 3. Direct regex search for videoplayback URLs
        vp_urls = re.findall(
            r'https?://(?:drive|docs)\.google\.com/videoplayback\?[^"\'\\<>,\s]+',
            unescaped,
        ) + re.findall(
            r'https?://[^"\'\\<>,\s]+\.googlevideo\.com/videoplayback\?[^"\'\\<>,\s]+',
            unescaped,
        )

        for u in vp_urls:
            u = u.rstrip("\\\';\"")
            try:
                parsed_qs = parse_qs(urlsplit(u).query)
                itag_val = parsed_qs.get("itag")
                if itag_val and itag_val[0].isdigit():
                    itag = int(itag_val[0])
                    if itag not in streams_by_itag:
                        streams_by_itag[itag] = u
            except Exception:
                continue

        if not streams_by_itag:
            continue

        formats: list[dict[str, Any]] = []
        best_muxed_url: Optional[str] = None
        best_muxed_height = 0
        best_video_url: Optional[str] = None
        best_video_height = 0
        audio_url = ""

        sorted_itags = sorted(
            streams_by_itag.keys(),
            key=lambda it: _ITAG_MAP.get(it, (0, "", "", False))[0],
            reverse=True,
        )

        for it in sorted_itags:
            stream_url = streams_by_itag[it]
            meta = _ITAG_MAP.get(it)
            height = meta[0] if meta else 0
            label = meta[1] if meta else f"{it}p"
            ext = meta[2] if meta else "mp4"
            is_muxed = meta[3] if meta else True

            if it == 140 or "audio" in label.lower():
                audio_url = stream_url

            if is_muxed:
                if height > best_muxed_height or not best_muxed_url:
                    best_muxed_url = stream_url
                    best_muxed_height = height
            else:
                if height > best_video_height or not best_video_url:
                    best_video_url = stream_url
                    best_video_height = height

            formats.append({
                "quality": f"{height}p" if height > 0 else label,
                "height": height,
                "width": round(height * 16 / 9) if height > 0 else 0,
                "url": stream_url,
                "ext": ext,
            })

        chosen_url = best_muxed_url or best_video_url or list(streams_by_itag.values())[0]
        chosen_height = best_muxed_height or best_video_height
        clean_title = title.rsplit(".", 1)[0] if "." in title else title

        return {
            "title": clean_title,
            "thumbnail_url": "",
            "direct_mp4_url": chosen_url,
            "original_url": original_url or f"https://drive.google.com/file/d/{item_id}/view",
            "quality": quality,
            "duration": 0,
            "file_size_mb": 0.0,
            "available_formats": formats,
            "max_merge_height": chosen_height,
            "downloaded_height": chosen_height,
            "downloaded_vcodec": "h264",
            "downloaded_universal": None,
            "downloaded_media": {"measured": False},
            "is_audio_only": False,
            "uploader": "Google Drive",
            "has_subtitles": False,
            "has_auto_captions": False,
            "available_subtitle_languages": [],
            "subtitle_source": "none",
            "chapters": [],
            "tags": [],
            "images": [],
            "audio_url": audio_url,
        }

    return None


async def resolve_gdrive_share(
    url: str,
    quality: str = "video",
    client: Optional[httpx.AsyncClient] = None,
    cookies_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve a Google Drive file or folder link into a dict compatible with
    extract_video_info() in routes.py.
    """
    parsed = parse_gdrive_url(url)
    if not parsed:
        raise ValueError("invalid_url")

    kind, item_id = parsed
    user_cookies = _load_netscape_cookies(cookies_file)
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": _UA},
            cookies=user_cookies or None,
            timeout=httpx.Timeout(_TIMEOUT),
            follow_redirects=True,
        )

    try:
        if kind == "folder":
            folder_url = f"https://drive.google.com/drive/folders/{item_id}"
            resp = await client.get(folder_url)
            html_text = resp.text or ""
            _check_html_errors(html_text, str(resp.url), resp.status_code)
            folder_title, entries = _parse_folder_html(html_text)
            if not entries:
                # Fallback: try embeddedfolderview
                embed_url = f"https://drive.google.com/embeddedfolderview?id={item_id}#list"
                resp2 = await client.get(embed_url)
                if resp2.status_code < 400:
                    _, entries = _parse_folder_html(resp2.text or "")
            if not entries:
                raise ValueError("no_media_found")

            first = entries[0]
            return {
                "title": folder_title,
                "thumbnail_url": "",
                "direct_mp4_url": first["url"],
                "original_url": url,
                "quality": quality,
                "duration": 0,
                "file_size_mb": 0.0,
                "available_formats": [
                    {
                        "quality": "original",
                        "height": 0,
                        "width": 0,
                        "url": first["url"],
                        "ext": "mp4",
                    }
                ],
                "entries": entries,
                "max_merge_height": 0,
                "downloaded_height": 0,
                "downloaded_vcodec": "",
                "downloaded_universal": None,
                "downloaded_media": {"measured": False},
                "is_audio_only": False,
                "uploader": "Google Drive",
                "has_subtitles": False,
                "has_auto_captions": False,
                "available_subtitle_languages": [],
                "subtitle_source": "none",
                "chapters": [],
                "tags": [],
                "images": [],
                "audio_url": "",
            }

        # Single file
        uc_url = build_uc_download_url(item_id)
        resp = await client.get(uc_url)
        ct = (resp.headers.get("content-type") or "").lower()
        final_url = str(resp.url)

        if resp.status_code >= 400 or "text/html" in ct:
            html_text = resp.text or ""
            try:
                _check_html_errors(html_text, final_url, resp.status_code)
            except ValueError as err:
                if str(err) == "owner_disabled_download":
                    player_info = await _extract_drive_player_stream(
                        item_id, client, quality=quality, original_url=url
                    )
                    if player_info:
                        return player_info
                raise

            cookies_dict = dict(resp.cookies)
            confirmed = _parse_confirm_page(html_text, item_id, cookies=cookies_dict)
            if confirmed:
                dl_url = confirmed["download_url"]
                title = confirmed["title"] or f"gdrive_{item_id}"
                size_mb = confirmed["size_mb"]
            else:
                player_info = await _extract_drive_player_stream(
                    item_id, client, quality=quality, original_url=url
                )
                if player_info:
                    return player_info
                dl_url = uc_url
                title = f"gdrive_{item_id}"
                size_mb = 0.0
        else:
            dl_url = final_url if final_url and final_url != uc_url else uc_url
            cd = resp.headers.get("content-disposition") or ""
            fn_m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', cd, re.I)
            title = fn_m.group(1).strip() if fn_m else f"gdrive_{item_id}"
            try:
                size_mb = round(int(resp.headers.get("content-length") or 0) / (1024 * 1024), 2)
            except (ValueError, TypeError):
                size_mb = 0.0

        clean_title = title.rsplit(".", 1)[0] if "." in title else title
        ext = title.rsplit(".", 1)[1].lower() if "." in title else "mp4"

        return {
            "title": clean_title,
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
            "max_merge_height": 0,
            "downloaded_height": 0,
            "downloaded_vcodec": "",
            "downloaded_universal": None,
            "downloaded_media": {"measured": False},
            "is_audio_only": False,
            "uploader": "Google Drive",
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


class GDriveExtractor(BaseExtractor):
    platform_name = "gdrive"
    supported_features = frozenset({"video", "audio", "batch", "channel"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [
        r"drive\.google\.com/file/d/[A-Za-z0-9_-]+",
        r"drive\.google\.com/drive/(?:u/\d+/)?folders/[A-Za-z0-9_-]+",
        r"drive\.google\.com/embeddedfolderview\?.*id=[A-Za-z0-9_-]+",
        r"drive\.(?:usercontent\.)?google\.com/(?:open|uc|download)\?.*id=[A-Za-z0-9_-]+",
    ]

    def detect_url(self, url: str) -> bool:
        return is_gdrive_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        try:
            info = _run_sync(
                resolve_gdrive_share(
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
            platform="gdrive",
            source_type="video",
            title=info.get("title", ""),
            author="Google Drive",
            thumbnail=info.get("thumbnail_url", ""),
            direct_url=dl_url,
            formats=[FormatInfo(quality="original", url=dl_url, size_estimate_mb=info.get("file_size_mb", 0.0))],
            raw_metadata=info,
            extraction_method="gdrive_uc",
        )

    def extract_batch(self, url: str, limit: int = 50, **kwargs) -> BatchResult:
        parsed = parse_gdrive_url(url)
        if not parsed or parsed[0] != "folder":
            return self._make_error_batch("invalid_url", "URL không phải thư mục Google Drive.")
        try:
            info = _run_sync(resolve_gdrive_share(url, client=kwargs.get("client")))
        except ValueError as e:
            code = str(e)
            meta = get_error_meta(code)
            return self._make_error_batch(code, meta.get("user_message", code))
        except Exception as e:
            return self._make_error_batch("processing_failed", str(e))

        raw_entries = info.get("entries") or []
        batch_entries = [
            BatchEntry(url=item["url"], title=item.get("title", ""))
            for item in raw_entries[:limit]
        ]
        return BatchResult(
            platform="gdrive",
            channel_title=info.get("title", "Google Drive Folder"),
            entries=batch_entries,
            total_found=len(raw_entries),
            total_queued=len(batch_entries),
        )


REGISTRY.register(GDriveExtractor())
