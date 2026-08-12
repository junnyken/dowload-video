"""
Job Lease & Heartbeat Tests
============================
Tests for app.core.job_lease: heartbeat primitives, lease acquire/renew/release,
duplicate-execution guard, and JobLease context manager.

Run with:
    cd backend && source venv/bin/activate
    python -m pytest tests/test_job_lease.py -q
"""

from __future__ import annotations

import sys
import time
import threading
from unittest.mock import MagicMock, patch

import pytest

# ── Stub heavy modules ────────────────────────────────────────────────────────
for _mod in (
    "realtime", "realtime.types", "realtime.connection",
    "supabase", "supabase.client", "supabase._sync", "supabase._async",
    "postgrest", "gotrue", "storage3",
    "app.main",
    "slowapi",
    "app.core.database",
):
    sys.modules.setdefault(_mod, MagicMock())

import app.main as _main_stub
_main_stub.limiter = MagicMock()
_main_stub.limiter.limit = lambda *a, **kw: (lambda f: f)


# ── Fake Redis ────────────────────────────────────────────────────────────────

class FakeRedis:
    """In-memory Redis mock for lease tests. Thread-safe enough for these cases."""

    def __init__(self):
        self._store: dict = {}  # key → (value, expire_at or None)
        self._lock = threading.Lock()

    def _now(self):
        return time.time()

    def _is_expired(self, key):
        if key not in self._store:
            return True
        _, exp = self._store[key]
        return exp is not None and self._now() > exp

    def set(self, key, value, ex=None, nx=False):
        with self._lock:
            if nx and key in self._store and not self._is_expired(key):
                return None  # NX failed
            expire_at = (self._now() + ex) if ex else None
            self._store[key] = (value, expire_at)
            return True

    def get(self, key):
        with self._lock:
            if self._is_expired(key):
                self._store.pop(key, None)
                return None
            val, _ = self._store[key]
            if isinstance(val, str):
                return val.encode()
            return val

    def delete(self, *keys):
        with self._lock:
            for k in keys:
                self._store.pop(k, None)

    def setex(self, key, ttl, value):
        self.set(key, value, ex=ttl)

    def expire(self, key, ttl):
        with self._lock:
            if key in self._store:
                val, _ = self._store[key]
                self._store[key] = (val, self._now() + ttl)


def _make_redis_patch(fake=None):
    if fake is None:
        fake = FakeRedis()
    return patch("app.core.job_lease._redis", return_value=fake), fake


# ═══════════════════════════════════════════════════════════════════
# A — Heartbeat primitives
# ═══════════════════════════════════════════════════════════════════

