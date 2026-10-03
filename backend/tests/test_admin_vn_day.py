"""
Admin "today" / per-day numbers use the Vietnam calendar day (UTC+7), not UTC.

Before: "Lượt tải hôm nay" reset at 07:00 Vietnam time and yesterday evening's
traffic (17:00–24:00 UTC = 00:00–07:00 VN) landed on the wrong day. The admin
now sums the 24 UTC hourly buckets of each VN day (download_outcomes
.admin_day_window is the one boundary); days the hourly buckets do not fully
cover fall back to the UTC-day hash and are marked approximate.

Every datetime here is timezone-aware. This file must pass under both
TZ=UTC (production container) and TZ=Asia/Ho_Chi_Minh (this workspace).
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import download_outcomes as do
from tests.test_admin_download_metrics import (  # noqa: F401  (fixtures + fakes)
    _FakeSupabase, _FakeTable, admin, rc,
)

Z = timezone.utc


def _utc(*a):
    return datetime(*a, tzinfo=Z)


@pytest.fixture
def pin_now(monkeypatch):
    def _pin(dt):
        monkeypatch.setattr(do, "_now", lambda: dt)
        return dt
    return _pin


@pytest.fixture(autouse=True)
def _default_tz(monkeypatch):
    monkeypatch.delenv("ADMIN_TIMEZONE", raising=False)


def _writer_since(dt):
    """An hourly bucket at `dt` (the hourly writer's first hour)."""
    do.record("other", True, now=dt)


# ── the one boundary helper ─────────────────────────────────────────────────

def test_day_window_is_vietnam_midnight_to_midnight():
    assert do.admin_day_window(date(2026, 10, 3)) == (_utc(2026, 10, 2, 17), _utc(2026, 10, 3, 17))
    assert do.admin_tz_name() == "Asia/Ho_Chi_Minh"


def test_today_flips_at_17_utc():
    assert do.admin_today(_utc(2026, 10, 2, 16, 59, 59)) == date(2026, 10, 2)
    assert do.admin_today(_utc(2026, 10, 2, 17, 0, 0)) == date(2026, 10, 3)


def test_admin_timezone_setting_and_bad_value(monkeypatch):
    monkeypatch.setenv("ADMIN_TIMEZONE", "UTC")
    assert do.admin_day_window(date(2026, 10, 3)) == (_utc(2026, 10, 3), _utc(2026, 10, 4))
    monkeypatch.setenv("ADMIN_TIMEZONE", "Not/AZone")
    assert do.admin_day_window(date(2026, 10, 3))[0] == _utc(2026, 10, 2, 17)


# ── read_admin_days ─────────────────────────────────────────────────────────

def test_1659z_and_1700z_land_on_different_vn_days(rc):
    _writer_since(_utc(2026, 9, 28))
    do.record("tiktok", True, now=_utc(2026, 10, 2, 16, 59, 30))
    do.record("tiktok", False, "video_unavailable", now=_utc(2026, 10, 2, 17, 0, 0))
    res = do.read_admin_days([date(2026, 10, 2), date(2026, 10, 3)], now=_utc(2026, 10, 3, 5))
    d2, d3 = res["days"]
    assert (d2["source"], d3["source"]) == ("hourly", "hourly")
    assert d2["per_platform"]["tiktok"] == {"ok": 1, "err": 0}
    assert d3["per_platform"]["tiktok"] == {"ok": 0, "err": 1}
    assert d3["codes"] == {("tiktok", "video_unavailable"): 1}


def test_vn_today_at_0600_includes_1700z_yesterday(rc):
    _writer_since(_utc(2026, 9, 28))
    now = _utc(2026, 10, 2, 23, 0)                       # 06:00 on 10-03 in Vietnam
    do.record("tiktok", True, now=_utc(2026, 10, 2, 16, 59))   # 23:59 on 10-02 VN
    do.record("tiktok", True, now=_utc(2026, 10, 2, 17, 0))    # 00:00 on 10-03 VN
    do.record("youtube", True, now=_utc(2026, 10, 2, 22, 45))  # 05:45 on 10-03 VN
    row = do.read_admin_days([do.admin_today(now)], now=now)["days"][0]
    assert row["date"] == "2026-10-03"
    assert do.sum_platforms(row["per_platform"])["total"] == 2
    assert row["approximate"] is False


def test_uncovered_days_are_utc_day_approximations_without_double_count(rc):
    # hourly writer starts 10-01 16:xx (partial hour) → covered from 17:00Z,
    # i.e. VN 10-02 is the first whole VN day.
    do.record("tiktok", True, now=_utc(2026, 10, 1, 16, 30))   # first bucket; UTC 10-01, VN 10-01
    do.record("tiktok", True, now=_utc(2026, 10, 1, 20, 0))    # UTC 10-01, VN 10-02
    do.record("tiktok", True, now=_utc(2026, 10, 2, 3, 0))     # UTC 10-02, VN 10-02
    rc.hset(do.DAY_KEY_TPL.format(date="2026-10-01"), "tiktok:ok",       # older traffic of
            int(rc.hget(do.DAY_KEY_TPL.format(date="2026-10-01"), "tiktok:ok")) + 5)  # UTC 10-01
    now = _utc(2026, 10, 3, 5, 30)
    res = do.read_admin_days([date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)], now=now)
    d1, d2, d3 = res["days"]
    assert res["covered_since"] == "2026-10-01T17:00:00+00:00"
    assert (d1["source"], d1["approximate"]) == ("utc_day", True)
    assert (d2["source"], d2["approximate"]) == ("hourly", False)
    assert (d3["source"], d3["approximate"]) == ("hourly", False)
    # UTC 10-01 hash = 7, minus the 20:00Z attempt VN 10-02 already counts
    assert d1["per_platform"]["tiktok"]["ok"] == 6
    assert (d1["counted_start"], d1["counted_end"]) == ("2026-10-01T00:00:00+00:00",
                                                        "2026-10-01T17:00:00+00:00")
    assert d2["per_platform"]["tiktok"]["ok"] == 2
    total = sum(do.sum_platforms(d["per_platform"])["total"] for d in res["days"])
    assert total == 8       # 5 older + 3 recorded: every attempt exactly once


