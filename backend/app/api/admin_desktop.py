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

  POST /admin/desktop/allowance   (task #6125, PLAN-32E P1 step 4)
      {device_id: <16 hex from the list>, action: "grant"|"reset",
       amount: 1..50 (grant), reason: 3..300 chars}
      Today only, Redis only. Applies to the allowance the machine is counted
      under (signed-in → the account, shared with the web; guest → the
      machine). grant raises today's limit (total ≤ quotas.BONUS_DAY_MAX);
      reset sets today's used count to 0. Audited via log_admin_action.

  POST /admin/desktop/ip/reset   (task #6125, PLAN-32E §6)
      {ip: <the machine's last IP from the list>, reason: 3..300 chars}
      Today only: clears the network's app-guest counter (the IP cap,
      reason "ip_limit"); each machine's own allowance stays. Audited.

  GET /admin/desktop/signals?days=7   (1..31; task #6126, PLAN-32E §5.3)
      soft anomaly hints, display only: many machines behind one IP / IP cap
      hit on several days; several accounts on one machine; offline reports
      at the grace on several days; many refunds; finished downloads in the
      synced history beyond what the server let through (accounts only);
      app call versions per day. Facts from app.core.desktop_signals.

Table missing (migration 037 not applied) → 200 with storage_ready=false.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.api.admin import verify_admin
from app.core.audit import log_admin_action
from app.core import update_gate
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
_DEVICE_ID = re.compile(r"^[0-9a-f]{16}$")
GRANT_MAX = 50
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
    """used/limit of today for each row, plus refunds, offline (retro)
    claims and today's admin grant (included in limit), in one MGET."""
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
                 f"vidgrab:quota:retro:{req.key}:{day}",
                 f"vidgrab:quota:bonus:{req.key}:{day}"]
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
        bonus = num(vals[4 * i + 3]) or 0
        if limit is not None and limit != -1:
            limit += bonus  # admin grant today (task #6125)
        out.append({"counted_as": "user" if req.kind == quotas.REQ_USER else "device",
                    "used": num(vals[4 * i]), "limit": limit, "bonus": bonus,
                    "refunds": num(vals[4 * i + 1]), "retro": num(vals[4 * i + 2])})
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


# ── allowance grant / reset (task #6125) ─────────────────────────────────

def _bad(code: str, message: str, status: int = 400):
    raise HTTPException(status_code=status, detail={"error": code, "message": message})


def _requester_for_device(device_id: str) -> "quotas.QuotaRequester":
    """The machine with this list id (first 16 hex of its hash), counted the
    same way the device list shows it."""
    try:
        res = (_db().table(TABLE).select("device_hash, user_id")
               .like("device_hash", f"{device_id}%").limit(2).execute())
    except Exception as exc:  # noqa: BLE001
        if _storage_not_ready(exc):
            _bad("storage_not_ready", "Chưa có bảng máy (migration 037).", 503)
        raise
    rows = res.data or []
    if len(rows) != 1:
        _bad("device_not_found", "Không tìm thấy máy này.", 404)
    row = rows[0]
    if row.get("user_id"):
        return quotas.QuotaRequester(quotas.REQ_USER, str(row["user_id"]))
    return quotas.QuotaRequester(quotas.REQ_DEVICE, str(row.get("device_hash") or "")[:32])


def _allowance(payload: dict) -> dict:
    device_id = str(payload.get("device_id") or "").strip().lower()
    action = payload.get("action")
    if not _DEVICE_ID.match(device_id):
        _bad("bad_device_id", "Mã máy không hợp lệ.")
    if action not in ("grant", "reset"):
        _bad("bad_action", "Thao tác không hợp lệ.")
    amount = 0
    if action == "grant":
        amount = payload.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= GRANT_MAX:
            _bad("bad_amount", f"Số lượt cộng phải từ 1 đến {GRANT_MAX}.")
    reason = str(payload.get("reason") or "").strip()
    if not 3 <= len(reason) <= 300:
        _bad("reason_required", "Cần nhập lý do (3–300 ký tự).")
    req = _requester_for_device(device_id)
    try:
        if action == "grant":
            bonus = quotas.grant_platform_bonus(req, amount)
            removed = None
        else:
            removed = quotas.reset_platform_usage(req)
            bonus = quotas.platform_bonus(req)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin_desktop allowance failed: %s", type(exc).__name__)
        _bad("redis_unavailable", "Không ghi được (Redis lỗi). Thử lại sau.", 503)
    counted_as = "user" if req.kind == quotas.REQ_USER else "device"
    return {"device_id": device_id, "action": action, "amount": amount, "reason": reason,
            "counted_as": counted_as, "removed": removed, "bonus_today": bonus,
            "limit_today": quotas.platform_limit(req),
            "used_today": quotas.platform_used(req, quotas._TOTAL_BUCKET),
            "day_utc": quotas._utc_day()}


@router.post("/desktop/allowance")
async def desktop_allowance(request: Request, payload: dict = Body(...), _=Depends(verify_admin)) -> dict:
    import asyncio  # noqa: PLC0415
    out = await asyncio.to_thread(_allowance, payload if isinstance(payload, dict) else {})
    log_admin_action(request, f"admin.desktop.allowance_{out['action']}", resource_type="desktop_device",
                     resource_id=out["device_id"],
                     metadata={"counted_as": out["counted_as"], "amount": out["amount"],
                               "bonus_today": out["bonus_today"], "removed": out["removed"],
                               "reason": out.pop("reason")[:300], "day_utc": out["day_utc"]})
    return out


def _ip_reset(payload: dict) -> dict:
    import ipaddress  # noqa: PLC0415
    raw = str(payload.get("ip") or "").strip()
    try:
        ip = str(ipaddress.ip_address(raw))
    except ValueError:
        _bad("bad_ip", "Địa chỉ IP không hợp lệ.")
    reason = str(payload.get("reason") or "").strip()
    if not 3 <= len(reason) <= 300:
        _bad("reason_required", "Cần nhập lý do (3–300 ký tự).")
    try:
        removed = quotas.reset_device_ip(ip)
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin_desktop ip reset failed: %s", type(exc).__name__)
        _bad("redis_unavailable", "Không ghi được (Redis lỗi). Thử lại sau.", 503)
    lim = quotas.platform_limit_anon()
    return {"ip": ip, "removed": removed, "reason": reason,
            "ip_cap": None if lim == -1 else lim * _ip_mult(), "day_utc": quotas._utc_day()}


def _ip_mult() -> int:
    from app.api import client_quota  # noqa: PLC0415
    return client_quota.ip_mult()


@router.post("/desktop/ip/reset")
async def desktop_ip_reset(request: Request, payload: dict = Body(...), _=Depends(verify_admin)) -> dict:
    import asyncio  # noqa: PLC0415
    out = await asyncio.to_thread(_ip_reset, payload if isinstance(payload, dict) else {})
    log_admin_action(request, "admin.desktop.ip_reset", resource_type="ip", resource_id=out["ip"],
                     metadata={"removed": out["removed"], "reason": out.pop("reason")[:300],
                               "day_utc": out["day_utc"]})
    return out


# ── stats ────────────────────────────────────────────────────────────────

def _flags() -> dict:
    from app.api import client_api, client_quota  # noqa: PLC0415
    env = os.environ.get
    return {
        "client_quota_enabled": client_quota.quota_enabled(),
        "client_quota_mode": client_quota.quota_mode(),
        "client_quota_enforce_for": sorted(client_quota.enforce_for()),
        # Task #6125: canary accounts (count only on the page)
        "client_quota_enforce_users_count": len(client_quota.enforce_users()),
        "client_update_gate_enabled": update_gate.gate_enabled(),
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


# ── signals (task #6126, PLAN-32E §5.3, P2) ──────────────────────────────
# Display only: soft hints for an admin to look at, never an automatic lock.
# Raw facts are written by app.core.desktop_signals.

SIG_IP_MACHINES = 4          # machines behind one IP in a day
SIG_IP_LIMIT_DAYS = 3        # days the IP cap was hit in the period
SIG_DEVICE_USERS = 3         # accounts signed in on one machine in a day
SIG_RETRO_DAYS = 3           # days with offline reports >= the offline grace
SIG_REFUND_MIN = 3           # refunds in a day, and
SIG_REFUND_SHARE = 0.5       # ... at least this share of that day's downloads
SIG_ROWS_MAX = 100


def _hash_ints(r, key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for k, v in (r.hgetall(key) or {}).items():
        try:
            out[_s(k)] = int(_s(v))
        except ValueError:
            pass
    return out


def _members(r, key: str) -> List[str]:
    return [_s(m) for m in (r.smembers(key) or [])]


def _completed_by_user_day(start: datetime) -> Optional[Dict[str, Dict[str, int]]]:
    """Synced app history (migration 034, signed-in only, G7): completed
    downloads per user per UTC day. None when the table cannot be read."""
    try:
        res = (_db().table("desktop_downloads").select("user_id, finished_at, state")
               .gte("finished_at", start.isoformat()).limit(50_000).execute())
    except Exception as exc:  # noqa: BLE001
        logger.info("admin_desktop signals: history unreadable: %s", type(exc).__name__)
        return None
    out: Dict[str, Dict[str, int]] = {}
    for row in res.data or []:
        if row.get("state") != "completed" or not row.get("user_id"):
            continue
        try:
            d = datetime.fromisoformat(str(row["finished_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        per = out.setdefault(str(row["user_id"]), {})
        per[_day(d)] = per.get(_day(d), 0) + 1
    return out


def _subject(req_key: str) -> dict:
    kind, _, ident = req_key.partition(":")
    if kind == "dev":
        return {"kind": "device", "code": ident[:8].upper(), "device_id": ident[:16]}
    if kind == "user":
        return {"kind": "user", "user_id": ident}
    return {"kind": "other", "code": ident[:16]}


def _attach_people(rows: List[dict]) -> None:
    """Email for account rows, and a machine to act on (the account's most
    recently seen one) so "Cộng lượt" / "Đặt lại" work from the list."""
    uids = sorted({r["subject"]["user_id"] for r in rows if r["subject"].get("user_id")})
    if not uids:
        return
    profs = _profiles(uids)
    last_dev: Dict[str, str] = {}
    try:
        res = (_db().table(TABLE).select("device_hash, user_id, last_seen").in_("user_id", uids)
               .order("last_seen", desc=True).limit(1000).execute())
        for row in res.data or []:
            last_dev.setdefault(str(row.get("user_id")), str(row.get("device_hash") or ""))
    except Exception as exc:  # noqa: BLE001
        logger.info("admin_desktop signals: device lookup skipped: %s", type(exc).__name__)
    for r in rows:
        uid = r["subject"].get("user_id")
        if uid:
            r["subject"]["email"] = (profs.get(uid) or {}).get("email")
            dev = last_dev.get(uid)
            if dev:
                r["subject"].setdefault("device_id", dev[:16])


def _signals(days: int) -> dict:
    from app.api import client_quota  # noqa: PLC0415
    days = max(1, min(int(days), 31))
    today = _today_start()
    day_keys = [_day(today - timedelta(days=i)) for i in range(days)]  # newest first
    grace = client_quota.offline_grace()
    rows: List[dict] = []
    versions: Dict[str, Dict[str, int]] = {}
    try:
        r = _r()
        app_dl = {d: _hash_ints(r, f"vidgrab:sig:app_dl:{d}") for d in day_keys}

        # 1. many machines behind one IP / the IP cap hit on several days
        ip_days: Dict[str, Dict[str, Any]] = {}
        for d in day_keys:
            for ip in _members(r, f"vidgrab:sig:ips:{d}"):
                n = int(r.scard(f"vidgrab:sig:ipdev:{ip}:{d}") or 0)
                e = ip_days.setdefault(ip, {"max": 0, "busy": [], "limit": []})
                e["max"] = max(e["max"], n)
                if n >= SIG_IP_MACHINES:
                    e["busy"].append(d)
            for ip, n in _hash_ints(r, f"vidgrab:sig:iplimit:{d}").items():
                if n > 0:
                    ip_days.setdefault(ip, {"max": 0, "busy": [], "limit": []})["limit"].append(d)
        for ip, e in ip_days.items():
            if e["busy"] or len(e["limit"]) >= SIG_IP_LIMIT_DAYS:
                hit = sorted(set(e["busy"]) | set(e["limit"]), reverse=True)
                rows.append({"signal": "ip_many_machines", "subject": {"kind": "ip", "ip": ip},
                             "value": e["max"], "days_hit": len(hit), "last_day": hit[0],
                             "ip_limit_days": len(e["limit"])})

        # 2. several accounts signed in on one machine in a day
        dev_max: Dict[str, Dict[str, Any]] = {}
        for d in day_keys:
            for dev in _members(r, f"vidgrab:sig:devs:{d}"):
                n = int(r.scard(f"vidgrab:sig:devusers:{dev}:{d}") or 0)
                if n >= SIG_DEVICE_USERS:
                    e = dev_max.setdefault(dev, {"max": 0, "days": []})
                    e["max"] = max(e["max"], n)
                    e["days"].append(d)
        for dev, e in dev_max.items():
            rows.append({"signal": "device_many_accounts", "subject": _subject(f"dev:{dev}"),
                         "value": e["max"], "days_hit": len(e["days"]), "last_day": e["days"][0]})

        # 3. offline reports at the grace on several days
        retro_days: Dict[str, List[str]] = {}
        refund_rows: Dict[str, Dict[str, Any]] = {}
        for d in day_keys:
            for req, n in _hash_ints(r, f"vidgrab:sig:retro:{d}").items():
                if grace > 0 and n >= grace:
                    retro_days.setdefault(req, []).append(d)
            # 4. refunds: many, and a large share of the day's downloads
            for req, n in _hash_ints(r, f"vidgrab:sig:refund:{d}").items():
                base = app_dl[d].get(req, 0)
                if n >= SIG_REFUND_MIN and n >= SIG_REFUND_SHARE * max(base, 1):
                    e = refund_rows.setdefault(req, {"max": 0, "days": [], "base": 0})
                    if n > e["max"]:
                        e["max"], e["base"] = n, base
                    e["days"].append(d)
        for req, ds in retro_days.items():
            if len(ds) >= SIG_RETRO_DAYS:
                rows.append({"signal": "offline_repeat", "subject": _subject(req),
                             "value": len(ds), "days_hit": len(ds), "last_day": ds[0]})
        for req, e in refund_rows.items():
            rows.append({"signal": "refund_high", "subject": _subject(req), "value": e["max"],
                         "downloads": e["base"], "days_hit": len(e["days"]), "last_day": e["days"][0]})

        # 5. version mix of app calls per day
        for d in day_keys:
            versions[d] = _hash_ints(r, f"vidgrab:sig:ver:{d}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin_desktop signals: redis unavailable: %s", type(exc).__name__)
        return {"days": days, "day_keys": day_keys, "redis_ok": False, "history_ok": False,
                "signals": [], "versions": {}, "thresholds": _thresholds(grace)}

    # 6. finished downloads in the synced history beyond what the server let
    #    through (+ the offline grace) — signed-in accounts only (G7)
    hist = _completed_by_user_day(today - timedelta(days=days - 1))
    if hist is not None:
        for uid, per in hist.items():
            hit = [(d, n) for d, n in per.items()
                   if d in app_dl and n > app_dl[d].get(f"user:{uid}", 0) + grace]
            if hit:
                hit.sort(reverse=True)
                worst = max(hit, key=lambda x: x[1] - app_dl[x[0]].get(f"user:{uid}", 0))
                rows.append({"signal": "unclaimed_downloads", "subject": _subject(f"user:{uid}"),
                             "value": worst[1], "downloads": app_dl[worst[0]].get(f"user:{uid}", 0),
                             "days_hit": len(hit), "last_day": hit[0][0]})

    rows.sort(key=lambda x: (x["days_hit"], x["last_day"], x["value"]), reverse=True)
    rows = rows[:SIG_ROWS_MAX]
    _attach_people(rows)
    return {"days": days, "day_keys": day_keys, "redis_ok": True, "history_ok": hist is not None,
            "signals": rows, "versions": versions, "thresholds": _thresholds(grace)}


def _thresholds(grace: int) -> dict:
    return {"ip_machines": SIG_IP_MACHINES, "ip_limit_days": SIG_IP_LIMIT_DAYS,
            "device_accounts": SIG_DEVICE_USERS, "offline_days": SIG_RETRO_DAYS,
            "offline_grace": grace, "refund_min": SIG_REFUND_MIN, "refund_share": SIG_REFUND_SHARE}


@router.get("/desktop/signals")
async def desktop_signals_view(days: int = Query(7), _=Depends(verify_admin)) -> dict:
    import asyncio  # noqa: PLC0415
    return await asyncio.to_thread(_signals, days)
