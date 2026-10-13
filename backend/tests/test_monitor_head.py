"""Phase 34A-1: uptime monitors (UptimeRobot Free) check with HEAD."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_head_on_monitor_paths_is_answered_like_get_without_body():
    for path in ("/health", "/api/v1/client/version"):
        g = client.get(path)
        h = client.head(path)
        assert h.status_code == g.status_code == 200, path
        assert h.content == b""


def test_head_watch_status_keeps_the_guest_refusal():
    assert client.head("/api/v1/watch/status").status_code == client.get("/api/v1/watch/status").status_code


def test_other_paths_are_untouched():
    assert client.head("/api/v1/admin/takedown").status_code in (401, 403, 405)
    assert client.post("/health").status_code == 405
