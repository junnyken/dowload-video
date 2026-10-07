"""
Task #6125 (PLAN-32E P1 step 1) — 426 update_required for Windows app builds
below DESKTOP_MIN_VERSION. Off by default; the website is never gated;
GET /client/version always answers; CORS headers present on the 426.
"""
import pytest
from fastapi.testclient import TestClient

from app.core import update_gate
from app.main import app as fastapi_app

client = TestClient(fastapi_app, raise_server_exceptions=False)
APP = {"Origin": "http://tauri.localhost"}


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("CLIENT_UPDATE_GATE_ENABLED", "true")
    monkeypatch.setenv("DESKTOP_MIN_VERSION", "0.6.0")
    monkeypatch.setenv("DESKTOP_LATEST_VERSION", "0.9.0")
    monkeypatch.setenv("DESKTOP_DOWNLOAD_URL", "https://dvid.vibe1.tinhgon.xyz/download")
    return monkeypatch


def _quota(headers):
    return client.get("/api/v1/client/quota", headers=headers)


class TestParse:
    @pytest.mark.parametrize("raw,want", [("0.8.0", (0, 8, 0)), ("v1.2.3-beta", (1, 2, 3)),
                                          ("", None), (None, None), ("abc", None), ("1.2", None)])
    def test_parse(self, raw, want):
        assert update_gate.parse_version(raw) == want


class TestGate:
    def test_old_app_gets_426_with_install_info(self, gate_on):
        r = _quota({**APP, "X-VG-Client": "0.5.9"})
        assert r.status_code == 426
        body = r.json()
        assert body["error_code"] == "update_required" and body["minSupported"] == "0.6.0"
        assert body["latest"] == "0.9.0" and body["downloadUrl"].endswith("/download")
        assert r.headers.get("access-control-allow-origin") == "http://tauri.localhost"

    def test_app_without_version_header_is_too_old(self, gate_on):
        assert _quota(APP).status_code == 426

    def test_exact_minimum_passes(self, gate_on):
        assert _quota({**APP, "X-VG-Client": "0.6.0"}).status_code != 426

    def test_desktop_source_header_counts_as_app(self, gate_on):
        r = client.post("/api/v1/fetch-link", json={"url": "https://www.tiktok.com/@a/video/1"},
                        headers={"X-VG-Source": "desktop", "X-VG-Client": "0.5.0"})
        assert r.status_code == 426

    def test_version_route_always_answers(self, gate_on):
        r = client.get("/api/v1/client/version", headers={**APP, "X-VG-Client": "0.1.0"})
        assert r.status_code == 200 and r.json()["minSupported"] == "0.6.0"

    def test_preflight_is_never_gated(self, gate_on):
        r = client.options("/api/v1/client/quota", headers={
            **APP, "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-vg-client,x-vg-device"})
        assert r.status_code == 200


class TestNeverGated:
    def test_website_without_app_headers(self, gate_on):
        r = client.post("/api/v1/fetch-link", json={"url": "not a url"})
        assert r.status_code != 426

    def test_flag_off(self, monkeypatch):
        monkeypatch.delenv("CLIENT_UPDATE_GATE_ENABLED", raising=False)
        monkeypatch.setenv("DESKTOP_MIN_VERSION", "9.9.9")
        assert _quota({**APP, "X-VG-Client": "0.1.0"}).status_code != 426

    def test_other_routes_from_the_app(self, gate_on):
        r = client.get("/health", headers={**APP, "X-VG-Client": "0.1.0"})
        assert r.status_code != 426
