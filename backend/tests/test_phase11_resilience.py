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
    """
    The worker-outage alert used to live inside worker_heartbeat_check, which
    is itself a Celery task — it only ran when a worker was alive to run it, so
    the alert could never fire. Detection now lives in the API process
    (alerts.check_worker_liveness) and reads a beacon the workers publish.

    These tests keep the original intent — "beacon gets written", "a stale
    beacon raises the alarm" — but point it at the half that can actually fire.
    """

    @staticmethod
    def _fake_redis(monkeypatch):
        import fakeredis
        fake_rc = fakeredis.FakeStrictRedis(decode_responses=True)
        # Two patch targets on purpose: queue_intelligence binds get_redis at
        # import time (`from ... import get_redis`), so patching the source
        # module alone misses it, while alerts.py and the heartbeat task import
        # it inside their functions and only see the source module.
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_rc)
        monkeypatch.setattr("app.core.queue_intelligence.get_redis", lambda: fake_rc)
        return fake_rc

    @staticmethod
    def _capture_alerts(monkeypatch):
        """Capture at the Telegram boundary and clear the cooldown memo."""
        from app.core import alerts as _alerts
        _alerts._last_alert.clear()
        sent = []
        monkeypatch.setattr(_alerts, "send_telegram_message_sync", lambda msg, **kw: sent.append(msg))
        return sent

    # ── the worker half: publish the beacon ───────────────────────────
    def test_running_worker_publishes_beacon(self, monkeypatch):
        fake_rc = self._fake_redis(monkeypatch)
        # The task also refreshes the cached worker count via a control-plane
        # ping; stub it so the test does not need a live broker.
        monkeypatch.setattr(
            "app.core.queue_intelligence._get_active_workers",
            lambda force_refresh=False: 2,
        )

        from app.tasks.video_tasks import worker_heartbeat_check
        # bind=True task called directly — Celery supplies `self`.
        worker_heartbeat_check()

        beacon = fake_rc.get("vidgrab:last_worker_seen_at")
        assert beacon is not None, "a running worker must publish its liveness beacon"
        parsed = datetime.fromisoformat(beacon)
        assert (datetime.now(timezone.utc) - parsed).total_seconds() < 60

    # ── the API half: notice the beacon went stale ────────────────────
    def test_stale_beacon_sends_alert(self, monkeypatch):
        fake_rc = self._fake_redis(monkeypatch)
        fake_rc.set(
            "vidgrab:last_worker_seen_at",
            (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        )
        sent = self._capture_alerts(monkeypatch)

        from app.core.alerts import check_worker_liveness
        check_worker_liveness()

        assert len(sent) == 1, "a 10-minute-old beacon must raise the worker alarm"
        assert "No Celery Workers" in sent[0]

    def test_missing_beacon_sends_alert(self, monkeypatch):
        self._fake_redis(monkeypatch)          # empty Redis: no beacon at all
        sent = self._capture_alerts(monkeypatch)

        from app.core.alerts import check_worker_liveness
        check_worker_liveness()

        assert len(sent) == 1
        assert "No Celery Workers" in sent[0]

    def test_fresh_beacon_stays_quiet(self, monkeypatch):
        fake_rc = self._fake_redis(monkeypatch)
        fake_rc.set("vidgrab:last_worker_seen_at", datetime.now(timezone.utc).isoformat())
        sent = self._capture_alerts(monkeypatch)

        from app.core.alerts import check_worker_liveness
        check_worker_liveness()

        assert sent == [], "a live worker pool must not page anyone"

    def test_unreachable_redis_does_not_cry_wolf(self, monkeypatch):
        def _boom():
            raise ConnectionError("redis down")
        monkeypatch.setattr("app.core.redis_client.get_redis", _boom)
        sent = self._capture_alerts(monkeypatch)

        from app.core.alerts import check_worker_liveness
        check_worker_liveness()

        assert sent == [], "a Redis outage is not evidence that workers are gone"

    def test_only_one_process_alerts_per_cooldown(self, monkeypatch):
        """
        The container runs uvicorn --workers 2, so two API processes evaluate
        this same condition every interval. The cooldown therefore has to be
        shared, not per-process, or one outage pages twice per window.

        A second process is simulated by clearing the in-process fallback dict
        between the two calls while keeping the same Redis — calling twice
        without that proves nothing, because the module-level dict would
        suppress the repeat on its own and the test would pass even with the
        shared lock removed.
        """
        from app.core import alerts as _alerts

        fake_rc = self._fake_redis(monkeypatch)
        fake_rc.set(
            "vidgrab:last_worker_seen_at",
            (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        )
        sent = self._capture_alerts(monkeypatch)

        from app.core.alerts import check_worker_liveness
        check_worker_liveness()          # process A
        _alerts._last_alert.clear()      # process B starts with its own empty dict
        check_worker_liveness()          # ...but the same Redis cooldown

        assert len(sent) == 1, (
            f"expected one alert per cooldown across processes, got {len(sent)} — "
            "the cooldown is not shared"
        )

    # ── the watchdog must be able to answer "am I running?" ───────────
    def test_watchdog_status_reports_alive_only_after_a_real_check(self, monkeypatch):
        """
        The first attempt at proving the watchdog runs was a one-shot log line.
        This container's log window holds ~3 minutes, so that line is gone
        before anyone asks — unverifiable observability is the exact failure
        that let the checks this replaced sit dead. /health now answers instead.
        """
        fake_rc = self._fake_redis(monkeypatch)
        from app import main as _main
        monkeypatch.setattr("app.core.redis_client.get_redis", lambda: fake_rc)

        before = _main._watchdog_status()
        assert before["enabled"] is True
        assert before["alive"] is False, "must not claim to be alive before running"
        assert before["last_run_at"] is None

        _main._publish_watchdog_beat()

        after = _main._watchdog_status()
        assert after["alive"] is True
        assert after["last_run_at"] is not None
        # And the beat expires, so a stopped watchdog stops reading as alive
        # instead of leaving a stale success behind.
        assert fake_rc.ttl(_main.WATCHDOG_BEAT_KEY) > 0

    # ── the count itself must be able to say "none" and "unknown" ─────
    def test_worker_count_can_report_zero_and_unknown(self, monkeypatch):
        fake_rc = self._fake_redis(monkeypatch)
        from app.core import queue_intelligence as qi

        # No cached value yet → unknown, never a made-up 1.
        assert qi._get_active_workers() == -1

        fake_rc.set(qi.WORKER_COUNT_CACHE_KEY, 0)
        assert qi._get_active_workers() == 0, "zero workers must be reportable"

        fake_rc.set(qi.WORKER_COUNT_CACHE_KEY, 3)
        assert qi._get_active_workers() == 3


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

        # Returns an int, not a list — main.py used to call len() on it, which
        # raised TypeError and pinned /health at worker_count: -1.
        monkeypatch.setattr(
            "app.core.queue_intelligence._get_active_workers",
            lambda force_refresh=False: 1,
        )

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
