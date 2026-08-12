"""
Reddit Extractor — Phase 15 Upgrade
=====================================
Single post download + subreddit/user batch scraping.

New in Phase 15:
  • extract_reddit_single() — download single v.redd.it post with audio merge.
  • Cookie pool support (platform "reddit") for NSFW/auth-gated content.
  • Standardised error codes: reddit_login_required, reddit_video_unavailable,
    reddit_crosspost_external, reddit_private_community, reddit_no_videos_found.

Strategy for single post:
  • Use yt-dlp which handles v.redd.it audio+video merge automatically.
  • For external links (YouTube embed, Imgur, Gfycat), yt-dlp follows through.
  • Cookie required for NSFW (18+) content.

Strategy for batch:
  • GET .json API (public, no auth for public subs)
  • Paginate up to 5 pages, filter video posts only.

Return schema (bulk-channel compatible):
    {
      "channel_title":  str,
      "entries":        [{"url": str, "title": str}],
      "total_found":    int,
      "total_queued":   int,
      "error_code":     str | None,
      "error_message":  str | None,
    }
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_USER_AGENT = "VidGrab/2.0 (public video downloader; https://vidgrab.io)"
_TIMEOUT = 15.0
_PAGE_LIMIT = 100          # max items Reddit returns per page
_MAX_PAGES = 5             # hard page-cap to avoid hammering the API
_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".m4v"}
_VALID_SORTS = {"hot", "new", "top"}

ERR_PRIVATE = "reddit_subreddit_private"
ERR_NO_VIDEOS = "reddit_no_videos_found"
ERR_RATE_LIMITED = "reddit_rate_limited"
ERR_FAILED = "reddit_extractor_failed"

_ERR_MESSAGES = {
    ERR_PRIVATE: (
        "Subreddit hoặc trang người dùng này không tồn tại, bị khoá hoặc là nội dung riêng tư."
    ),
    ERR_NO_VIDEOS: "Không tìm thấy bài đăng video nào.",
    ERR_RATE_LIMITED: (
        "Reddit đang giới hạn tốc độ yêu cầu. Vui lòng thử lại sau ít phút."
    ),
    ERR_FAILED: "Không thể trích xuất nội dung Reddit.",
}

# ── URL parsing ──────────────────────────────────────────────────────────────

_SUBREDDIT_RE = re.compile(
    r"(?:https?://)?(?:www\.)?reddit\.com/r/([^/?#\s]+)", re.IGNORECASE
)
_USER_RE = re.compile(
    r"(?:https?://)?(?:www\.)?reddit\.com/(?:u|user)/([^/?#\s]+)", re.IGNORECASE
)
_BARE_SUBREDDIT_RE = re.compile(r"^/?r/([^/?#\s]+)$", re.IGNORECASE)
_BARE_USER_RE = re.compile(r"^/?(?:u|user)/([^/?#\s]+)$", re.IGNORECASE)


def _parse_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a Reddit URL into (target_type, name) where target_type is
    "subreddit" or "user", and name is the subreddit or username string.

    Returns (None, None) if the URL cannot be recognised.
    """
    raw = (url or "").strip()

    # Full URLs
    m = _USER_RE.search(raw)
    if m:
        return "user", m.group(1)

    m = _SUBREDDIT_RE.search(raw)
    if m:
        return "subreddit", m.group(1)

    # Bare forms like r/videos or /r/videos
    m = _BARE_USER_RE.match(raw)
    if m:
        return "user", m.group(1)

    m = _BARE_SUBREDDIT_RE.match(raw)
    if m:
        return "subreddit", m.group(1)

    return None, None


# ── Video detection ──────────────────────────────────────────────────────────

def _is_video_post(post_data: Dict[str, Any]) -> bool:
    """
    Return True when a Reddit post contains a hosted or linked video.

    Checks, in order:
      1. is_video flag (Reddit-native video)
      2. post_hint == "hosted:video"
      3. media.type starts with "video"
      4. URL ends with a known video extension
    """
    if post_data.get("is_video") is True:
        return True

    if post_data.get("post_hint") == "hosted:video":
        return True

    media = post_data.get("media")
    if isinstance(media, dict):
        media_type = media.get("type") or ""
        if str(media_type).startswith("video"):
            return True

    post_url = post_data.get("url") or ""
    if any(post_url.lower().endswith(ext) for ext in _VIDEO_EXTENSIONS):
        return True

    return False


