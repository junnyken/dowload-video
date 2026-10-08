"""Task #6148 — failed downloads keep their reason where an admin can read it.

An owner's extension download failed with HTTP 500 on 08/10 and the cause was
never found: the API's logs are not readable from the hosting panel and are
wiped on redeploy; the outcome counters keep only a code.

  [1] redact(): no proxy password, key=/token= value, Bearer or cookie text
  [2] record()/read(): newest first, capped, filter by platform, never raises
  [3] /fetch-link failure → one row with the RAW reason (before the friendly
      rewrite), platform, status, guest/user, source, quality asked→served
  [4] unhandled exception on any /api/v1 route → one row with where it was
  [5] /admin/errors returns the rows
"""
import json

import fakeredis
import pytest

from app.api.admin import verify_admin   # bound at import, like the router (a test reloads app.api.admin)
from app.core import recent_errors as re_
from app.main import app as fastapi_app
from tests.test_fetch_link_uuid_scope import route  # noqa: F401 — fixture


# ── [1] ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,secret", [
    ("ProxyError: http://user123:s3cr3tPass@gate.dataimpulse.com:823 refused", "s3cr3tPass"),
    ("GET https://api.scraperapi.com/?api_key=ee4213d3abcdef&url=x 400", "ee4213d3abcdef"),
    ("https://cdn.x/v.mp4?sig=AbCdEf123&expire=1", "AbCdEf123"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "dozjgNryP4J3"),
    ("Cookie: sessionid=987654321abc; csrftoken=zzz", "987654321abc"),
    ("password=hunter2 rejected", "hunter2"),
])
def test_redact_removes_secrets(raw, secret):
    out = re_.redact(raw)
    assert secret not in out
    assert "***" in out


def test_redact_keeps_the_useful_part_and_caps_length():
    out = re_.redact("ERROR: [youtube] VZUN9GCkyQw: Sign in to confirm you're not a bot")
    assert "Sign in to confirm" in out and "VZUN9GCkyQw" in out
    assert len(re_.redact("too slow " * 500)) == re_.REASON_MAX


def test_url_brief_drops_the_query():
    assert re_.url_brief("https://www.youtube.com/watch?v=abc&list=RD1") == "www.youtube.com/watch"
    assert re_.url_brief("https://x.com/u/status/1?t=SECRET") == "x.com/u/status/1"


@pytest.mark.parametrize("origin,src,want", [
    ("chrome-extension://abcdef", None, "extension"),
    ("http://tauri.localhost", None, "app"),
    (None, "desktop", "app"),
    ("https://dvid.vibe1.tinhgon.xyz", None, "web"),
    (None, None, "api"),
])
def test_client_source(origin, src, want):
    assert re_.client_source(origin, src) == want


# ── [2] ──────────────────────────────────────────────────────────────────────

def test_record_read_newest_first_capped_and_filtered(monkeypatch):
    rc = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(re_, "MAX_ROWS", 5)
    for i in range(8):
        assert re_.record(platform="douyin" if i % 2 else "youtube", status=500,
                          error_code="processing_failed", reason=f"r{i}", rc=rc)
    rows = re_.read(50, rc=rc)
    assert [r["reason"] for r in rows] == ["r7", "r6", "r5", "r4", "r3"]
    assert rc.llen(re_.KEY) == 5 and 0 < rc.ttl(re_.KEY) <= re_.TTL_S
    assert [r["reason"] for r in re_.read(50, platform="douyin", rc=rc)] == ["r7", "r5", "r3"]


def test_record_never_raises_and_read_says_unknown_on_redis_failure():
    class Broken:
        def pipeline(self):
            raise ConnectionError("down")

        def lrange(self, *a):
            raise ConnectionError("down")
    assert re_.record(platform="x", status=500, error_code="e", reason="r", rc=Broken()) is False
    assert re_.read(rc=Broken()) is None


# ── [3] ──────────────────────────────────────────────────────────────────────

def _quota_ok(route, monkeypatch):
    monkeypatch.setattr(route, "check_platform_quota", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(route, "increment_usage", lambda *a, **k: None)


def test_fetch_link_failure_keeps_the_raw_reason(app, route, monkeypatch):
    async def _fail(url, *a, **k):
        raise ValueError("Không thể trích xuất thông tin video. (Lý do kỹ thuật: "
                         "HTTP Error 418 via http://u:pw999@proxy.example:80)")
    monkeypatch.setattr(route, "extract_video_info", _fail)
    _quota_ok(route, monkeypatch)
    r = app.post("/api/v1/fetch-link",
                 json={"url": "https://www.instagram.com/p/abc/?igsh=TOKEN", "quality": "video",
                       "user_cookies_b64": "c2Vzc2lvbmlkPTE="},
                 headers={"Origin": "chrome-extension://abc"})
    assert r.status_code >= 400
    rows = re_.read()
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == "/api/v1/fetch-link" and row["platform"] == "instagram"
    assert row["status"] == r.status_code and row["error_code"]
    assert row["reason"].startswith("ValueError: Không thể trích xuất") and "HTTP Error 418" in row["reason"]
    assert "pw999" not in row["reason"]
    assert row["url"] == "www.instagram.com/p/abc/"           # no query
    assert row["kind"] == "guest" and row["source"] == "extension" and row["user_cookies"] is True
    assert "c2Vzc2lvbmlkPTE" not in json.dumps(row)


def test_fetch_link_success_writes_no_row(app, route, monkeypatch):
    async def _ok(url, *a, **k):
        return {"title": "t", "direct_mp4_url": "https://cdn.example.com/v.mp4", "original_url": url}
    monkeypatch.setattr(route, "extract_video_info", _ok)
    _quota_ok(route, monkeypatch)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.core.metering.record_download", _noop)
    r = app.post("/api/v1/fetch-link", json={"url": "https://www.instagram.com/p/abc/", "quality": "video"})
    assert r.status_code == 200, r.text[:300]
    assert re_.read() == []


# ── [4] ──────────────────────────────────────────────────────────────────────

def test_unhandled_exception_is_kept_with_its_location(app, route, monkeypatch):
    import app.api.routes as routes

    def _boom(*a, **k):
        raise KeyError("downloaded_vcodec")
    # _preflight_disk_check runs at the top of fetch_link, before its try
    monkeypatch.setattr(routes, "_preflight_disk_check", _boom)
    r = app.post("/api/v1/fetch-link", json={"url": "https://www.instagram.com/p/abc/", "quality": "video"})
    assert r.status_code == 500
    rows = [x for x in re_.read() if x["error_code"] == "unhandled"]
    assert len(rows) == 1
    assert rows[0]["path"] == "POST /api/v1/fetch-link"
    assert "KeyError" in rows[0]["reason"] and "downloaded_vcodec" in rows[0]["reason"]
    assert "@ " in rows[0]["reason"]                          # file:line it was raised at


# ── [5] ──────────────────────────────────────────────────────────────────────

def test_admin_errors_returns_recent_attempts(app, route, monkeypatch):
    re_.record(platform="douyin", status=503, error_code="temporary_blocked", reason="r1")
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        r = app.get("/api/v1/admin/errors")
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)
    assert r.status_code == 200, r.text[:300]
    rows = r.json()["recent_attempts"]
    assert rows and rows[0]["platform"] == "douyin" and rows[0]["reason"] == "r1"
