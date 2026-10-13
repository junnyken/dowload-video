"""
Phase 34C (task #6334) — measurement on top of what already exists.

Nothing here is a new source of truth for downloads or Apify spend; it READS
the stores that are already written and adds only the counters that were
missing:

  read (existing)
    vidgrab:stats:{YYYY-MM-DD}            download_outcomes — attempts/successes
                                          per platform, every download path, 35 d
    vidgrab:latency:{YYYY-MM-DD}          download_outcomes — time-to-result
                                          histogram of successes (added in 34C)
    china:metrics:{YYYYMMDD}:{platform}   cost_metrics — paid Apify spend per
                                          platform (douyin/kuaishou/xiaohongshu =
                                          China access layer; instagram = the
                                          Channel Watch scans, budget class
                                          apify_watch), 40 d
    vidgrab:youtube:proxy_bytes:{date}    youtube_gate — proxy bytes (3 d). No
                                          code path writes it today, so proxy
                                          cost is reported as "not measured".
  write (new, O(1), TTL 40 d, never raise)
    metrics:cost:user:{YYYYMMDD}          ZSET member → micro-USD. Member is the
                                          account id for "user:<id>" requesters,
                                          "_unattributed" for guests (IP buckets —
                                          the IP itself is never stored) and
                                          "_system" for admin/system work
    metrics:cost:src:{YYYYMMDD}           HASH "{source}:{platform}" → micro-USD
                                          (ASR spend: "asr:_none")
    metrics:cost:since                    first UTC day the two above were written

All days are UTC days (the cost stores are keyed that way); the admin page
says so.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TTL = 40 * 86400
K_USER = "metrics:cost:user:{day}"
K_SRC = "metrics:cost:src:{day}"
K_SINCE = "metrics:cost:since"
UNATTRIBUTED = "_unattributed"
SYSTEM = "_system"

CHINA_PLATFORMS = ("douyin", "kuaishou", "xiaohongshu")
WATCH_PLATFORM = "instagram"       # cost_metrics platform used by the IG watch scans
WATCH_BUDGET_CLASS = "apify_watch"

ALLOWED_DAYS = (7, 30)

# Admin settings (generic Redis config store of /admin/config, so the flag is
# also visible and editable on the Config page).
CONFIG_PREFIX = "admin:config:"
CONFIG_INDEX = "admin:config:__index__"
WEEKLY_FLAG = "metrics_weekly_report_enabled"


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _i(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def usd(micros: Optional[int]) -> Optional[float]:
    return None if micros is None else round(micros / 1e6, 6)


def compact(iso_day: str) -> str:
    return iso_day.replace("-", "")


def utc_days(days: int, now: Optional[datetime] = None) -> List[str]:
    """ISO UTC dates, oldest first, today last."""
    now = (now or _now()).astimezone(timezone.utc)
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def clamp_days(days: int) -> int:
    return days if days in ALLOWED_DAYS else 7


def rate(num: int, den: int) -> Optional[float]:
    """num/den rounded to 4 places; None when there is nothing to divide by."""
    return round(num / den, 4) if den else None


# ── write path ──────────────────────────────────────────────────────────────

def member_for(requester: Optional[str]) -> str:
    """Who a cost is charged to, without ever keeping an IP address."""
    req = (requester or "").strip()
    if req.startswith("user:") and len(req) > 5:
        return req[5:][:64]
    if req in ("admin", "system"):
        return SYSTEM
    if not req or ":" in req or req == "unknown":
        return UNATTRIBUTED      # ip:*, dev:*, unknown, … — not a person we can name
    return req[:64]              # a bare account id (ASR quota key of a signed-in user)


def _mark_since(r, day: str) -> None:
    r.set(K_SINCE, day, nx=True)


def record_user_cost(requester: Optional[str], micros: int, *, now: Optional[datetime] = None) -> None:
    """Charge `micros` to the requester for today's top-users table. Never raises."""
    try:
        micros = int(micros or 0)
        if micros <= 0:
            return
        day = (now or _now()).astimezone(timezone.utc).strftime("%Y%m%d")
        r = _r()
        k = K_USER.format(day=day)
        r.zincrby(k, micros, member_for(requester))
        r.expire(k, TTL)
        _mark_since(r, day)
    except Exception as exc:  # noqa: BLE001
        logger.debug("unit_metrics user cost failed: %s", type(exc).__name__)


