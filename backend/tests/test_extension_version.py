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
    assert app.get("/api/v1/extension/version").json() == {"version": "5.3.0"}
    assert app.get("/api/v1/extension/download").status_code == 200


def test_no_zip_or_broken_zip_says_unknown(app, tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_extension_zip_path", lambda: None)
    assert app.get("/api/v1/extension/version").json() == {"version": None}
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    monkeypatch.setattr(routes, "_extension_zip_path", lambda: str(bad))
    assert app.get("/api/v1/extension/version").json() == {"version": None}