class TestHeartbeatPrimitives:

    def test_set_and_get_heartbeat(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import set_heartbeat, get_heartbeat
            set_heartbeat("job-1")
            raw = get_heartbeat("job-1")
        assert raw is not None
        assert "T" in raw or "-" in raw  # ISO timestamp

    def test_get_missing_heartbeat_returns_none(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import get_heartbeat
            result = get_heartbeat("no-such-job")
        assert result is None

    def test_clear_heartbeat_removes_key(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import set_heartbeat, get_heartbeat, clear_heartbeat
            set_heartbeat("job-2")
            assert get_heartbeat("job-2") is not None
            clear_heartbeat("job-2")
            assert get_heartbeat("job-2") is None

    def test_is_alive_true_with_recent_heartbeat(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import set_heartbeat, is_alive
            set_heartbeat("job-3")
            assert is_alive("job-3", threshold_seconds=30) is True

    def test_is_alive_false_when_no_heartbeat(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import is_alive
            assert is_alive("ghost-job") is False

    def test_is_alive_false_with_old_timestamp(self):
        """Heartbeat key exists but timestamp is stale (backdated)."""
        fake = FakeRedis()
        from datetime import datetime, timezone, timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        fake.setex("vidgrab:job_hb:job-old", 300, old_ts)

        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import is_alive
            assert is_alive("job-old", threshold_seconds=90) is False

    def test_set_heartbeat_never_raises_on_redis_error(self):
        bad_redis = MagicMock(side_effect=Exception("Redis down"))
        with patch("app.core.job_lease._redis", return_value=bad_redis):
            from app.core.job_lease import set_heartbeat
            set_heartbeat("job-x")  # should not raise


# ═══════════════════════════════════════════════════════════════════
# B — Lease primitives
# ═══════════════════════════════════════════════════════════════════

class TestLeasePrimitives:

    def test_acquire_lease_first_time_succeeds(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease
            result = acquire_lease("job-A", "worker-1")
        assert result is True

    def test_acquire_lease_same_worker_returns_true(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease
            acquire_lease("job-A", "worker-1")
            result = acquire_lease("job-A", "worker-1")
        assert result is True

    def test_acquire_lease_different_worker_returns_false(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease
            acquire_lease("job-B", "worker-1")
            result = acquire_lease("job-B", "worker-2")
        assert result is False

    def test_release_lease_frees_for_next_worker(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease, release_lease
            acquire_lease("job-C", "worker-1")
            release_lease("job-C", "worker-1")
            result = acquire_lease("job-C", "worker-2")
        assert result is True

    def test_release_by_non_owner_is_noop(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease, release_lease, get_lease_holder
            acquire_lease("job-D", "worker-1")
            release_lease("job-D", "worker-2")  # wrong worker
            holder = get_lease_holder("job-D")
        assert holder == "worker-1"

    def test_renew_lease_extends_ttl_for_owner(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease, renew_lease
            acquire_lease("job-E", "worker-1")
            result = renew_lease("job-E", "worker-1")
        assert result is True

    def test_renew_lease_fails_for_non_owner(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease, renew_lease
            acquire_lease("job-F", "worker-1")
            result = renew_lease("job-F", "worker-2")
        assert result is False

    def test_invalidate_lease_removes_key(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import acquire_lease, invalidate_lease, get_lease_holder
            acquire_lease("job-G", "worker-1")
            invalidate_lease("job-G")
            holder = get_lease_holder("job-G")
        assert holder is None

    def test_get_lease_holder_returns_none_when_absent(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import get_lease_holder
            assert get_lease_holder("no-job") is None

    def test_acquire_fails_open_on_redis_error(self):
        """If Redis is down, acquire should return True (fail-open) so task proceeds."""
        bad_redis = MagicMock(side_effect=Exception("Redis down"))
        with patch("app.core.job_lease._redis", return_value=bad_redis):
            from app.core.job_lease import acquire_lease
            result = acquire_lease("job-Z", "worker-1")
        assert result is True


# ═══════════════════════════════════════════════════════════════════
# C — JobLease context manager
# ═══════════════════════════════════════════════════════════════════

class TestJobLeaseContextManager:

    def test_acquire_and_stop(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import JobLease, get_lease_holder, get_heartbeat
            lease = JobLease("job-ctx-1", worker_id="w1", lease_ttl=60, hb_interval=1, hb_ttl=30)
            assert lease.acquire() is True
            lease.start_heartbeat()
            time.sleep(0.05)
            hb = get_heartbeat("job-ctx-1")
            assert hb is not None  # heartbeat was written
            lease.stop()
            # After stop: heartbeat cleared
            hb_after = get_heartbeat("job-ctx-1")
            assert hb_after is None
            # Lease should be released
            holder = get_lease_holder("job-ctx-1")
            assert holder is None

    def test_second_worker_blocked_while_first_active(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import JobLease
            lease1 = JobLease("job-ctx-2", "w1")
            lease2 = JobLease("job-ctx-2", "w2")
            assert lease1.acquire() is True
            assert lease2.acquire() is False

    def test_second_worker_can_take_over_after_first_releases(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import JobLease
            lease1 = JobLease("job-ctx-3", "w1")
            lease1.acquire()
            lease1.stop()

            lease2 = JobLease("job-ctx-3", "w2")
            result = lease2.acquire()
        assert result is True

    def test_heartbeat_thread_refreshes_key(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import JobLease, get_heartbeat
            lease = JobLease("job-hbt-1", "w1", hb_interval=0, hb_ttl=30)
            lease.acquire()
            lease.start_heartbeat()
            time.sleep(0.1)
            hb = get_heartbeat("job-hbt-1")
            lease.stop()
        assert hb is not None


# ═══════════════════════════════════════════════════════════════════
# D — is_alive integration
# ═══════════════════════════════════════════════════════════════════

class TestIsAliveEdgeCases:

    def test_is_alive_returns_false_on_redis_error(self):
        bad_redis = MagicMock(side_effect=Exception("down"))
        with patch("app.core.job_lease._redis", return_value=bad_redis):
            from app.core import job_lease
            # Reload the function to use patched _redis
            result = job_lease.is_alive("any-job")
        assert result is False

    def test_is_alive_false_after_clear(self):
        fake = FakeRedis()
        with patch("app.core.job_lease._redis", return_value=fake):
            from app.core.job_lease import set_heartbeat, clear_heartbeat, is_alive
            set_heartbeat("job-clr")
            assert is_alive("job-clr") is True
            clear_heartbeat("job-clr")
            assert is_alive("job-clr") is False
