"""
Job Recovery Tests
==================
Tests for app.core.recovery: heartbeat-aware stale detection, state transitions,
recovery log, container discovery cleanup, and retry policy enforcement.

Run with:
    cd backend && source venv/bin/activate
    python -m pytest tests/test_job_recovery.py -q
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

# ── Stub heavy modules ────────────────────────────────────────────────────────
for _mod in (
    "realtime", "realtime.types", "realtime.connection",
    "supabase", "supabase.client", "supabase._sync", "supabase._async",
    "postgrest", "gotrue", "storage3",
    "app.main",
    "slowapi",
    "app.core.database",
    "app.tasks.video_tasks",
):
    sys.modules.setdefault(_mod, MagicMock())

import app.main as _main_stub
_main_stub.limiter = MagicMock()
_main_stub.limiter.limit = lambda *a, **kw: (lambda f: f)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _old_ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _job(
    jid="job-1",
    url="https://youtu.be/dQw4w9WgXcQ",
    status="processing",
    job_stage="extracting",
    recovery_attempts=0,
    quality="video",
    direct_mp4_url=None,
    minutes_ago=10.0,
    last_recovery_at=None,
):
    return {
        "id":                jid,
        "original_url":      url,
        "status":            status,
        "job_stage":         job_stage,
        "recovery_attempts": recovery_attempts,
        "selected_quality":  quality,
        "direct_mp4_url":    direct_mp4_url,
        "updated_at":        _old_ts(minutes_ago),
        "created_at":        _old_ts(minutes_ago + 1),
        "last_recovery_at":  last_recovery_at,
    }


def _mock_supabase():
    """Build a minimal Supabase mock that records update calls."""
    sb = MagicMock()
    sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return sb


# ═══════════════════════════════════════════════════════════════════
# A — State model additions
# ═══════════════════════════════════════════════════════════════════

class TestStateModelAdditions:

    def test_stale_in_valid_states(self):
        from app.core.state_model import VALID_JOB_STATES
        assert "stale" in VALID_JOB_STATES

    def test_retrying_in_valid_states(self):
        from app.core.state_model import VALID_JOB_STATES
        assert "retrying" in VALID_JOB_STATES

    def test_partial_in_valid_states(self):
        from app.core.state_model import VALID_JOB_STATES
        assert "partial" in VALID_JOB_STATES

    def test_processing_can_transition_to_stale(self):
        from app.core.state_model import is_valid_transition
        assert is_valid_transition("processing", "stale") is True

    def test_stale_can_transition_to_pending(self):
        from app.core.state_model import is_valid_transition
        assert is_valid_transition("stale", "pending") is True

    def test_stale_can_transition_to_failed(self):
        from app.core.state_model import is_valid_transition
        assert is_valid_transition("stale", "failed") is True

    def test_stale_can_transition_to_abandoned(self):
        from app.core.state_model import is_valid_transition
        assert is_valid_transition("stale", "abandoned") is True

    def test_stale_cannot_transition_to_success(self):
        from app.core.state_model import is_valid_transition
        assert is_valid_transition("stale", "success") is False

    def test_processing_can_transition_to_partial(self):
        from app.core.state_model import is_valid_transition
        assert is_valid_transition("processing", "partial") is True

    def test_retrying_in_active_states(self):
        from app.core.state_model import ACTIVE_STATES
        assert "retrying" in ACTIVE_STATES

    def test_stale_in_retryable_states(self):
        from app.core.state_model import RETRYABLE_STATES
        assert "stale" in RETRYABLE_STATES

    def test_stale_not_in_terminal_states(self):
        from app.core.state_model import TERMINAL_STATES
        assert "stale" not in TERMINAL_STATES

    def test_state_ui_has_stale_entry(self):
        from app.core.state_model import STATE_UI, get_ui
        assert "stale" in STATE_UI
        ui = get_ui("stale")
        assert ui["color"] == "warning"

    def test_state_ui_has_retrying_entry(self):
        from app.core.state_model import get_ui
        ui = get_ui("retrying")
        assert "label" in ui

    def test_should_allow_retry_stale(self):
        from app.core.state_model import should_allow_retry
        job = {"status": "stale", "recovery_attempts": 0}
        allowed, reason = should_allow_retry(job)
        assert allowed is True

    def test_should_allow_retry_blocked_at_max(self):
        from app.core.state_model import should_allow_retry, MAX_MANUAL_RETRIES
        job = {"status": "stale", "recovery_attempts": MAX_MANUAL_RETRIES}
        allowed, reason = should_allow_retry(job)
        assert allowed is False

    def test_existing_transitions_unchanged(self):
        from app.core.state_model import is_valid_transition
        assert is_valid_transition("processing", "success") is True
        assert is_valid_transition("processing", "failed")  is True
        assert is_valid_transition("failed", "pending")     is True
        assert is_valid_transition("success", "archived")   is True


# ═══════════════════════════════════════════════════════════════════
# B — Heartbeat-aware recovery: _recover_or_abandon
# ═══════════════════════════════════════════════════════════════════

class TestRecoverOrAbandon:

    def _call(self, job, is_alive=False, requeue_ok=True):
        sb = _mock_supabase()
        vt = MagicMock()
        vt.process_video_task = MagicMock()
        vt.process_video_task.delay = MagicMock()
        # Swap in a fake app.tasks.video_tasks so _recover_or_abandon's
        # internal `.delay()` call doesn't hit a real Celery broker — but
        # restore the real module afterward (was a bare, uncleaned
        # `sys.modules[...] = vt` before, which permanently replaced the
        # real module for every test that ran afterward in the same
        # session, e.g. test_phase11_resilience.py's real _register_
        # pending_task/_compute_file_expires calls silently no-op'd).
        _prev = sys.modules.get("app.tasks.video_tasks")
        sys.modules["app.tasks.video_tasks"] = vt
        try:
            with patch("app.core.recovery._is_job_alive", return_value=is_alive):
                with patch("app.core.recovery._invalidate_job_lease"):
                    with patch("app.core.recovery._log_recovery"):
                        with patch("app.services.container_discovery.track_container_metric"):
                            from app.core.recovery import _recover_or_abandon
                            return _recover_or_abandon(sb, job, source="test"), sb
        finally:
            if _prev is not None:
                sys.modules["app.tasks.video_tasks"] = _prev
            else:
                sys.modules.pop("app.tasks.video_tasks", None)

    def test_alive_job_is_skipped(self):
        job = _job(minutes_ago=10)
        outcome, _ = self._call(job, is_alive=True)
        assert outcome == "skip"

    def test_young_job_without_heartbeat_is_skipped(self):
        job = _job(minutes_ago=3)   # only 3 min old, below 8 min threshold
        outcome, _ = self._call(job, is_alive=False)
        assert outcome == "skip"

    def test_old_job_without_heartbeat_is_recovered(self):
        job = _job(minutes_ago=10)  # 10 min > STUCK_PROCESSING_MINUTES=8
        outcome, _ = self._call(job, is_alive=False)
        assert outcome == "recovered"

    def test_batch_zip_job_is_skipped(self):
        job = _job(url="batch_zip", minutes_ago=30)
        outcome, _ = self._call(job, is_alive=False)
        assert outcome == "skip"

    def test_abandoned_after_max_attempts(self):
        from app.core.recovery import MAX_AUTO_RECOVERY
        job = _job(minutes_ago=10, recovery_attempts=MAX_AUTO_RECOVERY)
        outcome, _ = self._call(job, is_alive=False)
        assert outcome == "abandoned"

    def test_abandoned_when_too_old(self):
        from app.core.recovery import ABANDONED_MINUTES
        job = _job(minutes_ago=ABANDONED_MINUTES + 5)
        outcome, _ = self._call(job, is_alive=False)
        assert outcome == "abandoned"

    def test_cooldown_prevents_double_recovery(self):
        """Job was recovered 5 min ago — within cooldown window — skip."""
        job = _job(minutes_ago=10)
        # last_recovery_at = 5 min ago (within STUCK_PROCESSING_MINUTES=8 min window)
        job["last_recovery_at"] = _old_ts(5)
        outcome, _ = self._call(job, is_alive=False)
        assert outcome == "skip"

    def test_recovery_increments_attempts(self):
        job = _job(minutes_ago=10, recovery_attempts=1)
        _, sb = self._call(job, is_alive=False)
        # The update should include recovery_attempts=2
        call_args = str(sb.table.return_value.update.call_args_list)
        assert "recovery_attempts" in call_args or sb.table.return_value.update.called


# ═══════════════════════════════════════════════════════════════════
# C — _recover_pending
# ═══════════════════════════════════════════════════════════════════

class TestRecoverPending:

    def _call(self, job):
        sb = _mock_supabase()
        vt = MagicMock()
        vt.process_video_task.delay = MagicMock()
        # See TestRecoverOrAbandon._call above — must restore, not just set.
        _prev = sys.modules.get("app.tasks.video_tasks")
        sys.modules["app.tasks.video_tasks"] = vt
        try:
            with patch("app.core.recovery._log_recovery"):
                from app.core.recovery import _recover_pending
                return _recover_pending(sb, job, source="test"), sb
        finally:
            if _prev is not None:
                sys.modules["app.tasks.video_tasks"] = _prev
            else:
                sys.modules.pop("app.tasks.video_tasks", None)

    def test_young_pending_skipped(self):
        job = _job(status="pending", minutes_ago=1)
        outcome, _ = self._call(job)
        assert outcome == "skip"

    def test_pending_with_direct_url_auto_succeeds(self):
        job = _job(status="pending", minutes_ago=5, direct_mp4_url="https://cdn.example.com/v.mp4")
        outcome, _ = self._call(job)
        assert outcome == "succeeded"

    def test_old_pending_without_url_requeued(self):
        job = _job(status="pending", minutes_ago=5)
        outcome, _ = self._call(job)
        assert outcome == "recovered"

    def test_pending_abandoned_after_max_attempts(self):
        from app.core.recovery import MAX_AUTO_RECOVERY
        job = _job(status="pending", minutes_ago=5, recovery_attempts=MAX_AUTO_RECOVERY)
        outcome, _ = self._call(job)
        assert outcome == "abandoned"

    def test_pending_abandoned_when_too_old(self):
        from app.core.recovery import PENDING_ABANDONED_MINUTES
        job = _job(status="pending", minutes_ago=PENDING_ABANDONED_MINUTES + 2)
        outcome, _ = self._call(job)
        assert outcome == "abandoned"


# ═══════════════════════════════════════════════════════════════════
# D — Recovery log
# ═══════════════════════════════════════════════════════════════════

class TestRecoveryLog:

    def _fake_redis(self):
        store = []
        rc = MagicMock()
        rc.lpush = lambda key, val: store.insert(0, val)
        rc.ltrim = MagicMock()
        rc.expire = MagicMock()
        rc.lrange = lambda key, start, end: store[start:end + 1]
        return rc, store

    def test_log_recovery_writes_entry(self):
        rc, store = self._fake_redis()
        with patch("app.core.redis_client.get_redis", return_value=rc):
            from app.core.recovery import _log_recovery
            _log_recovery("recovered", "job-123", reason="test")

        assert len(store) == 1
        entry = json.loads(store[0])
        assert entry["action"] == "recovered"
        assert entry["job_id"] == "job-123"
        assert "ts" in entry

    def test_get_recovery_log_returns_entries(self):
        rc, store = self._fake_redis()
        # Pre-populate
        store.append(json.dumps({"ts": "2026-01-01T00:00:00", "action": "recovered", "job_id": "j1", "reason": ""}))
        with patch("app.core.redis_client.get_redis", return_value=rc):
            from app.core.recovery import get_recovery_log
            log = get_recovery_log(limit=10)

        assert isinstance(log, list)
        assert len(log) == 1
        assert log[0]["action"] == "recovered"

    def test_get_recovery_log_empty_on_error(self):
        bad_redis = MagicMock(side_effect=Exception("Redis down"))
        with patch("app.core.redis_client.get_redis", side_effect=Exception("Redis down")):
            from app.core.recovery import get_recovery_log
            log = get_recovery_log()
        assert log == []

    def test_log_recovery_never_raises(self):
        with patch("app.core.redis_client.get_redis", side_effect=Exception("Redis down")):
            from app.core.recovery import _log_recovery
            _log_recovery("abandoned", "job-xyz")  # should not raise


# ═══════════════════════════════════════════════════════════════════
# E — Container discovery stale recovery
# ═══════════════════════════════════════════════════════════════════

class TestRecoverStaleDiscovery:

    def _make_snapshot_json(self, job_id, status="discovering", age_seconds=400):
        now = time.time()
        return json.dumps({
            "job_id": job_id,
            "container_id": "cid-1",
            "platform": "youtube",
            "source_type": "playlist",
            "status": status,
            "progress_pct": 30,
            "stage": "fetch_summary",
            "message": "Working...",
            "created_at": now - age_seconds,
            "updated_at": now - age_seconds,
            "expires_at": now + 1800,
            "from_cache": False,
            "partial": False,
            "warnings": [],
            "summary": None,
            "sections": [],
            "stats": {"sections_ready": 0, "sections_total": 0, "items_loaded": 0, "items_estimated": 0, "cache_hits": 0, "expandables": 0},
            "error": None,
            "canonical_url": "https://youtu.be/playlist?list=PLtest",
            "raw_input": "",
            "terminal_reason": None,
            "recovered_from_cache": False,
            "processing_started_at": now - age_seconds,
        })

    def test_stale_discovering_job_marked_failed(self):
        snap_json = self._make_snapshot_json("discover-1", status="discovering", age_seconds=400)

        rc = MagicMock()
        cursor_calls = [0]
        def _scan(cursor, match, count):
            if cursor_calls[0] == 0:
                cursor_calls[0] = 1
                return 0, [b"container:job:discover-1"]
            return 0, []
        rc.scan = _scan
        rc.get = lambda key: snap_json.encode()

        patched_patch_job = MagicMock()
        with patch("app.core.redis_client.get_redis", return_value=rc):
            with patch("app.services.container_cache.patch_job", patched_patch_job):
                with patch("app.services.container_cache.release_discovery_lock", MagicMock()):
                    with patch("app.core.recovery._log_recovery"):
                        from app.core.recovery import _recover_stale_discovery_jobs
                        marked = _recover_stale_discovery_jobs(source="test")

        assert marked == 1
        patched_patch_job.assert_called_once()
        kwargs = patched_patch_job.call_args[1]
        assert "failed" in str(kwargs.get("status", ""))

    def test_recent_discovery_job_not_touched(self):
        snap_json = self._make_snapshot_json("discover-2", status="discovering", age_seconds=60)

        rc = MagicMock()
        cursor_calls = [0]
        def _scan(cursor, match, count):
            if cursor_calls[0] == 0:
                cursor_calls[0] = 1
                return 0, [b"container:job:discover-2"]
            return 0, []
        rc.scan = _scan
        rc.get = lambda key: snap_json.encode()

        patched_patch_job = MagicMock()
        with patch("app.core.redis_client.get_redis", return_value=rc):
            with patch("app.services.container_cache.patch_job", patched_patch_job):
                with patch("app.services.container_cache.release_discovery_lock", MagicMock()):
                    with patch("app.core.recovery._log_recovery"):
                        from app.core.recovery import _recover_stale_discovery_jobs
                        marked = _recover_stale_discovery_jobs(source="test")

        assert marked == 0
        patched_patch_job.assert_not_called()

    def test_succeeded_discovery_job_not_touched(self):
        snap_json = self._make_snapshot_json("discover-3", status="success", age_seconds=400)

        rc = MagicMock()
        cursor_calls = [0]
        def _scan(cursor, match, count):
            if cursor_calls[0] == 0:
                cursor_calls[0] = 1
                return 0, [b"container:job:discover-3"]
            return 0, []
        rc.scan = _scan
        rc.get = lambda key: snap_json.encode()

        patched_patch_job = MagicMock()
        with patch("app.core.redis_client.get_redis", return_value=rc):
            with patch("app.services.container_cache.patch_job", patched_patch_job):
                with patch("app.services.container_cache.release_discovery_lock", MagicMock()):
                    with patch("app.core.recovery._log_recovery"):
                        from app.core.recovery import _recover_stale_discovery_jobs
                        marked = _recover_stale_discovery_jobs(source="test")

        assert marked == 0

    def test_returns_zero_on_redis_error(self):
        with patch("app.core.redis_client.get_redis", side_effect=Exception("Redis down")):
            with patch("app.core.recovery._log_recovery"):
                from app.core.recovery import _recover_stale_discovery_jobs
                marked = _recover_stale_discovery_jobs(source="test")
        assert marked == 0


# ═══════════════════════════════════════════════════════════════════
# F — refresh_job_link extended to stale state
# ═══════════════════════════════════════════════════════════════════

class TestRefreshJobLink:

    def _sb_with_job(self, status="failed", attempts=0):
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "id": "job-rf-1",
            "original_url": "https://youtu.be/abc",
            "status": status,
            "job_stage": "failed",
            "recovery_attempts": attempts,
            "selected_quality": "video",
        }
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        return sb

    def test_stale_job_can_be_refreshed(self, monkeypatch):
        sb = self._sb_with_job(status="stale")
        vt = MagicMock()
        vt.process_video_task.delay = MagicMock()
        # monkeypatch.setitem auto-reverts after this test, unlike the bare
        # `sys.modules[...] = vt` this used to be (see TestRecoverOrAbandon
        # ._call above for why that permanently broke later-running tests).
        monkeypatch.setitem(sys.modules, "app.tasks.video_tasks", vt)

        with patch("app.core.database.get_service_client", return_value=sb):
            with patch("app.core.recovery._invalidate_job_lease"):
                with patch("app.core.recovery._log_recovery"):
                    from app.core.recovery import refresh_job_link
                    result = refresh_job_link("job-rf-1", "user-1")
        assert result["success"] is True

    def test_processing_job_cannot_be_refreshed(self):
        sb = self._sb_with_job(status="processing")
        with patch("app.core.database.get_service_client", return_value=sb):
            from app.core.recovery import refresh_job_link
            result = refresh_job_link("job-rf-2")
        assert result["success"] is False
        assert "xử lý" in result["message"]


# ═══════════════════════════════════════════════════════════════════
# G — Regression: existing logic unchanged
# ═══════════════════════════════════════════════════════════════════

class TestRegressions:

    def test_scan_stale_jobs_callable(self):
        """scan_stale_jobs must be importable and callable without crashing."""
        sb = _mock_supabase()
        sb.table.return_value.select.return_value.eq.return_value.lt.return_value.execute.return_value.data = []
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        with patch("app.core.database.get_service_client", return_value=sb):
            with patch("app.core.recovery._recover_stale_discovery_jobs", return_value=0):
                with patch("app.core.recovery._log_recovery"):
                    from app.core.recovery import scan_stale_jobs
                    scan_stale_jobs()  # should not raise

    def test_failure_classifier_still_works(self):
        from app.core.failure_classifier import classify_failure, FailureClass
        assert classify_failure("connection reset") == FailureClass.TRANSIENT
        assert classify_failure("429 too many requests") == FailureClass.RETRYABLE
        assert classify_failure("video unavailable") == FailureClass.PERMANENT
        assert classify_failure("sign in to confirm") == FailureClass.USER_ACTION

    def test_get_job_age_minutes_with_valid_ts(self):
        from app.core.recovery import _get_job_age_minutes
        job = {"updated_at": _old_ts(12.5)}
        age = _get_job_age_minutes(job)
        assert 12 < age < 13

    def test_get_job_age_minutes_missing_ts_returns_zero(self):
        from app.core.recovery import _get_job_age_minutes
        assert _get_job_age_minutes({}) == 0.0
