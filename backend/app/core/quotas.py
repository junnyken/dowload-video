"""
Quota & Tier System — VidGrab
==============================
Download allowance (since 2026-10-06): PER PLATFORM per UTC day — guest 5
(PLATFORM_DAILY_LIMIT_ANON), signed-in free 20 (PLATFORM_DAILY_LIMIT_USER),
paid tiers their configured daily_limit (never less than free), admin
unlimited. See "Per-platform daily allowance" below.

Tier settings (env-tunable): batch sizes, max quality, features, history.
FREE_DAILY_LIMIT / ANON_DAILY_LIMIT no longer block downloads (legacy /quota
output only).

Grace period: billing_status='canceling' + subscription_expiry > now → still Pro

Error codes (returned in 402/403/429 responses):
  quota_exceeded_daily    — daily download cap hit
  tier_limit_quality      — quality requires Pro (free capped at 1080p)
  tier_limit_batch        — batch size exceeds tier limit
  tier_required_feature   — feature not available on free tier
  youtube_pro_only        — YouTube video download requires Pro
  spotify_artist_full     — Spotify artist full mode (albums/singles/all_tracks) requires Pro
  bulk_zip_pro            — ZIP download requires Pro
"""

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from app.core.database import get_supabase_client

# ── Tier limits (env-tunable) ─────────────────────────────────────────
FREE_DAILY_LIMIT  = int(os.getenv("FREE_DAILY_LIMIT",   "10"))
PRO_DAILY_LIMIT   = int(os.getenv("PRO_DAILY_LIMIT",   "100"))
FREE_BATCH_LIMIT  = int(os.getenv("FREE_BATCH_LIMIT",    "5"))
PRO_BATCH_LIMIT   = int(os.getenv("PRO_BATCH_LIMIT",   "100"))
ANON_DAILY_LIMIT  = int(os.getenv("ANON_DAILY_LIMIT",    "5"))

# Quality strings that explicitly request 4K
PRO_ONLY_QUALITY_STRINGS = {"mp4_4k", "4k", "2160"}

TIER_PERMISSIONS: Dict[str, Dict[str, Any]] = {
    "free": {
        "daily_limit":          FREE_DAILY_LIMIT,
        "batch_limit":          FREE_BATCH_LIMIT,
        "max_height":           1080,
        "youtube_download":     "video",   # video allowed, capped 1080p + 5/day
        "bulk_zip":             False,
        "spotify_artist_full":  False,     # only top_tracks
        "cloud_save":           False,
        "chapters":             False,
        "logo_inpaint":         False,
        "history_limit":        20,
        "history_days":         7,
        "api_key":              False,
    },
    "pro": {
        "daily_limit":          PRO_DAILY_LIMIT,
        "batch_limit":          PRO_BATCH_LIMIT,
        "max_height":           2160,
        "youtube_download":     "video",   # full video allowed
        "bulk_zip":             True,
        "spotify_artist_full":  True,
        "cloud_save":           True,
        "chapters":             True,
        "logo_inpaint":         True,
        "history_limit":        None,      # unlimited
        "history_days":         90,
        "api_key":              True,
    },
    "team": {
        "daily_limit":          int(os.getenv("TEAM_DAILY_LIMIT", "500")),
        "batch_limit":          200,
        "max_height":           2160,
        "youtube_download":     "video",
        "bulk_zip":             True,
        "spotify_artist_full":  True,
        "cloud_save":           True,
        "chapters":             True,
        "logo_inpaint":         True,
        "history_limit":        None,
        "history_days":         90,
        "api_key":              True,
    },
    "enterprise": {
        "daily_limit":          -1,        # unlimited
        "batch_limit":          -1,
        "max_height":           2160,
        "youtube_download":     "video",
        "bulk_zip":             True,
        "spotify_artist_full":  True,
        "cloud_save":           True,
        "chapters":             True,
        "logo_inpaint":         True,
        "history_limit":        None,
        "history_days":         -1,
        "api_key":              True,
    },
}

# ── Error codes ───────────────────────────────────────────────────────
ERR_QUOTA_DAILY    = "quota_exceeded_daily"
ERR_QUALITY        = "tier_limit_quality"
ERR_BATCH          = "tier_limit_batch"
ERR_FEATURE        = "tier_required_feature"
ERR_YOUTUBE        = "youtube_pro_only"
ERR_SPOTIFY_FULL   = "spotify_artist_full"
ERR_BULK_ZIP       = "bulk_zip_pro"


# ── Internal helpers ──────────────────────────────────────────────────

