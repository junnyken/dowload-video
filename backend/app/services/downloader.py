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
from typing import Dict, Any, List, Optional
import httpx

# ── Platform cookies ─────────────────────────────────────────────────
# Source priority: Redis cookie pool → env var (single cookie fallback)
# Pool: add via POST /admin/cookies/add  (multiple accounts, auto-rotate)
# Env:  YOUTUBE_COOKIES_B64, TIKTOK_COOKIES_B64, etc. (single fallback)
_INSTAGRAM_COOKIES_B64 = os.getenv("INSTAGRAM_COOKIES_B64", "")
_YOUTUBE_COOKIES_B64   = os.getenv("YOUTUBE_COOKIES_B64", "")
_TIKTOK_COOKIES_B64    = os.getenv("TIKTOK_COOKIES_B64", "")
_FACEBOOK_COOKIES_B64  = os.getenv("FACEBOOK_COOKIES_B64", "")
_TWITTER_COOKIES_B64   = os.getenv("TWITTER_COOKIES_B64", "")

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


def _rotate_cookie(platform: str, hard: bool = False) -> None:
    """
    Mark current cookie soft/hard-blocked, clear local cache.
    hard=False (default): 429/rate-limit → 15 min block
    hard=True: challenge/suspended → 6 h block
    Next request picks a fresh cookie via LRU selection.
    """
    b64 = _active_cookie_b64.get(platform)
    if b64:
        try:
            from app.core.cookie_pool import mark_cookie_blocked
            mark_cookie_blocked(platform, b64, hard=hard)
        except Exception:
            pass
    _cookies_cache.pop(platform, None)
    _active_cookie_b64.pop(platform, None)
    label = "hard" if hard else "soft"
    print(f"[Cookies] Rotated {platform} cookie ({label} block)")


def _get_instagram_cookies_file() -> str | None:
    return _get_cookies_file("instagram", _INSTAGRAM_COOKIES_B64)

def _get_youtube_cookies_file() -> str | None:
    return _get_cookies_file("youtube", _YOUTUBE_COOKIES_B64)

def _get_tiktok_cookies_file() -> str | None:
    return _get_cookies_file("tiktok", _TIKTOK_COOKIES_B64)

def _get_facebook_cookies_file() -> str | None:
    return _get_cookies_file("facebook", _FACEBOOK_COOKIES_B64)

def _get_twitter_cookies_file() -> str | None:
    return _get_cookies_file("twitter", _TWITTER_COOKIES_B64)

_INSTAGRAM_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
)


class _YTDLPLogger:
    """Captures yt-dlp warnings/errors regardless of quiet/no_warnings settings.

    Every error line is also appended to `errors` (optionally a caller-owned
    list, so several YoutubeDL instances in one request share one sink). That
    list is the ONLY way a failure's real reason survives: base opts set
    `ignoreerrors`, so extract_info returns None instead of raising, and
    without a sink the reason reaches neither the user nor the log line that
    claims to report it.

    verbose=False keeps the routine screen chatter out of the log — attaching a
    logger bypasses `quiet`, and 8 workers narrating every download would bury
    the warnings worth reading. Warnings and errors always print.
    """
    def __init__(self, prefix: str = "", sink: list | None = None,
                 verbose: bool = True):
        self._prefix = prefix
        self._verbose = verbose
        self.errors: list = sink if sink is not None else []

    def debug(self, msg: str) -> None:
        if self._verbose and not msg.startswith("[debug] "):
            print(f"[yt-dlp{self._prefix}] {msg}")

    def info(self, msg: str) -> None:
        if self._verbose:
            print(f"[yt-dlp{self._prefix}] {msg}")

    def warning(self, msg: str) -> None:
        print(f"[yt-dlp{self._prefix} WARN] {msg}")

    def error(self, msg: str) -> None:
        text = str(msg).strip()
        if text:
            self.errors.append(text)
        print(f"[yt-dlp{self._prefix} ERROR] {msg}")


# Platforms whose extraction chatter is worth printing in full. YouTube has
# always logged this way (client fallbacks and PO-token trouble are read from
# these lines); everything else logs warnings and errors only.
_VERBOSE_LOG_PLATFORMS = ("youtube.com", "youtu.be", "ytsearch")


def _ytdlp_log_prefix(url: str) -> tuple[str, bool]:
    """Return (log prefix, verbose) for a target URL."""
    u = (url or "").lower()
    for name, tag in (
        ("youtube.com", "/YT"), ("youtu.be", "/YT"), ("ytsearch", "/YT"),
        ("facebook.com", "/FB"), ("fb.watch", "/FB"),
        ("instagram.com", "/IG"),
        ("tiktok.com", "/TT"), ("douyin.com", "/DY"),
        ("twitter.com", "/X"), ("x.com", "/X"),
        ("threads.net", "/TH"), ("threads.com", "/TH"),
    ):
        if name in u or u.startswith(name):
            return tag, any(v in u or u.startswith(v) for v in _VERBOSE_LOG_PLATFORMS)
    return "", False


# Trailing boilerplate yt-dlp appends to extractor errors. Useful to a
# maintainer reading a bug report, noise in a message shown to a user.
_YTDLP_NOISE_RE = re.compile(
    r"\s*;?\s*(please report this issue.*|Confirm you are on the latest version.*"
    r"|you might want to use.*|Use --.*)$",
    re.IGNORECASE | re.DOTALL,
)


def _ytdlp_failure_reason(errors: list | None) -> str:
    """Condense captured yt-dlp errors into one short user-facing reason.

    Returns "" when nothing was captured — callers must not print an empty
    parenthetical in that case.
    """
    if not errors:
        return ""
    for raw in reversed(errors):
        text = re.sub(r"\x1b\[[0-9;]*m", "", str(raw)).strip()
        text = re.sub(r"^ERROR:\s*", "", text).strip()
        text = _YTDLP_NOISE_RE.sub("", text).strip(" .;")
        # A bare "[facebook] 123: " with the reason stripped off helps nobody.
        if text and not re.fullmatch(r"\[[^\]]+\][\s:]*\S*[\s:]*", text):
            return text if len(text) <= 300 else text[:297] + "..."
    return ""


_impersonate_probe: list = []  # cache: [] = not probed, [None] = unavailable


def _impersonate_target():
    """A Chrome impersonation target yt-dlp can actually use, or None.

    Facebook increasingly answers a plain urllib request with a page yt-dlp
    cannot parse, while serving the video to a real browser TLS fingerprint.

    "curl_cffi imports" is NOT the check: yt-dlp accepts only curl_cffi 0.5.10
    and 0.10.x–0.14.x and refuses to load its impersonate handler for anything
    else, leaving zero available targets — and YoutubeDL then raises
    "Impersonate target ... is not available" from its CONSTRUCTOR, before a
    single request goes out. (Measured: curl_cffi 0.15.0 installed, targets
    available: 0.) So ask yt-dlp what it supports instead of inferring it.
    """
    if _impersonate_probe:
        return _impersonate_probe[0]

    target = None
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        chrome = ImpersonateTarget("chrome")
        probe = yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True})
        try:
            if probe._impersonate_target_available(chrome):
                target = chrome
        finally:
            probe.close()
    except Exception as _imp_err:
        print(f"[Downloader] Impersonation unavailable: {str(_imp_err)[:120]}")
        target = None

    if target is None:
        print("[Downloader] Impersonation unavailable — check the curl_cffi "
              "version bound in requirements.txt")
    _impersonate_probe.append(target)
    return target


def _facebook_retry_plan(opts: dict) -> list:
    """Attempts to make after Facebook's first extraction comes back empty.

    Returns [(label, opts), ...] — each entry a full opts dict ready to hand to
    YoutubeDL, ordered cheapest-first. Facebook is the one platform here with
    no fallback provider (Cobalt is optional and usually not deployed), so
    "first attempt failed" used to mean "user gets an error", even when a
    stale pool cookie or a missing browser fingerprint was the only problem.
    """
    plan = []
    has_cookie = bool(opts.get("cookiefile"))
    target = _impersonate_target()

    if has_cookie:
        # A cookie that is expired, checkpointed, or from a suspended account
        # turns a public video into a login wall. Anonymous is what the rest of
        # the internet gets, and it works for public content.
        no_cookie = dict(opts)
        no_cookie.pop("cookiefile", None)
        plan.append(("không dùng cookie", no_cookie))

    if target is not None:
        impersonated = dict(opts)
        impersonated["impersonate"] = target
        plan.append(("giả lập trình duyệt Chrome", impersonated))
        if has_cookie:
            both = dict(impersonated)
            both.pop("cookiefile", None)
            plan.append(("giả lập Chrome + không cookie", both))

    return plan

from app.core.proxy_manager import get_proxy_config_for_phase, dispatch_scraping_request, get_scraperapi_proxy_url
from app.core.redis_client import get_redis
from app.utils.link_resolver import resolve_short_url, is_douyin_url


def _make_progress_hook(progress_key: str):
    """Return a yt-dlp progress hook that streams download progress to Redis."""
    import json as _json

    def _hook(d: dict) -> None:
        try:
            rc = get_redis()
            status = d.get("status", "unknown")
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0

            if status == "finished":
                pct = 100.0
            elif total > 0:
                pct = round(downloaded / total * 100, 1)
            else:
                pct = 0.0

            rc.setex(progress_key, 300, _json.dumps({
                "status": status,
                "percent": pct,
                "speed_kbps": round((speed or 0) / 1024, 1),
                "eta_seconds": int(eta or 0),
                "total_bytes": total,
                "downloaded_bytes": downloaded,
            }))
        except Exception:
            pass

    return _hook
from app.services.douyin_extractor import extract_douyin_video_sync, _try_tikwm
from app.services.threads_extractor import (
    is_threads_url, is_threads_post_url, is_threads_share_url,
    extract_threads_sync, to_download_info,
)
from app.services.cobalt_service import is_cobalt_available, extract_youtube_formats_via_cobalt, download_from_cobalt, download_instagram_via_cobalt, download_facebook_via_cobalt, fetch_cobalt_stream

# Ensure Deno is discoverable for yt-dlp JS challenges
_deno_bin = os.path.join(os.path.expanduser("~"), ".deno", "bin")
if _deno_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _deno_bin + os.pathsep + os.environ.get("PATH", "")

