"""
Partner API job quotas — per key per day, per tenant per month.

Counted when a job is submitted (POST /api/v1/partner/jobs); polling, listing
and webhooks only count against the per-minute limit (partner_auth).

  partner:day:{key_id}:{YYYY-MM-DD}      vs tenant_api_keys.rate_limit_per_day
  partner:month:{tenant_id}:{YYYY-MM}    vs the tenant's monthly cap
                                          (tenants.plan_api_calls_per_month,
                                           -1 = unlimited)

Days and months are UTC. Redis INCR is atomic, which the previous check was
not: it read tenant_usage_daily and incremented it afterwards, so parallel
submissions all passed, and it never looked at the monthly cap at all.
A refused job is not counted. Fails OPEN on Redis errors, like api_quota.
tenant_usage_daily is still written, for the 30-day history and the admin.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

DAY_KEY = "partner:day:{key_id}:{day}"
MONTH_KEY = "partner:month:{tenant_id}:{month}"
_DAY_TTL = 2 * 86400
_MONTH_TTL = 40 * 86400


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def _day(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _month(now: datetime) -> str:
    return now.strftime("%Y-%m")


def day_reset_at(now: Optional[datetime] = None) -> str:
    n = _now(now)
    return (datetime(n.year, n.month, n.day, tzinfo=timezone.utc) + timedelta(days=1)).isoformat()


def month_reset_at(now: Optional[datetime] = None) -> str:
    n = _now(now)
    return datetime(n.year + (n.month == 12), 1 if n.month == 12 else n.month + 1, 1,
                    tzinfo=timezone.utc).isoformat()


def consume(tenant_id: str, key_id: Optional[str], per_day: int, monthly_cap: int,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """Count one job. Returns {allowed, reason, limit, used, reset_at}.
    reason is "daily" or "monthly" when refused. Never raises."""
    n = _now(now)
    try:
        rc = _r()
        mkey = MONTH_KEY.format(tenant_id=tenant_id, month=_month(n))
        month_used = None
        if monthly_cap is not None and int(monthly_cap) >= 0:
            month_used = int(rc.incr(mkey))
            if month_used == 1:
                rc.expire(mkey, _MONTH_TTL)
            if month_used > int(monthly_cap):
                rc.decr(mkey)
                return {"allowed": False, "reason": "monthly", "limit": int(monthly_cap),
                        "used": int(monthly_cap), "reset_at": month_reset_at(n)}
        if key_id and per_day and int(per_day) > 0:
            dkey = DAY_KEY.format(key_id=key_id, day=_day(n))
            day_used = int(rc.incr(dkey))
            if day_used == 1:
                rc.expire(dkey, _DAY_TTL)
            if day_used > int(per_day):
                rc.decr(dkey)
                if month_used is not None:
                    rc.decr(mkey)
                return {"allowed": False, "reason": "daily", "limit": int(per_day),
                        "used": int(per_day), "reset_at": day_reset_at(n)}
        return {"allowed": True, "reason": None, "limit": monthly_cap,
                "used": month_used, "reset_at": month_reset_at(n)}
    except Exception:  # noqa: BLE001 — fail open
        return {"allowed": True, "reason": None, "limit": monthly_cap,
                "used": None, "reset_at": month_reset_at(n)}


def refund(tenant_id: str, key_id: Optional[str], now: Optional[datetime] = None) -> None:
    """Give back one job counted by consume() whose job was never created
    (DB insert failed). Never raises."""
    n = _now(now)
    try:
        rc = _r()
        rc.decr(MONTH_KEY.format(tenant_id=tenant_id, month=_month(n)))
        if key_id:
            rc.decr(DAY_KEY.format(key_id=key_id, day=_day(n)))
    except Exception:  # noqa: BLE001
        pass


def usage(tenant_id: str, key_id: Optional[str] = None,
          now: Optional[datetime] = None) -> Dict[str, Optional[int]]:
    """Jobs counted this UTC month (tenant) and today (key). None = unknown."""
    n = _now(now)
    out: Dict[str, Optional[int]] = {"month": None, "today_key": None}
    try:
        rc = _r()
        out["month"] = int(rc.get(MONTH_KEY.format(tenant_id=tenant_id, month=_month(n))) or 0)
        if key_id:
            out["today_key"] = int(rc.get(DAY_KEY.format(key_id=key_id, day=_day(n))) or 0)
    except Exception:  # noqa: BLE001
        pass
    return out
