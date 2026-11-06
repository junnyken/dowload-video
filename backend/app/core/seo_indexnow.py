"""
SEO IndexNow & AI Search Discovery Service
==========================================
Automatically submits all 62 localized public pages (Vietnamese + English)
to IndexNow (Bing, ChatGPT Search / OAI-SearchBot, Yandex, Seznam, Naver, Yep)
on production deploy, and provides `/api/v1/seo/status` and `/api/v1/seo/ping`
for manual or automated re-indexing triggers.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import urlparse

from fastapi import APIRouter

router = APIRouter()

SITE_URL = (os.getenv("VIDGRAB_WEB_URL") or os.getenv("FRONTEND_URL") or "https://dowloadvideo.io.vn").split(",")[0].strip().rstrip("/")
INDEXNOW_KEY = (os.getenv("INDEXNOW_KEY") or "7b9e4f2a8c1d6e3b5a0f9c8d7e6f5a4b").strip()

# All 62 unique localized SEO landing pages (31 Vietnamese + 31 English)
SEO_PATHS: List[str] = [
    "/",
    "/en",
    "/platforms",
    "/en/platforms",
    "/extension",
    "/en/extension",
    "/download",
    "/en/download",
    "/pricing",
    "/en/pricing",
    "/faq",
    "/en/faq",
    "/phu-de",
    "/en/phu-de",
    "/api-docs",
    "/en/api-docs",
    "/privacy",
    "/en/privacy",
    "/terms",
    "/en/terms",
    "/takedown",
    "/en/takedown",
    "/tai-video-tiktok",
    "/en/tiktok-video-downloader",
    "/tai-video-facebook",
    "/en/facebook-video-downloader",
    "/tai-video-threads",
    "/en/threads-video-downloader",
    "/tai-video-douyin",
    "/en/douyin-video-downloader",
    "/tai-video-instagram",
    "/en/instagram-video-downloader",
    "/tai-video-youtube",
    "/en/youtube-video-downloader",
    "/tai-video-twitter-x",
    "/en/twitter-video-downloader",
    "/tai-video-pinterest",
    "/en/pinterest-video-downloader",
    "/tai-nhac-spotify",
    "/en/spotify-mp3-downloader",
    "/tai-nhac-soundcloud",
    "/en/soundcloud-mp3-downloader",
    "/tai-video-reddit",
    "/en/reddit-video-downloader",
    "/tai-video-bilibili",
    "/en/bilibili-video-downloader",
    "/tai-video-xiaohongshu",
    "/en/xiaohongshu-video-downloader",
    "/tai-video-lemon8",
    "/en/lemon8-video-downloader",
    "/tai-video-snapchat",
    "/en/snapchat-video-downloader",
    "/tai-video-vk",
    "/en/vk-video-downloader",
    "/tai-video-twitch",
    "/en/twitch-clip-vod-downloader",
    "/tai-video-rumble",
    "/en/rumble-video-downloader",
    "/tai-video-odysee",
    "/en/odysee-video-downloader",
    "/tai-video-dailymotion",
    "/en/dailymotion-video-downloader",
]

INDEXNOW_ENDPOINTS = [
    ("indexnow_org", "https://api.indexnow.org/indexnow"),
    ("bing", "https://www.bing.com/indexnow"),
    ("yandex", "https://yandex.com/indexnow"),
]

_REDIS_LAST_PING_KEY = "vidgrab:seo:indexnow_last_ping"
_REDIS_LAST_RESULT_KEY = "vidgrab:seo:indexnow_last_result"


def get_seo_urls(base_url: str | None = None) -> List[str]:
    base = (base_url or SITE_URL).rstrip("/")
    return [f"{base}{p}" for p in SEO_PATHS]


def ping_indexnow(force: bool = False) -> Dict[str, Any]:
    """Submit all 62 localized URLs to IndexNow endpoints.
    Uses Redis 12-hour deduplication unless `force=True`."""
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
    except Exception:
        r = None

    if not force and r is not None:
        try:
            cached = r.get(_REDIS_LAST_RESULT_KEY)
            last_ts = r.get(_REDIS_LAST_PING_KEY)
            if cached and last_ts:
                data = json.loads(cached if isinstance(cached, str) else cached.decode())
                data["skipped_dedup"] = True
                return data
        except Exception:
            pass

    parsed = urlparse(SITE_URL)
    host = parsed.netloc or "dowloadvideo.io.vn"
    key_location = f"{SITE_URL}/{INDEXNOW_KEY}.txt"
    url_list = get_seo_urls(SITE_URL)

    payload = json.dumps({
        "host": host,
        "key": INDEXNOW_KEY,
        "keyLocation": key_location,
        "urlList": url_list,
    }).encode("utf-8")

    results: Dict[str, Any] = {}
    for name, endpoint in INDEXNOW_ENDPOINTS:
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "VidGrab-SEO-IndexNow/1.0 (+https://dowloadvideo.io.vn)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                results[name] = {
                    "ok": resp.status in (200, 202),
                    "status": resp.status,
                }
        except urllib.error.HTTPError as e:
            results[name] = {
                "ok": e.code in (200, 202),
                "status": e.code,
                "error": str(e.reason),
            }
        except Exception as e:
            results[name] = {
                "ok": False,
                "status": 0,
                "error": str(e),
            }

    now_iso = datetime.now(timezone.utc).isoformat()
    summary = {
        "site": SITE_URL,
        "host": host,
        "indexnow_key": INDEXNOW_KEY,
        "key_location": key_location,
        "sitemap_url": f"{SITE_URL}/sitemap.xml",
        "llms_txt_url": f"{SITE_URL}/llms.txt",
        "total_urls": len(url_list),
        "pinged_at": now_iso,
        "endpoints": results,
    }

    if r is not None:
        try:
            r.set(_REDIS_LAST_PING_KEY, now_iso, ex=43200)  # 12h dedup
            r.set(_REDIS_LAST_RESULT_KEY, json.dumps(summary), ex=86400 * 7)
        except Exception:
            pass

    return summary


@router.get("/seo/status")
def seo_status() -> Dict[str, Any]:
    """Inspect current SEO & IndexNow configuration and last ping result."""
    last_result = None
    try:
        from app.core.redis_client import get_redis
        raw = get_redis().get(_REDIS_LAST_RESULT_KEY)
        if raw:
            last_result = json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception:
        pass

    return {
        "site": SITE_URL,
        "indexnow_key": INDEXNOW_KEY,
        "key_location": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
        "sitemap_url": f"{SITE_URL}/sitemap.xml",
        "llms_txt_url": f"{SITE_URL}/llms.txt",
        "llms_full_txt_url": f"{SITE_URL}/llms-full.txt",
        "total_urls": len(SEO_PATHS),
        "urls": get_seo_urls(SITE_URL),
        "last_ping": last_result,
    }


@router.post("/seo/ping")
def trigger_seo_ping(force: bool = True) -> Dict[str, Any]:
    """Manually trigger an immediate IndexNow push of all 62 localized URLs."""
    return ping_indexnow(force=force)
