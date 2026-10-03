"""
Admin error / failure numbers come from ONE source: the outcome store.

Live admin check 2026-10-02: Overview "Lỗi 24h 0" sat next to "Failed jobs 24h 9",
and Analytics "Top error patterns" said "No error patterns found" while 9
attempts had failed with codes. /errors still read download_jobs. These tests
pin download_outcomes.window_summary (the shared helper) and the endpoints.
"""

from datetime import datetime, timedelta, timezone

import fakeredis
import pytest

from app.core import download_outcomes as do
from tests.test_admin_download_metrics import (  # noqa: F401  (fixtures + fakes)
    _FakeSupabase, _FakeTable, _down_redis, admin, rc,
)

NOW = datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)


def _at(hours_ago):
    return NOW - timedelta(hours=hours_ago)


def _rows_supabase(rows):
    class _Rows(_FakeSupabase):
        def table(self, name):
            t = _FakeTable(self, name)

            def _exec():
                class R:
                    data = rows if name == "download_jobs" else []
                return R()
            t.execute = _exec
            return t
    return _Rows()


def _seed_store():
    """Oldest write first: the oldest bucket is the writer's first (partial)
    hour, so coverage starts one hour after it."""
    do.record("tiktok", True, now=_at(4))
    do.record("tiktok", False, "unsupported_url", now=_at(3))
    do.record("tiktok", False, "unsupported_url", now=_at(2))
    do.record("instagram", False, "temporary_blocked", now=_at(1))
    do.record("youtube", True, now=_at(0))


# ── helper ──────────────────────────────────────────────────────────────────

def test_errors_equal_sum_of_err_counters_and_codes(rc):
    _seed_store()
    w = do.window_summary(24, now=NOW)
    assert (w["ok"], w["err"], w["total"]) == (2, 3, 5)
    assert w["err"] == sum(c["err"] for c in w["by_platform"].values())
    assert w["err"] == sum(e["count"] for e in w["top_errors"])      # codes add up to the failures
    assert w["fail_rate"] == 60.0


def test_top_patterns_come_from_error_code_hash(rc):
    _seed_store()
    top = do.window_summary(24, now=NOW)["top_errors"]
    assert [(e["error_code"], e["count"]) for e in top] == [("unsupported_url", 2), ("temporary_blocked", 1)]
    assert top[0]["platforms"] == {"tiktok": 2}


def test_partial_coverage_flag(rc):
    _seed_store()                        # first bucket 4h ago (partial) → hours 3..0 = 4 covered
    w = do.window_summary(24, now=NOW)
    assert w["partial"] is True and w["coverage_hours"] == 4
    assert w["covered_since"] == _at(3).replace(minute=0).isoformat()
    assert w["total"] == 5               # the partial first hour is still counted
    # a bucket older than the window means every hour is covered
    do.record("tiktok", True, now=NOW - timedelta(days=3))
    w = do.window_summary(24, now=NOW)
    assert w["partial"] is False and w["coverage_hours"] == 24


def test_nothing_recorded_is_partial_zero_coverage_not_a_clean_zero(rc):
    w = do.window_summary(24, now=NOW)
    assert w["partial"] is True and w["coverage_hours"] == 0
    assert w["err"] == 0 and w["fail_rate"] is None


def test_hours_older_than_the_window_are_not_counted(rc):
    do.record("tiktok", True, now=NOW - timedelta(days=3))
    do.record("tiktok", False, "unsupported_url", now=_at(30))
    do.record("tiktok", False, "unsupported_url", now=_at(2))
    assert do.window_summary(24, now=NOW)["err"] == 1


def test_uncovered_day_falls_back_to_jobs_but_known_day_and_covered_hours_do_not(rc):
    # store's first bucket is 10-02 01:00 (covered from 02:00); 10-01 has no counter hash at all
    do.record("tiktok", True, now=_at(4))          # 01:30
    do.record("tiktok", False, "unsupported_url", now=_at(0))
    rows = [
        # 10-01: store has nothing for that day → counted from jobs
        {"status": "success", "platform": "youtube", "created_at": "2026-10-01T12:00:00+00:00"},
        {"status": "failed", "platform": "youtube", "error_code": "provider_unavailable",
         "created_at": "2026-10-01T13:00:00+00:00"},
        {"status": "pending", "platform": "youtube", "created_at": "2026-10-01T14:00:00+00:00"},
        # 10-02 00:30 is uncovered but the store HAS a hash for 10-02 → not counted from jobs
        {"status": "failed", "platform": "tiktok", "error_code": "x", "created_at": "2026-10-02T00:30:00+00:00"},
        # covered hour: the store already counted it
        {"status": "success", "platform": "tiktok", "created_at": "2026-10-02T03:00:00+00:00"},
    ]
    w = do.window_summary(24, now=NOW, jobs_loader=lambda a, b: rows)
    assert (w["ok"], w["err"]) == (2, 2)             # 1+1 from the store, 1+1 from 10-01 jobs
    assert w["sources"] == ["redis_hourly", "jobs_table"]
    assert {e["error_code"] for e in w["top_errors"]} == {"unsupported_url", "provider_unavailable"}
    assert w["partial"] is True


