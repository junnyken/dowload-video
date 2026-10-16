"""POST /client/xiaohongshu/channel — the app's Xiaohongshu / RedNote profile
listing. TestClient + fakeredis + MockTransport: no network, no paid call."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.auth_middleware import get_optional_user
from app.main import app as fastapi_app, limiter
from app.services.china_platforms import profile_listing
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from tests._china_fakes import clean_env, flags_on, rc  # noqa: F401
from tests.test_china_profile_channel import TOKEN, XHS_UID, Api, xhs_row

client = TestClient(fastapi_app, raise_server_exceptions=False)
REDNOTE = (f"https://www.rednote.com/user/profile/{XHS_UID}"
           "?xsec_token=ABFk%3D&xsec_source=pc_feed")


@pytest.fixture(autouse=True)
def _no_rate_limit():
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
    flags_on.setenv("CHINA_ACCESS_XIAOHONGSHU_ENABLED", "true")
    flags_on.setenv("CHINA_ACCESS_XIAOHONGSHU_MANAGED_MODE", "on")
    flags_on.setenv("CHINA_ACCESS_XIAOHONGSHU_CHANNEL_ENABLED", "true")
    flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", TOKEN)
    api = Api([xhs_row(i) for i in range(3)])
    real = profile_listing.list_profile

    async def with_fake(platform, url, n, ctx=None, **kw):
        kw["provider_factory"] = lambda spec, m: ApifyProvider(
            spec, token=TOKEN, transport=httpx.MockTransport(api.handler), max_items=m)
        return await real(platform, url, n, ctx, **kw)
    monkeypatch.setattr(profile_listing, "list_profile", with_fake)
    return api


@pytest.fixture
def signed_in():
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-xhs-1", "email": "a@b.vn"}
    yield
    fastapi_app.dependency_overrides.pop(get_optional_user, None)


def _post(url=REDNOTE, limit=20):
    return client.post("/api/v1/client/xiaohongshu/channel", json={"url": url, "limit": limit})


def test_guest_must_sign_in(on, rc):
    r = _post()
    assert r.status_code == 403, r.text
    assert r.json()["error_code"] == "login_required"
    assert on.starts() == []                                   # nothing paid


def test_signed_in_lists_rednote_profile_with_token(on, rc, signed_in):
    r = _post()
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["platform"] == "xiaohongshu"
    assert body["channelUrl"] == f"https://www.xiaohongshu.com/user/profile/{XHS_UID}"
    assert len(body["items"]) == 3
    assert body["items"][0]["url"].startswith("https://www.xiaohongshu.com/explore/")
    sent = json.loads(on.starts()[0].content)["userUrls"]
    assert sent == [f"https://www.xiaohongshu.com/user/profile/{XHS_UID}?xsec_token=ABFk%3D&xsec_source=pc_feed"]


@pytest.mark.parametrize("url", [
    "https://www.douyin.com/user/MS4wLjABAAAA",
    "https://www.xiaohongshu.com.evil.example/user/profile/x",
    "https://notrednote.com/user/profile/x",
])
def test_other_hosts_refused(on, rc, signed_in, url):
    r = _post(url)
    assert r.status_code == 400 and r.json()["error_code"] == "unsupported_url"
    assert on.starts() == []
