"""
F2 — anomaly spam.

Production 2026-10-02: /admin/anomalies held 100 "active" items, most of them
the same "success_drop:tiktok … dropped 21.4%" re-inserted by the detector
every 5 minutes; the Overview card read "Anomaly alerts 100"; a 7-day-old
CRITICAL schedule_drift stayed pinned and the bell stayed red.

Now: one entry per open condition (dedupe), auto-resolve when the condition
clears, nothing unseen for 24h counts as active, and an idempotent cleanup
for the rows already in Redis.
"""

import json
from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.core import anomaly_detector as ad


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)  # patch the singleton, not get_redis (modules bind get_redis at import)
    return r


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seed_tiktok_drop(rc, today_ok=11, today_err=3):
    """78.6 % today vs 100 % over the last 7 days — the production numbers."""
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"tiktok:ok": today_ok, "tiktok:err": today_err})
    for d in ad._past_dates():
        rc.hset(f"vidgrab:stats:{d}", mapping={"tiktok:ok": 50, "tiktok:err": 0})


def _active_raw(rc):
    return [json.loads(x) for x in rc.lrange(ad.ANOMALY_ACTIVE_KEY, 0, -1)]


# ── (a) dedupe ──────────────────────────────────────────────────────────────

def test_repeated_check_updates_one_entry_instead_of_inserting(rc):
    _seed_tiktok_drop(rc)
    for _ in range(12):                       # one hour of 5-minute runs
        ad.check_platform_success_drop()
    rows = [a for a in _active_raw(rc) if a["metric"] == "success_drop:tiktok"]
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 12
    assert rows[0]["first_seen"] <= rows[0]["last_seen"]
    assert abs(rows[0]["magnitude"] - (1 - 11 / 14)) < 1e-3


def test_dedupe_tracks_latest_magnitude(rc):
    _seed_tiktok_drop(rc)
    ad.check_platform_success_drop()
    _seed_tiktok_drop(rc, today_ok=5, today_err=5)       # worse now
    ad.check_platform_success_drop()
    rows = _active_raw(rc)
    assert len(rows) == 1 and rows[0]["magnitude"] == 0.5
    assert "50.0%" in rows[0]["likely_cause"]


def test_different_metrics_are_separate_entries(rc):
    _seed_tiktok_drop(rc)
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"instagram:ok": 1, "instagram:err": 9})
    for d in ad._past_dates():
        rc.hset(f"vidgrab:stats:{d}", mapping={"instagram:ok": 50, "instagram:err": 0})
    ad.run_all_checks()
    ad.run_all_checks()
    metrics = sorted(a["metric"] for a in _active_raw(rc) if a["state"] != "anomaly_resolved")
    assert metrics.count("success_drop:tiktok") == 1
    assert metrics.count("success_drop:instagram") == 1
    assert metrics.count("failure_spike:instagram") == 1


# ── (b) auto-resolve ───────────────────────────────────────────────────────

def test_condition_cleared_auto_resolves(rc):
    _seed_tiktok_drop(rc)
    ad.check_platform_success_drop()
    assert len(ad.get_active_anomalies()) == 1
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"tiktok:ok": 100, "tiktok:err": 3})   # recovered
    ad.check_platform_success_drop()
    assert ad.get_active_anomalies() == []
    row = _active_raw(rc)[0]
    assert row["state"] == ad.AnomalyState.RESOLVED.value
    assert row["auto_resolved"] is True and row["resolved_at"]
    assert row["resolution_reason"] == "condition_cleared"


def test_recurrence_after_resolve_opens_a_new_episode(rc):
    _seed_tiktok_drop(rc)
    ad.check_platform_success_drop()
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"tiktok:ok": 100, "tiktok:err": 3})
    ad.check_platform_success_drop()
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"tiktok:ok": 100, "tiktok:err": 60})
    ad.check_platform_success_drop()
    rows = _active_raw(rc)
    assert len(rows) == 2
    assert len(ad.get_active_anomalies()) == 1


def test_insufficient_data_does_not_resolve(rc):
    """Fewer than FAILURE_SPIKE_MIN_REQUESTS today means "can't tell", not "fine"."""
    _seed_tiktok_drop(rc)
    ad.check_platform_success_drop()
    rc.delete(f"vidgrab:stats:{_today()}")
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"tiktok:ok": 1})
    ad.check_platform_success_drop()
    assert len(ad.get_active_anomalies()) == 1


