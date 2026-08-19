"""
Phase 8 integration tests — YouTube proxy gate (feature flag, tier gating,
cost guard, circuit breaker, metrics, admin wiring).

Runs in CI WITHOUT a real Redis: a fakeredis instance is injected as the
process-wide `app.core.redis_client._client` singleton, so every
`get_redis()` call inside `youtube_gate` (and friends) hits the in-memory
fake. The fake uses decode_responses=True to mirror the real client.

Run:  cd backend && python -m pytest tests/test_phase8_youtube.py -v
"""

import os
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# Skip the whole module (not error) if fakeredis isn't installed, so a bare
# checkout still collects cleanly. CI installs it via requirements-dev.txt.
fakeredis = pytest.importorskip("fakeredis")


def _src(rel: str) -> str:
    return open(os.path.join(BASE, rel), encoding="utf-8").read()


# ── fakeredis injection ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """Inject a fresh fakeredis as the redis singleton for each test."""
    import app.core.redis_client as rc
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(rc, "_client", fake, raising=False)
    fake.flushall()
    yield fake
    fake.flushall()


@pytest.fixture
def g(fake_redis):
    """The youtube_gate module, with a clean env override each test."""
    from app.core import youtube_gate as _g
    # Ensure env doesn't leak across tests; default disabled.
    monkeypatch_env = {"YOUTUBE_ENABLED": "false", "YOUTUBE_PROXY_DOWNLOAD": "0"}
    for k, v in monkeypatch_env.items():
        os.environ[k] = v
    return _g


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Feature flag (P8)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFeatureFlag:
    def test_default_disabled_from_env(self, g):
        os.environ["YOUTUBE_ENABLED"] = "false"
        g.clear_youtube_override()
        assert g.is_youtube_enabled() is False

    def test_env_enabled(self, g):
        os.environ["YOUTUBE_ENABLED"] = "true"
        g.clear_youtube_override()
        assert g.is_youtube_enabled() is True

    def test_redis_override_beats_env_true(self, g):
        os.environ["YOUTUBE_ENABLED"] = "false"
        g.set_youtube_enabled(True)
        assert g.is_youtube_enabled() is True

    def test_redis_override_beats_env_false(self, g):
        os.environ["YOUTUBE_ENABLED"] = "true"
        g.set_youtube_enabled(False)
        assert g.is_youtube_enabled() is False

    def test_clear_override_falls_back_to_env(self, g):
        os.environ["YOUTUBE_ENABLED"] = "true"
        g.set_youtube_enabled(False)
        assert g.is_youtube_enabled() is False
        g.clear_youtube_override()
        assert g.is_youtube_enabled() is True


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Quality tier gating (P4 / P11)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTierGating:
    @pytest.mark.parametrize("quality", ["mp3_128", "mp3_320", "video_fast",
                                          "video_720", "video", "audio_m4a"])
    def test_allowed_tiers(self, g, quality):
        d = g.tier_decision(quality)
        assert d["allow"] is True
        assert d["needs_confirmation"] is False

    def test_best_video_clamped_to_cap(self, g):
        # "video" (best) must be clamped to the proxy height ceiling (720).
        assert g.tier_decision("video")["effective_height"] == g.MAX_PROXY_HEIGHT

    def test_720_effective_height(self, g):
        assert g.tier_decision("video_720")["effective_height"] == 720

    # ── Quality tiering was deliberately removed ──────────────────────
    # The 4K hard-block, the 1440p block and the 1080p confirmation prompt
    # existed only to cap metered residential-proxy bandwidth. VidGrab now runs
    # on the server's own proxy, so that cost model is gone and every height is
    # served as requested (commit 2575339). The tests below used to assert the
    # blocking behaviour; they now pin the decision that replaced it, so a
    # reintroduced cap fails loudly instead of silently degrading downloads.

    @pytest.mark.parametrize("quality,expected_height", [
        ("video_1080", 1080),
        ("video_1440", 1440),
        ("video_2160", 2160),
        ("video_4k",   2160),
        ("4k",         2160),
        ("mp4_4k",     2160),
    ])
    def test_high_quality_is_served_at_requested_height(self, g, quality, expected_height):
        d = g.tier_decision(quality)
        assert d["allow"] is True
        assert d["needs_confirmation"] is False
        assert d["reason"] == "ok"
        assert d["suggested"] is None
        assert d["effective_height"] == expected_height

    def test_confirmation_flag_changes_nothing(self, g):
        """No tier asks for confirmation any more, so the flag is inert."""
        for quality in ("video_1080", "video_4k", "video_720"):
            assert g.tier_decision(quality, confirmed=False) == \
                   g.tier_decision(quality, confirmed=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Cost guard (P3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCostGuard:
    def test_empty_under_limit(self, g):
        assert g.get_proxy_bytes_today() == 0
        assert g.over_daily_limit() is False

    def test_add_bytes_accumulates(self, g):
        g.add_proxy_megabytes(80)   # one 720p
        g.add_proxy_megabytes(80)
        assert g.get_proxy_bytes_today() == int(160 * 1024 * 1024)

    def test_over_limit_trips_at_ceiling(self, g):
        g.add_proxy_megabytes(g.DAILY_LIMIT_GB * 1024 + 1)  # just over 2GB
        assert g.over_daily_limit() is True

    def test_cost_estimate_matches_rate(self, g):
        g.add_proxy_bytes(g._BYTES_PER_GB)   # exactly 1 GB
        assert g.cost_estimate_today() == pytest.approx(g.PROXY_COST_PER_GB, abs=1e-3)

    def test_bytes_pct(self, g):
        g.add_proxy_bytes(int(g.daily_limit_bytes() * 0.5))
        assert g.bytes_pct_today() == pytest.approx(0.5, abs=1e-3)

    def test_negative_bytes_ignored(self, g):
        assert g.add_proxy_bytes(-5) == 0
        assert g.get_proxy_bytes_today() == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Circuit breaker (P5)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def test_starts_closed(self, g):
        assert g.circuit_state() == "closed"
        assert g.circuit_open() is False

    def test_trips_open_at_threshold(self, g):
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        assert g.circuit_state() == "open"
        assert g.circuit_open() is True

    def test_below_threshold_stays_closed(self, g):
        for _ in range(g.CB_FAIL_THRESHOLD - 1):
            g.record_proxy_failure()
        assert g.circuit_state() == "closed"

    def test_success_closes_circuit(self, g):
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        assert g.circuit_open() is True
        g.record_proxy_success()
        assert g.circuit_state() == "closed"

    def test_open_to_half_after_cooldown(self, g, fake_redis):
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        # Backdate the open timestamp past the cooldown window.
        fake_redis.set(g._CB_OPEN_SINCE, str(time.time() - g.CB_OPEN_SEC - 1))
        assert g.circuit_state() == "half"
        assert g.circuit_open() is False    # half allows the probe

    def test_half_open_failure_reopens(self, g, fake_redis):
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        fake_redis.set(g._CB_OPEN_SINCE, str(time.time() - g.CB_OPEN_SEC - 1))
        assert g.circuit_state() == "half"
        g.record_proxy_failure()            # probe fails
        assert g.circuit_state() == "open"

    def test_cooldown_remaining_positive_when_open(self, g):
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        assert 0 < g.circuit_cooldown_remaining() <= g.CB_OPEN_SEC


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Master preflight gate — order: flag → circuit → cost → tier
# ═══════════════════════════════════════════════════════════════════════════════

class TestPreflight:
    def test_blocked_when_disabled(self, g):
        g.set_youtube_enabled(False)
        with pytest.raises(g.YouTubeBlocked) as ei:
            g.preflight("mp3_128")
        assert ei.value.code == "youtube_disabled"
        assert ei.value.http == 503

    def test_blocked_when_circuit_open(self, g):
        g.set_youtube_enabled(True)
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        with pytest.raises(g.YouTubeBlocked) as ei:
            g.preflight("video_720")
        assert ei.value.code == "youtube_circuit_open"

    def test_blocked_when_over_cost_limit(self, g):
        g.set_youtube_enabled(True)
        g.add_proxy_megabytes(g.DAILY_LIMIT_GB * 1024 + 1)
        with pytest.raises(g.YouTubeBlocked) as ei:
            g.preflight("mp3_128")
        assert ei.value.code == "youtube_cost_limit"
        assert ei.value.http == 503

    def test_1080_passes_without_confirmation(self, g):
        """Was: raised youtube_confirm_required (409). Tiering is gone — see
        TestTierGating for why — so preflight lets it straight through."""
        g.set_youtube_enabled(True)
        out = g.preflight("video_1080", confirmed=False)
        assert out["effective_height"] == 1080

    def test_1080_confirmed_passes(self, g):
        g.set_youtube_enabled(True)
        out = g.preflight("video_1080", confirmed=True)
        assert out["effective_height"] == 1080

    def test_4k_passes(self, g):
        """Was: raised 4k_blocked (400). The 4K hard-block was removed with the
        rest of the bandwidth tiering; preflight now only enforces the kill
        switch, the circuit breaker and the daily cost ceiling."""
        g.set_youtube_enabled(True)
        out = g.preflight("video_4k")
        assert out["effective_height"] == 2160

    def test_mp3_passes_when_enabled(self, g):
        g.set_youtube_enabled(True)
        out = g.preflight("mp3_128")
        assert out["effective_height"] == g.MAX_PROXY_HEIGHT

    def test_flag_checked_before_cost(self, g):
        # Disabled + over-limit → the disabled error wins (flag is first guard).
        g.set_youtube_enabled(False)
        g.add_proxy_megabytes(g.DAILY_LIMIT_GB * 1024 + 1)
        with pytest.raises(g.YouTubeBlocked) as ei:
            g.preflight("mp3_128")
        assert ei.value.code == "youtube_disabled"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. record_result — bytes billing + circuit + metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecordResult:
    def test_success_bills_bytes(self, g):
        g.record_result(True, file_size_mb=80)
        assert g.get_proxy_bytes_today() == int(80 * 1024 * 1024)

    def test_success_increments_metric(self, g):
        g.record_result(True, file_size_mb=4)
        assert g.get_youtube_stats().get("success", 0) == 1

    def test_success_closes_open_circuit(self, g):
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        assert g.circuit_open() is True
        g.record_result(True, file_size_mb=4)
        assert g.circuit_state() == "closed"

    def test_failure_feeds_circuit(self, g):
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_result(False)
        assert g.circuit_open() is True

    def test_success_with_zero_mb_no_bytes(self, g):
        # When proxy wasn't used (mb=0), success must not bill proxy bytes.
        g.record_result(True, file_size_mb=0)
        assert g.get_proxy_bytes_today() == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Dashboard snapshot + status color (P3 / P9)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDashboard:
    def test_snapshot_has_required_keys(self, g):
        snap = g.dashboard_snapshot()
        for k in ("enabled", "bytes_today", "bytes_gb", "limit_gb", "cost_today",
                  "circuit_state", "success_rate", "status_color", "max_proxy_height"):
            assert k in snap

    def test_color_green_when_idle_enabled(self, g):
        g.set_youtube_enabled(True)
        assert g.dashboard_snapshot()["status_color"] == "green"

    def test_color_red_when_disabled(self, g):
        g.set_youtube_enabled(False)
        assert g.dashboard_snapshot()["status_color"] == "red"

    def test_color_yellow_at_80pct(self, g):
        g.set_youtube_enabled(True)
        g.add_proxy_bytes(int(g.daily_limit_bytes() * 0.85))
        assert g.dashboard_snapshot()["status_color"] == "yellow"

    def test_color_red_when_over_limit(self, g):
        g.set_youtube_enabled(True)
        g.add_proxy_megabytes(g.DAILY_LIMIT_GB * 1024 + 1)
        snap = g.dashboard_snapshot()
        assert snap["over_limit"] is True
        assert snap["status_color"] == "red"

    def test_color_red_when_circuit_open(self, g):
        g.set_youtube_enabled(True)
        for _ in range(g.CB_FAIL_THRESHOLD):
            g.record_proxy_failure()
        assert g.dashboard_snapshot()["status_color"] == "red"

    def test_success_rate_computed(self, g):
        for _ in range(3):
            g.youtube_metric("success")
        g.youtube_metric("fail")
        assert g.dashboard_snapshot()["success_rate"] == pytest.approx(75.0, abs=0.1)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Health watchdog runs without raising (alerts best-effort, no telegram cfg)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthWatchdog:
    def test_check_health_returns_snapshot(self, g):
        g.set_youtube_enabled(True)
        snap = g.check_health_and_alert()
        assert "status_color" in snap

    def test_check_health_over_budget_no_crash(self, g):
        g.set_youtube_enabled(True)
        g.add_proxy_bytes(int(g.daily_limit_bytes() * 0.9))
        snap = g.check_health_and_alert()   # would attempt a Telegram alert
        assert snap["bytes_pct"] >= 0.8


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Integration wiring — source inspection (locks the touch points in place)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWiring:
    def test_downloader_attaches_proxy_for_youtube_download(self):
        src = _src("app/services/downloader.py")
        assert "_youtube_proxy_download_enabled()" in src
        assert "tier_decision" in src
        # Proxy attached on the download phase only for YouTube.
        assert "YouTube Phase B" in src

    def test_routes_preflight_before_quota(self):
        src = _src("app/api/routes.py")
        assert "youtube_gate" in src
        assert "preflight" in src
        # preflight must appear before the quota reserve call.
        assert src.index("_ytg.preflight") < src.index("_ytq.reserve")

    def test_routes_records_result(self):
        src = _src("app/api/routes.py")
        assert "record_result(True" in src
        assert "record_result(False)" in src

    def test_routes_confirmed_field(self):
        assert "confirmed" in _src("app/api/routes.py")

    def test_admin_youtube_endpoints(self):
        src = _src("app/api/admin.py")
        assert "/youtube/status" in src
        assert "/youtube/toggle" in src
        assert "dashboard_snapshot" in src

    def test_celery_beat_youtube_health(self):
        src = _src("app/core/celery_app.py")
        assert "check_youtube_health" in src

    def test_video_tasks_bills_bulk_bytes(self):
        src = _src("app/tasks/video_tasks.py")
        assert "youtube_gate" in src
        assert "record_result" in src

    def test_frontend_handles_confirm_and_blocked(self):
        src = _src("../frontend/src/components/DashboardContent.jsx")
        assert "youtube_confirm_required" in src
