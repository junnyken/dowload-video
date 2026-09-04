"""
The stale-job watchdog could never fire.

get_stale_job_count() asked for status="stale". There is no such status — the
job_status enum holds pending/processing/success/failed, and recovery.py says
so in a comment before marking stale jobs with job_stage="stale" while leaving
status at "processing". Postgres rejected the comparison, Supabase answered
400, a bare except turned that into -1, and every caller reads -1 as "database
unavailable":

    alerts.check_stale_jobs()   `if count < 0: return`      — never alerted
    /ops                        `if stale_count > 0`        — never warned,
                                and published stale_job_count: -1

Confirmed on a clean production container: GET /health's own count read 0 while
this path returned -1, because /health asks a different question ("processing
and untouched for 8 minutes") through a different query that was always right.

A watchdog that cannot fire is worse than none, because its silence reads as
"nothing is wrong".
"""

from __future__ import annotations

import pytest


class _Result:
    def __init__(self, count):
        self.count = count
        self.data = []


class _FakeQuery:
    """Records the filters applied so the test can assert on the query shape."""

    def __init__(self, recorder, count=0, raise_on_execute=None):
        self._rec = recorder
        self._count = count
        self._raise = raise_on_execute

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._rec.setdefault("eq", []).append((field, value))
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if self._raise:
            raise self._raise
        return _Result(self._count)


class _FakeClient:
    def __init__(self, recorder, count=0, raise_on_execute=None):
        self._rec = recorder
        self._count = count
        self._raise = raise_on_execute

    def table(self, name):
        self._rec["table"] = name
        return _FakeQuery(self._rec, self._count, self._raise)


@pytest.fixture
def patched(monkeypatch):
    """Swap the Supabase client for a recorder. Returns (call, recorder)."""
    import app.core.database as db

    def _install(count=0, raise_on_execute=None):
        rec = {}
        monkeypatch.setattr(
            db, "get_service_client",
            lambda: _FakeClient(rec, count, raise_on_execute),
        )
        return rec

    return _install


class TestTheQueryMatchesHowStaleIsActuallyRecorded:

    def test_it_no_longer_asks_for_a_status_that_does_not_exist(self, patched):
        from app.core.metrics import get_stale_job_count

        rec = patched(count=0)
        get_stale_job_count()

        assert ("status", "stale") not in rec.get("eq", []), (
            "still filtering status=stale — that value is not in the "
            "job_status enum, so Postgres rejects the query"
        )

    def test_it_filters_the_way_recovery_marks_them(self, patched):
        """recovery.py sets job_stage='stale' and leaves status='processing'."""
        from app.core.metrics import get_stale_job_count

        rec = patched(count=0)
        get_stale_job_count()

        applied = rec.get("eq", [])
        assert ("job_stage", "stale") in applied
        assert ("status", "processing") in applied
        assert rec["table"] == "download_jobs"

    def test_it_returns_the_count(self, patched):
        from app.core.metrics import get_stale_job_count

        patched(count=7)
        assert get_stale_job_count() == 7


class TestTheAlertCanNowFire:
    """The point of the fix. Counting correctly is only useful if the alert
    that reads the count stops being short-circuited by -1."""

    def test_a_healthy_system_does_not_alert(self, patched, monkeypatch):
        import app.core.alerts as alerts

        patched(count=0)
        sent = []
        monkeypatch.setattr(alerts, "send_admin_alert", lambda *a, **k: sent.append(a))
        alerts.check_stale_jobs()
        assert sent == []

    def test_a_pile_of_stale_jobs_does_alert(self, patched, monkeypatch):
        import app.core.alerts as alerts

        patched(count=alerts.STALE_JOB_CRITICAL)
        sent = []
        monkeypatch.setattr(alerts, "send_admin_alert", lambda *a, **k: sent.append(a))
        alerts.check_stale_jobs()
        assert sent, (
            "the stale-job alert still does not fire — this is the bug, not "
            "the query shape"
        )

    def test_a_real_outage_still_suppresses_the_alert(self, patched, monkeypatch):
        """-1 must keep meaning "cannot tell", so a database outage does not
        page anyone about stale jobs it never counted."""
        import app.core.alerts as alerts

        patched(raise_on_execute=RuntimeError("db down"))
        sent = []
        monkeypatch.setattr(alerts, "send_admin_alert", lambda *a, **k: sent.append(a))
        alerts.check_stale_jobs()
        assert sent == []


class TestFailuresAreNoLongerSilent:

    def test_the_reason_is_printed(self, patched, capsys):
        """Swallowing the exception is how a broken query stayed invisible for
        as long as it did."""
        from app.core.metrics import get_stale_job_count

        patched(raise_on_execute=RuntimeError("boom-42"))
        assert get_stale_job_count() == -1
        assert "boom-42" in capsys.readouterr().out
