"""
Apify Service — Douyin Extraction via Apify Cloud Actors
==========================================================
Replaces yt-dlp and all legacy providers (ZenRows, TikWM, douyin.wtf,
ScraperAPI, SharePage, MobileAPI) for Douyin links.

Apify Actors run in cloud containers with residential proxies and
headless browsers, bypassing Douyin's JS VM anti-bot completely.

Supports:
  • Single video extraction  (no-watermark URL + metadata)
  • User profile scraping    (list all videos from a channel)

Actor used: natanielsantos/douyin-scraper  (public, free-tier compatible)
Fallback:   automation-lab/douyin-analytics-scraper
"""

import os
import re
import sys
import time
import httpx
import asyncio
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv

load_dotenv()

# ── Configuration ────────────────────────────────────────────────────
APIFY_TOKEN: str = os.getenv("APIFY_TOKEN", "")

# Primary Actor for single video extraction
# natanielsantos/douyin-scraper supports postUrls input
ACTOR_ID_VIDEO = "natanielsantos~douyin-scraper"

# Fallback / user-profile Actor
ACTOR_ID_PROFILE = "automation-lab~douyin-analytics-scraper"

# Apify API base
APIFY_BASE = "https://api.apify.com/v2"

# Timeouts
SYNC_RUN_TIMEOUT = 60        # seconds to wait for sync run
POLL_INTERVAL = 3             # seconds between status polls
MAX_POLL_DURATION = 120       # max seconds to wait for async run


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


# ── Helper: Resolve v.douyin.com short URLs ──────────────────────────
async def _resolve_short_url(url: str) -> str:
    """
    Resolve v.douyin.com short links via 302 redirect.
    Returns canonical URL or original if resolution fails.
    """
    if "v.douyin.com" not in url.lower():
        return url

    user_agents = [
        # WeChat UA — most lenient
        (
            "Mozilla/5.0 (Linux; Android 10; SM-G981B) "
            "AppleWebKit/537.36 Chrome/86.0.4240.198 "
            "Mobile MicroMessenger/8.0.2 WeChat/arm64"
        ),
        # Mobile Safari
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.5 Mobile/15E148 Safari/604.1"
        ),
    ]

    for ua in user_agents:
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=8.0) as client:
                resp = await client.get(url, headers={
                    "User-Agent": ua,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                })
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    vid_match = re.search(r'/video/(\d{15,25})', location)
                    if vid_match:
                        canonical = f"https://www.douyin.com/video/{vid_match.group(1)}"
                        _safe_print(f"[Apify] Resolved short URL -> {canonical}")
                        return canonical
                    # Return location even without video ID (e.g. user profile)
                    if location.startswith("http"):
                        return location
        except Exception:
            pass

    # Fallback: follow redirects
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            resp = await client.get(url, headers={"User-Agent": user_agents[0]})
            final = str(resp.url)
            if final != url:
                vid_match = re.search(r'/video/(\d{15,25})', final)
                if vid_match:
                    return f"https://www.douyin.com/video/{vid_match.group(1)}"
                return final
    except Exception:
        pass

    return url


# ═════════════════════════════════════════════════════════════════════
# SINGLE VIDEO EXTRACTION
# ═════════════════════════════════════════════════════════════════════

async def extract_douyin_apify(url: str, quality: str = "video") -> Dict[str, Any]:
    """
    Extract a single Douyin video via Apify Actor.

    Uses the synchronous run-sync-get-dataset-items endpoint
    so we get the result in a single HTTP call (blocks until done).

    Returns dict with: title, thumbnail_url, direct_mp4_url, file_size_mb, etc.

    Raises ValueError if extraction fails.
    """
    if not APIFY_TOKEN:
        raise ValueError("[Apify] APIFY_TOKEN not configured in .env")

    # Step 0: Resolve short URL
    original_url = url
    url = await _resolve_short_url(url)
    if url != original_url:
        _safe_print(f"[Apify] Unshortened: {original_url} -> {url}")

    _safe_print(f"[Apify] Extracting single video: {url}")

    # ── Strategy 1: Synchronous run (fast, single request) ───────────
    result = await _try_sync_run_video(url, quality)
    if result:
        result["original_url"] = original_url
        return result

    # ── Strategy 2: Async run with polling (more reliable) ───────────
    _safe_print("[Apify] Sync run failed, trying async run with polling...")
    result = await _try_async_run_video(url, quality)
    if result:
        result["original_url"] = original_url
        return result

    raise ValueError(
        "Không thể tải video Douyin qua Apify. "
        "Vui lòng kiểm tra lại link hoặc thử lại sau."
    )


