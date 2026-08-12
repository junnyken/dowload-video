"""
Pinterest Board Scraper — Isolated Extractor
============================================
Scope: Public Pinterest board URLs -> list of video pin entries for bulk download.

Strategy: yt-dlp PinterestCollectionIE handles board URLs.
Board URL pattern: pinterest.com/{user}/{board}/

Also accepts:
  - pinterest.co.uk / pinterest.fr / etc. (country domains)
  - pin.it short links (yt-dlp resolves them)

The extractor runs `extract_flat=True` to avoid fetching each pin individually
during discovery. It returns all pin entries up to max_videos; non-video pins
will fail quickly in the downstream process_video_task — this is intentional
and mirrors the Threads profile approach.

Normalized result schema (bulk schema, compatible with _scrape_channel_entries_impl):
    {
      "channel_title":  str,
      "entries":        [{"url": str, "title": str}],
      "total_found":    int,      # raw entries fetched from board
      "total_queued":   int,      # entries returned (= min(total_found, max_videos))
      "error_code":     str | None,
      "error_message":  str | None,
    }

Error codes:
  pinterest_board_private, pinterest_no_videos_found, pinterest_rate_limited
"""

import re
from typing import Dict, Any, List, Optional


# ── Error codes ───────────────────────────────────────────────────────
ERR_PRIVATE = "pinterest_board_private"
ERR_NO_VIDEOS = "pinterest_no_videos_found"
ERR_RATE_LIMITED = "pinterest_rate_limited"

ERR_MESSAGES = {
    ERR_PRIVATE: "Board Pinterest này ở chế độ riêng tư hoặc yêu cầu đăng nhập.",
    ERR_NO_VIDEOS: "Không tìm thấy pin video nào trong board Pinterest này.",
    ERR_RATE_LIMITED: "Pinterest đang giới hạn tốc độ yêu cầu. Vui lòng thử lại sau ít phút.",
}

# Country-code Pinterest domains: pinterest.com, pinterest.co.uk,
# pinterest.fr, pinterest.de, etc.
_PINTEREST_HOST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?pinterest\.[a-z]{2,6}(?:\.[a-z]{2})?|pin\.it",
    re.IGNORECASE,
)


def is_pinterest_url(url: str) -> bool:
    """True for any pinterest.* or pin.it URL."""
    return bool(_PINTEREST_HOST_RE.search(url or ""))


def _normalize_pinterest_url(url: str) -> str:
    """
    Normalize various Pinterest URL forms to a canonical
    https://www.pinterest.com/{user}/{board}/ path.

    pin.it short links and country-domain URLs are passed through as-is —
    yt-dlp's PinterestIE handles redirect resolution internally.
    """
    url = (url or "").strip()
    if not url:
        return url
    # Ensure scheme
    if not re.match(r"^https?://", url, re.IGNORECASE):
        url = "https://" + url
    # Normalize country domains → www.pinterest.com so yt-dlp picks
    # PinterestCollectionIE reliably.
    url = re.sub(
        r"(?i)https?://(?:www\.)?pinterest\.[a-z]{2,6}(?:\.[a-z]{2})?/",
        "https://www.pinterest.com/",
        url,
    )
    return url


def _channel_title_from_info(info: Dict[str, Any], url: str) -> str:
    """
    Extract a human-readable board title from yt-dlp's info dict.
    Falls back to parsing the URL path (/user/board/).
    """
    title = info.get("title") or info.get("playlist_title") or ""
    if title and title.strip():
        return title.strip()
    # Derive from URL: /username/board-name/
    m = re.search(r"pinterest\.com/([^/?#]+)/([^/?#]+)", url or "")
    if m:
        user, board = m.group(1), m.group(2)
        return f"{user}/{board}"
    return "Pinterest Board"


def _is_video_entry(entry: Dict[str, Any]) -> bool:
    """
    Heuristic: is this flat entry likely a video pin?

    In extract_flat mode yt-dlp returns minimal metadata, so we keep the
    check loose — include the entry and let process_video_task fail gracefully
    for image-only pins (they resolve quickly).
    """
    if not isinstance(entry, dict):
        return False
    # Strong signals
    ext = (entry.get("ext") or "").lower()
    if ext in ("mp4", "webm", "m4v", "mov"):
        return True
    vcodec = entry.get("vcodec") or ""
    if vcodec and vcodec != "none":
        return True
    entry_url = entry.get("url") or ""
    if "/videos/" in entry_url or entry_url.lower().endswith(".mp4"):
        return True
    formats = entry.get("formats")
    if isinstance(formats, list) and formats:
        return True
    # Weak / no signal: include anyway (yt-dlp will fail fast on image pins)
    return True