def _today_utc_str() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _get_profile(user_id: str) -> Dict[str, Any]:
    """Fetch tier, billing_status, subscription_expiry in one query."""
    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("profiles")
            # grace_period_ends_at is part of the shared tier rule — omitting it
            # silently turned a past_due account's grace period into a downgrade.
            .select("tier, billing_status, subscription_expiry, grace_period_ends_at")
            .eq("id", user_id)
            .single()
            .execute()
        )
        return res.data or {}
    except Exception:
        return {}


def _effective_tier(profile: Dict[str, Any]) -> str:
    """
    Delegates to the single shared rule in entitlements.resolve_effective_tier.

    This used to implement its own version and disagreed with entitlements.py
    on two points that both mattered:

      - a paid tier with billing_status='none' was honoured here but treated as
        free there, so the account menu said ENTERPRISE while every
        require_feature gate refused
      - during a grace period it returned a hardcoded "pro", quietly demoting a
        Team or Enterprise account for the duration of a payment problem
    """
    from app.core.entitlements import resolve_effective_tier  # noqa: PLC0415
    return resolve_effective_tier(profile)


def _get_tier(user_id: str) -> str:
    """Return effective tier ('free' or 'pro') for authenticated user."""
    return _effective_tier(_get_profile(user_id))


def _get_usage(user_id: str) -> Dict[str, Any]:
    """Fetch current usage row for user (lazy reset on first call of the day)."""
    try:
        supabase = get_supabase_client()
        res = (
            supabase.table("user_usage")
            .select("downloads_today, last_reset_at")
            .eq("user_id", user_id)
            .single()
            .execute()
        )
        row = res.data or {}

        # Lazy daily reset: if last_reset_at < today UTC midnight, zero out counter
        last_reset = row.get("last_reset_at")
        today_midnight = _today_utc_str()
        if last_reset and last_reset < today_midnight:
            try:
                supabase.table("user_usage").update({
                    "downloads_today": 0,
                    "last_reset_at":   today_midnight,
                }).eq("user_id", user_id).execute()
                row["downloads_today"] = 0
            except Exception:
                pass

        return row
    except Exception:
        return {}


# ── Per-platform daily allowance (owner decision 2026-10-06) ─────────
#
# "Khách chưa đăng ký 5 lượt/ngày mỗi nền tảng; tài khoản đăng ký 20 lượt/ngày
# mỗi nền tảng; admin không giới hạn — cho mọi nền tảng."
#
# One counter per (requester, platform, UTC day). The platform is
# app.core.platform_key.platform_key — the same slug download stats use; every
# unrecognised site shares the single "other" counter. This replaced the
# standard/cheap buckets (ANON_DAILY_LIMIT, FREE_DAILY_LIMIT as the enforced
# total, GUEST_CHEAP_DAILY/FREE_CHEAP_DAILY/CHEAP_PLATFORMS) as what blocks a
# download. FREE_DAILY_LIMIT & co. still feed TIER_PERMISSIONS (batch sizes,
# the paid tiers' per-platform numbers below, legacy /quota output).
#
# Requesters:
#   admin  a valid admin SESSION token in X-Admin-Token (POST /admin/login) —
#          unlimited, nothing counted. The raw admin password is not accepted.
#   user   signed-in Supabase account — PLATFORM_DAILY_LIMIT_USER for free;
#          paid tiers get their configured daily_limit, never less than free;
#          enterprise (-1) unlimited.
#   anon   guest, by client IP — PLATFORM_DAILY_LIMIT_ANON.
# A limit of -1 means unlimited.
#
# What counts: one SUCCESSFUL download (same moment as before: after the
# extractor returned). The same URL downloaded again by the same requester on
# the same UTC day is not counted twice — "analyse" then "download in 720p"
# is one video, one lượt — and is never refused for being over the limit.
#
# Day boundary: UTC midnight = 07:00 in Vietnam (UTC+7, no DST). The text
# shown to users is computed from the actual reset instant, never hardcoded.

REQ_ADMIN = "admin"
REQ_USER  = "user"
REQ_ANON  = "anon"
# Windows app guest, counted per machine (task #6087, PLAN-32D §4): key
# "dev:<first 32 hex of the device hash>". Same limit and wording as a guest.
REQ_DEVICE = "device"
_DEVICE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def valid_device_hash(value: Optional[str]) -> Optional[str]:
    """The X-VG-Device header value when it is a 64-hex hash, else None."""
    v = (value or "").strip().lower()
    return v if _DEVICE_HASH_RE.match(v) else None

QUOTA_SCOPE_PLATFORM = "per_platform"
QUOTA_SCOPE_TOTAL = "total"
_TOTAL_BUCKET = "_all"