async def _try_sync_run_video(url: str, quality: str) -> Optional[Dict[str, Any]]:
    """
    Run Actor synchronously and get dataset items in one call.
    Endpoint: POST /acts/{actorId}/run-sync-get-dataset-items
    """
    endpoint = f"{APIFY_BASE}/acts/{ACTOR_ID_VIDEO}/run-sync-get-dataset-items"

    input_body = {
        "postUrls": [url],
        "maxItems": 1,
    }

    headers = {
        "Authorization": f"Bearer {APIFY_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=SYNC_RUN_TIMEOUT + 10) as client:
            resp = await client.post(
                endpoint,
                json=input_body,
                headers=headers,
                params={"timeout": SYNC_RUN_TIMEOUT},
            )

            if resp.status_code == 408:
                _safe_print("[Apify] Sync run timed out (408)")
                return None

            if resp.status_code not in (200, 201):
                _safe_print(f"[Apify] Sync run failed: HTTP {resp.status_code}")
                _safe_print(f"[Apify] Response: {resp.text[:500]}")
                return None

            items = resp.json()
            if not items or not isinstance(items, list):
                _safe_print("[Apify] Sync run returned no items")
                return None

            return _parse_apify_video_item(items[0], quality)

    except httpx.TimeoutException:
        _safe_print("[Apify] Sync run HTTP timeout")
        return None
    except Exception as e:
        _safe_print(f"[Apify] Sync run error: {e}")
        return None


async def _try_async_run_video(url: str, quality: str) -> Optional[Dict[str, Any]]:
    """
    Start Actor run asynchronously, poll for completion, fetch dataset.
    """
    # Step 1: Start the run
    run_endpoint = f"{APIFY_BASE}/acts/{ACTOR_ID_VIDEO}/runs"

    input_body = {
        "postUrls": [url],
        "maxItems": 1,
    }

    headers = {
        "Authorization": f"Bearer {APIFY_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                run_endpoint,
                json=input_body,
                headers=headers,
            )

            if resp.status_code not in (200, 201):
                _safe_print(f"[Apify] Async run start failed: HTTP {resp.status_code}")
                return None

            run_data = resp.json().get("data", {})
            run_id = run_data.get("id")
            dataset_id = run_data.get("defaultDatasetId")

            if not run_id:
                _safe_print("[Apify] No run ID returned")
                return None

            _safe_print(f"[Apify] Run started: {run_id}")

        # Step 2: Poll for completion
        start_time = time.time()
        while time.time() - start_time < MAX_POLL_DURATION:
            await asyncio.sleep(POLL_INTERVAL)

            async with httpx.AsyncClient(timeout=15) as client:
                status_resp = await client.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    headers=headers,
                )

                if status_resp.status_code != 200:
                    continue

                status_data = status_resp.json().get("data", {})
                status = status_data.get("status", "")
                _safe_print(f"[Apify] Run status: {status}")

                if status == "SUCCEEDED":
                    dataset_id = dataset_id or status_data.get("defaultDatasetId")
                    break
                elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    _safe_print(f"[Apify] Run ended with status: {status}")
                    return None
        else:
            _safe_print("[Apify] Polling timed out")
            return None

        # Step 3: Fetch dataset items
        if not dataset_id:
            _safe_print("[Apify] No dataset ID")
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            items_resp = await client.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items",
                headers=headers,
            )

            if items_resp.status_code != 200:
                _safe_print(f"[Apify] Dataset fetch failed: HTTP {items_resp.status_code}")
                return None

            items = items_resp.json()
            if not items or not isinstance(items, list):
                _safe_print("[Apify] Dataset empty")
                return None

            return _parse_apify_video_item(items[0], quality)

    except Exception as e:
        _safe_print(f"[Apify] Async run error: {e}")
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


# ═════════════════════════════════════════════════════════════════════
# USER PROFILE / CHANNEL SCRAPING
# ═════════════════════════════════════════════════════════════════════

async def scrape_douyin_user_apify(
    user_url: str,
    max_videos: int = 20,
    min_views: int = 0,
) -> Dict[str, Any]:
    """
    Scrape all videos from a Douyin user profile via Apify.

    Apify Actors natively support user profile URLs and handle
    anti-bot, pagination, and JS rendering in the cloud.

    Returns dict matching downloader's channel format:
      { "channel_title": str, "entries": [...], "total_found": int, "total_queued": int }
    """
    if not APIFY_TOKEN:
        raise ValueError("[Apify] APIFY_TOKEN not configured in .env")

    _safe_print(f"[Apify] Scraping user profile: {user_url}")

    # Try primary actor first, then fallback
    for actor_id in [ACTOR_ID_VIDEO, ACTOR_ID_PROFILE]:
        _safe_print(f"[Apify] Trying actor: {actor_id}")
        result = await _run_profile_scrape(actor_id, user_url, max_videos, min_views)
        if result and result.get("total_queued", 0) > 0:
            return result

    raise ValueError(
        "Không thể quét kênh Douyin qua Apify. "
        "Thử dán trực tiếp link video thay vì link user profile."
    )


