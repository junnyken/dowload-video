"""
Xigua / Toutiao Video Extractor — Phase B3
==========================================
Supports:
  - `ixigua.com/<id>` / `ixigua.com/i<id>` / `m.ixigua.com/video/<id>`
  - `toutiao.com/video/<id>` / `toutiao.com/a<id>`
  - `v.ixigua.com/<code/>` (short share link)

Extraction strategy:
  1. Primary: `yt-dlp` extractor.
  2. Fallback A: Mobile Toutiao/Xigua JSON API (`https://m.toutiao.com/i<id>/info/`).
  3. Fallback B: HTML SSR state (`window._SSR_HYDRATED_DATA` / `RENDER_DATA`),
     decoding base64 `main_url` entries inside `video_list` / `videoResource`.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import unquote

import httpx

from app.services.base_extractor import (
    BaseExtractor,
    ExtractResult,
    FormatInfo,
)
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

_PLATFORM = "xigua"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class XiguaUrlInfo:
    kind: str                  # "video" | "short"
    video_id: Optional[str] = None
    source_host: str = "ixigua"


_IXIGUA_ID_RE = re.compile(
    r"(?:www\.|m\.)?ixigua\.com/(?:video/|i)?(\d{10,25})(?:[/?#]|$)",
    re.IGNORECASE,
)
_TOUTIAO_ID_RE = re.compile(
    r"(?:www\.|m\.)?toutiao\.com/(?:video/|a|i)(\d{10,25})(?:[/?#]|$)",
    re.IGNORECASE,
)
_SHORT_IXIGUA_RE = re.compile(
    r"v\.ixigua\.com/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def parse_xigua_url(url: str) -> Optional[XiguaUrlInfo]:
    """Parse Xigua / Toutiao video URLs."""
    u = (url or "").strip()
    if not u:
        return None

    m = _IXIGUA_ID_RE.search(u)
    if m:
        return XiguaUrlInfo(kind="video", video_id=m.group(1), source_host="ixigua")

    m = _TOUTIAO_ID_RE.search(u)
    if m:
        return XiguaUrlInfo(kind="video", video_id=m.group(1), source_host="toutiao")

    m = _SHORT_IXIGUA_RE.search(u)
    if m:
        return XiguaUrlInfo(kind="short", video_id=m.group(1), source_host="ixigua")

    return None


def is_xigua_url(url: str) -> bool:
    return parse_xigua_url(url) is not None


# ── Step 1: yt-dlp primary attempt ─────────────────────────────────────────

def _try_ytdlp_xigua(url: str) -> Optional[dict]:
    """Try extracting Xigua/Toutiao via yt-dlp. Returns normalized dict or None."""
    try:
        import yt_dlp
        proxy = os.getenv("XIGUA_PROXY", "") or os.getenv("CHINA_PROXY", "")
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
        if not info.get("url") and not info.get("formats"):
            return None
        formats = [
            {
                "quality": str(f.get("format_note") or f.get("height") or "best"),
                "codec": f.get("vcodec", "h264"),
                "bitrate": int(f.get("tbr", 0) or 0),
                "url": f.get("url", ""),
                "ext": f.get("ext", "mp4"),
            }
            for f in (info.get("formats") or [])
            if f.get("url")
        ]
        direct_url = info.get("url") or (formats[-1]["url"] if formats else "")
        if not direct_url:
            return None
        if not formats:
            formats = [{"quality": "best", "codec": "h264", "bitrate": 0, "url": direct_url, "ext": "mp4"}]
        dur_sec = int(float(info.get("duration") or 0))
        return {
            "platform": _PLATFORM,
            "source_type": "video",
            "title": info.get("title") or "Xigua Video",
            "author": info.get("uploader") or "Xigua",
            "thumbnail": info.get("thumbnail") or "",
            "duration": dur_sec,
            "duration_ms": dur_sec * 1000,
            "direct_url": direct_url,
            "formats": formats,
            "extraction_method": "ytdlp",
        }
    except Exception as exc:
        logger.debug("yt-dlp Xigua attempt failed, falling back: %s", exc)
        return None


# ── Step 2: Mobile JSON & SSR State Fallback ───────────────────────────────

def _decode_main_url(raw_val: str) -> str:
    """Decode base64-encoded `main_url` from Xigua video_list if needed."""
    val = (raw_val or "").strip()
    if not val:
        return ""
    if val.startswith(("http://", "https://")):
        return val
    if val.startswith("//"):
        return "https:" + val
    try:
        decoded = base64.b64decode(val).decode("utf-8")
        if decoded.startswith(("http://", "https://")):
            return decoded
        if decoded.startswith("//"):
            return "https:" + decoded
    except Exception:
        pass
    return ""


def parse_xigua_mobile_json(payload: dict) -> Optional[dict]:
    """
    Parse Toutiao/Xigua mobile info JSON (`https://m.toutiao.com/i<id>/info/`)
    or video play info JSON.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None

    video_list = (
        data.get("video_list")
        or (data.get("video_play_info") or {}).get("video_list")
        or (data.get("videoResource") or {}).get("normal", {}).get("video_list")
        or {}
    )
    formats: list[dict] = []
    if isinstance(video_list, dict):
        for k, v in video_list.items():
            if not isinstance(v, dict):
                continue
            u = _decode_main_url(str(v.get("main_url") or v.get("backup_url_1") or ""))
            if u:
                defn = str(v.get("definition") or k or "best")
                formats.append({
                    "quality": defn,
                    "codec": str(v.get("vcodec") or "h264"),
                    "bitrate": int(v.get("bitrate") or 0),
                    "url": u,
                    "ext": "mp4",
                })

    direct_url = _decode_main_url(str(data.get("video_url") or data.get("play_url") or ""))
    if direct_url and not formats:
        formats.append({
            "quality": "best",
            "codec": "h264",
            "bitrate": 0,
            "url": direct_url,
            "ext": "mp4",
        })

    if not formats:
        return None

    # Sort highest bitrate / quality first when bitrate is available
    formats.sort(key=lambda f: f.get("bitrate", 0), reverse=True)
    title = str(data.get("title") or "Xigua Video")[:120]
    author = str(data.get("detail_source") or data.get("creator_uid") or (data.get("user_info") or {}).get("name") or "Xigua")
    thumb = str(data.get("poster_url") or data.get("cover_url") or "")
    dur_sec = int(float(data.get("video_duration") or data.get("duration") or 0))

    return {
        "platform": _PLATFORM,
        "source_type": "video",
        "title": title,
        "author": author,
        "thumbnail": thumb,
        "duration": dur_sec,
        "duration_ms": dur_sec * 1000,
        "direct_url": formats[0]["url"],
        "formats": formats,
        "extraction_method": "xigua_mobile_json",
    }


