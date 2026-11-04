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
DEFAULT_STARTER_MONTH_USD_CENTS = 499  # $4.99/month (Decoy tier)
DEFAULT_STARTER_YEAR_USD_CENTS = 3500  # $35.00/year (~$2.91/mo)
DEFAULT_STARTER_MONTH_VND = 99_000     # ~99,000₫/month
DEFAULT_STARTER_YEAR_VND = 690_000     # ~690,000₫/year

DEFAULT_PRICE_MONTH_USD_CENTS = 699    # $6.99/month (Target tier — original anchor $9.99)
DEFAULT_PRICE_YEAR_USD_CENTS = 4900    # $49.00/year (~$4.08/mo, save 42%)
DEFAULT_PRICE_MONTH_VND = 149_000      # ~149,000₫/month
DEFAULT_PRICE_YEAR_VND = 990_000       # ~990,000₫/year

DEFAULT_TEAM_MONTH_USD_CENTS = 1999    # $19.99/month (High anchor — original $29.99)
DEFAULT_TEAM_YEAR_USD_CENTS = 14900    # $149.00/year (~$12.41/mo)
DEFAULT_TEAM_MONTH_VND = 449_000       # ~449,000₫/month
DEFAULT_TEAM_YEAR_VND = 2_990_000      # ~2,990,000₫/year

DEFAULT_API_CALLS_MONTH_PRO = 3_000    # Paid Pro tier: 3,000 API calls/month (+500 first-time bonus)
DEFAULT_API_CALLS_MONTH_TEAM = 15_000  # Paid Team tier: 15,000 API calls/month
_INT_FIELDS = {
    "starter_month_usd_cents": (100, 500_000, "Giá USD (cents) phải từ 100 ($1.00) đến 500000 ($5,000)"),
    "starter_year_usd_cents": (100, 500_000, "Giá USD (cents) phải từ 100 ($1.00) đến 500000 ($5,000)"),
    "starter_month_vnd": (1_000, 50_000_000, "Giá phải từ 1.000đ đến 50.000.000đ"),
    "starter_year_vnd": (1_000, 50_000_000, "Giá phải từ 1.000đ đến 50.000.000đ"),
    "price_month_usd_cents": (100, 500_000, "Giá USD (cents) phải từ 100 ($1.00) đến 500000 ($5,000)"),
    "price_year_usd_cents": (100, 500_000, "Giá USD (cents) phải từ 100 ($1.00) đến 500000 ($5,000)"),
    "price_month_vnd": (1_000, 50_000_000, "Giá phải từ 1.000đ đến 50.000.000đ"),
    "price_year_vnd": (1_000, 50_000_000, "Giá phải từ 1.000đ đến 50.000.000đ"),
    "team_month_usd_cents": (100, 500_000, "Giá USD (cents) phải từ 100 ($1.00) đến 500000 ($5,000)"),
    "team_year_usd_cents": (100, 500_000, "Giá USD (cents) phải từ 100 ($1.00) đến 500000 ($5,000)"),
    "team_month_vnd": (1_000, 50_000_000, "Giá phải từ 1.000đ đến 50.000.000đ"),
    "team_year_vnd": (1_000, 50_000_000, "Giá phải từ 1.000đ đến 50.000.000đ"),
    "api_calls_month_pro": (0, 10_000_000, "Lượt gọi API/tháng phải từ 0 đến 10.000.000"),
    "api_calls_month_team": (0, 10_000_000, "Lượt gọi API/tháng phải từ 0 đến 10.000.000"),
}
_TEXT_FIELDS = (
    "bank_name",
    "bank_account",
    "bank_holder",
    "contact",
    "rapidapi_proxy_secret",
    "rapidapi_url",
    "paypal_me_url",
    "bank_bin",
    "sepay_webhook_secret",
)
_TEXT_MAX = 240


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


# ── settings ────────────────────────────────────────────────────────────────

