"""
Task #6133 — Cobalt upgrade. Holds: the requested quality reaches Cobalt
(videoQuality) · an IP-refusal answer (error.api.fetch.fail) moves on to the
next instance while a link error does not · a platform that keeps failing is
tripped and Cobalt-first skips it until it expires, a success resets the
streak · admin status: admin only, instances without the key, per-platform
counts and trips · the YouTube health probe asks the configured instances.
fakeredis, no network.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

fakeredis = pytest.importorskip("fakeredis")

from app.api.admin import verify_admin  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.services import cobalt_service as cs  # noqa: E402
from app.services import downloader  # noqa: E402

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    return r


class _R:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d


def test_quality_mapping():
    assert cs.cobalt_video_quality("video") == "1080"
    assert cs.cobalt_video_quality("video_720") == "720"
    assert cs.cobalt_video_quality("video_4k") == "max"
    assert cs.cobalt_video_quality("video_fast") == "1080"
    assert cs.cobalt_video_quality(None) == "1080"


def test_requested_quality_reaches_cobalt(rc, monkeypatch):
    sent = []
    monkeypatch.setattr(cs, "_healthy_cobalt_instances", lambda: ["http://a/"])
    monkeypatch.setattr(cs.httpx, "post", lambda u, json, headers, timeout: sent.append(json) or
                        _R({"status": "error", "error": {"code": "error.api.content.video.unavailable"}}))
    cs.download_social_via_cobalt("https://x.com/a/status/1", "/tmp", "twitter", "video_720")
    assert sent[0]["videoQuality"] == "720"


def test_ip_refusal_moves_to_the_next_instance_but_a_link_error_does_not(rc, monkeypatch):
    calls = []
    answers = {"http://a/": {"status": "error", "error": {"code": "error.api.fetch.fail"}},
               "http://b/": {"status": "redirect", "url": "https://cdn/v.mp4"}}
    monkeypatch.setattr(cs, "_healthy_cobalt_instances", lambda: ["http://a/", "http://b/"])
    monkeypatch.setattr(cs.httpx, "post", lambda u, json, headers, timeout: calls.append(u) or _R(answers[u]))
    assert cs.fetch_cobalt_stream("https://www.tiktok.com/@a/video/1")["status"] == "redirect"
    assert calls == ["http://a/", "http://b/"]
    calls.clear()
    answers["http://a/"] = {"status": "error", "error": {"code": "error.api.link.invalid"}}
    assert cs.fetch_cobalt_stream("https://bad")["error"]["code"] == "error.api.link.invalid"
    assert calls == ["http://a/"]


def test_platform_trips_after_a_streak_and_a_success_resets(rc, monkeypatch):
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "instagram")
    assert downloader.cobalt_first("instagram", "video")
    cs.record_cobalt_outcome("instagram", False)
    cs.record_cobalt_outcome("instagram", False)
    cs.record_cobalt_outcome("instagram", True)            # streak reset
    cs.record_cobalt_outcome("instagram", False)
    cs.record_cobalt_outcome("instagram", False)
    assert not cs.cobalt_platform_tripped("instagram")
    cs.record_cobalt_outcome("instagram", False)            # 3 in a row
    assert cs.cobalt_platform_tripped("instagram")
    assert not downloader.cobalt_first("instagram", "video")
    assert 0 < rc.ttl("cobalt:trip:instagram") <= cs._TRIP_S
    assert not cs.cobalt_platform_tripped("facebook")
    stats = rc.hgetall(f"cobalt:stats:{cs._today()}")
    assert stats == {"instagram|fail": "5", "instagram|ok": "1"}


def test_a_failed_download_is_recorded_as_a_failure(rc, monkeypatch):
    monkeypatch.setattr(cs, "_download_social_via_cobalt", lambda *a: None)
    for _ in range(3):
        assert cs.download_social_via_cobalt("https://www.facebook.com/reel/1", "/tmp", "facebook") is None
    assert cs.cobalt_platform_tripped("facebook")


def test_admin_status(rc, monkeypatch):
    assert client.get("/api/v1/admin/cobalt/status").status_code in (401, 403)
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        monkeypatch.setattr(cs, "COBALT_API_URLS", ["https://dvid-cobalt.example/", "https://c2.example/"])
        monkeypatch.setattr(cs.httpx, "get", lambda u, timeout: _R({"cobalt": {"version": "11.7.1"}})
                            if "dvid" in u else (_ for _ in ()).throw(ConnectionError()))
        monkeypatch.setenv("COBALT_API_KEY", "should-never-appear")
        cs.record_cobalt_outcome("twitter", True)
        for _ in range(3):
            cs.record_cobalt_outcome("tiktok", False)
        rc.hincrby(f"cookie_last:stats:{cs._today()}", "facebook|cobalt_first_ok", 2)
        r = client.get("/api/v1/admin/cobalt/status?days=3")
        assert r.status_code == 200
        body = r.json()
        assert "should-never-appear" not in r.text
        assert [(i["host"], i["ok"], i["version"]) for i in body["instances"]] == [
            ("dvid-cobalt.example", True, "11.7.1"), ("c2.example", False, None)]
        assert body["platforms_total"]["twitter"] == {"ok": 1}
        assert body["platforms_total"]["tiktok"] == {"fail": 3}
        assert body["steps_total"]["facebook"] == {"cobalt_first_ok": 2}
        assert [t["platform"] for t in body["tripped"]] == ["tiktok"]
        assert body["days"] == 3 and len(body["day_keys"]) == 3
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_youtube_health_probes_the_configured_instances(rc, monkeypatch):
    import inspect
    from app.tasks import video_tasks
    src = inspect.getsource(video_tasks)
    assert '"http://cobalt-api:9000"' not in src and "/api/serverInfo" not in src
    assert "instances_status()" in src