def _entry_url(post_data: Dict[str, Any]) -> str:
    """
    Return the best URL to pass to yt-dlp for downloading.

    For Reddit-native video (is_video=True) we use the post permalink so
    yt-dlp can merge audio and video.  For external links we use post url.
    Note: media.reddit_video.fallback_url is intentionally NOT used here
    because it carries video only, with no audio track.
    """
    permalink = post_data.get("permalink", "")
    if permalink:
        # Reddit returns relative permalinks like /r/videos/comments/abc123/...
        if permalink.startswith("/"):
            return f"https://www.reddit.com{permalink}"
        return permalink

    # Fallback: direct post URL (external video link, .mp4 hotlinks, etc.)
    return post_data.get("url") or ""


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _get_json(endpoint: str, params: Dict[str, Any]) -> Tuple[Optional[Dict], int]:
    """
    Perform a single synchronous GET to a Reddit JSON endpoint.

    Returns (parsed_json, status_code).  On network/parse errors returns
    (None, 0).
    """
    try:
        resp = httpx.get(
            endpoint,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None, resp.status_code
        return resp.json(), resp.status_code
    except Exception as exc:
        logger.warning("[Reddit] HTTP error for %s: %s", endpoint, exc)
        return None, 0


# ── Result builders ──────────────────────────────────────────────────────────

def _ok_result(channel_title: str, entries: List[Dict[str, str]]) -> Dict[str, Any]:
    return {
        "channel_title": channel_title,
        "entries": entries,
        "total_found": len(entries),
        "total_queued": len(entries),
        "error_code": None,
        "error_message": None,
    }


def _err_result(code: str, channel_title: str = "", detail: str = "") -> Dict[str, Any]:
    return {
        "channel_title": channel_title or "Unknown",
        "entries": [],
        "total_found": 0,
        "total_queued": 0,
        "error_code": code,
        "error_message": detail or _ERR_MESSAGES.get(code, _ERR_MESSAGES[ERR_FAILED]),
    }


# ── Public API ───────────────────────────────────────────────────────────────

def scrape_reddit_videos(
    url: str,
    max_videos: int = 50,
    sort: str = "hot",
) -> Dict[str, Any]:
    """
    Scrape video posts from a public Reddit subreddit or user page.

    Parameters
    ----------
    url:
        Any of: full reddit.com URL, bare r/subreddit or u/username path.
    max_videos:
        Maximum number of video entries to return.
    sort:
        Listing sort order: "hot", "new", or "top" (default: "hot").
        Silently falls back to "hot" if an invalid value is given.

    Returns
    -------
    dict with keys: channel_title, entries, total_found, total_queued,
    error_code, error_message.
    """
    if sort not in _VALID_SORTS:
        sort = "hot"

    target_type, name = _parse_url(url)
    if not target_type or not name:
        return _err_result(
            ERR_FAILED,
            detail=f"Cannot parse Reddit URL or path: {url!r}",
        )

    if target_type == "subreddit":
        channel_title = f"r/{name}"
        endpoint = f"https://www.reddit.com/r/{name}/{sort}.json"
    else:
        channel_title = f"u/{name}"
        endpoint = f"https://www.reddit.com/user/{name}/submitted.json"

    entries: List[Dict[str, str]] = []
    after: Optional[str] = None
    page = 0

    while page < _MAX_PAGES and len(entries) < max_videos:
        params: Dict[str, Any] = {"limit": _PAGE_LIMIT, "raw_json": 1}
        if after:
            params["after"] = after

        data, status = _get_json(endpoint, params)

        if status == 429:
            return _err_result(ERR_RATE_LIMITED, channel_title)
        if status in (403, 404):
            return _err_result(ERR_PRIVATE, channel_title)
        if data is None:
            # Network error or unexpected status — treat as a transient failure
            return _err_result(
                ERR_FAILED,
                channel_title,
                detail=f"Reddit API returned status {status}.",
            )

        listing = data.get("data", {})
        children = listing.get("children") or []

        for child in children:
            if len(entries) >= max_videos:
                break

            post_data = child.get("data") or {}
            if not _is_video_post(post_data):
                continue

            entry_url = _entry_url(post_data)
            if not entry_url:
                continue

            title = (post_data.get("title") or "Untitled").strip()
            entries.append({"url": entry_url, "title": title})

        # Advance cursor or stop
        after = listing.get("after")
        if not after:
            break

        page += 1

    if not entries:
        return _err_result(ERR_NO_VIDEOS, channel_title)

    logger.info("[Reddit] %s -> %d video entries", channel_title, len(entries))
    return _ok_result(channel_title, entries)


# ── Single post extraction (Phase 15) ────────────────────────────────────────

_SINGLE_POST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?reddit\.com/r/[\w]+/comments/([\w]+)",
    re.IGNORECASE,
)
_VREDDIT_RE = re.compile(r"v\.redd\.it/", re.IGNORECASE)


