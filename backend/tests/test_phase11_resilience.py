"""
Phase 11 — SRE Resilience Tests
================================
Covers: flexible file expiry, retry classification tracking, disk pre-flight,
        Redis pending_tasks SET, idempotency, cleanup pass, health endpoint shape,
        worker heartbeat, and batch progress state.

All tests are unit-level: no real DB, no real Redis (fakeredis where needed).
"""

import hashlib
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest


# ─── 1. Flexible file expiry ──────────────────────────────────────────────────

class TestFileExpiry:
    def test_single_job_default_expiry(self, monkeypatch):
        monkeypatch.setenv("FILE_EXPIRY_SINGLE_MIN", "20")
        monkeypatch.setenv("FILE_EXPIRY_PRO_BONUS_MIN", "30")
        from app.tasks.video_tasks import _compute_file_expires
        before = datetime.now(timezone.utc)
        result = _compute_file_expires("single", "free")
        after = datetime.now(timezone.utc)
        exp = datetime.fromisoformat(result)
        # Should be ~20 min from now
        assert timedelta(minutes=19) < (exp - before) <= timedelta(minutes=21)

    def test_bulk_job_longer_expiry(self, monkeypatch):
        monkeypatch.setenv("FILE_EXPIRY_BULK_MIN", "30")
        monkeypatch.setenv("FILE_EXPIRY_SINGLE_MIN", "20")
        from app.tasks.video_tasks import _compute_file_expires
        single_exp = datetime.fromisoformat(_compute_file_expires("single", "free"))
        bulk_exp   = datetime.fromisoformat(_compute_file_expires("bulk",   "free"))
        assert bulk_exp > single_exp

    def test_artist_all_longest_expiry(self, monkeypatch):
        monkeypatch.setenv("FILE_EXPIRY_ARTIST_MIN", "45")
        monkeypatch.setenv("FILE_EXPIRY_BULK_MIN", "30")
        from app.tasks.video_tasks import _compute_file_expires
        bulk_exp   = datetime.fromisoformat(_compute_file_expires("bulk",       "free"))
        artist_exp = datetime.fromisoformat(_compute_file_expires("artist_all", "free"))
        assert artist_exp > bulk_exp

    def test_pro_bonus_added(self, monkeypatch):
        monkeypatch.setenv("FILE_EXPIRY_SINGLE_MIN",  "20")
        monkeypatch.setenv("FILE_EXPIRY_PRO_BONUS_MIN", "30")
        from app.tasks.video_tasks import _compute_file_expires
        free_exp = datetime.fromisoformat(_compute_file_expires("single", "free"))
        pro_exp  = datetime.fromisoformat(_compute_file_expires("single", "pro"))
        # Pro should be ~30 min longer
        diff = (pro_exp - free_exp).total_seconds()
        assert 28 * 60 < diff < 32 * 60

    def test_unknown_job_type_falls_back_to_single(self, monkeypatch):
        monkeypatch.setenv("FILE_EXPIRY_SINGLE_MIN", "20")
        from app.tasks.video_tasks import _compute_file_expires
        result_unknown = _compute_file_expires("unknown_type", "free")
        result_single  = _compute_file_expires("single", "free")
        # Both should be within 2s of each other (same base)
        diff = abs(
            datetime.fromisoformat(result_unknown).timestamp() -
            datetime.fromisoformat(result_single).timestamp()
        )
        assert diff < 2


# ─── 2. Redis pending tasks SET ───────────────────────────────────────────────

class TestPendingTasksSet:
    def test_register_adds_to_set(self, monkeypatch):
        import fakeredis
        fake_rc = fakeredis.FakeStrictRedis(decode_responses=True)
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_rc)
        from app.tasks.video_tasks import _register_pending_task
        _register_pending_task("task-abc-123")
        assert fake_rc.sismember("vidgrab:pending_tasks", "task-abc-123")

    def test_deregister_removes_from_set(self, monkeypatch):
        import fakeredis
        fake_rc = fakeredis.FakeStrictRedis(decode_responses=True)
        fake_rc.sadd("vidgrab:pending_tasks", "task-xyz")
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_rc)
        from app.tasks.video_tasks import _deregister_pending_task
        _deregister_pending_task("task-xyz")
        assert not fake_rc.sismember("vidgrab:pending_tasks", "task-xyz")

    def test_register_is_idempotent(self, monkeypatch):
        import fakeredis
        fake_rc = fakeredis.FakeStrictRedis(decode_responses=True)
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_rc)
        from app.tasks.video_tasks import _register_pending_task
        _register_pending_task("task-dup")
        _register_pending_task("task-dup")
        assert fake_rc.scard("vidgrab:pending_tasks") == 1

    def test_redis_unavailable_does_not_raise(self, monkeypatch):
        def _boom():
            raise ConnectionError("Redis down")
        monkeypatch.setattr("app.core.redis_client.get_redis", _boom)
        from app.tasks.video_tasks import _register_pending_task, _deregister_pending_task
        _register_pending_task("task-1")    # must not raise
        _deregister_pending_task("task-1")  # must not raise