def quota_scope() -> str:
    """Owner 2026-10-06 (correction after the per-platform release): a guest
    gets 5 downloads a day IN TOTAL across all platforms, a signed-in user 20
    in total. QUOTA_SCOPE=per_platform restores one allowance per platform.
    Per-platform counters are always kept for display either way."""
    v = (os.getenv("QUOTA_SCOPE") or QUOTA_SCOPE_TOTAL).strip().lower()
    return QUOTA_SCOPE_PLATFORM if v == QUOTA_SCOPE_PLATFORM else QUOTA_SCOPE_TOTAL


def _enforced_bucket(platform: str) -> str:
    """Which counter the allowance is checked against."""
    return platform if quota_scope() == QUOTA_SCOPE_PLATFORM else _TOTAL_BUCKET
_VN_TZ = timezone(timedelta(hours=7))   # Asia/Ho_Chi_Minh has no DST


def _env_limit(name: str, default: int) -> int:
    try:
        v = int(str(os.getenv(name, "")).strip() or default)
    except (TypeError, ValueError):
        return default
    return -1 if v < 0 else v


def platform_limit_anon() -> int:
    return _env_limit("PLATFORM_DAILY_LIMIT_ANON", 5)


def platform_limit_user() -> int:
    return _env_limit("PLATFORM_DAILY_LIMIT_USER", 20)


def platform_limit_for_tier(tier: str) -> int:
    """Per-platform daily limit of a signed-in tier. -1 = unlimited.

    free → PLATFORM_DAILY_LIMIT_USER. A paid tier keeps its configured
    daily_limit (PRO_DAILY_LIMIT 100, TEAM_DAILY_LIMIT 500, enterprise -1) and
    never gets less than free. FREE_DAILY_LIMIT plays no part here: production
    sets it to 1000 as the old TOTAL, which would make every paid tier 1000
    per platform."""
    base = platform_limit_user()
    if tier == "free" or tier not in TIER_PERMISSIONS:
        return base
    configured = TIER_PERMISSIONS[tier]["daily_limit"]
    if configured == -1 or base == -1:
        return -1
    return max(int(configured), base)


def is_admin_request(request) -> bool:
    """True only for a live admin SESSION token in X-Admin-Token (issued by
    POST /admin/login; same check the China access layer's admin canary uses).
    The raw admin password is deliberately not accepted: this is read on public
    download endpoints, where accepting it would add a brute-force surface
    without verify_admin's lockout. Redis down → False (fail closed)."""
    if request is None:
        return False
    try:
        token = (request.headers.get("X-Admin-Token") or "").strip()
        if not token:
            return False
        from app.api.admin import _redis, _session_is_valid  # noqa: PLC0415
        return bool(_session_is_valid(_redis(), token))
    except Exception:
        return False


class QuotaRequester:
    """Who a download is counted against. `key` is also the China access
    layer's requester key ("admin" | "user:<id>" | "ip:<ip>")."""

    __slots__ = ("kind", "ident", "_tier")

    def __init__(self, kind: str, ident: str = "", tier: Optional[str] = None):
        self.kind = kind
        self.ident = ident or ""
        self._tier = tier

    @property
    def key(self) -> str:
        if self.kind == REQ_ADMIN:
            return "admin"
        if self.kind == REQ_USER:
            return f"user:{self.ident}"
        if self.kind == REQ_DEVICE:
            return f"dev:{self.ident}"
        return f"ip:{self.ident or 'unknown'}"

    @property
    def tier(self) -> str:
        if self.kind != REQ_USER:
            return "free"
        if self._tier is None:
            self._tier = _get_tier(self.ident)
        return self._tier

    @classmethod
    def from_key(cls, key: Optional[str]) -> Optional["QuotaRequester"]:
        """Inverse of .key, for Celery kwargs. None/unknown → None."""
        key = (key or "").strip()
        if key == "admin":
            return cls(REQ_ADMIN)
        if key.startswith("user:") and len(key) > 5:
            return cls(REQ_USER, key[5:])
        if key.startswith("ip:") and len(key) > 3 and key != "ip:unknown":
            return cls(REQ_ANON, key[3:])
        if key.startswith("dev:") and len(key) > 4:
            return cls(REQ_DEVICE, key[4:])
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"QuotaRequester({self.key!r})"


def resolve_requester(request=None, user_id: Optional[str] = None,
                      ip: Optional[str] = None, is_admin: Optional[bool] = None,
                      device_id: Optional[str] = None) -> QuotaRequester:
    """admin session > signed-in user > Windows app machine > guest IP.
    device_id: the app's X-VG-Device hash (validated; anything else ignored)."""
    if is_admin is None:
        is_admin = is_admin_request(request)
    if is_admin:
        return QuotaRequester(REQ_ADMIN)
    if user_id:
        return QuotaRequester(REQ_USER, str(user_id))
    dev = valid_device_hash(device_id)
    if dev:
        return QuotaRequester(REQ_DEVICE, dev[:32])
    if ip is None and request is not None:
        from app.core.client_ip import get_client_ip  # noqa: PLC0415
        ip = get_client_ip(request)
    return QuotaRequester(REQ_ANON, ip or "unknown")


