"""
Video Downloader Service
=========================
Handles single video extraction and channel/playlist scraping.

Routing strategy:
  • Douyin links -> Dedicated multi-provider extractor (TikWM / douyin.wtf / ScraperAPI SSR)
  • All other platforms -> yt-dlp with proxy logic
  • Spotify -> YouTube search via yt-dlp

Proxy strategy:
  • YouTube / Facebook -> server IP (free)
  • TikTok / Douyin / Instagram -> IPRoyal proxy (metadata phase only)
  • Download/CDN fetch -> always server IP (CDNs rarely geo-block)
"""

import os
import asyncio
import base64
import hashlib
import tempfile
from time import sleep
import yt_dlp
import re
import signal
import concurrent.futures
from typing import Dict, Any, List
import httpx

# ── Platform cookies ─────────────────────────────────────────────────
# Source priority: Redis cookie pool → env var (single cookie fallback)
# Pool: add via POST /admin/cookies/add  (multiple accounts, auto-rotate)
# Env:  YOUTUBE_COOKIES_B64, TIKTOK_COOKIES_B64, etc. (single fallback)
_INSTAGRAM_COOKIES_B64 = os.getenv("INSTAGRAM_COOKIES_B64", "")
_YOUTUBE_COOKIES_B64   = os.getenv("YOUTUBE_COOKIES_B64", "")
_TIKTOK_COOKIES_B64    = os.getenv("TIKTOK_COOKIES_B64", "")
_FACEBOOK_COOKIES_B64  = os.getenv("FACEBOOK_COOKIES_B64", "")

# Per-process cache: {platform: (cookie_b64, tmp_file_path)}
_cookies_cache: dict[str, str | None] = {}
_active_cookie_b64: dict[str, str] = {}


def _get_cookies_file(platform: str, env_b64: str) -> str | None:
    """
    Get cookies temp file for a platform.
    Tries Redis pool first (rotating), falls back to env var.
    Result is cached per worker process — clears on block detection.
    """
    if platform in _cookies_cache:
        path = _cookies_cache[platform]
        if path and os.path.exists(path):
            return path
        # Stale cache entry — reset
        _cookies_cache.pop(platform, None)
        _active_cookie_b64.pop(platform, None)

    # Pick cookie: pool first, env var fallback
    b64_to_use = None
    try:
        from app.core.cookie_pool import get_cookie_from_pool
        b64_to_use = get_cookie_from_pool(platform)
    except Exception:
        pass
    if not b64_to_use:
        b64_to_use = env_b64

    if not b64_to_use:
        _cookies_cache[platform] = None
        return None

    try:
        decoded = base64.b64decode(b64_to_use).decode("utf-8")
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix=f"{platform}_cookies_"
        )
        tmp.write(decoded)
        tmp.close()
        _cookies_cache[platform] = tmp.name
        _active_cookie_b64[platform] = b64_to_use
        src = "pool" if b64_to_use != env_b64 else "env"
        print(f"[Cookies] Loaded {platform} cookies ({src}) → {tmp.name}")
        return tmp.name
    except Exception as e:
        print(f"[Cookies] Failed to decode {platform} cookies: {e}")
        _cookies_cache[platform] = None
        return None


def _rotate_cookie(platform: str) -> None:
    """
    Mark current cookie blocked, clear local cache.
    Next request picks a fresh cookie from the pool.
    """
    b64 = _active_cookie_b64.get(platform)
    if b64:
        try:
            from app.core.cookie_pool import mark_cookie_blocked
            mark_cookie_blocked(platform, b64)
        except Exception:
            pass
    _cookies_cache.pop(platform, None)
    _active_cookie_b64.pop(platform, None)
    print(f"[Cookies] Rotated {platform} cookie (block detected)")


def _get_instagram_cookies_file() -> str | None:
    return _get_cookies_file("instagram", _INSTAGRAM_COOKIES_B64)

def _get_youtube_cookies_file() -> str | None:
    return _get_cookies_file("youtube", _YOUTUBE_COOKIES_B64)

def _get_tiktok_cookies_file() -> str | None:
    return _get_cookies_file("tiktok", _TIKTOK_COOKIES_B64)

def _get_facebook_cookies_file() -> str | None:
    return _get_cookies_file("facebook", _FACEBOOK_COOKIES_B64)

_INSTAGRAM_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)


class _YTDLPLogger:
    """Captures yt-dlp warnings/errors regardless of quiet/no_warnings settings."""
    def __init__(self, prefix: str = ""):
        self._prefix = prefix

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            print(f"[yt-dlp{self._prefix}] {msg}")

    def info(self, msg: str) -> None:
        print(f"[yt-dlp{self._prefix}] {msg}")

    def warning(self, msg: str) -> None:
        print(f"[yt-dlp{self._prefix} WARN] {msg}")

    def error(self, msg: str) -> None:
        print(f"[yt-dlp{self._prefix} ERROR] {msg}")

from app.core.proxy_manager import get_proxy_config_for_phase, dispatch_scraping_request
from app.core.redis_client import get_redis
from app.utils.link_resolver import resolve_short_url, is_douyin_url
from app.services.douyin_extractor import extract_douyin_video_sync, _try_tikwm
from app.services.cobalt_service import is_cobalt_available, extract_youtube_formats_via_cobalt, download_from_cobalt, download_instagram_via_cobalt, fetch_cobalt_stream

# Ensure Deno is discoverable for yt-dlp JS challenges
_deno_bin = os.path.join(os.path.expanduser("~"), ".deno", "bin")
if _deno_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _deno_bin + os.pathsep + os.environ.get("PATH", "")

# Add a directory for temporary downloads
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── Global Extraction Timeout ────────────────────────────────────────
# Hard cap: if yt-dlp or any extractor hangs (e.g. Douyin captcha),
# we kill the operation after this many seconds.
# 30s accommodates the SharePage provider (URL resolve + page fetch).
EXTRACTION_TIMEOUT_SECONDS = 30


def _run_with_timeout(func, args=(), kwargs=None, timeout=EXTRACTION_TIMEOUT_SECONDS):
    """
    Execute `func(*args, **kwargs)` in a thread with a hard timeout.
    If the function doesn't return within `timeout` seconds,
    raise a TimeoutError with a clean message.
    """
    kwargs = kwargs or {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"Quá thời gian chờ ({timeout}s). "
                "Link có thể bị chặn bởi captcha hoặc server phản hồi chậm. "
                "Vui lòng thử lại sau."
            )


# ── TikTok-specific User-Agent ───────────────────────────────────────
TIKTOK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── Channel / Playlist URL patterns ─────────────────────────────────
CHANNEL_PATTERNS = [
    # TikTok profiles
    r"tiktok\.com/@[\w.-]+/?$",
    r"tiktok\.com/@[\w.-]+\?",
    # Douyin user profiles
    r"douyin\.com/user/",
    r"iesdouyin\.com/share/user/",
    r"youtube\.com/(c|channel|user|@)[\w.-]+",
    r"youtube\.com/playlist\?list=",
    r"youtu\.be/.*[?&]list=",
    # Instagram profiles (exclude single post/reel/tv but include stories & highlights as "channel" batch)
    r"instagram\.com/(?!p/|reel/|tv/)[\w.-]+/?$",
    r"instagram\.com/stories/[\w.-]+/?$",
    r"instagram\.com/[\w.-]+/highlights/",
    # Facebook video pages
    r"facebook\.com/[\w.-]+/videos",
    # X (Twitter) timelines / lists
    r"(twitter|x)\.com/[\w.-]+/?$",
    r"(twitter|x)\.com/[\w.-]+/media",
    r"(twitter|x)\.com/i/lists/",
    # Reddit subreddits / user pages
    r"reddit\.com/r/[\w.-]+/?$",
    r"reddit\.com/user/[\w.-]+/?$",
    # Pinterest boards
    r"pinterest\.(com|co\.uk)/[\w.-]+/[\w.-]+/?$",
]


