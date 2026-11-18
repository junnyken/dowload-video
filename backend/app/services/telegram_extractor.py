"""
Telegram Video & Media Extractor — Phase B1
============================================
Supports:
  - Public post:          t.me/<channel>/<msg_id>, telegram.me/<channel>/<msg_id>
  - Public preview post:  t.me/s/<channel>/<msg_id>
  - Public channel batch: t.me/s/<channel>, t.me/<channel>
  - Private channel post: t.me/c/<chat_id>/<msg_id>, t.me/c/<chat_id>/<thread_id>/<msg_id>

Extraction strategy:
  1. Public posts: Fetch `https://t.me/<channel>/<msg_id>?embed=1&mode=tme`
     (with fallback to `https://t.me/s/<channel>`) to parse `<video src="...">`,
     thumbnail, caption, author, and duration WITHOUT requiring login.
  2. Protected / private content:
     - Private `t.me/c/<chat_id>/<msg_id>` without an authenticated user MTProto
       session immediately raises `ValueError("telegram_login_required")`.
     - If the user has an authenticated MTProto session (via `telegram_auth`),
       inspect the message/chat via Telethon (or MTProto helper).
     - If `message.noforwards` or `chat.noforwards` is True (or embed page marks
       content as `noforwards` / saving restricted), MUST refuse with
       `ValueError("telegram_protected_content")`.
"""

from __future__ import annotations

import asyncio
import html as _html
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from app.services.base_extractor import (
    BaseExtractor,
    BatchEntry,
    BatchResult,
    ExtractResult,
    FormatInfo,
    MediaItem,
)
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

_PLATFORM = "telegram"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_RESERVED_PATHS = frozenset({
    "s", "c", "joinchat", "addstickers", "addemoji", "proxy", "socks",
    "share", "iv", "login", "contact", "setlanguage", "addlist", "boost",
})


class TelegramExtractionError(ValueError):
    """ValueError carrying `.error_code` for `classify_extraction_error`."""

    def __init__(self, error_code: str, detail: Optional[str] = None):
        self.error_code = error_code
        super().__init__(error_code if detail is None else f"{detail} (Lý do kỹ thuật: {error_code})")


@dataclass
class TelegramUrlInfo:
    kind: str                      # "public_post" | "public_channel" | "private_post"
    channel: Optional[str] = None
    chat_id: Optional[int] = None
    msg_id: Optional[int] = None
    thread_id: Optional[int] = None
    canonical_url: str = ""


# ── URL Regexes ────────────────────────────────────────────────────────────

_HOST_PAT = r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)"