def test_coverage_comes_from_the_buckets_not_the_marker(rc):
    do.record("tiktok", True, now=_utc(2026, 10, 2, 14, 6))    # deploy hour, partial
    rc.set(do.HOURLY_SINCE_KEY, "2026-10-03T01")               # marker introduced later
    cov = do.hourly_coverage(now=_utc(2026, 10, 3, 5))
    assert cov["first_bucket"] == _utc(2026, 10, 2, 14)
    assert cov["covered_since"] == _utc(2026, 10, 2, 15)       # first COMPLETE hour


def test_no_buckets_means_no_coverage(rc):
    assert do.hourly_coverage(now=_utc(2026, 10, 3, 5)) == {"first_bucket": None, "covered_since": None}
    row = do.read_admin_days([date(2026, 10, 3)], now=_utc(2026, 10, 3, 5))["days"][0]
    assert row["source"] is None and row["approximate"] is True


def test_hourly_buckets_outlive_the_30_day_view(rc):
    do.record("tiktok", False, "video_unavailable")
    hour = do.hour_str(do._now())
    need = 30 * 86400 + 7 * 3600
    assert rc.ttl(do.HOUR_KEY_TPL.format(hour=hour)) >= need
    assert rc.ttl(do.ERRCODE_HOUR_KEY_TPL.format(hour=hour)) >= need
    assert do.COVERAGE_SCAN_HOURS * 3600 >= need


def test_failures_without_hourly_codes_are_unclassified(rc):
    _writer_since(_utc(2026, 9, 28))
    # an hour written before error codes were kept per hour: count, no code
    rc.hset(do.HOUR_KEY_TPL.format(hour="2026-10-02T18"), "tiktok:err", 3)
    do.record("tiktok", False, "video_unavailable", now=_utc(2026, 10, 2, 19))
    row = do.read_admin_days([date(2026, 10, 3)], now=_utc(2026, 10, 3, 5))["days"][0]
    assert row["codes"] == {("tiktok", "unclassified"): 3, ("tiktok", "video_unavailable"): 1}
    assert sum(row["codes"].values()) == row["per_platform"]["tiktok"]["err"]


# ── endpoints ───────────────────────────────────────────────────────────────

