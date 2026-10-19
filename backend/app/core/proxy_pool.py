"""
Proxy Pool Manager
==================
Random rotating proxy pool per platform.

Sources (priority order):
  1. Redis pool: proxy_pool:{platform} LIST
  2. Env var: PROXY_POOL_{PLATFORM} (comma-separated proxy URLs)

Proxy URL format: http://user:pass@host:port
Platform → env var mapping:
  youtube   → PROXY_POOL_YT
  tiktok    → PROXY_POOL_TT
  facebook  → PROXY_POOL_FB
  instagram → PROXY_POOL_IG
  douyin    → PROXY_POOL_CN
  twitter   → PROXY_POOL_TW
  reddit    → PROXY_POOL_REDDIT
  default   → PROXY_POOL_DEFAULT
"""

import os
from typing import Optional

from app.core.redis_client import get_redis

_ENV_VARS = {
    "youtube":   "PROXY_POOL_YT",
    "tiktok":    "PROXY_POOL_TT",
    "facebook":  "PROXY_POOL_FB",
    "instagram": "PROXY_POOL_IG",
    "douyin":    "PROXY_POOL_CN",
    # China platforms other than Douyin (owner 14/10): admin pools, metadata only.
    "xiaohongshu": "PROXY_POOL_XHS",
    "kuaishou":  "PROXY_POOL_KS",
    "twitter":   "PROXY_POOL_TW",
    "reddit":    "PROXY_POOL_REDDIT",
    # Channel Watch scans only (channel_watch._watch_proxy) — never downloads.
    "tiktok_watch": "PROXY_POOL_TT_WATCH",
    # "Tất cả nền tảng": fallback only, after a platform blocked the server IP
    # (downloader.extract_video_info_sync, channel_watch._watch_proxy).
    "all":       "PROXY_POOL_ALL",
    "default":   "PROXY_POOL_DEFAULT",
}


def platform_of_url(url: str) -> str:
    """Pool name of a media URL (same mapping as downloader._get_base_opts)."""
    u = (url or "").lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    for host, name in (("tiktok.com", "tiktok"), ("facebook.com", "facebook"), ("fb.watch", "facebook"),
                       ("instagram.com", "instagram"), ("douyin.com", "douyin"), ("iesdouyin.com", "douyin"),
                       ("xiaohongshu.com", "xiaohongshu"), ("xhslink.com", "xiaohongshu"), ("rednote.com", "xiaohongshu"),
                       ("kuaishou.com", "kuaishou"), ("twitter.com", "twitter"),
                       ("x.com", "twitter"), ("reddit.com", "reddit"), ("redd.it", "reddit")):
        if host in u:
            return name
    return "default"


