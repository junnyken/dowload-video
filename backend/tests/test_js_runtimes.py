"""PhantomJS presence report for yt-dlp's iq.com (iQIYI) extractor. No real binary needed."""
from types import SimpleNamespace

from app.core import js_runtimes


def test_not_found(monkeypatch):
    monkeypatch.setattr(js_runtimes.shutil, "which", lambda name: None)
    s = js_runtimes.phantomjs_status(refresh=True)
    assert s == {"found": False, "path": None, "version": None}
    assert "NOT found" in js_runtimes.startup_line()


def test_found(monkeypatch):
    monkeypatch.setattr(js_runtimes.shutil, "which", lambda name: "/usr/local/bin/phantomjs")
    monkeypatch.setattr(js_runtimes.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="2.1.1\n"))
    s = js_runtimes.phantomjs_status(refresh=True)
    assert s == {"found": True, "path": "/usr/local/bin/phantomjs", "version": "2.1.1"}
    assert "PhantomJS 2.1.1 found" in js_runtimes.startup_line()


def test_present_but_broken(monkeypatch):
    # e.g. the bare binary without the OPENSSL_CONF wrapper: exits 1
    monkeypatch.setattr(js_runtimes.shutil, "which", lambda name: "/usr/local/bin/phantomjs")
    monkeypatch.setattr(js_runtimes.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    s = js_runtimes.phantomjs_status(refresh=True)
    assert s["found"] is False and s["path"]
    assert "did not run" in js_runtimes.startup_line()
    js_runtimes._cache = None


def test_health_reports_it(monkeypatch):
    from app.main import _js_runtimes_status
    monkeypatch.setattr(js_runtimes.shutil, "which", lambda name: None)
    js_runtimes.phantomjs_status(refresh=True)
    assert _js_runtimes_status() == {"phantomjs": {"found": False, "path": None, "version": None}}
    js_runtimes._cache = None
