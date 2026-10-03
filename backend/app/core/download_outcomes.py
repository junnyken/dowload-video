"""
Download outcome counters — the one place every download attempt is counted.

Why this exists
---------------
The admin dashboard used to count downloads from the ``download_jobs`` table.
The main download path, POST /fetch-link, writes a row there only for a
signed-in user's SUCCESS — never for a guest, never for a failure — so the
dashboard read "0 jobs" on days with plenty of real traffic, while the anomaly
detector (which reads the Redis hash below) correctly saw TikTok failing.

The Redis hash ``vidgrab:stats:{YYYY-MM-DD}`` (fields ``{platform}:ok`` /
``{platform}:err``) was already written by every download path — /fetch-link
(TikWM, Cobalt, yt-dlp alike, since they all run inside it), /fetch-threads
and the Celery worker that serves bulk, scheduled and retried jobs. It is the
only source that sees every attempt exactly once, so the admin metrics now
read it too, and the two views can no longer disagree.

This module adds three siblings to it, written in the same pipeline:

  vidgrab:stats_hourly:{YYYY-MM-DDTHH}  hash  {platform}:ok / {platform}:err
        hour buckets (UTC hours) for "last 24h" / "last 1h" windows and
        for the admin's Vietnam-time calendar days              TTL 35d
  vidgrab:errcodes:{YYYY-MM-DD}         hash  {platform}|{error_code} → count
        classified failure reasons for "top errors"            TTL 35d
  vidgrab:errcodes_hourly:{YYYY-MM-DDTHH} hash {platform}|{error_code} → count
        the same codes per hour, so "errors in the last 24h" can be
        broken down without borrowing whole days                TTL 35d
  vidgrab:stats_hourly:since            string  first hour the two hourly
        families above were written together (set once, NX). Still written,
        but coverage is no longer read from it: see ``hourly_coverage``.
  p27a:ts:{platform}:success / :failure string ISO timestamp
        last success / failure (same key obs_recorder writes)  TTL 90d

The day hash TTL is raised from 8 to 35 days so the 30-day analytics view has
data. The anomaly detector only ever reads the last 7 days, so a longer TTL
does not change its behaviour.

The hourly TTLs were 50h; they are 35 days so the admin can build every day of
its 30-day view from whole Vietnam-time days (see ``admin_day_window``). Cost:
at most 35×24 = 840 live keys per hourly family, each holding one field per
platform (≈ 2×12 counter fields, plus one per platform|error_code pair in the
error-code family) — well under 1 MB of Redis in total.

Day boundaries. The write-side day hash ``vidgrab:stats:{date}`` keeps its
UTC-day meaning: user quotas, the anomaly detector's day comparisons and
billing are not admin views and are not touched here. Only the admin read
helpers below (``admin_day_window`` / ``read_admin_days``) use the admin's
calendar day (ADMIN_TIMEZONE, default Asia/Ho_Chi_Minh), summed from hourly
buckets.

No URL, user id or IP is stored — only platform, outcome and a short error
code, so nothing here needs redaction.

Fail-soft contract
------------------
``record_async`` never raises and never blocks the caller on Redis: it hands
the write to one background thread. ``record`` is the synchronous form for
callers that are already off the request path (Celery workers).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DAY_KEY_TPL = "vidgrab:stats:{date}"
HOUR_KEY_TPL = "vidgrab:stats_hourly:{hour}"
ERRCODE_KEY_TPL = "vidgrab:errcodes:{date}"
LAST_TS_KEY_TPL = "p27a:ts:{platform}:{kind}"   # shared with obs_recorder
ERRCODE_HOUR_KEY_TPL = "vidgrab:errcodes_hourly:{hour}"
HOURLY_SINCE_KEY = "vidgrab:stats_hourly:since"

DAY_TTL = 35 * 86400
# 35 days, not 50h: the admin's 30-day view is built from VN-time days summed
# from hourly buckets, which therefore must outlive the 30 days (+7h offset).
HOUR_TTL = 35 * 86400
LAST_TS_TTL = 90 * 86400
SINCE_TTL = 400 * 86400

_SLUG_RE = re.compile(r"[^a-z0-9_]+")
_MAX_PENDING = 2000


def _redis():
    from app.core.redis_client import get_redis
    return get_redis()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: Optional[str], default: str) -> str:
    s = _SLUG_RE.sub("_", (value or "").strip().lower()).strip("_")[:40]
    return s or default


def day_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def hour_str(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


# ── Write path ───────────────────────────────────────────────────────────────

def record(platform: str, success: bool, error_code: Optional[str] = None,
           *, now: Optional[datetime] = None) -> None:
    """Count one finished download attempt. Synchronous, never raises."""
    try:
        now = now or _now()
        plat = _slug(platform, "other")
        field = f"{plat}:{'ok' if success else 'err'}"
        day_key = DAY_KEY_TPL.format(date=day_str(now))
        hour_key = HOUR_KEY_TPL.format(hour=hour_str(now))

        pipe = _redis().pipeline(transaction=False)
        pipe.hincrby(day_key, field, 1)
        pipe.expire(day_key, DAY_TTL)
        pipe.hincrby(hour_key, field, 1)
        pipe.expire(hour_key, HOUR_TTL)
        pipe.set(HOURLY_SINCE_KEY, hour_str(now), nx=True, ex=SINCE_TTL)
        if success:
            pipe.set(LAST_TS_KEY_TPL.format(platform=plat, kind="success"),
                     now.isoformat(), ex=LAST_TS_TTL)
        else:
            code = _slug(error_code, "unknown")
            err_key = ERRCODE_KEY_TPL.format(date=day_str(now))
            pipe.hincrby(err_key, f"{plat}|{code}", 1)
            pipe.expire(err_key, DAY_TTL)
            err_hour_key = ERRCODE_HOUR_KEY_TPL.format(hour=hour_str(now))
            pipe.hincrby(err_hour_key, f"{plat}|{code}", 1)
            pipe.expire(err_hour_key, HOUR_TTL)
            pipe.set(LAST_TS_KEY_TPL.format(platform=plat, kind="failure"),
                     now.isoformat(), ex=LAST_TS_TTL)
        pipe.execute()
    except Exception as exc:  # metrics are best-effort
        log.debug("[download_outcomes] record failed: %s", exc)


_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_pending = 0
_pending_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dl-outcome")
        return _executor


def _run(platform, success, error_code, now):
    global _pending
    try:
        record(platform, success, error_code, now=now)
    finally:
        with _pending_lock:
            _pending -= 1


def record_async(platform: str, success: bool, error_code: Optional[str] = None) -> None:
    """Queue ``record`` on a background thread. Never raises, never blocks on Redis.

    The timestamp is taken now, so a slow queue still lands the count in the
    right hour/day. If Redis is hung and the queue backs up past _MAX_PENDING
    the count is dropped rather than letting memory grow.
    """
    global _pending
    try:
        with _pending_lock:
            if _pending >= _MAX_PENDING:
                return
            _pending += 1
        try:
            _get_executor().submit(_run, platform, success, error_code, _now())
        except Exception:
            with _pending_lock:
                _pending -= 1
            raise
    except Exception as exc:
        log.debug("[download_outcomes] record_async failed: %s", exc)


def flush(timeout: float = 5.0) -> None:
    """Wait until every queued write has run (tests / graceful shutdown)."""
    try:
        _get_executor().submit(lambda: None).result(timeout=timeout)
    except Exception:
        pass


# ── Read path ────────────────────────────────────────────────────────────────

def _decode(v) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _parse_okerr(raw: dict) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for k, v in (raw or {}).items():
        key = _decode(k)
        if ":" not in key:
            continue
        plat, kind = key.split(":", 1)
        if kind not in ("ok", "err"):
            continue
        try:
            n = int(_decode(v))
        except Exception:
            continue
        slot = out.setdefault(plat, {"ok": 0, "err": 0})
        slot[kind] += n
    return out


def read_days(dates: Iterable[str]) -> Dict[str, Optional[Dict[str, Dict[str, int]]]]:
    """{date: {platform: {ok, err}}} — None for a day whose hash does not exist
    (no data recorded / expired), {} is never returned for a missing key so a
    caller can tell "no traffic" from "no data"."""
    dates = list(dates)
    rc = _redis()
    pipe = rc.pipeline(transaction=False)
    for d in dates:
        pipe.hgetall(DAY_KEY_TPL.format(date=d))
    raws = pipe.execute()
    out: Dict[str, Optional[Dict[str, Dict[str, int]]]] = {}
    for d, raw in zip(dates, raws):
        out[d] = _parse_okerr(raw) if raw else None
    return out


def read_hours(hours: int = 24, *, now: Optional[datetime] = None
               ) -> List[Tuple[str, Dict[str, Dict[str, int]]]]:
    """[(hour, {platform: {ok, err}})], oldest first; the last item is the
    current, partial hour."""
    now = now or _now()
    keys = [hour_str(now - timedelta(hours=h)) for h in range(hours - 1, -1, -1)]
    pipe = _redis().pipeline(transaction=False)
    for h in keys:
        pipe.hgetall(HOUR_KEY_TPL.format(hour=h))
    return [(h, _parse_okerr(raw)) for h, raw in zip(keys, pipe.execute())]


def sum_platforms(per_platform: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    ok = sum(v.get("ok", 0) for v in per_platform.values())
    err = sum(v.get("err", 0) for v in per_platform.values())
    return {"ok": ok, "err": err, "total": ok + err}


def merge(into: Dict[str, Dict[str, int]], other: Dict[str, Dict[str, int]]) -> None:
    for plat, c in (other or {}).items():
        slot = into.setdefault(plat, {"ok": 0, "err": 0})
        slot["ok"] += c.get("ok", 0)
        slot["err"] += c.get("err", 0)


def window_24h(*, now: Optional[datetime] = None) -> Dict[str, Dict[str, int]]:
    """Per-platform counts for the current hour plus the 23 before it."""
    agg: Dict[str, Dict[str, int]] = {}
    for _h, counts in read_hours(24, now=now):
        merge(agg, counts)
    return agg


def window_1h(*, now: Optional[datetime] = None) -> Dict[str, Dict[str, int]]:
    """Sliding last-60-minutes estimate: the current hour bucket plus the
    previous one weighted by the share of it still inside the window."""
    now = now or _now()
    (_p, prev), (_c, cur) = read_hours(2, now=now)
    weight = 1.0 - (now.minute * 60 + now.second) / 3600.0
    agg: Dict[str, Dict[str, int]] = {}
    for plat in set(prev) | set(cur):
        p = prev.get(plat, {"ok": 0, "err": 0})
        c = cur.get(plat, {"ok": 0, "err": 0})
        agg[plat] = {
            "ok": c["ok"] + int(round(p["ok"] * weight)),
            "err": c["err"] + int(round(p["err"] * weight)),
        }
    return agg


def read_error_codes(dates: Iterable[str]) -> Dict[Tuple[str, str], int]:
    """{(platform, error_code): count} summed over the given days."""
    pipe = _redis().pipeline(transaction=False)
    dates = list(dates)
    for d in dates:
        pipe.hgetall(ERRCODE_KEY_TPL.format(date=d))
    out: Dict[Tuple[str, str], int] = {}
    for raw in pipe.execute():
        for k, v in (raw or {}).items():
            key = _decode(k)
            if "|" not in key:
                continue
            plat, code = key.split("|", 1)
            try:
                out[(plat, code)] = out.get((plat, code), 0) + int(_decode(v))
            except Exception:
                continue
    return out


def top_errors(dates: Iterable[str], limit: int = 10) -> List[dict]:
    """[{error_code, count, platforms: {platform: n}}] most frequent first."""
    by_code: Dict[str, Dict[str, int]] = {}
    for (plat, code), n in read_error_codes(dates).items():
        by_code.setdefault(code, {})[plat] = by_code.get(code, {}).get(plat, 0) + n
    rows = [{"error_code": c, "count": sum(p.values()), "platforms": p}
            for c, p in by_code.items()]
    rows.sort(key=lambda r: (-r["count"], r["error_code"]))
    return rows[:limit]


def last_timestamp(platform: str, kind: str = "success") -> Optional[str]:
    try:
        raw = _redis().get(LAST_TS_KEY_TPL.format(platform=platform, kind=kind))
        return _decode(raw) if raw else None
    except Exception:
        return None


def success_rate_pct(ok: int, total: int) -> Optional[float]:
    """Percent with one decimal, or None when there were no attempts."""
    return round(ok / total * 100, 1) if total > 0 else None


# ── 24h window: ONE helper for every "last 24h" admin number ─────────────────

def _parse_hour(h: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.strptime(h, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if h else None
    except Exception:
        return None


def _parse_codes(raw: dict) -> Dict[Tuple[str, str], int]:
    out: Dict[Tuple[str, str], int] = {}
    for k, v in (raw or {}).items():
        key = _decode(k)
        if "|" not in key:
            continue
        plat, code = key.split("|", 1)
        try:
            out[(plat, code)] = out.get((plat, code), 0) + int(_decode(v))
        except Exception:
            continue
    return out


def _fold_top(codes: Dict[Tuple[str, str], int], limit: int) -> List[dict]:
    by_code: Dict[str, Dict[str, int]] = {}
    for (plat, code), n in codes.items():
        by_code.setdefault(code, {})[plat] = by_code.get(code, {}).get(plat, 0) + n
    rows = [{"error_code": c, "count": sum(p.values()), "platforms": p}
            for c, p in by_code.items()]
    rows.sort(key=lambda r: (-r["count"], r["error_code"]))
    return rows[:limit]


def window_summary(hours: int = 24, *, now: Optional[datetime] = None,
                   jobs_loader=None, fallback_when_redis_down: bool = False,
                   top_limit: int = 10, coverage: Optional[dict] = None) -> dict:
    """The single source for every admin "last N hours" error / failure number.

    Reads the hourly buckets (counts) and hourly error-code buckets (reasons)
    of the outcome store, so ``err`` always equals the sum of the error codes
    (failures in an hour written before the error-code buckets existed are
    reported under the code ``unclassified``). Hourly data only exists since
    the first build that wrote it (see ``hourly_coverage``); earlier hours are
    NOT silently counted as zero:

      * ``partial`` / ``coverage_hours`` / ``covered_since`` say how much of
        the window the buckets cover (from the first COMPLETE hour);
      * for an uncovered hour whose UTC DAY has no counter hash at all, the
        ``jobs_loader`` (download_jobs) fills in — never for a day the store
        knows, so nothing is counted twice; ``sources`` lists what was used.

    ``jobs_loader(start_iso, end_iso)`` returns rows
    ``{status, platform, error_code, created_at}`` (platform already resolved).
    If Redis cannot be read: ``redis_ok`` False and, unless
    ``fallback_when_redis_down``, every count is None (unknown is not zero).
    """
    now = now or _now()
    keys = [hour_str(now - timedelta(hours=h)) for h in range(hours - 1, -1, -1)]
    first_hour = _parse_hour(keys[0])
    out: dict = {
        "hours": hours, "redis_ok": True,
        "window_start": first_hour.isoformat(), "window_end": now.isoformat(),
        "coverage_hours": 0, "partial": True, "covered_since": None,
        "sources": [], "ok": None, "err": None, "total": None,
        "success_rate": None, "fail_rate": None,
        "by_platform": {}, "top_errors": [],
    }
    agg: Dict[str, Dict[str, int]] = {}
    codes: Dict[Tuple[str, str], int] = {}
    counted_from: Optional[datetime] = None   # first hour whose bucket is read
    covered: Optional[datetime] = None        # first COMPLETE hour
    day_known: Dict[str, bool] = {}
    try:
        cov = coverage if coverage is not None else hourly_coverage(now=now)
        rc = _redis()
        pipe = rc.pipeline(transaction=False)
        for h in keys:
            pipe.hgetall(HOUR_KEY_TPL.format(hour=h))
        for h in keys:
            pipe.hgetall(ERRCODE_HOUR_KEY_TPL.format(hour=h))
        res = pipe.execute()
        counters = res[:hours]
        errcodes = res[hours:]
        if cov["first_bucket"] is not None:
            counted_from = max(cov["first_bucket"], first_hour)
            covered = max(cov["covered_since"], first_hour)
            for h, raw, craw in zip(keys, counters, errcodes):
                if _parse_hour(h) < counted_from:
                    continue
                per_plat = _parse_okerr(raw)
                merge(agg, per_plat)
                _add_codes_with_remainder(codes, per_plat, _parse_codes(craw))
            out["sources"].append("redis_hourly")
    except Exception as exc:
        log.debug("[download_outcomes] window_summary redis failed: %s", exc)
        out["redis_ok"] = False
        agg, codes, counted_from, covered = {}, {}, None, None
        if not fallback_when_redis_down:
            return out

    cur_hour = _parse_hour(keys[-1])
    if covered is not None:
        out["covered_since"] = covered.isoformat()
        out["coverage_hours"] = max(0, int((cur_hour - covered).total_seconds() // 3600) + 1)
    out["partial"] = out["coverage_hours"] < hours
    since = counted_from

    # Uncovered hours: fill from download_jobs, only for UTC days the store has no hash for.
    gap_end = since if since is not None else cur_hour + timedelta(hours=1)
    if jobs_loader is not None and gap_end > first_hour:
        gap_days = sorted({(first_hour + timedelta(hours=i)).strftime("%Y-%m-%d")
                           for i in range(int((gap_end - first_hour).total_seconds() // 3600))})
        if out["redis_ok"]:
            try:
                known = read_days(gap_days)
                day_known = {d: known.get(d) is not None for d in gap_days}
            except Exception:
                day_known = {d: True for d in gap_days}   # cannot tell → do not double count
        else:
            day_known = {d: False for d in gap_days}
        if not all(day_known.values()):
            try:
                rows = jobs_loader(first_hour.isoformat(), gap_end.isoformat()) or []
            except Exception as exc:
                log.debug("[download_outcomes] jobs fallback failed: %s", exc)
                rows = []
            used = False
            for r in rows:
                created = (r.get("created_at") or "")
                if day_known.get(created[:10], True):
                    continue   # that day is the store's, or not in the gap
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if not (first_hour <= ts < gap_end):
                        continue   # inside a covered hour: the store has it
                except Exception:
                    continue
                st = r.get("status")
                if st not in ("success", "failed"):
                    continue
                plat = r.get("platform") or "other"
                slot = agg.setdefault(plat, {"ok": 0, "err": 0})
                if st == "success":
                    slot["ok"] += 1
                else:
                    slot["err"] += 1
                    k = (plat, r.get("error_code") or "unknown")
                    codes[k] = codes.get(k, 0) + 1
                used = True
            if used:
                out["sources"].append("jobs_table")

    t = sum_platforms(agg)
    out.update({
        "ok": t["ok"], "err": t["err"], "total": t["total"],
        "success_rate": success_rate_pct(t["ok"], t["total"]),
        "fail_rate": round(t["err"] / t["total"] * 100, 1) if t["total"] > 0 else None,
        "by_platform": agg,
        "top_errors": _fold_top(codes, top_limit),
    })
    return out


# ── Hourly coverage: derived from the buckets themselves ─────────────────────

UNCLASSIFIED = "unclassified"
COVERAGE_SCAN_HOURS = HOUR_TTL // 3600 + 1


def _hour_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hourly_coverage(*, now: Optional[datetime] = None) -> dict:
    """Where the hourly buckets start: ``{first_bucket, covered_since}``.

    Scans every hour the buckets can still be alive for (HOUR_TTL, bounded:
    one pipelined EXISTS per hour) and takes the earliest one that exists.
    That hour is the first the writer saw — normally the deploy hour, written
    for only part of the hour — so it is read as data but coverage starts at
    the NEXT hour, the first complete one. Hours after it with no bucket had
    no traffic (the writer was running). Buckets expire oldest-first, so the
    rule still holds once old hours fall off the TTL (it then under-claims by
    one hour at the far edge, never over-claims).

    The ``stats_hourly:since`` marker is not used: it was introduced after the
    hourly buckets (live 2026-10-03: marker 01:00Z, buckets since ~14:00Z the
    day before), and a marker cannot tell when the hours after it have expired.

    Raises if Redis cannot be read (callers decide what unknown means).
    """
    now_hour = _hour_floor(now or _now())
    hours = [now_hour - timedelta(hours=i) for i in range(COVERAGE_SCAN_HOURS - 1, -1, -1)]
    pipe = _redis().pipeline(transaction=False)
    for h in hours:
        pipe.exists(HOUR_KEY_TPL.format(hour=hour_str(h)))
    first = next((h for h, e in zip(hours, pipe.execute()) if e), None)
    return {"first_bucket": first,
            "covered_since": first + timedelta(hours=1) if first is not None else None}


def _add_codes_with_remainder(codes: Dict[Tuple[str, str], int],
                              per_plat: Dict[str, Dict[str, int]],
                              hour_codes: Dict[Tuple[str, str], int]) -> None:
    """Add one hour's error codes; failures the hour has no code for (written
    before the error-code buckets existed) go to ``unclassified`` so the codes
    always add up to the failures."""
    coded: Dict[str, int] = {}
    for (plat, code), n in hour_codes.items():
        codes[(plat, code)] = codes.get((plat, code), 0) + n
        coded[plat] = coded.get(plat, 0) + n
    for plat, c in per_plat.items():
        rest = c.get("err", 0) - coded.get(plat, 0)
        if rest > 0:
            codes[(plat, UNCLASSIFIED)] = codes.get((plat, UNCLASSIFIED), 0) + rest


# ── Admin calendar days (Vietnam time) ───────────────────────────────────────
#
# ADMIN ONLY. User quotas / daily limits (quotas.py, entitlements.py,
# yt_quota), the anomaly detector's day comparisons and billing keep their own
# (UTC) days on purpose: they are enforcement / detection keys whose meaning
# must not shift under running counters. Only what the admin DISPLAYS as
# "today" / per-day uses the helpers below.

ADMIN_TIMEZONE_DEFAULT = "Asia/Ho_Chi_Minh"


def admin_tz_name() -> str:
    return (os.getenv("ADMIN_TIMEZONE") or "").strip() or ADMIN_TIMEZONE_DEFAULT


@lru_cache(maxsize=8)
def _zone(name: str):
    try:
        return ZoneInfo(name)
    except Exception as exc:
        log.warning("[download_outcomes] ADMIN_TIMEZONE %r unusable (%s); using %s",
                    name, exc, ADMIN_TIMEZONE_DEFAULT)
        return ZoneInfo(ADMIN_TIMEZONE_DEFAULT)


def admin_tz():
    return _zone(admin_tz_name())


def admin_today(now: Optional[datetime] = None) -> date:
    """The admin's calendar date right now (aware datetimes only)."""
    return (now or _now()).astimezone(admin_tz()).date()


