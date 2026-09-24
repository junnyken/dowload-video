"""
Active platform probes
======================
The monitor exists because 16 of 21 platforms carried no health signal, and a
platform nobody is using looks healthy because it is silent — the same way it
looks the moment it breaks. So every test here is about a way the monitor could
report the wrong thing, not about it running.

Two failure modes matter more than the rest and both get their own class:
reporting "we never looked" as "we looked and it was fine", and alerting on the
state rather than on the transition into it, which trains people to mute the
channel and takes the next real break with it.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.core import platform_probe as pp


class _FakeRedis:
    """Enough Redis for this module: one string key and one hash."""

    def __init__(self, targets=None, state=None, alerted=None):
        self._str = {}
        if targets is not None:
            self._str[pp._TARGETS_KEY] = json.dumps(targets)
        self._hash = {k: json.dumps(v) for k, v in (state or {}).items()}
        self._set = set(alerted or [])

    def get(self, k):
        return self._str.get(k)

    def set(self, k, v):
        self._str[k] = v

    def hset(self, key, field, value):
        self._hash[field] = value

    def hgetall(self, key):
        return dict(self._hash)

    def smembers(self, key):
        return set(self._set)

    def sadd(self, key, *vals):
        self._set.update(vals)

    def srem(self, key, *vals):
        self._set.difference_update(vals)


def _with(rc):
    return patch("app.core.platform_probe.get_redis", return_value=rc)


class TestUnknownIsNotHealthy:

    def test_platform_without_a_target_is_not_configured(self):
        with _with(_FakeRedis(targets={})):
            states = pp.get_probe_states()
        assert states["tiktok"]["status"] == pp.NOT_CONFIGURED
        assert states["tiktok"]["status"] != pp.OK, (
            "'nobody has checked' must never be reported as 'checked and fine' — "
            "that is exactly how a dead platform stays invisible"
        )

    def test_configured_but_never_probed_is_stale_not_ok(self):
        with _with(_FakeRedis(targets={"tiktok": "https://example.com/v"})):
            states = pp.get_probe_states()
        assert states["tiktok"]["status"] == pp.STALE
        assert states["tiktok"]["reason"] == "never_probed"

    def test_an_old_success_stops_counting_as_evidence(self):
        old = int(time.time()) - (pp._STALE_AFTER_SEC + 60)
        rc = _FakeRedis(
            targets={"tiktok": "https://example.com/v"},
            state={"tiktok": {"status": pp.OK, "at": old, "ms": 100, "reason": None}},
        )
        with _with(rc):
            s = pp.get_probe_states()["tiktok"]
        assert s["status"] == pp.STALE, "a success from hours ago says nothing about now"

    def test_a_fresh_success_is_ok(self):
        rc = _FakeRedis(
            targets={"tiktok": "https://example.com/v"},
            state={"tiktok": {"status": pp.OK, "at": int(time.time()) - 60,
                              "ms": 120, "reason": None}},
        )
        with _with(rc):
            s = pp.get_probe_states()["tiktok"]
        assert s["status"] == pp.OK and s["age_s"] is not None

    def test_every_listed_platform_is_reported(self):
        with _with(_FakeRedis(targets={})):
            states = pp.get_probe_states()
        assert set(states) == set(pp.PROBE_PLATFORMS), (
            "a platform missing from the report is a platform nobody watches"
        )


class TestProbeNeverGoesQuiet:

    def test_an_extractor_that_raises_returns_a_failure_not_an_exception(self):
        """A probe that throws is a probe that reports nothing, and silence is
        indistinguishable from health."""
        boom = MagicMock()
        boom.return_value.__enter__.return_value.extract_info.side_effect = RuntimeError("blocked")
        with patch.dict("sys.modules", {"yt_dlp": MagicMock(YoutubeDL=boom)}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is False
        assert "RuntimeError" in r["reason"]

    def test_empty_metadata_is_a_failure(self):
        ydl = MagicMock()
        ydl.return_value.__enter__.return_value.extract_info.return_value = None
        with patch.dict("sys.modules", {"yt_dlp": MagicMock(YoutubeDL=ydl)}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is False and r["reason"] == "no_metadata_returned"

    def test_reason_is_truncated_so_an_html_page_cannot_land_in_redis(self):
        ydl = MagicMock()
        ydl.return_value.__enter__.return_value.extract_info.side_effect = RuntimeError("x" * 5000)
        with patch.dict("sys.modules", {"yt_dlp": MagicMock(YoutubeDL=ydl)}):
            r = pp.probe_once("https://example.com/v")
        assert len(r["reason"]) <= 200

    def test_success_reports_title_and_duration(self):
        ydl = MagicMock()
        ydl.return_value.__enter__.return_value.extract_info.return_value = {"title": "Me at the zoo"}
        with patch.dict("sys.modules", {"yt_dlp": MagicMock(YoutubeDL=ydl)}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is True and r["title"] == "Me at the zoo" and r["ms"] >= 0


class TestTargets:

    def test_defaults_apply_when_nothing_is_configured(self):
        with _with(_FakeRedis(targets=None)):
            assert pp.get_targets() == pp._DEFAULT_TARGETS

    def test_configured_target_overrides_the_default(self):
        with _with(_FakeRedis(targets={"youtube": "https://custom/v"})):
            assert pp.get_targets()["youtube"] == "https://custom/v"

    def test_empty_value_clears_a_default_rather_than_keeping_it(self):
        with _with(_FakeRedis(targets={"youtube": ""})):
            assert "youtube" not in pp.get_targets()

    def test_unknown_platform_is_rejected(self):
        with _with(_FakeRedis()):
            with pytest.raises(ValueError):
                pp.set_target("myspace", "https://example.com/v")

    def test_set_target_round_trips(self):
        rc = _FakeRedis()
        with _with(rc):
            pp.set_target("tiktok", "https://example.com/v")
            assert pp.get_targets()["tiktok"] == "https://example.com/v"


class TestAlertsFireOnTransition:
    """Alerting on the state instead of the change into it sends the same
    message every 30 minutes for a platform that has been broken for days. The
    channel gets muted, and the next real break is muted with it."""

    def _run(self, rc, results, was):
        from app.tasks import probe_tasks
        with patch("app.tasks.probe_tasks.get_redis", return_value=rc), \
             patch("app.core.platform_probe.get_redis", return_value=rc), \
             patch("app.tasks.probe_tasks._send") as send:
            probe_tasks._handle_alerts(results, was)
        return send

    def test_a_new_break_alerts(self):
        send = self._run(_FakeRedis(), {"tiktok": pp.FAILED}, {"tiktok": pp.OK})
        send.assert_called_once()
        assert "tiktok" in send.call_args[0][0]

    def test_an_ongoing_break_does_not_alert_again(self):
        send = self._run(_FakeRedis(alerted=["tiktok"]),
                         {"tiktok": pp.FAILED}, {"tiktok": pp.FAILED})
        send.assert_not_called()

    def test_recovery_alerts_and_rearms(self):
        rc = _FakeRedis(alerted=["tiktok"])
        send = self._run(rc, {"tiktok": pp.OK}, {"tiktok": pp.FAILED})
        send.assert_called_once()
        assert "tiktok" not in rc.smembers("x"), "must re-arm, or the next break is silent"

    def test_a_platform_that_stays_healthy_is_silent(self):
        send = self._run(_FakeRedis(), {"tiktok": pp.OK}, {"tiktok": pp.OK})
        send.assert_not_called()
