"""
Capacity — the concurrent-download ceiling, and how close we run to it.
======================================================================
Added 2026-10-19.

Setting
-------
/fetch-link runs at most N downloads at once (a Redis slot counter in
app.api.routes); the next one gets 429 concurrency_limit. N came only from the
env (MAX_CONCURRENT_DOWNLOADS, 6 on production), so changing it meant a
redeploy. Now the admin sets it (Redis `settings:max_concurrent_downloads`),
the env stays the fallback. Read per request with a 10 s in-process cache.

Telemetry
---------
Nobody could say whether the service had ever been overloaded: nothing
recorded concurrency, refusals or CPU over time. Now:
  * every slot taken bumps `cap:cur:acquired` and raises `cap:cur:peakz` to
    the number of slots in use (atomic max: ZADD GT, Redis >= 6.2);
  * every refusal bumps `cap:cur:refused`;
  * `sample()` — Celery beat, every minute — moves those counters into one
    record per minute together with the container's CPU % and memory %
    (cgroup v2, v1 fallback), the Celery downloads queue and the limit,
    and appends it to the list `cap:minutes` (7 days kept).
`series()` folds the minutes into 5-minute (24 h view) or hourly (7 d view)
points for the admin chart. Everything fails open and never raises.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

SETTING_KEY = "settings:max_concurrent_downloads"
HARD_MIN, HARD_MAX = 1, 50
_CACHE_S = 10.0
_cache: Dict[str, float] = {"value": 0.0, "at": 0.0}

CUR_PEAK = "cap:cur:peakz"          # zset, member "p": ZADD GT = atomic max
CUR_ACQ = "cap:cur:acquired"
CUR_REF = "cap:cur:refused"
MINUTES = "cap:minutes"
MINUTES_KEEP = 7 * 24 * 60
LAST_CPU = "cap:last_cpu"           # json {usage_usec, at}



def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def env_default() -> int:
    try:
        return max(HARD_MIN, min(HARD_MAX, int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "10"))))
    except ValueError:
        return 10


# ── setting ───────────────────────────────────────────────────────────

def get_max_concurrent() -> int:
    now = time.monotonic()
    if _cache["at"] and now - _cache["at"] < _CACHE_S:
        return int(_cache["value"])
    value = env_default()
    try:
        raw = _r().get(SETTING_KEY)
        if raw:
            value = max(HARD_MIN, min(HARD_MAX, int(raw)))
    except Exception:
        pass
    _cache.update(value=float(value), at=now)
    return value


def set_max_concurrent(n: Optional[int]) -> int:
    """Set the ceiling (None = back to the env value). Returns the effective one."""
    if n is None:
        _r().delete(SETTING_KEY)
    else:
        n = int(n)
        if not HARD_MIN <= n <= HARD_MAX:
            raise ValueError(f"Phải từ {HARD_MIN} đến {HARD_MAX}")
        _r().set(SETTING_KEY, str(n))
    _cache.update(value=0.0, at=0.0)
    return get_max_concurrent()


def setting_source() -> str:
    try:
        return "admin" if _r().get(SETTING_KEY) else "env"
    except Exception:
        return "env"


# ── counters (hot path: one pipeline, never raises) ───────────────────

def note_acquired(active: int) -> None:
    try:
        pipe = _r().pipeline(transaction=False)
        pipe.zadd(CUR_PEAK, {"p": int(active)}, gt=True)
        pipe.expire(CUR_PEAK, 600)
        pipe.incr(CUR_ACQ)
        pipe.execute()
    except Exception:
        pass


def note_refused() -> None:
    try:
        _r().incr(CUR_REF)
    except Exception:
        pass


# ── container resources ───────────────────────────────────────────────

def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def cpu_cores() -> Optional[float]:
    v2 = _read("/sys/fs/cgroup/cpu.max")
    if v2:
        quota, _, period = v2.partition(" ")
        if quota != "max" and period:
            return int(quota) / int(period)
    q, p = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if q and p and int(q) > 0:
        return int(q) / int(p)
    return float(os.cpu_count() or 1)


def cpu_usage_usec() -> Optional[int]:
    stat = _read("/sys/fs/cgroup/cpu.stat")
    if stat:
        for line in stat.splitlines():
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    v1 = _read("/sys/fs/cgroup/cpuacct/cpuacct.usage")      # nanoseconds
    return int(v1) // 1000 if v1 else None


def memory_pct() -> Optional[float]:
    cur, mx = _read("/sys/fs/cgroup/memory.current"), _read("/sys/fs/cgroup/memory.max")
    if not cur:
        cur, mx = _read("/sys/fs/cgroup/memory/memory.usage_in_bytes"), \
            _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    try:
        if cur and mx and mx != "max" and int(mx) < 1 << 60:
            return round(100 * int(cur) / int(mx), 1)
    except ValueError:
        pass
    return None


def _cpu_pct(rc) -> Optional[float]:
    usage = cpu_usage_usec()
    if usage is None:
        return None
    now = time.time()
    pct = None
    try:
        raw = rc.get(LAST_CPU)
        if raw:
            last = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
            dt = now - float(last["at"])
            cores = cpu_cores() or 1.0
            if 5 <= dt <= 900 and usage >= int(last["usage_usec"]):
                pct = round(100 * (usage - int(last["usage_usec"])) / (dt * 1_000_000 * cores), 1)
        rc.set(LAST_CPU, json.dumps({"usage_usec": usage, "at": now}), ex=3600)
    except Exception:
        return None
    return pct


# ── sampling (beat, every minute) ─────────────────────────────────────

def sample(now: Optional[float] = None) -> Optional[dict]:
    try:
        rc = _r()
        pipe = rc.pipeline(transaction=True)
        pipe.zscore(CUR_PEAK, "p")
        pipe.get(CUR_ACQ)
        pipe.get(CUR_REF)
        pipe.get("vidgrab:active_downloads")
        pipe.delete(CUR_PEAK, CUR_ACQ, CUR_REF)
        peak, acq, ref, active, _ = pipe.execute()
        try:
            queue = int(rc.llen("downloads") or 0)
        except Exception:
            queue = None
        active_now = int(active or 0)
        rec = {
            "t": int(now or time.time()),
            # The busiest moment of the minute, or what is in flight now if
            # no slot was taken during it (long downloads keep their slot).
            "peak": max(int(float(peak or 0)), active_now),
            "acquired": int(acq or 0),
            "refused": int(ref or 0),
            "limit": get_max_concurrent(),
            "cpu": _cpu_pct(rc),
            "mem": memory_pct(),
            "queue": queue,
        }
        rc.lpush(MINUTES, json.dumps(rec, separators=(",", ":")))
        rc.ltrim(MINUTES, 0, MINUTES_KEEP - 1)
        return rec
    except Exception:
        return None


# ── reading for the admin ─────────────────────────────────────────────

def _minutes(since: int) -> List[dict]:
    try:
        raw = _r().lrange(MINUTES, 0, MINUTES_KEEP - 1) or []
    except Exception:
        return []
    out = []
    for item in raw:
        try:
            rec = json.loads(item.decode() if isinstance(item, bytes) else item)
        except Exception:
            continue
        if rec.get("t", 0) >= since:
            out.append(rec)
    out.sort(key=lambda r: r["t"])
    return out


def series(range_: str = "24h", now: Optional[float] = None) -> dict:
    """{points: [{t, peak, limit, refused, acquired, cpu_avg, cpu_max, mem_max,
    queue_max, samples}], summary: {...}} — 5-min buckets for 24h, hourly for 7d."""
    now = int(now or time.time())
    span, bucket = (7 * 86400, 3600) if range_ == "7d" else (86400, 300)
    mins = _minutes(now - span)
    buckets: Dict[int, dict] = {}
    for m in mins:
        b = m["t"] - m["t"] % bucket
        x = buckets.setdefault(b, {"t": b, "peak": 0, "limit": m.get("limit"), "refused": 0,
                                   "acquired": 0, "cpu_sum": 0.0, "cpu_n": 0, "cpu_max": None,
                                   "mem_max": None, "queue_max": None, "samples": 0})
        x["peak"] = max(x["peak"], m.get("peak") or 0)
        x["limit"] = m.get("limit") or x["limit"]
        x["refused"] += m.get("refused") or 0
        x["acquired"] += m.get("acquired") or 0
        x["samples"] += 1
        if m.get("cpu") is not None:
            x["cpu_sum"] += m["cpu"]
            x["cpu_n"] += 1
            x["cpu_max"] = max(x["cpu_max"] or 0, m["cpu"])
        if m.get("mem") is not None:
            x["mem_max"] = max(x["mem_max"] or 0, m["mem"])
        if m.get("queue") is not None:
            x["queue_max"] = max(x["queue_max"] or 0, m["queue"])
    points = []
    for b in sorted(buckets):
        x = buckets[b]
        x["cpu_avg"] = round(x.pop("cpu_sum") / x["cpu_n"], 1) if x["cpu_n"] else None
        x.pop("cpu_n")
        points.append(x)
    cpus = sorted(m["cpu"] for m in mins if m.get("cpu") is not None)
    at_limit = sum(1 for m in mins if m.get("limit") and (m.get("peak") or 0) >= m["limit"])
    summary = {
        "range": "7d" if span > 86400 else "24h",
        "minutes_sampled": len(mins),
        "first_sample": mins[0]["t"] if mins else None,
        "peak": max((m.get("peak") or 0) for m in mins) if mins else None,
        "refused": sum((m.get("refused") or 0) for m in mins),
        "acquired": sum((m.get("acquired") or 0) for m in mins),
        "minutes_at_limit": at_limit,
        "cpu_p95": cpus[int(0.95 * (len(cpus) - 1))] if cpus else None,
        "cpu_max": cpus[-1] if cpus else None,
        "mem_max": max((m["mem"] for m in mins if m.get("mem") is not None), default=None),
    }
    return {"points": points, "summary": summary}


def _quantile_bound(buckets: Dict[str, int], q: float) -> Optional[int]:
    """Upper bound (ms) of the histogram bucket holding quantile q; None for
    the open-ended "inf" bucket or no data."""
    total = sum(buckets.values())
    if not total:
        return None
    need, seen = q * total, 0
    for k in sorted((k for k in buckets if k != "inf"), key=int):
        seen += buckets[k]
        if seen >= need:
            return int(k)
    return None


def latency_by_platform(days: int = 1) -> List[dict]:
    """Duration buckets per platform from download_outcomes (successful
    /fetch-link attempts), for 'how long does one download take'."""
    try:
        from app.core import download_outcomes as do  # noqa: PLC0415
        today = datetime.now(timezone.utc)
        dates = [do.day_str(datetime.fromtimestamp(today.timestamp() - d * 86400, timezone.utc))
                 for d in range(days)]
        agg: Dict[str, Dict[str, int]] = {}
        for _day, per in (do.read_latency(dates) or {}).items():
            for plat, b in (per or {}).items():
                slot = agg.setdefault(plat, {})
                for k, n in (b or {}).items():
                    slot[k] = slot.get(k, 0) + int(n)
        return [{"platform": p, "buckets": b, "total": sum(b.values()),
                 "p50_ms": _quantile_bound(b, 0.50), "p95_ms": _quantile_bound(b, 0.95)}
                for p, b in sorted(agg.items(), key=lambda kv: -sum(kv[1].values()))]
    except Exception:
        return []
