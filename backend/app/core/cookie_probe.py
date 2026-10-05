"""
Cookie live re-test
===================
Asks the PLATFORM whether a pooled cookie is still accepted. The date inside a
cookie file is only the cookie's own claim; platforms extend or revoke
sessions on their own schedule, so a cookie past its date can still work and a
cookie within its date can be dead.

Verdicts a probe may return:
  ok            platform confirmed a logged-in session
  rejected      platform clearly answered "not logged in"
  inconclusive  blocked / rate-limited / unexpected answer — proves nothing

Only `ok` clears an "expired" mark. Nothing here ever deletes a cookie, and a
`rejected`/`inconclusive` result never changes pool selection state — it is
only recorded in the cookie's meta so the admin can read the reason.

Safety:
  * per-cookie cooldown, per-platform minimum gap, global hourly cap — all in
    Redis so they hold across workers and survive a click-storm
  * admin-disabled cookies are never probed
  * platforms without a probe answer `unsupported` instead of guessing
  * results carry fixed strings only; cookie values are never put in a result,
    a log line or an exception message
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Callable, Optional

from app.core.redis_client import get_redis
from app.core import cookie_pool as cp

# ── limits ──────────────────────────────────────────────────────────────────
PER_COOKIE_COOLDOWN_S = 60      # same cookie cannot be re-tested sooner
PER_PLATFORM_GAP_MS = 3000      # min gap between two probes on one platform
HOURLY_CAP = 60                 # probes per rolling hour, all platforms
BATCH_CAP = 20                  # what the UI may queue in one "test all"
PROBE_TIMEOUT_S = 12

OK, REJECTED, INCONCLUSIVE = "ok", "rejected", "inconclusive"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ── cookie parsing (values stay inside this module) ─────────────────────────

def _cookie_header(cookie_b64: str, domains: tuple[str, ...]) -> str:
    """Build a Cookie: header from the Netscape text, only for `domains`."""
    try:
        text = base64.b64decode(cookie_b64).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    pairs = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        dom = parts[0].lstrip(".").lower()
        if any(dom == d or dom.endswith("." + d) or d.endswith("." + dom) for d in domains):
            pairs.append(f"{parts[5]}={parts[6]}")
    return "; ".join(pairs)


def _http_get(url: str, cookie_header: str, headers: dict, proxy: Optional[str]):
    import requests
    h = {"User-Agent": _UA, "Cookie": cookie_header, **headers}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    return requests.get(url, headers=h, timeout=PROBE_TIMEOUT_S,
                        allow_redirects=False, proxies=proxies)


def _json(resp) -> Optional[dict]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ── per-platform probes: (cookie_b64, proxy) -> (verdict, fixed message) ────

def _probe_bilibili(c: str, proxy):
    r = _http_get("https://api.bilibili.com/x/web-interface/nav",
                  _cookie_header(c, ("bilibili.com",)), {}, proxy)
    d = _json(r)
    if d is None:
        return INCONCLUSIVE, "Bilibili trả về dữ liệu không đọc được"
    if (d.get("data") or {}).get("isLogin") is True:
        return OK, "Bilibili xác nhận đang đăng nhập"
    if d.get("code") == -101 or (d.get("data") or {}).get("isLogin") is False:
        return REJECTED, "Bilibili báo chưa đăng nhập (phiên không còn hiệu lực)"
    return INCONCLUSIVE, "Bilibili trả lời không rõ ràng"


def _probe_reddit(c: str, proxy):
    r = _http_get("https://www.reddit.com/api/me.json",
                  _cookie_header(c, ("reddit.com",)), {}, proxy)
    if r.status_code in (403, 429):
        return INCONCLUSIVE, "Reddit chặn hoặc giới hạn yêu cầu kiểm tra"
    d = _json(r)
    if d is None:
        return INCONCLUSIVE, "Reddit trả về dữ liệu không đọc được"
    if (d.get("data") or {}).get("name"):
        return OK, "Reddit xác nhận đang đăng nhập"
    if r.status_code == 200 and not d.get("data"):
        return REJECTED, "Reddit báo chưa đăng nhập (phiên không còn hiệu lực)"
    return INCONCLUSIVE, "Reddit trả lời không rõ ràng"


def _probe_instagram(c: str, proxy):
    r = _http_get(
        "https://www.instagram.com/api/v1/accounts/current_user/?edit=true",
        _cookie_header(c, ("instagram.com",)),
        {"X-IG-App-ID": "936619743392459", "X-Requested-With": "XMLHttpRequest"},
        proxy,
    )
    if r.status_code == 429:
        return INCONCLUSIVE, "Instagram giới hạn yêu cầu kiểm tra"
    d = _json(r) or {}
    if r.status_code == 200 and d.get("user"):
        return OK, "Instagram xác nhận đang đăng nhập"
    if r.status_code in (401, 302) or d.get("message") == "login_required":
        return REJECTED, "Instagram yêu cầu đăng nhập lại (phiên không còn hiệu lực)"
    return INCONCLUSIVE, "Instagram trả lời không rõ ràng"


def _probe_youtube(c: str, proxy):
    r = _http_get("https://www.youtube.com/",
                  _cookie_header(c, ("youtube.com", "google.com")),
                  {"Accept-Language": "en-US,en;q=0.9"}, proxy)
    body = r.text if r.status_code == 200 else ""
    if '"LOGGED_IN":true' in body:
        return OK, "YouTube xác nhận đang đăng nhập"
    if '"LOGGED_IN":false' in body:
        return REJECTED, "YouTube báo chưa đăng nhập (phiên không còn hiệu lực)"
    return INCONCLUSIVE, "YouTube trả lời không rõ ràng (có thể là trang đồng ý/chặn)"


def _probe_tiktok(c: str, proxy):
    r = _http_get(
        "https://www.tiktok.com/passport/web/account/info/"
        "?aid=1459&app_language=en&device_platform=web_pc",
        _cookie_header(c, ("tiktok.com",)), {"Referer": "https://www.tiktok.com/"}, proxy,
    )
    if r.status_code in (403, 429):
        return INCONCLUSIVE, "TikTok chặn hoặc giới hạn yêu cầu kiểm tra"
    d = _json(r)
    if d is None:
        return INCONCLUSIVE, "TikTok trả về dữ liệu không đọc được"
    data = d.get("data") or {}
    if data.get("user_id_str") or data.get("username"):
        return OK, "TikTok xác nhận đang đăng nhập"
    if "data" in d and d.get("message") in ("error", "fail"):
        return REJECTED, "TikTok báo phiên đăng nhập không còn hiệu lực"
    return INCONCLUSIVE, "TikTok trả lời không rõ ràng"


# Platforms not listed here answer `unsupported`: we have no reliable,
# login-free-of-side-effects endpoint for them, and guessing would mislead.
PROBES: dict[str, Callable] = {
    "youtube": _probe_youtube,
    "tiktok": _probe_tiktok,
    "instagram": _probe_instagram,
    "reddit": _probe_reddit,
    "bilibili": _probe_bilibili,
}


def supported_platforms() -> list[str]:
    return sorted(PROBES)


# ── helpers ─────────────────────────────────────────────────────────────────

recently_verified = cp.recently_verified


def _find(rc, platform: str, h: str) -> Optional[str]:
    for c in rc.lrange(f"cookie_pool:{platform}", 0, -1):
        if cp._hash(c) == h:
            return c
    return None


def _result(status: str, message: str, **extra) -> dict:
    return {"status": status, "message": message, **extra}


# ── main entry ──────────────────────────────────────────────────────────────

def retest_cookie(platform: str, cookie_hash: str,
                  probe: Optional[Callable] = None) -> dict:
    """
    Re-test one pooled cookie against its platform.

    status: ok | rejected | inconclusive | unsupported | skipped_disabled |
            not_found | rate_limited
    Blocking (network) — call from a worker thread.
    """
    rc = get_redis()
    cookie = _find(rc, platform, cookie_hash)
    if cookie is None:
        return _result("not_found", "Không tìm thấy cookie này trong kho")

    health = rc.get(f"cookie_health:{platform}:{cookie_hash}")
    if health == "disabled":
        return _result("skipped_disabled",
                       "Cookie đang bị tắt thủ công, không kiểm tra (hãy bật lại trước)")

    probe = probe or PROBES.get(platform)
    if probe is None:
        return _result("unsupported", "Chưa hỗ trợ kiểm tra thật cho nền tảng này")

    # ── limits: checked before any network call, in cheapest-first order ──
    cd_key = f"cookie_retest_cd:{platform}:{cookie_hash}"
    ttl = rc.ttl(cd_key)
    if ttl and ttl > 0:
        return _result("rate_limited", "Cookie này vừa được kiểm tra, hãy chờ một lát",
                       reason="cookie_cooldown", retry_after_s=int(ttl))
    gap_key = f"cookie_retest_gap:{platform}"
    if not rc.set(gap_key, "1", nx=True, px=PER_PLATFORM_GAP_MS):
        pttl = rc.pttl(gap_key)
        return _result("rate_limited", "Đang giãn cách giữa các lần kiểm tra cùng nền tảng",
                       reason="platform_gap",
                       retry_after_s=max(1, int((pttl or PER_PLATFORM_GAP_MS) / 1000) + 1))
    cap_key = f"cookie_retest_hour:{int(time.time() // 3600)}"
    used = rc.incr(cap_key)
    if used == 1:
        rc.expire(cap_key, 3700)
    if used > HOURLY_CAP:
        return _result("rate_limited", "Đã đạt giới hạn số lần kiểm tra mỗi giờ",
                       reason="hourly_cap", retry_after_s=600)
    rc.set(cd_key, "1", ex=PER_COOKIE_COOLDOWN_S)

    # ── probe ──
    try:
        proxy = None
        try:
            from app.core.proxy_pool import get_proxy_from_pool
            proxy = get_proxy_from_pool(platform)
        except Exception:
            proxy = None
        verdict, message = probe(cookie, proxy)
    except Exception as e:  # class name only: messages can echo request data
        verdict, message = INCONCLUSIVE, f"Không kết nối được tới nền tảng ({type(e).__name__})"

    now = int(time.time())
    meta = cp._get_meta(rc, platform, cookie_hash)
    meta["last_test_at"] = now
    meta["last_test_status"] = verdict
    meta["last_test_message"] = message
    cleared = False
    if verdict == OK:
        meta["verified_ok_at"] = now
        if health == "expired":
            rc.delete(f"cookie_health:{platform}:{cookie_hash}")
            cleared = True
    cp._set_meta(rc, platform, cookie_hash, meta)

    return _result(verdict, message, cleared_expired=cleared, tested_at=now)


# ── storage status ("is it being saved?") ───────────────────────────────────

def storage_status() -> dict:
    """Real Redis persistence facts, or an honest 'cannot read'."""
    rc = get_redis()
    total = 0
    try:
        for key in rc.keys("cookie_pool:*") or []:
            total += int(rc.llen(key))
    except Exception:
        return {"store": "redis", "readable": False, "total_cookies": None,
                "message": "Không đọc được kho cookie"}
    out = {"store": "redis", "total_cookies": total, "readable": False,
           "message": "Không đọc được trạng thái lưu bền"}
    try:
        info = rc.info("persistence")
    except Exception:
        return out
    if not isinstance(info, dict) or not info:
        return out
    last_save = info.get("rdb_last_save_time")
    out.update({
        "readable": True,
        "message": "",
        "aof_enabled": bool(int(info.get("aof_enabled", 0))),
        "rdb_last_save_time": int(last_save) if last_save else None,
        "rdb_last_bgsave_status": info.get("rdb_last_bgsave_status"),
        "aof_last_write_status": info.get("aof_last_write_status"),
    })
    return out
