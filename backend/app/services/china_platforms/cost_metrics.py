"""
Cost measurement for the China access layer (task #6036, admin page
"Chi phí Apify"). Daily rollups in Redis, 40-day TTL, no DB table.

  china:metrics:{day}:{platform}   HASH (UTC day)
      managed_calls        paid runs dispatched (a budget reservation was made)
      managed_success      of those, a validated result
      usable_known         results whose media URL was checked (router/benchmark validation)
      usable_ok            of those, usable
      spend_usd_micros     what budget_guard recorded (floor at the estimate)
      cache_hits           served from the result cache (or the in-flight dedupe)
      cache_hits_managed   cache hits whose result came from a paid provider
      saved_usd_micros     estimated paid calls avoided by those hits

"Saved" is an estimate: a repeat request for a URL whose cached result came
from a paid provider would, without the cache, most likely have needed the
paid provider again (or been refused by the once-per-URL paid marker). Hits
on native results save nothing and are counted only in cache_hits.

Per token entry the rollups are the pool's own counters
(apify_pool.k_spend_day / k_calls_day).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from app.services.china_platforms import budget_guard, settings

logger = logging.getLogger("app.china_access")

TTL = 40 * 86400
PLATFORMS = ("douyin", "kuaishou", "xiaohongshu")
FIELDS = ("managed_calls", "managed_success", "usable_known", "usable_ok", "spend_usd_micros",
          "cache_hits", "cache_hits_managed", "saved_usd_micros")

_EST_BY_PROVIDER = {
    "apify_douyin": settings.apify_douyin_est_cost_usd,
    "apify_kuaishou": settings.apify_kuaishou_est_cost_usd,
    "apify_xiaohongshu": settings.apify_xiaohongshu_est_cost_usd,
}


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _i(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def key(day: str, platform: str) -> str:
    return f"china:metrics:{day}:{platform}"


def _bump(platform: str, incs: dict) -> None:
    try:
        r = _r()
        k = key(budget_guard.day_key(), platform)
        for f, v in incs.items():
            if v:
                r.hincrby(k, f, int(v))
        r.expire(k, TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access metrics write failed: %s", type(exc).__name__)


def est_micros_for(provider_name: str) -> int:
    fn = _EST_BY_PROVIDER.get(provider_name or "")
    return settings.usd_to_micros(fn()) if fn else 0


def record_paid(platform: str, *, success: bool, usable: Optional[bool], recorded_micros: int) -> None:
    _bump(platform, {
        "managed_calls": 1,
        "managed_success": 1 if success else 0,
        "usable_known": 1 if usable is not None else 0,
        "usable_ok": 1 if usable else 0,
        "spend_usd_micros": max(0, int(recorded_micros or 0)),
    })


def record_cache_hit(platform: str, provider_name: str, provider_mode: str) -> None:
    managed = provider_mode == "managed"
    _bump(platform, {
        "cache_hits": 1,
        "cache_hits_managed": 1 if managed else 0,
        "saved_usd_micros": est_micros_for(provider_name) if managed else 0,
    })


def _days(period: str, now) -> list[str]:
    if period == "today":
        return [budget_guard.day_key(now)]
    if period == "7d":
        return [budget_guard.day_key(now - timedelta(days=i)) for i in range(7)]
    # this UTC month, up to today
    return [budget_guard.day_key(now - timedelta(days=i)) for i in range(now.day)]


def _sum(r, platform: str, days: list[str]) -> dict:
    tot = {f: 0 for f in FIELDS}
    for d in days:
        h = r.hgetall(key(d, platform)) or {}
        for f in FIELDS:
            tot[f] += _i(h.get(f))
    return tot


def _view(t: dict) -> dict:
    calls, ok = t["managed_calls"], t["managed_success"]
    return {
        "managed_calls": calls,
        "managed_success": ok,
        "success_rate": round(ok / calls, 4) if calls else None,
        "usable_known": t["usable_known"],
        "usable_rate": round(t["usable_ok"] / t["usable_known"], 4) if t["usable_known"] else None,
        "cache_hits": t["cache_hits"],
        "cache_hits_managed": t["cache_hits_managed"],
        "saved_usd": t["saved_usd_micros"] / 1e6,
        "spend_usd": t["spend_usd_micros"] / 1e6,
        "cost_per_success_usd": round(t["spend_usd_micros"] / ok / 1e6, 6) if ok else None,
    }


def platform_report(now=None) -> dict:
    now = now or budget_guard.utcnow()
    r = _r()
    out: dict = {}
    for p in PLATFORMS:
        out[p] = {period: _view(_sum(r, p, _days(period, now))) for period in ("today", "7d", "month")}
    totals = {}
    for period in ("today", "7d", "month"):
        t = {f: 0 for f in FIELDS}
        for p in PLATFORMS:
            s = _sum(r, p, _days(period, now))
            for f in FIELDS:
                t[f] += s[f]
        totals[period] = _view(t)
    return {"platforms": out, "totals": totals}
