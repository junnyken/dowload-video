"""
Apify helpers shared by the China access layer (Douyin).

Every Apify call now goes through the access layer
(app/services/china_platforms): token pool managed in admin, budget guard,
one billable run per request. The legacy path that read the APIFY_TOKEN env
var — single-video extraction with a second async run on an empty result,
and the profile scraper — was removed in task #6055 (2026-10-07); production
never set that variable.

What remains:
  APIFY_BASE                 API base used by providers/apify_provider.py
  _parse_apify_video_item()  fallback field names for other actor shapes,
                             used by adapters/douyin.parse_actor_item
"""

import re
import sys
from typing import Dict, Any, Optional

# Apify API base
APIFY_BASE = "https://api.apify.com/v2"


def _safe_print(msg: str) -> None:
    """Print a message safely, replacing unencodable chars on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or "utf-8", errors="replace")
              .decode(sys.stdout.encoding or "utf-8", errors="replace"))


# ── Helper: Extract video ID from Douyin URL ─────────────────────────
def _extract_video_id(url: str) -> Optional[str]:
    """Extract numeric aweme_id from any Douyin URL form."""
    patterns = [
        r'/video/(\d{15,25})',
        r'/note/(\d{15,25})',
        r'item_ids=(\d{15,25})',
        r'aweme_id=(\d{15,25})',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _parse_apify_video_item(item: dict, quality: str = "video") -> Optional[Dict[str, Any]]:
    """
    Parse a single video item from Apify's Douyin scraper output.

    Apify Douyin scrapers typically return fields like:
      - title / desc / description
      - videoUrl / no_watermark_video_url / video_url / playAddr
      - coverUrl / thumbnail / cover
      - musicUrl / music_url
      - author / nickname
      - diggCount, shareCount, commentCount, playCount
    """
    if not item or not isinstance(item, dict):
        return None

    # ── Extract video URL (no-watermark preferred) ───────────────────
    direct_url = (
        item.get("no_watermark_video_url")
        or item.get("videoUrl")
        or item.get("video_url")
        or item.get("playAddr")
        or item.get("play_url")
        or item.get("videoPlayUrl")
        or ""
    )

    # Some actors nest video info
    if not direct_url:
        video_info = item.get("video", {})
        if isinstance(video_info, dict):
            play_addr = video_info.get("play_addr", {})
            if isinstance(play_addr, dict):
                url_list = play_addr.get("url_list", [])
                if url_list:
                    direct_url = url_list[0].replace("playwm", "play")
            if not direct_url:
                direct_url = video_info.get("playAddr", "") or video_info.get("downloadAddr", "")

    if not direct_url:
        _safe_print("[Apify] No video URL found in item")
        _safe_print(f"[Apify] Available keys: {list(item.keys())[:20]}")
        return None

    # ── Title ────────────────────────────────────────────────────────
    title = (
        item.get("title")
        or item.get("desc")
        or item.get("description")
        or item.get("text")
        or "Douyin Video"
    )

    # ── Thumbnail ────────────────────────────────────────────────────
    thumbnail = (
        item.get("coverUrl")
        or item.get("thumbnail")
        or item.get("cover")
        or item.get("originCover")
        or ""
    )
    if not thumbnail:
        cover = item.get("video", {}).get("cover", {})
        if isinstance(cover, dict):
            cover_urls = cover.get("url_list", [])
            thumbnail = cover_urls[0] if cover_urls else ""

    # ── Audio URL ────────────────────────────────────────────────────
    audio_url = (
        item.get("musicUrl")
        or item.get("music_url")
        or ""
    )
    if not audio_url:
        music = item.get("music", {})
        if isinstance(music, dict):
            play_url = music.get("play_url", {})
            if isinstance(play_url, dict):
                audio_urls = play_url.get("url_list", [])
                audio_url = audio_urls[0] if audio_urls else ""
            elif isinstance(play_url, str):
                audio_url = play_url

    # Switch to audio if MP3 quality requested
    if quality.startswith("mp3") and audio_url:
        direct_url = audio_url

    # ── File size ────────────────────────────────────────────────────
    file_size = item.get("videoSize", 0) or item.get("size", 0)
    file_size_mb = round(file_size / (1024 * 1024), 2) if file_size else 0

    _safe_print(f"[Apify] Success: {title[:60]}")
    return {
        "title": title,
        "thumbnail_url": thumbnail,
        "direct_mp4_url": direct_url,
        "audio_url": audio_url,
        "file_size_mb": file_size_mb,
        "quality": quality,
        "provider": "apify",
        "is_audio": quality.startswith("mp3"),
    }
