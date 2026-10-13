"""
Plans shown to users — Khách / Free / Pro (docs/PLANS-FREE-PRO.md, owner 13/10).

One source: every number below is read from the constant or env the server
ENFORCES (quotas, yt_quota, watch_config, smart_analysis, storage, …). Until
13/10 the upgrade modal and pricing page hard-coded their own numbers and said
"Free 10/day", "YouTube Free MP3 only", "4K Pro only" while the server gave 20,
video and 4K. Nothing is listed here that no code enforces (priority queue,
cloud save, chapters, concurrent jobs).

Price and bank-transfer details are admin settings (owner rule: settings in
admin, not env):  Redis hash  plans:settings
Payment for now: bank transfer, then an admin grants Pro for 30/365 days
(billing_status='manual', see entitlements.resolve_effective_tier).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

SETTINGS_KEY = "plans:settings"
DEFAULT_PRICE_MONTH_VND = 49_000      # owner 13/10
DEFAULT_PRICE_YEAR_VND = 399_000      # owner 13/10
_TEXT_FIELDS = ("bank_name", "bank_account", "bank_holder", "contact")
_TEXT_MAX = 120


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


# ── settings ────────────────────────────────────────────────────────────────

def get_settings() -> Dict[str, Any]:
    """Never raises; defaults when Redis is unreadable."""
    out: Dict[str, Any] = {
        "price_month_vnd": DEFAULT_PRICE_MONTH_VND,
        "price_year_vnd": DEFAULT_PRICE_YEAR_VND,
        **{f: "" for f in _TEXT_FIELDS},
    }
    try:
        raw = _r().hgetall(SETTINGS_KEY) or {}
    except Exception:  # noqa: BLE001
        return out
    for k, v in raw.items():
        k = k.decode() if isinstance(k, bytes) else k
        v = v.decode() if isinstance(v, bytes) else v
        if k in ("price_month_vnd", "price_year_vnd"):
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass
        elif k in _TEXT_FIELDS:
            out[k] = v
    return out


def set_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and store the given fields. Raises ValueError (Vietnamese
    message) on a bad value."""
    clean: Dict[str, str] = {}
    for k in ("price_month_vnd", "price_year_vnd"):
        if k in patch and patch[k] is not None:
            try:
                n = int(patch[k])
            except (TypeError, ValueError):
                raise ValueError("Giá phải là số nguyên (đồng)")
            if not 1_000 <= n <= 50_000_000:
                raise ValueError("Giá phải từ 1.000đ đến 50.000.000đ")
            clean[k] = str(n)
    for k in _TEXT_FIELDS:
        if k in patch and patch[k] is not None:
            v = " ".join(str(patch[k]).split())
            if len(v) > _TEXT_MAX:
                raise ValueError("Nội dung quá dài")
            clean[k] = v
    if clean:
        _r().hset(SETTINGS_KEY, mapping=clean)
    return get_settings()


# ── transfer code ───────────────────────────────────────────────────────────

_CODE_RE = re.compile(r"^[0-9a-f]{8}$")


def transfer_code(user_id: str) -> str:
    """'VG' + first 8 hex of the account id: short enough for a bank memo,
    and the admin can find the account by it (find_user_by_code)."""
    return "VG" + (user_id or "").replace("-", "")[:8].upper()


def code_prefix(code: str) -> Optional[str]:
    c = (code or "").strip().upper()
    if c.startswith("VG"):
        c = c[2:]
    c = c.lower()
    return c if _CODE_RE.match(c) else None


# ── the plan table ──────────────────────────────────────────────────────────

def _watch() -> Dict[str, Any]:
    try:
        from app.core import watch_config as wc  # noqa: PLC0415
        open_to_users = wc.enabled() and not wc.admin_only() and not wc.kill_switch()
        return {
            "available": bool(open_to_users),
            "free_sources": wc.max_sources("free"),
            "pro_sources": wc.max_sources("pro"),
            "free_interval_h": round(wc.min_interval_sec("free") / 3600),
            "pro_interval_h": round(wc.min_interval_sec("pro") / 3600),
            "instagram_pro": "instagram" in wc.platforms_enabled(),
        }
    except Exception:  # noqa: BLE001
        return {"available": False}


