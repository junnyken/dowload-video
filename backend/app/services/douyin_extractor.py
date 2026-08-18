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


def _canonical_douyin_url(video_id: str) -> str:
    """
    Build the canonical watch URL from an aweme_id.

    Every downstream consumer (yt-dlp, Apify, TikWM) only recognises the
    /video/<id> form. Feed-style URLs such as
    `douyin.com/jingxuan?modal_id=<id>` or `/discover?modal_id=<id>` are
    rejected outright ("Unsupported URL"), so we always normalise first.
    """
    return f"https://www.douyin.com/video/{video_id}"


def _resolve_cookie_file(user_cookies_file: Optional[str] = None) -> tuple[Optional[str], bool]:
    """
    Pick the best Douyin cookie file.

    Order: caller-supplied file (UI "Dùng cookie của tôi") -> shared cookie pool.
    Returns (path, is_temporary) — the caller deletes the file when temporary.
    """
    if user_cookies_file and os.path.exists(user_cookies_file):
        return user_cookies_file, False

    try:
        from app.core.cookie_manager import get_cookie_file
        pooled = get_cookie_file("douyin")
        if pooled:
            return pooled, True
    except Exception as e:
        _safe_print(f"[DouyinExtractor] Cookie pool unavailable: {e}")

    return None, False


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
    # TikWM free API: 1 req/sec hard limit.
    # Use Redis distributed lock so all workers share the quota (not just per-process sleep).
    try:
        from app.core.redis_client import get_redis
        _tikwm_rc = get_redis()
        _tikwm_lock = "tikwm:rate_lock"
        # SET NX PX 1200 — holds lock for 1.2s; only one worker proceeds at a time
        _acquired = _tikwm_rc.set(_tikwm_lock, "1", px=1200, nx=True)
        if not _acquired:
            # Another worker is within its 1-second window — wait for lock to clear
            await asyncio.sleep(1.3)
    except Exception:
        # Redis unavailable — fall back to simple sleep
        await asyncio.sleep(1.1)

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
            # `music` field: TikWM returns either a plain URL string or a nested dict
            music_raw  = data.get("music", "") or ""
            if isinstance(music_raw, dict):
                # Nested music_info object — extract play URL
                audio_url = (
                    music_raw.get("play_url") or
                    (music_raw.get("url_list") or [""])[0]
                ) or ""
            else:
                audio_url = music_raw

            if play_url.startswith("//"):   play_url   = "https:" + play_url
            if hdplay_url.startswith("//"): hdplay_url = "https:" + hdplay_url
            if wmplay_url.startswith("//"): wmplay_url = "https:" + wmplay_url
            if audio_url.startswith("//"):  audio_url  = "https:" + audio_url

            # Guard: if hdplay/play URLs look identical to the audio URL,
            # TikWM returned music in the video slot (slideshow/photo posts).
            # Fall back to wmplay (has watermark but is real video) or mark missing.
            def _is_audio_url(u: str) -> bool:
                """Heuristic: URL path ends in .mp3 or contains /music/ /audio/ segments."""
                lower = u.lower()
                return any(x in lower for x in (".mp3", "/music/", "/audio/", "soundcloud", "music_play"))

            if hdplay_url and _is_audio_url(hdplay_url):
                hdplay_url = ""
            if play_url and _is_audio_url(play_url):
                play_url = ""

            # Pick best video URL based on quality
            if quality.startswith("mp3"):
                direct_url = audio_url or play_url
            else:
                direct_url = hdplay_url or play_url or wmplay_url

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
    from app.core.scraperapi_pool import get_active_key
    api_key = get_active_key()
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
# PROVIDER 0: yt-dlp + signature cookies (primary — only unblocked path)
# ═════════════════════════════════════════════════════════════════════