# ─── 3. _sb_update graceful column fallback ───────────────────────────────────

class TestSbUpdateFallback:
    def test_strips_missing_column_on_error(self, monkeypatch):
        import app.tasks.video_tasks as vt
        # Reset global state
        vt._MISSING_COLS.clear()

        calls = []
        def _mock_update(data):
            nonlocal calls
            calls.append(set(data.keys()))
            if "retry_count" in data:
                raise Exception("column retry_count does not exist")

        mock_sb = MagicMock()
        mock_sb.table.return_value.update.side_effect = lambda d: type("R", (), {"eq": lambda s, f, v: type("R2", (), {"execute": lambda s: _mock_update(d)})()})()

        # Use a simpler mock approach
        update_calls = []
        class _FakeChain:
            def __init__(self, data):
                self._data = data
            def eq(self, *a):
                return self
            def execute(self):
                update_calls.append(set(self._data.keys()))
                if "retry_count" in self._data:
                    raise Exception("column retry_count of relation download_jobs does not exist")

        class _FakeSB:
            def table(self, name):
                return self
            def update(self, data):
                return _FakeChain(data)

        vt._MISSING_COLS.clear()
        vt._sb_update(_FakeSB(), {"status": "failed", "retry_count": 3}, "job-1")

        # First call included retry_count (failed), second call should have stripped it
        assert len(update_calls) == 2
        assert "retry_count" not in update_calls[1]
        assert "status" in update_calls[1]

    def test_raises_on_non_optional_column_error(self, monkeypatch):
        import app.tasks.video_tasks as vt
        vt._MISSING_COLS.clear()

        class _FailSB:
            def table(self, *a):
                return self
            def update(self, *a):
                return self
            def eq(self, *a):
                return self
            def execute(self):
                raise Exception("real DB error: network timeout")

        with pytest.raises(Exception, match="network timeout"):
            vt._sb_update(_FailSB(), {"status": "failed"}, "job-2")


# ─── 4. Disk pre-flight check ─────────────────────────────────────────────────

class TestDiskPreflightCheck:
    def test_rejects_when_over_90_percent(self, monkeypatch):
        monkeypatch.setattr("shutil.disk_usage", lambda _: (100 * 1024**3, 92 * 1024**3, 8 * 1024**3))
        from fastapi import HTTPException
        from app.api.routes import _preflight_disk_check
        with pytest.raises(HTTPException) as exc:
            _preflight_disk_check()
        assert exc.value.status_code == 507
        assert exc.value.detail["error_code"] == "disk_full"

    def test_rejects_when_under_500mb_free(self, monkeypatch):
        monkeypatch.setattr("shutil.disk_usage", lambda _: (100 * 1024**3, 99_700 * 1024**2, 300 * 1024**2))
        from fastapi import HTTPException
        from app.api.routes import _preflight_disk_check
        with pytest.raises(HTTPException) as exc:
            _preflight_disk_check()
        assert exc.value.status_code == 507

    def test_passes_with_healthy_disk(self, monkeypatch):
        monkeypatch.setattr("shutil.disk_usage", lambda _: (100 * 1024**3, 50 * 1024**3, 50 * 1024**3))
        from app.api.routes import _preflight_disk_check
        _preflight_disk_check()  # should not raise


# ─── 5. Retry tracking columns in failure path ────────────────────────────────

class TestRetryTracking:
    def test_permanent_failure_sets_error_type(self):
        from app.core.failure_classifier import classify_failure, FailureClass
        fc = classify_failure("Video unavailable — private or deleted")
        assert fc in (FailureClass.PERMANENT, FailureClass.USER_ACTION)

    def test_transient_failure_sets_retryable(self):
        from app.core.failure_classifier import classify_failure, FailureClass
        fc = classify_failure("HTTP Error 429: Too Many Requests")
        assert fc in (FailureClass.TRANSIENT, FailureClass.RETRYABLE)

    def test_backoff_increases_with_retries(self):
        from app.core.failure_classifier import backoff_seconds, FailureClass
        b0 = backoff_seconds(0, FailureClass.TRANSIENT)
        b1 = backoff_seconds(1, FailureClass.TRANSIENT)
        b2 = backoff_seconds(2, FailureClass.TRANSIENT)
        assert b1 > b0
        assert b2 > b1