def record_source_cost(source: str, platform: str, micros: int, *, now: Optional[datetime] = None) -> None:
    try:
        micros = int(micros or 0)
        if micros <= 0:
            return
        day = (now or _now()).astimezone(timezone.utc).strftime("%Y%m%d")
        r = _r()
        k = K_SRC.format(day=day)
        r.hincrby(k, f"{source}:{platform}", micros)
        r.expire(k, TTL)
        _mark_since(r, day)
    except Exception as exc:  # noqa: BLE001
        logger.debug("unit_metrics source cost failed: %s", type(exc).__name__)


def record_asr_cost(quota_key: Optional[str], cost_usd: float, *, now: Optional[datetime] = None) -> None:
    micros = int(round(max(0.0, float(cost_usd or 0.0)) * 1e6))
    record_source_cost("asr", "_none", micros, now=now)
    record_user_cost(quota_key, micros, now=now)


# ── percentiles from the latency histogram ─────────────────────────────────

def _bucket_sort_key(b: str) -> float:
    return float("inf") if b == "inf" else float(b)


def percentile(hist: Dict[str, int], q: float) -> Tuple[Optional[int], Optional[str]]:
    """(upper bound in ms, label) of the bucket holding the q-quantile.
    (None, None) without samples; an open last bucket → (None, "> N s")."""
    total = sum(v for v in hist.values() if v > 0)
    if total <= 0:
        return None, None
    target = q * total
    seen = 0
    buckets = sorted(hist.keys(), key=_bucket_sort_key)
    for b in buckets:
        seen += max(0, hist[b])
        if seen >= target:
            if b == "inf":
                from app.core.download_outcomes import LATENCY_BUCKETS_MS  # noqa: PLC0415
                return None, f"> {LATENCY_BUCKETS_MS[-1] // 1000} s"
            ms = int(b)
            return ms, f"≤ {ms / 1000:g} s"
    return None, None


def _merge_hist(into: Dict[str, int], other: Dict[str, int]) -> None:
    for b, n in (other or {}).items():
        into[b] = into.get(b, 0) + n


# ── success ─────────────────────────────────────────────────────────────────

def _cell(attempts: int, successes: int, hist: Optional[Dict[str, int]]) -> dict:
    p50, p50l = percentile(hist or {}, 0.5)
    p95, p95l = percentile(hist or {}, 0.95)
    return {
        "attempts": attempts,
        "successes": successes,
        "success_rate": rate(successes, attempts),
        "latency_samples": sum((hist or {}).values()) if hist is not None else None,
        "p50_ms": p50, "p50_label": p50l,
        "p95_ms": p95, "p95_label": p95l,
    }


def success_report(days: int = 7, now: Optional[datetime] = None) -> dict:
    from app.core import download_outcomes as do  # noqa: PLC0415
    days = clamp_days(days)
    dates = utc_days(days, now)
    counts = do.read_days(dates)
    lat = do.read_latency(dates)
    platforms = sorted({p for d in dates for p in (counts.get(d) or {})})
    rows = []
    tot_a = tot_s = 0
    tot_hist: Dict[str, int] = {}
    latency_any = any(lat.get(d) is not None for d in dates)
    for p in platforms:
        per_day = []
        pa = ps = 0
        phist: Dict[str, int] = {}
        for d in dates:
            day = counts.get(d)
            if day is None:
                per_day.append({"date": d, "available": False})
                continue
            c = day.get(p, {"ok": 0, "err": 0})
            a, s = c["ok"] + c["err"], c["ok"]
            # None = no histogram that day (not measured); {} = measured, no success
            h = lat[d].get(p, {}) if lat.get(d) is not None else None
            per_day.append({"date": d, "available": True, **_cell(a, s, h)})
            pa, ps = pa + a, ps + s
            _merge_hist(phist, h or {})
        tot_a, tot_s = tot_a + pa, tot_s + ps
        _merge_hist(tot_hist, phist)
        rows.append({"platform": p, "totals": _cell(pa, ps, phist if latency_any else None), "per_day": per_day})
    rows.sort(key=lambda r: -r["totals"]["attempts"])
    days_missing = [d for d in dates if counts.get(d) is None]
    return {
        "days": days,
        "day_basis": "UTC",
        "dates": dates,
        "platforms": rows,
        "totals": _cell(tot_a, tot_s, tot_hist if latency_any else None),
        "measurable": {
            "attempts": True,
            "time_to_result": latency_any,
        },
        "days_without_data": days_missing,
        "notes": [
            # wording: BA review
            "Lượt thử và lượt thành công: mọi đường tải (web, app, tiện ích, tải nền), mỗi lượt đếm 1 lần khi kết thúc.",
            "Thời gian tới kết quả: chỉ lượt thành công, ghi từ bản 34C. /fetch-link tính từ lúc nhận yêu cầu tới lúc có link; "
            "tải nền tính thời gian của lần thử thành công (chưa gồm thời gian xếp hàng). p50/p95 là mốc trên của khoảng (≤ N giây).",
        ],
    }


