"""The download page shows the version of the zip actually served (08/10:
the page said 5.2.5 while the file was 5.3.0)."""
import json
import zipfile

from app.api import routes


def test_version_comes_from_the_served_zip(app, tmp_path, monkeypatch):
    z = tmp_path / "VidGrab-extension.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("manifest.json", json.dumps({"manifest_version": 3, "version": "5.3.0"}))
    monkeypatch.setattr(routes, "_EXTENSION_ZIP_MOUNT", str(z))
    assert app.get("/api/v1/extension/version").json() == {"version": "5.3.0", "minSupported": None}
    assert app.get("/api/v1/extension/download").status_code == 200


def test_no_zip_or_broken_zip_says_unknown(app, tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_extension_zip_path", lambda: None)
    assert app.get("/api/v1/extension/version").json() == {"version": None, "minSupported": None}
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    monkeypatch.setattr(routes, "_extension_zip_path", lambda: str(bad))
    assert app.get("/api/v1/extension/version").json() == {"version": None, "minSupported": None}


def test_min_supported_empty_is_null_and_set_is_returned(app, tmp_path, monkeypatch):
    import fakeredis
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    monkeypatch.setattr(routes, "_extension_zip_path", lambda: None)
    assert app.get("/api/v1/extension/version").json()["minSupported"] is None
    r.set("admin:config:extension_min_version", "  ")
    assert app.get("/api/v1/extension/version").json()["minSupported"] is None
    r.set("admin:config:extension_min_version", "5.4.0")
    assert app.get("/api/v1/extension/version").json()["minSupported"] == "5.4.0"


def test_min_supported_survives_redis_down(app, monkeypatch):
    import fakeredis
    server = fakeredis.FakeServer()
    server.connected = False
    monkeypatch.setattr("app.core.redis_client._client", fakeredis.FakeRedis(server=server, decode_responses=True))
    monkeypatch.setattr(routes, "_extension_zip_path", lambda: None)
    assert app.get("/api/v1/extension/version").json() == {"version": None, "minSupported": None}


def test_setter_needs_admin_and_feeds_endpoint(app, monkeypatch):
    import fakeredis
    from app.main import app as fastapi_app
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    monkeypatch.setattr(routes, "_extension_zip_path", lambda: None)
    assert app.post("/api/v1/admin/config/extension_min_version", json={"value": "5.5.0"}).status_code in (401, 403)
    # override what the mounted routes hold (test_admin_security reloads app.api.admin)
    from tests._route_helpers import admin_dependencies
    deps = admin_dependencies(fastapi_app)
    for d in deps:
        fastapi_app.dependency_overrides[d] = lambda: None
    try:
        assert app.post("/api/v1/admin/config/extension_min_version", json={"value": "5.5.0"}).status_code == 200
    finally:
        for d in deps:
            fastapi_app.dependency_overrides.pop(d, None)
    assert app.get("/api/v1/extension/version").json()["minSupported"] == "5.5.0"


def test_cors_preflight_allows_ext_version_header(app):
    resp = app.options("/api/v1/extension/version", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "x-vg-ext-version,authorization",
    })
    assert resp.status_code == 200
    assert "x-vg-ext-version" in resp.headers["access-control-allow-headers"].lower()
