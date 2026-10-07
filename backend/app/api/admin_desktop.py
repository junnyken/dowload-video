"""
Admin — Windows app (task #6087, PLAN-32D §7.1). Read-only.
============================================================
All routes require verify_admin, mounted under /api/v1/admin.

  GET /admin/desktop/devices?q=&limit=50&offset=0
      machines from desktop_devices (migration 037), newest activity first,
      each with today's usage of the allowance it is counted under
      (signed-in → user:<id>, shared with the web; guest → dev:<32 hex>).
      Only the 8-char display code and a 16-hex opaque id leave the server,
      never the full 64-hex machine hash.
  GET /admin/desktop/stats?days=7   (1..31)
      per UTC day: vidgrab:stats:route:<day> grouped by route × outcome
      (written by app.api.client_quota._stat), totals, new machines per day,
      machines active today, the client-quota flags (non-secret values only)
      and a short summary: share local vs server, over-limit-in-shadow,
      refunds today.
      PLAN-32E P0 (gate 14/10): the V/C ratio (over_shadow / counted) overall
      and per requester kind (guest = machine + guest IP, account = signed
      in) from the "kind:<kind>|<outcome>" fields of the same hash, per day
      too; refunds and offline (retro) claims per day; the top 10 machines /
      accounts by over-limit today (display code only); machines active in
      the period per app version (desktop_devices.client_version).

Table missing (migration 037 not applied) → 200 with storage_ready=false.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.admin import verify_admin
from app.core import quotas

logger = logging.getLogger(__name__)

router = APIRouter()

TABLE = "desktop_devices"
ROUTES = ("local", "local_cookie", "server")
OUTCOMES = ("ok", "over_shadow", "refused", "retro",
            "settle_completed", "settle_failed", "settle_cancelled")
# Outcomes where the download went ahead and was counted against the allowance.
_COUNTED = ("ok", "over_shadow", "retro")
KINDS = ("user", "device", "anon", "admin")
KIND_OUTCOMES = OUTCOMES + ("refunded",)
TOP_OVER_MAX = 10
_Q_SAFE = re.compile(r"[^\w\s.@\-]", re.UNICODE)
_HEX = re.compile(r"^[0-9a-fA-F]{1,64}$")
_UID = re.compile(r"^[0-9a-fA-F-]{4,36}$")


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _db():
    from app.core.database import get_service_client  # noqa: PLC0415
    return get_service_client()


def _storage_not_ready(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "42p01" in msg or "pgrst205" in msg or (
        TABLE in msg and ("does not exist" in msg or "schema cache" in msg))


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _day(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _today_start() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _clean_q(q: str) -> str:
    # PostgREST or_() syntax uses , ( ) . : as separators — keep only word
    # characters, spaces, '.', '@', '-' (dots are fine inside a value).
    return _Q_SAFE.sub("", (q or "").strip())[:64].strip()


def _profiles(user_ids: List[str]) -> Dict[str, dict]:
    """One query for every user on the page (email + tier for the limit)."""
    if not user_ids:
        return {}
    try:
        res = (_db().table("profiles")
               .select("id, email, tier, billing_status, subscription_expiry, grace_period_ends_at")
               .in_("id", user_ids).execute())
        return {str(p.get("id")): p for p in (res.data or [])}
    except Exception as exc:  # noqa: BLE001
        logger.info("admin_desktop profiles lookup skipped: %s", type(exc).__name__)
        return {}


def _user_ids_by_email(q: str) -> List[str]:
    try:
        res = _db().table("profiles").select("id").ilike("email", f"%{q}%").limit(50).execute()
        return [str(p["id"]) for p in (res.data or []) if p.get("id")]
    except Exception:  # noqa: BLE001
        return []


def _search_filter(q: str) -> Optional[str]:
    """PostgREST or_() over: 8-char code (hash prefix), machine name, user id
    (prefix), or e-mail (resolved to user ids first). None = nothing to match.
    '.' is a separator in or_() syntax, so it is dropped from the name match."""
    parts = []
    name = q.replace(".", " ").replace("@", " ").strip()
    if name:
        parts.append(f"display_name.ilike.*{name}*")
    if _HEX.match(q):
        parts.append(f"device_hash.ilike.{q.lower()}*")
    if _UID.match(q):
        parts.append(f"user_id.ilike.{q.lower()}*")
    if "@" in q or "." in q:
        ids = _user_ids_by_email(q)
        if ids:
            parts.append(f"user_id.in.({','.join(ids)})")
    return ",".join(parts) or None


def _usage_today(rows: List[dict], profiles: Dict[str, dict]) -> List[dict]:
    """used/limit of today for each row, plus refunds and offline (retro)
    claims, in one MGET."""
    day = quotas._utc_day()
    reqs = []
    for row in rows:
        uid = row.get("user_id")
        if uid:
            reqs.append(quotas.QuotaRequester(quotas.REQ_USER, str(uid)))
        else:
            reqs.append(quotas.QuotaRequester(quotas.REQ_DEVICE, str(row.get("device_hash") or "")[:32]))
    keys = []
    for req in reqs:
        keys += [f"vidgrab:quota:plat:{req.key}:{quotas._TOTAL_BUCKET}:{day}",
                 f"vidgrab:quota:refund:{req.key}:{day}",
                 f"vidgrab:quota:retro:{req.key}:{day}"]
    vals: List[Any] = [None] * len(keys)
    if keys:
        try:
            vals = _r().mget(keys)
        except Exception as exc:  # noqa: BLE001
            logger.info("admin_desktop usage read failed: %s", type(exc).__name__)

    def num(v) -> Optional[int]:
        try:
            return int(_s(v)) if v is not None else 0
        except ValueError:
            return 0

    out = []
    for i, req in enumerate(reqs):
        if req.kind == quotas.REQ_USER:
            prof = profiles.get(req.ident)
            limit = quotas.platform_limit_for_tier(quotas._effective_tier(prof)) if prof else None
        else:
            limit = quotas.platform_limit_anon()
        out.append({"counted_as": "user" if req.kind == quotas.REQ_USER else "device",
                    "used": num(vals[3 * i]), "limit": limit,
                    "refunds": num(vals[3 * i + 1]), "retro": num(vals[3 * i + 2])})
    return out


def _device_view(row: dict, usage: dict, profiles: Dict[str, dict]) -> dict:
    h = str(row.get("device_hash") or "")
    uid = row.get("user_id")
    return {
        "id": h[:16],
        "code": h[:8].upper(),
        "display_name": row.get("display_name"),
        "client_version": row.get("client_version"),
        "user_id": uid,
        "user_email": (profiles.get(str(uid)) or {}).get("email") if uid else None,
        "last_ip": row.get("last_ip"),
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "today": usage,
    }


@router.get("/desktop/devices")
async def desktop_devices(
    q: str = Query("", max_length=128),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
    _=Depends(verify_admin),
) -> dict:
    import asyncio  # noqa: PLC0415
    return await asyncio.to_thread(_list_devices, q, limit, offset)


def _list_devices(q: str, limit: int, offset: int) -> dict:
    qq = _clean_q(q)
    try:
        query = (_db().table(TABLE)
                 .select("device_hash, display_name, client_version, user_id, last_ip, first_seen, last_seen",
                         count="exact")
                 .order("last_seen", desc=True))
        if qq:
            flt = _search_filter(qq)
            if not flt:
                return {"storage_ready": True, "devices": [], "total": 0, "limit": limit, "offset": offset,
                        "day_utc": quotas._utc_day(),
                        "limits": {"anon": quotas.platform_limit_anon(), "user": quotas.platform_limit_user()}}
            query = query.or_(flt)
        res = query.range(offset, offset + limit - 1).execute()
    except Exception as exc:  # noqa: BLE001
        if _storage_not_ready(exc):
            return {"storage_ready": False, "devices": [], "total": 0, "limit": limit, "offset": offset,
                    "day_utc": quotas._utc_day()}
        logger.warning("admin_desktop devices query failed: %s", type(exc).__name__)
        raise
    rows = res.data or []
    uids = sorted({str(r["user_id"]) for r in rows if r.get("user_id")})
    profiles = _profiles(uids)
    usage = _usage_today(rows, profiles)
    total = getattr(res, "count", None)
    return {
        "storage_ready": True,
        "devices": [_device_view(r, u, profiles) for r, u in zip(rows, usage)],
        "total": total if isinstance(total, int) else len(rows),
        "limit": limit, "offset": offset, "day_utc": quotas._utc_day(),
        "limits": {"anon": quotas.platform_limit_anon(), "user": quotas.platform_limit_user()},
    }


# ── stats ────────────────────────────────────────────────────────────────

def _flags() -> dict:
    from app.api import client_api, client_quota  # noqa: PLC0415
    env = os.environ.get
    return {
        "client_quota_enabled": client_quota.quota_enabled(),
        "client_quota_mode": client_quota.quota_mode(),
        "client_quota_enforce_for": sorted(client_quota.enforce_for()),
        "cookie_platforms": client_api.cookie_platforms(),
        "server_fallback_platforms": client_api.server_fallback_platforms(),
        "desktop_latest_version": (env("DESKTOP_LATEST_VERSION") or "0.1.0").strip(),
        "desktop_min_version": (env("DESKTOP_MIN_VERSION") or "0.1.0").strip(),
        "offline_grace": client_quota.offline_grace(),
        "refund_daily_max": client_quota.refund_daily_max(),
        "refund_daily_max_guest": client_quota.refund_daily_max_guest(),
        "ip_mult": client_quota.ip_mult(),
        "limit_anon": quotas.platform_limit_anon(),
        "limit_user": quotas.platform_limit_user(),
    }


def _empty_grid() -> Dict[str, Dict[str, int]]:
    return {r: {o: 0 for o in OUTCOMES} for r in ROUTES}


def _read_day(r, day: str) -> Dict[str, Dict[str, int]]:
    grid = _empty_grid()
    raw = r.hgetall(f"vidgrab:stats:route:{day}") or {}
    for k, v in raw.items():
        route, _, outcome = _s(k).partition("|")
        if route in grid and outcome in grid[route]:
            try:
                grid[route][outcome] += int(_s(v))
            except ValueError:
                pass
    return grid


def _empty_kinds() -> Dict[str, Dict[str, int]]:
    return {k: {o: 0 for o in KIND_OUTCOMES} for k in KINDS}


def _read_kinds(r, day: str) -> Dict[str, Dict[str, int]]:
    """"kind:<kind>|<outcome>" fields of the day's hash (client_quota._stat)."""
    out = _empty_kinds()
    raw = r.hgetall(f"vidgrab:stats:route:{day}") or {}
    for k, v in raw.items():
        key = _s(k)
        if not key.startswith("kind:"):
            continue
        kind, _, outcome = key[5:].partition("|")
        if kind in out and outcome in out[kind]:
            try:
                out[kind][outcome] += int(_s(v))
            except ValueError:
                pass
    return out