# t.me/c/<chat_id>/<msg_id> or t.me/c/<chat_id>/<thread_id>/<msg_id>
_PRIVATE_POST_RE = re.compile(
    rf"^{_HOST_PAT}/c/(\d+)/(?:(\d+)/)?(\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)

# t.me/s/<channel>/<msg_id> or t.me/<channel>/<msg_id>
_PUBLIC_POST_RE = re.compile(
    rf"^{_HOST_PAT}/(?:s/)?([A-Za-z0-9_]{{4,64}})/(\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)

# t.me/s/<channel> or t.me/<channel>
_PUBLIC_CHANNEL_RE = re.compile(
    rf"^{_HOST_PAT}/(?:s/)?([A-Za-z0-9_]{{4,64}})/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def parse_telegram_url(url: str) -> Optional[TelegramUrlInfo]:
    """Parse any supported Telegram URL into a TelegramUrlInfo descriptor."""
    u = (url or "").strip()
    if not u:
        return None

    m = _PRIVATE_POST_RE.match(u)
    if m:
        chat_id = int(m.group(1))
        thread_id = int(m.group(2)) if m.group(2) else None
        msg_id = int(m.group(3))
        return TelegramUrlInfo(
            kind="private_post",
            chat_id=chat_id,
            msg_id=msg_id,
            thread_id=thread_id,
            canonical_url=f"https://t.me/c/{chat_id}/{msg_id}",
        )

    m = _PUBLIC_POST_RE.match(u)
    if m:
        channel = m.group(1)
        if channel.lower() in _RESERVED_PATHS:
            return None
        msg_id = int(m.group(2))
        return TelegramUrlInfo(
            kind="public_post",
            channel=channel,
            msg_id=msg_id,
            canonical_url=f"https://t.me/{channel}/{msg_id}",
        )

    m = _PUBLIC_CHANNEL_RE.match(u)
    if m:
        channel = m.group(1)
        if channel.lower() in _RESERVED_PATHS or channel.startswith("+"):
            return None
        return TelegramUrlInfo(
            kind="public_channel",
            channel=channel,
            canonical_url=f"https://t.me/s/{channel}",
        )

    return None


def is_telegram_url(url: str) -> bool:
    return parse_telegram_url(url) is not None


# ── HTML Parsing Helpers ───────────────────────────────────────────────────

_VIDEO_SRC_RE = re.compile(r'<video[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
_THUMB_BG_RE = re.compile(
    r'tgme_widget_message_video_thumb[^>]+style=["\'][^"\']*background-image\s*:\s*url\([\'"]?([^\'")]+)[\'"]?\)',
    re.IGNORECASE,
)
_ANY_BG_RE = re.compile(
    r'background-image\s*:\s*url\([\'"]?([^\'")]+)[\'"]?\)',
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*tgme_widget_message_text[^"\']*["\'][^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_OWNER_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*tgme_widget_message_owner_name[^"\']*["\'][^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_AUTHOR_SPAN_RE = re.compile(
    r'<a[^>]+class=["\'][^"\']*tgme_widget_message_owner_name[^"\']*["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DURATION_RE = re.compile(
    r'<time[^>]+class=["\'][^"\']*tgme_widget_message_video_duration[^"\']*["\'][^>]*>([^<]+)</time>',
    re.IGNORECASE,
)
_ERROR_DIV_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*tgme_widget_message_error[^"\']*["\'][^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _clean_html_text(raw_html: str) -> str:
    txt = re.sub(r"<br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
    txt = _TAG_STRIP_RE.sub("", txt)
    return _html.unescape(txt).strip()


def _parse_duration_str(dur_str: str) -> int:
    """Convert 'MM:SS' or 'HH:MM:SS' into seconds."""
    parts = [p.strip() for p in (dur_str or "").split(":") if p.strip().isdigit()]
    if not parts:
        return 0
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0])


def _is_embed_protected(html: str) -> bool:
    """Check if Telegram embed HTML indicates `noforwards` / copy protection."""
    lower = html.lower()
    if "noforwards" in lower or "tgme_noforwards" in lower:
        return True
    if "saving and copying is disabled" in lower or "copying and forwarding is not allowed" in lower:
        return True
    if "restricts saving content" in lower or "protected content" in lower:
        return True
    return False


def _is_embed_login_or_private_error(html: str) -> bool:
    """Check if `tgme_widget_message_error` indicates private/restricted channel."""
    err_match = _ERROR_DIV_RE.search(html)
    if not err_match:
        return False
    err_text = _clean_html_text(err_match.group(1)).lower()
    if any(k in err_text for k in (
        "protected", "noforwards", "restricts saving", "copying is disabled",
    )):
        return False
    return bool(err_text)


def parse_embed_html(html: str, info: TelegramUrlInfo) -> dict:
    """
    Parse Telegram `?embed=1&mode=tme` HTML (or single message widget HTML).
    Returns dict with video_url, thumbnail, title, author, duration_sec,
    or status flags: protected=True / login_required=True.
    """
    if _is_embed_protected(html):
        return {"protected": True}

    video_match = _VIDEO_SRC_RE.search(html)
    video_url = _html.unescape(video_match.group(1)).strip() if video_match else ""

    if not video_url:
        if _is_embed_login_or_private_error(html):
            return {"login_required": True}
        return {"no_video": True}

    thumb_match = _THUMB_BG_RE.search(html) or _ANY_BG_RE.search(html)
    thumbnail = _html.unescape(thumb_match.group(1)).strip() if thumb_match else ""

    cap_match = _CAPTION_RE.search(html)
    caption = _clean_html_text(cap_match.group(1)) if cap_match else ""

    owner_match = _OWNER_RE.search(html) or _AUTHOR_SPAN_RE.search(html)
    author = _clean_html_text(owner_match.group(1)) if owner_match else (info.channel or "Telegram")

    dur_match = _DURATION_RE.search(html)
    duration_sec = _parse_duration_str(dur_match.group(1)) if dur_match else 0

    title = (caption.split("\n")[0][:120] if caption else "") or f"Telegram {info.channel or 'video'} #{info.msg_id}"

    return {
        "video_url": video_url,
        "thumbnail": thumbnail,
        "title": title,
        "caption": caption,
        "author": author,
        "duration_sec": duration_sec,
    }


# ── MTProto Inspection & Extraction Helper ─────────────────────────────────

async def inspect_message_mtproto(
    info: TelegramUrlInfo,
    session_record: dict,
) -> dict:
    """
    Inspect a Telegram message using user's authenticated Telethon StringSession.
    If `message.noforwards` or `chat.noforwards` is True, raises
    `ValueError("telegram_protected_content")`.
    Can be monkeypatched in unit tests without needing live Telegram servers.
    """
    from app.api.telegram_auth import get_server_api_credentials, is_telethon_installed

    if not is_telethon_installed():
        raise TelegramExtractionError("telegram_login_required")

    api_id, api_hash = get_server_api_credentials(
        session_record.get("api_id"),
        session_record.get("api_hash"),
    )
    if not api_id or not api_hash:
        raise TelegramExtractionError("telegram_login_required")

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_record["session_str"]), int(api_id), str(api_hash))
    await client.connect()
    try:
        entity_ref: Any = info.channel if info.channel else int(f"-100{info.chat_id}")
        chat = await client.get_entity(entity_ref)
        if getattr(chat, "noforwards", False):
            raise TelegramExtractionError("telegram_protected_content")

        msg = await client.get_messages(chat, ids=info.msg_id)
        if isinstance(msg, list):
            msg = msg[0] if msg else None
        if msg is None:
            raise TelegramExtractionError("no_media_found")

        if getattr(msg, "noforwards", False) or getattr(getattr(msg, "chat", None), "noforwards", False):
            raise TelegramExtractionError("telegram_protected_content")

        # Check media presence
        media = getattr(msg, "media", None)
        if not media:
            raise TelegramExtractionError("no_media_found")

        caption = getattr(msg, "message", "") or getattr(msg, "text", "") or ""
        author = getattr(chat, "title", None) or getattr(chat, "username", None) or (info.channel or "Telegram")
        duration_sec = int(getattr(getattr(msg, "file", None), "duration", 0) or 0)
        direct_url = getattr(msg, "direct_url", "") or getattr(media, "url", "") or ""
        title = (caption.split("\n")[0][:120] if caption else "") or f"Telegram {author} #{info.msg_id}"

        return {
            "video_url": direct_url,
            "thumbnail": "",
            "title": title,
            "caption": caption,
            "author": str(author),
            "duration_sec": duration_sec,
            "extraction_method": "mtproto",
        }
    finally:
        await client.disconnect()


def _run_coro_sync(coro):
    """Run an async coroutine from synchronous code safely."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=30)
    return asyncio.run(coro)


def _check_mtproto_message(info: TelegramUrlInfo, session_record: dict) -> dict:
    """Synchronous bridge to `inspect_message_mtproto`."""
    return _run_coro_sync(inspect_message_mtproto(info, session_record))


# ── HTTP Client Factory (monkeypatchable in tests) ─────────────────────────

def _default_http_get(url: str) -> httpx.Response:
    with httpx.Client(
        headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
        follow_redirects=True,
        timeout=15.0,
    ) as client:
        return client.get(url)


# ── Core Extraction Entry Points ───────────────────────────────────────────

def extract_telegram_single(
    url: str,
    *,
    user_key: str = "default",
    http_get: Optional[Callable[[str], httpx.Response]] = None,
) -> dict:
    """
    Extract a single Telegram post (public or private).
    Raises:
      - ValueError("telegram_protected_content") when `noforwards` is active
      - ValueError("telegram_login_required") when private and user is not logged in
    """
    info = parse_telegram_url(url)
    if not info or info.kind == "public_channel":
        raise ValueError("Unsupported Telegram post URL")

    from app.api.telegram_auth import load_user_session
    session_record = load_user_session(user_key)

    # 1. Private channel link: t.me/c/<chat_id>/<msg_id>
    if info.kind == "private_post":
        if not session_record:
            raise TelegramExtractionError("telegram_login_required")
        mt_res = _check_mtproto_message(info, session_record)
        return _build_result_dict(mt_res)

    # 2. Public post link: try public web embed first (?embed=1&mode=tme)
    getter = http_get or _default_http_get
    embed_url = f"https://t.me/{info.channel}/{info.msg_id}?embed=1&mode=tme"
    resp = getter(embed_url)
    parsed = parse_embed_html(resp.text, info)

    # If embed didn't yield a direct video and isn't explicitly protected, try /s/<channel>/<msg_id>
    if parsed.get("no_video") and not parsed.get("protected") and not parsed.get("login_required"):
        preview_url = f"https://t.me/s/{info.channel}/{info.msg_id}"
        try:
            resp_s = getter(preview_url)
            parsed_s = parse_embed_html(resp_s.text, info)
            if parsed_s.get("video_url") or parsed_s.get("protected") or parsed_s.get("login_required"):
                parsed = parsed_s
        except Exception:
            pass

    if parsed.get("protected"):
        # If user has an authenticated MTProto session, double-check via MTProto
        if session_record:
            mt_res = _check_mtproto_message(info, session_record)
            return _build_result_dict(mt_res)
        raise TelegramExtractionError("telegram_protected_content")

    if parsed.get("login_required"):
        if session_record:
            mt_res = _check_mtproto_message(info, session_record)
            return _build_result_dict(mt_res)
        raise TelegramExtractionError("telegram_login_required")

    if not parsed.get("video_url"):
        if session_record:
            mt_res = _check_mtproto_message(info, session_record)
            return _build_result_dict(mt_res)
        raise TelegramExtractionError("no_media_found")

    return _build_result_dict(parsed)


def _build_result_dict(parsed: dict) -> dict:
    video_url = parsed.get("video_url", "")
    dur_sec = int(parsed.get("duration_sec") or 0)
    formats = [
        {
            "quality": "best",
            "codec": "h264",
            "bitrate": 0,
            "url": video_url,
            "ext": "mp4",
        }
    ] if video_url else []
    return {
        "platform": _PLATFORM,
        "source_type": "video",
        "title": parsed.get("title", "Telegram Video"),
        "author": parsed.get("author", "Telegram"),
        "thumbnail": parsed.get("thumbnail", ""),
        "duration": dur_sec,
        "duration_ms": dur_sec * 1000,
        "direct_url": video_url,
        "direct_mp4_url": video_url,
        "formats": formats,
        "extraction_method": parsed.get("extraction_method", "telegram_embed"),
        "error_code": None,
        "error_message": None,
    }


# ── Channel Preview Batch Extraction (`t.me/s/<channel>`) ──────────────────

_MESSAGE_WRAP_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*tgme_widget_message\b[^"\']*["\'][^>]+data-post=["\']([^"\']+)["\'][^>]*>(.*?)<!--\s*tgme_widget_message\s*-->',
    re.IGNORECASE | re.DOTALL,
)
_DATA_POST_RE = re.compile(r'data-post=["\']([A-Za-z0-9_]+/\d+)["\']', re.IGNORECASE)
_CHANNEL_TITLE_RE = re.compile(
    r'<div[^>]+class=["\'][^"\']*tgme_channel_info_header_title[^"\']*["\'][^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


def extract_telegram_batch(
    url: str,
    limit: int = 50,
    *,
    http_get: Optional[Callable[[str], httpx.Response]] = None,
) -> BatchResult:
    """Scrape public channel preview `https://t.me/s/<channel>` for posts."""
    info = parse_telegram_url(url)
    if not info or not info.channel:
        return BatchResult.error(_PLATFORM, "invalid_url", "URL kênh Telegram không hợp lệ.")

    getter = http_get or _default_http_get
    preview_url = f"https://t.me/s/{info.channel}"
    try:
        resp = getter(preview_url)
        html = resp.text
    except Exception as exc:
        return BatchResult.error(_PLATFORM, "provider_unavailable", f"Không tải được trang kênh Telegram: {exc}")

    if _is_embed_protected(html):
        raise TelegramExtractionError("telegram_protected_content")

    title_m = _CHANNEL_TITLE_RE.search(html)
    channel_title = _clean_html_text(title_m.group(1)) if title_m else info.channel

    seen: set[str] = set()
    entries: list[BatchEntry] = []

    # Collect all data-post="<channel>/<msg_id>" occurrences in order
    for m in _DATA_POST_RE.finditer(html):
        post_ref = m.group(1)
        if post_ref in seen:
            continue
        seen.add(post_ref)
        post_url = f"https://t.me/{post_ref}"
        entries.append(BatchEntry(url=post_url, title=f"Telegram {post_ref}"))
        if len(entries) >= limit:
            break

    return BatchResult(
        platform=_PLATFORM,
        channel_title=channel_title,
        entries=entries,
        total_found=len(seen),
        total_queued=len(entries),
    )


# ── BaseExtractor Subclass ─────────────────────────────────────────────────

class TelegramExtractor(BaseExtractor):
    platform_name = _PLATFORM
    supported_features = frozenset({
        "video", "audio", "image", "batch", "channel", "private",
    })
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = True

    _URL_PATTERNS = [
        r"(?:t\.me|telegram\.me|telegram\.dog)/",
    ]
    _test_fixtures = [
        "https://t.me/durov/123",
        "https://t.me/s/durov/123",
    ]

    def detect_url(self, url: str) -> bool:
        return is_telegram_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        user_key = kwargs.get("user_key", "default")
        http_get = kwargs.get("http_get")
        raw = extract_telegram_single(url, user_key=user_key, http_get=http_get)

        formats = [
            FormatInfo(
                quality=str(f.get("quality", "best")),
                codec=f.get("codec", ""),
                bitrate=int(f.get("bitrate", 0) or 0),
                url=f.get("url", ""),
                ext=f.get("ext", "mp4"),
            )
            for f in raw.get("formats", [])
        ]
        return ExtractResult(
            platform=self.platform_name,
            source_type=raw.get("source_type", "video"),
            title=raw.get("title", ""),
            author=raw.get("author", ""),
            thumbnail=raw.get("thumbnail", ""),
            duration_ms=raw.get("duration_ms", 0),
            formats=formats,
            direct_url=raw.get("direct_url", ""),
            extraction_method=raw.get("extraction_method", "telegram_embed"),
        )

    def extract_batch(self, url: str, limit: int = 50, **kwargs) -> BatchResult:
        http_get = kwargs.get("http_get")
        return extract_telegram_batch(url, limit=limit, http_get=http_get)


try:
    REGISTRY.register(TelegramExtractor())
except Exception as _e:
    logger.warning("Could not register TelegramExtractor: %s", _e)
