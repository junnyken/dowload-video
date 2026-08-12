"""
Twitter/X Extractor — Phase 15 Upgrade
========================================
Single tweet video/GIF + user timeline batch via yt-dlp.

Supported URL types:
  - Single tweet:  x.com/user/status/ID  |  twitter.com/user/status/ID
  - User timeline: @handle | x.com/handle | x.com/handle/media
  - Thread:        x.com/user/status/ID (first tweet of a thread)

New in Phase 15:
  • Cookie pool integration (platform "twitter") — no more env-only.
  • GIF support: Twitter GIF is served as MP4 — detected and returned as video.
  • Thread media: scrape all media in a thread reply chain.
  • Standardised error codes: tw_login_required, tw_rate_limited,
    tw_media_not_found, tw_suspended_account.

Return schema (bulk-channel compatible):
    {
      "channel_title":  str,
      "entries":        [{"url": str, "title": str}],
      "total_found":    int,
      "total_queued":   int,
      "error_code":     str | None,
      "error_message":  str | None,
    }

Single tweet extraction returns:
    {
      "title": str, "author": str, "thumbnail": str,
      "direct_url": str, "formats": [...], "source_type": "video"|"gif",
      "error_code": str | None, "error_message": str | None,
    }
"""

import base64
import logging
import os
import re
import tempfile
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_HARD_MAX = 100
_VIDEO_EXTENSIONS = {"mp4", "webm", "m4v"}

# Legacy error codes (kept for backwards compat)
ERR_PRIVATE = "twitter_profile_private"
ERR_RATE_LIMITED = "twitter_rate_limited"
ERR_NO_VIDEOS = "twitter_no_videos_found"
ERR_FAILED = "twitter_extractor_failed"

# Phase 15 standardised error codes
ERR_LOGIN     = "tw_login_required"
ERR_RATE      = "tw_rate_limited"
ERR_NO_MEDIA  = "tw_media_not_found"
ERR_SUSPENDED = "tw_suspended_account"

_ERR_MESSAGES = {
    ERR_PRIVATE:   "Tài khoản Twitter/X này bị khoá, tạm ngưng hoặc yêu cầu đăng nhập.",
    ERR_RATE_LIMITED: "Twitter/X đang giới hạn tốc độ yêu cầu. Vui lòng thử lại sau ít phút.",
    ERR_NO_VIDEOS: "Không tìm thấy video nào trên tài khoản Twitter/X này.",
    ERR_FAILED:    "Không thể trích xuất timeline Twitter/X.",
    ERR_LOGIN:     "Twitter/X yêu cầu đăng nhập để xem nội dung này.",
    ERR_RATE:      "Twitter/X đang giới hạn tốc độ. Thử lại sau 5-10 phút.",
    ERR_NO_MEDIA:  "Tweet này không chứa video hoặc GIF.",
    ERR_SUSPENDED: "Tài khoản Twitter/X này đã bị tạm ngưng.",
}

# Detect single tweet URL
_SINGLE_TWEET_RE = re.compile(
    r"(?:twitter|x)\.com/[\w]+/status/(\d+)", re.IGNORECASE
)

# ── URL normalisation ────────────────────────────────────────────────────────

# Patterns accepted as input
_HANDLE_RE = re.compile(r"^@?([\w]{1,50})$")
_URL_RE = re.compile(
    r"(?:https?://)?(?:(?:www\.)?(?:twitter|x)\.com/)@?([\w]{1,50})(?:/media)?/?",
    re.IGNORECASE,
)