# ── costs ───────────────────────────────────────────────────────────────────

def _china_spend(r, day_c: str, platform: str) -> Optional[int]:
    from app.services.china_platforms import cost_metrics  # noqa: PLC0415
    h = r.hgetall(cost_metrics.key(day_c, platform))
    if not h:
        return None
    return _i(h.get("spend_usd_micros"))


def _proxy_price() -> float:
    try:
        from app.core import youtube_gate  # noqa: PLC0415
        return float(youtube_gate.PROXY_COST_PER_GB)
    except Exception:  # noqa: BLE001
        return float(os.getenv("YOUTUBE_PROXY_COST_PER_GB", "1.5"))


def _ceilings(r, dates: List[str]) -> List[dict]:
    """Average/peak daily spend against each provider's daily ceiling, plus the
    owner's rule-of-thumb ceiling (2–3× the measured average)."""
    out = []
    try:
        from app.services.china_platforms import settings as cs  # noqa: PLC0415
        targets = [(p, "apify_china", p, cs.platform_managed_daily_spend_micros(p)) for p in CHINA_PLATFORMS]
        targets.append((WATCH_BUDGET_CLASS, "apify_watch", WATCH_PLATFORM,
                        cs.provider_daily_spend_micros(WATCH_BUDGET_CLASS)))
    except Exception:  # noqa: BLE001
        targets = []
    for name, source, plat, ceiling in targets:
        vals = [_china_spend(r, compact(d), plat) for d in dates]
        known = [v for v in vals if v is not None]
        out.append(_ceiling_row(name, source, known, ceiling, len(dates)))
    try:
        from app.services.asr import config as asr_config  # noqa: PLC0415
        asr_ceiling = int(round(asr_config.spend_ceiling_usd() * 1e6))
    except Exception:  # noqa: BLE001
        asr_ceiling = 0
    since = r.get(K_SINCE)
    known = []
    for d in dates:
        if since and compact(d) >= since:
            known.append(_i(r.hget(K_SRC.format(day=compact(d)), "asr:_none")))
    out.append(_ceiling_row("asr", "asr", known, asr_ceiling, len(dates)))
    return out


def _ceiling_row(name: str, source: str, known: List[int], ceiling: int, n_days: int) -> dict:
    avg = int(sum(known) / n_days) if known else None
    peak = max(known) if known else None
    return {
        "provider": name, "source": source,
        "daily_ceiling_usd": usd(ceiling) if ceiling > 0 else None,
        "avg_daily_usd": usd(avg), "peak_daily_usd": usd(peak),
        "avg_pct_of_ceiling": round(avg * 100 / ceiling, 1) if (avg is not None and ceiling > 0) else None,
        "peak_pct_of_ceiling": round(peak * 100 / ceiling, 1) if (peak is not None and ceiling > 0) else None,
        "suggested_ceiling_usd": [usd(2 * avg), usd(3 * avg)] if avg else None,
        "available": bool(known),
    }


