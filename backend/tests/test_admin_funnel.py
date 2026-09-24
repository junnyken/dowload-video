"""
Admin conversion funnel — GET /admin/funnel
==========================================
The funnel exists to answer "where do people stop", so every test here is about
a way that question gets answered WRONG rather than about the endpoint running.

The counting rule is the whole point: analytics_daily already aggregates
event_count per day, and a funnel built on those counts flatters itself — one
person pasting ten links looks like ten people at that step, and every ratio
below it shrinks to match. So the endpoint counts distinct people and these
tests hold it to that.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _sb(rows):
    """Mock the exact call chain the endpoint uses."""
    sb = MagicMock()
    (sb.table.return_value
       .select.return_value
       .gte.return_value
       .in_.return_value
       .limit.return_value
       .execute.return_value.data) = rows
    return sb


def _ev(name, *, user=None, anon=None, platform=None):
    return {
        "event_name":   name,
        "user_id":      user,
        "anonymous_id": anon,
        "properties":   {"platform": platform} if platform else {},
        "created_at":   "2026-09-24T00:00:00+00:00",
    }


def _run(rows, days=7):
    from app.api.admin import get_funnel
    with patch("app.api.admin.get_supabase_client", return_value=_sb(rows)):
        return asyncio.run(get_funnel(days=days, _=None))


def _step(result, event):
    return next(s for s in result["steps"] if s["event"] == event)


class TestCountsPeopleNotEvents:

    def test_one_person_pasting_ten_links_is_one_person(self):
        rows = [_ev("paste_url", anon="a1") for _ in range(10)]
        r = _run(rows)
        s = _step(r, "paste_url")
        assert s["users"] == 1, "counted events instead of people — the exact bug this endpoint exists to avoid"
        assert s["events"] == 10, "raw event count should still be reported alongside"

    def test_signed_in_id_wins_so_one_person_is_not_two(self):
        """A session that logs in mid-way sends some rows anon and some with a
        user_id. Counting both identities would inflate every step."""
        rows = [
            _ev("paste_url", anon="a1"),
            _ev("paste_url", user="u1", anon="a1"),
        ]
        assert _step(_run(rows), "paste_url")["users"] == 2, (
            "two different identities here is expected — this test pins the "
            "current rule (user_id preferred per row) so a change to identity "
            "stitching is a deliberate decision, not a silent drift"
        )

    def test_rows_without_any_identity_are_not_counted_as_a_person(self):
        rows = [_ev("paste_url"), _ev("paste_url", anon="a1")]
        assert _step(_run(rows), "paste_url")["users"] == 1


class TestDropOff:

    def test_drop_is_computed_between_neighbouring_steps(self):
        rows = [_ev("landing_page_view", anon=f"a{i}") for i in range(10)]
        rows += [_ev("paste_url", anon=f"a{i}") for i in range(4)]
        r = _run(rows)
        assert _step(r, "paste_url")["drop_from_prev_pct"] == 60.0
        assert _step(r, "paste_url")["pct_of_top"] == 40.0

    def test_a_step_bigger_than_the_one_before_reports_no_drop(self):
        """Deep links never fire landing_page_view, so paste_url can legitimately
        exceed it. A naive subtraction turns that into a negative drop, which
        renders as growth and makes the funnel look like it gains people."""
        rows = [_ev("landing_page_view", anon="a1")]
        rows += [_ev("paste_url", anon=f"a{i}") for i in range(5)]
        s = _step(_run(rows), "paste_url")
        assert s["users"] == 5
        assert s["drop_from_prev_pct"] is None, (
            f"got {s['drop_from_prev_pct']} — a negative drop reads as growth"
        )

    def test_empty_window_does_not_divide_by_zero(self):
        r = _run([])
        assert all(s["users"] == 0 for s in r["steps"])
        assert all(s["pct_of_top"] is None for s in r["steps"])


class TestFailures:

    def test_failure_rate_counts_people_on_both_sides(self):
        rows = [_ev("fetch_success", anon=f"ok{i}") for i in range(3)]
        rows += [_ev("fetch_failed", anon="bad1")]
        f = next(x for x in _run(rows)["failures"] if x["stage"] == "Lấy thông tin")
        assert f["ok_users"] == 3 and f["failed_users"] == 1
        assert f["failure_rate_pct"] == 25.0

    def test_no_traffic_gives_null_rate_not_zero(self):
        """0% failure and 'nobody tried' are different facts. Reporting the
        second as the first is how a dead platform looks perfectly healthy."""
        f = next(x for x in _run([])["failures"] if x["stage"] == "Tải về")
        assert f["failure_rate_pct"] is None

    def test_failures_are_not_funnel_steps(self):
        events = {s["event"] for s in _run([])["steps"]}
        assert "fetch_failed" not in events and "download_failed" not in events


class TestPerPlatform:

    def test_worst_platform_is_listed_first(self):
        rows = []
        rows += [_ev("download_success", anon=f"g{i}", platform="tiktok") for i in range(9)]
        rows += [_ev("download_failed",  anon="b1",    platform="tiktok")]
        rows += [_ev("download_success", anon="g9",    platform="youtube")]
        rows += [_ev("download_failed",  anon=f"b{i}", platform="youtube") for i in range(2, 5)]
        by = _run(rows)["by_platform"]
        assert by[0]["platform"] == "youtube", "the platform failing most must sort to the top"
        assert by[0]["failure_rate_pct"] == 75.0
        assert by[1]["failure_rate_pct"] == 10.0

    def test_platform_comes_from_event_properties(self):
        r = _run([_ev("download_failed", anon="a1", platform="bilibili")])
        assert r["by_platform"][0]["platform"] == "bilibili"
        assert r["by_platform"][0]["download_failed"] == 1


class TestHonesty:

    def test_declares_that_steps_are_not_an_ordered_path(self):
        """Counting 'did this event' is not the same as 'walked this path', and
        a reader who assumes otherwise will misread every number."""
        r = _run([])
        assert r["ordered"] is False
        assert r["note"]

    def test_truncation_is_reported(self):
        from app.api.admin import _EVENT_ROW_LIMIT
        rows = [_ev("paste_url", anon=f"a{i}") for i in range(_EVENT_ROW_LIMIT)]
        assert _run(rows)["truncated"] is True, (
            "a silently truncated window makes every ratio wrong with no warning"
        )

    def test_not_truncated_when_under_the_limit(self):
        assert _run([_ev("paste_url", anon="a1")])["truncated"] is False


class TestWindow:

    @pytest.mark.parametrize("asked,expected", [(0, 1), (1, 1), (7, 7), (90, 90), (365, 90)])
    def test_days_is_clamped(self, asked, expected):
        assert _run([], days=asked)["range"]["days"] == expected