def test_no_fallback_without_loader_and_redis_down_is_unknown(monkeypatch):
    monkeypatch.setattr("app.core.redis_client._client", _down_redis())
    w = do.window_summary(24, now=NOW)
    assert w["redis_ok"] is False and w["err"] is None
    rows = [{"status": "failed", "platform": "tiktok", "error_code": "x",
             "created_at": "2026-10-02T04:00:00+00:00"}]
    w = do.window_summary(24, now=NOW, jobs_loader=lambda a, b: rows, fallback_when_redis_down=True)
    assert w["err"] == 1 and w["sources"] == ["jobs_table"]


# ── endpoints ───────────────────────────────────────────────────────────────

def _live_seed():
    now = datetime.now(timezone.utc)
    for _ in range(3):
        do.record("tiktok", True, now=now)
    for _ in range(9):
        do.record("tiktok", False, "unsupported_url", now=now)
    do.record("instagram", False, "temporary_blocked", now=now)


def test_errors_endpoint_reads_outcome_store_not_download_jobs(app, rc, admin, monkeypatch):
    _live_seed()
    # download_jobs says "nothing happened" — the old source must not win
    body = app.get("/api/v1/admin/errors").json()
    s = body["summary_24h"]
    assert (s["total"], s["failed"], s["fail_rate"]) == (13, 10, 76.9)
    pats = {p["error_code"]: p for p in body["error_patterns"]}
    assert pats["unsupported_url"]["count"] == 9
    assert pats["unsupported_url"]["pattern"] == "URL này không được hỗ trợ."   # existing Vietnamese copy
    assert body["outcome_source"].startswith("redis")
    # only the current hour has a bucket: it is the writer's first (partial)
    # hour, so it is counted but not claimed as covered
    assert body["partial"] is True and body["coverage_hours"] == 0 and body["covered_since"]


def test_errors_and_stats_agree_for_the_same_window(app, rc, admin):
    _live_seed()
    errors = app.get("/api/v1/admin/errors").json()
    stats = app.get("/api/v1/admin/stats").json()
    assert errors["summary_24h"]["failed"] == stats["failed_24h"] == stats["downloads_24h"]["failed"] == 10
    assert errors["summary_24h"]["total"] == stats["downloads_24h"]["attempts"] == 13
    assert stats["downloads_24h"]["partial"] == errors["partial"]
    assert stats["downloads_24h"]["coverage_hours"] == errors["coverage_hours"]
    assert stats["top_errors_24h"][0]["error_code"] == "unsupported_url"
    assert sum(e["count"] for e in stats["top_errors_24h"]) == stats["failed_24h"]


def test_errors_endpoint_keeps_the_old_fields(app, rc, admin, monkeypatch):
    _live_seed()
    monkeypatch.setattr(admin, "get_supabase_client", lambda: _rows_supabase(
        [{"id": "j1", "original_url": "https://x", "error_message": "boom", "created_at": "2026-10-01T00:00:00+00:00"}]))
    body = app.get("/api/v1/admin/errors").json()
    for k in ("success", "recent_errors", "error_patterns", "platform_fail_rates", "summary_24h"):
        assert k in body
    assert body["recent_errors"][0]["id"] == "j1"
    assert body["recent_errors_source"] == "download_jobs"
    assert set(body["summary_24h"]) >= {"total", "failed", "fail_rate"}
    assert {"pattern", "count"} <= set(body["error_patterns"][0])
    assert {"platform", "total", "failed", "fail_rate"} <= set(body["platform_fail_rates"][0])
    tiktok = next(r for r in body["platform_fail_rates"] if r["key"] == "tiktok")
    assert (tiktok["total"], tiktok["failed"]) == (12, 9)


def test_errors_endpoint_zero_errors_and_no_traffic(app, rc, admin):
    do.record("tiktok", True)
    s = app.get("/api/v1/admin/errors").json()["summary_24h"]
    assert (s["total"], s["failed"], s["fail_rate"]) == (1, 0, 0.0)
    rc.flushall()
    body = app.get("/api/v1/admin/errors").json()
    assert body["summary_24h"]["failed"] == 0 and body["summary_24h"]["fail_rate"] is None
    assert body["error_patterns"] == []


def test_errors_endpoint_unknown_code_shows_the_code(app, rc, admin):
    do.record("tiktok", False, "video_unavailable")
    pat = app.get("/api/v1/admin/errors").json()["error_patterns"][0]
    assert pat["pattern"] == "video_unavailable" == pat["error_code"]