def platform_limit(requester: QuotaRequester) -> int:
    if requester.kind == REQ_ADMIN:
        return -1
    if requester.kind == REQ_USER:
        base = platform_limit_for_tier(requester.tier)
    else:
        base = platform_limit_anon()
    return base if base == -1 else base + platform_bonus(requester)


# ── Admin grant / reset (task #6125, PLAN-32E P1 step 4) ──────────────────
# Day-only, Redis-only: a grant raises today's limit for one requester and
# expires at UTC midnight with the counters. No migration, no lasting change.
BONUS_DAY_MAX = 200


def _bonus_key(requester: QuotaRequester, day: Optional[str] = None) -> str:
    return f"vidgrab:quota:bonus:{requester.key}:{day or _utc_day()}"


def platform_bonus(requester: QuotaRequester) -> int:
    """Extra downloads an admin granted this requester today (0 on Redis errors)."""
    if requester.kind == REQ_ADMIN:
        return 0
    return max(0, _redis_count(_bonus_key(requester)))


def grant_platform_bonus(requester: QuotaRequester, amount: int) -> int:
    """Add `amount` to today's grant, capped at BONUS_DAY_MAX in total.
    Returns today's grant after the change. Raises on Redis errors."""
    from app.core.redis_client import get_redis  # noqa: PLC0415
    r = get_redis()
    key = _bonus_key(requester)
    total = min(BONUS_DAY_MAX, max(0, int(r.get(key) or 0)) + max(0, int(amount)))
    r.set(key, total, ex=_ttl_to_midnight())
    return total


def reset_platform_usage(requester: QuotaRequester) -> int:
    """Today's used count back to 0 (every platform bucket and the total).
    Videos already counted today stay free to fetch again. Returns the number
    of counters removed. Raises on Redis errors."""
    from app.core.redis_client import get_redis  # noqa: PLC0415
    r = get_redis()
    keys = list(r.scan_iter(match=f"vidgrab:quota:plat:{requester.key}:*:{_utc_day()}", count=500))
    return int(r.delete(*keys)) if keys else 0