def _ytdlp_extract_blocking(
    canonical_url: str,
    quality: str,
    cookie_file: Optional[str],
    proxy: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    Blocking yt-dlp extraction — the async wrapper runs this in a thread.

    yt-dlp's DouyinIE calls Douyin's own /aweme/v1/web/aweme/detail/ endpoint.
    That endpoint silently returns an empty body (HTTP 200, 0 bytes) unless the
    request carries valid signature cookies, which is why a cookie file is the
    deciding factor here rather than an optional extra.
    """
    try:
        import yt_dlp
    except ImportError:
        _safe_print("[yt-dlp] yt_dlp not installed")
        return None

    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    if proxy:
        opts["proxy"] = proxy
    if quality.startswith("mp3"):
        opts["format"] = "bestaudio/best"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(canonical_url, download=False)
    except Exception as e:
        _safe_print(f"[yt-dlp] {type(e).__name__}: {str(e)[:200]}")
        return None

    if not isinstance(info, dict):
        return None

    formats = [f for f in (info.get("formats") or []) if f.get("url")]
    if not formats:
        direct_url = info.get("url") or ""
    elif quality.startswith("mp3"):
        audio_only = [f for f in formats if f.get("vcodec") in (None, "none")]
        direct_url = (audio_only or formats)[-1].get("url", "")
    else:
        # yt-dlp sorts worst -> best, so the tail is the highest quality.
        direct_url = formats[-1].get("url", "")

    if not direct_url:
        _safe_print("[yt-dlp] No playable URL in extracted info")
        return None

    filesize = 0
    for f in reversed(formats):
        fs = f.get("filesize") or f.get("filesize_approx")
        if fs:
            filesize = round(fs / (1024 * 1024), 2)
            break

    _safe_print(f"[yt-dlp] Success: {(info.get('title') or '')[:60]}")
    return {
        "title": info.get("title") or "Douyin Video",
        "thumbnail_url": info.get("thumbnail") or "",
        "direct_mp4_url": direct_url,
        "audio_url": "",
        "file_size_mb": filesize,
        "duration": int(info.get("duration") or 0),
        "quality": quality,
        "original_url": canonical_url,
        "provider": "yt-dlp",
    }


async def _try_ytdlp(
    video_id: str,
    quality: str = "video",
    user_cookies_file: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Extract via yt-dlp's DouyinIE using the best cookie we can find.

    Douyin now requires signature cookies for every data path, so this provider
    is the only one that can succeed; the HTML-scraping providers below are kept
    as fallbacks in case Douyin restores its server-rendered payload.
    """
    if not video_id:
        return None

    canonical_url = _canonical_douyin_url(video_id)
    cookie_file, is_temp = _resolve_cookie_file(user_cookies_file)

    # ScraperAPI's proxy blocks Douyin's API paths (protected domain), so only a
    # genuine CN residential proxy is usable here — never the ScraperAPI fallback.
    try:
        from app.core.proxy_manager import IPROYAL_PROXY_CN
        proxy = IPROYAL_PROXY_CN or None
    except Exception:
        proxy = None

    _safe_print(
        f"[DouyinExtractor] Trying yt-dlp: {canonical_url} "
        f"(cookies={'yes' if cookie_file else 'NO'}, cn_proxy={'yes' if proxy else 'no'})"
    )

    try:
        return await asyncio.to_thread(
            _ytdlp_extract_blocking, canonical_url, quality, cookie_file, proxy
        )
    finally:
        if is_temp and cookie_file:
            try:
                os.unlink(cookie_file)
            except OSError:
                pass


# ═════════════════════════════════════════════════════════════════════
# PUBLIC API — Main entry point
# ═════════════════════════════════════════════════════════════════════

async def extract_douyin_video(
    url: str,
    quality: str = "video",
    user_cookies_file: Optional[str] = None,
) -> Dict[str, Any]:
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

    # Every provider below needs the canonical /video/<id> form: feed URLs such
    # as /jingxuan?modal_id=<id> are rejected outright by yt-dlp and TikWM.
    canonical_url = _canonical_douyin_url(video_id) if video_id else resolved_url

    # Provider 0: yt-dlp + signature cookies (the only path Douyin still serves)
    if video_id:
        result = await _try_ytdlp(video_id, quality, user_cookies_file)
        if result:
            result["original_url"] = original_url
            return result

    # Provider 1: iesdouyin Share Page (kept as fallback — Douyin dropped the
    # videoInfoRes payload from this page, so it currently returns nothing)
    if video_id:
        result = await _try_iesdouyin_share(video_id, quality)
        if result:
            result["original_url"] = original_url
            return result

    # Provider 2: TikWM
    result = await _try_tikwm(canonical_url, quality)
    if result:
        result["original_url"] = original_url
        return result

    # Provider 3: ScraperAPI SSR
    if canonical_url and "douyin.com" in canonical_url:
        result = await _try_scraperapi_ssr(canonical_url, quality)
        if result:
            result["original_url"] = original_url
            return result

    raise ValueError(
        "Không tải được video Douyin này. Douyin hiện yêu cầu cookie hợp lệ cho "
        "mọi video, kể cả video công khai. Hãy bật \"Dùng cookie của tôi\" và dán "
        "cookie douyin.com lấy từ trình duyệt, hoặc nhờ quản trị viên thêm cookie "
        "Douyin vào kho cookie dùng chung."
    )


def extract_douyin_video_sync(
    url: str,
    quality: str = "video",
    user_cookies_file: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronous wrapper for Celery / sync contexts."""
    return asyncio.run(extract_douyin_video(url, quality, user_cookies_file))


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
