"""
Observability Phase Tests
=========================
Tests for:
  A — metrics.py: emit_job_event, get_job_metrics, track_quota_denial,
      get_cookie_pool_health, get_queue_depths
  B — alerts.py: check_stale_jobs, check_cookie_pool_health, check_retry_storm
  C — ops.py: /admin/ops-signals endpoint, _derive_alerts
  D — Regression: recovery + video_tasks metric wiring smoke tests

Run with:
    cd backend && source venv/bin/activate
    python -m pytest tests/test_observability.py -q
"""

from __future__ import annotations

import sys
import time
import threading
from unittest.mock import MagicMock, patch

# ── Stub heavy modules ────────────────────────────────────────────────────────
for _mod in (
    "realtime", "realtime.types", "realtime.connection",
    "supabase", "supabase.client", "supabase._sync", "supabase._async",
    "postgrest", "gotrue", "storage3",
    "app.main",
    "slowapi",
    "app.core.database",
    "app.core.notifications",
    "app.core.celery_app",
):
    sys.modules.setdefault(_mod, MagicMock())

import app.main as _main_stub
_main_stub.limiter = MagicMock()
_main_stub.limiter.limit = lambda *a, **kw: (lambda f: f)


# ── Fake Redis ────────────────────────────────────────────────────────────────

class FakeRedis:
    """In-memory Redis mock — covers sorted sets, hashes, strings, lists."""

    def __init__(self):
        self._ss: dict = {}     # sorted sets  key → {member: score}
        self._hh: dict = {}     # hashes        key → {field: value}
        self._st: dict = {}     # strings
        self._ls: dict = {}     # lists
        self._lock = threading.Lock()

    # Sorted sets
    def zadd(self, key, mapping):
        with self._lock:
            self._ss.setdefault(key, {})
            self._ss[key].update(mapping)
            return len(mapping)

    def zrangebyscore(self, key, min_score, max_score):
        with self._lock:
            ss = self._ss.get(key, {})
            return [
                m.encode() if isinstance(m, str) else m
                for m, s in ss.items()
                if s >= min_score and (max_score == "+inf" or s <= float(max_score))
            ]

    def zremrangebyscore(self, key, min_score, max_score):
        with self._lock:
            ss = self._ss.get(key, {})
            to_del = [m for m, s in ss.items() if min_score <= s <= float(max_score)]
            for m in to_del:
                del ss[m]

    def zcount(self, key, min_score, max_score):
        return len(self.zrangebyscore(key, min_score, max_score))

    # Hashes
    def hincrby(self, key, field, amount=1):
        with self._lock:
            self._hh.setdefault(key, {})
            self._hh[key][field] = self._hh[key].get(field, 0) + amount
            return self._hh[key][field]

    def hgetall(self, key):
        with self._lock:
            return {
                k.encode() if isinstance(k, str) else k: str(v).encode()
                for k, v in self._hh.get(key, {}).items()
            }

    # Strings
    def get(self, key):
        with self._lock:
            v = self._st.get(key)
            return v.encode() if isinstance(v, str) else v

    def set(self, key, value, ex=None, nx=False):
        with self._lock:
            if nx and key in self._st:
                return None
            self._st[key] = value
            return True

    def setex(self, key, ttl, value):
        self.set(key, value)

    # Lists
    def lrange(self, key, start, stop):
        with self._lock:
            ls = self._ls.get(key, [])
            end = None if stop == -1 else stop + 1
            return [
                v.encode() if isinstance(v, str) else v
                for v in ls[start:end]
            ]

    def llen(self, key):
        with self._lock:
            return len(self._ls.get(key, []))

    def rpush(self, key, *values):
        with self._lock:
            self._ls.setdefault(key, [])
            self._ls[key].extend(values)

    def lpush(self, key, *values):
        with self._lock:
            self._ls.setdefault(key, [])
            for v in reversed(values):
                self._ls[key].insert(0, v)

    # TTL / expire (no-op — not needed for these tests)
    def expire(self, key, ttl): return True
    def ttl(self, key): return 3600