def admin_day_window(date_local: date) -> Tuple[datetime, datetime]:
    """THE admin day boundary: (start_utc, end_utc), end exclusive, for one
    calendar day in ADMIN_TIMEZONE. For Asia/Ho_Chi_Minh (UTC+7, no DST) the
    day D is [D-1 17:00Z, D 17:00Z)."""
    tz = admin_tz()
    start = datetime.combine(date_local, time(0), tzinfo=tz)
    end = datetime.combine(date_local + timedelta(days=1), time(0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _hours_between(start: datetime, end: datetime) -> List[datetime]:
    out, h = [], start
    while h < end:
        out.append(h)
        h += timedelta(hours=1)
    return out


def _sub_okerr(base: Dict[str, Dict[str, int]], minus: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    out = {p: dict(c) for p, c in base.items()}
    for p, c in minus.items():
        slot = out.setdefault(p, {"ok": 0, "err": 0})
        slot["ok"] = max(0, slot["ok"] - c.get("ok", 0))
        slot["err"] = max(0, slot["err"] - c.get("err", 0))
    return out


def read_admin_days(dates: Iterable[date], *, now: Optional[datetime] = None,
                    coverage: Optional[dict] = None) -> dict:
    """Per admin-calendar-day counts and error codes, oldest first.

    A day is EXACT (``source="hourly"``) when the hourly buckets cover the
    whole day: its 24 UTC hours (up to the current hour for today) are summed.
    Any other day is APPROXIMATE (``source="utc_day"``, ``approximate=True``):
    the UTC-day hash with the same date label is used, minus every hour that an
    exact day in the same request already counts — so a multi-day sum is a sum
    of disjoint intervals and nothing is counted twice. ``counted_start`` /
    ``counted_end`` give the interval a day's numbers really cover. A day with
    neither (``source=None``) is left to the caller (download_jobs) for that
    same counted interval.

    Raises if Redis cannot be read.
    """
    now = now or _now()
    dates = sorted(set(dates))
    cov = coverage if coverage is not None else hourly_coverage(now=now)
    covered = cov["covered_since"]
    now_hour = _hour_floor(now)

    plan = []
    exact_hours: set = set()
    for d in dates:
        start, end = admin_day_window(d)
        aligned = start == _hour_floor(start) and end == _hour_floor(end)
        exact = bool(covered is not None and aligned and start >= covered)
        hours = _hours_between(start, min(end, now_hour + timedelta(hours=1))) if exact else []
        exact_hours.update(hours)
        plan.append((d, start, end, exact, hours))

    rc = _redis()
    pipe = rc.pipeline(transaction=False)
    reads: List[Tuple[str, object]] = []
    for d, start, end, exact, hours in plan:
        if exact:
            for h in hours:
                pipe.hgetall(HOUR_KEY_TPL.format(hour=hour_str(h)))
                pipe.hgetall(ERRCODE_HOUR_KEY_TPL.format(hour=hour_str(h)))
            continue
        ds = d.isoformat()
        pipe.hgetall(DAY_KEY_TPL.format(date=ds))
        pipe.hgetall(ERRCODE_KEY_TPL.format(date=ds))
        utc_start = datetime.combine(d, time(0), tzinfo=timezone.utc)
        overlap = [h for h in _hours_between(utc_start, utc_start + timedelta(days=1))
                   if h in exact_hours]
        for h in overlap:
            pipe.hgetall(HOUR_KEY_TPL.format(hour=hour_str(h)))
            pipe.hgetall(ERRCODE_HOUR_KEY_TPL.format(hour=hour_str(h)))
    res = iter(pipe.execute())

    days = []
    for d, start, end, exact, hours in plan:
        row = {
            "date": d.isoformat(),
            "window_start": start.isoformat(), "window_end": end.isoformat(),
            "source": None, "approximate": not exact,
            "counted_start": None, "counted_end": None,
            "per_platform": {}, "codes": {},
        }
        if exact:
            per: Dict[str, Dict[str, int]] = {}
            codes: Dict[Tuple[str, str], int] = {}
            for _h in hours:
                hp = _parse_okerr(next(res))
                merge(per, hp)
                _add_codes_with_remainder(codes, hp, _parse_codes(next(res)))
            row.update(source="hourly", per_platform=per, codes=codes,
                       counted_start=start.isoformat(),
                       counted_end=min(end, now).isoformat())
        else:
            day_raw = next(res)
            day_codes = _parse_codes(next(res))
            utc_start = datetime.combine(d, time(0), tzinfo=timezone.utc)
            utc_end = utc_start + timedelta(days=1)
            overlap = [h for h in _hours_between(utc_start, utc_end) if h in exact_hours]
            minus: Dict[str, Dict[str, int]] = {}
            minus_codes: Dict[Tuple[str, str], int] = {}
            for _h in overlap:
                hp = _parse_okerr(next(res))
                merge(minus, hp)
                for k, n in _parse_codes(next(res)).items():
                    minus_codes[k] = minus_codes.get(k, 0) + n
            # exact days are always the most recent ones, so the overlap is a
            # tail of the UTC day: what is left is [00:00Z, first overlap hour)
            cut = min(overlap) if overlap else utc_end
            row["counted_start"] = utc_start.isoformat()
            row["counted_end"] = min(cut, now).isoformat()
            if day_raw:
                row["source"] = "utc_day"
                row["per_platform"] = _sub_okerr(_parse_okerr(day_raw), minus)
                row["codes"] = {k: max(0, n - minus_codes.get(k, 0)) for k, n in day_codes.items()
                                if n - minus_codes.get(k, 0) > 0}
        days.append(row)

    return {
        "timezone": admin_tz_name(),
        "covered_since": covered.isoformat() if covered is not None else None,
        "days": days,
    }


def fold_codes(codes: Dict[Tuple[str, str], int], limit: int = 10) -> List[dict]:
    """[{error_code, count, platforms}] most frequent first (public _fold_top)."""
    return _fold_top(codes, limit)