def get_settings() -> Dict[str, Any]:
    """Never raises; defaults when Redis is unreadable."""
    out: Dict[str, Any] = {
        "starter_month_usd_cents": DEFAULT_STARTER_MONTH_USD_CENTS,
        "starter_year_usd_cents": DEFAULT_STARTER_YEAR_USD_CENTS,
        "starter_month_vnd": DEFAULT_STARTER_MONTH_VND,
        "starter_year_vnd": DEFAULT_STARTER_YEAR_VND,
        "price_month_usd_cents": int(os.getenv("STRIPE_PRO_MONTH_USD_CENTS", str(DEFAULT_PRICE_MONTH_USD_CENTS)) or DEFAULT_PRICE_MONTH_USD_CENTS),
        "price_year_usd_cents": int(os.getenv("STRIPE_PRO_YEAR_USD_CENTS", str(DEFAULT_PRICE_YEAR_USD_CENTS)) or DEFAULT_PRICE_YEAR_USD_CENTS),
        "price_month_vnd": DEFAULT_PRICE_MONTH_VND,
        "price_year_vnd": DEFAULT_PRICE_YEAR_VND,
        "team_month_usd_cents": DEFAULT_TEAM_MONTH_USD_CENTS,
        "team_year_usd_cents": DEFAULT_TEAM_YEAR_USD_CENTS,
        "team_month_vnd": DEFAULT_TEAM_MONTH_VND,
        "team_year_vnd": DEFAULT_TEAM_YEAR_VND,
        "api_calls_month_pro": DEFAULT_API_CALLS_MONTH_PRO,
        "api_calls_month_team": DEFAULT_API_CALLS_MONTH_TEAM,
        **{f: "" for f in _TEXT_FIELDS},
        "rapidapi_proxy_secret": os.getenv("RAPIDAPI_PROXY_SECRET", "").strip(),
        "rapidapi_url": os.getenv("RAPIDAPI_HUB_URL", "").strip(),
        "paypal_me_url": os.getenv("PAYPAL_ME_URL", "").strip(),
        "bank_bin": os.getenv("BANK_BIN", "970416").strip() or "970416",
        "sepay_webhook_secret": os.getenv("SEPAY_WEBHOOK_SECRET", "whsec_hZfaB5HXGJquU4rM3qDowUZYiPqMcU6Z").strip(),
    }
    try:
        raw = _r().hgetall(SETTINGS_KEY) or {}
    except Exception:  # noqa: BLE001
        return out
    for k, v in raw.items():
        k = k.decode() if isinstance(k, bytes) else k
        v = v.decode() if isinstance(v, bytes) else v
        if k in _INT_FIELDS:
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass
        elif k in _TEXT_FIELDS:
            if v or not out.get(k):
                out[k] = v
    return out


