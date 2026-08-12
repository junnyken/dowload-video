"""
VidGrab Telegram Bot — Phase 10
================================
Commands:
  /start   /help   /download   /extension   /status   /cancel
  /link    /quota  /upgrade    /history

URL intake: paste any supported link → immediate "⏳ Đang xử lý..." reply
            → extract + download → send file (≤50 MB) or send link (>50 MB)

Account linking: /link → magic link via web UI → quota synced with VidGrab web account
Tier enforcement: YouTube video → Pro only; batch quota shared with web
Concurrent jobs: max 3 simultaneous downloads per linked user (Redis counter)
Security: 10 req/hr per-user + 30 req/hr per-chat rate limit, SSRF guard
"""

import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import threading
import time
from urllib.parse import urlparse

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vidgrab-bot")

# ─── Config ───────────────────────────────────────────────────────────────────

TOKEN = os.environ["TELEGRAM_DIST_BOT_TOKEN"]
API   = f"https://api.telegram.org/bot{TOKEN}"

BACKEND_URL = os.environ.get("VIDGRAB_BACKEND_URL", "http://backend:8000")
REDIS_URL   = os.environ.get("REDIS_URL",            "redis://redis:6379/0")
WEB_URL     = os.environ.get("VIDGRAB_WEB_URL",      "https://vidgrab.io")
BOT_SECRET  = os.environ.get("TELEGRAM_BOT_SECRET",  "")   # shared with backend

ZIP_PATH          = "/app/extension/VidGrab-extension.zip"
EXTENSION_VERSION = "4.9.0"

RATE_LIMIT       = 10     # requests per window per user
RATE_LIMIT_CHAT  = 30     # per-chat (group spam guard)
RATE_WINDOW      = 3600   # seconds

MAX_TELEGRAM_FILE_BYTES = 50 * 1024 * 1024   # 50 MB Telegram bot limit
MAX_CONCURRENT_JOBS     = 3                   # per linked user

SUPPORTED_PLATFORMS = {
    "youtube.com":     "YouTube",
    "youtu.be":        "YouTube",
    "tiktok.com":      "TikTok",
    "vm.tiktok.com":   "TikTok",
    "douyin.com":      "Douyin",
    "threads.com":     "Threads",
    "threads.net":     "Threads",
    "open.spotify.com": "Spotify",
    "soundcloud.com":  "SoundCloud",
    "facebook.com":    "Facebook",
    "fb.watch":        "Facebook",
    "instagram.com":   "Instagram",
}

# Internal hostnames the bot must never forward (SSRF)
_BLOCKED_HOSTS = {
    "localhost", "redis", "backend", "celery", "cobalt-api",
    "127.0.0.1", "0.0.0.0",
}

# ─── Copy ────────────────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "👋 *VidGrab Bot*\n\n"
    "Gửi link video để tải xuống, hoặc gõ /help để xem hướng dẫn.\n\n"
    "Kết nối tài khoản VidGrab để đồng bộ quota và mở tính năng Pro: /link"
)

HELP_TEXT = (
    "*VidGrab Bot — Hướng dẫn*\n\n"
    "📎 Dán link video để tải:\n"
    "• YouTube · TikTok · Douyin · Spotify\n"
    "• SoundCloud · Facebook · Instagram · Threads\n\n"
    "*Lệnh*\n"
    "/download `<link>` — tải theo link\n"
    "/link — kết nối tài khoản VidGrab\n"
    "/quota — xem hạn mức tải hôm nay\n"
    "/upgrade — nâng cấp Pro\n"
    "/history — xem 5 lịch sử tải gần nhất\n"
    "/extension — nhận file Chrome extension\n"
    "/status — kiểm tra trạng thái backend\n"
    "/cancel — huỷ tác vụ đang chờ\n"
    "/pwa — Cài app lên màn hình chính (Android & iOS)\n"
    "/share — Chia sẻ VidGrab với bạn bè\n"
    "/help — hiện hướng dẫn này\n\n"
    f"🌐 Web: {WEB_URL}"
)

EXTENSION_TEXT = (
    f"📦 *VidGrab Extension v{EXTENSION_VERSION}*\n\n"
    "Hỗ trợ: YouTube · TikTok · Douyin · Threads\n\n"
    "*Cài đặt:*\n"
    "1. Tải file ZIP ở trên về máy\n"
    "2. Mở `chrome://extensions` trong Chrome\n"
    "3. Bật *Developer mode* (góc phải trên)\n"
    "4. Kéo thả file ZIP vào trang\n\n"
    f"Hoặc tải tại: {WEB_URL}/extension"
)


# ─── Redis ───────────────────────────────────────────────────────────────────

def _get_redis():
    try:
        import redis as _redis
        client = _redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        return client
    except Exception as e:
        log.warning(f"Redis unavailable: {e}")
        return None


# ─── URL helpers ─────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,)") if m else None


def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        for blocked in _BLOCKED_HOSTS:
            if blocked in host:
                return False
        if host.startswith((
            "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
            "172.2", "172.3", "fc00:", "fd", "::1",
        )):
            return False
        return True
    except Exception:
        return False


def detect_platform(url: str) -> tuple[str, bool]:
    """Returns (platform_name, is_supported)."""
    url_lower = url.lower()
    for domain, name in SUPPORTED_PLATFORMS.items():
        if domain in url_lower:
            return name, True
    return "", False


def is_spotify_url(url: str) -> bool:
    return "open.spotify.com" in url.lower()


def is_youtube_url(url: str) -> bool:
    return "youtube.com" in url.lower() or "youtu.be" in url.lower()


def cache_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ─── Telegram API ────────────────────────────────────────────────────────────

def send_message(chat_id: int, text: str, parse_mode: str = "Markdown",
                 reply_markup: dict | None = None) -> dict | None:
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{API}/sendMessage", json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        log.error(f"sendMessage: {e}")
        return None


