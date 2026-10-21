"""
21/10 — POST /channel/preview for the website's bulk page: lists a Chinese
channel without creating jobs. TestClient + fakeredis; no network, no paid call.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from tests._china_fakes import clean_env, flags_on, rc  # noqa: F401
from tests.test_client_douyin_api import TestSignedListing, _no_rate_limit, on  # noqa: F401
from tests.test_douyin_channel_listing import PROFILE

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _preview_unlimited():
    from app.api import channel_preview
    was = channel_preview.limiter.enabled
    channel_preview.limiter.enabled = False
    yield
    channel_preview.limiter.enabled = was


def _preview(url, **kw):
    return client.post("/api/v1/channel/preview", json={"url": url, **kw})


def test_douyin_lists_the_free_page_and_creates_no_job(on, rc, monkeypatch):
    TestSignedListing._signed_page(monkeypatch, rc, {"status_code": 0, "has_more": 1,
                                                     "aweme_list": [TestSignedListing._aweme("7001")]})
    from app.api import routes
    monkeypatch.setattr(routes, "bulk_download", lambda *a, **k: pytest.fail("no job"))
    r = _preview(PROFILE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["id"] for i in body["items"]] == ["7001"]
    assert body["hasMore"] is True and body["moreHint"] == "douyin_extension"
    assert on.starts() == []


@pytest.mark.parametrize("url,p", [
    ("https://www.xiaohongshu.com/user/profile/5f0000000000000000000001?xsec_token=x", "XIAOHONGSHU"),
    ("https://www.kuaishou.com/profile/3xabcdefg", "KUAISHOU"),
])
def test_xhs_kuaishou_guest_needs_sign_in(on, rc, monkeypatch, url, p):
    # Same flags as tests/test_client_xhs_channel.py: the route is open, the guest is not signed in.
    for k in (f"CHINA_ACCESS_{p}_ENABLED", f"CHINA_ACCESS_{p}_CHANNEL_ENABLED"):
        monkeypatch.setenv(k, "true")
    monkeypatch.setenv(f"CHINA_ACCESS_{p}_MANAGED_MODE", "on")
    r = _preview(url)
    assert r.status_code == 403 and r.json()["error_code"] == "login_required", r.text


def test_bilibili_space_maps_entries(monkeypatch, rc):
    from app.services import downloader
    seen = {}

    def fake(url, n, mv):
        seen["args"] = (url, n)
        return {"channel_title": "UP主", "entries": [
            {"url": "https://www.bilibili.com/video/BV1xx411c7mD", "title": "第一集"},
            {"url": "https://www.bilibili.com/video/BV1xx411c7mD?p=2", "title": "dup"},
            {"url": "https://evil.example/x", "title": "x"}]}
    monkeypatch.setattr(downloader, "scrape_channel_entries_sync", fake)
    r = _preview("https://space.bilibili.com/946974?spm_id_from=333", limit=50)
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen["args"] == ("https://space.bilibili.com/946974/video", 50)
    assert body["platform"] == "bilibili" and body["channelTitle"] == "UP主"
    assert body["items"] == [{"id": "BV1xx411c7mD", "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                              "title": "第一集", "thumbnail": None, "durationSec": None}]


@pytest.mark.parametrize("url", ["https://www.youtube.com/@abc", "https://space.bilibili.com.evil.example/1",
                                 "http://127.0.0.1/user/x"])
def test_other_hosts_are_refused(url, rc):
    r = _preview(url)
    assert r.status_code == 400 and r.json()["error_code"] == "unsupported_url"