def cost_report(days: int = 7, now: Optional[datetime] = None) -> dict:
    from app.core import download_outcomes as do  # noqa: PLC0415
    days = clamp_days(days)
    dates = utc_days(days, now)
    r = _r()
    since = r.get(K_SINCE)
    counts = do.read_days(dates)
    price = _proxy_price()

    per_day = []
    by_plat: Dict[str, Dict[str, int]] = {}
    proxy_any = False
    asr_total = 0
    for d in dates:
        dc = compact(d)
        tracked = bool(since and dc >= since)
        row: Dict[str, Any] = {"date": d, "platforms": {}}
        day_micros = 0
        for p in CHINA_PLATFORMS + (WATCH_PLATFORM,):
            v = _china_spend(r, dc, p)
            if v is None:
                continue
            src = "apify_watch" if p == WATCH_PLATFORM else "apify_china"
            row["platforms"].setdefault(p, {})[src] = usd(v)
            by_plat.setdefault(p, {}).setdefault(src, 0)
            by_plat[p][src] += v
            day_micros += v
        raw = r.get(f"vidgrab:youtube:proxy_bytes:{d}")
        if raw is not None:
            proxy_any = True
            micros = int(_i(raw) / 1024 ** 3 * price * 1e6)
            row["platforms"].setdefault("youtube", {})["proxy"] = usd(micros)
            by_plat.setdefault("youtube", {}).setdefault("proxy", 0)
            by_plat["youtube"]["proxy"] += micros
            day_micros += micros
        if tracked:
            a = _i(r.hget(K_SRC.format(day=dc), "asr:_none"))
            row["asr_usd"] = usd(a)
            asr_total += a
            day_micros += a
        else:
            row["asr_usd"] = None    # not measured that day
        row["total_usd"] = usd(day_micros)
        per_day.append(row)

    successes: Dict[str, int] = {}
    for d in dates:
        for p, c in (counts.get(d) or {}).items():
            successes[p] = successes.get(p, 0) + c.get("ok", 0)

    platforms = []
    for p in sorted(set(by_plat) | set(successes)):
        srcs = by_plat.get(p, {})
        dl_micros = sum(v for s, v in srcs.items() if s != "apify_watch")
        ok = successes.get(p, 0)
        platforms.append({
            "platform": p,
            "by_source_usd": {s: usd(v) for s, v in srcs.items()},
            "download_cost_usd": usd(dl_micros),
            "watch_cost_usd": usd(srcs["apify_watch"]) if "apify_watch" in srcs else None,
            "successes": ok,
            "cost_per_success_usd": usd(int(dl_micros / ok)) if ok else None,
        })
    platforms.sort(key=lambda x: -(x["download_cost_usd"] or 0) - (x["watch_cost_usd"] or 0))

    # top users (only days the per-user ledger existed)
    users: Dict[str, int] = {}
    for d in dates:
        dc = compact(d)
        if not (since and dc >= since):
            continue
        for m, s in r.zrange(K_USER.format(day=dc), 0, -1, withscores=True) or []:
            users[m] = users.get(m, 0) + int(s)
    unattr = users.pop(UNATTRIBUTED, 0)
    system = users.pop(SYSTEM, 0)
    top = sorted(users.items(), key=lambda kv: -kv[1])[:10]
    attribution_available = bool(since)

    total_known = sum(sum(s.values()) for s in by_plat.values()) + asr_total
    return {
        "days": days,
        "day_basis": "UTC",
        "dates": dates,
        "per_day": per_day,
        "platforms": platforms,
        "asr_total_usd": usd(asr_total) if attribution_available else None,
        "total_usd": usd(total_known),
        "top_users": [{"user_id": u, "cost_usd": usd(v)} for u, v in top] if attribution_available else None,
        "unattributed": {
            "available": attribution_available,
            "guests_usd": usd(unattr) if attribution_available else None,
            "system_usd": usd(system) if attribution_available else None,
        },
        "ceilings": _ceilings(r, dates),
        "sources": {
            "apify_china": {"available": True},
            "apify_watch": {"available": True},
            "proxy": {"available": proxy_any, "price_per_gb_usd": price,
                      # wording: BA review
                      "note": None if proxy_any else
                      "Chưa đo được: hiện không có chỗ nào ghi số byte đi qua proxy YouTube."},
            "asr": {"available": attribution_available, "since_utc": since,
                    "note": "Ghi từ bản 34C; không gắn với nền tảng tải."},
            "cobalt": {"available": False, "note": "Chưa đo được: Cobalt chạy trên máy riêng, chưa có số chi phí theo lượt."},
            "storage": {"available": False, "note": "Chưa đo được."},
            "host_share": {"available": False, "note": "Chưa đo được."},
        },
        "attribution_since_utc": since,
        "notes": [
            # wording: BA review
            "Chi phí trên mỗi lượt tải thành công = chi phí gắn với lượt tải (Apify lớp Trung Quốc + proxy) / số lượt thành công. "
            "Chi phí quét Theo dõi kênh (apify_watch) tách riêng, không chia vào lượt tải.",
            "Người dùng tốn nhất: theo mã tài khoản (không có email). Khách vãng lai gộp vào 'không gắn được người dùng'; "
            "quét hệ thống (Theo dõi kênh, admin) gộp vào 'hệ thống'.",
        ],
    }


