"""
2026-10-02: VK showed "1 nền tảng đang gặp sự cố" to every user while the same
probe URL returned 200 through /fetch-link twice (first in 50s, then 3s). One
slow or throttled probe turned straight into a red banner and a Telegram alert.

Now a platform is FAILED only after _FAIL_CONFIRM consecutive failed probes;
a single failure is UNCONFIRMED (public status: unknown, no banner, no alert).
And a probe can no longer hang the sweep: it stops waiting after the timeout.
"""
import asyncio
import json
import threading
from unittest.mock import patch

from app.core import platform_probe as pp


class _FakeRedis:
    def __init__(self):
        self._hash = {}

    def hget(self, key, field):
        return self._hash.get(field)

    def hset(self, key, field, value):
        self._hash[field] = value

    def hgetall(self, key):
        return dict(self._hash)

    def get(self, k):
        return None


def _rec(rc, name):
    return json.loads(rc._hash[name])


def _record(rc, ok):
    with patch("app.core.platform_probe.get_redis", return_value=rc):
        return pp.record_probe("vk", {"ok": ok, "reason": None if ok else "boom", "ms": 1})


class TestOneFailureIsNotAnOutage:
    def test_first_failure_is_unconfirmed(self):
        rc = _FakeRedis()
        assert _record(rc, True) == pp.OK
        assert _record(rc, False) == pp.UNCONFIRMED
        assert _rec(rc, "vk")["fail_streak"] == 1

    def test_second_consecutive_failure_is_failed(self):
        rc = _FakeRedis()
        _record(rc, False)
        assert _record(rc, False) == pp.FAILED
        assert _rec(rc, "vk")["fail_streak"] == 2

    def test_success_resets_the_streak(self):
        rc = _FakeRedis()
        _record(rc, False)
        assert _record(rc, True) == pp.OK
        assert _rec(rc, "vk")["fail_streak"] == 0
        assert _record(rc, False) == pp.UNCONFIRMED, "an isolated failure after recovery is noise again"

    def test_state_without_a_streak_field_still_works(self):
        """Rows written before this change carry no fail_streak."""
        rc = _FakeRedis()
        rc._hash["vk"] = json.dumps({"status": pp.FAILED, "at": 1})
        assert _record(rc, False) == pp.UNCONFIRMED


class TestPublicStatus:
    def _row(self, status):
        from app.api.routes import get_platform_status
        probes = {p: {"status": pp.OK} for p in pp.PROBE_PLATFORMS}
        probes["vk"] = {"status": status}
        with patch("app.core.lane_observer.observe_all_platforms", return_value=[]), \
             patch("app.core.platform_probe.get_probe_states", return_value=probes):
            r = asyncio.run(get_platform_status())
        return r, next(p for p in r["platforms"] if p["platform"] == "vk")

    def test_unconfirmed_is_unknown_and_trips_no_banner(self):
        r, row = self._row(pp.UNCONFIRMED)
        assert row["status"] == "unknown"
        assert row["reason"] == "probe_unconfirmed"
        assert r["degraded_count"] == 0 and r["all_healthy"] is True

    def test_confirmed_failure_still_degrades(self):
        r, row = self._row(pp.FAILED)
        assert row["status"] == "degraded" and row["reason"] == "probe_failed"
        assert r["degraded_count"] == 1


class TestAlertsWaitForConfirmation:
    def test_unconfirmed_does_not_alert(self):
        from app.tasks import probe_tasks

        class _R(_FakeRedis):
            def smembers(self, k):
                return set()

            def sadd(self, *a):
                pass

        with patch("app.tasks.probe_tasks.get_redis", return_value=_R()), \
             patch("app.core.platform_probe.get_redis", return_value=_R()), \
             patch("app.tasks.probe_tasks._send") as send:
            probe_tasks._handle_alerts({"vk": pp.UNCONFIRMED}, {"vk": pp.OK})
        send.assert_not_called()

    def test_sweep_feeds_the_recorded_status_to_alerts(self):
        """probe_all_platforms must pass record_probe's status, not the raw outcome."""
        from app.tasks import probe_tasks
        seen = {}
        with patch("app.tasks.probe_tasks.get_targets", return_value={"vk": "https://vk.com/x"}), \
             patch("app.tasks.probe_tasks.get_probe_states", return_value={}), \
             patch("app.tasks.probe_tasks.probe_once", return_value={"ok": False, "reason": "x", "ms": 1}), \
             patch("app.tasks.probe_tasks.record_probe", return_value=pp.UNCONFIRMED), \
             patch("app.tasks.probe_tasks._handle_alerts", side_effect=lambda res, was: seen.update(res)):
            fn = getattr(probe_tasks.probe_all_platforms, "run", probe_tasks.probe_all_platforms)
            fn()
        assert seen == {"vk": pp.UNCONFIRMED}


class TestProbeIsBounded:
    def test_a_hanging_extractor_times_out(self):
        release = threading.Event()

        def _hang(url, quality="video"):
            release.wait(5)
            return {"title": "late"}

        with patch("app.services.downloader.extract_video_info_sync", side_effect=_hang):
            r = pp.probe_once("https://vk.com/x", timeout=1)
        release.set()
        assert r["ok"] is False
        assert r["reason"] == "timeout_after_1s"
        assert r["ms"] < 3000

    def test_a_fast_extractor_is_unaffected(self):
        with patch("app.services.downloader.extract_video_info_sync",
                   return_value={"title": "VK Звонки"}):
            r = pp.probe_once("https://vk.com/x", timeout=5)
        assert r["ok"] is True and r["title"] == "VK Звонки"

    def test_default_timeout_is_generous(self):
        assert pp._PROBE_TIMEOUT_SEC >= 120, "Odysee needed >60s on a real probe"