def edit_message_text(chat_id: int, message_id: int, text: str,
                      reply_markup: dict | None = None):
    payload: dict = {
        "chat_id": chat_id, "message_id": message_id,
        "text": text, "parse_mode": "Markdown",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{API}/editMessageText", json=payload, timeout=10)
    except Exception as e:
        log.error(f"editMessageText: {e}")


def answer_callback_query(cq_id: str, text: str = ""):
    try:
        requests.post(f"{API}/answerCallbackQuery",
                      json={"callback_query_id": cq_id, "text": text}, timeout=8)
    except Exception as e:
        log.error(f"answerCallbackQuery: {e}")


def send_chat_action(chat_id: int, action: str = "typing"):
    try:
        requests.post(f"{API}/sendChatAction",
                      json={"chat_id": chat_id, "action": action}, timeout=5)
    except Exception:
        pass


def send_extension_file(chat_id: int):
    if not os.path.exists(ZIP_PATH):
        send_message(chat_id, f"❌ File chưa sẵn sàng. Tải tại: {WEB_URL}/extension")
        return
    try:
        with open(ZIP_PATH, "rb") as f:
            resp = requests.post(
                f"{API}/sendDocument",
                data={"chat_id": chat_id, "caption": f"📦 VidGrab Extension v{EXTENSION_VERSION}",
                      "parse_mode": "Markdown"},
                files={"document": ("VidGrab-extension.zip", f, "application/zip")},
                timeout=60,
            )
        if resp.ok:
            send_message(chat_id, EXTENSION_TEXT)
        else:
            send_message(chat_id, f"❌ Lỗi gửi file. Tải tại: {WEB_URL}/extension")
    except Exception as e:
        log.error(f"send_extension_file: {e}")
        send_message(chat_id, f"❌ Không gửi được file. Tải tại: {WEB_URL}/extension")


def send_file_or_link(chat_id: int, file_url: str, title: str, is_audio: bool = False):
    """
    Download the file into memory and send to Telegram if ≤50 MB.
    If the file is larger, send the URL as a clickable link instead.
    """
    if not file_url:
        send_message(chat_id, "❌ Không có link tải.")
        return

    try:
        head = requests.head(file_url, timeout=10, allow_redirects=True)
        content_length = int(head.headers.get("content-length", 0))
    except Exception:
        content_length = 0

    if content_length > MAX_TELEGRAM_FILE_BYTES:
        send_message(chat_id, f"⬇️ *{title}*\n\nFile lớn hơn 50 MB — tải tại:\n{file_url}\n\n⏱ Hết hạn sau 60 phút.")
        return

    try:
        send_chat_action(chat_id, "upload_audio" if is_audio else "upload_video")
        r = requests.get(file_url, timeout=120, stream=True)
        data = b"".join(r.iter_content(65536))

        if len(data) > MAX_TELEGRAM_FILE_BYTES:
            send_message(chat_id, f"⬇️ *{title}*\n\nFile quá lớn — tải tại:\n{file_url}")
            return

        ctype = r.headers.get("content-type", "video/mp4")
        ext   = mimetypes.guess_extension(ctype.split(";")[0]) or (".mp3" if is_audio else ".mp4")
        fname = re.sub(r"[^\w\-. ]", "_", title)[:60] + ext

        buf = io.BytesIO(data)
        buf.name = fname

        if is_audio:
            resp = requests.post(
                f"{API}/sendAudio",
                data={"chat_id": chat_id, "title": title, "parse_mode": "Markdown"},
                files={"audio": (fname, buf, ctype)},
                timeout=120,
            )
        else:
            resp = requests.post(
                f"{API}/sendVideo",
                data={"chat_id": chat_id, "caption": f"📹 {title}", "parse_mode": "Markdown"},
                files={"video": (fname, buf, ctype)},
                timeout=120,
            )

        if not resp.ok:
            log.warning(f"send_file_or_link Telegram error: {resp.text[:200]}")
            send_message(chat_id, f"⬇️ *{title}*\n\n{file_url}\n\n⏱ Hết hạn sau 60 phút.")

    except Exception as e:
        log.error(f"send_file_or_link: {e}")
        send_message(chat_id, f"⬇️ *{title}*\n\n{file_url}\n\n⏱ Hết hạn sau 60 phút.")


# ─── Backend calls ────────────────────────────────────────────────────────────

def _bot_headers() -> dict:
    return {"X-Bot-Secret": BOT_SECRET} if BOT_SECRET else {}


def call_fetch_link(url: str, quality: str = "video") -> dict:
    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/v1/fetch-link",
            json={
                "url": url,
                "quality": quality,
                "remove_watermark": True,
                "source_surface": "telegram",
            },
            timeout=90,
        )
        data = resp.json()
        return {
            "title":             data.get("title", "Video"),
            "direct_mp4_url":    data.get("direct_mp4_url") or data.get("url", ""),
            "thumbnail_url":     data.get("thumbnail_url", ""),
            "available_formats": data.get("available_formats", []),
            "has_subtitles":     data.get("has_subtitles", False),
            "error_message":     data.get("error_message") or data.get("error", ""),
            "error_code":        data.get("error_code", ""),
        }
    except requests.exceptions.Timeout:
        return {"error_message": "Backend timeout. Thử lại sau.", "error_code": "timeout"}
    except Exception as e:
        log.error(f"call_fetch_link: {e}")
        return {"error_message": "Lỗi kết nối backend.", "error_code": "connection_error"}


