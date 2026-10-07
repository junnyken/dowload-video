"""
Task #6055 — Douyin whole-channel listing through the managed Apify route.

Holds:
  * one Apify run per profile, input {"profileUrls", "maxItemsPerUrl"} and the
    run-level maxItems both = N;
  * N = min(requested, what the requester can still download today), admin
    up to CHINA_ACCESS_DOUYIN_CHANNEL_ADMIN_MAX; 0 left → refused, no run;
  * the whole estimate (N × per-video) is reserved and settled like any paid
    call, and the pool/budget counters move;
  * each listed video lands in the router cache: the per-video resolve that
    follows is a cache hit — no second paid call for the same video;
  * a second scan of the same profile reuses the list (no new run);
  * route closed (flag / mode / no token / kill switch) → None from the
    integration hook (legacy scrapers keep running), no run.
httpx.MockTransport + fakeredis only: no network, no paid call.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services.china_platforms import budget_guard, channel_listing, integration, request_cache
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    RequestContext,
    reset_context,
    set_context,
)
from app.services.china_platforms.provider_router import ProviderRouter
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from tests._china_fakes import SIGNED, FakeProvider, clean_env, flags_on, rc  # noqa: F401

TOKEN = "apify_api_TESTSECRETTOKEN123456"
SEC_UID = "MS4wLjABAAAAtestSecUid_abcdefghijklmnop"
PROFILE = f"https://www.douyin.com/user/{SEC_UID}"
GUEST = RequestContext(requester_key="ip:198.51.100.7", origin="worker")
USER = RequestContext(requester_key="user:u-123", origin="worker")
ADMIN = RequestContext(requester_key="admin", is_admin=True, origin="worker")


def _item(i: int) -> dict:
    vid = f"73000000000000{i:05d}"
    return {
        "id": vid,
        "text": f"video {i}",
        "url": f"https://www.douyin.com/video/{vid}",
        "authorMeta": {"name": "Bếp Nhà Test", "nickName": "Bếp Nhà Test"},
        "videoMeta": {"playUrl": SIGNED, "cover": "https://p9-pc-sign.douyinpic.com/c.jpeg", "width": 720},
    }


class Api:
    def __init__(self, n_items=30, *, start_status=201, cost=0.0003, run_status="SUCCEEDED"):
        self.requests: list[httpx.Request] = []
        self.items = [_item(i) for i in range(n_items)]
        self.start_status = start_status
        self.run = {"id": "RUN1", "status": run_status, "defaultDatasetId": "DS1", "usageTotalUsd": cost}

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        p = req.url.path
        if req.method == "POST" and p.endswith("/runs"):
            if self.start_status not in (200, 201):
                return httpx.Response(self.start_status, text="nope")
            return httpx.Response(201, json={"data": self.run})
        if req.method == "GET" and "/actor-runs/" in p:
            return httpx.Response(200, json={"data": self.run})
        if req.method == "GET" and "/datasets/" in p:
            limit = int(req.url.params.get("limit", "1"))
            return httpx.Response(200, json=self.items[:limit])
        return httpx.Response(404)

    def starts(self):
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith("/runs")]


def _factory(api: Api):
    return lambda spec, n: ApifyProvider(spec, token=TOKEN, transport=httpx.MockTransport(api.handler),
                                         max_items=n)


@pytest.fixture
def on(flags_on):
    flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", TOKEN)     # pool "env" entry → route open
    flags_on.setenv("CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT", "5")
    return flags_on


def _list(ctx, n, api, url=PROFILE):
    return asyncio.run(channel_listing.list_douyin_profile(url, n, ctx, provider_factory=_factory(api)))


def _use_downloads(rc, key: str, n: int):
    """Simulate n downloads already counted today (total bucket)."""
    from app.core import quotas
    req = quotas.QuotaRequester.from_key(key)
    for i in range(n):
        quotas.record_platform_download(req, "tiktok", f"https://www.tiktok.com/@a/video/{i}")


class TestCap:

    def test_guest_is_capped_at_remaining_downloads(self, on, rc):
        api = Api()
        listing = _list(GUEST, 50, api)
        assert listing.cap == 5 and len(listing.videos) == 5
        start = api.starts()[0]
        assert json.loads(start.content) == {"profileUrls": [PROFILE], "maxItemsPerUrl": 5,
                                             "profileSortFilter": "latest"}
        assert start.url.params["maxItems"] == "5"
        assert len(api.starts()) == 1

    def test_guest_cap_counts_downloads_already_used(self, on, rc):
        _use_downloads(rc, GUEST.requester_key, 3)
        api = Api()
        assert len(_list(GUEST, 50, api).videos) == 2

    def test_signed_in_cap_is_20(self, on, rc):
        api = Api()
        assert len(_list(USER, 100, api).videos) == 20

    def test_requested_below_cap_is_kept(self, on, rc):
        api = Api()
        assert len(_list(USER, 7, api).videos) == 7

    def test_admin_up_to_admin_max(self, on, rc):
        api = Api(n_items=150)
        assert len(_list(ADMIN, 500, api).videos) == 100
        on.setenv("CHINA_ACCESS_DOUYIN_CHANNEL_ADMIN_MAX", "30")
        rc.flushall()
        assert len(_list(ADMIN, 500, Api(n_items=150)).videos) == 30

    def test_no_allowance_left_refuses_without_a_run(self, on, rc):
        _use_downloads(rc, GUEST.requester_key, 5)
        api = Api()
        with pytest.raises(channel_listing.ChannelListingError) as ei:
            _list(GUEST, 20, api)
        assert ei.value.code == "quota_exceeded" and "Đăng nhập" in str(ei.value)
        assert api.starts() == []

    def test_signed_in_no_allowance_names_reset_time(self, on, rc):
        _use_downloads(rc, USER.requester_key, 20)
        with pytest.raises(channel_listing.ChannelListingError) as ei:
            _list(USER, 20, Api())
        assert "07:00" in str(ei.value) and "đã dùng hết lượt tải" in str(ei.value)


class TestCostAndCache:

    def test_reserves_n_times_estimate_and_floors_at_it(self, on, rc):
        on.setenv("CHINA_ACCESS_COST_FLOOR_AT_ESTIMATE", "true")
        listing = _list(USER, 10, Api(cost=0.0002))          # vendor under-reports
        day = budget_guard.day_key()
        spent = int(rc.get(budget_guard.k_provider_spend(day, "apify")))
        assert spent == pytest.approx(10 * 7100, abs=2)       # 10 × $0.0071 in micro-USD
        assert listing.cost_usd == pytest.approx(0.071, abs=1e-5)
        assert int(rc.get(budget_guard.k_platform_calls(day, "douyin"))) == 1   # one call, N videos

    def test_listed_video_resolve_is_a_cache_hit_without_paying(self, on, rc):
        listing = _list(GUEST, 5, Api())
        calls: list = []
        paid = FakeProvider("apify_douyin", paid=True, calls=calls)
        native = FakeProvider("native_douyin", outcome="cookie_required", calls=calls)
        router = ProviderRouter(lambda p: {"native_douyin": native, "apify_douyin": paid})
        res = asyncio.run(router.resolve(ChinaResolveRequest(url=listing.videos[0].url), GUEST))
        assert res.cache_hit is True and res.primary_video_url() == SIGNED
        assert calls == []                                     # neither provider ran
        assert res.title == "video 0"

    def test_second_scan_reuses_the_list(self, on, rc):
        api = Api()
        _list(USER, 10, api)
        again = _list(USER, 8, api)
        assert again.from_cache and len(again.videos) == 8 and len(api.starts()) == 1
        bigger = _list(USER, 15, api)                          # asks for more → a new run
        assert not bigger.from_cache and len(api.starts()) == 2

    def test_short_channel_list_covers_a_bigger_request(self, on, rc):
        api = Api(n_items=3)
        _list(USER, 10, api)                                   # channel has only 3
        again = _list(USER, 20, api)
        assert again.from_cache and len(again.videos) == 3 and len(api.starts()) == 1

    def test_channel_title_and_bulk_shape(self, on, rc):
        out = _list(GUEST, 5, Api()).to_bulk_result()
        assert out["channel_title"] == "Bếp Nhà Test"
        assert out["total_queued"] == 5 and out["entries"][0]["url"].startswith("https://www.douyin.com/video/")


class TestFailures:

    def test_run_failure_is_upstream_message_and_settles(self, on, rc):
        with pytest.raises(channel_listing.ChannelListingError) as ei:
            _list(USER, 5, Api(run_status="FAILED"))
        assert ei.value.code == "provider_unavailable"
        assert str(ei.value).startswith("Nền tảng nguồn tạm thời không phản hồi")

    def test_empty_dataset_is_empty_channel(self, on, rc):
        with pytest.raises(channel_listing.ChannelListingError) as ei:
            _list(USER, 5, Api(n_items=0))
        assert ei.value.code == "no_media_found"

    def test_bad_url(self, on, rc):
        with pytest.raises(channel_listing.ChannelListingError) as ei:
            _list(USER, 5, Api(), url="https://www.douyin.com/video/7300000000000000001")
        assert ei.value.code == "unsupported_url"

    def test_platform_spend_ceiling_refuses_before_any_run(self, on, rc):
        on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD", "0.01")
        api = Api()
        with pytest.raises(channel_listing.ChannelListingError) as ei:
            _list(USER, 5, api)                                # 5 × 0.0071 > 0.01
        assert ei.value.code == "budget_exceeded" and api.starts() == []


class TestRouteClosed:

    @pytest.mark.parametrize("env,val", [
        ("CHINA_ACCESS_ENABLED", "false"),
        ("CHINA_ACCESS_DOUYIN_ENABLED", "false"),
        ("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "off"),
        ("CHINA_ACCESS_DOUYIN_CHANNEL_ENABLED", "false"),
        ("CHINA_ACCESS_APIFY_TOKEN", ""),
    ])
    def test_hook_returns_none(self, on, rc, env, val):
        on.setenv(env, val)
        tok = set_context(USER)
        try:
            assert integration.list_douyin_channel_via_access_layer(PROFILE, 10) is None
        finally:
            reset_context(tok)

    def test_canary_admin_mode_is_admin_only(self, on, rc):
        on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "canary_admin")
        assert channel_listing.route_open(USER) is False
        assert channel_listing.route_open(ADMIN) is True

    def test_kill_switch_closes_the_route(self, on, rc):
        budget_guard.set_killswitch("douyin", True)
        assert channel_listing.route_open(USER) is False


class TestLegacyApifyTokenGone:

    def test_legacy_env_var_no_longer_opens_douyin(self, clean_env, rc, monkeypatch):
        from app.services import douyin_extractor
        clean_env.setenv("APIFY_TOKEN", "legacy-token-value")
        monkeypatch.setattr("app.core.cookie_pool.has_selectable_cookie", lambda p: False)
        assert douyin_extractor.douyin_server_access_available() is False

    def test_apify_service_has_no_token_path(self):
        import app.services.apify_service as legacy
        assert not hasattr(legacy, "APIFY_TOKEN")
        for name in ("extract_douyin_apify", "extract_douyin_apify_sync",
                     "scrape_douyin_user_apify", "scrape_douyin_user_apify_sync"):
            assert not hasattr(legacy, name)
        assert legacy.APIFY_BASE.startswith("https://api.apify.com")