def test_schedule_drift_resolves_on_next_on_time_run(rc):
    now = datetime.now(timezone.utc).timestamp()
    rc.set(ad.KEY_PREFIX + "anomaly_check:last_run", str(now - 3600))
    assert ad.check_schedule_drift() is not None
    assert [a["metric"] for a in ad.get_active_anomalies()] == ["schedule_drift"]
    ad.check_schedule_drift()                  # ran again right away → on time
    assert ad.get_active_anomalies() == []


def test_manual_resolve_still_works(rc):
    _seed_tiktok_drop(rc)
    a = ad.check_platform_success_drop()[0]
    assert ad.resolve_anomaly(a["id"]) is True
    assert ad.get_active_anomalies() == []
    assert _active_raw(rc)[0]["auto_resolved"] is False


# ── (c) 24h staleness ──────────────────────────────────────────────────────

def _push(rc, metric, seen, state="anomaly_detected", **extra):
    item = {"id": f"{metric}-{seen.isoformat()}", "detected_at": seen.isoformat(),
            "state": state, "metric": metric, "window": "w", "magnitude": 2.0,
            "likely_cause": metric, "auto_mitigated": False, "mitigation_applied": None, **extra}
    rc.rpush(ad.ANOMALY_ACTIVE_KEY, json.dumps(item))
    return item


def test_week_old_schedule_drift_is_not_active(rc):
    now = datetime.now(timezone.utc)
    _push(rc, "schedule_drift", now - timedelta(days=7), magnitude=7200.0)
    _push(rc, "disk_pressure", now - timedelta(hours=1))
    active = ad.get_active_anomalies()
    assert [a["metric"] for a in active] == ["disk_pressure"]
    listed = {a["metric"]: a for a in ad.list_anomalies()}
    assert listed["schedule_drift"]["stale"] is True and listed["schedule_drift"]["active"] is False


def test_last_seen_keeps_old_detection_active(rc):
    now = datetime.now(timezone.utc)
    _push(rc, "queue_lag", now - timedelta(days=3), last_seen=(now - timedelta(minutes=5)).isoformat())
    assert len(ad.get_active_anomalies()) == 1


def test_run_all_checks_resolves_stale_entries(rc, monkeypatch):
    monkeypatch.setattr(ad, "check_disk_pressure", lambda: None)
    now = datetime.now(timezone.utc)
    _push(rc, "schedule_drift", now - timedelta(days=7))
    ad.run_all_checks()
    row = next(a for a in _active_raw(rc) if a["metric"] == "schedule_drift")
    assert row["state"] == ad.AnomalyState.RESOLVED.value
    assert row["resolution_reason"] == "stale" and row["auto_resolved"] is True


def test_stale_open_entry_is_closed_when_condition_returns(rc):
    now = datetime.now(timezone.utc)
    _push(rc, "success_drop:tiktok", now - timedelta(days=2))
    _seed_tiktok_drop(rc)
    ad.check_platform_success_drop()
    rows = [a for a in _active_raw(rc) if a["metric"] == "success_drop:tiktok"]
    assert len(rows) == 2
    assert sorted(r["state"] for r in rows) == ["anomaly_detected", "anomaly_resolved"]


# ── (d) cleanup ────────────────────────────────────────────────────────────

def _seed_production_mess(rc):
    now = datetime.now(timezone.utc)
    for i in range(97):                       # success_drop every 5 minutes
        _push(rc, "success_drop:tiktok", now - timedelta(minutes=5 * i))
    _push(rc, "schedule_drift", now - timedelta(days=7))
    _push(rc, "disk_pressure", now - timedelta(minutes=3))
    _push(rc, "queue_lag", now - timedelta(hours=2), state="anomaly_resolved")


def test_cleanup_collapses_duplicates_and_resolves_stale(rc):
    _seed_production_mess(rc)
    # Read side already counts each condition once, before any cleanup.
    assert len(ad.get_active_anomalies()) == 2
    dry = ad.cleanup_anomalies(dry_run=True)
    assert dry["duplicates_removed"] == 96 and dry["stale_resolved"] == 1
    assert rc.llen(ad.ANOMALY_ACTIVE_KEY) == 100                 # dry run wrote nothing
    report = ad.cleanup_anomalies(dry_run=False)
    assert report["duplicates_removed"] == 96
    assert report["after"] == 4 and report["active_after"] == 2
    tt = next(a for a in _active_raw(rc) if a["metric"] == "success_drop:tiktok")
    assert tt["occurrence_count"] == 97
    # idempotent
    again = ad.cleanup_anomalies(dry_run=False)
    assert again["duplicates_removed"] == 0 and again["stale_resolved"] == 0
    assert again["after"] == 4


