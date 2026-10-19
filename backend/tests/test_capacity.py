"""Capacity (19/10): the concurrent-download ceiling is an admin setting (env
as fallback) that /fetch-link reads; every slot taken / refused is counted,
a per-minute sample adds CPU and memory, and the admin series folds minutes
into chart points."""
import json

import fakeredis
import pytest


@pytest.fixture()
def fr(monkeypatch):
    import app.core.redis_client as rc
    r = fakeredis.FakeRedis()
    monkeypatch.setattr(rc, "get_redis", lambda: r)
    from app.core import capacity
    capacity._cache.update(value=0.0, at=0.0)
    return r


def test_setting_overrides_env_and_resets(fr, monkeypatch):
    from app.core import capacity
    monkeypatch.setenv("MAX_CONCURRENT_DOWNLOADS", "6")
    assert capacity.get_max_concurrent() == 6 and capacity.setting_source() == "env"
    assert capacity.set_max_concurrent(14) == 14
    assert capacity.get_max_concurrent() == 14 and capacity.setting_source() == "admin"
    assert capacity.set_max_concurrent(None) == 6
    with pytest.raises(ValueError):
        capacity.set_max_concurrent(0)
    with pytest.raises(ValueError):
        capacity.set_max_concurrent(51)


def test_fetch_link_slot_uses_the_admin_ceiling_and_counts(fr, monkeypatch):
    from app.core import capacity
    import app.api.routes as routes
    capacity.set_max_concurrent(2)
    got = [routes._acquire_download_slot() for _ in range(3)]
    assert [g[0] for g in got] == [True, True, False]
    rec = capacity.sample(now=1_000_000)
    assert rec["peak"] == 2 and rec["acquired"] == 2 and rec["refused"] == 1 and rec["limit"] == 2
    # counters were moved into the minute record
    rec2 = capacity.sample(now=1_000_060)
    assert rec2["acquired"] == 0 and rec2["refused"] == 0
    assert rec2["peak"] == 2          # two slots still held: what is in flight now
    for _ in range(2):
        routes._release_download_slot()


def test_peak_is_the_max_within_the_minute(fr):
    from app.core import capacity
    for n in (1, 5, 3):
        capacity.note_acquired(n)
    assert capacity.sample(now=1)["peak"] == 5


def test_cpu_and_memory_from_cgroup(fr, monkeypatch):
    from app.core import capacity
    files = {"/sys/fs/cgroup/cpu.max": "135000 100000",
             "/sys/fs/cgroup/memory.current": "1073741824",
             "/sys/fs/cgroup/memory.max": "4294967296"}
    usage = {"v": 10_000_000}
    monkeypatch.setattr(capacity, "_read", lambda p: (f"usage_usec {usage['v']}" if p.endswith("cpu.stat")
                                                      else files.get(p)))
    clock = {"t": 1000.0}
    monkeypatch.setattr(capacity.time, "time", lambda: clock["t"])
    assert capacity.cpu_cores() == 1.35
    assert capacity.memory_pct() == 25.0
    assert capacity.sample()["cpu"] is None            # first sample: no delta yet
    usage["v"] += 40_500_000                           # 40.5 CPU-seconds …
    clock["t"] += 60                                   # … in 60 s on 1.35 cores
    assert capacity.sample()["cpu"] == 50.0


def test_series_buckets_and_summary(fr):
    from app.core import capacity
    now = 1_000_000 - 1_000_000 % 300 + 299
    for i, (peak, ref, cpu) in enumerate([(3, 0, 20.0), (14, 2, 90.0), (6, 0, 40.0)]):
        fr.lpush(capacity.MINUTES, json.dumps({"t": now - 60 * i, "peak": peak, "acquired": 5, "refused": ref,
                                               "limit": 14, "cpu": cpu, "mem": 30.0, "queue": 0}))
    fr.lpush(capacity.MINUTES, json.dumps({"t": now - 2 * 86400, "peak": 50, "limit": 14}))  # outside 24h
    out = capacity.series("24h", now=now)
    s = out["summary"]
    assert s["peak"] == 14 and s["refused"] == 2 and s["minutes_at_limit"] == 1 and s["cpu_max"] == 90.0
    assert sum(p["samples"] for p in out["points"]) == 3
    assert max(p["peak"] for p in out["points"]) == 14
    assert capacity.series("7d", now=now)["summary"]["peak"] == 50


def test_latency_quantiles():
    from app.core.capacity import _quantile_bound
    b = {"1000": 50, "3000": 40, "10000": 9, "inf": 1}
    assert _quantile_bound(b, 0.5) == 1000
    assert _quantile_bound(b, 0.95) == 10000
    assert _quantile_bound({}, 0.5) is None


def test_admin_capacity_endpoints(fr, monkeypatch):
    import asyncio
    from unittest.mock import MagicMock
    from app.api import admin_platform_health as aph
    monkeypatch.setattr(aph, "log_admin_action", lambda *a, **k: None)
    out = asyncio.run(aph.capacity_settings(aph.CapacitySettingsBody(max_concurrent_downloads=14), MagicMock()))
    assert out == {"max_concurrent_downloads": 14, "source": "admin"}
    view = asyncio.run(aph.capacity_overview(range="24h"))
    assert view["setting"]["max_concurrent_downloads"] == 14 and "points" in view and "latency" in view
    with pytest.raises(Exception):
        aph.CapacitySettingsBody(max_concurrent_downloads=99)