def _utc_day(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def next_reset_utc(now: Optional[datetime] = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def reset_time_vn_text(now: Optional[datetime] = None) -> str:
    """'07:00' — the next reset instant in Vietnam time, independent of the
    server's TZ."""
    return next_reset_utc(now).astimezone(_VN_TZ).strftime("%H:%M")


def _plat_key(requester: QuotaRequester, platform: str, day: Optional[str] = None) -> str:
    return f"vidgrab:quota:plat:{requester.key}:{platform}:{day or _utc_day()}"


def _seen_key(requester: QuotaRequester, day: Optional[str] = None) -> str:
    return f"vidgrab:quota:plat_seen:{requester.key}:{day or _utc_day()}"


def _url_fingerprint(platform: str, url: Optional[str]) -> Optional[str]:
    u = (url or "").strip()
    if not u:
        return None
    u = u.split("#", 1)[0].rstrip("/").lower()
    import hashlib  # noqa: PLC0415
    return f"{platform}:{hashlib.sha1(u.encode('utf-8')).hexdigest()[:20]}"


def _ttl_to_midnight() -> int:
    now = datetime.now(timezone.utc)
    return max(int((next_reset_utc(now) - now).total_seconds()) + 60, 60)


def _already_counted(requester: QuotaRequester, platform: str, url: Optional[str]) -> bool:
    fp = _url_fingerprint(platform, url)
    if not fp:
        return False
    from app.core.redis_client import get_redis  # noqa: PLC0415
    try:
        return bool(get_redis().sismember(_seen_key(requester), fp))
    except Exception:
        return False


def platform_used(requester: QuotaRequester, platform: str) -> int:
    return _redis_count(_plat_key(requester, platform))


def platform_quota_message(requester: QuotaRequester, platform: str, limit: int) -> str:
    from app.core.platform_key import platform_label  # noqa: PLC0415
    label = platform_label(platform)
    for_label = f" cho {label}" if quota_scope() == QUOTA_SCOPE_PLATFORM else ""
    if requester.kind in (REQ_ANON, REQ_DEVICE):
        user_limit = platform_limit_user()
        signin = ("Đăng nhập để tải không giới hạn." if user_limit == -1
                  else f"Đăng nhập để tải {user_limit} lượt/ngày.")
        return f"Khách tải được tối đa {limit} lượt/ngày{for_label}. {signin}"
    return (f"Bạn đã dùng hết {limit} lượt hôm nay{for_label}. "
            f"Lượt mới được cộng lại lúc {reset_time_vn_text()}.")


def bulk_item_quota_message(requester: QuotaRequester, platform: str, limit: int) -> str:
    """Per-item text written on a bulk/queue job refused for the allowance."""
    from app.core.platform_key import platform_label  # noqa: PLC0415
    label = platform_label(platform)
    for_label = f" cho {label}" if quota_scope() == QUOTA_SCOPE_PLATFORM else ""
    if requester.kind in (REQ_ANON, REQ_DEVICE):
        user_limit = platform_limit_user()
        signin = ("Đăng nhập để tải không giới hạn." if user_limit == -1
                  else f"Đăng nhập để tải {user_limit} lượt/ngày.")
        return f"Đã hết {limit} lượt/ngày của khách{for_label}, mục này chưa được tải. {signin}"
    return (f"Đã hết {limit} lượt hôm nay{for_label}, mục này chưa được tải. "
            f"Lượt mới được cộng lại lúc {reset_time_vn_text()}.")


def check_platform_quota(requester: QuotaRequester, platform: str,
                         url: Optional[str] = None) -> Dict[str, Any]:
    """Read-only check. Redis errors fail open (as the old counters did).

    The result carries the old field names too (downloads_today, daily_limit,
    quota_bucket) so existing clients reading a 403/429 body keep working."""
    platform = platform or "other"
    limit = platform_limit(requester)
    used = 0 if requester.kind == REQ_ADMIN else platform_used(requester, _enforced_bucket(platform))
    remaining = -1 if limit == -1 else max(0, limit - used)
    base = {
        "quota_scope":     quota_scope(),
        "quota_bucket":    "platform",
        "requester":       requester.kind,
        "platform":        platform,
        "downloads_today": used,
        "daily_limit":     limit,
        "remaining":       remaining,
        "reset_at":        next_reset_utc().isoformat(),
        "reset_time_vn":   reset_time_vn_text(),
    }
    if limit == -1 or used < limit:
        return {"allowed": True, **base}
    if _already_counted(requester, platform, url):
        # Same video again today (another quality, a retry): already paid for.
        return {"allowed": True, "already_counted": True, **base}
    try:
        from app.core.metrics import track_quota_denial  # noqa: PLC0415
        track_quota_denial(ERR_QUOTA_DAILY, platform=f"{requester.kind}:{platform}",
                           **({"user_id": requester.ident} if requester.kind == REQ_USER else {}))
    except Exception:
        pass
    return {
        "allowed":    False,
        "error_code": ERR_QUOTA_DAILY,
        "message":    platform_quota_message(requester, platform, limit),
        **base,
    }


def record_platform_download(requester: QuotaRequester, platform: str,
                             url: Optional[str] = None) -> bool:
    """Count one successful download. Returns True when the counter moved.
    Admin: never counted. A URL already counted today for this requester:
    not counted again. Never raises."""
    if requester.kind == REQ_ADMIN:
        return False
    platform = platform or "other"
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        r = get_redis()
        fp = _url_fingerprint(platform, url)
        ttl = _ttl_to_midnight()
        if fp:
            sk = _seen_key(requester)
            added = r.sadd(sk, fp)
            r.expire(sk, ttl)
            if not added:
                return False
        _redis_incr_until_midnight(_plat_key(requester, platform))
        _redis_incr_until_midnight(_plat_key(requester, _TOTAL_BUCKET))
        return True
    except Exception as e:
        print(f"[Quota] record_platform_download failed for {requester.kind}: {type(e).__name__}")
        return False


def url_fingerprint(platform: str, url: Optional[str]) -> Optional[str]:
    """Public alias of the per-day "same video" key (desktop claims, task #6087)."""
    return _url_fingerprint(platform or "other", url)


def refund_platform_download(requester: QuotaRequester, platform: str, fp: Optional[str]) -> bool:
    """Undo one record_platform_download (a desktop download that failed,
    task #6087). Both counters go down (never below 0) and the URL is
    forgotten so a retry counts again. Never raises; False on any error."""
    if requester.kind == REQ_ADMIN:
        return False
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        r = get_redis()
        for key in (_plat_key(requester, platform or "other"), _plat_key(requester, _TOTAL_BUCKET)):
            if int(r.get(key) or 0) > 0:
                r.decr(key)
        if fp:
            r.srem(_seen_key(requester), fp)
        return True
    except Exception as e:
        print(f"[Quota] refund_platform_download failed for {requester.kind}: {type(e).__name__}")
        return False


def _device_ip_key(ip: str, day: Optional[str] = None) -> str:
    return f"vidgrab:quota:devip:{ip or 'unknown'}:{day or _utc_day()}"


def device_ip_used(ip: str) -> int:
    """Downloads counted today for app machines behind this IP (all devices)."""
    return _redis_count(_device_ip_key(ip))


def device_ip_add(ip: str, delta: int) -> None:
    """+1 when a machine's download is counted, -1 on a refund. Never raises."""
    try:
        if delta > 0:
            _redis_incr_until_midnight(_device_ip_key(ip))
        else:
            from app.core.redis_client import get_redis  # noqa: PLC0415
            r = get_redis()
            k = _device_ip_key(ip)
            if int(r.get(k) or 0) > 0:
                r.decr(k)
    except Exception:
        pass


def reset_device_ip(ip: str) -> int:
    """Admin "Đặt lại IP" (PLAN-32E §6): today's app-guest counter for this
    network back to 0, so machines behind it are no longer stopped by the IP
    cap. Each machine's own allowance is untouched. Returns the count that
    was removed (0 when there was none). Raises on Redis errors."""
    from app.core.redis_client import get_redis  # noqa: PLC0415
    r = get_redis()
    k = _device_ip_key(ip)
    try:
        before = int(r.get(k) or 0)
    except (TypeError, ValueError):
        before = 0
    r.delete(k)
    return before


class BatchAllowance:
    """Plans a bulk / queue request against the per-platform allowance: each
    item either fits in what is left today (counting the items already taken
    by this same batch) or gets a per-item refusal message — the rest of the
    batch still runs. A URL already counted today, or repeated inside the
    batch, does not take a second slot. Read-only: nothing is counted here."""

    def __init__(self, requester: QuotaRequester):
        self.requester = requester
        self.limit = platform_limit(requester)
        self._used: Dict[str, int] = {}
        self._taken: Dict[str, int] = {}
        self._seen: set = set()

    def remaining(self, platform: str) -> int:
        if self.limit == -1:
            return -1
        bucket = _enforced_bucket(platform)
        if bucket not in self._used:
            self._used[bucket] = platform_used(self.requester, bucket)
        return max(0, self.limit - self._used[bucket] - self._taken.get(bucket, 0))

    def take(self, platform: str, url: Optional[str] = None) -> Optional[str]:
        """None when the item may run (slot taken); else the refusal text."""
        platform = platform or "other"
        if self.limit == -1:
            return None
        fp = _url_fingerprint(platform, url)
        if fp and fp in self._seen:
            return None
        if fp and _already_counted(self.requester, platform, url):
            self._seen.add(fp)
            return None
        if self.remaining(platform) <= 0:
            return bulk_item_quota_message(self.requester, platform, self.limit)
        bucket = _enforced_bucket(platform)
        self._taken[bucket] = self._taken.get(bucket, 0) + 1
        if fp:
            self._seen.add(fp)
        return None


def platform_usage_snapshot(requester: QuotaRequester) -> Dict[str, Any]:
    """Per-platform usage for the usage endpoints. Platforms with nothing used
    today are listed too, so a client can show "0/20" for any of them."""
    from app.core.platform_key import PLATFORM_LABELS  # noqa: PLC0415
    limit = platform_limit(requester)
    platforms = []
    for slug, label in PLATFORM_LABELS.items():
        used = 0 if requester.kind == REQ_ADMIN else platform_used(requester, slug)
        platforms.append({
            "platform":  slug,
            "label":     label,
            "used":      used,
            "limit":     limit,
            "remaining": -1 if limit == -1 else max(0, limit - used),
        })
    total_used = 0 if requester.kind == REQ_ADMIN else platform_used(requester, _TOTAL_BUCKET)
    return {
        "scope":         quota_scope(),
        "requester":     requester.kind,
        "limit":         limit,
        "used_total":    total_used,
        "remaining_total": -1 if limit == -1 else max(0, limit - total_used),
        "unlimited":     limit == -1,
        "reset_at":      next_reset_utc().isoformat(),
        "reset_time_vn": reset_time_vn_text(),
        "platforms":     platforms,
    }


def legacy_usage_fields(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """`used` / `limit` for clients written before the per-platform rule (the
    account menu and the Chrome extension read `used ?? downloads_today` and
    `limit ?? daily_limit`): the platform closest to its limit, so "x/y" is
    always a real, enforced pair and never shows more used than allowed."""
    if snapshot.get("scope") == QUOTA_SCOPE_TOTAL:
        return {"used": snapshot.get("used_total", 0), "limit": snapshot.get("limit", 0),
                "used_platform": None, "used_platform_label": None}
    rows = snapshot.get("platforms") or []
    top = max(rows, key=lambda r: r["used"]) if rows else None
    if top is None or top["used"] == 0:
        return {"used": 0, "limit": snapshot.get("limit", 0),
                "used_platform": None, "used_platform_label": None}
    return {"used": top["used"], "limit": snapshot.get("limit", 0),
            "used_platform": top["platform"], "used_platform_label": top["label"]}


def _redis_count(key: str) -> int:
    from app.core.redis_client import get_redis
    try:
        return int(get_redis().get(key) or 0)
    except Exception:
        return 0


def _redis_incr_until_midnight(key: str) -> None:
    from app.core.redis_client import get_redis
    pipe = get_redis().pipeline()
    pipe.incr(key)
    pipe.expire(key, _ttl_to_midnight())
    pipe.execute()

# ── Public API ────────────────────────────────────────────────────────

def get_user_tier(user_id: str) -> str:
    """Return effective 'free' or 'pro' for the given user_id."""
    return _get_tier(user_id)


def get_tier_permissions(tier: str) -> Dict[str, Any]:
    return TIER_PERMISSIONS.get(tier, TIER_PERMISSIONS["free"])


def _enforced_daily_limit(tier: str, configured: int) -> int:
    """
    The daily limit actually enforced for a tier. -1 means unlimited.

    check_user_quota used to short-circuit on `tier in ("pro","team","enterprise")`
    and return allowed=True without looking at the number at all. Only
    enterprise is configured as unlimited (-1); pro and team have real limits
    (PRO_DAILY_LIMIT, TEAM_DAILY_LIMIT) that were shown in the UI as "x/200"
    and never enforced anywhere. Every one of those downloads can pull bytes
    through the paid residential proxy, so the ceiling that was supposed to
    bound that spend did nothing.

    The guard below exists because enforcing the configured numbers as-is would
    be worse than not enforcing them: this deployment sets FREE_DAILY_LIMIT to
    1000 and never sets PRO_DAILY_LIMIT, which defaults to 100 — so a paying
    Pro account would get one tenth of what a free account gets. A paid tier is
    never meant to allow less than free, so it never does. Fix the environment
    to raise the real ceiling; this only stops a misconfiguration from
    punishing the people who paid.
    """
    if configured == -1:
        return -1
    if tier == "free":
        return configured
    free_limit = TIER_PERMISSIONS["free"]["daily_limit"]
    if free_limit == -1:
        return -1
    return max(configured, free_limit)


def check_user_quota(user_id: str) -> Dict[str, Any]:
    """
    LEGACY — no download path calls this since 2026-10-06 (the per-platform
    allowance, check_platform_quota, replaced it). Kept read-only for callers
    that report the account's total downloads today against the tier setting.

    Check daily quota for an authenticated user.
    Respects grace period (canceling/past_due with future expiry → Pro).
    Returns: {allowed, plan, downloads_today, daily_limit, permissions}
    """
    profile = _get_profile(user_id)
    tier    = _effective_tier(profile)
    perms   = get_tier_permissions(tier)
    limit   = _enforced_daily_limit(tier, perms["daily_limit"])
    usage   = _get_usage(user_id)
    used    = usage.get("downloads_today", 0)

    if limit == -1:
        return {
            "allowed":         True,
            "plan":            tier,
            "downloads_today": used,
            "daily_limit":     limit,
            "permissions":     perms,
        }

    if used >= limit:
        try:
            from app.core.metrics import track_quota_denial
            track_quota_denial(ERR_QUOTA_DAILY, platform="user", user_id=user_id)
        except Exception:
            pass
        return {
            "allowed":         False,
            "error_code":      ERR_QUOTA_DAILY,
            "plan":            tier,
            "downloads_today": used,
            "daily_limit":     limit,
            "message":         (
                f"Đã đạt giới hạn {limit} lượt tải hôm nay. "
                "Nâng cấp Pro để tải không giới hạn hơn."
            ),
            "permissions":     perms,
        }

    return {
        "allowed":         True,
        "plan":            tier,
        "downloads_today": used,
        "daily_limit":     limit,
        "message":         f"{used}/{limit} lượt hôm nay",
        "permissions":     perms,
    }


def check_feature_permission(user_id: str, feature: str) -> Dict[str, Any]:
    """
    Check if user is allowed to use a specific feature.
    feature: 'cloud_save' | 'chapters' | 'logo_inpaint' | 'api_key' |
             'bulk_zip' | 'spotify_artist_full'
    """
    tier  = _get_tier(user_id)
    perms = get_tier_permissions(tier)
    val   = perms.get(feature)
    # Boolean or non-False string counts as allowed
    allowed = bool(val) if not isinstance(val, str) else True

    if allowed:
        return {"allowed": True, "tier": tier}

    feature_labels = {
        "cloud_save":           "Lưu Cloud",
        "chapters":             "Chapter Extractor",
        "logo_inpaint":         "Xoá Logo / Watermark",
        "api_key":              "API Key",
        "bulk_zip":             "ZIP Download",
        "spotify_artist_full":  "Spotify Artist (full catalog)",
    }
    label = feature_labels.get(feature, feature)

    error_map = {
        "bulk_zip":            ERR_BULK_ZIP,
        "spotify_artist_full": ERR_SPOTIFY_FULL,
    }
    error_code = error_map.get(feature, ERR_FEATURE)

    return {
        "allowed":    False,
        "error_code": error_code,
        "feature":    feature,
        "tier":       tier,
        "message":    f"Tính năng '{label}' chỉ dành cho Pro. Nâng cấp để sử dụng.",
        "required_tier": "pro",
        "upgrade_url":   "/upgrade",
    }


def check_quality_permission(user_id: str, quality_str: str, height: int = 0) -> Dict[str, Any]:
    """
    Return whether the requested quality is allowed for this user's tier.
    Free users are capped at 1080p.
    """
    tier  = _get_tier(user_id)
    perms = get_tier_permissions(tier)
    max_h = perms["max_height"]

    is_4k_str = quality_str.lower() in PRO_ONLY_QUALITY_STRINGS
    is_4k_h   = height > 1080

    if (is_4k_str or is_4k_h) and max_h < 2160:
        return {
            "allowed":    False,
            "error_code": ERR_QUALITY,
            "tier":       tier,
            "message":    "Chất lượng 4K chỉ dành cho Pro. Nâng cấp hoặc chọn ≤ 1080p.",
        }

    return {"allowed": True, "tier": tier}


def check_youtube_tier(_user_id: Optional[str], _quality_str: str) -> Dict[str, Any]:
    """
    YouTube-specific tier check.
    All registered users (free + pro) may download video, capped at 1080p for free.
    Anonymous users are audio-only (controlled by YT_ANON_AUDIO_ONLY env flag).
    Per-user daily YouTube count is handled separately by yt_quota.reserve().
    Returns: {allowed: True} always for registered users.
    """
    # Anonymous users: audio-only restriction is handled by YT_ANON_AUDIO_ONLY
    # valve in routes.py — not here.
    return {"allowed": True}


def check_batch_limit(user_id: Optional[str], count: int) -> Dict[str, Any]:
    """
    Check whether the batch size is within the user's tier limit.
    user_id: None means guest (free tier).
    """
    tier  = _get_tier(user_id) if user_id else "free"
    perms = get_tier_permissions(tier)
    limit = perms["batch_limit"]

    if count > limit:
        return {
            "allowed":    False,
            "error_code": ERR_BATCH,
            "tier":       tier,
            "limit":      limit,
            "requested":  count,
            "message":    (
                f"Batch tối đa {limit} video cho gói {tier.title()}. "
                f"Bạn đang yêu cầu {count}."
            ),
        }

    return {"allowed": True, "tier": tier, "limit": limit}


def increment_usage(user_id: str, count_daily: bool = True) -> None:
    """
    Increment daily + monthly download counters for an authenticated user.

    These are account statistics (all platforms together). What limits a
    download is the per-platform Redis counter (record_platform_download).
    count_daily=False bumps only the monthly total.
    """
    supabase = get_supabase_client()
    try:
        res = (
            supabase.table("user_usage")
            .select("downloads_today, downloads_this_month")
            .eq("user_id", user_id)
            .execute()
        )
        if res.data:
            row = res.data[0]
            update = {
                "downloads_this_month": (row.get("downloads_this_month", 0) or 0) + 1,
            }
            if count_daily:
                update["downloads_today"] = (row.get("downloads_today", 0) or 0) + 1
            supabase.table("user_usage").update(update).eq("user_id", user_id).execute()
        else:
            supabase.table("user_usage").insert({
                "user_id":              user_id,
                "downloads_today":      1 if count_daily else 0,
                "downloads_this_month": 1,
                "last_reset_at":        _today_utc_str(),
                "plan":                 "free",
            }).execute()
    except Exception as e:
        print(f"[Quota] increment_usage failed for {user_id}: {e}")


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hash for storing API keys in DB (never store plaintext)."""
    import hashlib
    return hashlib.sha256(raw_key.encode()).hexdigest()