def test_stats_today_at_0600_vn(app, rc, admin, pin_now):
    _writer_since(_utc(2026, 9, 28))
    do.record("tiktok", True, now=_utc(2026, 10, 2, 16, 59))   # yesterday (VN)
    do.record("tiktok", True, now=_utc(2026, 10, 2, 17, 0))    # today (VN)
    do.record("tiktok", False, "video_unavailable", now=_utc(2026, 10, 2, 22, 0))
    pin_now(_utc(2026, 10, 2, 23, 0))                          # 06:00 VN, 10-03
    body = app.get("/api/v1/admin/stats").json()
    assert body["downloads_today"] == {"attempts": 2, "success": 1, "failed": 1, "success_rate": 50.0}
    assert body["total_downloads_today"] == 1
    assert body["today"] == {
        "date": "2026-10-03", "timezone": "Asia/Ho_Chi_Minh",
        "window_start": "2026-10-02T17:00:00+00:00", "window_end": "2026-10-03T17:00:00+00:00",
        "source": "hourly", "approximate": False,
    }
    assert body["top_errors_today"][0]["error_code"] == "video_unavailable"


def test_analytics_days_are_vn_days(app, rc, admin, pin_now):
    _writer_since(_utc(2026, 9, 20))
    do.record("tiktok", True, now=_utc(2026, 10, 1, 16, 59))   # VN 10-01
    do.record("tiktok", True, now=_utc(2026, 10, 1, 17, 0))    # VN 10-02
    do.record("tiktok", True, now=_utc(2026, 10, 2, 16, 59))   # VN 10-02
    do.record("tiktok", True, now=_utc(2026, 10, 2, 17, 0))    # VN 10-03
    pin_now(_utc(2026, 10, 2, 23, 0))                          # 06:00 VN, 10-03
    body = app.get("/api/v1/admin/analytics?days=3").json()
    by_day = {d["date"]: d["total"] for d in body["daily_stats"]}
    assert by_day == {"2026-10-01": 1, "2026-10-02": 2, "2026-10-03": 1}
    assert body["timezone"] == "Asia/Ho_Chi_Minh"
    assert body["window_start"] == "2026-09-30T17:00:00+00:00"
    assert body["window_end"] == "2026-10-03T17:00:00+00:00"
    assert body["approximate_days"] == [] and body["summary"]["approximate"] is False
    assert {d["source"] for d in body["daily_stats"]} == {"hourly"}
    one = app.get("/api/v1/admin/analytics?days=1").json()
    assert one["daily_stats"][0]["date"] == "2026-10-03" and one["summary"]["total_jobs"] == 1


def test_analytics_marks_approximate_days(app, rc, admin, pin_now):
    do.record("tiktok", True, now=_utc(2026, 10, 2, 16, 30))  # first bucket → covered from 17:00Z
    do.record("tiktok", True, now=_utc(2026, 10, 2, 20, 0))   # VN 10-03, exact
    pin_now(_utc(2026, 10, 3, 5, 30))
    body = app.get("/api/v1/admin/analytics?days=2").json()
    d2, d3 = body["daily_stats"]
    assert (d2["date"], d2["source"], d2["approximate"]) == ("2026-10-02", "utc_day", True)
    assert (d3["date"], d3["source"], d3["approximate"]) == ("2026-10-03", "hourly", False)
    assert (d2["total"], d3["total"]) == (1, 1)               # 20:00Z is not counted twice
    assert body["summary"]["total_jobs"] == 2
    assert body["approximate_days"] == ["2026-10-02"]


def test_signups_today_is_vn_day(app, rc, admin, pin_now, monkeypatch):
    pin_now(_utc(2026, 10, 2, 23, 0))                          # 06:00 VN, 10-03
    rows = [{"created_at": "2026-10-02T16:59:00+00:00", "tier": "free"},   # VN 10-02
            {"created_at": "2026-10-02T17:30:00+00:00", "tier": "free"}]   # VN 10-03

    class _Rows(_FakeSupabase):
        def table(self, name):
            t = _FakeTable(self, name)

            def _exec():
                class R:
                    data = rows if name == "profiles" else []
                return R()
            t.execute = _exec
            return t
    monkeypatch.setattr(admin, "get_supabase_client", lambda: _Rows())
    body = app.get("/api/v1/admin/users/signups?days=2").json()
    assert body["today"] == 1
    assert [d["date"] for d in body["daily_signups"]] == ["2026-10-02", "2026-10-03"]
    assert [d["total"] for d in body["daily_signups"]] == [1, 1]