def call_health() -> dict:
    try:
        resp = requests.get(f"{BACKEND_URL}/health", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "ok":           True,
                "status":       data.get("status", "ok"),
                "redis":        data.get("redis", {}).get("status", "unknown"),
                "disk_free_gb": data.get("disk", {}).get("free_gb"),
                "yt_dlp":       data.get("ytdlp_version", ""),
                "app_version":  data.get("app_version", ""),
            }
        return {"ok": False, "status": "degraded"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "status": "offline"}
    except Exception as e:
        log.error(f"call_health: {e}")
        return {"ok": False, "status": "error"}


def call_link_request(telegram_user_id: int, telegram_username: str = "") -> dict | None:
    """Ask backend to create a link token for this Telegram user."""
    if not BOT_SECRET:
        return None
    try:
        resp = requests.post(
            f"{BACKEND_URL}/api/v1/telegram/link-request",
            json={"telegram_user_id": telegram_user_id, "telegram_username": telegram_username},
            headers=_bot_headers(),
            timeout=10,
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        log.error(f"call_link_request: {e}")
    return None


def call_user_info(telegram_id: int) -> dict:
    """
    Fetch tier + quota for a linked Telegram user.
    Returns {linked, tier, downloads_today, daily_limit, user_id}.
    """
    if not BOT_SECRET:
        return {"linked": False}
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/v1/telegram/user-info",
            params={"telegram_id": telegram_id},
            headers=_bot_headers(),
            timeout=8,
        )
        if resp.ok:
            return resp.json()
    except Exception as e:
        log.error(f"call_user_info: {e}")
    return {"linked": False}


def call_history(telegram_id: int) -> list:
    """Fetch recent download history for a linked user. Best-effort."""
    info = call_user_info(telegram_id)
    if not info.get("linked"):
        return []
    user_id = info.get("user_id")
    if not user_id:
        return []
    try:
        resp = requests.get(
            f"{BACKEND_URL}/api/v1/history",
            params={"user_id": user_id, "limit": 5},
            timeout=8,
        )
        if resp.ok:
            data = resp.json()
            return data if isinstance(data, list) else data.get("items", [])
    except Exception as e:
        log.error(f"call_history: {e}")
    return []


# ─── Rate limiting ────────────────────────────────────────────────────────────

def check_rate_limit(user_id: int) -> bool:
    r = _get_redis()
    if r is None:
        return True
    try:
        key = f"bot:rate:{user_id}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, RATE_WINDOW)
        return count <= RATE_LIMIT
    except Exception:
        return True


def check_chat_rate_limit(chat_id: int) -> bool:
    r = _get_redis()
    if r is None:
        return True
    try:
        key = f"bot:chatrate:{chat_id}"
        count = r.incr(key)
        if count == 1:
            r.expire(key, RATE_WINDOW)
        return count <= RATE_LIMIT_CHAT
    except Exception:
        return True


# ─── Concurrent job limit ─────────────────────────────────────────────────────

def concurrent_incr(user_id: int) -> int:
    """Increment concurrent job counter. Returns new count."""
    r = _get_redis()
    if r is None:
        return 1
    try:
        key = f"bot:concurrent:{user_id}"
        count = r.incr(key)
        r.expire(key, 600)   # safety TTL
        return count
    except Exception:
        return 1


def concurrent_decr(user_id: int):
    r = _get_redis()
    if r is None:
        return
    try:
        key = f"bot:concurrent:{user_id}"
        val = r.decr(key)
        if val <= 0:
            r.delete(key)
    except Exception:
        pass


def concurrent_get(user_id: int) -> int:
    r = _get_redis()
    if r is None:
        return 0
    try:
        val = r.get(f"bot:concurrent:{user_id}")
        return int(val) if val else 0
    except Exception:
        return 0


# ─── Job tracking ─────────────────────────────────────────────────────────────

def job_set(user_id: int, ck: str, status: str = "processing"):
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(f"bot:activejob:{user_id}", json.dumps({"ck": ck, "status": status}), ex=RATE_WINDOW)
    except Exception:
        pass


def job_get(user_id: int) -> dict | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(f"bot:activejob:{user_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def job_cancel(user_id: int):
    r = _get_redis()
    if r is None:
        return
    try:
        key = f"bot:activejob:{user_id}"
        raw = r.get(key)
        if raw:
            data = json.loads(raw)
            data["status"] = "cancelled"
            r.set(key, json.dumps(data), ex=300)
    except Exception:
        pass


def job_is_cancelled(user_id: int, ck: str) -> bool:
    r = _get_redis()
    if r is None:
        return False
    try:
        raw = r.get(f"bot:activejob:{user_id}")
        if not raw:
            return False
        data = json.loads(raw)
        return data.get("ck") == ck and data.get("status") == "cancelled"
    except Exception:
        return False


def job_clear(user_id: int):
    r = _get_redis()
    if r is None:
        return
    try:
        r.delete(f"bot:activejob:{user_id}")
    except Exception:
        pass


# ─── Cache ────────────────────────────────────────────────────────────────────

def cache_store(ck: str, data: dict):
    r = _get_redis()
    if r is None:
        return
    try:
        r.set(f"bot:dl:{ck}", json.dumps(data), ex=RATE_WINDOW)
    except Exception as e:
        log.warning(f"cache_store: {e}")


def cache_get(ck: str) -> dict | None:
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(f"bot:dl:{ck}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ─── Keyboards ────────────────────────────────────────────────────────────────

def _format_label(fmt: dict) -> str:
    if fmt.get("type") == "audio" or fmt.get("ext") in ("mp3", "m4a"):
        kbps = fmt.get("quality", "").replace("mp3_", "") or "320"
        return f"🎵 MP3 {kbps}kbps"
    height = fmt.get("height") or fmt.get("resolution", "")
    if height:
        return f"📹 {height}p"
    return f"📹 {fmt.get('label', 'Video')}"


def build_format_keyboard(ck: str, formats: list) -> dict:
    rows = []
    for i, fmt in enumerate(formats):
        rows.append([{"text": _format_label(fmt), "callback_data": f"dl:{ck}:{i}"}])
    rows.append([
        {"text": "🔄 Gia hạn",   "callback_data": f"refresh:{ck}"},
        {"text": "❌ Huỷ",       "callback_data": f"cancel:{ck}"},
    ])
    rows.append([
        {"text": "📦 Extension", "callback_data": "cmd:extension"},
        {"text": "❓ Help",      "callback_data": "cmd:help"},
    ])
    return {"inline_keyboard": rows}


def build_retry_keyboard(ck: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "🔄 Thử lại",   "callback_data": f"refresh:{ck}"},
        {"text": "📦 Extension", "callback_data": "cmd:extension"},
    ]]}