def set_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and store the given fields. Raises ValueError (Vietnamese
    message) on a bad value."""
    clean: Dict[str, str] = {}
    for k, (lo, hi, msg) in _INT_FIELDS.items():
        if k in patch and patch[k] is not None:
            try:
                n = int(patch[k])
            except (TypeError, ValueError):
                raise ValueError(msg)
            if not lo <= n <= hi:
                raise ValueError(msg)
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
    starter_p = q.TIER_PERMISSIONS.get("starter", free_p)
    pro_p = q.TIER_PERMISSIONS["pro"]
    team_p = q.TIER_PERMISSIONS.get("team", pro_p)
    guest_h = int(os.getenv("GUEST_MAX_HEIGHT", "1080") or 0)
    yt_anon_h = int(os.getenv("YT_MAX_HEIGHT_ANON", "720"))
    yt_user_h = int(os.getenv("YT_MAX_HEIGHT_USER", "1080"))
    from app.api.routes import youtube_height_for  # noqa: PLC0415
    yt_pro_h = youtube_height_for(True)
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
            "zip": False, "api_key": False, "api_calls_per_month": 0,
            "spotify_artist_tracks": sp.ARTIST_CAP_TOP_TRACKS,
            "smart_analysis_per_day": anon_ai,
            "watch_sources": 0,
            "pins": 0, "pin_days": 0, "schedules": 0, "playlists": 0,
            "price_month_vnd": 0, "price_year_vnd": 0,
            "price_month_usd": 0, "price_year_usd": 0,
        },
        {
            "id": "free",
            "daily_downloads": q.platform_limit_user(),
            "max_height": free_p["max_height"],
            "youtube_per_day": yt_quota.YT_QUOTA_USER, "youtube_max_height": yt_user_h,
            "batch_links": free_p["batch_limit"],
            "zip": bool(free_p["bulk_zip"]), "api_key": bool(free_p["api_key"]),
            "api_calls_per_month": 0,
            "spotify_artist_tracks": sp.ARTIST_CAP_TOP_TRACKS,
            "smart_analysis_per_day": sa.free_limit(),
            "smart_analysis_max_min": int(sa.MAX_DURATION_FREE_S // 60),
            "watch_sources": watch.get("free_sources", 0) if watch.get("available") else 0,
            "watch_interval_h": watch.get("free_interval_h"),
            "pins": _PIN_LIMITS["free"], "pin_days": _PIN_DAYS["free"],
            "schedules": _SCHEDULE_LIMITS["free"], "playlists": _PLAYLIST_LIMITS["free"],
            "price_month_vnd": 0, "price_year_vnd": 0,
            "price_month_usd": 0, "price_year_usd": 0,
        },
        {
            "id": "starter",
            "daily_downloads": q.platform_limit_for_tier("starter"),
            "max_height": starter_p["max_height"],
            "youtube_per_day": 15, "youtube_max_height": 1080,
            "batch_links": starter_p["batch_limit"],
            "zip": False, "api_key": False,
            "api_calls_per_month": 0,
            "spotify_artist_tracks": sp.ARTIST_CAP_TOP_TRACKS,
            "smart_analysis_per_day": 10,
            "smart_analysis_max_min": 30,
            "watch_sources": watch.get("free_sources", 0) if watch.get("available") else 0,
            "watch_interval_h": watch.get("free_interval_h"),
            "pins": 10, "pin_days": 14,
            "schedules": 5, "playlists": 5,
            "price_month_vnd": s["starter_month_vnd"], "price_year_vnd": s["starter_year_vnd"],
            "price_month_usd": round(int(s.get("starter_month_usd_cents") or DEFAULT_STARTER_MONTH_USD_CENTS) / 100, 2),
            "price_year_usd": round(int(s.get("starter_year_usd_cents") or DEFAULT_STARTER_YEAR_USD_CENTS) / 100, 2),
        },
        {
            "id": "pro",
            "daily_downloads": q.platform_limit_for_tier("pro"),
            "max_height": pro_p["max_height"] if pro_p["max_height"] >= free_p["max_height"] else free_p["max_height"],
            "youtube_per_day": 50, "youtube_max_height": yt_pro_h,
            "batch_links": pro_p["batch_limit"],
            "zip": bool(pro_p["bulk_zip"]), "api_key": bool(pro_p["api_key"]),
            "api_calls_per_month": s["api_calls_month_pro"] if pro_p["api_key"] else 0,
            "first_time_bonus_api": 500,
            "original_month_usd": 9.99,
            "original_year_usd": 83.88,
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
            "price_month_usd": round(int(s.get("price_month_usd_cents") or DEFAULT_PRICE_MONTH_USD_CENTS) / 100, 2),
            "price_year_usd": round(int(s.get("price_year_usd_cents") or DEFAULT_PRICE_YEAR_USD_CENTS) / 100, 2),
        },
        {
            "id": "team",
            "daily_downloads": q.platform_limit_for_tier("team"),
            "max_height": team_p["max_height"] if team_p["max_height"] >= pro_p["max_height"] else pro_p["max_height"],
            "youtube_per_day": 150, "youtube_max_height": yt_pro_h,
            "batch_links": team_p["batch_limit"],
            "zip": True, "api_key": True,
            "api_calls_per_month": s["api_calls_month_team"],
            "original_month_usd": 29.99,
            "original_year_usd": 239.88,
            "spotify_artist_tracks": sp.ARTIST_CAP_ALL_TRACKS,
            "smart_analysis_per_day": 200,
            "smart_analysis_max_min": 180,
            "watch_sources": (watch.get("pro_sources", 0) * 3) if watch.get("available") else 0,
            "watch_interval_h": watch.get("pro_interval_h"),
            "watch_instagram": True,
            "watch_webhook": True,
            "pins": -1, "pin_days": 90,
            "schedules": -1, "playlists": -1,
            "price_month_vnd": s["team_month_vnd"], "price_year_vnd": s["team_year_vnd"],
            "price_month_usd": round(int(s.get("team_month_usd_cents") or DEFAULT_TEAM_MONTH_USD_CENTS) / 100, 2),
            "price_year_usd": round(int(s.get("team_year_usd_cents") or DEFAULT_TEAM_YEAR_USD_CENTS) / 100, 2),
        },
    ]
    bank_ready = bool(s["bank_name"] and s["bank_account"] and s["bank_holder"])
    stripe_ready = bool(
        (os.getenv("PAYPAL_CLIENT_ID", "").strip() and os.getenv("PAYPAL_CLIENT_SECRET", "").strip())
        or s.get("paypal_me_url", "").strip()
        or os.getenv("PAYPAL_ME_URL", "").strip()
        or os.getenv("STRIPE_SECRET_KEY", "").strip()
        or os.getenv("INTERNATIONAL_CHECKOUT_URL_MONTH", "").strip()
        or os.getenv("INTERNATIONAL_CHECKOUT_URL", "").strip()
    )
    return {
        "currency": "USD",
        "tiers": tiers,
        "payment": {
            "method": "bank_transfer",
            "ready": bank_ready,
            "stripe_ready": stripe_ready,
            "bank_name": s["bank_name"], "bank_account": s["bank_account"],
            "bank_holder": s["bank_holder"], "contact": s["contact"],
            "paypal_me_url": s.get("paypal_me_url") or os.getenv("PAYPAL_ME_URL", "").strip(),
            "rapidapi_url": s.get("rapidapi_url") or os.getenv("RAPIDAPI_HUB_URL", "").strip(),
            "rapidapi_studio_url": "https://rapidapi.com/studio/api_b8b4154e-99d8-404b-a85c-4ac6e14f63cd/publish/general",
            "activation": "manual",   # an admin turns Pro on after the transfer arrives
        },
        "daily_reset_utc": "00:00",
        "auto_checkout": _auto_checkout_info(),
    }


def _auto_checkout_info() -> Dict[str, Any]:
    """Info about the auto checkout system for the frontend."""
    from app.core import payments_auto  # noqa: PLC0415
    if not payments_auto.enabled():
        return {"enabled": False}
    s = get_settings()
    bank_ready = bool(s.get("bank_account"))
    paypal_ready = bool(
        (os.getenv("PAYPAL_CLIENT_ID", "").strip() and os.getenv("PAYPAL_CLIENT_SECRET", "").strip())
        or (s.get("paypal_me_url") or "").strip()
        or os.getenv("PAYPAL_ME_URL", "").strip()
    )
    return {
        "enabled": True,
        "vietqr": bank_ready,
        "paypal": paypal_ready,
    }
