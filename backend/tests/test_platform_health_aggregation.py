"""
aggregate_platform_health_task.

It selected download_jobs.duration_ms, a column that does not exist, so
PostgREST rejected the whole query (42703). The task had been failing and
retrying every 300s and platform_health_metrics was never written — found in the
production logs. A second fault sat behind it: get_redis() uses
decode_responses=True, so the circuit-state value is already a str and the
.decode() call raised AttributeError, which would have kept the task broken even
once the column was fixed.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


def _iso(dt):
    return dt.isoformat()


@pytest.fixture
def wired(monkeypatch):
    """Capture the selected columns and whatever gets upserted."""
    seen = {"select": None, "upserts": []}
    now = datetime.now(timezone.utc)

    sb = MagicMock()
    def _select(cols):
        seen["select"] = cols
        q = MagicMock()
        q.gt.return_value.execute.return_value.data = [
            # completed 2s after creation
            {"platform": "tiktok", "status": "completed",
             "created_at": _iso(now - timedelta(seconds=12)),
             "completed_at": _iso(now - timedelta(seconds=10)),
             "error_message": None, "updated_at": _iso(now)},
            # failed, with an error and a 4s runtime
            {"platform": "tiktok", "status": "failed",
             "created_at": _iso(now - timedelta(seconds=9)),
             "completed_at": _iso(now - timedelta(seconds=5)),
             "error_message": "boom", "updated_at": _iso(now)},
            # still running — must not count toward duration
            {"platform": "tiktok", "status": "processing",
             "created_at": _iso(now - timedelta(seconds=3)),
             "completed_at": None,
             "error_message": None, "updated_at": _iso(now)},
        ]
        return q

    table = MagicMock()
    table.select.side_effect = _select
    table.upsert.side_effect = lambda payload, **kw: (
        seen["upserts"].append(payload) or MagicMock())
    sb.table.return_value = table

    monkeypatch.setattr("app.core.database.get_supabase_client", lambda: sb)
    # decode_responses=True — values come back as str, never bytes.
    fake_r = MagicMock()
    fake_r.get.return_value = "open"
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_r)
    return seen


def test_does_not_select_the_nonexistent_duration_column(wired):
    from app.tasks.video_tasks import aggregate_platform_health_task
    aggregate_platform_health_task()
    assert wired["select"] is not None, "the query never ran"
    assert "duration_ms" not in wired["select"], (
        f"still selecting a column download_jobs does not have: {wired['select']}"
    )
    for needed in ("created_at", "completed_at"):
        assert needed in wired["select"], f"{needed} is required to derive duration"


def test_duration_is_derived_and_ignores_unfinished_jobs(wired):
    from app.tasks.video_tasks import aggregate_platform_health_task
    aggregate_platform_health_task()
    assert wired["upserts"], "nothing was written to platform_health_metrics"
    row = wired["upserts"][0]
    assert row["total_jobs"] == 3
    assert row["success_jobs"] == 1
    assert row["failed_jobs"] == 1
    # 2000ms and 4000ms; the unfinished job contributes nothing.
    assert row["avg_duration_ms"] == 3000, row["avg_duration_ms"]


def test_str_circuit_state_does_not_raise(wired):
    """decode_responses=True means .decode() on this value is an AttributeError."""
    from app.tasks.video_tasks import aggregate_platform_health_task
    aggregate_platform_health_task()
    assert wired["upserts"][0]["circuit_opens"] == 1
