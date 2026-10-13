"""
Task #6332 follow-up — emergency switches operable from the admin page.

Holds: admin override wins over env, "clear" restores env · a dead Redis falls
back to env and never raises · watch scanning_allowed() honours the admin kill
switch · update_offer() returns None when the admin turns the updater off ·
China kill switches round-trip through /admin/switches · admin auth required ·
every POST audited. fakeredis only, no network.
"""
from __future__ import annotations

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.api import admin_switches as api_mod
from app.api.admin import verify_admin
from app.core import admin_switches as sw
from app.core import desktop_release as dr
from app.core import watch_config as cfg
from app.main import app as fastapi_app
from app.services.china_platforms import budget_guard

LIST = "/api/v1/admin/switches"
client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("WATCH_KILL_SWITCH", "WATCH_ENABLED", "DESKTOP_UPDATER_ENABLED",
              "CHINA_ACCESS_GLOBAL_KILL_SWITCH"):
        monkeypatch.delenv(k, raising=False)
    sw.reset_cache()
    yield
    sw.reset_cache()


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    return r


@pytest.fixture
def down(monkeypatch):
    server = fakeredis.FakeServer()
    server.connected = False
    monkeypatch.setattr("app.core.redis_client._client",
                        fakeredis.FakeRedis(server=server, decode_responses=True))


@pytest.fixture
def admin():
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    yield
    fastapi_app.dependency_overrides.pop(verify_admin, None)


@pytest.fixture
def audit(monkeypatch):
    calls = []
    monkeypatch.setattr(api_mod, "log_admin_action", lambda req, action, **kw: calls.append((action, kw)))
    return calls


def _by_key(payload):
    return {s["key"]: s for s in payload["switches"]}


# ── override layer ──────────────────────────────────────────────────────────

def test_no_override_keeps_env_behaviour(rc, monkeypatch):
    assert cfg.kill_switch() is False
    monkeypatch.setenv("WATCH_KILL_SWITCH", "true")
    sw.reset_cache()
    assert cfg.kill_switch() is True
    assert dr.updater_enabled() is True
    for v in ("0", "false", "No", "off"):
        monkeypatch.setenv("DESKTOP_UPDATER_ENABLED", v)
        assert dr.updater_enabled() is False


def test_admin_override_wins_and_clear_restores_env(rc, monkeypatch):
    monkeypatch.setenv("WATCH_KILL_SWITCH", "true")
    sw.set_override(sw.WATCH_KILL, "off", "admin", True, False)
    assert cfg.kill_switch() is False
    sw.set_override(sw.WATCH_KILL, "clear", "admin", False, True)
    assert cfg.kill_switch() is True

    monkeypatch.delenv("WATCH_KILL_SWITCH")
    sw.set_override(sw.WATCH_KILL, "on", "admin", False, True)
    assert cfg.kill_switch() is True

    monkeypatch.setenv("DESKTOP_UPDATER_ENABLED", "0")
    sw.set_override(sw.DESKTOP_UPDATER, "on", "admin", False, True)
    assert dr.updater_enabled() is True
    sw.set_override(sw.DESKTOP_UPDATER, "clear", "admin", True, False)
    assert dr.updater_enabled() is False


def test_redis_failure_falls_back_to_env(down, monkeypatch):
    assert cfg.kill_switch() is False
    monkeypatch.setenv("WATCH_KILL_SWITCH", "1")
    sw.reset_cache()
    assert cfg.kill_switch() is True
    assert dr.updater_enabled() is True
    monkeypatch.setenv("DESKTOP_UPDATER_ENABLED", "off")
    sw.reset_cache()
    assert dr.updater_enabled() is False


def test_failed_read_is_cached(monkeypatch):
    calls = []

    def boom(_key):
        calls.append(1)
        raise ConnectionError("down")

    monkeypatch.setattr(sw, "_read_override", boom)
    for _ in range(5):
        assert cfg.kill_switch() is False
    assert len(calls) == 1


def test_scanning_allowed_honours_admin_kill_switch(rc, monkeypatch):
    monkeypatch.setenv("WATCH_ENABLED", "true")
    assert cfg.scanning_allowed() is True
    sw.set_override(sw.WATCH_KILL, "on", "admin", False, True)
    assert cfg.scanning_allowed() is False
    sw.set_override(sw.WATCH_KILL, "clear", "admin", True, False)
    assert cfg.scanning_allowed() is True


