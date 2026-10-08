"""
Task #6171 — Xiaohongshu / Kuaishou whole-channel listing.

Owner rules (2026-10-08) held here:
  * signed-in users only: a guest is refused (worker: login_required; the
    bulk route: 401 for a request made only of such links, a failed row per
    link in a mixed batch), and no paid run starts;
  * at most 20 videos per scan, a smaller max_videos is kept;
  * never more than what the user may still download today; 0 left → refused
    without a run;
  * paid runs only through the existing guards (flags, managed mode, token
    pool, per-platform spend ceiling), flags OFF by default.
Mocked Apify responses use the row shapes documented by the actors
(natanielsantos~kuaishou-scraper README, vulnv~xiaohongshu-scraper dataset
schema). httpx.MockTransport + fakeredis only: no network, no paid call.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.core import quotas
from app.services.china_platforms import budget_guard, integration, profile_listing, settings
from app.services.china_platforms.channel_listing import ChannelListingError
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    RequestContext,
    reset_context,
    set_context,
)
from app.services.china_platforms.provider_router import ProviderRouter
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from tests._china_fakes import FakeProvider, clean_env, flags_on, rc  # noqa: F401

TOKEN = "apify_api_TESTSECRETTOKEN123456"
XHS_UID = "5ff0e6410000000001008400"
XHS_PROFILE = f"https://www.xiaohongshu.com/user/profile/{XHS_UID}"
KS_UID = "3x984ye63jkct29"
KS_PROFILE = f"https://www.kuaishou.com/profile/{KS_UID}"
KS_PLAY = "https://v1.kwaicdn.com/upic/2022/02/20/22/x_b.mp4?pkey=AAXH&tag=1-1768946212"
XHS_PLAY = "https://sns-video-bd.xhscdn.com/stream/abc.mp4"

GUEST = RequestContext(requester_key="ip:198.51.100.7", origin="worker")
USER = RequestContext(requester_key="user:u-123", origin="worker")
ADMIN = RequestContext(requester_key="admin", is_admin=True, origin="worker")


def ks_item(i: int) -> dict:
    """README "Video Output Example" of natanielsantos~kuaishou-scraper."""
    vid = f"5246975106670{i:06d}"
    return {
        "id": vid, "text": f"video {i} #体育", "createTime": 1645366735771,
        "thumb": "https://p2.a.yximgs.com/upic/c.jpg",
        "url": f"https://www.kuaishou.com/short-video/{vid}",
        "authorMeta": {"id": "1745294435", "name": "糙老爷们看拳", "username": KS_UID},
        "duration": 332, "cover": "https://p2.a.yximgs.com/upic/c.jpg", "width": 1270, "height": 720,
        "playUrl": KS_PLAY,
        "allPlayUrls": [{"url": KS_PLAY, "qualityType": "720p", "videoCodec": "avc", "avgBitrate": 2238}],
    }


def xhs_row(i: int, *, kind: str = "video", video_url: str | None = XHS_PLAY, token: str = "ABtok") -> dict:
    """vulnv~xiaohongshu-scraper note row (dataset schema, operation user_notes)."""
    nid = f"69d8ab67000000002200{i:04x}"
    row = {
        "record_type": "note", "operation": "user_notes", "input": XHS_PROFILE,
        "note_id": nid, "note_type": kind, "note_url": f"https://www.xiaohongshu.com/explore/{nid}",
        "xsec_token": token, "title": f"note {i}", "liked_count": 7,
        "author_user_id": XHS_UID, "author_nickname": "Tiệm Nhỏ", "cover_url": "https://sns-webpic.xhscdn.com/c.jpg",
    }
    if video_url:
        row["video_url"] = video_url
        row["video_duration"] = 31
    return row


class Api:
    def __init__(self, items, *, run_status="SUCCEEDED", cost=0.0003):
        self.items = items
        self.requests: list[httpx.Request] = []
        self.run = {"id": "RUN1", "status": run_status, "defaultDatasetId": "DS1", "usageTotalUsd": cost}

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        p = req.url.path
        if req.method == "POST" and p.endswith("/runs"):
            return httpx.Response(201, json={"data": self.run})
        if req.method == "GET" and "/actor-runs/" in p:
            return httpx.Response(200, json={"data": self.run})
        if req.method == "GET" and "/datasets/" in p:
            return httpx.Response(200, json=self.items[:int(req.url.params.get("limit", "1"))])
        return httpx.Response(404)

    def starts(self):
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith("/runs")]


def _factory(api: Api):
    return lambda spec, n: ApifyProvider(spec, token=TOKEN, transport=httpx.MockTransport(api.handler),
                                         max_items=n)


@pytest.fixture
def on(flags_on):
    for p in ("XIAOHONGSHU", "KUAISHOU"):
        flags_on.setenv(f"CHINA_ACCESS_{p}_ENABLED", "true")
        flags_on.setenv(f"CHINA_ACCESS_{p}_MANAGED_MODE", "on")
        flags_on.setenv(f"CHINA_ACCESS_{p}_CHANNEL_ENABLED", "true")
    flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", TOKEN)
    return flags_on


def _list(platform, ctx, n, api, url=None):
    url = url or (XHS_PROFILE if platform == "xiaohongshu" else KS_PROFILE)
    return asyncio.run(profile_listing.list_profile(platform, url, n, ctx, provider_factory=_factory(api)))


def _use(key: str, n: int):
    req = quotas.QuotaRequester.from_key(key)
    for i in range(n):
        quotas.record_platform_download(req, "tiktok", f"https://www.tiktok.com/@a/video/{i}")


def _ks_api(n=40):
    return Api([ks_item(i) for i in range(n)])


def _xhs_api(n=40):
    return Api([xhs_row(i) for i in range(n)])


# ── URL recognition ─────────────────────────────────────────────────────────

class TestUrls:

    @pytest.mark.parametrize("url,pid", [
        (XHS_PROFILE, XHS_UID),
        (XHS_PROFILE + "?xsec_token=x&xsec_source=pc_note", XHS_UID),
        (f"https://xiaohongshu.com/user/profile/{XHS_UID}/", XHS_UID),
        (f"https://www.xiaohongshu.com/user/profile/{XHS_UID}/69d8ab670000000022003dbe", None),   # a note
        ("https://www.xiaohongshu.com/explore/69d8ab670000000022003dbe", None),
        (f"https://evil.example/user/profile/{XHS_UID}", None),
    ])
    def test_xhs_profile(self, url, pid):
        assert profile_listing.profile_id("xiaohongshu", url) == pid

    @pytest.mark.parametrize("url,pid", [
        (KS_PROFILE, KS_UID),
        ("https://www.kuaishou.com/profile/1745294435?source=x", "1745294435"),
        (f"https://v.m.chenzhongtech.com/fw/user/{KS_UID}", KS_UID),
        ("https://www.kuaishou.com/short-video/3xhf4kv5ahrt8cy", None),
        (f"https://kuaishou.com.evil.example/profile/{KS_UID}", None),
    ])
    def test_ks_profile(self, url, pid):
        assert profile_listing.profile_id("kuaishou", url) == pid

    def test_classify_profile_as_channel_and_note_as_video(self):
        from app.services.downloader import classify_url
        assert classify_url(XHS_PROFILE) == "channel"
        assert classify_url(KS_PROFILE) == "channel"
        assert classify_url(f"{XHS_PROFILE}/69d8ab670000000022003dbe") == "video"
        assert classify_url("https://www.kuaishou.com/short-video/3xhf4kv5ahrt8cy") == "video"

    def test_short_link_resolving_to_a_profile_is_scanned(self, on, rc, monkeypatch):
        async def fake_redirect(url, **kw):
            assert url == "https://v.kuaishou.com/AbCd12"
            return f"https://v.m.chenzhongtech.com/fw/user/{KS_UID}?cc=share"
        monkeypatch.setattr("app.services.china_platforms.adapters.short_links.first_redirect", fake_redirect)
        api = _ks_api()
        listing = _list("kuaishou", USER, 5, api, url="https://v.kuaishou.com/AbCd12")
        assert listing.sec_uid == KS_UID and len(listing.videos) == 5
        assert json.loads(api.starts()[0].content)["startUrls"] == [KS_PROFILE]

    def test_short_link_to_a_video_is_not_a_channel(self, on, rc, monkeypatch):
        async def fake_redirect(url, **kw):
            return "https://www.xiaohongshu.com/discovery/item/69d8ab670000000022003dbe"
        monkeypatch.setattr("app.services.china_platforms.adapters.short_links.first_redirect", fake_redirect)
        api = _xhs_api()
        with pytest.raises(ChannelListingError) as ei:
            _list("xiaohongshu", USER, 5, api, url="http://xhslink.com/a/AbCdEf")
        assert ei.value.code == "unsupported_url" and "trang cá nhân Xiaohongshu" in str(ei.value)
        assert api.starts() == []


# ── owner rules ─────────────────────────────────────────────────────────────

class TestRules:

    @pytest.mark.parametrize("platform", ["xiaohongshu", "kuaishou"])
    def test_guest_is_refused_without_a_run(self, on, rc, platform):
        api = _ks_api() if platform == "kuaishou" else _xhs_api()
        with pytest.raises(ChannelListingError) as ei:
            _list(platform, GUEST, 20, api)
        assert ei.value.code == "login_required" and "đăng nhập" in str(ei.value)
        assert api.starts() == []

    @pytest.mark.parametrize("platform", ["xiaohongshu", "kuaishou"])
    def test_flags_off_by_default(self, flags_on, rc, platform):
        flags_on.setenv(f"CHINA_ACCESS_{platform.upper()}_ENABLED", "true")
        flags_on.setenv(f"CHINA_ACCESS_{platform.upper()}_MANAGED_MODE", "on")
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", TOKEN)
        assert settings.china_channel_enabled(platform) is False
        api = _ks_api()
        with pytest.raises(ChannelListingError) as ei:
            _list(platform, USER, 20, api)
        assert ei.value.code == "platform_disabled" and "chưa mở" in str(ei.value)
        assert api.starts() == []

    @pytest.mark.parametrize("env,val", [
        ("CHINA_ACCESS_ENABLED", "false"),
        ("CHINA_ACCESS_KUAISHOU_ENABLED", "false"),
        ("CHINA_ACCESS_KUAISHOU_MANAGED_MODE", "off"),
        ("CHINA_ACCESS_KUAISHOU_CHANNEL_ENABLED", "false"),
        ("CHINA_ACCESS_APIFY_TOKEN", ""),
    ])
    def test_any_closed_guard_refuses(self, on, rc, env, val):
        on.setenv(env, val)
        assert profile_listing.route_open("kuaishou", USER) is False
        with pytest.raises(ChannelListingError) as ei:
            _list("kuaishou", USER, 5, _ks_api())
        assert ei.value.code == "platform_disabled"

    def test_kill_switch_closes_the_route(self, on, rc):
        budget_guard.set_killswitch("xiaohongshu", True)
        assert profile_listing.route_open("xiaohongshu", USER) is False

    @pytest.mark.parametrize("platform", ["xiaohongshu", "kuaishou"])
    def test_cap_is_20_per_scan(self, on, rc, platform, monkeypatch):
        # A user allowed 100 downloads a day: the 20-per-scan cap, not the
        # daily allowance, is what limits this scan.
        on.setenv("PLATFORM_DAILY_LIMIT_USER", "100")
        monkeypatch.setattr(quotas, "_get_tier", lambda uid: "free", raising=False)
        assert quotas.BatchAllowance(quotas.QuotaRequester.from_key(USER.requester_key)).remaining(platform) > 20
        api = _ks_api() if platform == "kuaishou" else _xhs_api()
        listing = _list(platform, USER, 100, api)
        assert listing.cap == 20 and len(listing.videos) == 20
        start = api.starts()[0]
        assert start.url.params["maxItems"] == "20"
        assert json.loads(start.content)["maxItems"] == 20

    def test_admin_is_also_capped_at_20(self, on, rc):
        assert len(_list("kuaishou", ADMIN, 500, _ks_api()).videos) == 20

    def test_env_may_lower_the_cap_but_not_raise_it(self, on, rc):
        on.setenv("CHINA_ACCESS_KUAISHOU_CHANNEL_MAX", "500")
        assert settings.china_channel_max("kuaishou") == 20
        on.setenv("CHINA_ACCESS_KUAISHOU_CHANNEL_MAX", "8")
        assert len(_list("kuaishou", USER, 100, _ks_api()).videos) == 8

    def test_smaller_request_is_kept(self, on, rc):
        api = _ks_api()
        assert len(_list("kuaishou", USER, 7, api).videos) == 7
        assert api.starts()[0].url.params["maxItems"] == "7"

    def test_cap_is_what_is_left_today(self, on, rc):
        _use(USER.requester_key, 15)                  # 20/day total → 5 left
        api = _xhs_api()
        listing = _list("xiaohongshu", USER, 20, api)
        assert len(listing.videos) == 5 and api.starts()[0].url.params["maxItems"] == "5"

    def test_nothing_left_refuses_without_a_run(self, on, rc):
        _use(USER.requester_key, 20)
        api = _ks_api()
        with pytest.raises(ChannelListingError) as ei:
            _list("kuaishou", USER, 20, api)
        assert ei.value.code == "quota_exceeded" and "07:00" in str(ei.value)
        assert api.starts() == []

    def test_platform_spend_ceiling_refuses_before_any_run(self, on, rc):
        on.setenv("CHINA_ACCESS_KUAISHOU_MANAGED_DAILY_SPEND_CEILING_USD", "0.01")
        api = _ks_api()
        with pytest.raises(ChannelListingError) as ei:
            _list("kuaishou", USER, 5, api)                # 5 × 0.004 > 0.01
        assert ei.value.code == "budget_exceeded" and "ngân sách" in str(ei.value)
        assert api.starts() == []

    def test_default_ceiling_admits_one_full_scan_and_counts_it(self, on, rc):
        on.setenv("CHINA_ACCESS_COST_FLOOR_AT_ESTIMATE", "true")
        _list("kuaishou", USER, 20, _ks_api(), )
        day = budget_guard.day_key()
        spent = int(rc.get(budget_guard.k_provider_spend(day, "apify")))
        assert spent == pytest.approx(20 * 4000 + 50, abs=2)      # 20 × $0.004 + start
        assert int(rc.get(budget_guard.k_platform_calls(day, "kuaishou"))) == 1


# ── output mapping and the per-video cache ──────────────────────────────────

class TestOutput:

    def test_kuaishou_input_and_cache_hit(self, on, rc):
        api = _ks_api()
        listing = _list("kuaishou", USER, 3, api)
        assert json.loads(api.starts()[0].content) == {
            "startUrls": [KS_PROFILE], "profileSortBy": "latest", "maxItems": 3,
            "maxCommentsPerVideo": 0, "maxRepliesPerComment": 0}
        assert api.starts()[0].url.path.endswith("natanielsantos~kuaishou-scraper/runs")
        assert listing.channel_title == "糙老爷们看拳"
        v = listing.videos[0]
        assert v.url == "https://www.kuaishou.com/short-video/5246975106670000000" and v.cached
        calls: list = []
        router = ProviderRouter(lambda p: {"apify_kuaishou": FakeProvider("apify_kuaishou", paid=True, calls=calls)})
        res = asyncio.run(router.resolve(ChinaResolveRequest(url=v.url), USER))
        assert res.cache_hit is True and res.primary_video_url() == KS_PLAY and calls == []

    def test_xhs_input_skips_image_notes_and_keeps_xsec_token(self, on, rc):
        rows = [xhs_row(0), xhs_row(1, kind="normal"), xhs_row(2, video_url=None), xhs_row(3)]
        api = Api(rows)
        listing = _list("xiaohongshu", USER, 4, api)
        start = api.starts()[0]
        assert json.loads(start.content) == {"operation": "user_notes", "userUrls": [XHS_PROFILE], "maxItems": 4}
        assert start.url.path.endswith("vulnv~xiaohongshu-scraper/runs")
        # the actor refuses runs below its minimalMaxTotalChargeUsd ($0.10)
        assert float(start.url.params["maxTotalChargeUsd"]) >= 0.10
        assert [v.title for v in listing.videos] == ["note 0", "note 2", "note 3"]
        assert listing.videos[0].url == (
            "https://www.xiaohongshu.com/explore/69d8ab670000000022000000?xsec_token=ABtok&xsec_source=pc_user")
        assert [v.cached for v in listing.videos] == [True, False, True]   # no video_url → resolved later
        assert listing.channel_title == "Tiệm Nhỏ"

    def test_xhs_listed_video_is_a_cache_hit(self, on, rc):
        listing = _list("xiaohongshu", USER, 2, _xhs_api())
        calls: list = []
        router = ProviderRouter(lambda p: {
            "native_xiaohongshu": FakeProvider("native_xiaohongshu", outcome="cookie_required", calls=calls),
            "apify_xiaohongshu": FakeProvider("apify_xiaohongshu", paid=True, calls=calls)})
        res = asyncio.run(router.resolve(ChinaResolveRequest(url=listing.videos[0].url), USER))
        assert res.cache_hit is True and res.primary_video_url() == XHS_PLAY and calls == []

    def test_second_scan_reuses_the_list(self, on, rc):
        api = _ks_api()
        _list("kuaishou", USER, 10, api)
        again = _list("kuaishou", USER, 6, api)
        assert again.from_cache and len(again.videos) == 6 and len(api.starts()) == 1

    def test_empty_channel_message(self, on, rc):
        with pytest.raises(ChannelListingError) as ei:
            _list("xiaohongshu", USER, 5, Api([xhs_row(0, kind="normal")]))
        assert ei.value.code == "no_media_found" and "riêng tư" in str(ei.value)

    def test_run_failure_is_upstream_message(self, on, rc):
        with pytest.raises(ChannelListingError) as ei:
            _list("kuaishou", USER, 5, Api([ks_item(0)], run_status="FAILED"))
        assert ei.value.code == "provider_unavailable"


# ── wiring into the channel job ─────────────────────────────────────────────

class TestWiring:

    def test_integration_hook_returns_bulk_shape(self, on, rc, monkeypatch):
        async def fake_list(platform, url, n, ctx, **kw):
            return profile_listing.ChannelListing(
                sec_uid=KS_UID, channel_title="K", videos=[
                    profile_listing.ListedVideo(video_id="1", url="https://www.kuaishou.com/short-video/1")])
        monkeypatch.setattr(profile_listing, "list_profile", fake_list)
        out = integration.list_china_profile_channel("kuaishou", KS_PROFILE, 20)
        assert out == {"channel_title": "K", "entries": [{"url": "https://www.kuaishou.com/short-video/1",
                                                          "title": "Kuaishou 1"}],
                       "total_found": 1, "total_queued": 1}

    def test_hook_raises_when_flag_off(self, flags_on, rc):
        tok = set_context(USER)
        try:
            with pytest.raises(ChannelListingError) as ei:
                integration.list_china_profile_channel("xiaohongshu", XHS_PROFILE, 20)
            assert ei.value.code == "platform_disabled"
        finally:
            reset_context(tok)

    @pytest.mark.parametrize("url,plat", [(XHS_PROFILE, "xiaohongshu"), (KS_PROFILE, "kuaishou"),
                                          ("https://v.kuaishou.com/AbCd12", "kuaishou")])
    def test_downloader_routes_to_the_profile_listing(self, monkeypatch, url, plat):
        from app.services import downloader
        seen = []
        monkeypatch.setattr(downloader, "resolve_short_url", lambda u: u)
        monkeypatch.setattr(integration, "list_china_profile_channel",
                            lambda p, u, n: seen.append((p, u, n)) or {"entries": []})

        def no_ytdlp(*a, **k):
            raise AssertionError("yt-dlp must not run for XHS/Kuaishou channels")
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", no_ytdlp)
        downloader._scrape_channel_entries_impl(url, 50, 0)
        assert seen == [(plat, url, 50)]

    def test_worker_shows_the_refusal_text(self, monkeypatch):
        """A ChannelListingError ends the channel job with the Vietnamese
        sentence itself (no "Channel scrape failed:" prefix)."""
        import app.tasks.video_tasks as vt
        updates = []

        class SB:
            def table(self, _n):
                outer = self

                class T:
                    def update(self, row):
                        updates.append(row)
                        return self

                    def __getattr__(self, _a):
                        return lambda *a, **k: self

                    def execute(self):
                        return None
                return T()

        def refuse(*a, **k):
            raise ChannelListingError(profile_listing._msg(profile_listing.MSG_SIGN_IN, "kuaishou"),
                                      "login_required")
        monkeypatch.setattr(vt, "scrape_channel_entries_sync", refuse)
        vt._scrape_channel_body(SB(), KS_PROFILE, "b1", "c1", 20, 0, None, "video", False, False,
                                "ip:198.51.100.7", "other", 10, 3, "balanced")
        assert updates[-1] == {"status": "failed",
                               "error_message": "Bạn cần đăng nhập để tải cả kênh Kuaishou. "
                                                "Đăng nhập miễn phí rồi thử lại."}