def _is_channel_or_playlist(url: str) -> bool:
    """Detect if a URL points to a channel, profile, or playlist."""
    for pattern in CHANNEL_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def _get_base_opts(url: str, phase: str = "metadata", quality: str = "video") -> dict:
    """
    Return base yt-dlp options with PHASE-AWARE proxy selection.

    Args:
        url:     The target video/channel URL.
        phase:   "metadata" -> proxy if needed; "download" -> server IP.
        quality: "video" (no-watermark), "video_4k" (best merge), "mp3_128", "mp3_320".
    """
    if quality == "video_4k":
        # 4K/2K: request highest quality video+audio, merge with FFmpeg
        # WebM fallback added: YouTube often serves WebM DASH even when MP4 DASH is SABR-blocked
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio/best[ext=mp4]/best"
    elif quality.startswith("video_") and quality != "video":
        # Specific resolution merge, e.g., video_1080
        # Fallback chain: prefer target height → try WebM → relax to bestvideo → avoid progressive-only
        try:
            height = int(quality.split("_")[1])
            fmt = (
                f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={height}][ext=webm]+bestaudio[ext=webm]"
                f"/bestvideo[height<={height}]+bestaudio"
                f"/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo+bestaudio"
                f"/best[height<={height}][ext=mp4]/best[height<={height}]"
            )
        except:
            fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio/best[ext=mp4]/best"
    elif quality.startswith("mp3"):
        # Audio only extraction
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
    elif quality == "video_fast":
        # Fast mode: pre-merged only (no FFmpeg merge needed) — lower quality but instant
        # This is the OLD "video" behavior, kept for backward compatibility
        fmt = "b[ext=mp4]/best[ext=mp4]/best"
    else:
        # Default "video" quality — BEST quality with FFmpeg merge
        # YouTube separates HD/4K video and audio into DASH adaptive streams.
        # Pre-merged streams (progressive) are only 360p or 720p max.
        # By requesting bestvideo+bestaudio, yt-dlp downloads both and merges via FFmpeg.
        # WebM fallback: YouTube DASH WebM streams are less restricted by SABR than MP4.
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio/best[ext=mp4]/best"

    opts = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "no_color": True,
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10
    }
    
    if not quality.startswith("mp3"):
        opts["merge_output_format"] = "mp4"

    # FFmpeg postprocessor for merging (4K or specific resolutions)
    if quality.startswith("video_") and quality not in ("video", "video_fast"):
        opts["postprocessors"] = [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }]
    elif quality.startswith("mp3"):
        bitrate = "320" if "320" in quality else "128"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": bitrate,
        }]
    elif quality.startswith("audio_"):
        # e.g., audio_m4a, audio_webm
        codec = quality.split("_")[1]
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": codec,
            "preferredquality": "128",
        }]

    # Ensure output is saved to temp for server-side download + merge
    # This is needed for ANY quality that requires FFmpeg merging (which is most of them now)
    is_tiktok = "tiktok.com" in url.lower()
    needs_local_download = (
        quality.startswith("mp3") or 
        quality.startswith("audio_") or 
        (quality.startswith("video_") and quality != "video_fast") or 
        quality == "video" or  # Default quality now uses merge → needs local download
        is_tiktok
    )
    if needs_local_download:
        opts["outtmpl"] = os.path.join(DOWNLOAD_DIR, "%(id)s_%(format_id)s.%(ext)s")

    # ── Proxy: pool first (random residential), then existing proxy manager ──
    proxy = None
    try:
        from app.core.proxy_pool import get_proxy_from_pool
        _platform = (
            "youtube"   if ("youtube.com" in url or "youtu.be" in url) else
            "tiktok"    if "tiktok.com"   in url else
            "facebook"  if "facebook.com" in url or "fb.watch" in url else
            "instagram" if "instagram.com" in url else
            "douyin"    if "douyin.com"   in url else
            "twitter"   if "twitter.com"  in url or "x.com" in url else
            "default"
        )
        proxy = get_proxy_from_pool(_platform)
    except Exception:
        pass
    if not proxy:
        proxy = get_proxy_config_for_phase(url, phase=phase)
    if proxy:
        opts["proxy"] = proxy

    if "youtube.com" in url.lower() or "youtu.be" in url.lower():
        opts["logger"] = _YTDLPLogger("/YT")
        # yt-dlp 2025.5+: use node for JS challenges + enable EJS remote solver script
        opts["js_runtimes"] = {"node": {}}
        opts["remote_components"] = ["ejs:github"]
        # Cache EJS solver + extractor data on disk — avoids re-downloading through proxy each request
        opts["cachedir"] = "/tmp/ytdlp_cache"
        # Prioritize resolution, then codec compatibility, then bitrate
        opts["format_sort"] = ["res", "ext:mp4:m4a", "tbr", "vbr", "abr", "asr"]
        yt_cookies = _get_youtube_cookies_file()
        if yt_cookies:
            opts["cookiefile"] = yt_cookies
            print("[Downloader] YouTube cookies loaded")

    if "facebook.com" in url.lower():
        fb_cookies = _get_facebook_cookies_file()
        if fb_cookies:
            opts["cookiefile"] = fb_cookies
            print("[Downloader] Facebook cookies loaded")

    if "instagram.com" in url.lower():
        opts["http_headers"] = {"User-Agent": _INSTAGRAM_MOBILE_UA}
        opts["retries"] = 2
        opts["socket_timeout"] = 15
        from app.core.proxy_manager import IPROYAL_PROXY
        if not IPROYAL_PROXY and "proxy" in opts:
            del opts["proxy"]
        ig_cookies = _get_instagram_cookies_file()
        if ig_cookies:
            opts["cookiefile"] = ig_cookies
        else:
            opts["extractor_args"] = {"instagram": {"api": ["1"]}}

    return opts


def _apply_tiktok_opts(opts: dict, url: str, remove_watermark: bool = True) -> dict:
    """
    Inject TikTok/Douyin-specific options.
    By default: always attempt no-watermark extraction via API hostname.
    """
    if "tiktok.com" in url.lower() or "douyin.com" in url.lower():
        opts["http_headers"] = {"User-Agent": TIKTOK_USER_AGENT}
        opts["extractor_args"] = {
            "tiktok": {
                "api_hostname": ["api16-normal-c-useast1a.tiktokv.com"],
            }
        }
        # Inject TikTok cookies — reduces rate limiting on bulk downloads
        tt_cookies = _get_tiktok_cookies_file()
        if tt_cookies and "tiktok.com" in url.lower():
            opts["cookiefile"] = tt_cookies
            print("[Downloader] TikTok cookies loaded")
        if remove_watermark:
            opts["format"] = "bestvideo[format_id!~=watermark]/bestvideo/best"
        else:
            opts["format"] = "best[ext=mp4]/best"
    return opts


# ── Single Video Extraction ─────────────────────────────────────────

def _extract_best_url(info: dict) -> tuple[str, float]:
    """
    Extract the best direct download URL from yt-dlp info dict, and its filesize.
    """
    best_filesize = info.get("filesize") or info.get("filesize_approx")
    
    # 1. Top-level URL (works for single-stream formats like TikTok)
    direct_url = info.get("url")
    if direct_url:
        return direct_url, best_filesize

    # 2. Check requested_formats (merged format case — YouTube)
    requested = info.get("requested_formats", [])
    if requested:
        # Prefer video stream (first entry in requested_formats)
        for fmt in requested:
            fmt_url = fmt.get("url")
            if fmt_url:
                fs = fmt.get("filesize") or fmt.get("filesize_approx") or best_filesize
                return fmt_url, fs

    # 3. Scan all available formats for a single mp4 with both video+audio
    formats = info.get("formats", [])
    # Sort by quality (height) descending, prefer mp4 with both streams
    best_combined = None
    best_video_only = None
    for f in reversed(formats):  # reversed = highest quality first
        f_url = f.get("url")
        if not f_url:
            continue
        ext = f.get("ext", "")
        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        has_video = vcodec and vcodec != "none"
        has_audio = acodec and acodec != "none"

        if ext == "mp4" and has_video and has_audio and not best_combined:
            best_combined = f_url
            best_filesize = f.get("filesize") or f.get("filesize_approx")
        elif ext == "mp4" and has_video and not best_video_only:
            best_video_only = f_url
            if not best_filesize:
                best_filesize = f.get("filesize") or f.get("filesize_approx")

    return best_combined or best_video_only or "", best_filesize