def test_update_offer_none_when_admin_turns_updater_off(rc, monkeypatch):
    monkeypatch.setenv("DESKTOP_RELEASE_SOURCE", "github")
    called = []

    def fake_latest(fetch=None, fetch_sig=None):
        called.append(1)
        return None

    monkeypatch.setattr(dr, "github_latest", fake_latest)
    dr.update_offer("windows", "x86_64", "0.1.0")
    assert called, "updater on by default must reach the release lookup"
    called.clear()
    sw.set_override(sw.DESKTOP_UPDATER, "off", "admin", True, False)
    assert dr.update_offer("windows", "x86_64", "0.1.0") is None
    assert not called


# ── endpoints ───────────────────────────────────────────────────────────────

def test_admin_auth_required(rc):
    assert client.get(LIST).status_code in (401, 403)
    assert client.post(f"{LIST}/watch_kill_switch", json={"state": "on"}).status_code in (401, 403)
    assert rc.hget(sw.OVERRIDE_KEY, sw.WATCH_KILL) is None


def test_list_shows_state_and_source(rc, admin, monkeypatch):
    monkeypatch.setenv("DESKTOP_UPDATER_ENABLED", "0")
    s = _by_key(client.get(LIST).json())
    assert {"watch_kill_switch", "desktop_updater", "china_global", "china_douyin"} <= set(s)
    assert s["watch_kill_switch"]["state"] == "off" and s["watch_kill_switch"]["source"] == "default"
    assert s["desktop_updater"]["state"] == "off" and s["desktop_updater"]["source"] == "env"
    assert s["china_global"]["state"] == "off"
    assert all(v["label"] for v in s.values())


def test_post_override_audited_with_old_new(rc, admin, audit):
    r = client.post(f"{LIST}/watch_kill_switch", json={"state": "on"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["switch"]["state"] == "on" and body["switch"]["source"] == "admin"
    assert body["prior_state"] == "off"
    assert body["switch"]["last_change"]["by"].startswith("admin")
    assert body["switch"]["last_change"]["at"]
    sw.reset_cache()
    assert cfg.kill_switch() is True

    action, kw = audit[-1]
    assert action == "admin.switch.set" and kw["resource_id"] == "watch_kill_switch"
    assert kw["metadata"]["old"]["state"] == "off" and kw["metadata"]["new"]["state"] == "on"
    assert kw["metadata"]["by"].startswith("admin")

    r = client.post(f"{LIST}/watch_kill_switch", json={"state": "clear"})
    assert r.json()["switch"]["source"] == "default" and r.json()["switch"]["state"] == "off"
    assert len(audit) == 2


def test_china_switch_round_trip(rc, admin, audit):
    r = client.post(f"{LIST}/china_douyin", json={"state": "on"})
    assert r.status_code == 200, r.text
    assert budget_guard.platform_killswitch_on("douyin") is True
    assert r.json()["switch"]["source"] == "admin"
    r = client.post(f"{LIST}/china_global", json={"state": "on"})
    assert budget_guard.global_killswitch_on() is True
    s = _by_key(client.get(LIST).json())
    assert s["china_douyin"]["state"] == "on" and s["china_global"]["state"] == "on"
    client.post(f"{LIST}/china_douyin", json={"state": "off"})
    client.post(f"{LIST}/china_global", json={"state": "clear"})
    assert budget_guard.platform_killswitch_on("douyin") is False
    assert budget_guard.global_killswitch_on() is False
    assert [a for a, _ in audit] == ["admin.switch.set"] * 4


def test_china_global_env_forced_stays_on(rc, admin, audit, monkeypatch):
    monkeypatch.setenv("CHINA_ACCESS_GLOBAL_KILL_SWITCH", "true")
    r = client.post(f"{LIST}/china_global", json={"state": "off"})
    sw_ = r.json()["switch"]
    assert sw_["state"] == "on" and sw_["source"] == "env" and sw_["env_forced"] is True


def test_unknown_key_and_bad_state(rc, admin):
    assert client.post(f"{LIST}/nope", json={"state": "on"}).status_code == 404
    assert client.post(f"{LIST}/china_tiktok", json={"state": "on"}).status_code == 404
    assert client.post(f"{LIST}/watch_kill_switch", json={"state": "maybe"}).status_code == 422


def test_post_redis_down_is_503(down, admin, audit):
    r = client.post(f"{LIST}/watch_kill_switch", json={"state": "on"})
    assert r.status_code == 503
    assert audit == []
    s = _by_key(client.get(LIST).json())
    assert s["watch_kill_switch"]["readable"] is False