# ── counts endpoints ───────────────────────────────────────────────────────



def _admin_dependencies(app_obj):
    """Every verify_admin callable the mounted admin routes actually depend on.

    test_admin_security reloads app.api.admin, after which the module's
    `verify_admin` is a new object while the routes still hold the original —
    so override what the routes hold, not the module attribute."""
    found = set()
    for route in app_obj.routes:
        dep = getattr(route, "dependant", None)
        stack = list(getattr(dep, "dependencies", []) or [])
        while stack:
            d = stack.pop()
            if getattr(d.call, "__name__", "") == "verify_admin":
                found.add(d.call)
            stack.extend(d.dependencies or [])
    return found

@pytest.fixture
def as_admin(monkeypatch):
    import app.main as main_mod
    from app.api import admin as admin_mod
    deps = _admin_dependencies(main_mod.app)
    for d in deps:
        main_mod.app.dependency_overrides[d] = lambda: True

    async def _ok(*a, **k):
        return None
    monkeypatch.setattr(admin_mod, "verify_admin", _ok)        # intelligence calls it directly
    yield
    for d in deps:
        main_mod.app.dependency_overrides.pop(d, None)


def test_ops_health_counts_only_active(app, rc, as_admin):
    _seed_production_mess(rc)
    body = app.get("/api/v1/admin/ops-health").json()
    assert body["anomaly_count"] == 2      # 97 open copies of one metric count once
    app.post("/api/v1/intelligence/anomalies/cleanup?dry_run=false")
    assert rc.llen(ad.ANOMALY_ACTIVE_KEY) == 4
    body = app.get("/api/v1/admin/ops-health").json()
    assert body["anomaly_count"] == 2
    assert {a["metric"] for a in body["active_anomalies"]} == {"success_drop:tiktok", "disk_pressure"}
    assert all(a["active"] for a in body["active_anomalies"])


def test_intelligence_anomalies_count_is_active_only(app, rc, as_admin):
    now = datetime.now(timezone.utc)
    _push(rc, "schedule_drift", now - timedelta(days=7))
    _push(rc, "queue_lag", now - timedelta(hours=2), state="anomaly_resolved")
    _push(rc, "disk_pressure", now - timedelta(minutes=1))
    body = app.get("/api/v1/intelligence/anomalies").json()
    assert body["count"] == 1 and body["active_count"] == 1 and body["total"] == 3
    status = {a["metric"]: a["status"] for a in body["anomalies"]}
    assert status == {"schedule_drift": "detected", "queue_lag": "resolved", "disk_pressure": "detected"}
    assert body["anomalies"][0]["metric"] == "disk_pressure"          # active first


def test_cleanup_endpoint_defaults_to_dry_run_and_requires_admin(app, rc):
    _seed_production_mess(rc)
    r = app.post("/api/v1/intelligence/anomalies/cleanup")
    assert r.status_code in (401, 403)
    assert rc.llen(ad.ANOMALY_ACTIVE_KEY) == 100


def test_cleanup_endpoint_dry_run_default(app, rc, as_admin):
    _seed_production_mess(rc)
    body = app.post("/api/v1/intelligence/anomalies/cleanup").json()
    assert body["dry_run"] is True and body["duplicates_removed"] == 96
    assert rc.llen(ad.ANOMALY_ACTIVE_KEY) == 100


def test_schedule_drift_count_reads_the_list(app, rc, as_admin):
    now = datetime.now(timezone.utc)
    rc.lpush("vidgrab:schedule:drift_alerts", json.dumps({"detected_at": (now - timedelta(days=3)).isoformat()}))
    rc.lpush("vidgrab:schedule:drift_alerts", json.dumps({"detected_at": now.isoformat()}))
    assert app.get("/api/v1/admin/ops-health").json()["schedule_drift_count"] == 1


def test_fallback_summary_success_rate_null_without_attempts(app, rc, as_admin):
    rc.hset(f"vidgrab:stats:{_today()}", mapping={"tiktok:ok": 0, "tiktok:err": 0})
    body = app.get("/api/v1/admin/ops-health").json()
    assert body["fallback_summary"]["tiktok"]["success_rate"] is None
