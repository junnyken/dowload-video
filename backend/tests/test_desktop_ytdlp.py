"""
Task #6333 (Phase 34A-3) — the yt-dlp an admin approves for the Windows app.
Holds: admin auth on GET/PUT /admin/desktop/ytdlp · strict validation (url
must be the official GitHub release asset of THAT version, sha256 64 hex,
version YYYY.MM.DD[.N]) · default off → GET /client/ytdlp 204 · enabled →
{version, url, sha256} · disabling again → 204 (rollback) · audited ·
/client/ytdlp is not blocked by the update gate. fakeredis, no network.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import admin_desktop
from app.core import update_gate
from app.main import app as fastapi_app
from tests._china_fakes import rc  # noqa: F401
from tests.test_admin_desktop import admin  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
ADMIN = "/api/v1/admin/desktop/ytdlp"
PUBLIC = "/api/v1/client/ytdlp"
V = "2026.10.10"
URL = f"https://github.com/yt-dlp/yt-dlp/releases/download/{V}/yt-dlp.exe"
SHA = "a" * 64


@pytest.fixture
def audit(monkeypatch):
    calls = []
    monkeypatch.setattr(admin_desktop, "log_admin_action",
                        lambda request, action, **kw: calls.append((action, kw)))
    return calls


def good(**kw):
    body = {"version": V, "url": URL, "sha256": SHA, "enabled": True}
    body.update(kw)
    return body


def test_admin_auth_required(rc):
    assert client.get(ADMIN).status_code in (401, 403)
    assert client.put(ADMIN, json=good()).status_code in (401, 403)
    assert rc.get("vidgrab:desktop:ytdlp") is None


def test_default_off_public_204(rc, admin):
    assert client.get(PUBLIC).status_code == 204
    r = client.get(ADMIN)
    assert r.status_code == 200
    assert r.json()["configured"] is False and r.json()["enabled"] is False


@pytest.mark.parametrize("bad, code", [
    ({"url": "https://example.com/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp.exe"}, "bad_url"),
    ({"url": "http://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp.exe"}, "bad_url"),
    ({"url": "https://github.com/evil/yt-dlp/releases/download/2026.10.10/yt-dlp.exe"}, "bad_url"),
    ({"url": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.09/yt-dlp.exe"}, "bad_url"),
    ({"url": "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10/yt-dlp_x86.exe"}, "bad_url"),
    ({"url": URL + "?x=1"}, "bad_url"),
    ({"version": "2026.10"}, "bad_version"),
    ({"version": "2026.13.01"}, "bad_version"),
    ({"version": "../2026.10.10"}, "bad_version"),
    ({"sha256": "a" * 63}, "bad_sha256"),
    ({"sha256": "g" * 64}, "bad_sha256"),
    ({"sha256": ""}, "bad_sha256"),
    ({"enabled": "yes"}, "bad_enabled"),
])
def test_validation(rc, admin, audit, bad, code):
    r = client.put(ADMIN, json=good(**bad))
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["error"] == code
    assert rc.get("vidgrab:desktop:ytdlp") is None
    assert audit == []


def test_enable_then_disable(rc, admin, audit):
    r = client.put(ADMIN, json=good(sha256=SHA.upper()))
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True and r.json()["sha256"] == SHA

    p = client.get(PUBLIC)
    assert p.status_code == 200
    assert p.json() == {"version": V, "url": URL, "sha256": SHA}

    action, kw = audit[0]
    assert action == "admin.desktop.ytdlp_set" and kw["metadata"]["enabled"] is True

    # Rollback: untick "enabled" → the app goes back to its bundled yt-dlp.
    assert client.put(ADMIN, json=good(enabled=False)).status_code == 200
    assert client.get(PUBLIC).status_code == 204
    g = client.get(ADMIN).json()
    assert g["configured"] is True and g["version"] == V and g["enabled"] is False


def test_url_defaults_to_the_official_asset(rc, admin, audit):
    body = good(version="2026.10.10.1")
    body.pop("url")
    r = client.put(ADMIN, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["url"] == "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.10.1/yt-dlp.exe"


def test_garbage_in_redis_is_ignored(rc, admin):
    rc.set("vidgrab:desktop:ytdlp", '{"version":"x","url":"https://evil/","sha256":"1","enabled":true}')
    assert client.get(PUBLIC).status_code == 204


def test_not_blocked_by_update_gate(rc, monkeypatch):
    monkeypatch.setenv("CLIENT_UPDATE_GATE_ENABLED", "true")
    monkeypatch.setenv("DESKTOP_MIN_VERSION", "9.0.0")
    assert PUBLIC in update_gate.ALWAYS_OPEN
    r = client.get(PUBLIC, headers={"X-VG-Source": "desktop", "X-VG-Client": "0.13.1"})
    assert r.status_code == 204