async def _run_profile_scrape(
    actor_id: str,
    user_url: str,
    max_videos: int,
    min_views: int,
) -> Optional[Dict[str, Any]]:
    """
    Run an Apify Actor for user profile scraping (async with polling).
    """
    run_endpoint = f"{APIFY_BASE}/acts/{actor_id}/runs"

    # Build input — different actors expect different formats
    input_body = {
        "postUrls": [user_url],
        "maxItems": max_videos,
    }
    # Some actors use 'startUrls' format
    if "analytics" in actor_id:
        input_body = {
            "startUrls": [{"url": user_url}],
            "maxItems": max_videos,
        }

    headers = {
        "Authorization": f"Bearer {APIFY_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        # Start run
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(run_endpoint, json=input_body, headers=headers)

            if resp.status_code not in (200, 201):
                _safe_print(f"[Apify:Profile] Run start failed: HTTP {resp.status_code}")
                return None

            run_data = resp.json().get("data", {})
            run_id = run_data.get("id")
            dataset_id = run_data.get("defaultDatasetId")

            if not run_id:
                return None

            _safe_print(f"[Apify:Profile] Run started: {run_id}")

        # Poll for completion
        start_time = time.time()
        while time.time() - start_time < MAX_POLL_DURATION:
            await asyncio.sleep(POLL_INTERVAL)

            async with httpx.AsyncClient(timeout=15) as client:
                status_resp = await client.get(
                    f"{APIFY_BASE}/actor-runs/{run_id}",
                    headers=headers,
                )
                if status_resp.status_code != 200:
                    continue

                status = status_resp.json().get("data", {}).get("status", "")
                _safe_print(f"[Apify:Profile] Run status: {status}")

                if status == "SUCCEEDED":
                    dataset_id = dataset_id or status_resp.json().get("data", {}).get("defaultDatasetId")
                    break
                elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    return None
        else:
            _safe_print("[Apify:Profile] Polling timed out")
            return None

        # Fetch dataset
        if not dataset_id:
            return None

        async with httpx.AsyncClient(timeout=30) as client:
            items_resp = await client.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items",
                headers=headers,
                params={"limit": max_videos},
            )

            if items_resp.status_code != 200:
                return None

            items = items_resp.json()
            if not items or not isinstance(items, list):
                return None

            return _parse_profile_items(items, max_videos, min_views)

    except Exception as e:
        _safe_print(f"[Apify:Profile] Error: {e}")
        return None


def _parse_profile_items(
    items: list,
    max_videos: int,
    min_views: int,
) -> Optional[Dict[str, Any]]:
    """
    Parse a list of video items from Apify dataset into channel format.
    """
    entries = []
    channel_title = "Douyin User"
    total_found = len(items)

    for item in items:
        if not isinstance(item, dict):
            continue

        if len(entries) >= max_videos:
            break

        # View count filter
        views = (
            item.get("playCount", 0)
            or item.get("play_count", 0)
            or item.get("diggCount", 0)
            or 0
        )
        if views < min_views:
            continue

        # Extract video URL
        video_id = (
            item.get("id")
            or item.get("aweme_id")
            or item.get("videoId")
            or ""
        )
        video_url = item.get("videoUrl") or item.get("url") or item.get("webVideoUrl") or ""

        if not video_url and video_id:
            video_url = f"https://www.douyin.com/video/{video_id}"

        if not video_url:
            continue

        # Title
        title = (
            item.get("title")
            or item.get("desc")
            or item.get("description")
            or "Video"
        )

        # Channel title from author info
        if channel_title == "Douyin User":
            author = item.get("author", {}) or item.get("authorMeta", {})
            if isinstance(author, dict):
                channel_title = author.get("nickname", "") or author.get("name", "") or "Douyin User"
            elif isinstance(item.get("authorName"), str):
                channel_title = item["authorName"]

        entries.append({
            "url": video_url,
            "title": title,
        })

    if not entries:
        return None

    _safe_print(f"[Apify:Profile] Parsed {len(entries)} videos for '{channel_title}'")
    return {
        "channel_title": channel_title,
        "entries": entries,
        "total_found": total_found,
        "total_queued": len(entries),
    }


# ── Sync wrappers for Celery / synchronous contexts ─────────────────

def extract_douyin_apify_sync(url: str, quality: str = "video") -> Dict[str, Any]:
    """Synchronous wrapper for extract_douyin_apify."""
    return asyncio.run(extract_douyin_apify(url, quality))


def scrape_douyin_user_apify_sync(
    user_url: str,
    max_videos: int = 20,
    min_views: int = 0,
) -> Dict[str, Any]:
    """Synchronous wrapper for scrape_douyin_user_apify."""
    return asyncio.run(scrape_douyin_user_apify(user_url, max_videos, min_views))


# ── Test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/U7FRoXWosnY/"

    sys.stdout.reconfigure(encoding='utf-8')
    print(f"Testing Apify Douyin extraction: {test_url}\n")

    result = asyncio.run(extract_douyin_apify(test_url))
    print(f"\n{'='*60}")
    print(f"Title:     {result.get('title')}")
    print(f"Thumbnail: {result.get('thumbnail_url', '')[:80]}")
    print(f"Video URL: {result.get('direct_mp4_url', '')[:120]}")
    print(f"Size:      {result.get('file_size_mb')} MB")
    print(f"Provider:  {result.get('provider')}")
