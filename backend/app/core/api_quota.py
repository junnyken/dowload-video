"""
Monthly API call quota per account (owner 14/10).

Every request authenticated with an API key (X-API-Key / Bearer vidgrab_ /
Bearer vg_) counts one call against the key owner's account for the current
UTC calendar month; web/app/extension traffic (Supabase session) never does.
Over the limit → 429 api_monthly_quota_exceeded until the 1st of next month
(00:00 UTC = 07:00 giờ VN). Downloads made through the API still also count
against the account's daily download quota.

Limits per tier are admin settings (owner rule: admin, not env), stored with
the plan settings (app.core.plans, Redis hash plans:settings):
  api_calls_month_pro   default 3000   (the number entitlements.py always declared)
  api_calls_month_team  default 10000
  enterprise            unlimited (-1)
  free / anything else  0 (Free cannot hold a key)

Counter: Redis  api_calls:{user_id}:{YYYY-MM}  (INCR, expires after ~40 days).
Fails OPEN: if Redis cannot be read the call goes through — a paying
developer is not cut off by our bookkeeping.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

KEY = "api_calls:{uid}:{month}"
_TTL = 40 * 86400
HEADER_LIMIT = "X-API-Quota-Limit"
HEADER_REMAINING = "X-API-Quota-Remaining"
HEADER_RESET = "X-API-Quota-Reset"


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def month_of(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def reset_at(now: Optional[datetime] = None) -> datetime:
    n = now or datetime.now(timezone.utc)
    return datetime(n.year + (n.month == 12), 1 if n.month == 12 else n.month + 1, 1, tzinfo=timezone.utc)


def limit_for_tier(tier: str) -> int:
    """-1 = unlimited, 0 = no API access."""
    from app.core import plans  # noqa: PLC0415
    s = plans.get_settings()
    t = (tier or "free").lower()
    if t == "enterprise":
        return -1
    if t == "team":
        return int(s.get("api_calls_month_team") or 0)
    if t == "pro":
        return int(s.get("api_calls_month_pro") or 0)
    return 0


def usage(user_id: str, tier: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    lim = limit_for_tier(tier)
    used: Optional[int]
    try:
        raw = _r().get(KEY.format(uid=user_id, month=month_of(now)))
        used = int(raw) if raw else 0
    except Exception:  # noqa: BLE001
        used = None
    return {
        "month": month_of(now),
        "used": used,
        "limit": lim,
        "remaining": (None if used is None or lim == -1 else max(0, lim - used)),
        "reset_at": reset_at(now).isoformat(),
    }


def consume(user_id: str, tier: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Count one call. Returns {allowed, limit, used, remaining, reset_at}.
    Never raises."""
    lim = limit_for_tier(tier)
    out: Dict[str, Any] = {"limit": lim, "reset_at": reset_at(now).isoformat()}
    if lim == -1:
        out.update(allowed=True, used=None, remaining=None)
        return out
    key = KEY.format(uid=user_id, month=month_of(now))
    try:
        rc = _r()
        n = int(rc.incr(key))
        if n == 1:
            rc.expire(key, _TTL)
        if n > lim:
            rc.decr(key)          # a refused call is not a used call
            out.update(allowed=False, used=lim, remaining=0)
            return out
        out.update(allowed=True, used=n, remaining=max(0, lim - n))
        return out
    except Exception:  # noqa: BLE001 — fail open
        out.update(allowed=True, used=None, remaining=None)
        return out


def headers(q: Dict[str, Any]) -> Dict[str, str]:
    h = {HEADER_RESET: str(int(datetime.fromisoformat(q["reset_at"]).timestamp()))}
    h[HEADER_LIMIT] = "unlimited" if q.get("limit") == -1 else str(q.get("limit"))
    if q.get("remaining") is not None:
        h[HEADER_REMAINING] = str(q["remaining"])
    return h


class ApiQuotaHeadersMiddleware:
    """Adds the X-API-Quota-* headers to responses of API-key requests
    (the auth dependency leaves the numbers in request.state.api_quota)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        async def _send(message):
            if message.get("type") == "http.response.start":
                q = (scope.get("state") or {}).get("api_quota")
                if q:
                    hdrs = list(message.get("headers") or [])
                    for k, v in headers(q).items():
                        hdrs.append((k.lower().encode(), v.encode()))
                    message = {**message, "headers": hdrs}
            await send(message)

        return await self.app(scope, receive, _send)