def build_spotify_keyboard(ck: str, kind: str) -> dict:
    """For Spotify playlists/albums: first track vs all (ZIP)."""
    if kind == "artist":
        return {"inline_keyboard": [[
            {"text": "🎵 Top 10 tracks", "callback_data": f"sp:{ck}:top"},
            {"text": "📚 Full catalog (Pro)", "callback_data": f"sp:{ck}:full"},
        ], [
            {"text": "❌ Huỷ", "callback_data": f"cancel:{ck}"},
        ]]}
    # playlist / album
    return {"inline_keyboard": [[
        {"text": "🎵 Track đầu tiên", "callback_data": f"sp:{ck}:first"},
        {"text": "📦 Tất cả ZIP (Pro)", "callback_data": f"sp:{ck}:all"},
    ], [
        {"text": "❌ Huỷ", "callback_data": f"cancel:{ck}"},
    ]]}


# ─── Tier enforcement helpers ─────────────────────────────────────────────────

def _tier_check_youtube(user_id: int, quality: str) -> tuple[bool, str]:
    """
    Returns (allowed, reason_message).
    Audio qualities → always allowed.
    Video qualities → require Pro.
    """
    audio_prefixes = ("mp3_", "audio_", "m4a_")
    is_audio = any(quality.startswith(p) for p in audio_prefixes) or quality == "audio"
    if is_audio:
        return True, ""
    info = call_user_info(user_id)
    if not info.get("linked"):
        return False, (
            "⚠️ YouTube video yêu cầu tài khoản Pro.\n"
            "Dùng /link để kết nối VidGrab, sau đó nâng cấp Pro: "
            f"{WEB_URL}/upgrade"
        )
    if info.get("tier") != "pro":
        return False, (
            "⚠️ YouTube video chỉ dành cho Pro.\n"
            f"Nâng cấp tại: {WEB_URL}/upgrade\n\n"
            "Bạn có thể tải *MP3* miễn phí — chọn định dạng 🎵 trong danh sách."
        )
    return True, ""


def _quota_check(tg_user_id: int) -> tuple[bool, str]:
    """
    Returns (allowed, error_message).
    Checks quota via backend for linked users; skips for anonymous.
    """
    info = call_user_info(tg_user_id)
    if not info.get("linked"):
        return True, ""   # anonymous — backend enforces via IP
    used  = info.get("downloads_today", 0)
    limit = info.get("daily_limit", 10)
    if used >= limit:
        tier = info.get("tier", "free")
        if tier == "pro":
            msg = f"⏳ Bạn đã dùng hết {limit} lượt hôm nay (Pro). Quota reset lúc 00:05 UTC."
        else:
            msg = (
                f"⏳ Bạn đã dùng hết {limit} lượt hôm nay (Free).\n"
                f"Nâng cấp Pro để có 100 lượt/ngày: {WEB_URL}/upgrade"
            )
        return False, msg
    return True, ""


# ─── Command handlers ─────────────────────────────────────────────────────────

def cmd_start(chat_id: int):
    kb = {"inline_keyboard": [[
        {"text": "🔗 Kết nối tài khoản", "callback_data": "cmd:link"},
        {"text": "📦 Extension",          "callback_data": "cmd:extension"},
    ], [
        {"text": "❓ Help", "callback_data": "cmd:help"},
    ]]}
    send_message(chat_id, WELCOME_TEXT, reply_markup=kb)


def cmd_help(chat_id: int):
    send_message(chat_id, HELP_TEXT)


def cmd_extension(chat_id: int):
    send_chat_action(chat_id, "upload_document")
    send_extension_file(chat_id)


def cmd_status(chat_id: int):
    send_chat_action(chat_id, "typing")
    health = call_health()
    if health["ok"]:
        redis_ok   = health.get("redis") in ("ok", "connected", "healthy")
        redis_icon = "✅" if redis_ok else "⚠️"
        disk_line  = f"\n💾 Còn: {health['disk_free_gb']:.1f} GB" if health.get("disk_free_gb") else ""
        yt_line    = f"\n🔧 yt-dlp: `{health['yt_dlp']}`"         if health.get("yt_dlp") else ""
        ver_line   = f"\n📦 Phiên bản: `{health['app_version']}`" if health.get("app_version") else ""
        text = f"✅ *Bot & Backend*: Hoạt động tốt\n{redis_icon} Redis: {health.get('redis','ok')}{disk_line}{yt_line}{ver_line}"
    else:
        status = health.get("status", "unknown")
        icons  = {"degraded": "⚠️", "offline": "🔴", "maintenance": "🔧"}
        labels = {"degraded": "Hoạt động không ổn định", "offline": "Backend không phản hồi", "maintenance": "Đang bảo trì"}
        text   = f"{icons.get(status,'❌')} *Backend*: {labels.get(status,'Lỗi')}\n\nThử lại sau hoặc truy cập {WEB_URL}."
    send_message(chat_id, text)