# Add a directory for temporary downloads
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def _convert_vtt_to_srt(vtt_path: str, srt_path: str) -> None:
    """Simple WebVTT → SRT conversion (strips header, converts timestamp format)."""
    try:
        with open(vtt_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        content = re.sub(r'^WEBVTT[^\n]*\n+', '', content)
        content = re.sub(r'NOTE[^\n]*\n[^\n]*\n+', '', content)
        content = re.sub(r'(\d{2}:\d{2}:\d{2})\.(\d{3})', r'\1,\2', content)
        content = re.sub(r'<[^>]+>', '', content)
        blocks = [b.strip() for b in content.split('\n\n') if b.strip() and '-->' in b]
        lines: list[str] = []
        for i, block in enumerate(blocks, 1):
            lines.append(str(i))
            lines.append(block)
            lines.append('')
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write('\n'.join(lines))
    except Exception as e:
        print(f"[Subtitles] VTT→SRT conversion failed: {e}")


def _find_subtitle_file(video_path: str) -> str | None:
    """Locate the subtitle file written by yt-dlp alongside a downloaded video."""
    import glob as _glob
    base = os.path.splitext(video_path)[0]
    priority_langs = ["vi", "vi-VN", "vi-VIE", "en", "en-US"]
    for lang in priority_langs:
        for ext in ("srt", "vtt"):
            path = f"{base}.{lang}.{ext}"
            if os.path.exists(path):
                if ext == "vtt":
                    srt = f"{base}.{lang}.srt"
                    _convert_vtt_to_srt(path, srt)
                    try: os.remove(path)
                    except: pass
                    return srt if os.path.exists(srt) else None
                return path
    for pattern in (f"{base}.*.srt", f"{base}.*.vtt"):
        matches = sorted(_glob.glob(pattern))
        if matches:
            p = matches[0]
            if p.endswith(".vtt"):
                srt = p[:-4] + ".srt"
                _convert_vtt_to_srt(p, srt)
                try: os.remove(p)
                except: pass
                return srt if os.path.exists(srt) else None
            return p
    return None

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
    # NOTE: do NOT use `with ThreadPoolExecutor()` here — its __exit__ calls
    # shutdown(wait=True), which BLOCKS until the worker finishes. A hung
    # yt-dlp/network call would then keep the whole call alive far past
    # `timeout` (the TimeoutError can't propagate until the thread returns).
    # Manage the executor manually and shut it down WITHOUT waiting so the
    # timeout is actually enforced; the orphaned thread dies on its own.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(
            f"Quá thời gian chờ ({timeout}s). "
            "Link có thể bị chặn bởi captcha hoặc server phản hồi chậm. "
            "Vui lòng thử lại sau."
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


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


def _bgutil_extractor_args() -> dict:
    """
    PO token provider configuration, decided in one place.

    BGUTIL_POT_URL selects the HTTP provider — that is the docker-compose
    layout, where a `bgutil-pot` service really is running. With no URL set,
    use the script provider baked into the image, which generates tokens
    locally under Deno with no service to deploy or reach.

    This used to be built inline in three places, each defaulting to
    "http://bgutil-pot:4416" — a compose-only hostname that cannot resolve on a
    split-project deployment. It produced no token and no error, which is why
    every android_vr download came back 403.
    """
    base = os.getenv("BGUTIL_POT_URL", "").split(",")[0].strip()
    if base:
        return {"youtubepot-bgutilhttp": {"base_url": [base]}}
    home = os.getenv("BGUTIL_POT_HOME", "/opt/bgutil-pot/server")
    return {"youtubepot-bgutilscript": {"server_home": [home]}}


# The first client Phase A asks, direct and PO-token-free.
#
# This was android_vr, which measured ZERO adaptive streams on every video
# tried — five of five, across a vertical Short, two music videos, a 4K video
# and a 2005 upload — and is the only client that offers format 18 at all. It
# therefore lost every race it entered and cost a network round trip before
# visionos rescued each request. visionos returned the full ladder on all five,
# up to whatever the video actually has (4K where 4K exists, 240p on the 2005
# upload, which is genuinely all that was ever uploaded).
#
# android_vr is not deleted, only demoted: it is first in the fallback list, so
# whichever of the two a given video prefers, both are still tried, free and
# direct, before anything that costs money.
_YT_PRIMARY_CLIENT = "visionos"

# Tried in this order, direct, when the primary answers without adaptive
# formats. All are PO-token-free and cost nothing — no proxy, no ScraperAPI —
# so this runs before any of the paid layers.
#
# Measured against the Short that exposed this, from a datacenter IP:
#
#     client         adaptive streams   best resolution   format 18 offered
#     android_vr     0                  —                 yes
#     visionos       29                 1080x1920         no
#     ios            0                  —                 no
#     tv             0                  —                 no
#     web_safari     0                  —                 no
#     web            0                  —                 no
#     mweb           0                  —                 no
#     web_embedded   0                  —                 no
#     android        0                  —                 no
#
# Worth stating plainly: web_safari is what the bgutil PO-token layer uses, and
# it returned nothing here either. Letting the existing chain run past a
# degraded android_vr result was necessary but NOT sufficient — every later
# layer comes back empty and restores the same 360p. visionos was the only
# client of the nine that saw the adaptive streams at all.
#
# None means "let yt-dlp choose", kept as a last attempt because its default
# client set moves with each release: an unpinned run picked visionos by itself
# here, so if visionos is ever blocked the defaults are the next best guess.
_YT_ADAPTIVE_FALLBACK_CLIENTS = ("android_vr", None)


def _yt_extraction_is_degraded(info: dict | None) -> bool:
    """
    True when a YouTube extraction came back with no adaptive video streams.

    A healthy YouTube extraction always exposes DASH video-only formats — that
    is where every resolution above 360p lives. When a client is answered
    without them, the only thing left that carries video AND audio is the
    legacy progressive format 18, which is 360p. The format selector then falls
    all the way down its chain to `best[ext=mp4]` and picks it.

    That is exactly what a Short came back as: quality video_4320 requested,
    downloaded_height 640, file kGWFwVWwJYU_18.mp4, 0.52 MB for 13 seconds.
    Not a height cap — the cap was 4320 — the formats simply were not offered.

    Deliberately tested on the presence of adaptive streams rather than on
    height: a Short is vertical, so format 18 reports height 640 and sails past
    any "height looks too low" check.
    """
    fmts = (info or {}).get("formats") or []
    if not fmts:
        return True
    has_adaptive_video = any(
        f.get("vcodec") and f.get("vcodec") != "none"
        and f.get("acodec") in (None, "none")
        for f in fmts
    )
    return not has_adaptive_video


def _youtube_proxy_download_enabled() -> bool:
    """
    Whether YouTube video bytes may be downloaded THROUGH the residential proxy.

    YouTube CDN URLs are signed for the IP that extracted them. The datacenter
    (Oracle) IP is bot-blocked, so Phase A must extract via the residential
    proxy and the resulting URLs only serve to that proxy exit IP. When this is
    ON, Phase B fetches the bytes through that same proxy (uses paid per-GB
    bandwidth) — the ONLY way YouTube works from a blocked datacenter IP.
    Default OFF (metadata-only / cost-safe); flip YOUTUBE_PROXY_DOWNLOAD=1 to
    re-enable YouTube.
    """
    return os.getenv("YOUTUBE_PROXY_DOWNLOAD", "0").strip().lower() in (
        "1", "true", "yes", "on"
    )


def _get_base_opts(url: str, phase: str = "metadata", quality: str = "video",
                   error_sink: list | None = None) -> dict:
    """
    Return base yt-dlp options with PHASE-AWARE proxy selection.

    Args:
        url:     The target video/channel URL.
        phase:   "metadata" -> proxy if needed; "download" -> server IP.
        quality: "video" (no-watermark), "video_4k" (best merge), "mp3_128", "mp3_320".
        error_sink: list that collects every yt-dlp error line from the
                 YoutubeDL built with these opts. Pass the SAME list to every
                 _get_base_opts call in one request so the failure reason
                 survives whichever attempt produced it.
    """
    if quality == "video_4k":
        # 4K/2K: request highest quality video+audio, merge with FFmpeg.
        # PRIORITISE H.264 (avc1) + AAC (mp4a) so the merged .mp4 opens in
        # QuickTime / Apple devices. VP9/AV1 (webm) is only a last resort —
        # at 4K it's often the only option, but lower res stays Apple-friendly.
        fmt = (
            "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]"
            "/bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]"
            "/bestvideo[ext=webm]+bestaudio[ext=webm]"
            "/bestvideo+bestaudio/best"
        )
    elif quality.startswith("video_") and quality != "video":
        # Specific resolution merge, e.g., video_1080
        # Fallback chain: H.264/AAC at target height (QuickTime-safe) →
        # any mp4 at height → progressive mp4 → VP9/webm (last resort).
        try:
            height = int(quality.split("_")[1])
            fmt = (
                # Portrait first. "1080p" names the SHORTER side — a 1080x1920
                # Short is what everyone calls a 1080p vertical video, and
                # yt-dlp labels it that way too. Filtering on height alone is
                # technically correct and semantically wrong there: height is
                # the LONG side, so height<=1080 rejects the 1080x1920 stream
                # and settles for 480x854. Measured on a real Short: asking for
                # 1080p returned 0.97 MB at 480x854 while "best" returned
                # 3.15 MB at 1080x1920 — the higher-sounding choice gave the
                # worse picture, which is exactly the complaint that started
                # this whole thread.
                #
                # The height>N clause keeps this branch portrait-only, so
                # landscape falls straight through to the height rules below
                # and behaves exactly as before.
                f"bestvideo[width<={height}][height>{height}][vcodec^=avc1]+bestaudio[acodec^=mp4a]"
                f"/bestvideo[width<={height}][height>{height}][ext=mp4]+bestaudio[ext=m4a]"
                f"/bestvideo[height<={height}][vcodec^=avc1]+bestaudio[acodec^=mp4a]"
                f"/bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]"
                f"/best[height<={height}][ext=mp4]"
                f"/bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]"
                f"/bestvideo[height<={height}][ext=webm]+bestaudio[ext=webm]"
                f"/bestvideo[height<={height}]+bestaudio"
                f"/bestvideo+bestaudio"
                f"/best[height<={height}]"
            )
        except:
            fmt = "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo[ext=webm]+bestaudio[ext=webm]/bestvideo+bestaudio/best"
    elif quality.startswith("mp3"):
        # Audio only extraction
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
    elif quality == "video_fast":
        # Fast mode: pre-merged only (no FFmpeg merge needed) — lower quality but instant
        # This is the OLD "video" behavior, kept for backward compatibility
        fmt = "b[ext=mp4]/best[ext=mp4]/best"
    else:
        # Default "video" quality == the "HD" pill in the extension/web UI,
        # which explicitly promises "MP4 1080p". This used to be byte-for-byte
        # identical to the "video_4k" selector below (fully uncapped
        # bestvideo+bestaudio) — so picking "HD" silently downloaded whatever
        # the source's true best was (4K, 8K, ...) instead of the promised
        # 1080p, and the "HD" vs "4K" pills had no actual effect on each
        # other. Capped at height<=1080 to match the label.
        # PRIORITISE H.264 (avc1) + AAC (mp4a) so the merged .mp4 plays in
        # QuickTime / Apple devices; only fall back to VP9/webm if AVC is
        # genuinely unavailable (otherwise QuickTime rejects the file).
        fmt = (
            "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[acodec^=mp4a]"
            "/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]"
            "/best[height<=1080][ext=mp4]"
            "/bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]"
            "/bestvideo[height<=1080][ext=webm]+bestaudio[ext=webm]"
            "/bestvideo[height<=1080]+bestaudio"
            "/bestvideo+bestaudio"
            "/best[height<=1080]"
        )

    opts = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "no_color": True,
        # Metadata calls should fail fast (bounds the orphan thread that a
        # timed-out _run_with_timeout leaves behind); downloads need longer reads.
        "socket_timeout": 30 if phase == "metadata" else 60,
        "retries": 10,
        "fragment_retries": 10,
        "keepvideo": False,
        "nopart": False,          # allow .part files (default)
        "concurrent_fragment_downloads": 4,  # parallel fragment download — faster
        "noplaylist": True,       # single video only — ignore list=/radio params to prevent OOM
    }

    # Every platform gets a logger, not just YouTube. `ignoreerrors` above means
    # a failed extraction returns None WITHOUT raising, so the except-branch that
    # logs the reason never runs — the logger is what makes the reason readable
    # at all, and `error_sink` is what carries it back to the caller.
    _log_prefix, _log_verbose = _ytdlp_log_prefix(url)
    opts["logger"] = _YTDLPLogger(_log_prefix, sink=error_sink, verbose=_log_verbose)
    
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

    # ── Proxy: METADATA-ONLY by design (EXCEPT YouTube, see below) ──────
    # The (paid, per-GB) proxy is used ONLY to extract metadata for every other
    # platform. Video bytes are NEVER tunneled through it — a single video would
    # burn the whole plan. The download phase goes direct from the server IP.
    #
    # YouTube is the one exception (Phase 8): the Oracle IP is bot-blocked AND
    # YouTube CDN URLs are IP-locked to the proxy exit that extracted them, so
    # the bytes MUST flow through the same residential proxy or YouTube can't
    # work at all. This is gated hard: YOUTUBE_PROXY_DOWNLOAD on + the quality
    # tier must be proxy-allowed (4K never). Cost/feature/circuit guards live in
    # routes (youtube_gate.preflight) — this block only attaches the proxy.
    _url_l = url.lower()
    _is_youtube_url = ("youtube.com" in _url_l or "youtu.be" in _url_l
                       or _url_l.startswith("ytsearch"))
    proxy = None
    if phase != "download":
        try:
            from app.core.proxy_pool import get_proxy_from_pool
            _platform = (
                "youtube"   if _is_youtube_url else
                "tiktok"    if "tiktok.com"   in url else
                "facebook"  if "facebook.com" in url or "fb.watch" in url else
                "instagram" if "instagram.com" in url else
                "douyin"    if "douyin.com"   in url else
                "twitter"   if "twitter.com"  in url or "x.com" in url else
                "reddit"    if "reddit.com"   in url or "redd.it" in url else
                "default"
            )
            proxy = get_proxy_from_pool(_platform)
        except Exception:
            pass
        if not proxy:
            proxy = get_proxy_config_for_phase(url, phase=phase)
    elif _is_youtube_url and _youtube_proxy_download_enabled():
        # Phase B: YouTube bytes through the residential proxy — tier-gated.
        # CDN URLs are IP-locked to the exit used for metadata extraction.
        # PROXY_POOL_YT_STATIC (ISP/static residential) is preferred because it
        # gives a stable single exit IP. Falls back to IPROYAL_PROXY (rotating
        # residential) which may break CDN lock if IPs rotate between requests.
        try:
            from app.core.youtube_gate import tier_decision
            if tier_decision(quality).get("allow"):
                from app.core.proxy_pool import get_proxy_from_pool
                from app.core.proxy_manager import IPROYAL_PROXY
                _yt_static = os.getenv("PROXY_POOL_YT_STATIC", "")
                proxy = _yt_static or get_proxy_from_pool("youtube") or IPROYAL_PROXY or None
                if proxy:
                    _src = "ISP-static" if _yt_static else "rotating-residential"
                    print(f"[Downloader] YouTube Phase B: bytes via {_src} proxy "
                          f"(quality={quality})")
        except Exception as _yp_err:
            print(f"[Downloader] YouTube proxy-download gate error: {_yp_err}")
    if proxy:
        opts["proxy"] = proxy

    # YouTube metadata: also prefer PROXY_POOL_YT_STATIC for CDN URL lock consistency
    _yt_static_meta = os.getenv("PROXY_POOL_YT_STATIC", "")
    if _is_youtube_url and _yt_static_meta and phase != "download":
        opts["proxy"] = _yt_static_meta
        print("[Downloader] YouTube metadata via PROXY_POOL_YT_STATIC (ISP/static)")

    _is_youtube = (
        "youtube.com" in url.lower()
        or "youtu.be" in url.lower()
        or url.lower().startswith("ytsearch")  # Spotify → ytsearch1:track name
    )
    if _is_youtube:
        # (the logger is attached for every platform where opts are built —
        # this branch used to be the only place it happened)
        # yt-dlp 2025.5+: use node for JS challenges + enable EJS remote solver script
        opts["js_runtimes"] = {"node": {}}
        opts["remote_components"] = ["ejs:github"]
        # Cache EJS solver + extractor data on disk — avoids re-downloading through proxy each request
        opts["cachedir"] = "/tmp/ytdlp_cache"
        # Prioritize resolution, then codec compatibility, then bitrate
        opts["format_sort"] = ["res", "ext:mp4:m4a", "tbr", "vbr", "abr", "asr"]

        # PO token provider.
        #
        # The comment here used to claim android_vr needs no PO token. Measured
        # against production, that is not true for these URLs: extraction
        # succeeded and every byte fetch came back 403, for 4K and 720p alike,
        # with the server's own IP matching the ip= signed into the link.
        #
        # Two ways to supply one. BGUTIL_POT_URL points at a running provider
        # service — the docker-compose layout, kept working here. Otherwise use
        # the script provider baked into the image (see Dockerfile), which runs
        # the generator locally under Deno with no service to deploy, reach or
        # health-check. Preferring the URL when it is set means the compose
        # deployment is unaffected by this.
        opts["extractor_args"] = {
            "youtube": {
                # android_vr: JSLESS and the most reliable client here
                # web_safari: fallback, session-bound PO token
                # ios/web_music removed: need a GVS PO token we do not provide
                #   → always skipped, and noisy about it
                "player_client": ["android_vr", "web_safari"],
            },
            **_bgutil_extractor_args(),
        }
        print(f"[Downloader] YouTube: android_vr+web_safari + PO token "
              f"({'HTTP' if os.getenv('BGUTIL_POT_URL', '').strip() else 'script'})")

        # Only load cookies if they exist — expired cookies trigger bot detection warnings
        # and can hurt more than help. Skip silently if file is missing.
        yt_cookies = _get_youtube_cookies_file()
        if yt_cookies:
            opts["cookiefile"] = yt_cookies
            print("[Downloader] YouTube cookies loaded")
        else:
            print("[Downloader] YouTube: no cookies (android_vr client works without them)")

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

    if "twitter.com" in url.lower() or "x.com" in url.lower():
        tw_cookies = _get_twitter_cookies_file()
        if tw_cookies:
            opts["cookiefile"] = tw_cookies
            opts["extractor_args"] = {"twitter": {"api": ["graphql"]}}
            print("[Downloader] Twitter/X cookies loaded — using graphql API")
        else:
            # X requires auth for most content since 2023.
            # Try graphql first (needs cookies for videos), fall through to legacy.
            opts["extractor_args"] = {"twitter": {"api": ["graphql", "legacy"]}}

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
    seen_audio = set()
    max_video_only_height = 0

    # Duration lets us estimate the size of DASH streams that yt-dlp reports
    # without `filesize`/`filesize_approx` (common for YouTube video-only
    # adaptive formats — bytes ≈ bitrate_kbps * 1000 / 8 * duration_seconds).
    duration = info.get("duration") or 0

    def _has_real_size(f):
        return bool(f.get("filesize") or f.get("filesize_approx"))

    def _approx_bytes(f):
        """Best available byte-size for a format, falling back to bitrate×duration."""
        fs = f.get("filesize") or f.get("filesize_approx")
        if fs:
            return fs
        tbr = f.get("tbr") or f.get("vbr") or f.get("abr") or 0
        if tbr and duration:
            return int(tbr * 1000 / 8 * duration)
        return 0

    # ── Pick the audio stream the downloader will actually merge in ──
    # The download format string ends with `+bestaudio[acodec^=mp4a]`, so the
    # merged file carries an m4a/AAC track. Use its REAL size for the estimate.
    def _is_audio_only(f):
        return (f.get("vcodec") or "none") == "none" and (f.get("acodec") or "none") != "none"

    audio_streams = [f for f in raw_formats if f.get("url") and _is_audio_only(f)]
    m4a_streams = [f for f in audio_streams if (f.get("acodec") or "").lower().startswith("mp4a")]
    merge_audio = max(m4a_streams or audio_streams, key=_approx_bytes, default=None)
    best_audio_bytes = _approx_bytes(merge_audio) if merge_audio else 0

    # Codec ranking MUST mirror the download format string (avc1 first) so the
    # size and codec we advertise match the file the user actually receives —
    # and H.264/AAC plays on Windows, macOS, iPhone and Android out of the box.
    def _vcodec_rank(f):
        vc = (f.get("vcodec") or "").lower()
        if vc.startswith(("avc1", "h264")):
            return 0   # H.264 — universal compatibility, what we download
        if vc.startswith(("vp9", "vp09")):
            return 1
        if vc.startswith(("av01", "av1")):
            return 2
        return 3

    merge_audio_real = bool(merge_audio) and _has_real_size(merge_audio)

    def _make_video_entry(f, height, ext, requires_merge):
        # Add the merged audio size ONLY for video-only streams. HLS/progressive
        # formats already carry audio, so adding it would double-count.
        video_only = (f.get("acodec") or "none") == "none"
        filesize = _approx_bytes(f)
        if video_only and filesize:
            filesize += best_audio_bytes
        filesize_mb = round(filesize / (1024 * 1024), 2) if filesize else 0
        # Size is exact only when every byte-source had a reported filesize.
        size_estimated = (not _has_real_size(f)) or (video_only and not merge_audio_real)
        # H.264 plays on every OS/player (Windows, macOS, iPhone, Android, QuickTime).
        # VP9/AV1 (2K/4K — YouTube has no H.264 there) may not open on Apple/older devices.
        universal = (f.get("vcodec") or "").lower().startswith(("avc1", "h264"))
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
            "size_estimated": size_estimated,
            "universal": universal,
            "recommended": False,  # set below: best universal (H.264) option
            "url": f.get("url"),
            "requires_merge": requires_merge
        }

    # ── Pick ONE best stream per height, mirroring the downloader's choice ──
    # For each resolution we choose the stream yt-dlp would actually download:
    #   1. lowest codec rank  (avc1 > vp9 > av01)
    #   2. video-only         (the merge target — `bestvideo`)
    #   3. larger known size  (tie-break)
    # This avoids the android_vr client's inflated "combined" formats whose
    # reported size doesn't match the merged output.
    best_by_height = {}
    for f in raw_formats:
        if not f.get("url"):
            continue
        if (f.get("vcodec") or "none") == "none":
            continue  # audio-only handled separately
        if (f.get("ext") or "") not in ("mp4", "webm"):
            continue
        height = f.get("height") or 0
        if not height:
            continue
        if (f.get("acodec") or "none") == "none" and height > max_video_only_height:
            max_video_only_height = height

        f_vo = (f.get("acodec") or "none") == "none"
        f_key = (_vcodec_rank(f), 0 if f_vo else 1, -_approx_bytes(f))
        cur = best_by_height.get(height)
        if cur is None:
            best_by_height[height] = (f, f_key)
        elif f_key < cur[1]:
            best_by_height[height] = (f, f_key)

    for height, (f, _key) in best_by_height.items():
        # HLS (m3u8) "URLs" are playlists, NOT downloadable files — fetching one
        # directly yields a ~1KB manifest, not the video. Such formats MUST be
        # routed through the backend yt-dlp downloader (requires_merge=True),
        # which fetches+concatenates the segments into a real mp4. Same for
        # video-only DASH (needs an audio track merged in).
        is_hls = "m3u8" in (f.get("protocol") or "")
        requires_merge = (f.get("acodec") or "none") == "none" or is_hls
        # Merged output is always remuxed to mp4; true progressive keeps its ext.
        ext = "mp4" if requires_merge else (f.get("ext") or "mp4")
        video_formats.append(_make_video_entry(f, height, ext, requires_merge))

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

            filesize = _approx_bytes(f)
            filesize_mb = round(filesize / (1024 * 1024), 2) if filesize else 0

            audio_formats.append({
                "type": "audio",
                "label": f"{abr}kbps",
                "ext": ext,
                "filesize_mb": filesize_mb,
                "size_estimated": not _has_real_size(f),
                "url": f_url,
                "bitrate": abr,
            })

    # Sort: video by height desc, audio by bitrate desc
    video_formats.sort(key=lambda x: x["height"], reverse=True)
    audio_formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)

    top_videos = video_formats[:6]
    # Recommend the highest-resolution UNIVERSAL (H.264) format — it plays on
    # every OS. Above 1080p YouTube only has VP9/AV1, so this lands on ≤1080p.
    best_universal = next((v for v in top_videos if v.get("universal")), None)
    if best_universal:
        best_universal["recommended"] = True

    return {
        "video_formats": top_videos,
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


def _parse_upload_date(s):
    if s and len(str(s)) == 8:
        s = str(s)
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return None


def _extract_video_info_impl(url: str, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False, progress_token: str = "", subtitle_language: str = "auto", user_cookies_file: str = None) -> Dict[str, Any]:
    """
    Extract info for a single video URL (synchronous).
    Returns title, thumbnail, and direct MP4 URL.

    Uses PROXY ONLY for metadata extraction, not file download.
    Falls back to Scraping API if primary extraction fails.
    """
    # Shared across every YoutubeDL this request builds, so the reason a
    # download failed is still available at the end — whichever attempt
    # produced it. Without this the user gets a hard-coded guess and the log
    # gets nothing at all (see _YTDLPLogger).
    _ytdlp_errors: list = []

    # ── Step 0: Unshorten short links (v.douyin.com, vm.tiktok.com, etc.)
    # This MUST happen before any other processing so that downstream
    # extractors and proxy rules see the canonical URL.
    original_input_url = url
    url = resolve_short_url(url)
    if url != original_input_url:
        print(f"[Downloader] Unshortened: {original_input_url} -> {url}")

    # ── Step 0b: For YouTube watch URLs, strip playlist/radio params ──
    # URLs like ?v=ID&list=RD...&start_radio=1 tell yt-dlp to fetch the
    # entire radio mix (50+ tracks). Without noplaylist=True this causes OOM.
    # Keep only ?v=VIDEO_ID for single-video extraction.
    _yt_watch_m = re.match(r'(https?://(?:www\.)?(?:youtube\.com/watch))\?(.+)', url)
    if _yt_watch_m:
        from urllib.parse import parse_qs, urlencode
        _qs = parse_qs(_yt_watch_m.group(2), keep_blank_values=True)
        if "v" in _qs:
            _clean = _yt_watch_m.group(1) + "?v=" + _qs["v"][0]
            if _clean != url:
                print(f"[Downloader] Stripped playlist params: {url} -> {_clean}")
                url = _clean

    _prog_key = f"dl_progress:{progress_token}" if progress_token else ""
    _progress_hook = _make_progress_hook(_prog_key) if _prog_key else None

    # ── Douyin: Bypass yt-dlp entirely ─────────────────────────
    # yt-dlp cannot handle Douyin's anti-bot (JS VM + captcha).
    # Route through Apify (cloud) when token available, else multi-provider extractor.
    if is_douyin_url(url) or is_douyin_url(original_input_url):
        try:
            result = None

            # Feed-style links (/jingxuan?modal_id=, /discover?modal_id=) are not
            # recognised by Apify or yt-dlp — normalise to /video/<id> up front.
            from app.services.douyin_extractor import (
                _extract_video_id as _dy_video_id,
                _canonical_douyin_url as _dy_canonical,
            )
            _dy_id = _dy_video_id(original_input_url) or _dy_video_id(url)
            douyin_input = _dy_canonical(_dy_id) if _dy_id else original_input_url

            if os.getenv("APIFY_TOKEN", ""):
                try:
                    from app.services.apify_service import extract_douyin_apify_sync
                    result = extract_douyin_apify_sync(douyin_input, quality)
                    print(f"[Downloader] Douyin via Apify: {result.get('title','')[:60]}")
                except Exception as apify_err:
                    print(f"[Downloader] Apify Douyin failed, falling back: {apify_err}")
                    result = None
            if result is None:
                # Douyin requires signature cookies — forward the cookies the user
                # supplied via "Dùng cookie của tôi" instead of dropping them.
                result = extract_douyin_video_sync(douyin_input, quality, user_cookies_file)

            # Download Douyin file: try direct (Oracle Cloud) first, CN proxy only on failure
            if result.get("direct_mp4_url") and not result.get("local_file_path"):
                import uuid
                import httpx
                from app.core.proxy_manager import IPROYAL_PROXY_CN

                os.makedirs("downloads", exist_ok=True)
                ext = "mp3" if quality.startswith("mp3") else "mp4"
                local_path = f"downloads/douyin_{uuid.uuid4().hex[:8]}.{ext}"
                cdn_url = result["direct_mp4_url"]

                def _stream_to_file(client: httpx.Client) -> bool:
                    try:
                        with client.stream("GET", cdn_url) as resp:
                            resp.raise_for_status()
                            with open(local_path, "wb") as f:
                                for chunk in resp.iter_bytes(chunk_size=65536):
                                    f.write(chunk)
                        return os.path.exists(local_path) and os.path.getsize(local_path) > 0
                    except Exception:
                        if os.path.exists(local_path):
                            os.remove(local_path)
                        return False

                # Douyin's CDN rejects header-less requests (403), so mimic the
                # browser session the play URL was issued for.
                _cdn_headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.douyin.com/",
                    "Accept": "*/*",
                }

                # Attempt 1: direct (free)
                _downloaded = False
                try:
                    with httpx.Client(
                        follow_redirects=True, timeout=120.0, headers=_cdn_headers
                    ) as client:
                        _downloaded = _stream_to_file(client)
                    if _downloaded:
                        print("[Downloader] Douyin: CDN download direct OK")
                except Exception:
                    pass

                # Attempt 2: CN proxy fallback (only if direct failed)
                if not _downloaded and IPROYAL_PROXY_CN:
                    print("[Downloader] Douyin: direct failed — retrying via CN proxy")
                    try:
                        with httpx.Client(
                            follow_redirects=True, timeout=120.0,
                            proxy=IPROYAL_PROXY_CN, headers=_cdn_headers
                        ) as client:
                            _downloaded = _stream_to_file(client)
                        if _downloaded:
                            print("[Downloader] Douyin: CDN download via CN proxy OK")
                    except Exception as cn_err:
                        print(f"[Downloader] Douyin: CN proxy also failed: {cn_err}")

                if _downloaded:
                    result["local_file_path"] = local_path
                    result["file_size_mb"] = round(os.path.getsize(local_path) / (1024 * 1024), 2)
                    result["direct_mp4_url"] = None
                    if ext == "mp3":
                        result["local_mp3_path"] = local_path
                else:
                    print("[Downloader] Douyin: both direct and CN proxy failed — returning CDN URL")

            return result
        except Exception as dy_err:
            print(f"[Downloader] Douyin extractor failed: {dy_err}")
            raise ValueError(f"Không thể tải video Douyin: {dy_err}")

    # ── Threads: dedicated public-content extractor ────────────────────
    # Only single public POST URLs flow through the download path here;
    # profile URLs are handled by the dedicated /fetch-threads endpoint.
    # Failures stay isolated to Threads (never leak into yt-dlp pipeline).
    if is_threads_url(url) or is_threads_url(original_input_url):
        threads_url = url if is_threads_url(url) else original_input_url
        # /share/<code> links are admitted too. They are not post permalinks, so
        # this gate used to reject them with the profile message below — which
        # is what a user gets from the app's own "Copy link" button, and it
        # blamed their link instead of our gap. extract_threads_sync resolves
        # the redirect to the real permalink, so the gate only has to let them
        # past; a share code that does not resolve fails there with a message
        # about the share link, not about a profile.
        if not (is_threads_post_url(threads_url) or is_threads_share_url(threads_url)):
            raise ValueError(
                "Đây là trang cá nhân Threads. Hãy dán link một bài viết "
                "công khai để tải media, hoặc xem danh sách bài qua chế độ Threads."
            )
        try:
            threads_res = extract_threads_sync(threads_url)
            info = to_download_info(threads_res, quality)
            print(f"[Downloader] Threads post -> {info.get('media_count')} media item(s)")
            return info
        except Exception as th_err:
            print(f"[Downloader] Threads extractor failed: {th_err}")
            raise ValueError(str(th_err))

    # ── Reddit: dedicated extractor (proxy for metadata only) ────────────
    # Oracle Cloud / datacenter IPs are blocked by Reddit since 2023.
    # extract_reddit_single uses skip_download=True → only ~200KB flows
    # through the residential proxy (JSON + redirect) to get the v.redd.it
    # CDN URL.  The actual video download hits v.redd.it directly (no IP lock).
    _is_reddit = "reddit.com" in url.lower() or "redd.it" in url.lower()
    if _is_reddit:
        _reddit_err: Optional[str] = None
        try:
            from app.services.reddit_extractor import extract_reddit_single
            rd_result = extract_reddit_single(url, quality)
            if rd_result.get("error_code"):
                _reddit_err = rd_result.get("error_message", "Reddit extraction failed")
            else:
                # Normalize field: direct_url → direct_mp4_url (route contract)
                rd_result["direct_mp4_url"] = rd_result.pop("direct_url", None) or ""
                return rd_result
        except Exception as rd_err:
            print(f"[Downloader] Reddit extractor failed: {rd_err}")
            _reddit_err = str(rd_err)

        # Cobalt fallback: no cookie needed, and Reddit gates some posts
        # (quarantined subs, some galleries) that the dedicated extractor
        # can't reach even for otherwise-public content. Same pattern as
        # the Instagram Phase 1.5c fallback above.
        print(f"[Downloader] Reddit extractor failed ({_reddit_err}) — trying Cobalt")
        from app.services.cobalt_service import is_cobalt_available as _reddit_cobalt_available, fetch_cobalt_stream as _reddit_fetch_cobalt_stream
        if _reddit_cobalt_available():
            _cob = _reddit_fetch_cobalt_stream(url, video_quality="1080", download_mode="auto")
            if _cob.get("status") != "error" and _cob.get("url"):
                print("[Downloader] Reddit: Cobalt fallback OK")
                return {
                    "title":             _cob.get("filename", "Reddit video").rsplit(".", 1)[0],
                    "thumbnail_url":     "",
                    "direct_mp4_url":    _cob["url"],
                    "quality":           quality,
                    "original_url":      url,
                    "duration":          0,
                    "available_formats": [],
                    "provider":          "cobalt",
                }
            print(f"[Downloader] Reddit: Cobalt fallback failed ({_cob.get('error')})")
        else:
            print("[Downloader] Reddit: Cobalt not available")

        raise ValueError(f"Không thể trích xuất thông tin video Reddit: {_reddit_err}")

    # ── LinkedIn: yt-dlp supports natively (public posts only) ─────────────
    if "linkedin.com" in url.lower():
        _li_video = re.search(
            r"linkedin\.com/(?:posts|feed/update|learning)/", url, re.IGNORECASE
        )
        if not _li_video:
            raise ValueError(
                "LinkedIn: chỉ hỗ trợ link bài đăng hoặc video công khai "
                "(linkedin.com/posts/...). Nội dung cá nhân yêu cầu đăng nhập."
            )
        # Falls through to generic yt-dlp path below

    # ── Instagram: gate on cookie; disable profile/batch ─────────────────────
    # Single post/reel → falls through to generic yt-dlp below.
    # _get_base_opts already injects: cookie (line ~564) + IPROYAL_PROXY via
    # _PLATFORM_RULES (RESIDENTIAL tier). CDN (cdninstagram.com) is not IP-locked
    # → bytes always go direct, never through proxy.
    if "instagram.com" in url.lower():
        _ig_single = re.search(
            r"instagram\.com/(?:p|reel|tv)/[^/?#]+", url, re.IGNORECASE
        )
        if not _ig_single:
            raise ValueError(
                "Tải hàng loạt Instagram tạm dừng. "
                "Vui lòng dán link bài đăng cụ thể "
                "(instagram.com/p/... hoặc /reel/...)."
            )
        try:
            from app.core.cookie_pool import get_cookie_from_pool as _igpool
            _has_ig_cookie = bool(_INSTAGRAM_COOKIES_B64 or _igpool("instagram"))
        except Exception:
            _has_ig_cookie = bool(_INSTAGRAM_COOKIES_B64)
        if not _has_ig_cookie:
            raise ValueError(
                "Instagram yêu cầu đăng nhập để tải video. "
                "Vui lòng liên hệ admin để cấu hình cookie Instagram."
            )
        # Single post with cookie: continue to generic yt-dlp path below

    # ── Twitter/X: gate on cookie; disable profile/search/batch ─────────────
    # Single post (status URL) → falls through to generic yt-dlp below.
    # _get_base_opts already injects: cookie (line ~571) + IPROYAL_PROXY via
    # _PLATFORM_RULES (RESIDENTIAL tier). CDN (video.twimg.com) is not IP-locked
    # → bytes always go direct, never through proxy.
    if "twitter.com" in url.lower() or "x.com" in url.lower():
        _is_spaces = bool(re.search(
            r"(?:twitter|x)\.com/i/spaces/[A-Za-z0-9]+", url, re.IGNORECASE
        ))
        _tw_single = bool(re.search(
            r"(?:twitter|x)\.com/[^/?#]+/status/\d+", url, re.IGNORECASE
        ))
        if not _tw_single and not _is_spaces:
            raise ValueError(
                "Twitter/X: chỉ hỗ trợ link bài đăng "
                "(twitter.com/user/status/...) và Twitter Spaces "
                "(twitter.com/i/spaces/...). "
                "Trang cá nhân và tìm kiếm tạm dừng."
            )
        try:
            from app.core.cookie_pool import get_cookie_from_pool as _twpool
            _has_tw_cookie = bool(_TWITTER_COOKIES_B64 or _twpool("twitter"))
        except Exception:
            _has_tw_cookie = bool(_TWITTER_COOKIES_B64)

        if _is_spaces:
            # Twitter Spaces: audio-only HLS stream — force mp3 quality
            if not _has_tw_cookie:
                raise ValueError(
                    "Twitter Spaces yêu cầu đăng nhập để tải audio. "
                    "Vui lòng liên hệ admin để cấu hình cookie Twitter/X."
                )
            # Force audio quality for Spaces and fall through to yt-dlp
            quality = "mp3_128"
        elif not _has_tw_cookie:
            # Single tweet without cookie → try Cobalt as cookieless fallback
            try:
                # is_cobalt_available/fetch_cobalt_stream already imported at module level (line ~191) —
                # a local re-import here previously shadowed them for this entire function's scope
                # (Python treats a name as local to the whole function if it's assigned/imported
                # ANYWHERE in the function body), causing UnboundLocalError on any other code path
                # in this function that reached a bare is_cobalt_available() call first.
                if is_cobalt_available():
                    _cob = fetch_cobalt_stream(url, video_quality="1080", download_mode="auto")
                    if _cob.get("url"):
                        import uuid as _uuid_tw, httpx as _httpx_tw
                        _tw_fname = f"twitter_{_uuid_tw.uuid4().hex[:8]}.mp4"
                        _tw_path = os.path.join(DOWNLOAD_DIR, _tw_fname)
                        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
                        with _httpx_tw.Client(timeout=300.0, follow_redirects=True) as _cl:
                            with _cl.stream("GET", _cob["url"]) as _r:
                                _r.raise_for_status()
                                with open(_tw_path, "wb") as _f:
                                    for _ch in _r.iter_bytes(65536):
                                        _f.write(_ch)
                        if os.path.exists(_tw_path) and os.path.getsize(_tw_path) > 0:
                            return {
                                "title": _cob.get("filename", "Twitter video").rsplit(".", 1)[0],
                                "thumbnail_url": "",
                                "direct_mp4_url": "",
                                "local_file_path": _tw_path,
                                "file_size_mb": round(os.path.getsize(_tw_path) / (1024 * 1024), 2),
                                "quality": quality,
                                "original_url": url,
                                "duration": 0,
                                "available_formats": [],
                            }
            except Exception as _cob_err:
                print(f"[Cobalt/Twitter] Fallback failed: {_cob_err}")
            raise ValueError(
                "Twitter/X yêu cầu đăng nhập để tải video. "
                "Vui lòng liên hệ admin để cấu hình cookie Twitter/X."
            )
        # Single post with cookie, or Spaces with cookie: continue to generic yt-dlp path below

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
    _spotify_origin = False        # set True for Spotify-derived requests
    _spotify_sc_query = None       # SoundCloud fallback search query
    if "open.spotify.com" in url:
        from app.services.spotify_service import get_track_info_async
        try:
            sp_info = asyncio.run(get_track_info_async(url))
            _spotify_title = sp_info.get("name", "")
            _spotify_artist = sp_info.get("artist_str", "")
            _spotify_thumbnail = sp_info.get("thumbnail", "")
            search_query = sp_info["search_query"]
            _spotify_origin = True
            # SoundCloud fallback for when YouTube is unavailable (proxy down / bot-block)
            _spotify_sc_query = (
                f"scsearch1:{_spotify_artist} - {_spotify_title}"
                if _spotify_artist else f"scsearch1:{_spotify_title}"
            )

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
                # Phase 0: resolve ytsearch → YouTube URL (no proxy — ytsearch works direct)
                _search_opts = _get_base_opts(search_query, phase="metadata", error_sink=_ytdlp_errors)
                _search_opts.pop("proxy", None)  # ytsearch needs no proxy, saves residential bandwidth
                _search_opts["extract_flat"] = True
                _search_opts["quiet"] = True
                _search_opts["no_warnings"] = True
                try:
                    with yt_dlp.YoutubeDL(_search_opts) as _ydl:
                        _sr = _ydl.extract_info(search_query, download=False)
                    _entries = _sr.get("entries", []) if _sr else []
                    _yt_url = None
                    if _entries:
                        _e = _entries[0]
                        _yt_url = _e.get("webpage_url") or _e.get("url") or (
                            f"https://www.youtube.com/watch?v={_e['id']}" if _e.get("id") else None
                        )
                    if _yt_url:
                        print(f"[Spotify Phase0] {search_query[:60]} -> {_yt_url}")
                        url = _yt_url
                        # Pre-cache so next request skips ytsearch entirely
                        try:
                            get_redis().setex(_spotify_search_key, 7 * 24 * 3600, _yt_url)
                            _spotify_search_key = None  # Prevent double-cache at end
                        except Exception:
                            pass
                    else:
                        url = search_query  # Fallback: let yt-dlp resolve it
                except Exception as _se:
                    print(f"[Spotify Phase0] search failed ({_se}), falling back to ytsearch")
                    url = search_query

            quality = "mp3_128"  # Force audio-only for Spotify
            print(f"[Downloader] Spotify -> {_spotify_artist} - {_spotify_title} -> {url[:80]}")
        except Exception as sp_err:
            print(f"[Downloader] Spotify error: {sp_err}")
            raise ValueError(f"Không thể tải nhạc Spotify: {sp_err}")
    elif url.lower().startswith("ytsearch"):
        # Spotify per-track / "Tải tất cả (.ZIP)" buttons send a bare ytsearch
        # query (the playlist's embed scrape has no real track URL). YouTube is
        # the primary source, but when it is bot-blocked the search resolves
        # metadata yet produces no media file. Arm the SoundCloud fallback so
        # these audio downloads survive a YouTube outage.
        import re as _re
        _q = _re.sub(r"^ytsearch\d*:", "", url, flags=_re.I).strip()
        _q = _re.sub(r"\s+audio$", "", _q, flags=_re.I).strip()
        if _q:
            _spotify_origin = True
            _spotify_sc_query = f"scsearch1:{_q}"
            print(f"[Downloader] ytsearch -> SoundCloud-first armed: {_spotify_sc_query}")

    # ── SoundCloud: audio-only platform → force MP3 mode ────────────
    # SoundCloud tracks have no video stream; a "video" quality request
    # would fail format selection. yt-dlp handles soundcloud.com natively
    # (DIRECT tier, no proxy). scsearch: results are also SoundCloud.
    _is_soundcloud = "soundcloud.com" in url.lower() or url.lower().startswith("scsearch")
    if _is_soundcloud and not (quality.startswith("mp3") or quality.startswith("audio_")):
        quality = "mp3_128"
        print(f"[Downloader] SoundCloud → forcing audio mode (mp3_128): {url[:80]}")

    # ── Phase 1: Metadata extraction (and download if needed) ──────
    opts = _get_base_opts(url, phase="metadata", quality=quality, error_sink=_ytdlp_errors)
    opts["extract_flat"] = False
    opts = _apply_tiktok_opts(opts, url, remove_watermark)
    if user_cookies_file:
        opts["cookiefile"] = user_cookies_file

    # Bilibili: inject admin cookie from pool/env (users often need login for HD)
    # bilibili.com = Chinese main site (BiliBili extractor)
    # bilibili.tv  = International site (BiliIntl extractor) — same cookie pool
    _is_bilibili = any(d in url.lower() for d in ("bilibili.com", "bilibili.tv", "b23.tv"))
    if _is_bilibili:
        try:
            from app.services.bilibili_extractor import _get_bili_cookies_file as _bili_ck_fn
            _bili_ck = _bili_ck_fn()
            if _bili_ck and not user_cookies_file:
                opts["cookiefile"] = _bili_ck
        except Exception as _be:
            print(f"[Bilibili] Cookie inject failed: {_be}")

    # Force server-side download for FFmpeg merging (HD/4K quality)
    # Only "video_fast" mode skips download (returns direct pre-merged URL for browser)
    is_tiktok = "tiktok.com" in url.lower()
    is_youtube_url = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    is_instagram = "instagram.com" in url.lower()
    is_facebook  = "facebook.com" in url.lower()
    should_download = quality != "video_fast" or is_tiktok

    # ── Per-platform circuit breaker ───────────────────────────────
    # If a platform has been blocking us repeatedly, fail fast for a cooldown
    # instead of hammering it (which escalates a throttle into a hard ban that
    # would kill the feature for ALL users). TikTok/Douyin/SoundCloud are exempt
    # (third-party proxy / low risk).
    _cb_platform = (
        "youtube"   if is_youtube_url else
        "facebook"  if is_facebook    else
        "instagram" if is_instagram   else
        "twitter"   if ("twitter.com" in url.lower() or "x.com" in url.lower()) else
        "threads"   if ("threads.net" in url.lower() or "threads.com" in url.lower()) else
        None
    )
    if _cb_platform:
        try:
            from app.core import platform_circuit as _pcb
            if _pcb.is_open(_cb_platform):
                _cd = _pcb.cooldown_remaining(_cb_platform)
                raise ValueError(
                    f"Nền tảng {_cb_platform} đang tạm nghẽn do quá nhiều yêu cầu. "
                    f"Vui lòng thử lại sau ~{max(1, _cd // 60)} phút."
                )
        except ValueError:
            raise
        except Exception:
            pass  # never let the breaker itself block a download

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
    _yt_already_downloaded = False  # initialized here so all code paths below are safe

    # ── Spotify music: SoundCloud-FIRST (user preference) ────────────
    # SoundCloud is DIRECT (no proxy = free) and, given the "Artist - Title"
    # query, returns the correct track. YouTube via the rotating residential
    # proxy 403s mid-download (the exit IP changes between fragments) and burns
    # paid bandwidth, so it's only a fallback now. Try SoundCloud first; return
    # on success and never touch YouTube/proxy.
    if _spotify_origin and _spotify_sc_query and should_download:
        try:
            _scf_q = quality if str(quality).startswith("mp3") else "mp3_128"
            _scf = _extract_video_info_impl(
                _spotify_sc_query, quality=_scf_q,
                remove_watermark=False, download_subs=False,
                progress_token=progress_token,
            )
            if _scf and (_scf.get("local_mp3_path") or _scf.get("local_file_path")):
                if _spotify_title:
                    _scf["title"] = (
                        f"{_spotify_artist} - {_spotify_title}"
                        if _spotify_artist else _spotify_title
                    )
                if _spotify_thumbnail:
                    _scf["thumbnail_url"] = _spotify_thumbnail
                print("[Spotify] SoundCloud-first OK — skipped YouTube/proxy")
                return _scf
            print("[Spotify] SoundCloud-first: no match — falling through to YouTube")
        except Exception as _scf_err:
            print(f"[Spotify] SoundCloud-first failed ({str(_scf_err)[:60]}) — trying YouTube")

    try:
        if is_youtube_url and should_download:
            # YouTube two-phase: proxy for auth/metadata only, direct CDN for download
            # Phase A: extract info (signed CDN URLs) via residential proxy
            # Cache Phase A result in Redis — same URL within TTL skips proxy entirely
            import json as _json_dl
            _YT_PHASE_A_TTL = 1800  # 30 min — YouTube signed URLs valid ~6 hours
            # Key includes quality: mp3 vs video resolve different formats, so a
            # URL-only key would serve an mp3 result to a video request (and vice versa).
            _yt_phase_a_key = f"yt_phaseA:{quality}:{hashlib.md5(url.encode()).hexdigest()}"
            _proxy_used_for_a = None  # Track which IP Phase A used — Phase B MUST match
            _oracle_blocked = False   # Defined here so Phase A.5 can access it on cache HIT
            info = None
            _rc = None
            try:
                _rc = get_redis()
                _cached_raw = _rc.get(_yt_phase_a_key)
                if _cached_raw:
                    _cached_obj = _json_dl.loads(_cached_raw)
                    # New format: {"info": ..., "proxy": ...}; legacy: plain info dict
                    if isinstance(_cached_obj, dict) and "info" in _cached_obj:
                        info = _cached_obj["info"]
                        _proxy_used_for_a = _cached_obj.get("proxy")
                    else:
                        info = _cached_obj
                    # A degraded extraction must not be served from cache. It
                    # would pin every request for this URL to 360p for the rest
                    # of the TTL and never give the better clients another try —
                    # so one unlucky extraction became half an hour of bad
                    # downloads for everyone asking for that video. Also clears
                    # entries written before this check existed.
                    if _yt_extraction_is_degraded(info):
                        print(f"[Cache] YouTube Phase A HIT but degraded "
                              f"({_yt_phase_a_key[-8:]}) — discarding, re-extracting")
                        info = None
                        _proxy_used_for_a = None
                        try:
                            _rc.delete(_yt_phase_a_key)
                        except Exception:
                            pass
                    else:
                        print(f"[Cache] YouTube Phase A HIT ({_yt_phase_a_key[-8:]})")
            except Exception:
                pass

            if info is None:
                import time as _time
                from app.core.oracle_circuit_breaker import oracle_circuit
                from app.core.po_token_cache import is_bgutil_healthy, mark_bgutil_unhealthy

                _yt_proxy = opts.get("proxy")
                _circuit_state = oracle_circuit.get_state()
                _bgutil_ok = is_bgutil_healthy()
                # Set True when Layer 2 already downloaded the file in-session
                # (proxy-download mode) → Phase A.5 / Phase B become no-ops.
                _yt_already_downloaded = False
                # Holds a Layer-1 result that parsed fine but carried no adaptive
                # formats, so the better layers get a turn. Restored after the
                # chain if none of them produced anything.
                _degraded_info = None

                # ── INVARIANT: YDL_OPTS_ANDROID_VR — NEVER contains cookies ──
                # yt-dlp auto-skips android_vr when cookiefile is present (unsupported).
                # This dict comprehension + assertion PERMANENTLY enforces the rule.
                _avr_opts = {k: v for k, v in opts.items()
                             if k not in ("proxy", "cookiefile", "cookiesfrombrowser")}
                _avr_opts["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
                _avr_opts["ignoreerrors"] = False
                _avr_opts["retries"] = 1
                _avr_opts["extractor_retries"] = 1
                assert "cookiefile" not in _avr_opts and "cookiesfrombrowser" not in _avr_opts, \
                    "BUG: android_vr opts must NOT contain cookies — yt-dlp silently skips the client"

                # Layer 1's own options. Built from the same cookie-free base
                # rather than reusing _avr_opts, because _avr_opts is also what
                # the ScraperAPI layers below clone — those still go out as
                # android_vr and are deliberately left alone.
                _primary_opts = dict(_avr_opts)
                _primary_opts["extractor_args"] = {
                    "youtube": {"player_client": [_YT_PRIMARY_CLIENT]}
                }

                # ── YDL_OPTS_WEB_WITH_COOKIES — web_safari + bgutil + cookies ─
                _web_a_opts = opts.copy()
                _web_a_opts.pop("proxy", None)
                _web_a_opts["extractor_args"] = {
                    "youtube": {"player_client": ["web_safari", "web"]},
                    **_bgutil_extractor_args(),
                }
                _web_a_opts["ignoreerrors"] = False
                _web_a_opts["retries"] = 1
                _web_a_opts["extractor_retries"] = 1

                def _log_pa(layer, result, err=None, fallback=False, ms=0):
                    parts = [
                        f"[PhaseA] layer={layer}",
                        f"result={result}",
                        f"oracle_circuit={_circuit_state}",
                        f"cookies={'yes' if 'web' in layer else 'no'}",
                        f"po_token_used={'yes' if 'bgutil' in layer else 'no'}",
                        f"bgutil_healthy={_bgutil_ok}",
                        f"duration_ms={ms}",
                    ]
                    if err:
                        parts.append(f"error_code={str(err)[:50]}")
                    if fallback:
                        parts.append("fallback_triggered=true")
                    print(" ".join(parts))

                # ── Layer 1b: the other PO-token-free clients, direct ──
                # Costs nothing (no proxy, no ScraperAPI), so it runs before any
                # paid layer. Reachable from BOTH Layer 1 outcomes: a result
                # with no adaptive formats, and an outright failure. It used to
                # sit only under the first, so a bot-block on the primary client
                # skipped straight to the residential proxy and started spending
                # money while a free client was still untried.
                def _try_alt_clients():
                    for _alt in _YT_ADAPTIVE_FALLBACK_CLIENTS:
                        _t1b = _time.time()
                        _alt_name = _alt or "ytdlp_default"
                        _alt_opts = dict(_avr_opts)
                        if _alt:
                            _alt_opts["extractor_args"] = {
                                "youtube": {"player_client": [_alt]}
                            }
                        else:
                            _alt_opts.pop("extractor_args", None)
                        try:
                            with yt_dlp.YoutubeDL(_alt_opts) as ydl:
                                _alt_info = ydl.extract_info(url, download=False)
                        except Exception as _ae:
                            _log_pa(f"{_alt_name}_direct", "fail", str(_ae)[:40],
                                    ms=int((_time.time() - _t1b) * 1000))
                            continue
                        if not _yt_extraction_is_degraded(_alt_info):
                            _log_pa(f"{_alt_name}_direct", "success",
                                    ms=int((_time.time() - _t1b) * 1000))
                            return _alt_info
                        _log_pa(f"{_alt_name}_direct", "degraded_no_adaptive_formats",
                                fallback=True, ms=int((_time.time() - _t1b) * 1000))
                    return None

                # ── Layer 1: visionos direct (oracle CLOSED or HALF probe) ──
                if _circuit_state != "open":
                    _t = _time.time()
                    try:
                        with yt_dlp.YoutubeDL(_primary_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                        oracle_circuit.record_success()
                        _proxy_used_for_a = None
                        # "Did not raise" is not the same as "usable". A client
                        # can be answered with progressive formats only, and then
                        # the best the selector can do is 360p. Accepting that as
                        # success set `info`, and every later layer is guarded by
                        # `if info is None`, so nothing better ever ran. Hold the
                        # result aside instead and let the chain continue; it is
                        # restored below if nothing better turns up, so this can
                        # only improve the outcome.
                        if _yt_extraction_is_degraded(info):
                            _degraded_info = info
                            info = None
                            _log_pa(f"{_YT_PRIMARY_CLIENT}_direct",
                                    "degraded_no_adaptive_formats",
                                    fallback=True, ms=int((_time.time()-_t)*1000))
                            info = _try_alt_clients()
                            if info is not None:
                                _proxy_used_for_a = None
                        else:
                            _log_pa(f"{_YT_PRIMARY_CLIENT}_direct", "success",
                                    ms=int((_time.time()-_t)*1000))
                    except Exception as _e1:
                        _e1s = str(_e1).lower()
                        _is_bot = any(k in _e1s for k in ["sign in", "bot", "confirm", "429", "403", "forbidden"])
                        if _is_bot:
                            oracle_circuit.record_failure()
                            _log_pa(f"{_YT_PRIMARY_CLIENT}_direct", "fail", "bot_blocked",
                                    fallback=True, ms=int((_time.time()-_t)*1000))
                        else:
                            _log_pa(f"{_YT_PRIMARY_CLIENT}_direct", "fail", _e1s[:40],
                                    ms=int((_time.time()-_t)*1000))
                        # The primary client failed outright. Try the free ones
                        # before going any further — a block on one client says
                        # nothing about the others, and everything downstream
                        # (residential proxy, ScraperAPI) costs money per GB.
                        info = _try_alt_clients()
                        if info is not None:
                            _proxy_used_for_a = None
                        elif not _is_bot:
                            # Not a block — "video unavailable", "private video"
                            # and friends. This re-raised before, and it must
                            # still: no client and no proxy can make an absent
                            # video appear, so the paid layers would spend money
                            # to arrive at the same error.
                            raise
                            raise

                # ── Layer 2: android_vr via residential proxy (oracle OPEN or L1 bot-blocked) ──
                # Rotating residential: a FRESH YoutubeDL session re-rolls the exit
                # IP, so a flagged-IP bot-check is retried on a NEW IP. In-session
                # extractor_retries reuse the same pooled connection (same IP) and
                # do NOT help, so we loop with fresh sessions instead.
                # When YOUTUBE_PROXY_DOWNLOAD is on, extract+download run in the
                # SAME proxy session so the CDN URL is fetched from the exact exit
                # IP that signed it (a separate Phase-B session would get a
                # different rotating IP → 403). android_vr is the only client that
                # clears YouTube's bot-check via residential proxy, and it must run
                # WITHOUT cookies (stale cookies actually trigger the bot wall).
                # Engage the proxy byte-download for YouTube ONLY when:
                #   • the request is Spotify-derived audio (cheap — a song is a
                #     few MB), OR
                #   • YOUTUBE_PROXY_DOWNLOAD is on (also allow direct YouTube).
                # A plain YouTube link with the flag OFF never touches the proxy
                # at all (skips this layer), so large mp4 videos can't burn the
                # paid residential bandwidth. The free layers (1/3/Cobalt) still run.
                _combined_dl = _spotify_origin or _youtube_proxy_download_enabled()
                if info is None and _yt_proxy and _combined_dl:
                    _vr_base = _get_base_opts(url, phase="download", quality=quality, error_sink=_ytdlp_errors)
                    _vr_base.pop("cookiefile", None)
                    _vr_base.pop("cookiesfrombrowser", None)
                    if download_subs and not quality.startswith("mp3"):
                        _vr_base["writesubtitles"] = True
                        _vr_base["writeautomaticsub"] = True
                        _sub_langs = {"vi": ["vi","vi-VN","vi-VIE"], "en": ["en","en-US","en-GB"], "all": None}.get(subtitle_language, ["vi","vi-VN","vi-VIE","en","en-US"])
                        _vr_base["subtitleslangs"] = _sub_langs if _sub_langs else ["all"]
                    # #4: rotate the innertube client across attempts so a block
                    # on one (android_vr) is retried on another (ios/tv) — these
                    # don't need a PO token and often survive when one is flagged.
                    _YT_CLIENTS = ["android_vr", "ios", "android_vr", "tv", "ios"]
                    _vr_exhausted = False
                    for _vr_try in range(5):
                        _t = _time.time()
                        _client = _YT_CLIENTS[_vr_try % len(_YT_CLIENTS)]
                        _proxy_a_opts = dict(_vr_base)
                        _proxy_a_opts["proxy"] = _yt_proxy
                        _proxy_a_opts["extractor_args"] = {"youtube": {"player_client": [_client]}}
                        _proxy_a_opts["extract_flat"] = False
                        _proxy_a_opts["ignoreerrors"] = False
                        _proxy_a_opts["extractor_retries"] = 1
                        _proxy_a_opts["retries"] = 1
                        if _combined_dl and _progress_hook:
                            _proxy_a_opts["progress_hooks"] = [_progress_hook]
                        try:
                            with yt_dlp.YoutubeDL(_proxy_a_opts) as ydl:
                                _vr_info = ydl.extract_info(url, download=_combined_dl)
                            if _combined_dl:
                                _rd = (_vr_info or {}).get("requested_downloads") or []
                                _fp = _rd[0].get("filepath", "") if _rd else ""
                                _vr_ok = bool(_fp and os.path.exists(_fp) and os.path.getsize(_fp) > 0)
                            else:
                                _vr_ok = bool(_vr_info and _vr_info.get("formats"))
                            if _vr_ok:
                                info = _vr_info
                                _proxy_used_for_a = _yt_proxy
                                if _combined_dl:
                                    _yt_already_downloaded = True
                                _log_pa(f"{_client}_proxy" + ("_dl" if _combined_dl else ""),
                                        f"success(try{_vr_try + 1})", ms=int((_time.time() - _t) * 1000))
                                break
                            _log_pa(f"{_client}_proxy", f"fail(try{_vr_try + 1})", "no_formats",
                                    fallback=True, ms=int((_time.time() - _t) * 1000))
                        except Exception as _e2:
                            _e2s = str(_e2).lower()
                            _vr_exhausted = any(k in _e2s for k in [
                                "402", "407", "payment required", "traffic_exhausted",
                                "proxyerror", "tunnel connection"])
                            _log_pa(f"{_client}_proxy", f"fail(try{_vr_try + 1})",
                                    "proxy_exhausted" if _vr_exhausted else _e2s[:40],
                                    fallback=True, ms=int((_time.time() - _t) * 1000))
                            if _vr_exhausted:
                                break  # re-rolling the IP won't restore quota
                        # else: bot-block / transient → loop re-rolls the exit IP

                    # Proxy out of traffic → cool it down so the pool rotates
                    # away (no-op with a single proxy) + alert ops + ScraperAPI.
                    if info is None and _vr_exhausted:
                        try:
                            from app.core.proxy_manager import mark_proxy_bad as _mpb
                            _mpb(_yt_proxy)
                        except Exception:
                            pass
                        try:
                            if _rc and _rc.set("proxy_exhausted_alerted", "1", ex=3600, nx=True):
                                from app.core.notifications import send_telegram_message_sync
                                send_telegram_message_sync(
                                    "🚨 <b>Proxy hết dung lượng</b> (407 TRAFFIC_EXHAUSTED)\n"
                                    "Residential proxy (Proxying.io) đã cạn traffic → "
                                    "YouTube downloads đang fail toàn bộ.\n"
                                    "👉 Nạp lại traffic hoặc đổi proxy để khôi phục."
                                )
                        except Exception:
                            pass
                        _sa = get_scraperapi_proxy_url()
                        if _sa:
                            _t = _time.time()
                            _sa_opts = dict(_avr_opts)
                            _sa_opts["proxy"] = _sa
                            _sa_opts["ignoreerrors"] = True
                            _sa_opts["nocheckcertificate"] = True  # ScraperAPI proxy uses its own MITM cert
                            _sa_opts["socket_timeout"] = 45  # ScraperAPI (esp. residential) is slow
                            try:
                                with yt_dlp.YoutubeDL(_sa_opts) as ydl:
                                    info = ydl.extract_info(url, download=False)
                                if info:
                                    _proxy_used_for_a = _sa
                                    _log_pa("android_vr_scraperapi", "success", ms=int((_time.time() - _t) * 1000))
                                else:
                                    _log_pa("android_vr_scraperapi", "fail", "no_formats", fallback=True,
                                            ms=int((_time.time() - _t) * 1000))
                            except Exception:
                                _log_pa("android_vr_scraperapi", "fail", fallback=True,
                                        ms=int((_time.time() - _t) * 1000))
                    # Non-exhausted failure: do NOT raise — fall through to Layer 3 (web_safari).

                # ── Layer 2b: ScraperAPI when no residential proxy configured ──
                if info is None and not _yt_proxy:
                    _sa = get_scraperapi_proxy_url()
                    if _sa:
                        _t = _time.time()
                        _sa_no_proxy_opts = dict(_avr_opts)
                        _sa_no_proxy_opts["proxy"] = _sa
                        _sa_no_proxy_opts["ignoreerrors"] = True
                        _sa_no_proxy_opts["nocheckcertificate"] = True  # ScraperAPI proxy uses its own MITM cert
                        _sa_no_proxy_opts["socket_timeout"] = 45  # ScraperAPI (esp. residential) is slow
                        try:
                            with yt_dlp.YoutubeDL(_sa_no_proxy_opts) as ydl:
                                info = ydl.extract_info(url, download=False)
                            if info:
                                _proxy_used_for_a = _sa
                                _log_pa("android_vr_scraperapi_fallback", "success", ms=int((_time.time()-_t)*1000))
                            else:
                                _log_pa("android_vr_scraperapi_fallback", "fail", "no_formats", fallback=True,
                                        ms=int((_time.time()-_t)*1000))
                        except Exception:
                            _log_pa("android_vr_scraperapi_fallback", "fail", fallback=True,
                                    ms=int((_time.time()-_t)*1000))

                # ── Layer 3: web_safari + bgutil PO token (only when bgutil healthy) ──
                if info is None and _bgutil_ok:
                    _t = _time.time()
                    try:
                        with yt_dlp.YoutubeDL(_web_a_opts) as ydl:
                            info = ydl.extract_info(url, download=False)
                        _proxy_used_for_a = None
                        _log_pa("web_safari_bgutil", "success", ms=int((_time.time()-_t)*1000))
                    except Exception as _e3:
                        _e3s = str(_e3).lower()
                        # Distinguish IP bot-block (not bgutil's fault) from a real
                        # bgutil/PO-token failure. A "sign in / bot / 403 / 429" error
                        # means YouTube flagged THIS IP — the PO token was fine.
                        # Only poison the bgutil health flag on genuine PO-token
                        # failures, else one IP-block needlessly skips this layer for
                        # 15 min and cascades every later request straight to Cobalt.
                        _ip_block = any(k in _e3s for k in [
                            "sign in", "bot", "confirm", "403", "429", "forbidden"])
                        _pot_fail = any(k in _e3s for k in [
                            "po_token", "po token", "pot ", "proof of origin",
                            "bgutil", "connection refused", "connect to", "timed out",
                            "timeout", "getpot", "fetch pot"])
                        _log_pa("web_safari_bgutil", "fail",
                                ("ip_bot_block" if _ip_block and not _pot_fail
                                 else str(_e3)[:40]),
                                fallback=True, ms=int((_time.time()-_t)*1000))
                        if _pot_fail or not _ip_block:
                            mark_bgutil_unhealthy(ttl=900)
                        else:
                            print("[PhaseA] web_safari failed on IP bot-block — "
                                  "bgutil NOT marked unhealthy (PO token was valid)")
                elif info is None:
                    print("[PhaseA] bgutil_unhealthy=1 — skipping web_safari layer, next fallback=Cobalt")

                # Nothing better than the progressive-only result turned up.
                # Use it rather than failing the request: 360p beats an error,
                # and this is exactly the behaviour before the degraded check.
                if info is None and _degraded_info is not None:
                    info = _degraded_info
                    _proxy_used_for_a = None
                    print("[PhaseA] no layer returned adaptive formats — falling back "
                          "to the progressive-only extraction (expect ≤360p)")

                # ── Cache Phase A result ──────────────────────────────────────
                # Skip caching the combined proxy-download result: its CDN URLs
                # are bound to the (rotating) proxy exit IP and its local file is
                # deleted after ~60 min — a cache hit would hand back a dead URL
                # or a missing file. Proxy downloads are fresh per request.
                if info and _rc and not _yt_already_downloaded:
                    try:
                        # Cache a degraded result only long enough to absorb a
                        # burst of identical requests. Giving it the full 30
                        # minutes would lock that URL to 360p for everyone until
                        # it expired, with no retry of the clients that return
                        # adaptive formats — the failure would outlive its cause
                        # by half an hour.
                        _degraded_now = _yt_extraction_is_degraded(info)
                        _ttl = 60 if _degraded_now else _YT_PHASE_A_TTL
                        _cache_obj = {"info": info, "proxy": _proxy_used_for_a}
                        _rc.setex(_yt_phase_a_key, _ttl, _json_dl.dumps(_cache_obj, default=str))
                        print(f"[Cache] YouTube Phase A cached "
                              f"({'proxy' if _proxy_used_for_a else 'direct'}, {_ttl}s"
                              f"{', degraded' if _degraded_now else ''})")
                    except Exception as _ce:
                        print(f"[Cache] Phase A write failed: {_ce}")

            # Phase A.5: when Phase A used proxy, re-extract from Oracle IP (android_vr + bgutil-pot, no proxy, no cookies)
            # Purpose: get CDN URLs signed for Oracle IP → Phase B downloads directly → 0 DataImpulse for video bytes
            # Skip when circuit=OPEN — Oracle IP is network-level blocked, Phase A.5 will always fail
            if _proxy_used_for_a and info and _circuit_state != "open" and not _yt_already_downloaded:
                from app.core.po_token_cache import is_bgutil_healthy as _a5_bgutil_ok
                _a5_clients = ["android_vr", "web_safari"] if _a5_bgutil_ok() else ["android_vr"]
                _a5_extractor_args: dict = {"youtube": {"player_client": _a5_clients}}
                if _a5_bgutil_ok():
                    _a5_extractor_args.update(_bgutil_extractor_args())
                print(f"[Downloader] YouTube Phase A.5: re-extracting from Oracle IP (clients={_a5_clients})")
                _a5_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "ignoreerrors": False,
                    "retries": 1,
                    "extractor_retries": 1,
                    "extract_flat": False,
                    "cachedir": "/app/ytdlp_cache",
                    "js_runtimes": {"node": {}},
                    "remote_components": ["ejs:github"],
                    "extractor_args": _a5_extractor_args,
                }
                # Add cookies to Phase A.5: web_safari + cookies can bypass Oracle IP bot-block
                # when fresh cookies are available → gets CDN URLs signed for Oracle IP → Phase B direct
                _a5_cookies = _get_youtube_cookies_file()
                if _a5_cookies and "web_safari" in _a5_clients:
                    _a5_opts["cookiefile"] = _a5_cookies
                try:
                    with yt_dlp.YoutubeDL(_a5_opts) as ydl:
                        _a5_info = ydl.extract_info(url, download=False)
                    if _a5_info and _a5_info.get("formats"):
                        info = _a5_info
                        _proxy_used_for_a = None  # CDN URLs now signed for Oracle IP
                        print("[Downloader] YouTube Phase A.5: OK — Phase B direct (0 proxy bandwidth)")
                        if _rc:
                            try:
                                _rc.setex(_yt_phase_a_key, _YT_PHASE_A_TTL,
                                          _json_dl.dumps({"info": info, "proxy": None}, default=str))
                            except Exception:
                                pass
                    else:
                        print("[Downloader] YouTube Phase A.5: no formats — Phase B will try direct anyway")
                except Exception as _a5_err:
                    print(f"[Downloader] YouTube Phase A.5: failed ({str(_a5_err)[:80]}) — Phase B tries direct anyway")

            # Phase B: download with proxy matching Phase A
            # If Phase A used proxy AND Phase A.5 failed → CDN URL is IP-bound to proxy exit IP → Phase B must use same proxy
            # If Phase A was direct OR Phase A.5 succeeded → CDN URL is IP-bound to Oracle IP → Phase B uses direct

            # Pre-download disk space check (best-effort — don't block if estimate unavailable)
            if info and not _yt_already_downloaded:
                try:
                    import shutil as _shutil
                    _est_bytes = 0
                    for _f in (info.get("requested_formats") or info.get("formats") or [])[:2]:
                        _est_bytes += (_f.get("filesize") or _f.get("filesize_approx") or 0)
                    if not _est_bytes:
                        _tbr = info.get("tbr") or 0
                        _dur = info.get("duration") or 0
                        _est_bytes = int(_tbr * 1000 / 8 * _dur) if _tbr and _dur else 0
                    if _est_bytes > 0:
                        _free_bytes = _shutil.disk_usage(DOWNLOAD_DIR).free
                        if _free_bytes < _est_bytes * 1.3:
                            raise Exception(
                                f"Không đủ dung lượng đĩa. Cần ≈{_est_bytes // (1024*1024)}MB, "
                                f"còn {_free_bytes // (1024*1024)}MB trống. Thử lại sau vài phút."
                            )
                except OSError:
                    pass  # DOWNLOAD_DIR not mounted yet — skip check

            if info and not _yt_already_downloaded:
                dl_opts = _get_base_opts(url, phase="download", quality=quality, error_sink=_ytdlp_errors)
                if user_cookies_file:
                    dl_opts["cookiefile"] = user_cookies_file
                dl_opts["extract_flat"] = False
                # Subtitle download: write .srt alongside video when user requested subs
                if download_subs and not quality.startswith("mp3"):
                    dl_opts["writesubtitles"] = True
                    dl_opts["writeautomaticsub"] = True
                    _sub_langs = {"vi": ["vi","vi-VN","vi-VIE"], "en": ["en","en-US","en-GB"], "all": None}.get(subtitle_language, ["vi","vi-VN","vi-VIE","en","en-US"])
                    dl_opts["subtitleslangs"] = _sub_langs if _sub_langs else ["all"]
                # Phase B: prefer DIRECT (free). But when Phase A used the proxy
                # AND Phase A.5 couldn't re-sign URLs for the Oracle IP, the CDN
                # URLs only serve to the proxy exit IP — direct would 403. If
                # YOUTUBE_PROXY_DOWNLOAD is enabled, fetch the bytes through that
                # same proxy (uses paid per-GB bandwidth) so YouTube works at all
                # from a bot-blocked datacenter IP. Otherwise stay direct-only
                # (YouTube effectively off — the cost-safe default).
                dl_opts.pop("proxy", None)
                _yt_dl_via_proxy = bool(_proxy_used_for_a) and _youtube_proxy_download_enabled()
                if _yt_dl_via_proxy:
                    dl_opts["proxy"] = _proxy_used_for_a
                    print("[Downloader] YouTube Phase B: downloading THROUGH proxy "
                          "(CDN IP-bound to proxy exit — uses proxy bandwidth)")
                else:
                    _phase_b_note = "Phase A.5 OK" if not _proxy_used_for_a else (
                        "Phase A.5 failed — direct attempt (proxy-download disabled)"
                    )
                    print(f"[Downloader] YouTube Phase B: direct CDN ({_phase_b_note}, 0 proxy bandwidth)")
                dl_opts["ignoreerrors"] = False
                if _progress_hook:
                    dl_opts["progress_hooks"] = [_progress_hook]
                try:
                    with yt_dlp.YoutubeDL(dl_opts) as ydl:
                        info = ydl.process_ie_result(info, download=True)
                    # Verify file exists — ignoreerrors=False can still produce empty result
                    _b_file_ok = False
                    if info and info.get("requested_downloads"):
                        _b_fp = info["requested_downloads"][0].get("filepath", "")
                        _b_file_ok = bool(_b_fp and os.path.exists(_b_fp) and os.path.getsize(_b_fp) > 0)
                    if not _b_file_ok:
                        raise RuntimeError("Phase B: no valid output file after download")
                except Exception as _phase_b_err:
                    _pb_str = str(_phase_b_err)
                    # CDN 403 = URL was proxy-IP-signed and direct was rejected.
                    # Purge Phase A cache + rescue: re-extract from Oracle IP (no proxy) → re-download direct.
                    _cdn_fail_kws = ["403", "forbidden", "no valid output", "404",
                                     "read timeout", "connect timeout"]
                    _is_cdn_fail = any(kw in _pb_str.lower() for kw in _cdn_fail_kws)
                    if _is_cdn_fail and _yt_dl_via_proxy:
                        # Bytes were already fetched through the proxy and still
                        # failed — the direct re-extract can't help (Oracle IP is
                        # blocked). Skip straight to Cobalt.
                        print(f"[Downloader] YouTube Phase B (proxy) failed ({_pb_str[:60]}) — Cobalt")
                        info = None
                    elif _is_cdn_fail:
                        print(f"[Downloader] YouTube Phase B: direct CDN failed ({_pb_str[:60]}) — clearing cache, rescue re-extract")
                        try:
                            if _rc:
                                _rc.delete(_yt_phase_a_key)
                        except Exception:
                            pass
                        try:
                            _rescue_opts = _get_base_opts(url, phase="download", quality=quality, error_sink=_ytdlp_errors)
                            _rescue_opts.pop("proxy", None)
                            _rescue_opts["ignoreerrors"] = False
                            _rescue_opts["extractor_args"] = {
                                **_rescue_opts.get("extractor_args", {}),
                                "youtube": {"player_client": ["android_vr"]},
                            }
                            if _progress_hook:
                                _rescue_opts["progress_hooks"] = [_progress_hook]
                            with yt_dlp.YoutubeDL(_rescue_opts) as ydl:
                                _rescued = ydl.extract_info(url, download=True)
                            _r_ok = False
                            if _rescued and _rescued.get("requested_downloads"):
                                _rfp = _rescued["requested_downloads"][0].get("filepath", "")
                                _r_ok = bool(_rfp and os.path.exists(_rfp) and os.path.getsize(_rfp) > 0)
                            if _r_ok:
                                info = _rescued
                                print("[Downloader] YouTube Phase B rescue (direct android_vr) OK")
                            else:
                                info = None
                        except Exception as _rescue_err:
                            print(f"[Downloader] YouTube Phase B rescue failed: {str(_rescue_err)[:80]} — Cobalt")
                            info = None
                    else:
                        print(f"[Downloader] YouTube Phase B failed: {_pb_str[:120]} — Cobalt")
                        info = None  # trigger Cobalt fallback at Phase 1.5b
        elif should_download and (is_tiktok or is_instagram or "twitter.com" in url.lower() or "x.com" in url.lower()):
            # Two-phase for proxy platforms: metadata via proxy, CDN download direct
            # CDN URLs for TikTok/Instagram/X are IP-independent once obtained
            import json as _json_dl2
            _PHASE_A_TTL = 1200  # 20 min
            _platform_tag = (
                "tiktok"  if is_tiktok    else
                "ig"      if is_instagram else
                "twitter"
            )
            _pa_key = f"phaseA:{_platform_tag}:{hashlib.md5(url.encode()).hexdigest()}"
            info = None
            try:
                _rc2 = get_redis()
                _hit = _rc2.get(_pa_key)
                if _hit:
                    info = _json_dl2.loads(_hit)
                    print(f"[Cache] {_platform_tag} Phase A HIT — skip proxy")
            except Exception:
                pass

            if info is None:
                _pa2_opts = opts.copy()
                _pa2_opts["ignoreerrors"] = False
                _pa2_opts["retries"] = 1
                _pa2_opts["extractor_retries"] = 1
                try:
                    with yt_dlp.YoutubeDL(_pa2_opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                except Exception as _pa2_err:
                    _pa2_str = str(_pa2_err)
                    if "402" in _pa2_str or "payment required" in _pa2_str.lower() or "tunnel connection" in _pa2_str.lower():
                        print(f"[Downloader] {_platform_tag}: proxy unavailable — fallback direct")
                        _direct_opts2 = opts.copy()
                        _direct_opts2.pop("proxy", None)
                        with yt_dlp.YoutubeDL(_direct_opts2) as ydl:
                            info = ydl.extract_info(url, download=False)
                    else:
                        raise
                if info:
                    try:
                        _rc2 = get_redis()
                        _rc2.setex(_pa_key, _PHASE_A_TTL, _json_dl2.dumps(info, default=str))
                        print(f"[Cache] {_platform_tag} Phase A cached {_PHASE_A_TTL}s")
                    except Exception:
                        pass

            if info:
                dl_opts = _get_base_opts(url, phase="download", quality=quality, error_sink=_ytdlp_errors)
                dl_opts = _apply_tiktok_opts(dl_opts, url, remove_watermark)
                if user_cookies_file:
                    dl_opts["cookiefile"] = user_cookies_file
                dl_opts["extract_flat"] = False
                if download_subs and not quality.startswith("mp3"):
                    dl_opts["writesubtitles"] = True
                    dl_opts["writeautomaticsub"] = True
                    _sub_langs = {"vi": ["vi","vi-VN","vi-VIE"], "en": ["en","en-US","en-GB"], "all": None}.get(subtitle_language, ["vi","vi-VN","vi-VIE","en","en-US"])
                    dl_opts["subtitleslangs"] = _sub_langs if _sub_langs else ["all"]
                if _progress_hook:
                    dl_opts["progress_hooks"] = [_progress_hook]
                print(f"[Downloader] {_platform_tag}: CDN download direct (no proxy)")
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    info = ydl.process_ie_result(info, download=True)
        elif should_download and opts.get("proxy"):
            # Generic proxy-tier platform (e.g. Pinterest): extract metadata via
            # proxy, then download bytes DIRECT. Never tunnel video through the
            # paid per-GB proxy. (DIRECT-tier platforms have no proxy in opts and
            # fall to the plain branch below.)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                dl_opts = _get_base_opts(url, phase="download", quality=quality, error_sink=_ytdlp_errors)
                dl_opts = _apply_tiktok_opts(dl_opts, url, remove_watermark)
                if user_cookies_file:
                    dl_opts["cookiefile"] = user_cookies_file
                dl_opts["extract_flat"] = False
                if download_subs and not quality.startswith("mp3"):
                    dl_opts["writesubtitles"] = True
                    dl_opts["writeautomaticsub"] = True
                    _sub_langs = {"vi": ["vi","vi-VN","vi-VIE"], "en": ["en","en-US","en-GB"], "all": None}.get(subtitle_language, ["vi","vi-VN","vi-VIE","en","en-US"])
                    dl_opts["subtitleslangs"] = _sub_langs if _sub_langs else ["all"]
                if _progress_hook:
                    dl_opts["progress_hooks"] = [_progress_hook]
                print("[Downloader] generic: metadata via proxy → download direct (0 proxy bandwidth)")
                with yt_dlp.YoutubeDL(dl_opts) as ydl:
                    info = ydl.process_ie_result(info, download=True)
        else:
            if _progress_hook:
                opts["progress_hooks"] = [_progress_hook]
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
        _soft_signals = ["rate limit", "too many requests", "429"]
        _hard_signals = ["sign in to confirm", "login_required", "challenge", "suspended",
                         "checkpoint", "unusual activity"]
        _err_lower = primary_err_str.lower()
        _is_hard_block = any(s in _err_lower for s in _hard_signals)
        _is_soft_block = any(s in _err_lower for s in _soft_signals) or "403" in primary_err_str or "bot" in _err_lower
        if _is_hard_block or _is_soft_block:
            _platform_name = (
                "youtube"   if is_youtube_url else
                "tiktok"    if is_tiktok      else
                "facebook"  if is_facebook    else
                "instagram" if is_instagram   else None
            )
            if _platform_name:
                _rotate_cookie(_platform_name, hard=_is_hard_block)
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

    # ── Phase 1.5c: Instagram → Cobalt (before burning ScraperAPI credits) ─
    if info is None and is_instagram:
        print(f"[Downloader] Instagram yt-dlp failed — trying Cobalt before ScraperAPI")
        if is_cobalt_available():
            _ig_cobalt = download_instagram_via_cobalt(url, DOWNLOAD_DIR)
            if _ig_cobalt:
                print("[Downloader] Instagram: Cobalt Phase 1.5c OK — skipping ScraperAPI")
                return _ig_cobalt
            print("[Downloader] Instagram: Cobalt Phase 1.5c failed — falling through to ScraperAPI")
        else:
            print("[Downloader] Instagram: Cobalt not available for Phase 1.5c")

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
                
                fallback_opts = _get_base_opts(url, phase="download", error_sink=_ytdlp_errors)  # no proxy for API
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

    # ── Phase 3a-FB: Facebook retry ladder ─────────────────────────
    # One failed attempt is not evidence the video is private. Retry without the
    # cookie and/or with a browser TLS fingerprint before giving up — a public
    # reel that downloads fine anonymously must not be reported as login-only.
    if info is None and is_facebook:
        for _fb_label, _fb_opts in _facebook_retry_plan(opts):
            print(f"[Downloader] Facebook retry: {_fb_label}")
            try:
                with yt_dlp.YoutubeDL(_fb_opts) as ydl:
                    info = ydl.extract_info(url, download=should_download)
            except Exception as _fb_err:
                print(f"[Downloader] Facebook retry ({_fb_label}) failed: "
                      f"{str(_fb_err)[:150]}")
                info = None
            if info:
                print(f"[Downloader] Facebook: recovered via {_fb_label}")
                # Dropping the cookie is what fixed it, and it came from the
                # pool — take it out of rotation so the next request doesn't
                # pay for the same bad cookie. A cookie the USER uploaded for
                # this one download isn't ours to block.
                if (opts.get("cookiefile") and "cookiefile" not in _fb_opts
                        and not user_cookies_file):
                    _rotate_cookie("facebook", hard=True)
                break

    # ── Phase 3b: Facebook Cobalt fallback ─────────────────────────
    if info is None and is_facebook:
        print(f"[Downloader] yt-dlp failed for Facebook — trying Cobalt: {url}")
        if is_cobalt_available():
            cobalt_info = download_facebook_via_cobalt(url, DOWNLOAD_DIR)
            if cobalt_info:
                info = cobalt_info
                print("[Downloader] Facebook: Cobalt fallback OK")
            else:
                print("[Downloader] Facebook: Cobalt also failed")
        else:
            print("[Downloader] Facebook: Cobalt not available")

    # Spotify-derived audio (open.spotify.com or bare ytsearch): YouTube can
    # return metadata WITHOUT an actual media file when bot-blocked (or when the
    # proxy is metadata-only). Treat a result with no downloadable artifact as a
    # failure so the SoundCloud fallback below runs — never report a phantom
    # success that resolves a title but downloads nothing.
    if info is not None and _spotify_origin and not (
        _yt_already_downloaded
        or info.get("local_mp3_path") or info.get("local_file_path") or info.get("direct_mp4_url")
        or ((info.get("requested_downloads") or [{}])[0].get("filepath"))
    ):
        print("[Spotify] YouTube returned metadata but no media file — forcing SoundCloud fallback")
        info = None

    if info is None:
        # ── Spotify → SoundCloud fallback ────────────────────────────
        # The Spotify track resolved to a YouTube source but the YouTube
        # path failed (proxy exhausted / bot-block). Retry the same track
        # against SoundCloud so audio downloads survive a YouTube outage.
        if _spotify_origin and _spotify_sc_query:
            print(f"[Spotify] YouTube path failed — falling back to SoundCloud: {_spotify_sc_query}")
            try:
                _sc_quality = quality if str(quality).startswith("mp3") else "mp3_128"
                sc_info = _extract_video_info_impl(
                    _spotify_sc_query, quality=_sc_quality,
                    remove_watermark=False, download_subs=False,
                    progress_token=progress_token,
                )
                if sc_info:
                    # Prefer the original Spotify title/cover for a clean UI
                    if _spotify_title:
                        sc_info["title"] = (
                            f"{_spotify_artist} - {_spotify_title}"
                            if _spotify_artist else _spotify_title
                        )
                    if _spotify_thumbnail:
                        sc_info["thumbnail_url"] = _spotify_thumbnail
                    print("[Spotify] SoundCloud fallback OK")
                    return sc_info
            except Exception as _sc_err:
                print(f"[Spotify] SoundCloud fallback failed: {_sc_err}")
            raise ValueError(
                "Không thể tải nhạc này. Nguồn YouTube đang bị chặn và "
                "SoundCloud không có bản phù hợp. Vui lòng thử lại sau."
            )

        # The real reason, captured from yt-dlp itself. Every message below used
        # to assert a cause the code had never established — "yêu cầu đăng nhập"
        # was printed for a deleted video, a broken extractor and a blocked
        # server IP alike, and the actual error reached neither user nor log.
        _fail_reason = _ytdlp_failure_reason(_ytdlp_errors)
        _detail = f" (Lý do kỹ thuật: {_fail_reason})" if _fail_reason else ""
        if _fail_reason:
            print(f"[Downloader] Extraction failed for {url} — reason: {_fail_reason}")

        _is_twitter = "twitter.com" in url.lower() or "x.com" in url.lower()
        if is_youtube_url:
            raise ValueError(
                "Không thể tải video YouTube. YouTube đang chặn bot — "
                "vui lòng thử lại sau 30 giây. "
                "Nếu lỗi tiếp tục, video có thể bị xoá, giới hạn vùng (geo-block), "
                "hoặc yêu cầu đăng nhập." + _detail
            )
        if is_facebook:
            raise ValueError(
                "Không thể tải video Facebook. Video có thể ở chế độ riêng tư, "
                "đã bị xoá, hoặc Facebook đang chặn máy chủ tải. "
                "Nếu video là Public mà vẫn lỗi, hãy nạp cookies Facebook "
                "qua Admin panel: POST /admin/cookies/upload." + _detail
            )
        if is_instagram:
            raise ValueError(
                "Không thể tải video Instagram. Instagram yêu cầu đăng nhập. "
                "Vui lòng upload cookies Instagram (hết hạn sau ~7 ngày) "
                "qua Admin panel: POST /admin/cookies/upload" + _detail
            )
        if _is_twitter:
            raise ValueError(
                "Không thể tải video Twitter/X. X (Twitter) yêu cầu tài khoản đăng nhập "
                "để xem video từ tháng 6/2023. "
                "Vui lòng upload cookies Twitter/X qua Admin panel: POST /admin/cookies/upload"
                + _detail
            )
        raise ValueError(
            "Không thể trích xuất thông tin video. "
            "Vui lòng kiểm tra: link đúng không, video có Public không, "
            "hoặc video có bị xoá/riêng tư không." + _detail
        )

    direct_url, filesize = _extract_best_url(info)
    
    filesize_mb = 0
    if filesize:
        filesize_mb = round(filesize / (1024 * 1024), 2)

    # ── Extract all available formats for user selection ─────
    fmt_info = _extract_available_formats(info)

    # ── Cobalt Fallback for YouTube SABR-blocked formats ─────
    # Only for video quality requests on actual YouTube URLs (not Spotify/ytsearch/audio)
    is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    yt_dlp_video_count = len(fmt_info["video_formats"])

    if is_youtube and yt_dlp_video_count <= 1 and quality.startswith("video"):
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
    _subtitle_extraction_error = None
    if download_subs:
        try:
            subs = info.get("subtitles") or {}
            auto_subs = info.get("automatic_captions") or {}
            # Prefer manual subtitles; fall back to auto-generated
            combined = subs if subs else auto_subs
            if combined:
                target_lang = next((l for l in ["vi", "en", "en-US", "vi-VN"] if l in combined), None)
                if not target_lang:
                    target_lang = list(combined.keys())[0]
                if target_lang:
                    sub_tracks = combined[target_lang]
                    best_sub = next((st for st in sub_tracks if st.get("ext") in ["srt", "vtt"]), sub_tracks[0] if sub_tracks else None)
                    if best_sub:
                        subtitle_url = best_sub.get("url")
        except Exception as _sub_err:
            print(f"[Downloader] Subtitle URL extraction error: {_sub_err}")
            _subtitle_extraction_error = "subtitle_extraction_failed"

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

    if local_path:
        # FFmpegExtractAudio converts .m4a/.webm/.opus → .mp3 and deletes the original.
        # The info dict still holds the pre-conversion path, so check for the .mp3 sibling.
        if not os.path.exists(local_path):
            mp3_path = re.sub(r'\.(m4a|webm|ogg|opus)$', '.mp3', local_path)
            if os.path.exists(mp3_path):
                local_path = mp3_path

    if local_path and os.path.exists(local_path):
        result["local_file_path"] = local_path
        result["file_size_mb"] = round(os.path.getsize(local_path) / (1024 * 1024), 2)
        if local_path.endswith(".mp3") or local_path.endswith(".m4a"):
            result["local_mp3_path"] = local_path
        # Locate subtitle file written by yt-dlp alongside the video
        if download_subs and not quality.startswith("mp3"):
            if _subtitle_extraction_error:
                result["subtitle_error"] = _subtitle_extraction_error
            else:
                try:
                    sub_file = _find_subtitle_file(local_path)
                    if sub_file:
                        result["local_subtitle_path"] = sub_file
                    elif not result.get("subtitle_url"):
                        result["subtitle_error"] = "no_subtitles_available"
                except Exception as _sf_err:
                    print(f"[Downloader] Subtitle file find error: {_sf_err}")
                    result["subtitle_error"] = "subtitle_extraction_failed"
    elif download_subs and not quality.startswith("mp3"):
        # No local file (CDN direct link platforms) — can only offer CDN subtitle URL
        if _subtitle_extraction_error:
            result["subtitle_error"] = _subtitle_extraction_error
        elif not result.get("subtitle_url"):
            result["subtitle_error"] = "no_subtitles_available"

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

    # ── Video Digest metadata ─────────────────────────────────────────
    result["uploader"]        = info.get("uploader") or info.get("channel")
    result["channel_handle"]  = info.get("uploader_id") or info.get("channel_id")
    result["view_count"]      = info.get("view_count")
    result["tags"]            = (info.get("tags") or [])[:10]
    result["upload_date_iso"] = _parse_upload_date(info.get("upload_date"))
    result["language"]        = info.get("language")
    _manual_subs = info.get("subtitles") or {}
    _auto_subs   = info.get("automatic_captions") or {}
    result["has_subtitles"]              = bool(_manual_subs or _auto_subs)
    result["has_auto_captions"]          = bool(_auto_subs)
    result["available_subtitle_languages"] = sorted(set(list(_manual_subs.keys()) + list(_auto_subs.keys())))
    if _manual_subs:
        result["subtitle_source"] = "manual"
    elif _auto_subs:
        result["subtitle_source"] = "auto"
    else:
        result["subtitle_source"] = "none"

    return result


def extract_video_info_sync(url: str, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False, progress_token: str = "", subtitle_language: str = "auto", user_cookies_file: str = None) -> Dict[str, Any]:
    """
    Public entry point with HARD TIMEOUT.
    Wraps the actual extraction in a thread.
    Use a much longer timeout if the quality requires downloading and merging/converting.
    """
    try:
        # Tiered timeout by operation cost (audio is far smaller than video):
        #   • metadata-only (video_fast) → short
        #   • audio/mp3                  → medium (SoundCloud ~5s, YT-proxy ~140s)
        #   • full video + merge         → long
        q = (quality or "")
        if q == "video_fast":
            timeout = EXTRACTION_TIMEOUT_SECONDS          # 30s
        elif q.startswith("mp3") or q.startswith("audio"):
            timeout = int(os.getenv("AUDIO_EXTRACT_TIMEOUT", "300"))   # 5 min
        else:
            timeout = int(os.getenv("VIDEO_EXTRACT_TIMEOUT", "600"))   # 10 min
        return _run_with_timeout(
            _extract_video_info_impl,
            args=(url, quality, remove_watermark, download_subs, progress_token, subtitle_language),
            kwargs={"user_cookies_file": user_cookies_file} if user_cookies_file else {},
            timeout=timeout,
        )
    except TimeoutError as e:
        print(f"[Downloader] TIMEOUT: {url}")
        raise ValueError(str(e))


async def extract_video_info(url: str, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False, progress_token: str = "", subtitle_language: str = "auto", user_cookies_file: str = None) -> Dict[str, Any]:
    """Async wrapper for single video extraction with Redis caching and dedup lock."""
    import json as _json
    import hashlib as _hashlib
    import re as _re_yt
    from app.core.redis_client import get_redis

    # ── A2: YouTube shared file cache ───────────────────────────────
    # Same video+quality downloaded recently → reuse the file on disk and skip
    # the proxy entirely (0 bandwidth). TTL kept under the 60-min file cleanup so
    # a hit's file still exists; we re-verify existence before serving.
    _yt_cache_key = None
    _ytm = _re_yt.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})", url or "")
    if _ytm and not (url or "").lower().startswith("ytsearch"):
        _yt_cache_key = f"ytfile:{_ytm.group(1)}:{quality}"
        try:
            _c = get_redis().get(_yt_cache_key)
            if _c:
                _r = _json.loads(_c)
                _fp = _r.get("local_file_path") or _r.get("local_mp3_path")
                if _fp and os.path.exists(_fp):
                    print(f"[YT FileCache] HIT {_yt_cache_key} -> {_fp} (0 proxy)")
                    return _r
        except Exception:
            pass

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
        result = await asyncio.to_thread(extract_video_info_sync, url, quality, remove_watermark, download_subs, progress_token, subtitle_language, user_cookies_file)
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

    # A2: store the YouTube file in the shared cache so repeat downloads of the
    # same video skip the proxy. Only when we produced a real local file.
    if _yt_cache_key and result and (result.get("local_file_path") or result.get("local_mp3_path")):
        try:
            get_redis().setex(_yt_cache_key, int(os.getenv("YT_FILE_CACHE_TTL", "7200")),
                              _json.dumps(result, default=str))
            print(f"[YT FileCache] SET {_yt_cache_key}")
        except Exception:
            pass

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


