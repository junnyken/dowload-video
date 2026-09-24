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
    """The probe calls extract_video_info_sync — the same entry point
    /fetch-link uses — rather than bare yt-dlp.

    The bare version got TikTok wrong on its first real run, in the most
    damaging direction: it reported an IP block and would have alerted, while
    the app returned a title and a playable URL for the same video. The app
    reaches TikTok through TikWM, whose servers fetch on our behalf, so a block
    on ours never touches it. A monitor that cries wolf gets muted, and takes
    the next real outage with it."""

    def _app(self, **kw):
        return patch("app.services.downloader.extract_video_info_sync", **kw)

    def test_a_raising_extractor_reports_a_failure_not_an_exception(self):
        with self._app(side_effect=RuntimeError("blocked")):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is False and "RuntimeError" in r["reason"]

    def test_success_false_is_a_failure_even_with_a_200_shaped_body(self):
        with self._app(return_value={"success": False, "error": "private_video"}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is False and r["reason"] == "private_video"

    def test_a_body_with_no_title_and_no_media_url_is_not_a_success(self):
        """However it labelled itself. An empty shell means extraction did not
        actually produce anything usable."""
        with self._app(return_value={"success": True}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is False and r["reason"] == "no_title_or_media_url"

    def test_empty_response_is_a_failure(self):
        with self._app(return_value={}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is False and r["reason"] == "no_metadata_returned"

    def test_a_title_alone_counts_as_working(self):
        with self._app(return_value={"success": True, "title": "Me at the zoo"}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is True and r["title"] == "Me at the zoo"

    def test_a_media_url_without_a_title_still_counts(self):
        with self._app(return_value={"success": True, "direct_mp4_url": "https://cdn/x.mp4"}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is True

    def test_reason_is_truncated_so_an_html_page_cannot_land_in_redis(self):
        with self._app(side_effect=RuntimeError("E" * 5000)):
            r = pp.probe_once("https://example.com/v")
        assert len(r["reason"]) <= 200

    def test_an_unimportable_downloader_is_reported_not_raised(self):
        with patch.dict("sys.modules", {"app.services.downloader": None}):
            r = pp.probe_once("https://example.com/v")
        assert r["ok"] is False and "probe_unavailable" in r["reason"]


class TestTargets:

    def test_defaults_apply_when_nothing_is_configured(self):
        with _with(_FakeRedis(targets=None)):
            assert pp.get_targets() == pp._DEFAULT_TARGETS

    def test_every_shipped_default_was_actually_verified(self):
        """Defaults are the one place a wrong URL turns the monitor into a liar
        on day one. Only platforms whose target was run through the real probe
        ship with one; the rest report not_configured until an operator sets
        them. Measured 2026-09-24: youtube and vk resolved, 19 others did not."""
        assert set(pp._DEFAULT_TARGETS) == {"youtube", "vk"}

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


class TestCoverageIsNotConfidence:
    """The summary field on /platform-status reintroduced, one level up, the
    defect the probes exist to remove: the first run after they shipped
    reported all_healthy=True while only 2 of 21 platforms had a probe
    configured. "Nothing is known to be broken" and "everything was checked
    and is fine" are different claims and need different fields."""

    def _status(self, lanes, probes):
        import asyncio
        from app.api.routes import get_platform_status
        with patch("app.core.lane_observer.observe_all_platforms", return_value=lanes), \
             patch("app.core.platform_probe.get_probe_states", return_value=probes):
            return asyncio.run(get_platform_status())

    def _all_unconfigured(self):
        return {p: {"status": pp.NOT_CONFIGURED} for p in pp.PROBE_PLATFORMS}

    def test_unchecked_platforms_block_the_fully_checked_claim(self):
        r = self._status([], self._all_unconfigured())
        assert r["fully_checked"] is False, (
            "claimed every platform was checked while none had a probe"
        )
        assert r["unmonitored_count"] == r["total_count"]

    def test_all_healthy_still_means_nothing_known_broken(self):
        """Kept separate on purpose: the user-facing banner keys off this, and
        a platform nobody has probed is no reason to alarm a user."""
        r = self._status([], self._all_unconfigured())
        assert r["all_healthy"] is True
        assert r["degraded_count"] == 0

    def test_fully_checked_only_when_everything_is_probed_and_ok(self):
        probes = {p: {"status": pp.OK} for p in pp.PROBE_PLATFORMS}
        r = self._status([], probes)
        assert r["fully_checked"] is True
        assert r["unmonitored_count"] == 0

    def test_a_failed_probe_degrades_the_platform_and_the_summary(self):
        probes = {p: {"status": pp.OK} for p in pp.PROBE_PLATFORMS}
        probes["tiktok"] = {"status": pp.FAILED}
        r = self._status([], probes)
        assert r["degraded_count"] == 1
        assert r["all_healthy"] is False and r["fully_checked"] is False
        assert next(p for p in r["platforms"] if p["platform"] == "tiktok")["reason"] == "probe_failed"

    def test_counts_add_up(self):
        probes = self._all_unconfigured()
        probes["youtube"] = {"status": pp.OK}
        r = self._status([], probes)
        assert r["monitored_count"] + r["unmonitored_count"] == r["total_count"]
        assert r["monitored_count"] == 1