def cmd_cancel(chat_id: int, user_id: int):
    job = job_get(user_id)
    if job and job.get("status") == "processing":
        job_cancel(user_id)
        send_message(chat_id, "✅ Đã huỷ tác vụ đang chờ.")
    else:
        send_message(chat_id, "ℹ️ Không có tác vụ nào đang chờ.")


def cmd_link(chat_id: int, user_id: int, username: str = ""):
    """Generate a link token and send the linking URL to the user."""
    if not BOT_SECRET:
        send_message(
            chat_id,
            "ℹ️ Tính năng kết nối tài khoản chưa được cấu hình.\n"
            f"Truy cập web để quản lý tài khoản: {WEB_URL}"
        )
        return

    # Check if already linked
    info = call_user_info(user_id)
    if info.get("linked"):
        tier = info.get("tier", "free")
        used = info.get("downloads_today", 0)
        limit = info.get("daily_limit", 10)
        tier_label = "🔑 Pro" if tier == "pro" else "🆓 Free"
        send_message(
            chat_id,
            f"✅ Tài khoản đã được kết nối.\n\n"
            f"Gói: {tier_label}\n"
            f"Đã dùng hôm nay: {used}/{limit} lượt\n\n"
            f"Quản lý tại: {WEB_URL}/settings"
        )
        return

    result = call_link_request(user_id, username)
    if not result:
        send_message(
            chat_id,
            "❌ Không thể tạo link kết nối. Thử lại sau.\n"
            f"Hoặc đăng nhập trực tiếp tại: {WEB_URL}"
        )
        return

    link_url = result.get("link_url", "")
    expires  = result.get("expires_in_seconds", 900)
    minutes  = expires // 60

    send_message(
        chat_id,
        f"🔗 *Kết nối tài khoản VidGrab*\n\n"
        f"1. Nhấn link bên dưới\n"
        f"2. Đăng nhập VidGrab (nếu chưa)\n"
        f"3. Nhấn *Xác nhận kết nối*\n\n"
        f"{link_url}\n\n"
        f"⏱ Hết hạn sau {minutes} phút."
    )


