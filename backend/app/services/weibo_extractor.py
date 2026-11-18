"""
Weibo Video Extractor — Phase B2
================================
Supports:
  - `weibo.com/<uid>/<bid>`
  - `weibo.com/tv/show/<fid>:<id>`
  - `m.weibo.cn/status/<id>` / `m.weibo.cn/detail/<id>`
  - `video.weibo.com/show?fid=<fid>:<id>`

Extraction strategy:
  1. Mobile JSON endpoint: `https://m.weibo.cn/statuses/show?id=<id>`
     (extracts `data.page_info.media_info.urls` / `mp4_720p_mp4` / `stream_url_hd`).
  2. Weibo TV Component API: `https://weibo.com/tv/api/component?page=/tv/show/<fid>`
     with form `data={"Component_Play_Playinfo":{"oid":"<fid>"}}` and Visitor `SUB`
     cookie fallback (cached in Redis/memory, minted via Sina Visitor `genvisitor`
     similar to `douyin_visitor.py`).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.services.base_extractor import (
    BaseExtractor,
    ExtractResult,
    FormatInfo,
)
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

_PLATFORM = "weibo"

_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_VISITOR_REDIS_KEY = "weibo:visitor:sub_cookie"
_VISITOR_TTL_SEC = 12 * 3600
_MEMORY_VISITOR_SUB: tuple[str, float] = ("", 0.0)


@dataclass
class WeiboUrlInfo:
    kind: str                  # "status" | "tv"
    status_id: Optional[str] = None
    fid: Optional[str] = None  # e.g. "1034:4912345678901234"
    uid: Optional[str] = None


# ── URL Patterns ───────────────────────────────────────────────────────────

# weibo.com/tv/show/1034:4912345678901234
_TV_SHOW_RE = re.compile(
    r"(?:www\.)?weibo\.com/tv/show/(\d+:[A-Za-z0-9]+)",
    re.IGNORECASE,
)

# video.weibo.com/show?fid=1034:4912345678901234
_VIDEO_FID_RE = re.compile(
    r"video\.weibo\.com/show\?[^#]*fid=(\d+:[A-Za-z0-9]+)",
    re.IGNORECASE,
)

# m.weibo.cn/status/<id> or m.weibo.cn/detail/<id> or m.weibo.cn/statuses/show?id=<id>
_MOBILE_STATUS_RE = re.compile(
    r"m\.weibo\.cn/(?:status|detail)/([A-Za-z0-9]+)",
    re.IGNORECASE,
)

# weibo.com/<uid>/<bid>
_WEB_POST_RE = re.compile(
    r"(?:www\.)?weibo\.(?:com|cn)/(\d{5,15})/([A-Za-z0-9]{6,24})(?:[/?#]|$)",
    re.IGNORECASE,
)


def parse_weibo_url(url: str) -> Optional[WeiboUrlInfo]:
    """Parse supported Weibo video URLs into WeiboUrlInfo."""
    u = (url or "").strip()
    if not u:
        return None

    m = _TV_SHOW_RE.search(u)
    if m:
        return WeiboUrlInfo(kind="tv", fid=m.group(1))

    m = _VIDEO_FID_RE.search(u)
    if m:
        return WeiboUrlInfo(kind="tv", fid=m.group(1))

    # Also check query param fid if on video.weibo.com
    if "video.weibo.com" in u.lower():
        qs = parse_qs(urlparse(u).query)
        fid_vals = qs.get("fid") or []
        if fid_vals and ":" in fid_vals[0]:
            return WeiboUrlInfo(kind="tv", fid=fid_vals[0])

    m = _MOBILE_STATUS_RE.search(u)
    if m:
        return WeiboUrlInfo(kind="status", status_id=m.group(1))

    m = _WEB_POST_RE.search(u)
    if m:
        return WeiboUrlInfo(kind="status", uid=m.group(1), status_id=m.group(2))

    return None


def is_weibo_url(url: str) -> bool:
    return parse_weibo_url(url) is not None


# ── Visitor SUB Cookie Fallback (douyin_visitor style) ─────────────────────

def get_cached_visitor_sub() -> Optional[str]:
    global _MEMORY_VISITOR_SUB
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r is not None:
            raw = r.get(_VISITOR_REDIS_KEY)
            if raw:
                return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    except Exception:
        pass
    sub, exp = _MEMORY_VISITOR_SUB
    if sub and time.time() < exp:
        return sub
    return None


def store_visitor_sub(sub_cookie: str) -> None:
    global _MEMORY_VISITOR_SUB
    _MEMORY_VISITOR_SUB = (sub_cookie, time.time() + _VISITOR_TTL_SEC)
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r is not None:
            r.set(_VISITOR_REDIS_KEY, sub_cookie, ex=_VISITOR_TTL_SEC)
    except Exception:
        pass


def mint_weibo_visitor_sub(client: Optional[httpx.Client] = None) -> str:
    """
    Obtain an anonymous Weibo visitor `SUB` cookie from Sina Visitor System
    (`passport.weibo.com/visitor/genvisitor` -> `visitor?a=incarnate`).
    Falls back to a deterministic guest token if upstream is unreachable.
    """
    cached = get_cached_visitor_sub()
    if cached:
        return cached

    own_client = client is None
    if own_client:
        client = httpx.Client(headers={"User-Agent": _DESKTOP_UA}, timeout=10.0, follow_redirects=True)
    try:
        r1 = client.post(
            "https://passport.weibo.com/visitor/genvisitor",
            data={"cb": "gen_callback", "fp": '{"os":"1","browser":"Chrome124,0,0,0"}'},
        )
        m_tid = re.search(r'"tid"\s*:\s*"([^"]+)"', r1.text)
        if m_tid:
            tid = m_tid.group(1)
            r2 = client.get(
                "https://passport.weibo.com/visitor/visitor",
                params={"a": "incarnate", "t": tid, "w": "2", "c": "095", "cb": "cross_domain", "from": "weibo"},
            )
            m_sub = re.search(r'"sub"\s*:\s*"([^"]+)"', r2.text)
            if m_sub:
                sub = m_sub.group(1)
                store_visitor_sub(sub)
                return sub
    except Exception as exc:
        logger.debug("Weibo visitor mint fallback: %s", exc)
    finally:
        if own_client and client is not None:
            client.close()

    fallback_sub = "_2AkMR_visitor_fallback"
    store_visitor_sub(fallback_sub)
    return fallback_sub


# ── Mobile Status & Weibo TV API Parsers ───────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def _normalize_media_url(u: str) -> str:
    if not u:
        return ""
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    return u


def parse_weibo_mobile_json(payload: dict) -> Optional[dict]:
    """
    Parse response from `https://m.weibo.cn/statuses/show?id=<id>`.
    Returns normalized video dict or None if no video found.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None

    page_info = data.get("page_info") or {}
    media_info = page_info.get("media_info") or {}
    urls_map = page_info.get("urls") or media_info.get("urls") or {}

    formats: list[dict] = []
    seen_urls: set[str] = set()

    if isinstance(urls_map, dict):
        for label, raw_url in urls_map.items():
            u = _normalize_media_url(str(raw_url or ""))
            if u and u not in seen_urls:
                seen_urls.add(u)
                formats.append({
                    "quality": str(label),
                    "codec": "h264",
                    "bitrate": 0,
                    "url": u,
                    "ext": "mp4",
                })

    for key, q_label in (
        ("mp4_720p_mp4", "720p"),
        ("mp4_hd_url", "hd"),
        ("stream_url_hd", "hd"),
        ("mp4_sd_url", "sd"),
        ("stream_url", "sd"),
    ):
        u = _normalize_media_url(str(media_info.get(key) or ""))
        if u and u not in seen_urls:
            seen_urls.add(u)
            formats.append({
                "quality": q_label,
                "codec": "h264",
                "bitrate": 0,
                "url": u,
                "ext": "mp4",
            })

    if not formats:
        return None

    user_info = data.get("user") or {}
    author = user_info.get("screen_name") or media_info.get("author_name") or "Weibo"
    raw_title = (
        page_info.get("content2")
        or page_info.get("page_title")
        or _strip_tags(data.get("text") or "")
        or "Weibo Video"
    )
    title = _strip_tags(str(raw_title)).split("\n")[0][:120] or "Weibo Video"
    thumb = _normalize_media_url(
        str((page_info.get("page_pic") or {}).get("url") if isinstance(page_info.get("page_pic"), dict) else (page_info.get("page_pic") or ""))
    )
    duration_sec = int(float(media_info.get("duration") or page_info.get("media_info", {}).get("duration") or 0))

    return {
        "platform": _PLATFORM,
        "source_type": "video",
        "title": title,
        "author": str(author),
        "thumbnail": thumb,
        "duration": duration_sec,
        "duration_ms": duration_sec * 1000,
        "direct_url": formats[0]["url"],
        "formats": formats,
        "extraction_method": "weibo_mobile_api",
    }


