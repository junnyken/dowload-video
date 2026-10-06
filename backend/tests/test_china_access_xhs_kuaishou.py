"""
Phase 32B-3 — Kuaishou and Xiaohongshu single video through the China access
layer. Mocks only: no network, no paid call. Actor fixtures are the README
output examples, copied verbatim into tests/fixtures/china_access/.

What is held here:
  * URL recognition / canonicalisation for both platforms, share links and
    share text included; profiles and lookalike hosts are not matched.
  * Actor output parsing for the chosen actors (and the documented
    alternative XHS actor), error rows, missing fields.
  * Policy order: Kuaishou managed only; XHS native first, managed only after
    a fallback-eligible native failure, never for an image note.
  * Flags off → XHS, Kuaishou, Douyin, TikTok and generic links take exactly
    the path they took before (the layer is never entered).
  * Per-platform daily caps, and the managed server-download time budget.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os

import httpx
import pytest

from app.core import source_classifier, url_normalizer
from app.services import downloader
from app.services.china_platforms import budget_guard, integration, provider_router, registry, settings
from app.services.china_platforms.adapters import kuaishou as ks
from app.services.china_platforms.adapters import xiaohongshu as xhs
from app.services.china_platforms.errors import ChinaAccessFailure, ProviderFailureError, make_failure
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaFormat,
    NormalizedMediaResult,
)
from app.services.china_platforms.provider_router import ProviderRouter
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from tests._china_fakes import (  # noqa: F401
    ADMIN_CTX, DY_URL, PUBLIC_CTX, SIGNED, FakeProvider, clean_env, factory, rc,
)
from tests.test_china_access_integration import layer_spy, legacy_douyin  # noqa: F401

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "china_access")


def _fixture(name: str) -> dict:
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)["item"]


KS_ITEM = _fixture("kuaishou_natanielsantos_readme_video.json")
XHS_ITEM = _fixture("xhs_blue_puppy_readme_ok.json")
XHS_AGENTFLOW_ITEM = _fixture("xhs_agentflow_readme_video.json")

NOTE = "6a06c9360000000036001d5a"
XHS_URL = f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=ABC%3D&xsec_source=pc_feed&share_id=zz"
XHS_CANON = f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=ABC%3D&xsec_source=pc_feed"
KS_URL = "https://www.kuaishou.com/short-video/3xhf4kv5ahrt8cy"
XHS_CDN = "https://sns-video-bd.xhscdn.com/stream/abc.mp4"
KS_CDN = "https://v1.kwaicdn.com/upic/x_b.mp4?pkey=SECRETPKEY"


@pytest.fixture
def layer_on(clean_env, rc):
    """Master + both new platforms on, managed mode `on`, token configured."""
    for k, v in {
        "CHINA_ACCESS_ENABLED": "true",
        "CHINA_ACCESS_KUAISHOU_ENABLED": "true",
        "CHINA_ACCESS_KUAISHOU_MANAGED_MODE": "on",
        "CHINA_ACCESS_XIAOHONGSHU_ENABLED": "true",
        "CHINA_ACCESS_XIAOHONGSHU_MANAGED_MODE": "on",
        "CHINA_ACCESS_APIFY_TOKEN": "apify_api_TESTTOKEN_0123456789",
        "CHINA_ACCESS_DEDUPE_WAIT_SEC": "1",
    }.items():
        clean_env.setenv(k, v)
    return clean_env


@pytest.fixture
def no_redirect(monkeypatch):
    """Short-link lookups return a scripted Location; no network."""
    seen: list = []
    table: dict = {}

    async def fake(url, *, transport=None):
        seen.append(url)
        return table.get(url)

    monkeypatch.setattr(ks, "first_redirect", fake)
    monkeypatch.setattr(xhs, "first_redirect", fake)
    return table, seen


def _result(platform: str, provider: str, mode: str, url: str, media: str) -> NormalizedMediaResult:
    return NormalizedMediaResult(
        platform=platform, canonical_url=url, title="t", provider_name=provider, provider_mode=mode,
        formats=[NormalizedMediaFormat(format_id="v", ext="mp4", label="video", has_video=True,
                                       has_audio=True, source_url=media)],
    )


class PlatformFake(FakeProvider):
    def __init__(self, name, platform, **kw):
        super().__init__(name, **kw)
        self.platform = platform

    async def resolve(self, request, context):
        self.calls.append((self.name, request.url))
        from app.services.china_platforms.providers.base import RunRecord
        self.last_run = RunRecord(run_started=self.paid and self.run_started, actual_cost_usd=self.cost)
        if self.outcome != "ok":
            raise ProviderFailureError(make_failure(self.platform, self.name, self.outcome, "fake"))
        return _result(self.platform, self.name, self.mode, request.url, XHS_CDN)


# ── URL recognition / canonicalisation ──────────────────────────────────────

class TestKuaishouUrls:
    a = ks.KuaishouAdapter()

    @pytest.mark.parametrize("url,canon", [
        (KS_URL, KS_URL),
        ("http://kuaishou.com/short-video/3xhf4kv5ahrt8cy?authorId=1&streamSource=find#x", KS_URL),
        ("https://v.m.chenzhongtech.com/fw/photo/3xburnkmj3auazc?shareId=9",
         "https://v.m.chenzhongtech.com/fw/photo/3xburnkmj3auazc"),
        ("https://kphm5nf3.m.chenzhongtech.com/fw/photo/3xw5fib4nichapa?cc=share_im",
         "https://v.m.chenzhongtech.com/fw/photo/3xw5fib4nichapa"),
        ("https://www.kuaishou.com/f/XzOKg8ZBFnb2Ty", "https://www.kuaishou.com/f/XzOKg8ZBFnb2Ty"),
        ("https://v.kuaishou.com/abcdEF", "https://v.kuaishou.com/abcdEF"),
    ])
    def test_recognised_and_canonical(self, url, canon):
        assert self.a.matches(url)
        assert registry.identify_platform(url) == "kuaishou"
        assert self.a.canonicalize(url) == canon

    @pytest.mark.parametrize("url", [
        "https://www.kuaishou.com/profile/1745294435",
        "https://www.kuaishou.com/search/video?searchKey=x",
        "https://kuaishou.com.evil.example/short-video/3xhf4kv5ahrt8cy",
        "https://evilkuaishou.com/short-video/3xhf4kv5ahrt8cy",
        "https://www.douyin.com/video/7300000000000000001",
    ])
    def test_not_matched(self, url):
        assert not self.a.matches(url)

    def test_short_link_resolved_once_without_following(self, clean_env, no_redirect):
        table, seen = no_redirect
        table["https://v.kuaishou.com/abcdEF"] = (
            "https://kphm5nf3.m.chenzhongtech.com/fw/photo/3xw5fib4nichapa?cc=share_im&photoId=3xw5fib4nichapa")
        got = asyncio.run(self.a.resolve_canonical("https://v.kuaishou.com/abcdEF"))
        assert got == "https://v.m.chenzhongtech.com/fw/photo/3xw5fib4nichapa"
        assert seen == ["https://v.kuaishou.com/abcdEF"]

    def test_short_link_lookup_failure_keeps_share_link(self, clean_env, no_redirect):
        table, _ = no_redirect
        table["https://v.kuaishou.com/zz99"] = "https://evil.example/fw/photo/abc"   # off-site: ignored
        assert asyncio.run(self.a.resolve_canonical("https://v.kuaishou.com/zz99")) == "https://v.kuaishou.com/zz99"
        assert asyncio.run(self.a.resolve_canonical("https://v.kuaishou.com/none1")) == "https://v.kuaishou.com/none1"

    def test_canonical_url_needs_no_lookup(self, clean_env, no_redirect):
        _, seen = no_redirect
        assert asyncio.run(self.a.resolve_canonical(KS_URL + "?x=1")) == KS_URL
        assert seen == []


class TestXhsUrls:
    a = xhs.XiaohongshuAdapter()

    @pytest.mark.parametrize("url,canon", [
        (XHS_URL, XHS_CANON),
        (f"https://www.xiaohongshu.com/discovery/item/{NOTE}?xsec_token=ABC%3D&xsec_source=app_share&type=video",
         f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=ABC%3D&xsec_source=app_share"),
        (f"https://www.xiaohongshu.com/user/profile/5e1a2b3c4d5e6f7a8b9c0d1e/{NOTE}?xsec_token=T",
         f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=T"),
        (f"http://xiaohongshu.com/explore/{NOTE.upper()}", f"https://www.xiaohongshu.com/explore/{NOTE}"),
        ("http://xhslink.com/o/6oKW7wkJf09", "http://xhslink.com/o/6oKW7wkJf09"),
        ("https://xhslink.cn/o/6oKW7wkJf09/", "https://xhslink.cn/o/6oKW7wkJf09"),
        ("https://xhslink.com/a/abcdef", "https://xhslink.com/a/abcdef"),
    ])
    def test_recognised_and_canonical(self, url, canon):
        assert self.a.matches(url)
        assert registry.identify_platform(url) == "xiaohongshu"
        assert self.a.canonicalize(url) == canon

    @pytest.mark.parametrize("url", [
        "https://www.xiaohongshu.com/user/profile/5e1a2b3c4d5e6f7a8b9c0d1e",   # profile: later phase
        "https://www.xiaohongshu.com/explore",
        f"https://xiaohongshu.com.evil.example/explore/{NOTE}",
        "https://www.xiaohongshu.com/explore/not-a-note-id",
        "https://xhslink.com.evil.example/o/6oKW7wkJf09",
    ])
    def test_not_matched(self, url):
        assert not self.a.matches(url)

    def test_short_link_resolved_to_note(self, clean_env, no_redirect):
        table, seen = no_redirect
        table["http://xhslink.com/o/6oKW7wkJf09"] = (
            f"https://www.xiaohongshu.com/discovery/item/{NOTE}?app_platform=android&xsec_source=app_share"
            "&type=normal&xsec_token=CBMGWf%3D&author_share=1&share_id=3355")
        got = asyncio.run(self.a.resolve_canonical("http://xhslink.com/o/6oKW7wkJf09"))
        assert got == f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=CBMGWf%3D&xsec_source=app_share"
        assert seen == ["http://xhslink.com/o/6oKW7wkJf09"]

    def test_short_link_lookup_failure_keeps_share_link(self, clean_env, no_redirect):
        got = asyncio.run(self.a.resolve_canonical("http://xhslink.com/o/6oKW7wkJf09"))
        assert got == "http://xhslink.com/o/6oKW7wkJf09"


class TestShareText:

    @pytest.mark.parametrize("text,url", [
        ("【周末去了这家小店 - 小红书】 😆 abcDEF 😆 http://xhslink.com/o/6oKW7wkJf09，复制本条信息，打开【小红书】App查看精彩内容！",
         "http://xhslink.com/o/6oKW7wkJf09"),
        ("68 看看这个作品 http://xhslink.com/a/abcdef 复制后打开小红书", "http://xhslink.com/a/abcdef"),
        ("https://v.kuaishou.com/abcdEF 拳击比赛 该作品在快手被播放过12.9万次，点击链接，打开【快手】直接观看！",
         "https://v.kuaishou.com/abcdEF"),
        ("快来看 https://v.kuaishou.com/abcdEF。", "https://v.kuaishou.com/abcdEF"),
    ])
    def test_extract_share_url(self, text, url):
        assert url_normalizer.extract_share_url(text) == url

    def test_extracted_url_is_routable(self):
        u = url_normalizer.extract_share_url("看看 http://xhslink.com/o/6oKW7wkJf09，复制本条信息")
        assert registry.identify_platform(u) == "xiaohongshu"


# ── recognition for /fetch-link and /resolve-input is flag-gated ────────────

class TestRecognitionGate:

    def test_flags_off_unchanged(self, clean_env):
        assert source_classifier.classify(KS_URL).platform == "unknown"
        assert url_normalizer.normalize("https://v.kuaishou.com/abcdEF").is_short_link is False
        assert url_normalizer.normalize("https://xhslink.cn/o/abcd1234").is_short_link is False
        # discovery/item was never classified; stays that way with flags off
        assert source_classifier.classify(
            f"https://www.xiaohongshu.com/discovery/item/{NOTE}").platform == "unknown"
        # existing XHS rules are untouched
        assert source_classifier.classify(XHS_URL).platform == "xiaohongshu"
        from app.services.container_registry import get_capability
        from app.services.container_registry import _active
        assert ("kuaishou", "single_video") not in _active()   # only the generic fallback answers

    def test_master_alone_is_not_enough(self, clean_env):
        clean_env.setenv("CHINA_ACCESS_ENABLED", "true")
        assert source_classifier.classify(KS_URL).platform == "unknown"
        clean_env.setenv("CHINA_ACCESS_KUAISHOU_ENABLED", "true")
        clean_env.delenv("CHINA_ACCESS_ENABLED")
        assert source_classifier.classify(KS_URL).platform == "unknown"

    def test_flags_on(self, clean_env):
        clean_env.setenv("CHINA_ACCESS_ENABLED", "true")
        clean_env.setenv("CHINA_ACCESS_KUAISHOU_ENABLED", "true")
        clean_env.setenv("CHINA_ACCESS_XIAOHONGSHU_ENABLED", "true")
        c = source_classifier.classify(KS_URL)
        assert (c.platform, c.source_type, c.normalized_id) == ("kuaishou", "single_video", "3xhf4kv5ahrt8cy")
        assert url_normalizer.normalize("https://v.kuaishou.com/abcdEF").is_short_link is True
        assert url_normalizer.normalize("https://xhslink.cn/o/abcd1234").is_short_link is True
        c2 = source_classifier.classify(f"https://www.xiaohongshu.com/discovery/item/{NOTE}?xsec_token=x")
        assert (c2.platform, c2.normalized_id) == ("xiaohongshu", NOTE)
        from app.services.container_registry import get_capability
        cap = get_capability("kuaishou", "single_video")
        assert cap.support_level.value == "experimental"
        assert not cap.requirements.proxy_required


# ── actor output parsing ────────────────────────────────────────────────────

class TestKuaishouActorParse:

    def test_readme_example(self):
        p = ks.parse_actor_item(KS_ITEM)
        assert p.error is None
        assert p.media_url == KS_ITEM["playUrl"]
        assert p.title == KS_ITEM["text"]
        assert p.duration_sec == 332
        assert p.thumbnail_url == KS_ITEM["thumb"]
        assert p.uploader == "糙老爷们看拳"
        assert p.media_id == "5246975106670222075"
        assert p.width == 1270
        assert p.audio_url is None      # musicMeta is the music entry, not offered

    def test_all_play_urls_when_play_url_missing(self):
        item = dict(KS_ITEM, playUrl=None, allPlayUrls=[
            {"url": "https://v1.kwaicdn.com/low.mp4", "qualityType": "540p", "avgBitrate": 900},
            {"url": "https://v1.kwaicdn.com/high.mp4", "qualityType": "720p", "avgBitrate": 2238},
        ])
        assert ks.parse_actor_item(item).media_url == "https://v1.kwaicdn.com/high.mp4"

    def test_millisecond_duration_and_missing_fields(self):
        p = ks.parse_actor_item({"id": "1", "playUrl": KS_CDN, "duration": 45_000})
        assert p.duration_sec == 45.0 and p.title == "Kuaishou 1"
        p2 = ks.parse_actor_item({"text": "x"})
        assert p2.media_url == "" and p2.error is None   # validation rejects it as parse_failed

    def test_error_field(self):
        assert ks.parse_actor_item({"error": "Video is private"}).error == "Video is private"
        assert ks._classify_item_error("Video is private") == "private_or_login_required"
        assert ks.parse_actor_item("nope").error

    def test_actor_input_and_spec(self, clean_env):
        spec = ks.apify_spec()
        assert spec.actor_id == "natanielsantos~kuaishou-scraper"
        assert spec.build_input(KS_URL) == {"startUrls": [KS_URL], "maxCommentsPerVideo": 0,
                                            "maxRepliesPerComment": 0}
        assert spec.est_cost_usd == pytest.approx(0.00405)
        assert spec.budget_class == "apify"
        clean_env.setenv("CHINA_ACCESS_APIFY_KUAISHOU_ACTOR_ID", "someone~other")
        clean_env.setenv("CHINA_ACCESS_APIFY_KUAISHOU_EST_COST_USD", "0.006")
        assert ks.apify_spec().actor_id == "someone~other" and ks.apify_spec().est_cost_usd == 0.006


class TestXhsActorParse:

    def test_readme_example(self):
        p = xhs.parse_actor_item(XHS_ITEM)
        assert p.error is None
        assert p.media_url == "https://sns-video-bd.xhscdn.com/xyz123"
        assert p.title == "Hello" and p.uploader == "Alice" and p.media_id == "abc123"
        assert p.duration_sec is None and p.thumbnail_url is None   # not documented

    def test_alternative_actor_shape(self):
        p = xhs.parse_actor_item(XHS_AGENTFLOW_ITEM)
        assert p.error is None and p.media_url == "https://sns-video-hw.xhscdn.com/....mp4"
        assert p.title == "周末去了这家小店"

    @pytest.mark.parametrize("item,category", [
        ({"url": "u", "status": "error", "downloadUrl": None, "error": {"code": "not_video", "message": "图文"}},
         "unsupported_url"),
        ({"url": "u", "status": "error", "error": {"code": "invalid_url", "message": "x"}}, "unsupported_url"),
        ({"url": "u", "status": "error", "error": {"code": "fetch_failed", "message": "x"}}, "provider_unavailable"),
        ({"url": "u", "status": "error", "error": {"code": "parse_failed", "message": "x"}}, "parse_failed"),
        ({"input": "u", "success": False, "error": "No downloadable media found"}, "parse_failed"),
        (dict(XHS_ITEM, type="image"), "unsupported_url"),
    ])
    def test_error_rows(self, item, category):
        p = xhs.parse_actor_item(item)
        assert p.error
        assert xhs._classify_item_error(p.error) == category

    def test_title_falls_back_to_description(self):
        p = xhs.parse_actor_item(dict(XHS_ITEM, title=""))
        assert p.title == "A description"
        p2 = xhs.parse_actor_item(dict(XHS_ITEM, title=None, description=None))
        assert p2.title == "Xiaohongshu abc123"

    def test_actor_input_and_spec(self, clean_env):
        spec = xhs.apify_spec()
        assert spec.actor_id == "blue_puppy~rednote-video-downloader"
        assert spec.build_input(XHS_CANON) == {"urls": [{"url": XHS_CANON}], "maxUrls": 1}
        assert spec.est_cost_usd == pytest.approx(0.00255)
        clean_env.setenv("CHINA_ACCESS_APIFY_XIAOHONGSHU_ACTOR_ID", "agentflow~xiaohongshu-video-downloader")
        assert xhs.apify_spec().build_input(XHS_CANON) == {"urls": [XHS_CANON]}


class TestApifyRunShape:
    """One billable run with the documented input, through MockTransport."""

    def _run(self, spec, items, url):
        reqs = []

        def handler(req: httpx.Request):
            reqs.append(req)
            if req.method == "POST" and req.url.path.endswith("/runs"):
                return httpx.Response(201, json={"data": {"id": "R", "status": "SUCCEEDED",
                                                          "defaultDatasetId": "D", "usageTotalUsd": 0.00005}})
            if "/datasets/" in req.url.path:
                return httpx.Response(200, json=items)
            return httpx.Response(404)

        p = ApifyProvider(spec, token="apify_api_TESTTOKEN_0123456789", transport=httpx.MockTransport(handler))
        res = asyncio.run(p.resolve(ChinaResolveRequest(url=url), PUBLIC_CTX))
        starts = [r for r in reqs if r.method == "POST" and r.url.path.endswith("/runs")]
        return res, starts

    def test_kuaishou(self, clean_env):
        res, starts = self._run(ks.apify_spec(), [KS_ITEM], KS_URL)
        assert len(starts) == 1
        assert "/acts/natanielsantos~kuaishou-scraper/runs" in starts[0].url.path
        assert starts[0].url.params["maxItems"] == "1"
        assert json.loads(starts[0].content)["startUrls"] == [KS_URL]
        assert res.primary_video_url() == KS_ITEM["playUrl"] and res.duration_sec == 332
        assert res.provider_name == "apify_kuaishou" and res.provider_mode == "managed"

    def test_xhs(self, clean_env):
        res, starts = self._run(xhs.apify_spec(), [XHS_ITEM], XHS_CANON)
        assert len(starts) == 1
        assert json.loads(starts[0].content) == {"urls": [{"url": XHS_CANON}], "maxUrls": 1}
        assert res.primary_video_url() == "https://sns-video-bd.xhscdn.com/xyz123"

    def test_xhs_not_video_row_is_unsupported(self, clean_env):
        row = {"url": XHS_CANON, "status": "error", "downloadUrl": None,
               "error": {"code": "not_video", "message": "图文"}}
        with pytest.raises(ProviderFailureError) as ei:
            self._run(xhs.apify_spec(), [row], XHS_CANON)
        assert ei.value.failure.category == "unsupported_url"


# ── policy order through the real router (fake providers) ───────────────────

class TestPolicyOrder:

    def test_effective_orders(self, clean_env):
        assert registry.effective_order(registry.get_policy("kuaishou"), "single_media") == ["apify_kuaishou"]
        assert registry.effective_order(registry.get_policy("xiaohongshu"), "single_media") == [
            "native_xiaohongshu", "apify_xiaohongshu"]
        # an override cannot smuggle in a provider the policy does not allow
        clean_env.setenv("CHINA_ACCESS_KUAISHOU_PROVIDER_ORDER", "native_kuaishou,apify_kuaishou")
        assert registry.effective_order(registry.get_policy("kuaishou"), "single_media") == ["apify_kuaishou"]

    def _xhs_router(self, native_outcome, calls):
        native = PlatformFake("native_xiaohongshu", "xiaohongshu", outcome=native_outcome, calls=calls)
        managed = PlatformFake("apify_xiaohongshu", "xiaohongshu", paid=True, est=0.00255, calls=calls)
        return ProviderRouter(factory(native, managed), poll_interval=0.01)

    def test_xhs_native_success_never_pays(self, layer_on, no_redirect):
        calls = []
        res = asyncio.run(self._xhs_router("ok", calls).resolve(ChinaResolveRequest(url=XHS_URL), PUBLIC_CTX))
        assert res.provider_name == "native_xiaohongshu"
        assert calls == [("native_xiaohongshu", XHS_CANON)]

    @pytest.mark.parametrize("cat", ["cookie_required", "parse_failed", "provider_timeout", "geo_restricted"])
    def test_xhs_falls_back_to_managed(self, layer_on, no_redirect, cat):
        calls = []
        res = asyncio.run(self._xhs_router(cat, calls).resolve(ChinaResolveRequest(url=XHS_URL), PUBLIC_CTX))
        assert res.provider_name == "apify_xiaohongshu"
        assert [c[0] for c in calls] == ["native_xiaohongshu", "apify_xiaohongshu"]

    @pytest.mark.parametrize("cat", ["unsupported_url", "private_or_login_required"])
    def test_xhs_no_paid_call_for_image_or_private(self, layer_on, no_redirect, cat):
        calls = []
        with pytest.raises(ChinaAccessFailure) as ei:
            asyncio.run(self._xhs_router(cat, calls).resolve(ChinaResolveRequest(url=XHS_URL), PUBLIC_CTX))
        assert ei.value.category == cat
        assert [c[0] for c in calls] == ["native_xiaohongshu"]

    def test_xhs_managed_mode_off_stays_native(self, layer_on, no_redirect):
        layer_on.setenv("CHINA_ACCESS_XIAOHONGSHU_MANAGED_MODE", "off")
        calls = []
        with pytest.raises(ChinaAccessFailure):
            asyncio.run(self._xhs_router("cookie_required", calls).resolve(
                ChinaResolveRequest(url=XHS_URL), PUBLIC_CTX))
        assert [c[0] for c in calls] == ["native_xiaohongshu"]

    def test_kuaishou_managed_only_and_admin_canary(self, layer_on, no_redirect):
        layer_on.setenv("CHINA_ACCESS_KUAISHOU_MANAGED_MODE", "canary_admin")
        calls = []
        managed = PlatformFake("apify_kuaishou", "kuaishou", paid=True, est=0.00405, calls=calls)
        router = ProviderRouter(factory(managed), poll_interval=0.01)
        with pytest.raises(ChinaAccessFailure):
            asyncio.run(router.resolve(ChinaResolveRequest(url=KS_URL), PUBLIC_CTX))
        assert calls == []                                   # public user: no paid call
        res = asyncio.run(router.resolve(ChinaResolveRequest(url=KS_URL), ADMIN_CTX))
        assert res.provider_name == "apify_kuaishou" and calls == [("apify_kuaishou", KS_URL)]

    def test_platform_flag_off_is_disabled(self, layer_on, no_redirect):
        layer_on.setenv("CHINA_ACCESS_KUAISHOU_ENABLED", "false")
        with pytest.raises(ChinaAccessFailure) as ei:
            asyncio.run(ProviderRouter(factory()).resolve(ChinaResolveRequest(url=KS_URL), ADMIN_CTX))
        assert ei.value.category == "platform_disabled"


# ── per-platform budget caps ────────────────────────────────────────────────

class TestPerPlatformBudget:

    def test_defaults(self, clean_env):
        # Call caps default to unlimited since 2026-10-06 (per-person limits
        # bound traffic); the spend ceilings are unchanged.
        assert settings.platform_managed_daily_call_limit("kuaishou") == -1
        assert settings.platform_managed_daily_call_limit("xiaohongshu") == -1
        assert settings.platform_managed_daily_spend_micros("kuaishou") == 100_000
        assert settings.platform_managed_daily_spend_micros("xiaohongshu") == 100_000
        assert settings.platform_managed_daily_call_limit("douyin") == -1
        assert settings.platform_managed_daily_spend_micros("douyin") == 1_000_000

    def test_each_platform_has_its_own_call_cap(self, layer_on):
        layer_on.setenv("CHINA_ACCESS_KUAISHOU_MANAGED_DAILY_CALL_LIMIT", "2")
        cand = budget_guard.PaidCandidate("apify_kuaishou", "apify", 4050)
        budget_guard.reserve("kuaishou", "admin", cand)
        budget_guard.reserve("kuaishou", "admin", cand)
        with pytest.raises(budget_guard.BudgetDenied) as ei:
            budget_guard.reserve("kuaishou", "admin", cand)
        assert ei.value.level == "platform_calls"
        # Xiaohongshu is not affected by Kuaishou's cap
        budget_guard.reserve("xiaohongshu", "admin", budget_guard.PaidCandidate("apify_xiaohongshu", "apify", 2550))

    def test_spend_ceiling_blocks_before_dispatch(self, layer_on, no_redirect):
        layer_on.setenv("CHINA_ACCESS_KUAISHOU_MANAGED_DAILY_SPEND_CEILING_USD", "0.004")  # < one call
        calls = []
        managed = PlatformFake("apify_kuaishou", "kuaishou", paid=True, est=0.00405, calls=calls)
        with pytest.raises(ChinaAccessFailure) as ei:
            asyncio.run(ProviderRouter(factory(managed)).resolve(ChinaResolveRequest(url=KS_URL), ADMIN_CTX))
        assert ei.value.category == "budget_exceeded" and calls == []

    def test_shared_apify_monthly_ceiling_still_applies(self, layer_on, rc):
        layer_on.setenv("CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD", "0.005")
        budget_guard.reserve("kuaishou", "admin", budget_guard.PaidCandidate("apify_kuaishou", "apify", 4050))
        with pytest.raises(budget_guard.BudgetDenied) as ei:
            budget_guard.reserve("xiaohongshu", "admin",
                                 budget_guard.PaidCandidate("apify_xiaohongshu", "apify", 2550))
        assert ei.value.level == "provider_month"


# ── downloader: flags off = unchanged; flags on = layer + server copy rules ─

class _Sentinel(BaseException):
    pass


class TestDownloaderFlagsOff:

    @pytest.mark.parametrize("url", [
        XHS_URL,
        f"https://www.xiaohongshu.com/discovery/item/{NOTE}?xsec_token=T",
        KS_URL,
        "https://www.tiktok.com/@someone/video/7300000000000000009",
        "https://vimeo.com/76979871",
    ])
    def test_generic_paths_untouched(self, clean_env, rc, layer_spy, monkeypatch, url):
        monkeypatch.setattr(integration, "resolve_platform_via_access_layer",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("layer entered")))

        async def tikwm(*a, **k):
            raise _Sentinel("tiktok")

        class YDL:
            def __init__(self, *a, **k):
                raise _Sentinel("yt-dlp")

        monkeypatch.setattr(downloader, "_try_tikwm", tikwm)
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", YDL)
        with pytest.raises(_Sentinel):
            downloader._extract_video_info_impl(url, "video")

    def test_gate_reads_only_env_when_master_off(self, clean_env, monkeypatch):
        touched = []
        monkeypatch.setattr(registry, "get_adapter", lambda *a: touched.append(a))
        monkeypatch.setattr(budget_guard, "platform_killswitch_on", lambda *a: touched.append(a))
        assert integration.china_platform_for_download(KS_URL) is None
        assert integration.resolve_platform_via_access_layer("kuaishou", KS_URL, KS_URL, "video") is None
        assert touched == []

    def test_douyin_still_legacy_with_new_flags_on(self, clean_env, rc, legacy_douyin, monkeypatch):
        clean_env.setenv("CHINA_ACCESS_ENABLED", "true")
        clean_env.setenv("CHINA_ACCESS_KUAISHOU_ENABLED", "true")
        clean_env.setenv("CHINA_ACCESS_XIAOHONGSHU_ENABLED", "true")
        monkeypatch.setattr(provider_router.ProviderRouter, "resolve",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("router")))
        out = downloader._extract_video_info_impl(DY_URL, "video")
        assert out["title"] == "legacy" and len(legacy_douyin["native"]) == 1
        assert integration.china_platform_for_download("https://www.tiktok.com/@a/video/1") is None
        assert integration.china_platform_for_download(DY_URL) is None

    def test_old_kuaishou_flag_still_wins(self, clean_env, rc, monkeypatch):
        clean_env.setenv("KUAISHOU_ENABLED", "true")
        clean_env.setenv("CHINA_ACCESS_ENABLED", "true")
        clean_env.setenv("CHINA_ACCESS_KUAISHOU_ENABLED", "true")
        seen = []
        monkeypatch.setattr("app.services.kuaishou_extractor.extract_kuaishou_download_info",
                            lambda u, q="video": seen.append(u) or {"title": "scaffold"})
        monkeypatch.setattr(integration, "resolve_platform_via_access_layer",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("layer entered")))
        assert downloader._extract_video_info_impl(KS_URL, "video")["title"] == "scaffold"
        assert seen == [KS_URL]

    def test_kill_switch_returns_to_legacy_path(self, layer_on, monkeypatch):
        budget_guard.set_killswitch("xiaohongshu", True)
        assert integration.china_platform_for_download(XHS_URL) is None
        assert integration.china_platform_for_download(KS_URL) == "kuaishou"


@pytest.fixture
def cdn(monkeypatch):
    """httpx.Client stub: records the proxy kwarg; streams `chunks` chunks,
    1 s of fake time per chunk."""
    state = {"proxies": [], "headers": [], "chunks": 200}
    clock = itertools.count(0.0, 1.0)
    monkeypatch.setattr("time.monotonic", lambda: next(clock))

    class Resp:
        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size=65536):
            for _ in range(state["chunks"]):
                yield b"x" * 1024

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Client:
        def __init__(self, *a, **k):
            state["proxies"].append(k.get("proxy") or k.get("proxies"))
            state["headers"].append(k.get("headers") or {})

        def stream(self, *a, **k):
            return Resp()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr("app.core.proxy_manager.IPROYAL_PROXY_CN", "http://cn-proxy.invalid:1")
    monkeypatch.setenv("CHINA_ACCESS_MANAGED_SERVER_DOWNLOAD_BUDGET_SEC", "10")
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", str(_tmpdir(monkeypatch)))
    return state


def _tmpdir(monkeypatch):
    import tempfile
    return tempfile.mkdtemp(prefix="cn_layer_")


class TestDownloaderFlagsOn:

    def _route(self, monkeypatch, provider, mode, media):
        async def fake_resolve(self, req, ctx=None, **kw):
            plat = registry.identify_platform(req.url)
            return _result(plat, provider, mode, req.url, media)
        monkeypatch.setattr(provider_router.ProviderRouter, "resolve", fake_resolve)

    def test_managed_result_hands_off_after_budget_without_cn_proxy(self, layer_on, monkeypatch, cdn):
        self._route(monkeypatch, "apify_kuaishou", "managed", KS_CDN)
        out = downloader._extract_video_info_impl(KS_URL, "video")
        assert out["provider"] == "apify_kuaishou" and out["platform"] == "kuaishou"
        assert out["direct_mp4_url"] == KS_CDN and not out.get("local_file_path")
        assert cdn["proxies"] == [None]                       # one direct attempt, never the CN proxy
        assert cdn["headers"][0]["Referer"] == "https://www.kuaishou.com/"

    def test_native_xhs_result_is_copied_in_full(self, layer_on, monkeypatch, cdn):
        self._route(monkeypatch, "native_xiaohongshu", "native", XHS_CDN)
        out = downloader._extract_video_info_impl(XHS_URL, "video")
        assert out["provider"] == "native_xiaohongshu"
        assert out["local_file_path"] and os.path.getsize(out["local_file_path"]) == 200 * 1024
        assert out["direct_mp4_url"] is None and cdn["proxies"] == [None]
        os.remove(out["local_file_path"])

    def test_small_managed_file_finishes_inside_budget(self, layer_on, monkeypatch, cdn):
        cdn["chunks"] = 3
        self._route(monkeypatch, "apify_xiaohongshu", "managed", XHS_CDN)
        out = downloader._extract_video_info_impl(XHS_URL, "video")
        assert out["local_file_path"] and out["direct_mp4_url"] is None
        os.remove(out["local_file_path"])

    def test_resolved_short_link_input_is_used(self, layer_on, monkeypatch, cdn):
        cdn["chunks"] = 1
        seen = []

        async def fake_resolve(self, req, ctx=None, **kw):
            seen.append(req.url)
            return _result("xiaohongshu", "native_xiaohongshu", "native", req.url, XHS_CDN)

        monkeypatch.setattr(provider_router.ProviderRouter, "resolve", fake_resolve)
        monkeypatch.setattr(downloader, "resolve_short_url", lambda u: XHS_URL)
        out = downloader._extract_video_info_impl("http://xhslink.com/o/6oKW7wkJf09", "video")
        assert seen == [XHS_URL] and out["original_url"] == "http://xhslink.com/o/6oKW7wkJf09"
        os.remove(out["local_file_path"])

    def test_failure_raises_user_message_without_legacy_fallback(self, layer_on, monkeypatch):
        async def fake_resolve(self, req, ctx=None, **kw):
            raise ChinaAccessFailure("xiaohongshu", "unsupported_url", [
                make_failure("xiaohongshu", "native_xiaohongshu", "unsupported_url", "image note")])

        class YDL:
            def __init__(self, *a, **k):
                raise AssertionError("legacy yt-dlp path reached")

        monkeypatch.setattr(provider_router.ProviderRouter, "resolve", fake_resolve)
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", YDL)
        with pytest.raises(ValueError) as ei:
            downloader._extract_video_info_impl(XHS_URL, "video")
        assert "SIG" not in str(ei.value) and str(ei.value)


class TestNativeXhsProvider:

    def _run(self, monkeypatch, raw):
        monkeypatch.setattr("app.services.xiaohongshu_extractor.extract_xiaohongshu", lambda u, q="video": raw)
        return asyncio.run(xhs._native_resolve(ChinaResolveRequest(url=XHS_CANON), PUBLIC_CTX))

    def test_video(self, monkeypatch):
        res = self._run(monkeypatch, {"title": "T", "author": "A", "thumbnail": "https://c/x.jpg",
                                      "duration_ms": 15000, "direct_url": XHS_CDN, "source_type": "video",
                                      "error_code": None})
        assert res.primary_video_url() == XHS_CDN and res.duration_sec == 15.0 and res.media_id == NOTE
        assert res.provider_mode == "native"

    @pytest.mark.parametrize("raw,cat", [
        ({"error_code": "xhs_login_required", "error_message": "cookie"}, "cookie_required"),
        ({"error_code": "xhs_extract_failed", "error_message": "api"}, "parse_failed"),
        ({"source_type": "mixed", "media_items": [1, 2], "error_code": None}, "unsupported_url"),
        ({"source_type": "video", "direct_url": "", "error_code": None}, "parse_failed"),
    ])
    def test_failures(self, monkeypatch, raw, cat):
        with pytest.raises(ProviderFailureError) as ei:
            self._run(monkeypatch, raw)
        assert ei.value.failure.category == cat
