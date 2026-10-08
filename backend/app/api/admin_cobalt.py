"""
Admin — our Cobalt instances (task #6133). Read-only, verify_admin.
=================================================================
  GET /admin/cobalt/status?days=7   (1..31)
      instances: host, answering, latency, version, cooling down (never the key)
      platforms: per UTC day and summed — Cobalt ok / fail per platform
                 (cobalt:stats:<day>, written by record_cobalt_outcome) and
                 which platforms are currently skipped by the circuit
                 (cobalt:trip:<platform>)
      steps:     which step served a FB / IG / X download per day
                 (cookie_last:stats:<day>: cobalt_first_ok / cobalt_first_miss /
                 anon_ok / cobalt_ok / cookie_ok / all_fail / gone)
      flags:     COBALT_FIRST_PLATFORMS, COOKIE_LAST_PLATFORMS, trip settings
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from app.api.admin import verify_admin

logger = logging.getLogger(__name__)
router = APIRouter()


def _s(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _read(rc, key: str) -> dict:
    out: dict = {}
    for k, v in (rc.hgetall(key) or {}).items():
        name, _, what = _s(k).partition("|")
        try:
            out.setdefault(name, {})[what] = int(_s(v))
        except ValueError:
            pass
    return out


def _status(days: int) -> dict:
    from app.services import cobalt_service as cs
    days = max(1, min(int(days), 31))
    today = datetime.now(timezone.utc)
    day_keys = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    out = {"days": days, "day_keys": day_keys, "instances": cs.instances_status(),
           "platforms": {}, "platforms_total": {}, "steps": {}, "steps_total": {},
           "tripped": [], "redis_ok": True,
           "flags": {"cobalt_first": os.getenv("COBALT_FIRST_PLATFORMS", ""),
                     "cookie_last": os.getenv("COOKIE_LAST_PLATFORMS", ""),
                     "trip_after": cs._TRIP_AFTER, "trip_seconds": cs._TRIP_S}}
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        for d in day_keys:
            out["platforms"][d] = _read(rc, f"cobalt:stats:{d}")
            out["steps"][d] = _read(rc, f"cookie_last:stats:{d}")
        for src, dst in (("platforms", "platforms_total"), ("steps", "steps_total")):
            for per_day in out[src].values():
                for name, counts in per_day.items():
                    tot = out[dst].setdefault(name, {})
                    for k, n in counts.items():
                        tot[k] = tot.get(k, 0) + n
        for k in rc.scan_iter(match="cobalt:trip:*", count=200):
            name = _s(k).split(":", 2)[2]
            out["tripped"].append({"platform": name, "seconds_left": int(rc.ttl(k) or 0)})
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin_cobalt: redis unavailable: %s", type(exc).__name__)
        out["redis_ok"] = False
    return out


@router.get("/cobalt/status")
async def cobalt_status(days: int = Query(7), _=Depends(verify_admin)) -> dict:
    import asyncio  # noqa: PLC0415
    return await asyncio.to_thread(_status, days)