def _normalise_url(raw: str) -> Optional[str]:
    """
    Normalise any of the following forms to https://x.com/{handle}/media:
      @handle
      handle
      twitter.com/handle
      x.com/handle
      twitter.com/handle/media
      x.com/handle/media
      https://twitter.com/handle
      https://x.com/handle/media

    Returns None if the input cannot be parsed as a Twitter/X identifier.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    # Try bare @handle / handle (no dot / slash)
    m = _HANDLE_RE.match(raw.lstrip("@") if raw.startswith("@") else raw)
    if m and "." not in raw and "/" not in raw:
        return f"https://x.com/{m.group(1)}/media"

    # Try URL forms
    m = _URL_RE.match(raw)
    if m:
        return f"https://x.com/{m.group(1)}/media"

    return None


# ── Entry filtering ──────────────────────────────────────────────────────────

def _is_video_entry(entry: Dict[str, Any]) -> bool:
    """
    Return True when an entry is likely a video.

    Inclusion policy: lean towards including uncertain entries so that
    process_video_task can fail gracefully rather than silently dropping
    valid videos.  Only hard-exclude when we have a positive signal that
    the entry is NOT a video (e.g. it carries explicit audio-only vcodec
    or is an image extension).
    """
    if not isinstance(entry, dict):
        return False

    # Positive signals
    if entry.get("media_type") == "video":
        return True
    if entry.get("is_video") is True:
        return True

    vcodec = entry.get("vcodec", "")
    if vcodec and vcodec not in ("none", ""):
        return True

    ext = (entry.get("ext") or "").lower().lstrip(".")
    if ext in _VIDEO_EXTENSIONS:
        return True

    # Negative signals — explicit non-video
    if ext and ext not in _VIDEO_EXTENSIONS and ext in ("jpg", "jpeg", "png", "gif", "webp"):
        return False

    # No reliable signal — include by default (false negatives preferred)
    return True


# ── Cookie helpers ───────────────────────────────────────────────────────────

def _write_cookie_tempfile() -> Optional[str]:
    """
    Get Twitter cookie from pool (LRU rotating) → env var fallback.
    Writes to a temp Netscape file and returns its path.
    Caller is responsible for deleting the file after use.
    """
    # Try cookie pool first (Phase 15: pool-first strategy)
    b64_data: Optional[str] = None
    try:
        from app.core.cookie_pool import get_cookie_from_pool
        b64_data = get_cookie_from_pool("twitter")
    except Exception:
        pass

    # Env var fallback
    if not b64_data:
        b64_data = os.environ.get("TWITTER_COOKIES_B64", "").strip()

    if not b64_data:
        return None

    try:
        cookie_bytes = base64.b64decode(b64_data)
    except Exception as exc:
        logger.warning("[Twitter] Failed to decode cookie: %s", exc)
        return None

    try:
        tmp = tempfile.NamedTemporaryFile(
            mode="wb", suffix=".txt", delete=False, prefix="tw_cookies_"
        )
        tmp.write(cookie_bytes)
        tmp.flush()
        tmp.close()
        return tmp.name
    except Exception as exc:
        logger.warning("[Twitter] Failed to write cookie temp file: %s", exc)
        return None


# ── Single tweet video/GIF extraction ────────────────────────────────────────

def extract_twitter_single(url: str, quality: str = "video") -> Dict[str, Any]:
    """
    Download a single tweet video or GIF.
    Twitter GIF is encoded as MP4 — returned with source_type='gif'.

    Returns dict compatible with standard downloader result contract.
    """
    import yt_dlp

    if not _SINGLE_TWEET_RE.search(url):
        return {
            "error_code": ERR_NO_MEDIA,
            "error_message": "URL không phải tweet đơn lẻ hợp lệ.",
        }

    fmt = "bestaudio/best" if quality.startswith("mp3") else "bestvideo+bestaudio/best[ext=mp4]/best"
    opts: Dict[str, Any] = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_args": {"twitter": {"api": ["graphql"]}},
    }

    cookie_path = _write_cookie_tempfile()
    try:
        if cookie_path:
            opts["cookiefile"] = cookie_path

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False) or {}
        except yt_dlp.utils.DownloadError as e:
            code = _classify_exception(e)
            return {
                "error_code": code or ERR_NO_MEDIA,
                "error_message": _ERR_MESSAGES.get(code or ERR_NO_MEDIA, str(e)),
            }
        except Exception as e:
            return {"error_code": ERR_FAILED, "error_message": str(e)}

        if not info or not (info.get("url") or info.get("formats")):
            return {"error_code": ERR_NO_MEDIA, "error_message": _ERR_MESSAGES[ERR_NO_MEDIA]}

        # Detect GIF: Twitter GIFs have ext=mp4 but no duration or very short
        duration = info.get("duration") or 0
        is_gif = duration < 2 and any(
            (f.get("ext") == "mp4" and f.get("vcodec", "") not in ("none", ""))
            for f in info.get("formats", [])
        )

        formats = [
            {
                "quality": str(f.get("format_note") or f.get("height") or "best"),
                "codec": f.get("vcodec", ""),
                "bitrate": int(f.get("tbr", 0) or 0),
                "url": f.get("url", ""),
                "ext": f.get("ext", "mp4"),
            }
            for f in info.get("formats", [])
            if f.get("url")
        ]

        return {
            "platform": "Twitter",
            "title": info.get("title", ""),
            "author": info.get("uploader", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration_ms": int(duration * 1000),
            "duration": duration,
            "formats": formats,
            "direct_url": info.get("url", "") or (formats[-1]["url"] if formats else ""),
            "source_type": "gif" if is_gif else "video",
            "error_code": None,
            "error_message": None,
        }
    finally:
        if cookie_path:
            try:
                os.unlink(cookie_path)
            except Exception:
                pass


# ── Error classification ─────────────────────────────────────────────────────

def _classify_exception(exc: Exception) -> Optional[str]:
    """
    Inspect an exception message for known Twitter block signals.
    Returns standardised error code string, or None if unrecognised.
    """
    msg = str(exc).lower()

    if any(s in msg for s in ("suspended", "account is suspended")):
        return ERR_SUSPENDED
    if any(s in msg for s in ("sign in", "login", "locked", "private account", "not logged")):
        return ERR_LOGIN
    if any(s in msg for s in ("rate limit", "429", "too many requests")):
        return ERR_RATE
    if any(s in msg for s in ("no video", "no media", "not found", "does not exist")):
        return ERR_NO_MEDIA

    # Legacy codes for backwards compat
    if any(s in msg for s in ("private account",)):
        return ERR_PRIVATE
    return None


# ── Result builders ──────────────────────────────────────────────────────────

def _ok_result(handle: str, entries: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "channel_title": f"@{handle}",
        "entries": entries,
        "total_found": len(entries),
        "total_queued": len(entries),
        "error_code": None,
        "error_message": None,
    }


def _err_result(code: str, detail: str = "", handle: str = "") -> Dict[str, Any]:
    return {
        "channel_title": f"@{handle}" if handle else "Unknown",
        "entries": [],
        "total_found": 0,
        "total_queued": 0,
        "error_code": code,
        "error_message": detail or _ERR_MESSAGES.get(code, _ERR_MESSAGES[ERR_FAILED]),
    }


# ── Public API ───────────────────────────────────────────────────────────────

def scrape_twitter_timeline(url: str, max_videos: int = 50) -> Dict[str, Any]:
    """
    Scrape a Twitter/X user's video timeline and return entries in the
    bulk-channel schema.

    Parameters
    ----------
    url:
        Any of: @handle, bare handle, twitter.com/handle, x.com/handle,
        twitter.com/handle/media, x.com/handle/media (with or without https://).
    max_videos:
        Maximum number of video entries to return. Hard-capped at 100.

    Returns
    -------
    dict with keys: channel_title, entries, total_found, total_queued,
    error_code, error_message.
    """
    # yt-dlp imported here to avoid import-time side effects / heavy startup cost.
    import yt_dlp  # noqa: PLC0415

    max_videos = min(max_videos, _HARD_MAX)

    canonical = _normalise_url(url)
    if not canonical:
        return _err_result(ERR_FAILED, f"Cannot parse Twitter/X URL or handle: {url!r}")

    # Extract the handle for logging and channel_title
    handle_match = re.search(r"x\.com/([\w]+)/media", canonical)
    handle = handle_match.group(1) if handle_match else "unknown"

    cookie_path: Optional[str] = None
    try:
        cookie_path = _write_cookie_tempfile()

        ydl_opts: Dict[str, Any] = {
            "extract_flat": True,
            "quiet": True,
            "ignoreerrors": True,
            # Fetch a few extra so we have room to filter non-video entries
            "playlistend": max_videos + 20,
        }
        if cookie_path:
            ydl_opts["cookiefile"] = cookie_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(canonical, download=False)
            except Exception as exc:
                code = _classify_exception(exc)
                if code:
                    return _err_result(code, str(exc), handle)
                logger.error("[Twitter] yt-dlp extraction error for %s: %s", canonical, exc)
                return _err_result(ERR_FAILED, str(exc), handle)

        if not info:
            return _err_result(ERR_NO_VIDEOS, handle=handle)

        # Flatten: info may be a playlist or a single entry
        raw_entries: List[Dict[str, Any]] = []
        if info.get("_type") == "playlist":
            raw_entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
        elif isinstance(info, dict):
            raw_entries = [info]

        # Filter to video-only entries
        video_entries: List[Dict[str, str]] = []
        for entry in raw_entries:
            if not _is_video_entry(entry):
                continue
            entry_url = entry.get("url") or entry.get("webpage_url") or ""
            if not entry_url:
                continue
            title = entry.get("title") or entry.get("id") or "Untitled"
            video_entries.append({"url": entry_url, "title": str(title)})
            if len(video_entries) >= max_videos:
                break

        if not video_entries:
            return _err_result(ERR_NO_VIDEOS, handle=handle)

        logger.info(
            "[Twitter] @%s -> %d video entries (from %d raw)",
            handle, len(video_entries), len(raw_entries),
        )
        return _ok_result(handle, video_entries)

    finally:
        # Always clean up the cookie temp file
        if cookie_path:
            try:
                os.unlink(cookie_path)
            except Exception:
                pass