def build_plans() -> Dict[str, Any]:
    from app.core import quotas as q  # noqa: PLC0415
    from app.core import yt_quota  # noqa: PLC0415
    from app.core import spotify_artist_ops as sp  # noqa: PLC0415
    from app.api import smart_analysis as sa  # noqa: PLC0415
    from app.api.storage import _PIN_DAYS, _PIN_LIMITS  # noqa: PLC0415
    from app.api.schedule import _SCHEDULE_LIMITS  # noqa: PLC0415
    from app.api.playlists import _PLAYLIST_LIMITS  # noqa: PLC0415

    free_p = q.TIER_PERMISSIONS["free"]
    pro_p = q.TIER_PERMISSIONS["pro"]
    guest_h = int(os.getenv("GUEST_MAX_HEIGHT", "1080") or 0)
    yt_anon_h = int(os.getenv("YT_MAX_HEIGHT_ANON", "720"))
    yt_user_h = int(os.getenv("YT_MAX_HEIGHT_USER", "1080"))
    anon_ai = sa.anon_limit()
    watch = _watch()
    s = get_settings()

    tiers: List[Dict[str, Any]] = [
        {
            "id": "guest",
            "daily_downloads": q.platform_limit_anon(),
            "max_height": guest_h,
            "youtube_per_day": yt_quota.YT_QUOTA_ANON, "youtube_max_height": yt_anon_h,
            "batch_links": free_p["batch_limit"],
            "zip": False, "api_key": False,
            "spotify_artist_tracks": sp.ARTIST_CAP_TOP_TRACKS,
            "smart_analysis_per_day": anon_ai,
            "watch_sources": 0,
            "pins": 0, "pin_days": 0, "schedules": 0, "playlists": 0,
            "price_month_vnd": 0, "price_year_vnd": 0,
        },
        {
            "id": "free",
            "daily_downloads": q.platform_limit_user(),
            "max_height": free_p["max_height"],
            "youtube_per_day": yt_quota.YT_QUOTA_USER, "youtube_max_height": yt_user_h,
            "batch_links": free_p["batch_limit"],
            "zip": bool(free_p["bulk_zip"]), "api_key": bool(free_p["api_key"]),
            "spotify_artist_tracks": sp.ARTIST_CAP_TOP_TRACKS,
            "smart_analysis_per_day": sa.free_limit(),
            "smart_analysis_max_min": int(sa.MAX_DURATION_FREE_S // 60),
            "watch_sources": watch.get("free_sources", 0) if watch.get("available") else 0,
            "watch_interval_h": watch.get("free_interval_h"),
            "pins": _PIN_LIMITS["free"], "pin_days": _PIN_DAYS["free"],
            "schedules": _SCHEDULE_LIMITS["free"], "playlists": _PLAYLIST_LIMITS["free"],
            "price_month_vnd": 0, "price_year_vnd": 0,
        },
        {
            "id": "pro",
            "daily_downloads": q.platform_limit_for_tier("pro"),
            "max_height": pro_p["max_height"] if pro_p["max_height"] >= free_p["max_height"] else free_p["max_height"],
            "youtube_per_day": yt_quota.YT_QUOTA_USER, "youtube_max_height": yt_user_h,
            "batch_links": pro_p["batch_limit"],
            "zip": bool(pro_p["bulk_zip"]), "api_key": bool(pro_p["api_key"]),
            "spotify_artist_tracks": sp.ARTIST_CAP_ALL_TRACKS,
            "smart_analysis_per_day": sa.pro_limit(),
            "smart_analysis_max_min": int(sa.MAX_DURATION_PRO_S // 60),
            "watch_sources": watch.get("pro_sources", 0) if watch.get("available") else 0,
            "watch_interval_h": watch.get("pro_interval_h"),
            "watch_instagram": bool(watch.get("available") and watch.get("instagram_pro")),
            "watch_webhook": bool(watch.get("available")),
            "pins": _PIN_LIMITS["pro"], "pin_days": _PIN_DAYS["pro"],
            "schedules": _SCHEDULE_LIMITS["pro"], "playlists": _PLAYLIST_LIMITS["pro"],
            "price_month_vnd": s["price_month_vnd"], "price_year_vnd": s["price_year_vnd"],
        },
    ]
    bank_ready = bool(s["bank_name"] and s["bank_account"] and s["bank_holder"])
    return {
        "currency": "VND",
        "tiers": tiers,
        "payment": {
            "method": "bank_transfer",
            "ready": bank_ready,
            "bank_name": s["bank_name"], "bank_account": s["bank_account"],
            "bank_holder": s["bank_holder"], "contact": s["contact"],
            "activation": "manual",   # an admin turns Pro on after the transfer arrives
        },
        "daily_reset_utc": "00:00",
    }