# ── adoption ────────────────────────────────────────────────────────────────

def _parse_ts(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _select(db, table: str, cols: str, *, gte: Optional[Tuple[str, str]] = None,
            eq: Optional[Tuple[str, str]] = None, limit: int = 20000) -> Optional[list]:
    """Rows, or None when the table cannot be read (missing migration, outage)."""
    try:
        q = db.table(table).select(cols)
        if gte:
            q = q.gte(gte[0], gte[1])
        if eq:
            q = q.eq(eq[0], eq[1])
        return q.limit(limit).execute().data or []
    except Exception as exc:  # noqa: BLE001
        logger.info("unit_metrics: %s unreadable: %s", table, type(exc).__name__)
        return None


def _na(note: str) -> dict:
    return {"available": False, "note": note}


def retention(cohort: Dict[str, Any], activity: Dict[str, set], today: Any, k: int) -> dict:
    """D-k return of users registered on each local day. A cohort counts only
    once day d+k is complete (before `today`)."""
    eligible = returned = 0
    for d, users in cohort.items():
        target = d + timedelta(days=k)
        if target >= today:
            continue
        eligible += len(users)
        active = activity.get(target, set())
        returned += sum(1 for u in users if u in active)
    return {"cohort_users": eligible, "returned": returned, "rate": rate(returned, eligible)}


def adoption_report(days: int = 30, now: Optional[datetime] = None, db=None) -> dict:
    from app.core import download_outcomes as do  # noqa: PLC0415
    days = clamp_days(days)
    now = now or _now()
    tz = do.admin_tz()
    today = now.astimezone(tz).date()
    first = today - timedelta(days=days - 1)
    since = do.admin_day_window(first)[0].isoformat()
    if db is None:
        from app.core import database  # noqa: PLC0415
        db = database.get_supabase_client()

    local = lambda ts: ts.astimezone(tz).date()  # noqa: E731
    out: Dict[str, Any] = {"days": days, "day_basis": do.admin_tz_name(), "from": first.isoformat(),
                           "to": today.isoformat()}

    # registrations / day
    profiles = _select(db, "profiles", "id, created_at", gte=("created_at", since))
    cohort: Dict[Any, set] = {}
    if profiles is None:
        out["registrations"] = _na("Không đọc được bảng profiles.")
    else:
        per: Dict[str, int] = {}
        for row in profiles:
            ts = _parse_ts(row.get("created_at"))
            if not ts:
                continue
            d = local(ts)
            per[d.isoformat()] = per.get(d.isoformat(), 0) + 1
            if row.get("id"):
                cohort.setdefault(d, set()).add(str(row["id"]))
        series = [{"date": (first + timedelta(days=i)).isoformat(),
                   "count": per.get((first + timedelta(days=i)).isoformat(), 0)} for i in range(days)]
        out["registrations"] = {"available": True, "total": sum(per.values()), "per_day": series}

    # D1 / D7 return: any signed-in activity (analytics event or saved download)
    activity: Dict[Any, set] = {}
    sources_ok = 0
    for table in ("analytics_events", "download_jobs"):
        rows = _select(db, table, "user_id, created_at", gte=("created_at", since), limit=50000)
        if rows is None:
            continue
        sources_ok += 1
        for row in rows:
            uid, ts = row.get("user_id"), _parse_ts(row.get("created_at"))
            if uid and ts:
                activity.setdefault(local(ts), set()).add(str(uid))
    if profiles is None or not sources_ok:
        out["retention"] = _na("Không đọc được dữ liệu đăng ký hoặc hoạt động.")
    else:
        out["retention"] = {
            "available": True,
            "d1": retention(cohort, activity, today, 1),
            "d7": retention(cohort, activity, today, 7),
            # wording: BA review
            "note": "Quay lại = có sự kiện hoặc lượt tải khi đã đăng nhập vào đúng ngày thứ 1 / thứ 7 sau ngày đăng ký "
                    "(theo giờ Việt Nam). Chỉ tính các nhóm đã qua đủ ngày đó; người dùng không đăng nhập không đo được.",
        }

    # Channel Watch subscriptions per account
    subs = _select(db, "watch_subscriptions", "user_id, status", eq=("status", "active"))
    if subs is None:
        out["watch"] = _na("Bảng Theo dõi kênh chưa có (migration 038).")
    else:
        per_user: Dict[str, int] = {}
        for s in subs:
            if s.get("user_id"):
                per_user[str(s["user_id"])] = per_user.get(str(s["user_id"]), 0) + 1
        vals = sorted(per_user.values())
        out["watch"] = {
            "available": True,
            "accounts": len(vals),
            "active_sources": sum(vals),
            "avg_per_account": round(sum(vals) / len(vals), 2) if vals else None,
            "max_per_account": vals[-1] if vals else None,
            "distribution": {
                "1": sum(1 for v in vals if v == 1),
                "2-5": sum(1 for v in vals if 2 <= v <= 5),
                "6+": sum(1 for v in vals if v >= 6),
            },
        }

    # Inbox ("Hộp thư"): only "mark as read" leaves a trace
    inbox = _select(db, "watch_inbox_state", "user_id, updated_at")
    if inbox is None:
        out["inbox"] = _na("Bảng Hộp thư chưa có (migration 039).")
    else:
        recent = sum(1 for r_ in inbox if (_parse_ts(r_.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc))
                     >= datetime.fromisoformat(since))
        out["inbox"] = {"available": True, "accounts_marked_read": len(inbox), "active_in_period": recent,
                        "note": "Chỉ đếm được tài khoản đã bấm 'đánh dấu đã xem'; lượt mở Hộp thư chưa được ghi."}

    # Windows app machines
    devs = _select(db, "desktop_devices", "client_version, first_seen, last_seen")
    if devs is None:
        out["app_devices"] = _na("Bảng máy App Windows chưa có (migration 037).")
    else:
        since_dt = datetime.fromisoformat(since)
        active = [d for d in devs if (_parse_ts(d.get("last_seen")) or since_dt - timedelta(days=1)) >= since_dt]
        new = [d for d in devs if (_parse_ts(d.get("first_seen")) or since_dt - timedelta(days=1)) >= since_dt]
        versions: Dict[str, int] = {}
        for d in active:
            v = d.get("client_version") or "?"
            versions[v] = versions.get(v, 0) + 1
        out["app_devices"] = {
            "available": True, "total": len(devs), "active_in_period": len(active), "new_in_period": len(new),
            "versions": [{"version": v, "machines": c} for v, c in sorted(versions.items(), key=lambda kv: -kv[1])],
        }

    out["extension_versions"] = _na("Chưa đo được: tiện ích không gửi số phiên bản lên máy chủ.")
    return out


# ── settings ────────────────────────────────────────────────────────────────

def weekly_report_enabled() -> bool:
    try:
        v = _r().get(CONFIG_PREFIX + WEEKLY_FLAG)
    except Exception:  # noqa: BLE001
        return False
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def set_weekly_report_enabled(on: bool) -> bool:
    r = _r()
    r.set(CONFIG_PREFIX + WEEKLY_FLAG, "true" if on else "false")
    r.set(CONFIG_PREFIX + WEEKLY_FLAG + ":desc",
          "Phase 34C: gửi báo cáo đo lường hằng tuần qua Telegram (thứ Hai 08:00 giờ VN)")
    r.sadd(CONFIG_INDEX, WEEKLY_FLAG)
    return on


# ── weekly report ───────────────────────────────────────────────────────────

def _money(v: Optional[float]) -> str:
    return "chưa đo được" if v is None else f"${v:,.4f}" if v < 1 else f"${v:,.2f}"


def _pct(v: Optional[float]) -> str:
    return "chưa có lượt" if v is None else f"{v * 100:.1f}%"


def build_weekly_text(success: dict, costs: dict, *, top_n_platforms: int = 8) -> str:
    """Short Vietnamese Telegram summary (HTML parse mode) from the two reports."""
    # wording: BA review
    lines = [f"📈 <b>VidGrab — Báo cáo tuần</b> ({success['dates'][0]} → {success['dates'][-1]}, ngày UTC)", ""]
    t = success["totals"]
    lines.append(f"<b>Tải thành công:</b> {t['successes']}/{t['attempts']} ({_pct(t['success_rate'])})"
                 + (f", p50 {t['p50_label']}, p95 {t['p95_label']}" if t.get("p50_label") else ""))
    for row in success["platforms"][:top_n_platforms]:
        c = row["totals"]
        lines.append(f"  • {row['platform']}: {_pct(c['success_rate'])} ({c['successes']}/{c['attempts']})")
    lines.append("")
    lines.append(f"<b>Chi phí đo được:</b> {_money(costs['total_usd'])}")
    for p in costs["platforms"]:
        if not p["download_cost_usd"] and not p["watch_cost_usd"]:
            continue
        parts = []
        if p["download_cost_usd"]:
            parts.append(f"{_money(p['download_cost_usd'])}, mỗi lượt thành công {_money(p['cost_per_success_usd'])}")
        if p["watch_cost_usd"]:
            parts.append(f"Theo dõi kênh {_money(p['watch_cost_usd'])}")
        lines.append(f"  • {p['platform']}: " + "; ".join(parts))
    if costs.get("asr_total_usd") is not None:
        lines.append(f"  • phiên âm: {_money(costs['asr_total_usd'])}")
    if costs.get("top_users"):
        lines.append("")
        lines.append("<b>Tốn nhất (mã tài khoản):</b>")
        for u in costs["top_users"][:5]:
            lines.append(f"  • {u['user_id'][:8]}…: {_money(u['cost_usd'])}")
    un = costs.get("unattributed") or {}
    if un.get("available"):
        lines.append(f"  • khách vãng lai: {_money(un['guests_usd'])} · hệ thống: {_money(un['system_usd'])}")
    used = [c for c in costs.get("ceilings", []) if c.get("available") and c.get("daily_ceiling_usd")]
    if used:
        lines.append("")
        lines.append("<b>Mức dùng trần ngày</b> (TB / cao nhất):")
        for c in used:
            lines.append(f"  • {c['provider']}: {c['avg_pct_of_ceiling']}% / {c['peak_pct_of_ceiling']}% "
                         f"của {_money(c['daily_ceiling_usd'])}")
    missing = [k for k, v in (costs.get("sources") or {}).items() if not v.get("available")]
    if missing:
        lines.append("")
        lines.append("<i>Chưa đo được: " + ", ".join(missing) + "</i>")
    return "\n".join(lines)


def iso_week(now: Optional[datetime] = None) -> str:
    y, w, _ = (now or _now()).isocalendar()
    return f"{y}-W{w:02d}"


def send_weekly_report(*, force: bool = False, now: Optional[datetime] = None) -> dict:
    """Compose and send the weekly report. force=False (beat): only when the
    admin flag is on, once per ISO week. force=True (manual admin button):
    always. Returns {sent, skipped?, text?}."""
    now = now or _now()
    if not force:
        if not weekly_report_enabled():
            return {"sent": False, "skipped": "disabled"}
        try:
            if not _r().set(f"metrics:weekly_sent:{iso_week(now)}", "1", nx=True, ex=8 * 86400):
                return {"sent": False, "skipped": "already_sent"}
        except Exception:  # noqa: BLE001
            return {"sent": False, "skipped": "redis_unavailable"}
    text = build_weekly_text(success_report(7, now), cost_report(7, now))
    try:
        from app.core.notifications import send_telegram_message_sync  # noqa: PLC0415
        ok = bool(send_telegram_message_sync(text))
    except Exception as exc:  # noqa: BLE001
        logger.warning("weekly metrics report send failed: %s", type(exc).__name__)
        ok = False
    return {"sent": ok, "text": text}
