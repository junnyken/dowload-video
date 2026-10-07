"""
Daily allowance for downloads the Windows app makes ITSELF (task #6087, PLAN-32D §5)
====================================================================================
Owner 2026-10-07: downloads done locally by the app (its own yt-dlp, later the
user's own cookies) count toward the same daily allowance as the web — guest 5,
signed-in 20, total across platforms — so people sign up and upgrade.

  POST /client/quota/claim        {url, route, clientVersion?, retro?}
  POST /client/quota/claim-batch  {items: [{url}], route}          (≤ 100, channels)
  POST /client/quota/settle       {claimId, outcome}               (refund on failure)
  GET  /client/quota                                                (badge "Hôm nay x/y")

Who is counted: admin session > signed-in user (Bearer) > the app's machine
(X-VG-Device: 64-hex hash, key "dev:<32 hex>") > guest IP. A machine is a soft
signal (spoofable), so app guests are also capped per IP at
PLATFORM_DAILY_LIMIT_ANON × CLIENT_QUOTA_IP_MULT.

Reserve-first: a claim counts the download at once (same counters and same
"one URL once a day" rule as the web, app.core.quotas). Skipping the settle
call only costs the caller. A failed download may be refunded by settle:
at most CLIENT_QUOTA_REFUND_DAILY_MAX a day, only within 2 h of the claim.

Flags (read at call time):
  CLIENT_QUOTA_ENABLED     off (default) → every route answers 503
                           client_quota_disabled and the app behaves as before
  CLIENT_QUOTA_MODE        shadow (default): count and log, never refuse
                           enforce: refuse requesters listed in
  CLIENT_QUOTA_ENFORCE_FOR comma list of user,device,anon (default: all three)
  CLIENT_QUOTA_OFFLINE_GRACE   downloads the app may make offline, then
                               report with retro=true (default 3)
  CLIENT_QUOTA_REFUND_DAILY_MAX (default 10, signed-in users)
  CLIENT_QUOTA_REFUND_DAILY_MAX_GUEST (default 2, machines and guests —
                               owner 2026-10-07, PLAN-32E G5: a guest could
                               otherwise "fail" its way to 5 + 10 a day)
  CLIENT_QUOTA_IP_MULT (default 3)

Stats (PLAN-32E P0, admin "App Windows"): the daily hash
vidgrab:stats:route:<day> holds "<route>|<outcome>" and, per requester kind,
"kind:<user|device|anon|admin>|<outcome>" (outcomes as above plus
"refunded"); vidgrab:stats:over_top:<day> is a sorted set of dev:/user: keys
scored by over-limit-in-shadow claims (top machines today).
"""

import logging
import os
import secrets
import time
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core import desktop_signals, quotas
from app.core.auth_middleware import get_optional_user
from app.main import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

CLAIM_TTL_SEC = 48 * 3600
REFUND_WINDOW_SEC = 2 * 3600
MAX_BATCH = 100
_ROUTES = ("local", "local_cookie", "server")
_OUTCOMES = ("completed", "failed", "cancelled")


# ── flags ────────────────────────────────────────────────────────────────

def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def quota_enabled() -> bool:
    return _env("CLIENT_QUOTA_ENABLED").lower() in ("1", "true", "yes", "on")


def quota_mode() -> str:
    return "enforce" if _env("CLIENT_QUOTA_MODE", "shadow").lower() == "enforce" else "shadow"


def enforce_for() -> set:
    raw = _env("CLIENT_QUOTA_ENFORCE_FOR", "user,device,anon").lower()
    return {p.strip() for p in raw.split(",") if p.strip()}


def offline_grace() -> int:
    return max(0, _int_env("CLIENT_QUOTA_OFFLINE_GRACE", 3))


def refund_daily_max() -> int:
    """Refunds a day for a signed-in user."""
    return max(0, _int_env("CLIENT_QUOTA_REFUND_DAILY_MAX", 10))


def refund_daily_max_guest() -> int:
    """Refunds a day for a machine (dev:) or guest IP (anon)."""
    return max(0, _int_env("CLIENT_QUOTA_REFUND_DAILY_MAX_GUEST", 2))


def refund_daily_max_for(req: "quotas.QuotaRequester") -> int:
    return refund_daily_max() if req.kind in (quotas.REQ_USER, quotas.REQ_ADMIN) else refund_daily_max_guest()


def ip_mult() -> int:
    return max(1, _int_env("CLIENT_QUOTA_IP_MULT", 3))