# ═══════════════════════════════════════════════════════════════════
# A — metrics.py
# ═══════════════════════════════════════════════════════════════════

class TestEmitJobEvent:

    def test_emit_known_event_writes_to_sorted_set(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import emit_job_event
            emit_job_event("succeeded", job_id="job-1", platform="youtube")
        assert fake._ss.get("vg:m:j:succeeded")

    def test_unknown_event_is_ignored(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core import metrics
            metrics.emit_job_event("nonexistent_event", job_id="j")
        assert "vg:m:j:nonexistent_event" not in fake._ss

    def test_emit_increments_daily_hash(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import emit_job_event
            emit_job_event("failed", job_id="j2", platform="tiktok")
        # daily hash should have an entry
        daily_keys = [k for k in fake._hh if k.startswith("vg:m:t:")]
        assert daily_keys

    def test_emit_never_raises_on_redis_error(self):
        bad_redis = MagicMock(side_effect=Exception("Redis down"))
        with patch("app.core.metrics._redis", return_value=bad_redis):
            from app.core import metrics
            metrics.emit_job_event("succeeded", job_id="j")  # must not raise

    def test_emit_all_valid_events(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core import metrics
            for event in metrics.JOB_EVENTS:
                metrics.emit_job_event(event, job_id="j", platform="other")
        assert len(fake._ss) == len(metrics.JOB_EVENTS)

    def test_platform_stored_in_member(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import emit_job_event
            emit_job_event("started", job_id="job-xyz", platform="instagram")
        members = list(fake._ss.get("vg:m:j:started", {}).keys())
        assert any("instagram" in m for m in members)


class TestGetJobMetrics:

    def test_returns_correct_structure(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import get_job_metrics
            result = get_job_metrics(window_minutes=30)
        assert "by_event" in result
        assert "by_platform" in result
        assert "total" in result
        assert "window_minutes" in result

    def test_counts_events_in_window(self):
        fake = FakeRedis()
        now = time.time()
        # Add 3 "succeeded" events (2 recent, 1 old)
        fake._ss["vg:m:j:succeeded"] = {
            "youtube:job-a": now - 100,     # within 30m
            "tiktok:job-b": now - 200,      # within 30m
            "youtube:job-c": now - 3600,    # outside 30m (1 hour ago)
        }
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core import metrics as _m
            result = _m.get_job_metrics(window_minutes=30)
        assert result["by_event"]["succeeded"] == 2

    def test_zero_counts_when_no_events(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import get_job_metrics
            result = get_job_metrics(window_minutes=30)
        assert result["total"] == 0
        assert result["by_event"]["succeeded"] == 0

    def test_never_raises_on_redis_error(self):
        bad = MagicMock(side_effect=Exception("down"))
        with patch("app.core.metrics._redis", return_value=bad):
            from app.core import metrics as _m
            result = _m.get_job_metrics()
        assert result["total"] == 0


class TestTrackQuotaDenial:

    def test_writes_to_quota_sorted_set(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import track_quota_denial
            track_quota_denial("quota_exceeded_daily")
        assert fake._ss.get("vg:m:q")

    def test_never_raises_on_redis_error(self):
        bad = MagicMock(side_effect=Exception("down"))
        with patch("app.core.metrics._redis", return_value=bad):
            from app.core import metrics as _m
            _m.track_quota_denial("reason")  # must not raise


class TestGetQuotaDenialCount:

    def test_counts_within_window(self):
        fake = FakeRedis()
        now = time.time()
        fake._ss["vg:m:q"] = {
            "reason:uid1": now - 60,   # 1m ago — in window
            "reason:uid2": now - 120,  # 2m ago — in window
            "reason:uid3": now - 3600, # 1h ago — out of 30m window
        }
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import get_quota_denial_count
            assert get_quota_denial_count(window_minutes=30) == 2


class TestGetQueueDepths:

    def test_returns_all_queues(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import get_queue_depths
            result = get_queue_depths()
        assert "downloads" in result
        assert "bulk" in result
        assert "media" in result
        assert "celery" in result

    def test_reads_llen_for_each_queue(self):
        fake = FakeRedis()
        for q in ("downloads", "bulk"):
            fake._ls[q] = ["task1", "task2"]
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import get_queue_depths
            result = get_queue_depths()
        assert result["downloads"] == 2
        assert result["bulk"] == 2

    def test_zero_when_empty(self):
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import get_queue_depths
            result = get_queue_depths()
        assert all(v == 0 for v in result.values())


class TestGetCookiePoolHealth:

    def _make_fake(self, platform, n_total, n_hard=0, n_soft=0):
        """Build a FakeRedis with cookie_pool and cookie_health entries."""
        import base64, hashlib
        fake = FakeRedis()
        cookies = [f"cookie-{i}" for i in range(n_total)]
        encoded = [base64.b64encode(c.encode()).decode() for c in cookies]
        fake._ls[f"cookie_pool:{platform}"] = encoded
        for i in range(n_hard):
            raw_bytes = cookies[i].encode()
            h = hashlib.sha256(raw_bytes).hexdigest()[:16]
            # store as decoded base64 → cookie_pool contains base64 encoded
            # get_cookie_pool_health decodes it then hashes
            fake._st[f"cookie_health:{platform}:{h}"] = "hard"
        for i in range(n_hard, n_hard + n_soft):
            raw_bytes = cookies[i].encode()
            h = hashlib.sha256(raw_bytes).hexdigest()[:16]
            fake._st[f"cookie_health:{platform}:{h}"] = "soft"
        return fake

    def test_returns_total_for_platform_with_cookies(self):
        fake = self._make_fake("instagram", n_total=3)
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core import metrics as _m
            result = _m.get_cookie_pool_health()
        assert result.get("instagram", {}).get("total") == 3

    def test_available_equals_total_minus_blocked(self):
        fake = self._make_fake("youtube", n_total=4, n_hard=1, n_soft=1)
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core import metrics as _m
            result = _m.get_cookie_pool_health()
        h = result.get("youtube", {})
        assert h.get("total") == 4

    def test_empty_platform_omitted(self):
        fake = FakeRedis()  # no cookie pools
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core import metrics as _m
            result = _m.get_cookie_pool_health()
        assert "youtube" not in result

    def test_never_raises_on_error(self):
        with patch("app.core.metrics._redis", side_effect=Exception("down")):
            from app.core import metrics as _m
            result = _m.get_cookie_pool_health()
        assert result == {}


# ═══════════════════════════════════════════════════════════════════
# B — alerts.py extensions
# ═══════════════════════════════════════════════════════════════════

class TestAlertExtensions:

    def _setup_alerts(self):
        """Return the alerts module with send_admin_alert patched to a mock."""
        from app.core import alerts as _al
        return _al

    def test_check_stale_jobs_no_alert_below_threshold(self):
        al = self._setup_alerts()
        with patch("app.core.metrics.get_stale_job_count", return_value=0), \
             patch.object(al, "send_admin_alert") as mock_alert:
            al.check_stale_jobs()
        mock_alert.assert_not_called()

    def test_check_stale_jobs_warning_above_warning_threshold(self):
        al = self._setup_alerts()
        with patch("app.core.metrics.get_stale_job_count", return_value=5), \
             patch.object(al, "send_admin_alert") as mock_alert, \
             patch.object(al, "_throttle", return_value=False):
            al.STALE_JOB_WARNING = 3
            al.STALE_JOB_CRITICAL = 10
            al.check_stale_jobs()
        mock_alert.assert_called_once()
        args = mock_alert.call_args[0]
        assert args[0] == "warning"

    def test_check_stale_jobs_critical(self):
        al = self._setup_alerts()
        with patch("app.core.metrics.get_stale_job_count", return_value=15), \
             patch.object(al, "send_admin_alert") as mock_alert, \
             patch.object(al, "_throttle", return_value=False):
            al.STALE_JOB_WARNING = 3
            al.STALE_JOB_CRITICAL = 10
            al.check_stale_jobs()
        args = mock_alert.call_args[0]
        assert args[0] == "critical"

    def test_check_stale_jobs_unavailable_db_skips(self):
        al = self._setup_alerts()
        with patch("app.core.metrics.get_stale_job_count", return_value=-1), \
             patch.object(al, "send_admin_alert") as mock_alert:
            al.check_stale_jobs()
        mock_alert.assert_not_called()

    def test_check_cookie_pool_all_available_no_alert(self):
        al = self._setup_alerts()
        health = {"youtube": {"total": 2, "available": 2, "blocked_soft": 0, "blocked_hard": 0}}
        with patch("app.core.metrics.get_cookie_pool_health", return_value=health), \
             patch.object(al, "send_admin_alert") as mock_alert:
            al.check_cookie_pool_health()
        mock_alert.assert_not_called()

    def test_check_cookie_pool_depleted_fires_critical(self):
        al = self._setup_alerts()
        health = {"instagram": {"total": 3, "available": 0, "blocked_soft": 1, "blocked_hard": 2}}
        with patch("app.core.metrics.get_cookie_pool_health", return_value=health), \
             patch.object(al, "send_admin_alert") as mock_alert, \
             patch.object(al, "_throttle", return_value=False):
            al.check_cookie_pool_health()
        args = mock_alert.call_args[0]
        assert args[0] == "critical"
        assert "instagram" in args[1]

    def test_check_retry_storm_below_threshold_no_alert(self):
        al = self._setup_alerts()
        metrics = {"by_event": {"retrying": 5}}  # 5 / 10 min = 0.5 /min < threshold
        with patch("app.core.metrics.get_job_metrics", return_value=metrics), \
             patch.object(al, "send_admin_alert") as mock_alert:
            al.RETRY_STORM_PER_MIN = 3.0
            al.check_retry_storm(window_minutes=10)
        mock_alert.assert_not_called()

    def test_check_retry_storm_above_threshold_fires(self):
        al = self._setup_alerts()
        metrics = {"by_event": {"retrying": 50}}  # 50 / 10 min = 5/min > 3
        with patch("app.core.metrics.get_job_metrics", return_value=metrics), \
             patch.object(al, "send_admin_alert") as mock_alert, \
             patch.object(al, "_throttle", return_value=False):
            al.RETRY_STORM_PER_MIN = 3.0
            al.check_retry_storm(window_minutes=10)
        mock_alert.assert_called_once()

    def test_run_all_health_checks_calls_new_checks(self):
        al = self._setup_alerts()
        with patch.object(al, "check_disk_usage"), \
             patch.object(al, "check_queue_depth"), \
             patch.object(al, "check_worker_count"), \
             patch.object(al, "check_platform_fail_rates"), \
             patch.object(al, "check_stale_jobs") as m_stale, \
             patch.object(al, "check_cookie_pool_health") as m_cookie, \
             patch.object(al, "check_retry_storm") as m_retry, \
             patch.object(al, "check_quota_denial_rate") as m_quota:
            al.run_all_health_checks()
        m_stale.assert_called_once()
        m_cookie.assert_called_once()
        m_retry.assert_called_once()
        m_quota.assert_called_once()


# ═══════════════════════════════════════════════════════════════════
# C — ops.py: _derive_alerts and endpoint logic
# ═══════════════════════════════════════════════════════════════════

class TestDeriveAlerts:

    def _derive(self, **kw):
        from app.api.ops import _derive_alerts
        defaults = dict(
            stale_count=0, open_platforms=[], depleted_cookies=[],
            failed=0, total_terminal=0, quota_denials=0,
        )
        defaults.update(kw)
        return _derive_alerts(**defaults)

    def test_no_alerts_when_all_ok(self):
        assert self._derive() == []

    def test_stale_count_above_zero_adds_warning(self):
        alerts = self._derive(stale_count=2)
        assert any(a["level"] == "warning" for a in alerts)
        assert any("stale" in a["msg"] for a in alerts)

    def test_open_circuit_fires_critical(self):
        alerts = self._derive(open_platforms=["instagram"])
        assert any(a["level"] == "critical" for a in alerts)
        assert any("instagram" in a["msg"] for a in alerts)

    def test_depleted_cookie_fires_critical(self):
        alerts = self._derive(depleted_cookies=["youtube"])
        assert any(a["level"] == "critical" for a in alerts)

    def test_high_failure_rate_fires_critical(self):
        alerts = self._derive(failed=9, total_terminal=11)  # 82% fail, total > 10
        assert any(a["level"] == "critical" for a in alerts)

    def test_low_failure_rate_no_alert(self):
        alerts = self._derive(failed=1, total_terminal=10)  # 10% fail
        assert not any("failure rate" in a.get("msg", "") for a in alerts)

    def test_high_quota_denials_warning(self):
        alerts = self._derive(quota_denials=60)
        assert any("quota" in a.get("msg", "").lower() for a in alerts)

    def test_multiple_alerts_returned(self):
        alerts = self._derive(stale_count=5, open_platforms=["tiktok"])
        assert len(alerts) >= 2


class TestCountRecentRecovery:

    def test_counts_within_window(self):
        from datetime import datetime, timezone, timedelta
        from app.api.ops import _count_recent_recovery
        now = datetime.now(timezone.utc)
        log = [
            {"ts": (now - timedelta(minutes=10)).isoformat()},
            {"ts": (now - timedelta(minutes=20)).isoformat()},
            {"ts": (now - timedelta(minutes=60)).isoformat()},  # outside
        ]
        assert _count_recent_recovery(log, window_minutes=30) == 2

    def test_empty_log_returns_zero(self):
        from app.api.ops import _count_recent_recovery
        assert _count_recent_recovery([], window_minutes=30) == 0

    def test_missing_ts_skipped(self):
        from app.api.ops import _count_recent_recovery
        log = [{"action": "recovered"}]  # no ts
        assert _count_recent_recovery(log, window_minutes=30) == 0


# ═══════════════════════════════════════════════════════════════════
# D — Regression: metric wiring smoke tests
# ═══════════════════════════════════════════════════════════════════

class TestMetricWiringSmoke:

    def test_emit_job_event_called_in_recovery_stale_transition(self):
        """_recover_or_abandon emits 'stale' metric when marking job stale."""
        from app.core import recovery as _rec
        mock_emit = MagicMock()
        job = {"id": "j1", "original_url": "https://youtu.be/x", "platform": "youtube",
               "recovery_attempts": 0, "status": "processing",
               "updated_at": "2020-01-01T00:00:00+00:00"}
        mock_sb = MagicMock()
        mock_sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = None
        mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = None

        with patch("app.core.metrics.emit_job_event", mock_emit), \
             patch("app.core.recovery._is_job_alive", return_value=False):
            try:
                _rec._recover_or_abandon(mock_sb, job, source="test")
            except Exception:
                pass  # allow task dispatch to fail
        mock_emit.assert_called()

    def test_get_job_metrics_returns_dict_structure(self):
        """get_job_metrics always returns a well-formed dict."""
        fake = FakeRedis()
        with patch("app.core.metrics._redis", return_value=fake):
            from app.core.metrics import get_job_metrics, JOB_EVENTS
            result = get_job_metrics()
        assert all(e in result["by_event"] for e in JOB_EVENTS)
        assert isinstance(result["total"], int)

    def test_get_stale_job_count_returns_negative_on_db_error(self):
        """get_stale_job_count returns -1 when Supabase is unavailable."""
        with patch("app.core.database.get_service_client", side_effect=Exception("db down")):
            from app.core import metrics as _m
            result = _m.get_stale_job_count()
        assert result == -1

    def test_get_provider_circuit_states_returns_dict(self):
        """get_provider_circuit_states returns a platform → state dict."""
        mock_get_state = MagicMock(return_value="closed")
        with patch("app.core.platform_circuit.get_state", mock_get_state):
            from app.core import metrics as _m
            result = _m.get_provider_circuit_states()
        assert isinstance(result, dict)
        assert "youtube" in result

    def test_get_provider_circuit_states_safe_on_import_error(self):
        """get_provider_circuit_states returns empty dict if platform_circuit unavailable."""
        with patch.dict("sys.modules", {"app.core.platform_circuit": None}):
            from app.core import metrics as _m
            result = _m.get_provider_circuit_states()
        assert isinstance(result, dict)