def _get_reddit_cookies_file() -> Optional[str]:
    """Cookie pool (platform 'reddit') → env fallback."""
    try:
        from app.services.downloader import _get_cookies_file
        import os
        b64_env = os.getenv("REDDIT_COOKIES_B64", "")
        return _get_cookies_file("reddit", b64_env)
    except Exception:
        return None


def extract_reddit_single(url: str, quality: str = "video") -> Dict[str, Any]:
    """
    Download a single Reddit post video (v.redd.it, external embed).

    yt-dlp handles:
      - v.redd.it: merges separate video + audio streams automatically.
      - YouTube/Imgur/Gfycat links embedded in post: follows through to source.
      - NSFW: requires cookie with NSFW-enabled account.

    Returns dict compatible with standard downloader result contract.
    """
    import yt_dlp

    # Normalise: if URL is v.redd.it direct, try to get reddit permalink instead
    # (yt-dlp handles both, but permalink gives better metadata)
    fmt = "bestaudio/best" if quality.startswith("mp3") else "bestvideo+bestaudio/best[ext=mp4]/best"

    opts: Dict[str, Any] = {
        "format": fmt,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
    }

    cookies_file = _get_reddit_cookies_file()
    if cookies_file:
        opts["cookiefile"] = cookies_file

    # Residential proxy for metadata only — Oracle/datacenter IPs 403'd by Reddit.
    # skip_download=True means only ~200KB flows through the proxy (JSON + redirect),
    # never the video bytes (those hit v.redd.it CDN directly, which is not IP-locked).
    # Priority: PROXY_POOL_REDDIT → PROXY_POOL_DEFAULT → pick_proxy() (IPROYAL_PROXY).
    try:
        from app.core.proxy_pool import get_proxy_from_pool
        from app.core.proxy_manager import pick_proxy
        _reddit_proxy = get_proxy_from_pool("reddit") or pick_proxy()
        if _reddit_proxy:
            opts["proxy"] = _reddit_proxy
    except Exception:
        pass

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if "private" in msg or "403" in msg or "restricted" in msg:
            return {
                "error_code": "reddit_private_community",
                "error_message": "Nội dung Reddit này là riêng tư hoặc bị giới hạn.",
            }
        if "login" in msg or "nsfw" in msg or "over18" in msg:
            return {
                "error_code": "reddit_login_required",
                "error_message": "Nội dung NSFW yêu cầu đăng nhập. Thêm cookie Reddit vào admin.",
            }
        if "removed" in msg or "deleted" in msg or "not found" in msg:
            return {
                "error_code": "reddit_video_unavailable",
                "error_message": "Video Reddit đã bị xoá hoặc không còn khả dụng.",
            }
        return {
            "error_code": "processing_failed",
            "error_message": f"Reddit extraction failed: {e}",
        }
    except Exception as e:
        return {"error_code": "processing_failed", "error_message": str(e)}

    if not info or not (info.get("url") or info.get("formats")):
        return {
            "error_code": "reddit_video_unavailable",
            "error_message": "Không tìm thấy video trong bài đăng Reddit này.",
        }

    # yt-dlp handles crosspost external embeds (YouTube, Imgur, etc.) transparently

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
        "platform": "Reddit",
        "title": info.get("title", ""),
        "author": info.get("uploader", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration_ms": int((info.get("duration") or 0) * 1000),
        "duration": info.get("duration", 0),
        "formats": formats,
        "direct_url": info.get("url", "") or (formats[-1]["url"] if formats else ""),
        "source_type": "video",
        "error_code": None,
        "error_message": None,
    }