def parse_weibo_tv_json(payload: dict) -> Optional[dict]:
    """
    Parse response from `https://weibo.com/tv/api/component?page=/tv/show/<fid>`.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    playinfo = data.get("Component_Play_Playinfo")
    if not isinstance(playinfo, dict):
        return None

    urls_map = playinfo.get("urls") or {}
    formats: list[dict] = []
    if isinstance(urls_map, dict):
        for label, raw_url in urls_map.items():
            u = _normalize_media_url(str(raw_url or ""))
            if u:
                formats.append({
                    "quality": str(label),
                    "codec": "h264",
                    "bitrate": 0,
                    "url": u,
                    "ext": "mp4",
                })

    stream_url = _normalize_media_url(str(playinfo.get("stream_url") or ""))
    if stream_url and not formats:
        formats.append({
            "quality": "best",
            "codec": "h264",
            "bitrate": 0,
            "url": stream_url,
            "ext": "mp4",
        })

    if not formats:
        return None

    title = _strip_tags(str(playinfo.get("title") or playinfo.get("text") or "Weibo TV Video"))[:120]
    author = str(playinfo.get("author") or playinfo.get("user", {}).get("screen_name") or "Weibo")
    thumb = _normalize_media_url(str(playinfo.get("cover_image") or ""))
    duration_sec = int(float(playinfo.get("duration") or 0))

    return {
        "platform": _PLATFORM,
        "source_type": "video",
        "title": title,
        "author": author,
        "thumbnail": thumb,
        "duration": duration_sec,
        "duration_ms": duration_sec * 1000,
        "direct_url": formats[0]["url"],
        "formats": formats,
        "extraction_method": "weibo_tv_api",
    }


# ── Core Extraction ────────────────────────────────────────────────────────

def extract_weibo(
    url: str,
    *,
    client: Optional[httpx.Client] = None,
) -> dict:
    """
    Extract Weibo video metadata and direct MP4 URLs.
    Uses mobile JSON API for status/bid links and Weibo TV API for fid links,
    with automatic Visitor SUB cookie fallback.
    """
    info = parse_weibo_url(url)
    if not info:
        return {
            "error_code": "unsupported_url",
            "error_message": "URL Weibo không hợp lệ.",
        }

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=15.0, follow_redirects=True)

    try:
        sub_cookie = mint_weibo_visitor_sub(client)

        # 1. Status / bid path -> Mobile JSON API
        if info.kind == "status" and info.status_id:
            headers = {
                "User-Agent": _MOBILE_UA,
                "Referer": f"https://m.weibo.cn/status/{info.status_id}",
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
                "MWeibo-Pwa": "1",
                "Cookie": f"SUB={sub_cookie}",
            }
            resp = client.get(
                "https://m.weibo.cn/statuses/show",
                params={"id": info.status_id},
                headers=headers,
            )
            if resp.status_code == 200:
                try:
                    parsed = parse_weibo_mobile_json(resp.json())
                    if parsed:
                        return parsed
                except Exception:
                    pass

        # 2. TV / fid path -> Weibo TV Component API
        fid = info.fid
        if fid:
            tv_headers = {
                "User-Agent": _DESKTOP_UA,
                "Referer": f"https://weibo.com/tv/show/{fid}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json, text/plain, */*",
                "Cookie": f"SUB={sub_cookie}",
            }
            form_payload = json.dumps({"Component_Play_Playinfo": {"oid": fid}})
            resp_tv = client.post(
                "https://weibo.com/tv/api/component",
                params={"page": f"/tv/show/{fid}"},
                data={"data": form_payload},
                headers=tv_headers,
            )
            if resp_tv.status_code == 200:
                try:
                    parsed_tv = parse_weibo_tv_json(resp_tv.json())
                    if parsed_tv:
                        return parsed_tv
                except Exception:
                    pass

        return {
            "error_code": "no_media_found",
            "error_message": "Không tìm thấy video trong bài đăng Weibo này.",
        }
    except Exception as exc:
        return {
            "error_code": "processing_failed",
            "error_message": f"Không thể trích xuất Weibo: {exc}",
        }
    finally:
        if own_client and client is not None:
            client.close()


# ── BaseExtractor Wrapper ──────────────────────────────────────────────────

class WeiboExtractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({"video"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [
        r"(?:www\.)?weibo\.(?:com|cn)/",
        r"m\.weibo\.cn/",
        r"video\.weibo\.com/",
    ]
    _test_fixtures = [
        "https://weibo.com/1234567890/NabcDef12",
        "https://weibo.com/tv/show/1034:4912345678901234",
        "https://m.weibo.cn/status/4912345678901234",
        "https://video.weibo.com/show?fid=1034:4912345678901234",
    ]

    def detect_url(self, url: str) -> bool:
        return is_weibo_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        client = kwargs.get("client")
        raw = extract_weibo(url, client=client)
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
            extraction_method=raw.get("extraction_method", "weibo_mobile_api"),
        )


try:
    REGISTRY.register(WeiboExtractor())
except Exception as _e:
    logger.warning("Could not register WeiboExtractor: %s", _e)