def _error_result(code: str, detail: str = "") -> Dict[str, Any]:
    return {
        "channel_title": "",
        "entries": [],
        "total_found": 0,
        "total_queued": 0,
        "error_code": code,
        "error_message": detail or ERR_MESSAGES.get(code, "Lỗi không xác định."),
    }


def scrape_pinterest_board(url: str, max_videos: int = 50) -> Dict[str, Any]:
    """
    Scrape a public Pinterest board and return up to max_videos pin entries
    in the bulk schema used by _scrape_channel_entries_impl.

    Accepts: pinterest.com, pinterest.co.uk, pin.it short links.

    Returns:
        {
          "channel_title": str,
          "entries": [{"url": str, "title": str}],
          "total_found": int,
          "total_queued": int,
          "error_code": str | None,
          "error_message": str | None,
        }
    """
    import yt_dlp  # noqa: PLC0415 — lazy import, no hard dep at module level

    url = _normalize_pinterest_url(url)
    if not url:
        return _error_result(ERR_NO_VIDEOS, "URL Pinterest không hợp lệ.")

    # Fetch extra entries because boards contain many image-only pins;
    # we need to discover enough to fill the caller's quota.
    fetch_count = max_videos * 4

    opts: Dict[str, Any] = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "playlistend": fetch_count,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc).lower()
        if "private" in msg or "403" in msg:
            return _error_result(ERR_PRIVATE)
        if "rate limit" in msg or "429" in msg or "too many" in msg:
            return _error_result(ERR_RATE_LIMITED)
        return _error_result(ERR_NO_VIDEOS, str(exc))
    except Exception as exc:
        msg = str(exc).lower()
        if "private" in msg or "403" in msg:
            return _error_result(ERR_PRIVATE)
        if "rate limit" in msg or "429" in msg or "too many" in msg:
            return _error_result(ERR_RATE_LIMITED)
        return _error_result(ERR_NO_VIDEOS, str(exc))

    if not info:
        return _error_result(ERR_NO_VIDEOS, "yt-dlp không trả về kết quả.")

    # yt-dlp wraps playlist entries in info["entries"]
    raw_entries: List[Dict[str, Any]] = []
    if "entries" in info:
        for e in (info["entries"] or []):
            if isinstance(e, dict):
                raw_entries.append(e)
    elif info.get("_type") == "url" or info.get("url"):
        # Single pin resolved (pin.it -> single post)
        raw_entries = [info]

    if not raw_entries:
        return _error_result(ERR_NO_VIDEOS)

    channel_title = _channel_title_from_info(info, url)

    # Filter for likely-video entries then cap at max_videos.
    # As noted in _is_video_entry: in flat mode nearly all entries pass —
    # the filter mainly exists to exclude obviously-known non-video types
    # if richer metadata becomes available.
    video_entries = [e for e in raw_entries if _is_video_entry(e)]

    # Cap to requested quota
    queued = video_entries[:max_videos]

    entries: List[Dict[str, Any]] = []
    for e in queued:
        pin_url = e.get("url") or e.get("webpage_url") or ""
        if not pin_url:
            continue
        title = (
            e.get("title")
            or e.get("description")
            or e.get("id")
            or "Pinterest pin"
        )
        entries.append({"url": pin_url, "title": str(title).strip() or "Pinterest pin"})

    if not entries:
        return _error_result(ERR_NO_VIDEOS)

    return {
        "channel_title": channel_title,
        "entries": entries,
        "total_found": len(raw_entries),
        "total_queued": len(entries),
        "error_code": None,
        "error_message": None,
    }


# ── Manual test ───────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    test_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://www.pinterest.com/categories/videos/"
    )
    max_v = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    result = scrape_pinterest_board(test_url, max_videos=max_v)
    print(json.dumps(result, indent=2, ensure_ascii=False))