def _ratio(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def _kind_summary(cells: List[Dict[str, int]]) -> dict:
    """Sum of several kinds' outcome cells → counted / over / V/C ratio."""
    tot = {o: sum(c[o] for c in cells) for o in KIND_OUTCOMES}
    counted = sum(tot[o] for o in _COUNTED)
    return {"counted": counted, "over_shadow": tot["over_shadow"], "refused": tot["refused"],
            "retro": tot["retro"], "refunded": tot["refunded"],
            "settle_failed": tot["settle_failed"], "settle_cancelled": tot["settle_cancelled"],
            "vc_ratio": _ratio(tot["over_shadow"], counted)}


def _split(kinds: Dict[str, Dict[str, int]]) -> dict:
    return {"guest": _kind_summary([kinds["device"], kinds["anon"]]),
            "account": _kind_summary([kinds["user"]]),
            "device": _kind_summary([kinds["device"]]),
            "anon": _kind_summary([kinds["anon"]])}


def _top_over_today(r) -> List[dict]:
    """Top machines / accounts by over-limit-in-shadow claims today. Only the
    8-char display code leaves the server (machine: hash prefix as on the
    device list; account: user id prefix)."""
    # read a little more than shown, in case a member of another kind is in it
    rows = r.zrevrange(f"vidgrab:stats:over_top:{quotas._utc_day()}", 0, 2 * TOP_OVER_MAX - 1,
                       withscores=True) or []
    out = []
    for member, score in rows:
        key = _s(member)
        kind, _, ident = key.partition(":")
        if kind == "dev":
            out.append({"kind": "device", "code": ident[:8].upper(), "over": int(score)})
        elif kind == "user":
            out.append({"kind": "user", "code": ident[:8], "over": int(score)})
    return out[:TOP_OVER_MAX]


def _version_key(v: Optional[str]):
    parts = []
    for p in re.split(r"[.\-+]", v or ""):
        parts.append(int(p) if p.isdigit() else -1)
    return (v is not None, parts)


def _refunds_today(r) -> int:
    """Sum of every requester's refund counter today (those keys expire at
    UTC midnight, so only today exists)."""
    total = 0
    try:
        for k in r.scan_iter(match=f"vidgrab:quota:refund:*:{quotas._utc_day()}", count=500):
            try:
                total += int(_s(r.get(k) or 0))
            except ValueError:
                pass
    except Exception:  # noqa: BLE001
        return 0
    return total


def _device_counts(start: datetime) -> dict:
    """New machines per UTC day since `start`, active today, total, and the
    machines active since `start` per app version."""
    out: Dict[str, Any] = {"storage_ready": True, "new_by_day": {}, "active_today": 0, "total": 0,
                           "versions": []}
    try:
        db = _db()
        res = (db.table(TABLE).select("first_seen").gte("first_seen", start.isoformat())
               .limit(10_000).execute())
        for row in res.data or []:
            try:
                d = datetime.fromisoformat(str(row["first_seen"]).replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            key = _day(d)
            out["new_by_day"][key] = out["new_by_day"].get(key, 0) + 1
        act = (db.table(TABLE).select("device_hash", count="exact")
               .gte("last_seen", _today_start().isoformat()).limit(1).execute())
        out["active_today"] = act.count if isinstance(getattr(act, "count", None), int) else len(act.data or [])
        tot = db.table(TABLE).select("device_hash", count="exact").limit(1).execute()
        out["total"] = tot.count if isinstance(getattr(tot, "count", None), int) else len(tot.data or [])
        ver = (db.table(TABLE).select("client_version").gte("last_seen", start.isoformat())
               .limit(10_000).execute())
        by_ver: Dict[Optional[str], int] = {}
        for row in ver.data or []:
            v = (str(row.get("client_version") or "").strip() or None)
            by_ver[v] = by_ver.get(v, 0) + 1
        out["versions"] = [{"version": v, "machines": c}
                           for v, c in sorted(by_ver.items(), key=lambda kv: _version_key(kv[0]), reverse=True)]
    except Exception as exc:  # noqa: BLE001
        if _storage_not_ready(exc):
            out["storage_ready"] = False
        else:
            logger.info("admin_desktop device counts failed: %s", type(exc).__name__)
            out["error"] = True
    return out


def _sum_grid(grids: List[Dict[str, Dict[str, int]]]) -> Dict[str, Dict[str, int]]:
    tot = _empty_grid()
    for g in grids:
        for route in ROUTES:
            for o in OUTCOMES:
                tot[route][o] += g[route][o]
    return tot


@router.get("/desktop/stats")
async def desktop_stats(days: int = Query(7), _=Depends(verify_admin)) -> dict:
    import asyncio  # noqa: PLC0415
    return await asyncio.to_thread(_stats, days)


def _stats(days: int) -> dict:
    days = max(1, min(int(days), 31))
    today = _today_start()
    day_keys = [_day(today - timedelta(days=i)) for i in range(days)]  # newest first
    redis_ok = True
    grids: Dict[str, Dict[str, Dict[str, int]]] = {}
    kinds: Dict[str, Dict[str, Dict[str, int]]] = {}
    refunds = 0
    top_over: List[dict] = []
    try:
        r = _r()
        for d in day_keys:
            grids[d] = _read_day(r, d)
            kinds[d] = _read_kinds(r, d)
        refunds = _refunds_today(r)
        top_over = _top_over_today(r)
    except Exception as exc:  # noqa: BLE001
        logger.info("admin_desktop stats read failed: %s", type(exc).__name__)
        redis_ok = False
        grids = {d: _empty_grid() for d in day_keys}
        kinds = {d: _empty_kinds() for d in day_keys}
    dev = _device_counts(today - timedelta(days=days - 1))

    per_day = []
    for d in day_keys:
        g = grids[d]
        day_counted = sum(g[r][o] for r in ROUTES for o in _COUNTED)
        day_over = sum(g[r]["over_shadow"] for r in ROUTES)
        per_day.append({
            "day": d, "routes": g,
            "over_shadow": day_over,
            "refused": sum(g[r]["refused"] for r in ROUTES),
            "new_devices": dev["new_by_day"].get(d, 0),
            "counted": day_counted,
            "vc_ratio": _ratio(day_over, day_counted),
            "retro": sum(g[r]["retro"] for r in ROUTES),
            "refunds": sum(kinds[d][k]["refunded"] for k in KINDS),
            "by_kind": _split(kinds[d]),
        })
    totals = _sum_grid(list(grids.values()))
    counted = {r: sum(totals[r][o] for o in _COUNTED) for r in ROUTES}
    local = counted["local"] + counted["local_cookie"]
    all_counted = local + counted["server"]
    summary = {
        "counted_local": counted["local"],
        "counted_local_cookie": counted["local_cookie"],
        "counted_server": counted["server"],
        "local_share": round(local / all_counted, 4) if all_counted else None,
        "over_shadow": sum(totals[r]["over_shadow"] for r in ROUTES),
        "refused": sum(totals[r]["refused"] for r in ROUTES),
        "retro": sum(totals[r]["retro"] for r in ROUTES),
        "settle_failed": sum(totals[r]["settle_failed"] for r in ROUTES),
        "settle_cancelled": sum(totals[r]["settle_cancelled"] for r in ROUTES),
        "refunds_today": refunds,
        "vc_ratio": _ratio(sum(totals[r]["over_shadow"] for r in ROUTES), all_counted),
    }
    kind_totals = _empty_kinds()
    for d in day_keys:
        for k in KINDS:
            for o in KIND_OUTCOMES:
                kind_totals[k][o] += kinds[d][k][o]
    summary["by_kind"] = _split(kind_totals)
    return {
        "days": days, "day_utc": day_keys[0], "redis_ok": redis_ok,
        "routes": list(ROUTES), "outcomes": list(OUTCOMES),
        "per_day": per_day, "totals": totals, "summary": summary,
        "devices": {"storage_ready": dev["storage_ready"], "active_today": dev["active_today"],
                    "total": dev["total"],
                    "new_in_period": sum(dev["new_by_day"].get(d, 0) for d in day_keys),
                    "versions": dev["versions"]},
        "top_over_today": top_over,
        "flags": _flags(),
    }
