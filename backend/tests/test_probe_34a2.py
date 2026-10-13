"""
Phase 34A-2 (task #6332): probe coverage for all 21 platforms, paid probes
rate-limited across restarts, last_verified_at and known_limitation.

No network: every extraction is stubbed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import fakeredis
import pytest

from app.core import platform_probe as pp


@pytest.fixture
def rc(monkeypatch):
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server, decode_responses=True)
    r._test_server = server
    monkeypatch.setattr(pp, "get_redis", lambda: r)
    return r


class TestEveryPlatformIsCoveredOrExplained:

    def test_each_platform_has_a_default_or_a_reason(self):
        missing = [p for p in pp.PROBE_PLATFORMS
                   if p not in pp._DEFAULT_TARGETS and p not in pp.NOT_CONFIGURED_REASON]
        assert missing == [], f"no default target and no stated reason: {missing}"
        assert len(pp.PROBE_PLATFORMS) == 21

    def test_paid_platforms_ship_without_a_default(self):
        for p in pp.PAID_PROBE_PLATFORMS & set(pp.PROBE_PLATFORMS):
            assert p not in pp._DEFAULT_TARGETS, f"{p} would spend on a paid provider by default"
            assert p in pp.NOT_CONFIGURED_REASON

    def test_unconfigured_paid_platform_reports_its_reason(self, rc):
        st = pp.get_probe_states()["douyin"]
        assert st["status"] == pp.NOT_CONFIGURED
        assert st["reason"] == "paid_probe_admin_opt_in"
        assert st["paid"] is True and st["min_interval_s"] == pp.PAID_MIN_INTERVAL_SEC

    def test_defaults_are_https_urls(self):
        for p, url in pp._DEFAULT_TARGETS.items():
            assert p in pp.PROBE_PLATFORMS
            assert url.startswith("https://"), p


class TestPaidMinInterval:

    def test_free_platform_is_never_gated(self, rc):
        assert pp.get_min_interval("youtube") == 0
        assert all(pp.claim_probe_slot("youtube") for _ in range(3))

    def test_paid_platform_runs_once_per_interval(self, rc):
        assert pp.claim_probe_slot("douyin") is True
        assert pp.claim_probe_slot("douyin") is False
        assert pp.claim_probe_slot("xiaohongshu") is True   # independent slot
        ttl = rc.ttl("probe:last_run:douyin")
        assert 0 < ttl <= pp.PAID_MIN_INTERVAL_SEC

    def test_claim_survives_a_restart(self, rc):
        """The claim lives in Redis, not in process memory: a worker that
        restarts (a fresh client on the same Redis) still sees it."""
        assert pp.claim_probe_slot("douyin") is True
        fresh = fakeredis.FakeRedis(server=rc._test_server, decode_responses=True)
        with patch.object(pp, "get_redis", lambda: fresh):
            assert pp.claim_probe_slot("douyin") is False

    def test_redis_down_means_no_paid_probe(self, monkeypatch):
        def _down():
            raise ConnectionError("redis down")
        monkeypatch.setattr(pp, "get_redis", _down)
        assert pp.get_min_interval("douyin") == pp.PAID_MIN_INTERVAL_SEC
        assert pp.claim_probe_slot("douyin") is False
        assert pp.claim_probe_slot("youtube") is True

    def test_admin_cannot_set_a_paid_interval_below_one_hour(self, rc):
        assert pp.set_min_interval("douyin", 60) == 3600
        assert pp.set_min_interval("douyin", None) == pp.PAID_MIN_INTERVAL_SEC
        assert pp.set_min_interval("vk", 7200) == 7200

    def test_sweep_skips_a_paid_platform_inside_its_interval(self, rc, monkeypatch):
        from app.tasks import probe_tasks
        monkeypatch.setattr(probe_tasks, "get_redis", lambda: rc)
        calls = []

        def _probe(url, quality="video"):
            calls.append(url)
            return {"ok": True, "reason": None, "ms": 1, "title": "t"}

        targets = {"douyin": "https://www.douyin.com/video/1", "vk": "https://vk.com/video-1_2"}
        with patch.object(probe_tasks, "get_targets", return_value=targets), \
             patch.object(probe_tasks, "probe_once", side_effect=_probe), \
             patch.object(probe_tasks, "_send"):
            fn = getattr(probe_tasks.probe_all_platforms, "run", probe_tasks.probe_all_platforms)
            first = fn()
            second = fn()
        assert set(first) == {"douyin", "vk"}
        assert set(second) == {"vk"}, "douyin probed twice inside 24 h"
        assert calls.count("https://www.douyin.com/video/1") == 1

    def test_a_daily_probe_is_not_stale_after_three_hours(self, rc):
        pp.set_target("douyin", "https://www.douyin.com/video/1")
        pp.record_probe("douyin", {"ok": True, "ms": 1, "title": "t"})
        rec = json.loads(rc.hget(pp._STATE_KEY, "douyin"))
        rec["at"] -= 5 * 3600
        rc.hset(pp._STATE_KEY, "douyin", json.dumps(rec))
        assert pp.get_probe_states()["douyin"]["status"] == pp.OK


class TestLastVerifiedAt:

    def test_set_on_success_only(self, rc):
        pp.record_probe("vk", {"ok": False, "reason": "x", "ms": 1})
        assert pp.get_probe_states()["vk"]["last_verified_at"] is None
        pp.record_probe("vk", {"ok": True, "ms": 1, "title": "t"})
        first = pp.get_probe_states()["vk"]["last_verified_at"]
        assert first and first.endswith("Z")
        with patch("app.core.platform_probe.time.time", return_value=9_999_999_999):
            pp.record_probe("vk", {"ok": False, "reason": "y", "ms": 1})
        assert json.loads(rc.hget(pp._STATE_KEY, "vk"))["verified_at"] != 9_999_999_999
        assert pp.get_probe_states()["vk"]["last_verified_at"] == first, (
            "a failure must not move the last-verified time")

    def test_platform_status_exposes_new_fields_without_dropping_old(self, rc):
        import asyncio
        from app.api.routes import get_platform_status
        pp.record_probe("vk", {"ok": True, "ms": 1, "title": "t"})
        pp.set_known_limitation("vk", "Chỉ video công khai")
        with patch("app.core.lane_observer.observe_all_platforms", return_value=[]):
            r = asyncio.run(get_platform_status())
        row = next(p for p in r["platforms"] if p["platform"] == "vk")
        for k in ("platform", "status", "message", "reason", "probe", "monitored"):
            assert k in row
        assert row["status"] == "healthy"
        assert row["last_verified_at"]
        assert row["known_limitation"] == "Chỉ video công khai"
        other = next(p for p in r["platforms"] if p["platform"] == "douyin")
        assert other["known_limitation"] == "" and other["last_verified_at"] is None


class TestKnownLimitationAdmin:

    @pytest.fixture
    def client(self, rc, monkeypatch):
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        monkeypatch.setattr("app.api.admin.log_admin_action", lambda *a, **k: None)
        monkeypatch.setattr("app.api.admin._redis", lambda: rc)
        return TestClient(fastapi_app)

    def test_requires_admin(self, client):
        r = client.post("/api/v1/admin/probes/settings",
                        json={"platform": "vk", "known_limitation": "x"})
        assert r.status_code in (401, 403), r.text[:200]
        assert pp.get_known_limitations() == {}

    def test_round_trip(self, client, rc):
        # A real admin session (Bearer) rather than a dependency override:
        # another test reloads app.api.admin, after which the route's
        # verify_admin is no longer the object an override would key on.
        import app.api.admin as admin_mod
        rc.set(admin_mod._session_key("probe-test-session"), "1")
        client.headers.update({"Authorization": "Bearer probe-test-session"})
        r = client.post("/api/v1/admin/probes/settings",
                        json={"platform": "vk", "known_limitation": "  Chỉ tải được   video công khai "})
        assert r.status_code == 200, r.text[:300]
        assert r.json()["known_limitation"] == "Chỉ tải được video công khai"
        rows = client.get("/api/v1/admin/probes").json()["platforms"]
        assert next(x for x in rows if x["platform"] == "vk")["known_limitation"] == \
            "Chỉ tải được video công khai"

        r = client.post("/api/v1/admin/probes/settings",
                        json={"platform": "douyin", "min_interval_sec": 7200})
        assert r.json()["min_interval_s"] == 7200

        r = client.post("/api/v1/admin/probes/settings",
                        json={"platform": "vk", "known_limitation": ""})
        assert r.json()["known_limitation"] == ""
        assert "vk" not in pp.get_known_limitations()

        assert client.post("/api/v1/admin/probes/settings",
                           json={"platform": "myspace", "known_limitation": "x"}).status_code == 400
