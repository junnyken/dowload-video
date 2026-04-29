"""
Douyin Video Extractor — Multi-Provider API Pipeline
======================================================
Dedicated extractor for Douyin (Chinese TikTok) videos.
yt-dlp cannot handle Douyin's anti-bot (JS VM + captcha),
so we bypass it entirely using direct page parsing.

Provider waterfall (ordered by reliability):
  1. iesdouyin Share Page — parse _ROUTER_DATA SSR JSON (free, no auth)
  2. TikWM API — free, no auth, GET method (backup)
  3. ScraperAPI + SSR parse — last resort (needs API key)

Usage:
  from app.services.douyin_extractor import extract_douyin_video
  result = await extract_douyin_video("https://v.douyin.com/xxxxx/")
"""

import os
import re
import sys
import json
import httpx
import asyncio
from typing import Dict, Any, Optional
from urllib.parse import unquote, quote

from dotenv import load_dotenv

load_dotenv()


def _safe_print(msg: str) -> None:
    """Print a message safely, replacing unencodable chars on Windows."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or "utf-8", errors="replace")
              .decode(sys.stdout.encoding or "utf-8", errors="replace"))


# ── Helper: Resolve v.douyin.com short URLs ──────────────────────────

async def _resolve_short_url(url: str) -> str:
    """Resolve v.douyin.com short links via 302 redirect."""
    if "v.douyin.com" not in url.lower():
        return url

    user_agents = [
        (
            "Mozilla/5.0 (Linux; Android 10; SM-G981B Build/QP1A.190711.020; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/86.0.4240.198 "
            "Mobile Safari/537.36 MicroMessenger/8.0.2.1860(0x2800023B) WeChat/arm64 "
            "Weixin NetType/WIFI Language/zh_CN ABI/arm64"
        ),
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
                        _safe_print(f"[DouyinExtractor] Resolved -> {canonical}")
                        return canonical
                    if location.startswith("http"):
                        return location
        except Exception as e:
            _safe_print(f"[DouyinExtractor] Resolve error: {e}")

    # Fallback: follow all redirects
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


def _extract_video_id(url: str) -> Optional[str]:
    """Extract the numeric aweme_id from a Douyin URL."""
    patterns = [
        r'/video/(\d{15,25})',
        r'/note/(\d{15,25})',
        r'item_ids=(\d{15,25})',
        r'aweme_id=(\d{15,25})',
        r'modal_id=(\d{15,25})',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


# ═════════════════════════════════════════════════════════════════════
# PROVIDER 1: iesdouyin Share Page (Primary — free, no auth)
# ═════════════════════════════════════════════════════════════════════

async def _try_iesdouyin_share(video_id: str, quality: str = "video") -> Optional[Dict[str, Any]]:
    """
    Fetch the iesdouyin.com share page and parse _ROUTER_DATA for video info.
    This is a server-side rendered page that embeds video metadata as JSON.
    
    The _ROUTER_DATA contains videoInfoRes -> item_list -> video -> play_addr.
    We replace /playwm/ with /play/ to get no-watermark URL.
    """
    if not video_id:
        return None

    _safe_print(f"[DouyinExtractor] Trying iesdouyin share page: video_id={video_id}")

    share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"

    mobile_ua = (
        "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    )

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(share_url, headers={
                "User-Agent": mobile_ua,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })

            if resp.status_code != 200:
                _safe_print(f"[iesdouyin] HTTP {resp.status_code}")
                return None

            html = resp.text
            if len(html) < 1000:
                _safe_print("[iesdouyin] Page too small")
                return None

            # Parse _ROUTER_DATA JSON from the page
            m = re.search(r'_ROUTER_DATA\s*=\s*(\{.+)', html, re.DOTALL)
            if not m:
                _safe_print("[iesdouyin] No _ROUTER_DATA found")
                return None

            # Extract the full JSON object by counting braces
            raw = m.group(1)
            depth = 0
            end = 0
            for i, c in enumerate(raw):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                if depth == 0:
                    end = i + 1
                    break

            if end == 0:
                _safe_print("[iesdouyin] Could not parse JSON boundaries")
                return None

            data = json.loads(raw[:end])
            loader = data.get("loaderData", {})

            # Find the video page data
            item = None
            for k, v in loader.items():
                if not isinstance(v, dict):
                    continue
                video_info = v.get("videoInfoRes", {})
                if not video_info:
                    continue
                item_list = video_info.get("item_list", [])
                if item_list:
                    item = item_list[0]
                    break

            if not item:
                _safe_print("[iesdouyin] No video item found in _ROUTER_DATA")
                return None

            # Extract video data
            video = item.get("video", {})
            play_addr = video.get("play_addr", {})
            url_list = play_addr.get("url_list", [])

            if not url_list:
                _safe_print("[iesdouyin] No play_addr URLs")
                return None

            # Get watermark URL and convert to no-watermark
            wm_url = url_list[0]
            direct_url = wm_url.replace("/playwm/", "/play/")

            # Title
            title = item.get("desc", "") or "Douyin Video"

            # Cover/thumbnail
            thumbnail = ""
            cover = video.get("cover", {})
            if isinstance(cover, dict):
                cover_urls = cover.get("url_list", [])
                if cover_urls:
                    thumbnail = cover_urls[0]

            # Audio URL
            audio_url = ""
            music = item.get("music", {})
            if isinstance(music, dict):
                music_play = music.get("play_url", {})
                if isinstance(music_play, dict):
                    music_urls = music_play.get("url_list", [])
                    if music_urls:
                        audio_url = music_urls[0]
                elif isinstance(music_play, str):
                    audio_url = music_play

            # If MP3 quality requested, switch to audio
            if quality.startswith("mp3") and audio_url:
                direct_url = audio_url

            # Duration (usually in ms in Douyin _ROUTER_DATA)
            duration_ms = video.get("duration", 0)
            duration = int(duration_ms / 1000) if duration_ms > 1000 else int(duration_ms)

            _safe_print(f"[iesdouyin] Success: {title[:60]} ({duration}s)")
            return {
                "title": title,
                "thumbnail_url": thumbnail,
                "direct_mp4_url": direct_url,
                "audio_url": audio_url,
                "file_size_mb": 0,
                "duration": duration,
                "quality": quality,
                "original_url": f"https://www.douyin.com/video/{video_id}",
                "provider": "iesdouyin",
            }

    except json.JSONDecodeError as e:
        _safe_print(f"[iesdouyin] JSON parse error: {e}")
        return None
    except httpx.TimeoutException:
        _safe_print("[iesdouyin] Timeout")
        return None
    except Exception as e:
        _safe_print(f"[iesdouyin] Error: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════
# PROVIDER 2: TikWM API (Backup — free, no auth)
# ═════════════════════════════════════════════════════════════════════

async def _try_tikwm(url: str, quality: str = "video") -> Optional[Dict[str, Any]]:
    """
    TikWM free public API. May reject Douyin URLs but works for TikTok.
    Kept as fallback in case they re-enable Douyin support.
    
    Endpoint: GET https://www.tikwm.com/api/?url=<encoded_url>&hd=1
    """
    _safe_print(f"[DouyinExtractor] Trying TikWM API: {url}")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                "https://www.tikwm.com/api/",
                params={"url": url, "hd": 1},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "application/json",
                    "Referer": "https://www.tikwm.com/",
                },
            )

            if resp.status_code != 200:
                _safe_print(f"[TikWM] HTTP {resp.status_code}")
                return None

            body = resp.json()
            if body.get("code") != 0 or not body.get("data"):
                _safe_print(f"[TikWM] API error: {body.get('msg', 'unknown')}")
                return None

            data = body["data"]

            play_url   = data.get("play", "") or ""
            hdplay_url = data.get("hdplay", "") or ""
            wmplay_url = data.get("wmplay", "") or ""
            audio_url  = data.get("music", "") or ""

            for field in [play_url, hdplay_url, wmplay_url, audio_url]:
                if field and field.startswith("//"):
                    field = "https:" + field

            # Pick best video URL based on quality
            if quality.startswith("mp3"):
                direct_url = audio_url or play_url
            else:
                direct_url = hdplay_url or play_url

            if not direct_url:
                _safe_print("[TikWM] No video URL in response")
                return None

            title     = data.get("title", "TikTok Video")
            thumbnail = data.get("cover") or data.get("origin_cover") or ""

            hd_size = data.get("hd_size", 0)
            size = data.get("size", 0)
            file_size_mb = round((hd_size or size) / (1024 * 1024), 2)
            hd_size_mb = round(hd_size / (1024 * 1024), 2) if hd_size else 0
            size_mb = round(size / (1024 * 1024), 2) if size else 0
            duration     = int(data.get("duration", 0))

            _safe_print(f"[TikWM] Success: {title[:60]} ({duration}s)")
            return {
                "title":         title,
                "thumbnail_url": thumbnail,
                "direct_mp4_url": direct_url,
                "play_url":      play_url,
                "hdplay_url":    hdplay_url,
                "wmplay_url":    wmplay_url,
                "audio_url":     audio_url,
                "file_size_mb":  file_size_mb,
                "hd_size_mb":    hd_size_mb,
                "size_mb":       size_mb,
                "duration":      duration,
                "quality":       quality,
                "original_url":  url,
                "provider":      "tikwm",
            }

    except httpx.TimeoutException:
        _safe_print("[TikWM] Timeout")
        return None
    except Exception as e:
        _safe_print(f"[TikWM] Error: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════
# PROVIDER 3: ScraperAPI + SSR Parse (Last resort)
# ═════════════════════════════════════════════════════════════════════

async def _try_scraperapi_ssr(url: str, quality: str = "video") -> Optional[Dict[str, Any]]:
    """
    Use ScraperAPI to fetch the Douyin page HTML with JS rendering,
    then parse RENDER_DATA or _ROUTER_DATA for video info.
    """
    api_key = os.getenv("SCRAPERAPI_API_KEY", "")
    if not api_key:
        return None

    _safe_print(f"[DouyinExtractor] Trying ScraperAPI SSR parse: {url}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                "http://api.scraperapi.com/",
                params={
                    "api_key": api_key,
                    "url": url,
                    "render": "true",
                    "country_code": "cn",
                },
            )

            if resp.status_code != 200:
                _safe_print(f"[ScraperAPI/SSR] HTTP {resp.status_code}")
                return None

            html = resp.text
            if len(html) < 1000:
                _safe_print("[ScraperAPI/SSR] Page too small, likely blocked")
                return None

            video_id = _extract_video_id(url) or ""

            # Try _ROUTER_DATA first (same as iesdouyin)
            m = re.search(r'_ROUTER_DATA\s*=\s*(\{.+)', html, re.DOTALL)
            if m:
                raw = m.group(1)
                depth = 0
                end = 0
                for i, c in enumerate(raw):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                    if depth == 0:
                        end = i + 1
                        break

                if end > 0:
                    try:
                        data = json.loads(raw[:end])
                        loader = data.get("loaderData", {})
                        for k, v in loader.items():
                            if not isinstance(v, dict):
                                continue
                            video_info = v.get("videoInfoRes", {})
                            item_list = video_info.get("item_list", [])
                            if item_list:
                                item = item_list[0]
                                video = item.get("video", {})
                                play_addr = video.get("play_addr", {})
                                url_list = play_addr.get("url_list", [])
                                if url_list:
                                    direct_url = url_list[0].replace("/playwm/", "/play/")
                                    title = item.get("desc", "") or "Douyin Video"
                                    
                                    duration_ms = video.get("duration", 0)
                                    duration = int(duration_ms / 1000) if duration_ms > 1000 else int(duration_ms)
                                    
                                    _safe_print(f"[ScraperAPI/SSR] Success via _ROUTER_DATA")
                                    return {
                                        "title": title,
                                        "thumbnail_url": "",
                                        "direct_mp4_url": direct_url,
                                        "audio_url": "",
                                        "file_size_mb": 0,
                                        "duration": duration,
                                        "quality": quality,
                                        "original_url": url,
                                        "provider": "scraperapi_ssr",
                                    }
                    except json.JSONDecodeError:
                        pass

            # Try RENDER_DATA fallback
            render_match = re.search(
                r'<script\s+id="RENDER_DATA"\s+type="application/json">(.*?)</script>',
                html, re.DOTALL
            )
            if render_match:
                try:
                    raw = unquote(render_match.group(1))
                    data = json.loads(raw)
                    direct_url = ""
                    title = "Douyin Video"

                    for key, val in data.items():
                        if not isinstance(val, dict):
                            continue
                        val_str = json.dumps(val, ensure_ascii=False)
                        play_urls = re.findall(r'"playApi"\s*:\s*"([^"]+)"', val_str)
                        if play_urls:
                            direct_url = play_urls[0].replace("\\u002F", "/")
                        if not direct_url:
                            bitrate_urls = re.findall(r'"url_list"\s*:\s*\["([^"]+)"', val_str)
                            if bitrate_urls:
                                direct_url = bitrate_urls[0].replace("\\u002F", "/")
                        desc_match = re.findall(r'"desc"\s*:\s*"([^"]{3,200})"', val_str)
                        if desc_match:
                            title = desc_match[0]

                    if direct_url:
                        if direct_url.startswith("//"):
                            direct_url = "https:" + direct_url
                            
                        # RENDER_DATA usually doesn't expose duration easily, default to 0
                        duration = 0
                            
                        _safe_print(f"[ScraperAPI/SSR] Success via RENDER_DATA")
                        return {
                            "title": title,
                            "thumbnail_url": "",
                            "direct_mp4_url": direct_url,
                            "audio_url": "",
                            "file_size_mb": 0,
                            "duration": duration,
                            "quality": quality,
                            "original_url": url,
                            "provider": "scraperapi_ssr",
                        }
                except (json.JSONDecodeError, Exception):
                    pass

            _safe_print("[ScraperAPI/SSR] Could not extract video URL")
            return None

    except Exception as e:
        _safe_print(f"[ScraperAPI/SSR] Error: {e}")
        return None


# ═════════════════════════════════════════════════════════════════════
# PUBLIC API — Main entry point
# ═════════════════════════════════════════════════════════════════════

async def extract_douyin_video(url: str, quality: str = "video") -> Dict[str, Any]:
    """
    Extract a Douyin video using the multi-provider waterfall.

    Providers (in order):
      1. iesdouyin Share Page (parse _ROUTER_DATA SSR JSON)
      2. TikWM API (free, fast — may reject Douyin URLs)
      3. ScraperAPI SSR parse (needs API key, slower)

    Args:
        url:     Any Douyin URL (short or canonical)
        quality: "video", "video_4k", "mp3_128", "mp3_320"

    Returns:
        Dict with: title, thumbnail_url, direct_mp4_url, file_size_mb, quality, provider

    Raises:
        ValueError if all providers fail
    """
    # Step 0: Resolve short URL to get video ID
    original_url = url
    resolved_url = await _resolve_short_url(url)
    if resolved_url != original_url:
        _safe_print(f"[DouyinExtractor] Unshortened: {original_url} -> {resolved_url}")

    # Extract video ID for direct API calls
    video_id = _extract_video_id(resolved_url) or _extract_video_id(original_url)

    _safe_print(f"[DouyinExtractor] video_id={video_id}")

    # Provider 1: iesdouyin Share Page (most reliable)
    if video_id:
        result = await _try_iesdouyin_share(video_id, quality)
        if result:
            result["original_url"] = original_url
            return result

    # Provider 2: TikWM — try with original short URL (best chance)
    result = await _try_tikwm(original_url, quality)
    if result:
        result["original_url"] = original_url
        return result

    # Provider 3: ScraperAPI SSR — use canonical URL
    if resolved_url and "douyin.com" in resolved_url:
        result = await _try_scraperapi_ssr(resolved_url, quality)
        if result:
            result["original_url"] = original_url
            return result

    raise ValueError(
        "Không thể tải video Douyin. Tất cả provider đều thất bại. "
        "Vui lòng thử lại sau."
    )


def extract_douyin_video_sync(url: str, quality: str = "video") -> Dict[str, Any]:
    """Synchronous wrapper for Celery / sync contexts."""
    return asyncio.run(extract_douyin_video(url, quality))


# ── Test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://v.douyin.com/fV1sXyht2FA/"

    sys.stdout.reconfigure(encoding='utf-8')
    print(f"Testing Douyin extraction: {test_url}\n")

    result = asyncio.run(extract_douyin_video(test_url))
    print(f"\n{'='*60}")
    print(f"Provider:  {result.get('provider')}")
    print(f"Title:     {result.get('title', '')[:80]}")
    print(f"Thumbnail: {result.get('thumbnail_url', '')[:100]}")
    print(f"Video URL: {result.get('direct_mp4_url', '')[:120]}")
    print(f"Size:      {result.get('file_size_mb')} MB")
    print(f"SUCCESS!")