_SSR_RE = re.compile(
    r"window\._SSR_HYDRATED_DATA\s*=\s*(\{.*?\})\s*</script>",
    re.IGNORECASE | re.DOTALL,
)
_RENDER_DATA_RE = re.compile(
    r'<script[^>]+id=["\']RENDER_DATA["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def parse_xigua_ssr_html(html: str) -> Optional[dict]:
    """
    Extract video metadata & base64 `main_url` streams from Xigua/Toutiao SSR HTML
    (`window._SSR_HYDRATED_DATA` or `RENDER_DATA`).
    """
    state: Optional[dict] = None
    m_ssr = _SSR_RE.search(html or "")
    if m_ssr:
        raw_json = re.sub(r":\s*undefined\b", ": null", m_ssr.group(1))
        try:
            state = json.loads(raw_json)
        except Exception:
            state = None

    if state is None:
        m_rd = _RENDER_DATA_RE.search(html or "")
        if m_rd:
            try:
                state = json.loads(unquote(m_rd.group(1).strip()))
            except Exception:
                state = None

    if not isinstance(state, dict):
        return None

    # Navigate common Xigua/Toutiao SSR structures
    teleplay = (
        (state.get("anyVideo") or {}).get("gidInformation", {}).get("packerData", {}).get("video")
        or (state.get("anyVideo") or {}).get("gidInformation", {}).get("packerData")
        or state.get("data")
        or state
    )
    if isinstance(teleplay, dict):
        res = parse_xigua_mobile_json({"data": teleplay})
        if res:
            res["extraction_method"] = "xigua_ssr"
            return res
    return None


# ── Main Extraction Function ───────────────────────────────────────────────

def extract_xigua(
    url: str,
    *,
    client: Optional[httpx.Client] = None,
    skip_ytdlp: bool = False,
) -> dict:
    """
    Extract video from Xigua (`ixigua.com`, `v.ixigua.com`) or Toutiao (`toutiao.com/video/`).
    1. Try yt-dlp first.
    2. Fallback to mobile JSON (`m.toutiao.com/i<id>/info/`) and SSR HTML state extraction.
    """
    parsed = parse_xigua_url(url)
    if not parsed:
        return {
            "error_code": "unsupported_url",
            "error_message": "URL Xigua / Toutiao không hợp lệ.",
        }

    if not skip_ytdlp:
        ydl_res = _try_ytdlp_xigua(url)
        if ydl_res:
            return ydl_res

    own_client = client is None
    if own_client:
        client = httpx.Client(
            headers={"User-Agent": _UA, "Referer": "https://www.ixigua.com/"},
            follow_redirects=True,
            timeout=15.0,
        )

    try:
        video_id = parsed.video_id

        # Resolve short share link `v.ixigua.com/...` if needed
        if parsed.kind == "short":
            r_short = client.get(url)
            final_url = str(r_short.url)
            resolved = parse_xigua_url(final_url)
            if resolved and resolved.video_id and resolved.kind == "video":
                video_id = resolved.video_id
            # Also try SSR parsing directly on the response HTML
            ssr_direct = parse_xigua_ssr_html(r_short.text)
            if ssr_direct:
                return ssr_direct

        # Fallback A: Mobile info JSON endpoint
        if video_id and video_id.isdigit():
            info_url = f"https://m.toutiao.com/i{video_id}/info/"
            try:
                r_info = client.get(info_url)
                if r_info.status_code == 200 and "json" in (r_info.headers.get("content-type") or "").lower():
                    mob_res = parse_xigua_mobile_json(r_info.json())
                    if mob_res:
                        return mob_res
                elif r_info.status_code == 200:
                    mob_res = parse_xigua_mobile_json(r_info.json())
                    if mob_res:
                        return mob_res
            except Exception:
                pass

        # Fallback B: Fetch page HTML and parse SSR state
        page_url = f"https://www.ixigua.com/{video_id}" if (video_id and video_id.isdigit()) else url
        r_page = client.get(page_url, headers={"User-Agent": _UA, "Cookie": "ttwid=1%7Cguest_fallback"})
        if r_page.status_code == 200:
            ssr_res = parse_xigua_ssr_html(r_page.text)
            if ssr_res:
                return ssr_res

        return {
            "error_code": "no_media_found",
            "error_message": "Không tìm thấy luồng video Xigua / Toutiao.",
        }
    except Exception as exc:
        return {
            "error_code": "processing_failed",
            "error_message": f"Lỗi trích xuất Xigua / Toutiao: {exc}",
        }
    finally:
        if own_client and client is not None:
            client.close()


# ── BaseExtractor Wrapper ──────────────────────────────────────────────────

class XiguaExtractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({"video"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [
        r"(?:www\.|m\.)?ixigua\.com/",
        r"v\.ixigua\.com/",
        r"(?:www\.|m\.)?toutiao\.com/(?:video/|a|i)\d+",
    ]
    _test_fixtures = [
        "https://www.ixigua.com/7123456789012345678",
        "https://www.toutiao.com/video/7123456789012345678/",
        "https://v.ixigua.com/iAbCdEf/",
    ]

    def detect_url(self, url: str) -> bool:
        return is_xigua_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        client = kwargs.get("client")
        skip_ytdlp = bool(kwargs.get("skip_ytdlp", False))
        raw = extract_xigua(url, client=client, skip_ytdlp=skip_ytdlp)
        if raw.get("error_code"):
            return self._make_error_result(raw["error_code"], raw.get("error_message", ""))

        formats = [
            FormatInfo(
                quality=str(f.get("quality", "best")),
                codec=f.get("codec", "h264"),
                bitrate=int(f.get("bitrate", 0) or 0),
                url=f.get("url", ""),
                ext=f.get("ext", "mp4"),
            )
            for f in raw.get("formats", [])
        ]
        return ExtractResult(
            platform=self.platform_name,
            source_type="video",
            title=raw.get("title", ""),
            author=raw.get("author", ""),
            thumbnail=raw.get("thumbnail", ""),
            duration_ms=raw.get("duration_ms", 0),
            formats=formats,
            direct_url=raw.get("direct_url", ""),
            extraction_method=raw.get("extraction_method", "ytdlp"),
        )


try:
    REGISTRY.register(XiguaExtractor())
except Exception as _e:
    logger.warning("Could not register XiguaExtractor: %s", _e)
