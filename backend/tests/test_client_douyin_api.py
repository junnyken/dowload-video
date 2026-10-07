"""
Task #6055 — /client/douyin/channel and /client/douyin/video for the Windows
app. TestClient + fakeredis + MockTransport: no network, no paid call.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app, limiter
from app.services.china_platforms import channel_listing
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from tests._china_fakes import SIGNED, clean_env, flags_on, rc  # noqa: F401
from tests.test_douyin_channel_listing import PROFILE, TOKEN, Api

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    # Other test modules reload app.main, so the limiter the route decorators
    # captured (client_douyin.limiter) may not be app.main.limiter any more.
    from app.api import client_douyin
    lims = {id(x): x for x in (limiter, client_douyin.limiter)}.values()
    prev = [(x, x.enabled) for x in lims]
    for x in lims:
        x.enabled = False
    yield
    for x, was in prev:
        x.enabled = was


@pytest.fixture
def on(flags_on, monkeypatch):
    flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", TOKEN)
    api = Api()
    real = channel_listing.list_douyin_profile

    async def with_fake(url, n, ctx=None, **kw):
        kw["provider_factory"] = lambda spec, m: ApifyProvider(
            spec, token=TOKEN, transport=httpx.MockTransport(api.handler), max_items=m)
        return await real(url, n, ctx, **kw)
    monkeypatch.setattr(channel_listing, "list_douyin_profile", with_fake)
    return api


def _channel(url=PROFILE, limit=20):
    return client.post("/api/v1/client/douyin/channel", json={"url": url, "limit": limit})


class TestChannel:

    def test_guest_lists_five(self, on, rc):
        r = _channel()
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cap"] == 5 and len(body["items"]) == 5
        assert body["channelUrl"] == PROFILE and body["items"][0]["url"].startswith("https://www.douyin.com/video/")
        assert "directUrl" not in body["items"][0]           # listing hands out no media URL

    def test_share_text_is_accepted(self, on, rc):
        r = _channel(url=f"Xem kênh này {PROFILE} 复制此链接")
        assert r.status_code == 200

    @pytest.mark.parametrize("url", ["https://evil.example/user/MS4wLjABAAAAx", "http://127.0.0.1/user/abc",
                                     "https://www.tiktok.com/@abc"])
    def test_non_douyin_host_is_refused(self, on, rc, url):
        r = _channel(url=url)
        assert r.status_code == 400 and r.json()["error_code"] == "unsupported_url"
        assert on.starts() == []

    def test_route_closed(self, on, rc, monkeypatch):
        monkeypatch.setenv("CHINA_ACCESS_DOUYIN_CHANNEL_ENABLED", "false")
        r = _channel()
        assert r.status_code == 503 and r.json()["error_code"] == "platform_disabled"

    # Task #6125: a guest app machine's scan cap comes from ITS allowance
    # (dev:<32 hex>), while provider budgets stay on the IP.
    def test_guest_machine_cap_is_per_machine(self, on, rc, monkeypatch):
        from app.core import quotas
        from app.services.china_platforms import budget_guard
        dev_a, dev_b = "a" * 64, "b" * 64
        ip = quotas.QuotaRequester(quotas.REQ_ANON, "testclient")
        for i in range(5):  # the IP's web bucket is full
            quotas.record_platform_download(ip, "tiktok", f"https://www.tiktok.com/@a/video/{i}")
        machine_a = quotas.QuotaRequester(quotas.REQ_DEVICE, dev_a[:32])
        for i in range(3):
            quotas.record_platform_download(machine_a, "tiktok", f"https://www.tiktok.com/@b/video/{i}")
        keys = []
        real_reserve = budget_guard.reserve
        monkeypatch.setattr(budget_guard, "reserve",
                            lambda platform, key, cand, *a, **k: keys.append(key) or real_reserve(platform, key, cand, *a, **k))

        r = client.post("/api/v1/client/douyin/channel", json={"url": PROFILE, "limit": 20},
                        headers={"X-VG-Device": dev_a})
        assert r.status_code == 200 and r.json()["cap"] == 2
        r = client.post("/api/v1/client/douyin/channel", json={"url": PROFILE, "limit": 20},
                        headers={"X-VG-Device": dev_b})
        assert r.status_code == 200 and r.json()["cap"] == 5
        assert keys and all(k.startswith("ip:") for k in keys)

    def test_guest_machine_out_of_downloads_cannot_scan(self, on, rc):
        from app.core import quotas
        dev = "c" * 64
        m = quotas.QuotaRequester(quotas.REQ_DEVICE, dev[:32])
        for i in range(5):
            quotas.record_platform_download(m, "tiktok", f"https://www.tiktok.com/@c/video/{i}")
        r = client.post("/api/v1/client/douyin/channel", json={"url": PROFILE, "limit": 20},
                        headers={"X-VG-Device": dev})
        assert r.status_code == 422 and r.json()["error_code"] == "quota_exceeded"
        assert on.starts() == []


class TestVideo:

    def test_listed_video_is_free_and_counts_one_download(self, on, rc):
        ch = _channel()
        assert ch.status_code == 200, ch.text
        items = ch.json()["items"]
        runs = len(on.starts())
        r = client.post("/api/v1/client/douyin/video", json={"url": items[0]["url"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["directUrl"] == SIGNED and body["cacheHit"] is True
        assert body["headers"]["Referer"] == "https://www.douyin.com/"
        assert body["expiresAt"].startswith("2033-")
        assert len(on.starts()) == runs                       # no second Apify run
        from app.core import quotas
        req = quotas.QuotaRequester(quotas.REQ_ANON, "testclient")
        assert quotas.platform_used(req, "douyin") == 1
        from app.core.quotas import _utc_day
        assert rc.hget(f"vidgrab:stats:route:{_utc_day()}", "server|ok") == "1"   # admin stats (#6090)

    def test_guest_out_of_downloads_gets_quota_body(self, on, rc):
        from app.core import quotas
        req = quotas.QuotaRequester(quotas.REQ_ANON, "testclient")
        for i in range(5):
            quotas.record_platform_download(req, "tiktok", f"https://www.tiktok.com/@a/video/{i}")
        r = client.post("/api/v1/client/douyin/video",
                        json={"url": "https://www.douyin.com/video/7300000000000000009"})
        assert r.status_code == 429 and r.json()["error_code"] == "quota_exceeded_daily"

    def test_non_douyin_refused(self, on, rc):
        r = client.post("/api/v1/client/douyin/video", json={"url": "https://www.youtube.com/watch?v=x"})
        assert r.status_code == 400