def _extract_available_formats(info: dict) -> dict:
    """
    Parse yt-dlp's format list into a clean, deduplicated list of
    downloadable formats grouped by type (video / audio).

    Returns a dict with:
      - video_formats: list of combined (video+audio) mp4 streams
      - audio_formats: list of audio-only streams
      - max_video_only_height: highest resolution available via merge
    """
    raw_formats = info.get("formats", [])
    if not raw_formats:
        return {"video_formats": [], "audio_formats": [], "max_video_only_height": 0}

    video_formats = []
    audio_formats = []
    seen_video_heights = set()
    seen_audio = set()
    max_video_only_height = 0

    def _make_video_entry(f, height, ext, requires_merge):
        filesize = f.get("filesize") or f.get("filesize_approx") or 0
        filesize_mb = round(filesize / (1024 * 1024), 2) if filesize else 0
        if height >= 2160:
            label = "4K"
        elif height >= 1440:
            label = "2K"
        elif height >= 1080:
            label = "Full HD"
        elif height >= 720:
            label = "HD"
        elif height >= 480:
            label = "SD"
        else:
            label = f"{height}p"
        return {
            "type": "video",
            "label": label,
            "resolution": f"{height}p",
            "height": height,
            "ext": ext,
            "filesize_mb": filesize_mb,
            "url": f.get("url"),
            "requires_merge": requires_merge
        }

    # ── Pass 1: Collect pre-merged (V+A) formats (like 360p progressive) ──
    for f in reversed(raw_formats):
        f_url = f.get("url")
        if not f_url:
            continue
        ext = f.get("ext", "")
        vcodec = (f.get("vcodec") or "none")
        acodec = (f.get("acodec") or "none")
        has_video = vcodec != "none"
        has_audio = acodec != "none"

        if has_video and has_audio and ext in ("mp4", "webm"):
            height = f.get("height") or 0
            if not height:
                continue
            if height in seen_video_heights:
                continue
            seen_video_heights.add(height)
            video_formats.append(_make_video_entry(f, height, ext, False))

    # ── Pass 2: Add video-only (requires_merge) formats at new heights ──
    for f in reversed(raw_formats):
        f_url = f.get("url")
        if not f_url:
            continue
        ext = f.get("ext", "")
        vcodec = (f.get("vcodec") or "none")
        acodec = (f.get("acodec") or "none")
        has_video = vcodec != "none"
        has_audio = acodec != "none"

        if has_video and not has_audio:
            height = f.get("height") or 0
            if not height:
                continue
            if height > max_video_only_height:
                max_video_only_height = height
            if ext not in ("mp4", "webm"):
                continue
            if height in seen_video_heights:
                continue
            seen_video_heights.add(height)
            video_formats.append(_make_video_entry(f, height, ext, True))

    # ── Audio-only streams ───────────────────────────────────
    for f in reversed(raw_formats):
        f_url = f.get("url")
        if not f_url:
            continue
        ext = f.get("ext", "")
        vcodec = (f.get("vcodec") or "none")
        acodec = (f.get("acodec") or "none")
        has_video = vcodec != "none"
        has_audio = acodec != "none"

        if has_audio and not has_video:
            abr = int(f.get("abr") or f.get("tbr") or 0)
            if not abr:
                abr = 128

            dedup_key = f"{abr}_{ext}"
            if dedup_key in seen_audio:
                continue
            seen_audio.add(dedup_key)

            filesize = f.get("filesize") or f.get("filesize_approx") or 0
            filesize_mb = round(filesize / (1024 * 1024), 2) if filesize else 0

            audio_formats.append({
                "type": "audio",
                "label": f"{abr}kbps",
                "ext": ext,
                "filesize_mb": filesize_mb,
                "url": f_url,
                "bitrate": abr,
            })

    # Sort: video by height desc, audio by bitrate desc
    video_formats.sort(key=lambda x: x["height"], reverse=True)
    audio_formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)

    return {
        "video_formats": video_formats[:6],
        "audio_formats": audio_formats[:4],
        "max_video_only_height": max_video_only_height,
    }