def _env_proxies(platform: str) -> list[str]:
    key = _ENV_VARS.get(platform) or _ENV_VARS["default"]
    raw = os.getenv(key, "") or os.getenv(_ENV_VARS["default"], "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def get_proxy_from_pool(platform: str) -> Optional[str]:
    """The proxy to use for the platform, or None for a direct connection.

    PRIORITY ORDER since 2026-10-19: the first proxy of the admin pool that is
    not cooling down (proxy_manager.mark_proxy_bad — out of traffic, tunnel
    error). The first one added is the main proxy, the next ones are backups.
    It picked one at RANDOM before, ignoring health: a backup proxy carried
    half the traffic (and its cost) all the time, and once the main proxy ran
    out of traffic half the requests still went to it and failed.
    When every proxy is cooling down, the first is returned (a cooled proxy
    may have recovered; nothing is worse than no proxy for YouTube)."""
    rc = get_redis()
    pool_key = f"proxy_pool:{platform}"
    items = [p.strip() for p in (rc.lrange(pool_key, 0, -1) or []) if p and p.strip()]
    if not items:
        items = _env_proxies(platform)
    if not items:
        return None
    from app.core.proxy_manager import _proxy_is_cooled  # noqa: PLC0415
    for p in items:
        if not _proxy_is_cooled(p):
            return p
    return items[0]


EXHAUSTED_COOLDOWN_S = 3600


def next_proxy_after_failure(platform: str, failed: Optional[str], reason: str = "") -> Optional[str]:
    """`failed` ran out of traffic / refused the tunnel: cool it for an hour
    and return the next healthy proxy of the pool, or None when there is no
    other one. Tells the admin once an hour that the backup took over."""
    from app.core.proxy_manager import mark_proxy_bad  # noqa: PLC0415
    from app.core.china_proxy_test import mask  # noqa: PLC0415
    if failed:
        mark_proxy_bad(failed, ttl=EXHAUSTED_COOLDOWN_S)
    nxt = get_proxy_from_pool(platform)
    if not nxt or nxt == failed:
        return None
    try:
        if get_redis().set(f"proxy_failover_alerted:{platform}", "1", ex=3600, nx=True):
            from app.core.notifications import send_telegram_message_sync  # noqa: PLC0415
            # wording: BA review
            send_telegram_message_sync(
                f"🟠 <b>Proxy {platform} lỗi — đã chuyển sang proxy dự phòng</b>\n"
                f"Proxy {mask(failed or '')} lỗi ({reason[:60] or 'không rõ'}), tạm nghỉ 1 giờ.\n"
                f"Đang dùng {mask(nxt)}.\nViệc cần làm: nạp thêm dung lượng hoặc kiểm tra proxy chính."
            )
    except Exception:
        pass
    return nxt


_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")


def normalize_proxy_url(raw: str) -> str:
    """Turn what an admin pastes into one proxy URL, or raise ValueError with a
    Vietnamese reason. 19/10: a whole curl tail was pasted
    ("http://u:p@host:8080 https://ipv4.icanhazip.com") and stored as is —
    every request through it failed with "Unsupported proxy syntax".

    Accepted: scheme://user:pass@host:port, user:pass@host:port,
    host:port:user:pass (the provider dashboards' format), host:port — inside
    a pasted `curl -x ... <url>` command too."""
    import re  # noqa: PLC0415
    text = (raw or "").strip().strip("'\"")
    if not text:
        raise ValueError("Chưa nhập proxy")
    tokens = text.split()
    if len(tokens) > 1:
        # `curl -v -x <proxy> <url>` or "<proxy> <url>": the token after -x /
        # --proxy, else the first one that is not a test URL.
        for flag in ("-x", "--proxy"):
            if flag in tokens and tokens.index(flag) + 1 < len(tokens):
                tokens = [tokens[tokens.index(flag) + 1]]
                break
        cand = [t for t in tokens if not t.startswith("-") and t.lower() != "curl"]
        cand = [t for t in cand if "@" in t or t.count(":") >= 3] or cand
        if not cand:
            raise ValueError("Không tìm thấy proxy trong nội dung đã dán")
        text = cand[0].strip("'\"")
    if not text.lower().startswith(_SCHEMES):
        parts = text.split(":")
        if "@" not in text and len(parts) == 4 and parts[1].isdigit():
            host, port, user, pwd = parts                      # host:port:user:pass
            text = f"http://{user}:{pwd}@{host}:{port}"
        else:
            text = "http://" + text
    m = re.match(r"^(https?|socks5h?)://(?:([^:@/\s]+):([^@/\s]+)@)?([A-Za-z0-9.-]+):(\d{2,5})/?$", text)
    if not m:
        raise ValueError("Sai định dạng. Dùng http://user:pass@host:port hoặc host:port:user:pass")
    if not 0 < int(m.group(5)) < 65536:
        raise ValueError("Cổng không hợp lệ")
    return text.rstrip("/")


def add_proxy(platform: str, proxy_url: str) -> int:
    """Add proxy to Redis pool. Skips duplicates. Returns new pool size."""
    rc = get_redis()
    pool_key = f"proxy_pool:{platform}"
    existing = rc.lrange(pool_key, 0, -1)
    if proxy_url in existing:
        return rc.llen(pool_key)
    rc.rpush(pool_key, proxy_url)
    return rc.llen(pool_key)


def remove_proxy(platform: str, index: int) -> int:
    """Remove proxy at index. Returns remaining pool size."""
    rc = get_redis()
    pool_key = f"proxy_pool:{platform}"
    sentinel = f"__del_{index}__"
    rc.lset(pool_key, index, sentinel)
    rc.lrem(pool_key, 0, sentinel)
    return rc.llen(pool_key)


def get_pool_status() -> dict:
    """Return count of proxies per platform (Redis + env)."""
    rc = get_redis()
    result = {}
    for platform in ("youtube", "tiktok", "facebook", "instagram", "douyin", "xiaohongshu", "kuaishou",
                     "twitter", "reddit", "tiktok_watch", "all"):
        redis_count = rc.llen(f"proxy_pool:{platform}")
        env_count = len(_env_proxies(platform))
        result[platform] = {
            "redis_pool": redis_count,
            "env_fallback": env_count,
            "total": redis_count + env_count,
        }
    return result
