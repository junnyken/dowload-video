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
        hour buckets for "last 24h" / "last 1h" windows        TTL 50h
  vidgrab:errcodes:{YYYY-MM-DD}         hash  {platform}|{error_code} → count
        classified failure reasons for "top errors"            TTL 35d
  vidgrab:errcodes_hourly:{YYYY-MM-DDTHH} hash {platform}|{error_code} → count
        the same codes per hour, so "errors in the last 24h" can be
        broken down without borrowing whole days                TTL 50h
  vidgrab:stats_hourly:since            string  first hour the two hourly
        families above were written together (set once, NX). Hourly data
        does not exist before it, which the 24h window reports as partial.
  p27a:ts:{platform}:success / :failure string ISO timestamp
        last success / failure (same key obs_recorder writes)  TTL 90d

The day hash TTL is raised from 8 to 35 days so the 30-day analytics view has
data. The anomaly detector only ever reads the last 7 days, so a longer TTL
does not change its behaviour.

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
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

log = logging.getLogger(__name__)

DAY_KEY_TPL = "vidgrab:stats:{date}"
HOUR_KEY_TPL = "vidgrab:stats_hourly:{hour}"
ERRCODE_KEY_TPL = "vidgrab:errcodes:{date}"
LAST_TS_KEY_TPL = "p27a:ts:{platform}:{kind}"   # shared with obs_recorder
ERRCODE_HOUR_KEY_TPL = "vidgrab:errcodes_hourly:{hour}"
HOURLY_SINCE_KEY = "vidgrab:stats_hourly:since"

DAY_TTL = 35 * 86400
HOUR_TTL = 50 * 3600
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
                   top_limit: int = 10) -> dict:
    """The single source for every admin "last N hours" error / failure number.

    Reads the hourly buckets (counts) and hourly error-code buckets (reasons)
    of the outcome store, so ``err`` always equals the sum of the error codes.
    Hourly data only exists since HOURLY_SINCE_KEY (the first deploy that wrote
    it); earlier hours are NOT silently counted as zero:

      * ``partial`` / ``coverage_hours`` / ``covered_since`` say how much of
        the window the buckets cover;
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
    since: Optional[datetime] = None
    day_known: Dict[str, bool] = {}
    try:
        rc = _redis()
        pipe = rc.pipeline(transaction=False)
        pipe.get(HOURLY_SINCE_KEY)
        for h in keys:
            pipe.hgetall(HOUR_KEY_TPL.format(hour=h))
        for h in keys:
            pipe.hgetall(ERRCODE_HOUR_KEY_TPL.format(hour=h))
        res = pipe.execute()
        marker = _parse_hour(_decode(res[0]) if res[0] else None)
        counters = res[1:1 + hours]
        errcodes = res[1 + hours:]
        if marker is None:
            # Only pre-marker data exists (older build): trust the first
            # non-empty bucket, but there are no hourly error codes yet.
            for h, raw in zip(keys, counters):
                if raw:
                    marker = _parse_hour(h)
                    break
        if marker is not None:
            since = max(marker, first_hour)
            for h, raw, craw in zip(keys, counters, errcodes):
                if _parse_hour(h) < since:
                    continue
                merge(agg, _parse_okerr(raw))
                for k, n in _parse_codes(craw).items():
                    codes[k] = codes.get(k, 0) + n
            out["sources"].append("redis_hourly")
    except Exception as exc:
        log.debug("[download_outcomes] window_summary redis failed: %s", exc)
        out["redis_ok"] = False
        agg, codes, since = {}, {}, None
        if not fallback_when_redis_down:
            return out

    cur_hour = _parse_hour(keys[-1])
    if since is not None:
        out["covered_since"] = since.isoformat()
        out["coverage_hours"] = int((cur_hour - since).total_seconds() // 3600) + 1
    out["partial"] = out["coverage_hours"] < hours

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