def enforce_users() -> set:
    """Canary (task #6125, owner decision 1): when non-empty, only these
    signed-in user ids are enforced among accounts; other accounts behave as
    shadow. Empty (default) = every account when "user" is in ENFORCE_FOR."""
    raw = _env("CLIENT_QUOTA_ENFORCE_USERS", "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def _enforced(req: "quotas.QuotaRequester") -> bool:
    if quota_mode() != "enforce" or req.kind not in enforce_for():
        return False
    if req.kind == quotas.REQ_USER:
        canary = enforce_users()
        if canary and req.ident not in canary:
            return False
    return True


def _disabled() -> JSONResponse:
    return JSONResponse(status_code=503, content={
        "detail": "Tính năng đếm lượt tải của app Windows chưa được bật.",
        "error_code": "client_quota_disabled"})


# ── models ───────────────────────────────────────────────────────────────

class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(min_length=8, max_length=2048)
    route: str = Field(default="local", max_length=20)
    clientVersion: str = Field(default="", max_length=32)
    retro: bool = False


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    url: str = Field(min_length=8, max_length=2048)


class ClaimBatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: List[BatchItem] = Field(min_length=1, max_length=MAX_BATCH)
    route: str = Field(default="local", max_length=20)


class SettleIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    claimId: str = Field(min_length=8, max_length=64)
    outcome: str
    errorCode: Optional[str] = Field(default=None, max_length=64)


# ── helpers ──────────────────────────────────────────────────────────────

def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _ip(request: Request) -> str:
    from app.core.client_ip import get_client_ip  # noqa: PLC0415
    return get_client_ip(request) or "unknown"


def _device_header(request: Request) -> Optional[str]:
    return quotas.valid_device_hash(request.headers.get("X-VG-Device"))


def _requester(request: Request, user: Optional[dict]) -> "quotas.QuotaRequester":
    return quotas.resolve_requester(request, user_id=(user or {}).get("id"),
                                    device_id=_device_header(request))


def _http_url(url: str) -> Optional[str]:
    u = (url or "").strip()
    return u if u.lower().startswith(("http://", "https://")) and " " not in u else None


def _claim_key(claim_id: str) -> str:
    return f"vidgrab:claim:{claim_id}"


def _refund_key(req: "quotas.QuotaRequester") -> str:
    return f"vidgrab:quota:refund:{req.key}:{quotas._utc_day()}"


def _retro_key(req: "quotas.QuotaRequester") -> str:
    return f"vidgrab:quota:retro:{req.key}:{quotas._utc_day()}"


def _take_retro(req: "quotas.QuotaRequester") -> bool:
    """A retro claim (download already made offline) skips the check, but only
    CLIENT_QUOTA_OFFLINE_GRACE times a day — otherwise "retro" would be a way
    around the allowance. Past that it is an ordinary claim."""
    grace = offline_grace()
    if grace <= 0:
        return False
    try:
        if quotas._redis_count(_retro_key(req)) >= grace:
            return False
        quotas._redis_incr_until_midnight(_retro_key(req))
        return True
    except Exception:
        return False


STATS_TTL_SEC = 40 * 86400
OVER_TOP_TTL_SEC = 3 * 86400


def record_route_stat(route: str, outcome: str,
                      req: Optional["quotas.QuotaRequester"] = None) -> None:
    """Public: one count in the daily route stats (admin "App Windows").
    Used by /fetch-link and /client/douyin/video for the app's server route."""
    _stat(route, outcome, req)


def _stat(route: str, outcome: str, req: Optional["quotas.QuotaRequester"] = None) -> None:
    try:
        r = _r()
        day = quotas._utc_day()
        k = f"vidgrab:stats:route:{day}"
        r.hincrby(k, f"{route}|{outcome}", 1)
        if req is not None:
            r.hincrby(k, f"kind:{req.kind}|{outcome}", 1)
            if outcome == "over_shadow" and req.kind in (quotas.REQ_DEVICE, quotas.REQ_USER):
                tk = f"vidgrab:stats:over_top:{day}"
                r.zincrby(tk, 1, req.key)
                r.expire(tk, OVER_TOP_TTL_SEC)
        r.expire(k, STATS_TTL_SEC)
    except Exception:
        pass


def _kind_stat(req: "quotas.QuotaRequester", outcome: str) -> None:
    """Per-kind only (no route), e.g. "refunded"."""
    try:
        r = _r()
        k = f"vidgrab:stats:route:{quotas._utc_day()}"
        r.hincrby(k, f"kind:{req.kind}|{outcome}", 1)
        r.expire(k, STATS_TTL_SEC)
    except Exception:
        pass


def ip_cap_exceeded(req: "quotas.QuotaRequester", ip: str, platform: str, url: Optional[str]) -> bool:
    """Public (PLAN-32E G2): an app machine (dev:) is a soft, spoofable id, so
    every machine behind one IP shares PLATFORM_DAILY_LIMIT_ANON × IP_MULT a
    day — a made-up device id must not mint a fresh allowance. A URL this
    machine already has counted today never takes a new slot, so it is not
    refused either. Other requester kinds: never capped here."""
    if req.kind != quotas.REQ_DEVICE:
        return False
    lim = quotas.platform_limit_anon()
    if lim == -1:
        return False
    if quotas._already_counted(req, platform or "other", url):
        return False
    # an admin grant for this machine (task #6125) also widens its IP's share
    return quotas.device_ip_used(ip) >= lim * ip_mult() + quotas.platform_bonus(req)


def note_device_counted(req: "quotas.QuotaRequester", ip: str, counted: bool) -> None:
    """Public: after record_platform_download — a machine's counted download
    also takes one slot of its IP's cap."""
    if counted and req.kind == quotas.REQ_DEVICE:
        quotas.device_ip_add(ip, +1)


def ip_cap_message() -> str:
    return _ip_cap_message()


def _usage(req: "quotas.QuotaRequester") -> dict:
    limit = quotas.platform_limit(req)
    used = 0 if req.kind == quotas.REQ_ADMIN else quotas.platform_used(req, quotas._TOTAL_BUCKET)
    return {"limit": limit, "usedToday": used,
            "remaining": -1 if limit == -1 else max(0, limit - used),
            "resetTimeVn": quotas.reset_time_vn_text(), "requester": req.kind}


def _refusal(req: "quotas.QuotaRequester", message: str, reason: str = "daily_limit") -> dict:
    return {"allowed": False, "error_code": "quota_exceeded_daily", "reason": reason,
            "detail": message, "upsell": "upgrade" if req.kind == quotas.REQ_USER else "signin",
            **_usage(req), "remaining": 0}


def _ip_cap_message() -> str:
    return ("Mạng này đã dùng hết lượt tải của khách hôm nay. "
            f"Đăng nhập để có {quotas.platform_limit_user()} lượt/ngày.")


def _claim_one(req: "quotas.QuotaRequester", ip: str, url: str, route: str, retro: bool,
               device: Optional[str] = None) -> dict:
    """Check, then count at once (reserve-first). Shadow mode never refuses
    but reports overLimit. Never raises on Redis errors (counters fail open,
    as on the web)."""
    from app.core.platform_key import platform_key  # noqa: PLC0415
    platform = platform_key(url)
    over, message, reason = False, "", "daily_limit"
    if not retro:
        q = quotas.check_platform_quota(req, platform, url)
        if not q.get("allowed"):
            over, message = True, q.get("message") or ""
        elif not q.get("already_counted") and ip_cap_exceeded(req, ip, platform, url):
            over, message, reason = True, _ip_cap_message(), "ip_limit"
            desktop_signals.note_ip_limit(ip)  # admin signals (task #6126), shadow mode too
        if over and _enforced(req):
            _stat(route, "refused", req)
            return _refusal(req, message, reason)

    counted = quotas.record_platform_download(req, platform, url)
    note_device_counted(req, ip, counted)
    claim_id = secrets.token_hex(12)
    try:
        r = _r()
        r.hset(_claim_key(claim_id), mapping={
            "req": req.key, "platform": platform, "fp": quotas.url_fingerprint(platform, url) or "",
            "counted": "1" if counted else "0", "ip": ip, "ts": str(int(time.time())),
            "settled": "0", "route": route,
        })
        r.expire(_claim_key(claim_id), CLAIM_TTL_SEC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("client_quota claim store failed: %s", type(exc).__name__)
    _stat(route, "retro" if retro else ("over_shadow" if over else "ok"), req)
    desktop_signals.note_app_download(req, ip, device)
    if retro:
        desktop_signals.note_retro(req)
    return {"allowed": True, "claimId": claim_id, "platform": platform,
            "alreadyCounted": (not counted) and req.kind != quotas.REQ_ADMIN,
            "overLimit": over, "mode": quota_mode(), **_usage(req)}


def _status_for(req: "quotas.QuotaRequester") -> int:
    return 403 if req.kind == quotas.REQ_USER else 429


def _record_device(request: Request, req: "quotas.QuotaRequester", user: Optional[dict]) -> Optional[str]:
    """Best effort: desktop_devices row (migration 037). Returns the 8-char
    display code of the machine, or None without a device header."""
    dev = _device_header(request)
    if not dev:
        return None
    try:
        from datetime import datetime, timezone  # noqa: PLC0415
        from app.core.database import get_service_client  # noqa: PLC0415
        row = {
            "device_hash": dev,
            "display_name": (request.headers.get("X-VG-Device-Name") or "")[:120] or None,
            "client_version": (request.headers.get("X-VG-Client") or "")[:32] or None,
            "user_id": str(user["id"]) if user and user.get("id") else None,
            "last_ip": _ip(request),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        get_service_client().table("desktop_devices").upsert(row, on_conflict="device_hash").execute()
    except Exception as exc:  # noqa: BLE001  table missing / storage down: never blocks a download
        logger.info("client_quota device upsert skipped: %s", type(exc).__name__)
    return dev[:8].upper()


# ── routes ───────────────────────────────────────────────────────────────

@router.post("/client/quota/claim")
@limiter.limit("60/minute")
async def claim(payload: ClaimIn, request: Request, user=Depends(get_optional_user)):
    if not quota_enabled():
        return _disabled()
    url = _http_url(payload.url)
    if not url:
        return JSONResponse(status_code=400, content={"detail": "Link không hợp lệ.", "error_code": "invalid_url"})
    route = payload.route if payload.route in _ROUTES else "local"
    req = _requester(request, user)
    retro = bool(payload.retro) and _take_retro(req)
    out = _claim_one(req, _ip(request), url, route, retro, _device_header(request))
    if not out["allowed"]:
        return JSONResponse(status_code=_status_for(req), content=out)
    return out


@router.post("/client/quota/claim-batch")
@limiter.limit("20/minute")
async def claim_batch(payload: ClaimBatchIn, request: Request, user=Depends(get_optional_user)):
    if not quota_enabled():
        return _disabled()
    route = payload.route if payload.route in _ROUTES else "local"
    req, ip, dev = _requester(request, user), _ip(request), _device_header(request)
    results = []
    for it in payload.items:
        url = _http_url(it.url)
        if not url:
            results.append({"url": it.url, "allowed": False, "error_code": "invalid_url",
                            "detail": "Link không hợp lệ."})
            continue
        results.append({"url": url, **_claim_one(req, ip, url, route, False, dev)})
    return {"items": results, "mode": quota_mode(), **_usage(req)}


@router.post("/client/quota/settle")
@limiter.limit("120/minute")
async def settle(payload: SettleIn, request: Request, user=Depends(get_optional_user)):
    if not quota_enabled():
        return _disabled()
    if payload.outcome not in _OUTCOMES:
        return JSONResponse(status_code=400, content={"detail": "outcome không hợp lệ.",
                                                      "error_code": "validation_failed"})
    req = _requester(request, user)
    refunded = False
    try:
        r = _r()
        key = _claim_key(payload.claimId)
        c = r.hgetall(key) or {}
        c = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
             for k, v in c.items()}
        # Only the requester who claimed may settle it; an unknown or expired
        # claim is a no-op (the app keeps going).
        if c and c.get("req") == req.key and c.get("settled") == "0":
            r.hset(key, "settled", "1")
            age = time.time() - int(c.get("ts") or 0)
            if (payload.outcome != "completed" and c.get("counted") == "1" and age < REFUND_WINDOW_SEC
                    and quotas._redis_count(_refund_key(req)) < refund_daily_max_for(req)):
                refunded = quotas.refund_platform_download(req, c.get("platform") or "other", c.get("fp") or None)
                if refunded:
                    quotas._redis_incr_until_midnight(_refund_key(req))
                    _kind_stat(req, "refunded")
                    desktop_signals.note_refund(req)
                    if req.kind == quotas.REQ_DEVICE:
                        quotas.device_ip_add(c.get("ip") or "unknown", -1)
            _stat(c.get("route") or "local", f"settle_{payload.outcome}", req)
    except Exception as exc:  # noqa: BLE001
        logger.warning("client_quota settle failed: %s", type(exc).__name__)
    return {"refunded": refunded, **_usage(req)}


@router.get("/client/quota")
@limiter.limit("60/minute")
async def snapshot(request: Request, user=Depends(get_optional_user)):
    if not quota_enabled():
        return _disabled()
    req = _requester(request, user)
    import asyncio  # noqa: PLC0415
    code = await asyncio.to_thread(_record_device, request, req, user)
    return {**_usage(req), "deviceCode": code, "mode": quota_mode(),
            "enforced": _enforced(req), "offlineGrace": offline_grace(),
            "refundDailyMax": refund_daily_max_for(req)}