def cmd_quota(chat_id: int, tg_user_id: int):
    """Show today's quota status for the linked account."""
    info = call_user_info(tg_user_id)
    if not info.get("linked"):
        send_message(
            chat_id,
            "ℹ️ Chưa kết nối tài khoản VidGrab.\n"
            "Dùng /link để kết nối và xem quota."
        )
        return

    tier  = info.get("tier", "free")
    used  = info.get("downloads_today", 0)
    limit = info.get("daily_limit", 10)
    pct   = min(int(used / limit * 100), 100) if limit else 0
    bar   = "█" * (pct // 10) + "░" * (10 - pct // 10)

    tier_label = "🔑 Pro" if tier == "pro" else "🆓 Free"
    text = (
        f"📊 *Quota hôm nay*\n\n"
        f"Gói: {tier_label}\n"
        f"Đã dùng: {used}/{limit} lượt\n"
        f"`{bar}` {pct}%\n\n"
    )
    if tier != "pro":
        text += f"Nâng cấp Pro (100 lượt/ngày): {WEB_URL}/upgrade"

    send_message(chat_id, text)


def cmd_upgrade(chat_id: int):
    send_message(
        chat_id,
        f"🚀 *VidGrab Pro*\n\n"
        f"• 100 lượt tải/ngày (vs 10 Free)\n"
        f"• YouTube video (Free chỉ MP3)\n"
        f"• Spotify full catalog\n"
        f"• ZIP batch download\n"
        f"• Lịch sử 90 ngày\n"
        f"• API key management\n\n"
        f"Nâng cấp tại: {WEB_URL}/upgrade",
        reply_markup={"inline_keyboard": [[
            {"text": "🔑 Nâng cấp Pro", "url": f"{WEB_URL}/upgrade"},
        ]]}
    )


def cmd_history(chat_id: int, tg_user_id: int):
    """Show last 5 downloads for the linked user."""
    info = call_user_info(tg_user_id)
    if not info.get("linked"):
        send_message(chat_id, "ℹ️ Dùng /link để kết nối tài khoản và xem lịch sử.")
        return

    send_chat_action(chat_id, "typing")
    items = call_history(tg_user_id)
    if not items:
        send_message(chat_id, "📋 Chưa có lịch sử tải.")
        return

    lines = ["📋 *Lịch sử tải gần nhất:*\n"]
    for i, item in enumerate(items[:5], 1):
        title    = item.get("title", "Video")[:50]
        platform = item.get("platform", "")
        ts       = item.get("created_at", "")[:10]
        lines.append(f"{i}. {title} ({platform}) — {ts}")

    send_message(chat_id, "\n".join(lines))


def cmd_pwa(chat_id: int):
    text = (
        "📱 *Cài VidGrab lên màn hình chính*\n\n"
        "Mở VidGrab như app thật — nhanh, không tốn dung lượng\\!\n\n"
        "*Android \\(Chrome\\):*\n"
        "1\\. Mở https://dowloadvideo\\.io\\.vn trên Chrome\n"
        "2\\. Nhấn menu ⋮ \\(3 chấm\\)\n"
        "3\\. Chọn _\"Thêm vào màn hình chính\"_\n"
        "4\\. Nhấn \"Thêm\" → Xong\\! ✅\n\n"
        "*iOS \\(Safari\\):*\n"
        "1\\. Mở https://dowloadvideo\\.io\\.vn trên Safari\n"
        "2\\. Nhấn nút Chia sẻ 🔗 \\(thanh dưới\\)\n"
        "3\\. Chọn _\"Thêm vào màn hình chính\"_\n"
        "4\\. Nhấn \"Thêm\" → Xong\\! ✅\n\n"
        "💡 PWA = app thật, không cần vào Store\\!"
    )
    kb = {"inline_keyboard": [[
        {"text": "🌐 Mở VidGrab", "url": "https://dowloadvideo.io.vn"},
    ]]}
    send_message(chat_id, text, parse_mode="MarkdownV2", reply_markup=kb)


def cmd_share(chat_id: int):
    text = (
        "Chia sẻ VidGrab với bạn bè 🎯\n\n"
        "https://dowloadvideo.io.vn\n\n"
        "Tải video miễn phí từ 40+ nền tảng: TikTok, YouTube, Facebook, Instagram, Spotify và nhiều hơn!"
    )
    send_message(chat_id, text, parse_mode="Markdown")


def cmd_download(chat_id: int, user_id: int, args: str):
    url = extract_url(args)
    if not url:
        send_message(chat_id, "⚠️ Vui lòng cung cấp link video.\nVí dụ: `/download https://youtube.com/watch?v=...`")
        return
    handle_url(chat_id, user_id, url)


def handle_command(chat_id: int, user_id: int, text: str, username: str = ""):
    parts = text.strip().split(None, 1)
    cmd   = parts[0].lower().split("@")[0]
    args  = parts[1] if len(parts) > 1 else ""

    dispatch = {
        "/start":     lambda: cmd_start(chat_id),
        "/help":      lambda: cmd_help(chat_id),
        "/extension": lambda: cmd_extension(chat_id),
        "/status":    lambda: cmd_status(chat_id),
        "/cancel":    lambda: cmd_cancel(chat_id, user_id),
        "/download":  lambda: cmd_download(chat_id, user_id, args),
        "/link":      lambda: cmd_link(chat_id, user_id, username),
        "/quota":     lambda: cmd_quota(chat_id, user_id),
        "/upgrade":   lambda: cmd_upgrade(chat_id),
        "/history":   lambda: cmd_history(chat_id, user_id),
        "/pwa":       lambda: cmd_pwa(chat_id),
        "/share":     lambda: cmd_share(chat_id),
    }
    handler = dispatch.get(cmd)
    if handler:
        handler()
    else:
        send_message(chat_id, "❓ Lệnh không được hỗ trợ. Gõ /help để xem danh sách.")


# ─── URL download flow ────────────────────────────────────────────────────────

def _fetch_and_reply(chat_id: int, user_id: int, url: str, searching_msg_id: int | None,
                     quality: str = "video"):
    """Background thread: fetch video info, send file or URL."""
    ck = cache_key(url)
    job_set(user_id, ck, "processing")

    try:
        # Tier + quota enforcement for YouTube video
        if is_youtube_url(url) and not quality.startswith(("mp3_", "audio_", "m4a_")):
            allowed, reason = _tier_check_youtube(user_id, quality)
            if not allowed:
                if searching_msg_id:
                    edit_message_text(chat_id, searching_msg_id, reason)
                else:
                    send_message(chat_id, reason)
                return

        result = call_fetch_link(url, quality)

        if job_is_cancelled(user_id, ck):
            if searching_msg_id:
                edit_message_text(chat_id, searching_msg_id, "🚫 Tác vụ đã bị huỷ.")
            return

        if result.get("error_message"):
            err = result["error_message"]
            kb  = build_retry_keyboard(ck)
            if searching_msg_id:
                edit_message_text(chat_id, searching_msg_id, f"❌ {err}", reply_markup=kb)
            else:
                send_message(chat_id, f"❌ {err}", reply_markup=kb)
            return

        title   = result.get("title", "Video")
        formats = result.get("available_formats", [])
        platform, _ = detect_platform(url)
        cache_store(ck, {"url": url, "result": result, "quality": quality})

        if formats:
            keyboard = build_format_keyboard(ck, formats)
            sub_badge = " · ✓ Phụ đề" if result.get("has_subtitles") else ""
            body = f"📺 *{platform or 'Video'}* — {title}{sub_badge}\n\nChọn chất lượng:"
            if searching_msg_id:
                edit_message_text(chat_id, searching_msg_id, body, reply_markup=keyboard)
            else:
                send_message(chat_id, body, reply_markup=keyboard)
        else:
            direct_url = result.get("direct_mp4_url", "")
            if direct_url:
                if searching_msg_id:
                    edit_message_text(chat_id, searching_msg_id, f"⬇️ Đang gửi *{title}*...")
                is_audio = quality.startswith(("mp3_", "audio_", "m4a_")) or quality == "audio"
                send_file_or_link(chat_id, direct_url, title, is_audio=is_audio)
            else:
                msg = "❌ Không lấy được link tải."
                if searching_msg_id:
                    edit_message_text(chat_id, searching_msg_id, msg, reply_markup=build_retry_keyboard(ck))
                else:
                    send_message(chat_id, msg, reply_markup=build_retry_keyboard(ck))
    finally:
        job_clear(user_id)
        concurrent_decr(user_id)


def handle_url(chat_id: int, user_id: int, url: str, quality: str = "video"):
    """Validate, rate-limit, quota-check, then spin a fetch thread."""
    if not is_safe_url(url):
        send_message(chat_id, "❌ Liên kết không hợp lệ.")
        return

    platform, is_supported = detect_platform(url)
    if not is_supported:
        send_message(
            chat_id,
            f"❌ Nền tảng này chưa hỗ trợ trong bot.\nThử tại web: {WEB_URL}"
        )
        return

    if not check_chat_rate_limit(chat_id):
        send_message(chat_id, "⏳ Chat này đang bị giới hạn tốc độ. Thử lại sau.")
        return

    if not check_rate_limit(user_id):
        send_message(chat_id, f"⏳ Bạn đã dùng hết {RATE_LIMIT} lượt trong giờ này. Thử lại sau.")
        return

    # Quota check (only for linked users — anonymous still allowed)
    allowed, quota_msg = _quota_check(user_id)
    if not allowed:
        send_message(chat_id, quota_msg)
        return

    # Concurrent job limit for linked users
    count = concurrent_incr(user_id)
    if count > MAX_CONCURRENT_JOBS:
        concurrent_decr(user_id)
        send_message(
            chat_id,
            f"⏳ Bạn đang có {MAX_CONCURRENT_JOBS} tác vụ chạy song song. "
            "Vui lòng chờ hoàn thành trước.\nGõ /cancel để huỷ tác vụ hiện tại."
        )
        return

    sent = send_message(chat_id, "⏳ Đang xử lý...")
    searching_msg_id = sent["result"]["message_id"] if sent and sent.get("ok") else None

    # Spotify: show inline keyboard first (no fetch yet)
    if is_spotify_url(url):
        _handle_spotify_url(chat_id, user_id, url, searching_msg_id)
        concurrent_decr(user_id)
        return

    threading.Thread(
        target=_fetch_and_reply,
        args=(chat_id, user_id, url, searching_msg_id, quality),
        daemon=True,
    ).start()


def _handle_spotify_url(chat_id: int, user_id: int, url: str, msg_id: int | None):
    """Show a keyboard for Spotify playlist/album/artist selection before fetching."""
    url_lower = url.lower()
    if "/artist/" in url_lower:
        kind = "artist"
    elif "/album/" in url_lower:
        kind = "album"
    elif "/playlist/" in url_lower:
        kind = "playlist"
    else:
        # Single track — fetch directly
        threading.Thread(
            target=_fetch_and_reply,
            args=(chat_id, user_id, url, msg_id, "mp3_320"),
            daemon=True,
        ).start()
        return

    ck = cache_key(url)
    cache_store(ck, {"url": url, "result": {}, "kind": kind})
    keyboard = build_spotify_keyboard(ck, kind)
    body = f"🎵 *Spotify {kind.capitalize()}*\n\nChọn cách tải:"
    if msg_id:
        edit_message_text(chat_id, msg_id, body, reply_markup=keyboard)
    else:
        send_message(chat_id, body, reply_markup=keyboard)


# ─── Callback handler ─────────────────────────────────────────────────────────

def handle_callback(cq: dict):
    cq_id   = cq["id"]
    cq_data = cq.get("data", "")
    chat_id = cq["message"]["chat"]["id"]
    msg_id  = cq["message"]["message_id"]
    user_id = cq["from"]["id"]
    username = cq["from"].get("username", "")

    # cmd:{name}
    if cq_data.startswith("cmd:"):
        cmd = cq_data[4:]
        answer_callback_query(cq_id)
        if cmd == "extension":
            cmd_extension(chat_id)
        elif cmd == "help":
            cmd_help(chat_id)
        elif cmd == "link":
            cmd_link(chat_id, user_id, username)
        return

    # cancel:{ck}
    if cq_data.startswith("cancel:"):
        ck = cq_data[len("cancel:"):]
        job_cancel(user_id)
        concurrent_decr(user_id)
        answer_callback_query(cq_id, "✅ Đã huỷ")
        edit_message_text(
            chat_id, msg_id,
            "🚫 Đã huỷ tác vụ.",
            reply_markup={"inline_keyboard": [[
                {"text": "📦 Extension", "callback_data": "cmd:extension"},
            ]]},
        )
        return

    # sp:{ck}:{mode} — Spotify inline choice
    if cq_data.startswith("sp:"):
        parts = cq_data.split(":", 2)
        if len(parts) != 3:
            answer_callback_query(cq_id, "❌ Dữ liệu không hợp lệ")
            return
        _, ck, mode = parts
        answer_callback_query(cq_id, "⏳ Đang xử lý...")
        edit_message_text(chat_id, msg_id, "⏳ Đang xử lý Spotify...")

        cached = cache_get(ck)
        if not cached:
            edit_message_text(chat_id, msg_id, "🔗 Link đã hết hạn. Gửi lại để thử mới.")
            return

        url  = cached.get("url", "")
        kind = cached.get("kind", "playlist")

        # mode=top/first → free OK; mode=full/all → Pro required
        if mode in ("full", "all"):
            info = call_user_info(user_id)
            if not info.get("linked") or info.get("tier") != "pro":
                edit_message_text(
                    chat_id, msg_id,
                    f"⚠️ Tải toàn bộ Spotify {kind} yêu cầu Pro.\n"
                    f"Nâng cấp tại: {WEB_URL}/upgrade"
                )
                return

        quality = "mp3_320"
        if mode == "top":
            url += "?mode=top_tracks"
        elif mode == "first":
            url += "?mode=first"
        elif mode in ("all", "full"):
            url += "?mode=all_tracks"

        count = concurrent_incr(user_id)
        if count > MAX_CONCURRENT_JOBS:
            concurrent_decr(user_id)
            edit_message_text(chat_id, msg_id, "⏳ Quá nhiều tác vụ đang chạy. Thử lại sau.")
            return

        threading.Thread(
            target=_fetch_and_reply,
            args=(chat_id, user_id, url, msg_id, quality),
            daemon=True,
        ).start()
        return

    # dl:{ck}:{fmt_idx} — format selected
    if cq_data.startswith("dl:"):
        parts = cq_data.split(":", 2)
        if len(parts) != 3:
            answer_callback_query(cq_id, "❌ Dữ liệu không hợp lệ")
            return
        _, ck, fmt_idx_str = parts
        try:
            fmt_idx = int(fmt_idx_str)
        except ValueError:
            answer_callback_query(cq_id, "❌ Dữ liệu không hợp lệ")
            return

        cached = cache_get(ck)
        if not cached:
            answer_callback_query(cq_id, "🔗 Link hết hạn")
            edit_message_text(chat_id, msg_id, "🔗 Link đã hết hạn. Gửi lại link để tải mới.")
            return

        result  = cached.get("result", {})
        url     = cached.get("url", "")
        formats = result.get("available_formats", [])
        if fmt_idx >= len(formats):
            answer_callback_query(cq_id, "❌ Định dạng không tồn tại")
            return

        fmt        = formats[fmt_idx]
        direct_url = fmt.get("url") or fmt.get("download_url") or result.get("direct_mp4_url", "")
        title      = result.get("title", "Video")
        quality    = fmt.get("quality", "video")

        # Tier enforcement for YouTube video quality
        if is_youtube_url(url):
            allowed, reason = _tier_check_youtube(user_id, quality)
            if not allowed:
                answer_callback_query(cq_id, "⚠️ Cần Pro")
                edit_message_text(chat_id, msg_id, reason)
                return

        if not direct_url:
            answer_callback_query(cq_id, "❌ Không có URL")
            send_message(chat_id, "❌ Không lấy được link tải cho định dạng này.")
            return

        answer_callback_query(cq_id, "⏳ Đang gửi file...")
        is_audio = fmt.get("type") == "audio" or fmt.get("ext") in ("mp3", "m4a")

        def _send_thread():
            edit_message_text(chat_id, msg_id, f"⬇️ Đang gửi *{title}*...")
            send_file_or_link(chat_id, direct_url, title, is_audio=is_audio)

        threading.Thread(target=_send_thread, daemon=True).start()
        return

    # refresh:{ck}
    if cq_data.startswith("refresh:"):
        ck = cq_data[len("refresh:"):]
        cached = cache_get(ck)
        if not cached:
            answer_callback_query(cq_id, "🔗 Link hết hạn")
            edit_message_text(chat_id, msg_id, "🔗 Link đã hết hạn. Gửi lại link để tải mới.")
            return

        url = cached.get("url")
        if not url:
            answer_callback_query(cq_id, "❌ Không tìm thấy link gốc")
            return

        if not check_rate_limit(user_id):
            answer_callback_query(cq_id, "⏳ Đã hết lượt trong giờ này")
            return

        answer_callback_query(cq_id, "🔍 Đang gia hạn...")
        edit_message_text(chat_id, msg_id, "🔍 Đang gia hạn link...")

        def _refresh_thread():
            result = call_fetch_link(url)
            if result.get("error_message"):
                edit_message_text(chat_id, msg_id, f"❌ {result['error_message']}",
                                  reply_markup=build_retry_keyboard(ck))
                return
            cache_store(ck, {"url": url, "result": result})
            title   = result.get("title", "Video")
            formats = result.get("available_formats", [])
            platform, _ = detect_platform(url)
            if formats:
                edit_message_text(chat_id, msg_id,
                                  f"📺 *{platform or 'Video'}* — {title}\n\nChọn chất lượng:",
                                  reply_markup=build_format_keyboard(ck, formats))
            else:
                direct_url = result.get("direct_mp4_url", "")
                edit_message_text(chat_id, msg_id,
                                  f"⬇️ *{title}*\n\n{direct_url}\n\n⏱ Hết hạn sau 60 phút.",
                                  reply_markup={"inline_keyboard": [[
                                      {"text": "🔄 Gia hạn", "callback_data": f"refresh:{ck}"},
                                  ]]})

        threading.Thread(target=_refresh_thread, daemon=True).start()
        return

    answer_callback_query(cq_id)


# ─── Poll loop ────────────────────────────────────────────────────────────────

def poll():
    offset = 0
    log.info("VidGrab bot started (Phase 10), polling...")

    while True:
        try:
            resp = requests.get(
                f"{API}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=35,
            )
            updates = resp.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1

                if "callback_query" in update:
                    cq       = update["callback_query"]
                    username = cq.get("from", {}).get("username", "unknown")
                    log.info(f"Callback @{username}: {cq.get('data','')[:60]}")
                    try:
                        handle_callback(cq)
                    except Exception as e:
                        log.error(f"handle_callback: {e}")
                    continue

                msg      = update.get("message", {})
                chat_id  = msg.get("chat", {}).get("id")
                text     = (msg.get("text") or "").strip()
                user_id  = msg.get("from", {}).get("id")
                username = msg.get("from", {}).get("username", "unknown")

                if not chat_id:
                    continue

                log.info(f"Message @{username} ({chat_id}): {text[:80]}")

                if text.startswith("/"):
                    try:
                        handle_command(chat_id, user_id, text, username)
                    except Exception as e:
                        log.error(f"handle_command: {e}")
                        send_message(chat_id, "❌ Lỗi xử lý lệnh.")
                    continue

                url = extract_url(text)
                if url:
                    try:
                        handle_url(chat_id, user_id, url)
                    except Exception as e:
                        log.error(f"handle_url: {e}")
                        send_message(chat_id, "❌ Lỗi xử lý link.")
                else:
                    kb = {"inline_keyboard": [[
                        {"text": "🔗 Kết nối tài khoản", "callback_data": "cmd:link"},
                        {"text": "📦 Extension",          "callback_data": "cmd:extension"},
                    ], [
                        {"text": "❓ Help", "callback_data": "cmd:help"},
                    ]]}
                    send_message(
                        chat_id,
                        "📎 Hãy gửi link video để tải về.\n"
                        "Hỗ trợ: YouTube · TikTok · Douyin · Spotify · SoundCloud · Facebook · Instagram · Threads\n\n"
                        f"Ví dụ: `https://youtube.com/watch?v=...`\n\n"
                        f"🌐 Web đầy đủ tính năng: {WEB_URL}",
                        reply_markup=kb,
                    )

        except requests.exceptions.Timeout:
            pass
        except Exception as e:
            log.error(f"Poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    poll()