# ─── 6. Worker heartbeat check ────────────────────────────────────────────────

class TestWorkerHeartbeat:
    def test_healthy_workers_update_redis(self, monkeypatch):
        import fakeredis
        fake_rc = fakeredis.FakeStrictRedis(decode_responses=True)
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_rc)
        monkeypatch.setattr("app.core.queue_intelligence._get_active_workers", lambda: ["w1", "w2"])

        from app.tasks.video_tasks import worker_heartbeat_check
        # worker_heartbeat_check(self) is a bind=True Celery task — calling
        # the Celery-wrapped proxy directly (not via .delay()/.apply_async())
        # already supplies `self` automatically, so passing an extra `ctx`
        # positional arg here raised "takes 1 positional argument but 2 were
        # given". The function body doesn't reference self.request/anything
        # from a context object, so no replacement arg is needed either.
        worker_heartbeat_check()

        assert fake_rc.get("vidgrab:last_worker_seen_at") is not None

    def test_no_workers_sends_alert_after_5min(self, monkeypatch):
        import fakeredis
        fake_rc = fakeredis.FakeStrictRedis(decode_responses=True)
        # Simulate last_worker_seen_at was 10 min ago
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        fake_rc.set("vidgrab:last_worker_seen_at", old_time)
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_rc)
        monkeypatch.setattr("app.core.queue_intelligence._get_active_workers", lambda: [])

        alert_sent = []
        monkeypatch.setattr(
            "app.core.notifications.send_telegram_message_sync",
            lambda msg, **kw: alert_sent.append(msg),
        )

        from app.tasks.video_tasks import worker_heartbeat_check
        # See test_healthy_workers_update_redis — no explicit arg needed.
        worker_heartbeat_check()

        assert len(alert_sent) == 1
        assert "No Workers" in alert_sent[0]


# ─── 7. Health endpoint shape ─────────────────────────────────────────────────

class TestHealthEndpointShape:
    def test_health_contains_phase11_fields(self, monkeypatch):
        """Health response must include celery, jobs, and youtube_circuit_breaker keys."""
        import json

        # Minimal mocks to avoid real IO
        monkeypatch.setattr("shutil.disk_usage", lambda _: (100 * 1024**3, 40 * 1024**3, 60 * 1024**3))
        monkeypatch.setattr("importlib.metadata.version", lambda _: "2025.01.01")

        import redis as _r_mod
        fake_ping = MagicMock()
        fake_rc = MagicMock()
        fake_rc.ping.return_value = True
        fake_rc.llen.return_value = 0
        fake_rc.scard.return_value = 0
        fake_rc.get.return_value = None
        monkeypatch.setattr(_r_mod, "from_url", lambda *a, **kw: fake_rc)

        monkeypatch.setattr("app.core.queue_intelligence._get_active_workers", lambda: ["w1"])

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.in_.return_value.execute.return_value.count = 2
        mock_sb.table.return_value.select.return_value.eq.return_value.lt.return_value.execute.return_value.count = 0
        monkeypatch.setattr("app.core.database.get_service_client", lambda: mock_sb)

        # app.main.health_check calls youtube_gate.circuit_state() (a plain
        # string), not a get_status() dict wrapper — that function never
        # existed, which is why this field was silently stuck at "unknown"
        # in production behind main.py's blanket except. Fixed alongside this test.
        monkeypatch.setattr("app.core.youtube_gate.circuit_state", lambda: "closed")

        from app.main import health_check
        import asyncio
        # asyncio.get_event_loop() is deprecated/fragile in 3.12+: it only
        # auto-creates a loop the FIRST time it's called with none set for
        # the thread. Once some earlier test in the suite has created and
        # closed a loop (e.g. via TestClient/httpx making an async request
        # against a different endpoint), later calls raise "RuntimeError:
        # There is no current event loop" instead — exactly the failure this
        # test showed intermittently depending on what ran before it in the
        # full suite. asyncio.run() always creates a fresh loop and closes
        # it cleanly, with no dependency on prior thread-local loop state.
        result = asyncio.run(health_check())

        assert "celery" in result
        assert "jobs" in result
        assert "youtube_circuit_breaker" in result
        assert result["celery"]["worker_count"] == 1