def scrape_tiktok_user_posts(channel_url: str, max_videos: int = 100, min_views: int = 0) -> Dict[str, Any]:
    """
    List a TikTok user's videos via the TikWM public API (no bot-block, no
    login). Paginates with TikWM's cursor. Returns the standard channel-scrape
    shape; view filtering uses play_count.
    """
    import httpx as _httpx
    m = re.search(r"tiktok\.com/@([\w.-]+)", channel_url)
    if not m:
        raise ValueError("Không nhận diện được username TikTok.")
    uid = m.group(1).rstrip(".")

    entries: list = []
    total_found = 0
    cursor = 0
    page = 0
    with _httpx.Client(timeout=20, headers={"User-Agent": TIKTOK_USER_AGENT}) as client:
        # Cap pages so a huge profile can't loop forever (30/page → 600 max).
        while len(entries) < max_videos and page < 20:
            page += 1
            try:
                resp = client.get(
                    "https://www.tikwm.com/api/user/posts",
                    params={"unique_id": uid, "count": 30, "cursor": cursor},
                )
                payload = resp.json()
            except Exception as e:
                print(f"[TikWM] user/posts request failed (page {page}): {e}")
                break

            if payload.get("code") != 0:
                print(f"[TikWM] user/posts error: {payload.get('msg')}")
                break

            data = payload.get("data") or {}
            videos = data.get("videos") or []
            if not videos:
                break

            for v in videos:
                total_found += 1
                vid = v.get("video_id") or v.get("id")
                if not vid:
                    continue
                if (v.get("play_count") or 0) < min_views:
                    continue
                entries.append({
                    "url": f"https://www.tiktok.com/@{uid}/video/{vid}",
                    "title": (v.get("title") or "TikTok Video")[:200],
                })
                if len(entries) >= max_videos:
                    break

            if not data.get("hasMore"):
                break
            cursor = data.get("cursor") or cursor

    print(f"[TikWM] @{uid}: found {total_found}, queued {len(entries)} (max={max_videos}, min_views={min_views})")
    return {
        "channel_title": f"@{uid}",
        "entries": entries,
        "total_found": total_found,
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

    # ── TikTok profiles: TikWM user-posts API (reliable, no bot-block) ──
    # yt-dlp's TikTok user extractor is bot-blocked / hangs on datacenter IPs.
    # TikWM lists a user's videos directly. Fall through to yt-dlp on failure.
    if re.search(r"tiktok\.com/@[\w.-]+", channel_url.lower()):
        try:
            tk = scrape_tiktok_user_posts(channel_url, max_videos, min_views)
            if tk.get("entries"):
                return tk
            print("[Downloader] TikWM user-posts returned 0 — falling back to yt-dlp")
        except Exception as _tk_err:
            print(f"[Downloader] TikWM user-posts failed ({str(_tk_err)[:80]}) — falling back to yt-dlp")

    # ── Twitter/X: Route to dedicated scraper ────────────────────
    if "twitter.com" in channel_url.lower() or "x.com" in channel_url.lower():
        from app.services.twitter_extractor import scrape_twitter_timeline
        return scrape_twitter_timeline(channel_url, max_videos)

    # ── Reddit: Route to dedicated scraper ───────────────────────
    if "reddit.com" in channel_url.lower():
        from app.services.reddit_extractor import scrape_reddit_videos
        return scrape_reddit_videos(channel_url, max_videos)

    # ── Pinterest: Route to dedicated scraper ────────────────────
    if "pinterest.com" in channel_url.lower() or "pinterest.co.uk" in channel_url.lower() or "pin.it" in channel_url.lower():
        from app.services.pinterest_extractor import scrape_pinterest_board
        return scrape_pinterest_board(channel_url, max_videos)

    # ── Threads profiles: Route to dedicated scraper ─────────────
    if ("threads.net" in channel_url.lower() or "threads.com" in channel_url.lower()):
        from app.services.threads_extractor import scrape_threads_profile_sync
        return scrape_threads_profile_sync(channel_url, max_videos)

    # Clean TikTok URLs to avoid yt-dlp extraction errors caused by tracking params
    if "tiktok.com" in channel_url.lower() and "?" in channel_url:
        channel_url = channel_url.split("?")[0]

    # YouTube @handle URLs point to the channel home page which only shows
    # featured/recent videos (often just 2-5). Append /videos to force yt-dlp
    # to enumerate the full Videos tab with all uploads and pagination.
    _is_yt_handle = re.search(r'youtube\.com/@[\w.-]+/?$', channel_url)
    if _is_yt_handle and not channel_url.rstrip('/').endswith('/videos'):
        channel_url = channel_url.rstrip('/') + '/videos'
        print(f"[Downloader] YouTube @handle → redirected to /videos tab: {channel_url}")

    opts = _get_base_opts(channel_url, phase="metadata")
    # extract_flat gives us the video list without resolving each video's streams
    opts["extract_flat"] = "in_playlist"
    # Channel/playlist scraping needs all entries — override single-video guard
    opts["noplaylist"] = False
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