async def _try_instagram_embed(url: str) -> dict | None:
    """
    Last-resort Instagram extractor: scrapes the /embed/ page HTML for video src.
    Works for public Reels/Posts without login cookies.
    Returns a minimal yt-dlp-compatible info dict or None.
    """
    import json as _json
    try:
        # Extract shortcode from URL: /reel/CODE/ or /p/CODE/
        m = re.search(r"/(?:reel|p|tv)/([A-Za-z0-9_-]+)", url)
        if not m:
            return None
        shortcode = m.group(1)
        embed_url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
        headers = {
            "User-Agent": _INSTAGRAM_MOBILE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.instagram.com/",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0, headers=headers) as client:
            resp = await client.get(embed_url)
            html = resp.text

        # Try to find video URL in embed HTML
        video_url = None
        # Pattern 1: direct src in <video>
        vm = re.search(r'<video[^>]+src="([^"]+\.mp4[^"]*)"', html)
        if vm:
            video_url = vm.group(1).replace("&amp;", "&")

        # Pattern 2: JSON blob with video_url
        if not video_url:
            jm = re.search(r'"video_url"\s*:\s*"([^"]+)"', html)
            if jm:
                video_url = jm.group(1).replace("\\u0026", "&").replace("\\/", "/")

        # Pattern 3: EmbedHelper JSON
        if not video_url:
            jm = re.search(r'window\.__additionalDataLoaded\([^,]+,\s*(\{.+?\})\s*\)', html)
            if jm:
                try:
                    d = _json.loads(jm.group(1))
                    video_url = (
                        d.get("shortcode_media", {}).get("video_url") or
                        d.get("media", {}).get("video_url")
                    )
                except Exception:
                    pass

        if not video_url:
            print("[Instagram] embed scraper: no video URL found in embed page")
            return None

        # Extract thumbnail
        thumb = ""
        tm = re.search(r'"display_url"\s*:\s*"([^"]+)"', html)
        if tm:
            thumb = tm.group(1).replace("\\u0026", "&").replace("\\/", "/")

        # Extract title
        title = "Instagram Video"
        titm = re.search(r'<title>([^<]+)</title>', html)
        if titm:
            title = titm.group(1).strip()

        print(f"[Instagram] embed scraper success: {video_url[:60]}...")
        return {
            "url": video_url,
            "title": title,
            "thumbnail": thumb,
            "ext": "mp4",
            "id": shortcode,
            "extractor": "instagram_embed",
        }
    except Exception as e:
        print(f"[Instagram] embed scraper failed: {e}")
        return None


def _extract_video_info_impl(url: str, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False) -> Dict[str, Any]:
    """
    Extract info for a single video URL (synchronous).
    Returns title, thumbnail, and direct MP4 URL.

    Uses PROXY ONLY for metadata extraction, not file download.
    Falls back to Scraping API if primary extraction fails.
    """
    # ── Step 0: Unshorten short links (v.douyin.com, vm.tiktok.com, etc.)
    # This MUST happen before any other processing so that downstream
    # extractors and proxy rules see the canonical URL.
    original_input_url = url
    url = resolve_short_url(url)
    if url != original_input_url:
        print(f"[Downloader] Unshortened: {original_input_url} -> {url}")

    # ── Douyin: Bypass yt-dlp entirely ─────────────────────────
    # yt-dlp cannot handle Douyin's anti-bot (JS VM + captcha).
    # Route through Apify (cloud) when token available, else multi-provider extractor.
    if is_douyin_url(url) or is_douyin_url(original_input_url):
        try:
            result = None
            if os.getenv("APIFY_TOKEN", ""):
                try:
                    from app.services.apify_service import extract_douyin_apify_sync
                    result = extract_douyin_apify_sync(original_input_url, quality)
                    print(f"[Downloader] Douyin via Apify: {result.get('title','')[:60]}")
                except Exception as apify_err:
                    print(f"[Downloader] Apify Douyin failed, falling back: {apify_err}")
                    result = None
            if result is None:
                result = extract_douyin_video_sync(original_input_url, quality)

            # Force server-side download for Douyin to prevent proxy-download 403s
            if result.get("direct_mp4_url") and not result.get("local_file_path"):
                import uuid
                import httpx

                os.makedirs("downloads", exist_ok=True)
                ext = "mp3" if quality.startswith("mp3") else "mp4"
                local_path = f"downloads/douyin_{uuid.uuid4().hex[:8]}.{ext}"

                try:
                    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
                        with client.stream("GET", result["direct_mp4_url"]) as resp:
                            resp.raise_for_status()
                            with open(local_path, "wb") as f:
                                for chunk in resp.iter_bytes(chunk_size=65536):
                                    f.write(chunk)

                    if os.path.exists(local_path):
                        result["local_file_path"] = local_path
                        result["file_size_mb"] = round(os.path.getsize(local_path) / (1024 * 1024), 2)
                        result["direct_mp4_url"] = None
                        if ext == "mp3":
                            result["local_mp3_path"] = local_path
                except Exception as dl_err:
                    print(f"[Downloader] Failed to download Douyin file locally: {dl_err}")

            return result
        except Exception as dy_err:
            print(f"[Downloader] Douyin extractor failed: {dy_err}")
            raise ValueError(f"Không thể tải video Douyin: {dy_err}")

    # ── TikTok VN: TikWM first (fast, no local download needed) ────────
    # TikWM returns signed CDN URLs directly — proxy-download streams them.
    # yt-dlp is kept only as fallback in case TikWM fails.
    is_tiktok = "tiktok.com" in url.lower()
    if is_tiktok:
        # Clean tracking params before calling TikWM
        clean_url = url.split("?")[0] if "?" in url else url
        try:
            tikwm_res = asyncio.run(_try_tikwm(clean_url, quality))
            if tikwm_res and tikwm_res.get("direct_mp4_url"):
                print(f"[Downloader] TikWM success for {clean_url}")
                # Build multi-format list so frontend can show HD / SD / Watermark / MP3 buttons
                tikwm_formats = []
                if tikwm_res.get("hdplay_url"):
                    tikwm_formats.append({
                        "type": "video", "label": "H.265 (Dung lượng nhỏ)",
                        "resolution": "HD", "height": 1080, "ext": "mp4",
                        "url": tikwm_res["hdplay_url"],
                        "filesize_mb": tikwm_res.get("hd_size_mb", 0),
                        "requires_merge": False,
                    })
                if tikwm_res.get("play_url"):
                    tikwm_formats.append({
                        "type": "video", "label": "H.264 (Chất lượng gốc)",
                        "resolution": "SD", "height": 720, "ext": "mp4",
                        "url": tikwm_res["play_url"],
                        "filesize_mb": tikwm_res.get("size_mb", 0),
                        "requires_merge": False,
                    })
                if tikwm_res.get("wmplay_url"):
                    tikwm_formats.append({
                        "type": "video", "label": "With Watermark",
                        "resolution": "SD", "height": 540, "ext": "mp4",
                        "url": tikwm_res["wmplay_url"],
                        "filesize_mb": 0, "requires_merge": False,
                    })
                if tikwm_res.get("audio_url"):
                    tikwm_formats.append({
                        "type": "audio", "label": "Music MP3",
                        "ext": "mp3", "filesize_mb": 0, "bitrate": 128,
                        "url": tikwm_res["audio_url"],
                    })
                return {
                    "title":             tikwm_res["title"],
                    "thumbnail_url":     tikwm_res["thumbnail_url"],
                    "direct_mp4_url":    tikwm_res["direct_mp4_url"],
                    "file_size_mb":      tikwm_res.get("file_size_mb", 0),
                    "quality":           quality,
                    "original_url":      clean_url,
                    "duration":          tikwm_res.get("duration", 0),
                    "available_formats": tikwm_formats,
                    "max_merge_height":  0,
                    "provider":          "tikwm",
                }
        except Exception as tw_err:
            print(f"[Downloader] TikWM failed: {tw_err} — falling back to yt-dlp")

    # Clean TikTok URLs before yt-dlp
    if is_tiktok and "?" in url:
        url = url.split("?")[0]



    # ── Spotify: Resolve to YouTube search (no API key needed for tracks) ──
    _spotify_title = ""
    _spotify_artist = ""
    _spotify_thumbnail = ""
    _spotify_search_key = None
    if "open.spotify.com" in url:
        from app.services.spotify_service import get_track_info_async
        try:
            sp_info = asyncio.run(get_track_info_async(url))
            _spotify_title = sp_info.get("name", "")
            _spotify_artist = sp_info.get("artist_str", "")
            _spotify_thumbnail = sp_info.get("thumbnail", "")
            search_query = sp_info["search_query"]

            # Check Redis cache: search_query → direct YouTube URL (7-day TTL)
            _spotify_search_key = f"spotify_yt:{hashlib.md5(search_query.encode()).hexdigest()}"
            cached_yt = None
            try:
                cached_yt = get_redis().get(_spotify_search_key)
            except Exception:
                pass

            if cached_yt:
                print(f"[Spotify Cache HIT] {_spotify_artist} - {_spotify_title} -> {cached_yt[:60]}")
                url = cached_yt
            else:
                url = search_query

            quality = "mp3_128"  # Force audio-only for Spotify
            print(f"[Downloader] Spotify -> {_spotify_artist} - {_spotify_title} -> {url[:80]}")
        except Exception as sp_err:
            print(f"[Downloader] Spotify error: {sp_err}")
            raise ValueError(f"Không thể tải nhạc Spotify: {sp_err}")

    # ── Phase 1: Metadata extraction (and download if needed) ──────
    opts = _get_base_opts(url, phase="metadata", quality=quality)
    opts["extract_flat"] = False
    opts = _apply_tiktok_opts(opts, url, remove_watermark)

    # Force server-side download for FFmpeg merging (HD/4K quality)
    # Only "video_fast" mode skips download (returns direct pre-merged URL for browser)
    is_tiktok = "tiktok.com" in url.lower()
    is_youtube_url = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    is_instagram = "instagram.com" in url.lower()
    is_facebook  = "facebook.com" in url.lower()
    should_download = quality != "video_fast" or is_tiktok

    # ── Per-platform rate limiter (sliding window, Redis-backed) ────
    # Prevents 8 workers from bursting the same platform simultaneously.
    # TikTok/Instagram are throttled most aggressively (10-20 req/min).
    _throttle_platform = (
        "youtube"   if is_youtube_url else
        "tiktok"    if is_tiktok      else
        "facebook"  if is_facebook    else
        "instagram" if is_instagram   else None
    )
    if _throttle_platform:
        try:
            from app.core.platform_throttle import check_and_acquire, PlatformThrottleError
            check_and_acquire(_throttle_platform)
        except Exception as _te:
            print(f"[Throttle] {_throttle_platform}: {_te}")

    info = None
    try:
        if is_youtube_url and should_download:
            # YouTube two-phase: proxy for auth/metadata only, direct CDN for download
            # Phase A: extract info (signed CDN URLs) via residential proxy
            # Cache Phase A result in Redis — same URL within TTL skips proxy entirely
            import json as _json_dl
            _YT_PHASE_A_TTL = 1800  # 30 min — YouTube signed URLs valid ~6 hours
            _yt_phase_a_key = f"yt_phaseA:{hashlib.md5(url.encode()).hexdigest()}"
            info = None
            try:
                from app.core.redis_client import get_redis
                _rc = get_redis()
                _cached_raw = _rc.get(_yt_phase_a_key)
                if _cached_raw:
                    info = _json_dl.loads(_cached_raw)
                    print(f"[Cache] YouTube Phase A HIT — skipping proxy ({_yt_phase_a_key[-8:]})")
            except Exception:
                pass

            if info is None:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                # Cache the Phase A result so next request within 30 min skips proxy
                if info:
                    try:
                        _rc = get_redis()
                        _rc.setex(_yt_phase_a_key, _YT_PHASE_A_TTL, _json_dl.dumps(info, default=str))
                        print(f"[Cache] YouTube Phase A cached {_YT_PHASE_A_TTL}s")
                    except Exception as _ce:
                        print(f"[Cache] Phase A cache write failed: {_ce}")

            # Phase B: download using already-resolved CDN URLs, no proxy needed
            if info:
                dl_opts = _get_base_opts(url, phase="download", quality=quality)
                dl_opts["extract_flat"] = False
                print(f"[Downloader] YouTube: info via proxy OK, downloading CDN direct (no proxy)")
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    info = ydl.process_ie_result(info, download=True)
        else:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=should_download)
        # ytsearch returns a playlist container — unwrap to the actual video entry
        if info and info.get("entries") and not info.get("formats") and not info.get("url"):
            entries = [e for e in info["entries"] if e]
            if entries:
                info = entries[0]
        # Log actual downloaded quality
        if should_download and info and info.get("requested_downloads"):
            dl = info["requested_downloads"][0]
            actual_h = dl.get("height") or info.get("height", 0)
            actual_mb = (dl.get("filesize") or 0) / (1024 * 1024)
            print(f"[Downloader] yt-dlp downloaded: {actual_h}p, {actual_mb:.1f}MB, format={dl.get('format_id','?')}")
    except Exception as primary_err:
        primary_err_str = str(primary_err)
        print(f"[Downloader] Primary extraction failed for {url}: {primary_err_str}")
        # Detect block signals → rotate cookie + invalidate PO token if YouTube
        _block_signals = ["sign in to confirm", "login_required", "rate limit",
                          "too many requests", "429", "403", "bot", "captcha"]
        if any(s in primary_err_str.lower() for s in _block_signals):
            _platform_name = (
                "youtube"   if is_youtube_url else
                "tiktok"    if is_tiktok      else
                "facebook"  if is_facebook    else
                "instagram" if is_instagram   else None
            )
            if _platform_name:
                _rotate_cookie(_platform_name)
            if is_youtube_url:
                # Invalidate cached PO token so next request gets a fresh one
                try:
                    from app.core.po_token_cache import invalidate_po_token
                    invalidate_po_token()
                    print("[Downloader] PO Token cache invalidated after YouTube block")
                except Exception:
                    pass
        if is_youtube_url and ("Sign in to confirm" in primary_err_str or "LOGIN_REQUIRED" in primary_err_str):
            print("[Downloader] YouTube bot detection confirmed — PO Token invalidated, will refresh on next request")

    # ── Phase 1.5a: YouTube SABR Recovery via Cobalt (safety net) ──────
    # With android_vr client, SABR is usually bypassed successfully.
    # This Cobalt fallback is kept as safety net in case android_vr stops working.
    # Detect quality downgrade and replace the file using Cobalt's tunnel.
    if is_youtube_url and should_download and info and quality.startswith("video_") and "_" in quality:
        try:
            target_height = int(quality.split("_")[1])
        except (ValueError, IndexError):
            target_height = 0

        if target_height > 0:
            actual_height = 0
            if info.get("requested_downloads"):
                dl0 = info["requested_downloads"][0]
                actual_height = dl0.get("height") or info.get("height") or 0

            # SABR triggered: downloaded quality is significantly below target
            if actual_height == 0 or actual_height < target_height * 0.8:
                print(f"[Downloader] SABR: yt-dlp got {actual_height}p (need {target_height}p). Cobalt fallback...")
                if is_cobalt_available():
                    cobalt_path = download_from_cobalt(url, str(target_height), DOWNLOAD_DIR)
                    if cobalt_path:
                        # Delete the wrong-quality file yt-dlp downloaded
                        if info.get("requested_downloads"):
                            old_path = info["requested_downloads"][0].get("filepath")
                            if old_path and os.path.exists(old_path):
                                try:
                                    os.remove(old_path)
                                except Exception:
                                    pass
                            info["requested_downloads"][0]["filepath"] = cobalt_path
                        print(f"[Downloader] Cobalt recovered {target_height}p successfully")
                    else:
                        print("[Downloader] Cobalt fallback failed — keeping yt-dlp result")
                else:
                    print("[Downloader] Cobalt not available for SABR recovery")

    # ── Phase 1.5b-YT: YouTube total bot-block → Cobalt primary download ─
    # yt-dlp is fully blocked by YouTube bot detection (all clients failed).
    # Cobalt manages its own PO tokens internally and can bypass detection.
    # This fires when info is None AND url is YouTube.
    if info is None and is_youtube_url:
        print("[Downloader] YouTube bot detection — trying Cobalt as primary downloader...")
        if is_cobalt_available():
            # Pick Cobalt download mode & quality based on requested quality
            if quality.startswith("mp3") or quality.startswith("audio_"):
                cobalt_resp = fetch_cobalt_stream(url, download_mode="audio", audio_format="mp3", audio_bitrate="128")
                cobalt_ext = "mp3"
            elif quality == "video_4k":
                cobalt_resp = fetch_cobalt_stream(url, video_quality="2160", download_mode="auto")
                cobalt_ext = "mp4"
                if cobalt_resp.get("status") == "error":
                    cobalt_resp = fetch_cobalt_stream(url, video_quality="1080", download_mode="auto")
            elif quality.startswith("video_") and "_" in quality:
                try:
                    h = quality.split("_")[1]
                    cobalt_resp = fetch_cobalt_stream(url, video_quality=h, download_mode="auto")
                except Exception:
                    cobalt_resp = fetch_cobalt_stream(url, video_quality="1080", download_mode="auto")
                cobalt_ext = "mp4"
            else:
                cobalt_resp = fetch_cobalt_stream(url, video_quality="1080", download_mode="auto")
                cobalt_ext = "mp4"

            cobalt_status = cobalt_resp.get("status", "error")
            cobalt_stream_url = cobalt_resp.get("url")

            if cobalt_status != "error" and cobalt_stream_url:
                import uuid as _uuid
                raw_name = cobalt_resp.get("filename", f"cobalt_{_uuid.uuid4().hex[:8]}.{cobalt_ext}")
                cobalt_path = os.path.join(DOWNLOAD_DIR, os.path.basename(raw_name))
                try:
                    with httpx.Client(timeout=600.0, follow_redirects=True) as _client:
                        with _client.stream("GET", cobalt_stream_url) as _resp:
                            _resp.raise_for_status()
                            with open(cobalt_path, "wb") as _f:
                                for _chunk in _resp.iter_bytes(chunk_size=65536):
                                    if _chunk:
                                        _f.write(_chunk)

                    file_size = os.path.getsize(cobalt_path) if os.path.exists(cobalt_path) else 0
                    if file_size > 0:
                        print(f"[Cobalt] Primary download OK: {file_size/(1024*1024):.1f}MB → {cobalt_path}")

                        # Title + thumbnail via YouTube oEmbed (no bot-protection)
                        _title = "YouTube Video"
                        _thumb = ""
                        try:
                            _vid_m = re.search(r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
                            if _vid_m:
                                _oe = httpx.get(
                                    f"https://www.youtube.com/oembed?url=https://youtu.be/{_vid_m.group(1)}&format=json",
                                    timeout=5.0,
                                )
                                if _oe.status_code == 200:
                                    _oe_data = _oe.json()
                                    _title = _oe_data.get("title", _title)
                                    _thumb = _oe_data.get("thumbnail_url", "")
                        except Exception:
                            pass

                        _cobalt_result = {
                            "title": _title,
                            "thumbnail_url": _thumb,
                            "direct_mp4_url": None,
                            "local_file_path": cobalt_path,
                            "file_size_mb": round(file_size / (1024 * 1024), 2),
                            "quality": quality,
                            "original_url": url,
                            "duration": 0,
                            "available_formats": [],
                            "max_merge_height": 0,
                            "downloaded_height": 1080 if cobalt_ext == "mp4" else 0,
                            "chapters": [],
                        }
                        if cobalt_ext == "mp3":
                            _cobalt_result["local_mp3_path"] = cobalt_path
                        return _cobalt_result
                    else:
                        print("[Cobalt] Primary download: file empty, skipping")
                        if os.path.exists(cobalt_path):
                            try: os.remove(cobalt_path)
                            except: pass
                except Exception as _ce:
                    print(f"[Cobalt] Primary download error: {_ce}")
                    if os.path.exists(cobalt_path):
                        try: os.remove(cobalt_path)
                        except: pass
            else:
                print(f"[Cobalt] Primary fallback failed: status={cobalt_status}, err={cobalt_resp.get('error', {})}")
        else:
            print("[Downloader] Cobalt not available for YouTube bot recovery")

    # ── Phase 1.5b: TikWM fallback (yt-dlp failed, TikWM not yet tried) ─
    # Reaches here only if TikWM was skipped (non-tiktok) or yt-dlp failed
    # for a TikTok URL that somehow bypassed the early-return above.
    if info is None and is_tiktok:
        print(f"[Downloader] yt-dlp failed, retrying TikWM for {url}")
        tikwm_res = asyncio.run(_try_tikwm(url, quality))
        if tikwm_res and tikwm_res.get("direct_mp4_url"):
            return {
                "title":          tikwm_res.get("title", "TikTok Video"),
                "thumbnail_url":  tikwm_res.get("thumbnail_url", ""),
                "direct_mp4_url": tikwm_res["direct_mp4_url"],
                "file_size_mb":   tikwm_res.get("file_size_mb", 0),
                "quality":        quality,
                "original_url":   url,
                "duration":       tikwm_res.get("duration", 0),
                "available_formats": [],
                "max_merge_height":  0,
                "provider":       "tikwm",
            }

    # ── Phase 2: Scraping API fallback ───────────────────────────
    if info is None:
        import tempfile
        print(f"[Downloader] Trying Smart Proxy Dispatcher fallback for {url}")
        html_content = asyncio.run(dispatch_scraping_request(url))
        
        if html_content:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(html_content)
                    tmp_path = f.name
                
                fallback_opts = _get_base_opts(url, phase="download")  # no proxy for API
                fallback_opts["extract_flat"] = False
                fallback_opts["enable_file_urls"] = True
                fallback_opts = _apply_tiktok_opts(fallback_opts, url, remove_watermark)

                # Fix for Windows paths in yt-dlp
                file_url = f"file:///{tmp_path.replace(chr(92), '/')}"
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(file_url, download=(quality.startswith('mp3')))
                    
            except Exception as fallback_err:
                print(f"[Downloader] Scraping API fallback parse failed: {fallback_err}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except: pass

    # ── Phase 3: Instagram fallbacks (Cobalt → embed scraper) ──────
    is_instagram = "instagram.com" in url.lower()
    if info is None and is_instagram:
        print(f"[Downloader] Trying Cobalt for Instagram: {url}")
        cobalt_info = download_instagram_via_cobalt(url, DOWNLOAD_DIR)
        if cobalt_info:
            info = cobalt_info
        else:
            print(f"[Downloader] Cobalt failed, trying embed scraper for Instagram")
            info = asyncio.run(_try_instagram_embed(url))

    if info is None:
        # Provide more specific error messages based on platform
        if is_youtube_url:
            raise ValueError(
                "Không thể tải video YouTube. YouTube đang chặn bot — "
                "vui lòng thử lại sau 30 giây. "
                "Nếu lỗi tiếp tục, video có thể bị xoá, giới hạn vùng (geo-block), "
                "hoặc yêu cầu đăng nhập."
            )
        raise ValueError("Không thể trích xuất thông tin. Vui lòng kiểm tra lại xem link có bị thiếu chữ/số, sai định dạng hoặc video bị cài đặt riêng tư không.")

    direct_url, filesize = _extract_best_url(info)
    
    filesize_mb = 0
    if filesize:
        filesize_mb = round(filesize / (1024 * 1024), 2)

    # ── Extract all available formats for user selection ─────
    fmt_info = _extract_available_formats(info)

    # ── Cobalt Fallback for YouTube SABR-blocked formats ─────
    is_youtube = "youtube" in info.get("extractor", "").lower() or "youtube.com" in url.lower() or "youtu.be" in url.lower()
    yt_dlp_video_count = len(fmt_info["video_formats"])
    
    if is_youtube and yt_dlp_video_count <= 1:
        print(f"[Downloader] YouTube SABR detected ({yt_dlp_video_count} video format). Trying Cobalt fallback...")
        try:
            if is_cobalt_available():
                cobalt_fmts = extract_youtube_formats_via_cobalt(url)
                cobalt_videos = cobalt_fmts.get("video_formats", [])
                cobalt_audios = cobalt_fmts.get("audio_formats", [])
                
                if len(cobalt_videos) > yt_dlp_video_count:
                    print(f"[Downloader] Cobalt found {len(cobalt_videos)} video + {len(cobalt_audios)} audio formats!")
                    fmt_info = cobalt_fmts
                else:
                    print(f"[Downloader] Cobalt returned {len(cobalt_videos)} video formats (same or less). Keeping yt-dlp result.")
            else:
                print("[Downloader] Cobalt instance not available. Skipping fallback.")
        except Exception as cobalt_err:
            print(f"[Downloader] Cobalt fallback error: {cobalt_err}")

    # ── Check if best url has no video (e.g. TikTok photo slides) ──
    is_audio_only = False
    for f in info.get("formats", []):
        if f.get("url") == direct_url:
            if f.get("vcodec") == "none":
                is_audio_only = True
            break

    subtitle_url = None
    if download_subs:
        subs = info.get("subtitles") or info.get("automatic_captions") or {}
        if subs:
            target_lang = next((l for l in ["vi", "en", "en-US", "vi-VN"] if l in subs), None)
            if not target_lang and subs:
                target_lang = list(subs.keys())[0]
            if target_lang:
                sub_tracks = subs[target_lang]
                best_sub = next((st for st in sub_tracks if st.get("ext") in ["srt", "vtt"]), sub_tracks[0] if sub_tracks else None)
                if best_sub:
                    subtitle_url = best_sub.get("url")

    result = {
        "title": info.get("title", "Unknown Title"),
        "thumbnail_url": info.get("thumbnail", ""),
        "direct_mp4_url": direct_url,
        "file_size_mb": filesize_mb,
        "quality": quality,
        "original_url": url,
        "duration": info.get("duration", 0),
        "available_formats": fmt_info["video_formats"] + fmt_info["audio_formats"],
        "max_merge_height": fmt_info["max_video_only_height"],
        "is_audio_only": is_audio_only,
        "subtitle_url": subtitle_url,
    }

    # Override with Spotify metadata for better title/thumbnail accuracy
    if _spotify_title:
        result["title"] = f"{_spotify_artist} - {_spotify_title}" if _spotify_artist else _spotify_title
    if _spotify_thumbnail:
        result["thumbnail_url"] = _spotify_thumbnail

    # Cache Spotify search_query → YouTube URL for 7 days (avoids repeat YouTube searches)
    if _spotify_search_key:
        try:
            yt_url = info.get("webpage_url", "") or info.get("original_url", "")
            if yt_url and "youtube.com" in yt_url:
                get_redis().setex(_spotify_search_key, 7 * 24 * 3600, yt_url)
                print(f"[Spotify Cache SET] {_spotify_artist} - {_spotify_title}")
        except Exception:
            pass

    # Add local file path logic if a file was downloaded locally
    local_path = info.get("filepath")
    if not local_path and info.get("requested_downloads"):
        local_path = info["requested_downloads"][0].get("filepath")
        
    if local_path and os.path.exists(local_path):
        result["local_file_path"] = local_path
        result["file_size_mb"] = round(os.path.getsize(local_path) / (1024 * 1024), 2)
        # For backward compatibility with the frontend that might expect local_mp3_path
        if local_path.endswith(".mp3") or local_path.endswith(".m4a"):
            result["local_mp3_path"] = local_path

    # Add the actual downloaded video height so frontend knows
    # what quality is already available locally (avoids re-downloading)
    downloaded_height = 0
    if info.get("requested_downloads"):
        dl0 = info["requested_downloads"][0]
        downloaded_height = dl0.get("height") or info.get("height") or 0
    elif info.get("height"):
        downloaded_height = info["height"]
    result["downloaded_height"] = downloaded_height

    # ── YouTube Chapters ─────────────────────────────────────
    # yt-dlp exposes chapters as a list of {title, start_time, end_time} dicts.
    # Only populated for YouTube and a few other platforms that embed chapter markers.
    raw_chapters = info.get("chapters") or []
    chapters = []
    for ch in raw_chapters:
        start = ch.get("start_time", 0)
        end   = ch.get("end_time",   0)
        if end > start:
            chapters.append({
                "title":      ch.get("title", f"Chapter {len(chapters) + 1}"),
                "start_time": round(start, 2),
                "end_time":   round(end,   2),
                "duration":   round(end - start, 2),
            })
    result["chapters"] = chapters

    return result


def extract_video_info_sync(url: str, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False) -> Dict[str, Any]:
    """
    Public entry point with HARD TIMEOUT.
    Wraps the actual extraction in a thread.
    Use a much longer timeout if the quality requires downloading and merging/converting.
    """
    try:
        # All quality modes now download+merge server-side except video_fast
        timeout = EXTRACTION_TIMEOUT_SECONDS if quality == "video_fast" else 600
        return _run_with_timeout(
            _extract_video_info_impl,
            args=(url, quality, remove_watermark, download_subs),
            timeout=timeout,
        )
    except TimeoutError as e:
        print(f"[Downloader] TIMEOUT: {url}")
        raise ValueError(str(e))


async def extract_video_info(url: str, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False) -> Dict[str, Any]:
    """Async wrapper for single video extraction with Redis caching and dedup lock."""
    import json as _json
    import hashlib as _hashlib
    from app.core.redis_client import get_redis

    _CACHEABLE = quality in ("video_fast",)
    _CACHE_TTL = int(os.getenv("VIDEO_INFO_CACHE_TTL", "600"))

    cache_key = None
    lock_acquired = False

    if _CACHEABLE:
        raw_key = f"vidinfo:{url}:{quality}:{int(remove_watermark)}"
        cache_key = "vidcache:" + _hashlib.md5(raw_key.encode()).hexdigest()
        lock_key  = f"lock:{cache_key}"

        try:
            rc = get_redis()
            cached = rc.get(cache_key)
            if cached:
                print(f"[Cache] HIT {cache_key}")
                return _json.loads(cached)

            # Try to acquire extraction lock (60s expiry = hard cap)
            lock_acquired = bool(rc.set(lock_key, "1", nx=True, ex=60))
            if not lock_acquired:
                # Another worker is already extracting this URL — wait for result
                print(f"[Dedup] Waiting for in-flight extraction: {cache_key}")
                for _ in range(60):   # max 30 s
                    await asyncio.sleep(0.5)
                    cached = rc.get(cache_key)
                    if cached:
                        print(f"[Dedup] Got result from in-flight extraction")
                        return _json.loads(cached)
                # Timed out — fall through and extract ourselves (lock may have expired)
                print(f"[Dedup] Wait timeout; proceeding with own extraction")

        except Exception as _ce:
            print(f"[Cache] Redis unavailable: {_ce}")

    try:
        result = await asyncio.to_thread(extract_video_info_sync, url, quality, remove_watermark, download_subs)
    finally:
        # Always release the lock after extraction (success or failure)
        if lock_acquired and cache_key:
            try:
                get_redis().delete(f"lock:{cache_key}")
            except Exception:
                pass

    if _CACHEABLE and cache_key and result:
        try:
            rc = get_redis()
            if not result.get("local_file_path") and not result.get("local_mp3_path"):
                rc.setex(cache_key, _CACHE_TTL, _json.dumps(result))
                print(f"[Cache] SET {cache_key} TTL={_CACHE_TTL}s")
        except Exception as _ce2:
            print(f"[Cache] Failed to store: {_ce2}")

    return result


# ── Channel / Playlist Scraping ──────────────────────────────────────


def _scrape_douyin_channel(channel_url: str, max_videos: int = 20) -> Dict[str, Any]:
    """
    Dedicated Douyin channel/user scraper.
    Douyin's anti-bot JS VM prevents yt-dlp from scraping user profiles.
    
    Strategy:
      1. Extract sec_uid from the URL
      2. Use ScraperAPI (render=true, country_code=cn) to render the JS page
      3. Parse RENDER_DATA or regex-extract video IDs from the rendered HTML
      4. Return video URLs for the existing single-video pipeline
    """
    print(f"[Downloader] Using dedicated Douyin channel scraper for: {channel_url}")

    # Extract sec_uid from URL
    sec_uid_match = re.search(r'/user/([A-Za-z0-9_-]+)', channel_url)
    if not sec_uid_match:
        raise ValueError("Không thể xác định sec_uid từ URL Douyin. Vui lòng dùng link dạng: douyin.com/user/...")

    sec_uid = sec_uid_match.group(1)
    canonical_url = f"https://www.douyin.com/user/{sec_uid}"

    # ── Method 0: Apify cloud scraper (best reliability, needs APIFY_TOKEN) ──
    if os.getenv("APIFY_TOKEN", ""):
        print(f"[Douyin Channel] Trying Apify for {canonical_url}")
        try:
            from app.services.apify_service import scrape_douyin_user_apify_sync
            result = scrape_douyin_user_apify_sync(channel_url, max_videos=max_videos)
            if result and result.get("total_queued", 0) > 0:
                print(f"[Douyin Channel] Apify returned {result['total_queued']} videos")
                return result
        except Exception as apify_err:
            print(f"[Douyin Channel] Apify failed, falling back: {apify_err}")

    # ── Method 1: ScraperAPI with JS rendering ──────────────
    from app.core.scraperapi_pool import get_active_key as _sa_key
    scraperapi_key = _sa_key()
    video_ids = []

    if scraperapi_key:
        print(f"[Douyin Channel] Trying ScraperAPI render for {canonical_url}")
        try:
            resp = None
            with httpx.Client(timeout=60.0) as client:
                resp = client.get(
                    "http://api.scraperapi.com/",
                    params={
                        "api_key": scraperapi_key,
                        "url": canonical_url,
                        "render": "true",
                        "country_code": "cn",
                        "wait_for_selector": ".video-card",
                    },
                )

            if resp and resp.status_code == 200:
                html = resp.text
                print(f"[Douyin Channel] ScraperAPI returned {len(html)} bytes")

                # Extract video IDs from rendered page
                # Pattern 1: /video/XXXXXXXXXXX links
                found_ids = re.findall(r'/video/(\d{15,25})', html)
                # Pattern 2: aweme_id in JSON data
                aweme_ids = re.findall(r'"aweme_id"\s*:\s*"(\d{15,25})"', html)
                # Pattern 3: From data attributes or href
                href_ids = re.findall(r'href="[^"]*?/video/(\d{15,25})', html)

                all_ids = found_ids + aweme_ids + href_ids
                # Deduplicate while preserving order
                seen = set()
                for vid in all_ids:
                    if vid not in seen:
                        seen.add(vid)
                        video_ids.append(vid)

                print(f"[Douyin Channel] Found {len(video_ids)} unique video IDs via ScraperAPI")
            else:
                print(f"[Douyin Channel] ScraperAPI returned status {resp.status_code if resp else 'None'}")
        except Exception as e:
            print(f"[Douyin Channel] ScraperAPI error: {e}")

    # ── Method 2: iesdouyin share user page (free fallback) ──
    if not video_ids:
        print(f"[Douyin Channel] Trying iesdouyin share user page fallback")
        try:
            share_url = f"https://www.iesdouyin.com/share/user/{sec_uid}/"
            mobile_ua = (
                "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Mobile Safari/537.36"
            )
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(share_url, headers={
                    "User-Agent": mobile_ua,
                    "Accept-Language": "zh-CN,zh;q=0.9",
                })
                if resp.status_code == 200:
                    html = resp.text
                    found_ids = re.findall(r'/video/(\d{15,25})', html)
                    aweme_ids = re.findall(r'"aweme_id"\s*:\s*"(\d{15,25})"', html)
                    all_ids = found_ids + aweme_ids
                    seen = set()
                    for vid in all_ids:
                        if vid not in seen:
                            seen.add(vid)
                            video_ids.append(vid)
                    print(f"[Douyin Channel] iesdouyin found {len(video_ids)} video IDs")
        except Exception as e:
            print(f"[Douyin Channel] iesdouyin fallback error: {e}")

    if not video_ids:
        raise ValueError(
            "Không thể quét kênh Douyin. Douyin sử dụng hệ thống chống bot rất mạnh "
            "(JS VM + Captcha) khiến việc quét danh sách video từ trang cá nhân bị chặn. "
            "Vui lòng copy từng link video riêng lẻ và dán vào ô nhập liệu."
        )

    # Limit to max_videos
    video_ids = video_ids[:max_videos]

    entries = []
    for vid in video_ids:
        entries.append({
            "url": f"https://www.douyin.com/video/{vid}",
            "title": f"Douyin Video {vid[-6:]}",
        })

    return {
        "channel_title": f"Douyin User {sec_uid[:12]}...",
        "entries": entries,
        "total_found": len(video_ids),
        "total_queued": len(entries),
    }


def _scrape_channel_entries_impl(channel_url: str, max_videos: int = 100, min_views: int = 0) -> Dict[str, Any]:
    """
    Scrape a channel or playlist URL to get a flat list of video entries
    WITHOUT downloading or fully processing each video.
    Filters by view_count and limits to max_videos.
    
    NOTE: process=True is REQUIRED for YouTube channels to trigger 
    InnerTube continuation token pagination. With process=False, 
    yt-dlp only returns the first ~20 items from the initial page load.
    """
    # ── Step 0: Unshorten short links ────────────────────────
    original_channel_url = channel_url
    channel_url = resolve_short_url(channel_url)
    if channel_url != original_channel_url:
        print(f"[Downloader] Unshortened channel URL: {original_channel_url} -> {channel_url}")

    # ── Douyin: Route to dedicated scraper ────────────────────
    if is_douyin_url(channel_url) or is_douyin_url(original_channel_url):
        return _scrape_douyin_channel(channel_url, max_videos)

    # Clean TikTok URLs to avoid yt-dlp extraction errors caused by tracking params
    if "tiktok.com" in channel_url.lower() and "?" in channel_url:
        channel_url = channel_url.split("?")[0]

    opts = _get_base_opts(channel_url, phase="metadata")
    # extract_flat gives us the video list without resolving each video's streams
    opts["extract_flat"] = "in_playlist"
    # Limit how many entries yt-dlp fetches to avoid excessive API calls
    # playlistend caps pagination so we don't fetch thousands of videos
    opts["playlistend"] = max_videos + 50  # fetch extra to allow for view filtering
    opts["ignoreerrors"] = True  # skip private/deleted videos without crashing
    opts = _apply_tiktok_opts(opts, channel_url)

    info = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            # process=True is CRITICAL: it triggers YouTube InnerTube continuation
            # token pagination. Without it, only the first ~20 videos are returned.
            info = ydl.extract_info(channel_url, download=False, process=True)
    except Exception as extract_err:
        print(f"[Downloader] Channel extraction error (continuing with partial): {extract_err}")

    if info is None:
        import tempfile
        print(f"[Downloader] Channel extraction failed, trying Smart Proxy Dispatcher fallback for {channel_url}")
        from app.core.proxy_manager import dispatch_scraping_request
        html_content = asyncio.run(dispatch_scraping_request(channel_url))
        
        if html_content:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(html_content)
                    tmp_path = f.name
                
                fallback_opts = _get_base_opts(channel_url, phase="download")  # no proxy for API
                fallback_opts["extract_flat"] = "in_playlist"
                fallback_opts["enable_file_urls"] = True
                fallback_opts["playlistend"] = max_videos + 50
                fallback_opts = _apply_tiktok_opts(fallback_opts, channel_url)

                # Fix for Windows paths in yt-dlp
                file_url = f"file:///{tmp_path.replace(chr(92), '/')}"
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(file_url, download=False, process=True)
                    
            except Exception as fallback_err:
                print(f"[Downloader] Scraping API fallback parse failed: {fallback_err}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except: pass

    if info is None:
        raise ValueError("yt-dlp could not extract any information from this channel. Try again later or use direct links.")

    entries = []
    channel_title = info.get("title") or info.get("uploader") or "Unknown Channel"

    # yt-dlp returns entries as a generator or list depending on process flag
    raw_entries = info.get("entries", [])
    # If it's a generator, convert to list (needed for counting)
    if not isinstance(raw_entries, list):
        raw_entries = list(raw_entries)

    total_found = 0
    total_queued = 0

    for entry in raw_entries:
        if entry is None:
            continue
            
        total_found += 1
        
        # Stop if we hit the limit
        if total_queued >= max_videos:
            break
            
        # Filter by min_views (defaults to 0 if not available)
        view_count = entry.get("view_count")
        if view_count is None:
            view_count = 0
            
        if view_count < min_views:
            continue

        video_url = entry.get("url") or entry.get("webpage_url") or ""
        video_title = entry.get("title") or "Untitled"

        # yt-dlp sometimes returns just the video ID for YouTube
        if video_url and not video_url.startswith("http"):
            video_url = f"https://www.youtube.com/watch?v={video_url}"

        if video_url:
            entries.append({
                "url": video_url,
                "title": video_title,
            })
            total_queued += 1

    print(f"[Downloader] Channel '{channel_title}': found {total_found} videos, queued {total_queued} (max={max_videos}, min_views={min_views})")

    return {
        "channel_title": channel_title,
        "entries": entries,
        "total_found": total_found,
        "total_queued": total_queued
    }


def scrape_channel_entries_sync(channel_url: str, max_videos: int = 100, min_views: int = 0) -> Dict[str, Any]:
    """
    Public entry point with HARD TIMEOUT for channel scraping.
    Wraps the actual scraping in a thread with a timeout cap.
    YouTube pagination for large channels may need 60-90s.
    """
    try:
        # Douyin channels need longer timeout due to ScraperAPI JS rendering
        is_douyin = "douyin.com" in channel_url.lower()
        timeout = 90 if is_douyin else 120  # YouTube pagination needs more time for large channels
        return _run_with_timeout(
            _scrape_channel_entries_impl,
            args=(channel_url, max_videos, min_views),
            timeout=timeout,
        )
    except TimeoutError as e:
        print(f"[Downloader] CHANNEL TIMEOUT: {channel_url}")
        raise ValueError(str(e))


async def scrape_channel_entries(channel_url: str, max_videos: int = 100, min_views: int = 0) -> Dict[str, Any]:
    """Async wrapper for channel scraping."""
    return await asyncio.to_thread(scrape_channel_entries_sync, channel_url, max_videos, min_views)


# ── URL Classification Helper ───────────────────────────────────────

def classify_url(url: str) -> str:
    """
    Classify a URL as 'channel' or 'video'.
    Used by the API to decide the processing pipeline.
    """
    return "channel" if _is_channel_or_playlist(url) else "video"
