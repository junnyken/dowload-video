"""
Phase 32B-1 — ApifyProvider (thin adapter over the generic managed-actor
provider) for natanielsantos~douyin-scraper. httpx.MockTransport only: no
network, no paid call.

Why: apify_service.extract_douyin_apify starts a sync run and then a second
async run when the first returns nothing (apify_service.py:161-168), and its
parser does not read videoMeta.playUrl — the field the actor README documents.
These tests hold: one billable run per resolve, validated output, actual cost
from run.usageTotalUsd, and no token / signed URL in any failure text or log.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from app.services.china_platforms.adapters.douyin import apify_spec, parse_actor_item
from app.services.china_platforms.errors import ProviderFailureError, redact
from app.services.china_platforms.normalized_models import ChinaResolveRequest
from app.services.china_platforms.provider_router import ProviderRouter
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from tests._china_fakes import (  # noqa: F401
    DY_URL, PUBLIC_CTX, SIGNED, FakeProvider, clean_env, factory, flags_on, rc,
)

TOKEN = "apify_api_TESTSECRETTOKEN123456"

# Shape copied from the actor README "Example Output" (read 2026-10-06).
README_ITEM = {
    "id": "7296149517517212980",
    "text": "cute cats.:D#小咪会赶走你一天的疲惫 #喵星人",
    "createTime": 1698767202,
    "thumb": "https://p9-pc-sign.douyinpic.com/x.jpeg?x-expires=1700596800&x-signature=k8o35",
    "url": "https://www.douyin.com/video/7296149517517212980",
    "authorMeta": {"id": "101823080930", "name": "Chandler"},
    "musicMeta": {"id": "7275161107433244674", "name": "m", "duration": 16},
    "videoMeta": {"cover": "https://p9-pc-sign.douyinpic.com/c.jpeg", "width": 720, "playUrl": SIGNED},
    "statistics": {"diggCount": 1},
}


class Api:
    """Scriptable fake of the three Apify endpoints we call."""

    def __init__(self, *, start_status=201, run=None, polls=None, items=None, dataset_status=200,
                 start_body=None):
        self.requests: list[httpx.Request] = []
        self.start_status = start_status
        self.run = run if run is not None else {"id": "RUN1", "status": "SUCCEEDED",
                                                "defaultDatasetId": "DS1", "usageTotalUsd": 0.00705}
        self.polls = list(polls or [])
        self.items = [README_ITEM] if items is None else items
        self.dataset_status = dataset_status
        self.start_body = start_body

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if req.method == "POST" and path.endswith("/runs"):
            if self.start_status not in (200, 201):
                return httpx.Response(self.start_status, text=self.start_body or f"error token={TOKEN} {SIGNED}")
            return httpx.Response(self.start_status, json={"data": self.run})
        if req.method == "GET" and "/actor-runs/" in path:
            run = self.polls.pop(0) if self.polls else self.run
            return httpx.Response(200, json={"data": run})
        if req.method == "POST" and path.endswith("/abort"):
            return httpx.Response(200, json={"data": {**self.run, "status": "ABORTED", "usageTotalUsd": 0.00005}})
        if req.method == "GET" and "/datasets/" in path:
            return httpx.Response(self.dataset_status, json=self.items)
        return httpx.Response(404)

    def starts(self):
        return [r for r in self.requests if r.method == "POST" and r.url.path.endswith("/runs")]


def _provider(api: Api, clock=None, **spec_kw):
    spec = apify_spec()
    for k, v in spec_kw.items():
        setattr(spec, k, v)
    kw = {"token": TOKEN, "transport": httpx.MockTransport(api.handler)}
    if clock:
        kw["clock"] = clock
    return ApifyProvider(spec, **kw)


def _resolve(p):
    return asyncio.run(p.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))


def _failure(p) -> ProviderFailureError:
    with pytest.raises(ProviderFailureError) as ei:
        _resolve(p)
    return ei.value


class TestSingleRun:

    def test_readme_shape_succeeds_with_one_run(self, clean_env):
        api = Api()
        p = _provider(api)
        res = _resolve(p)
        assert len(api.starts()) == 1
        assert res.title.startswith("cute cats") and res.primary_video_url() == SIGNED
        assert res.uploader == "Chandler" and res.watermark_state == "unknown"
        assert res.duration_sec is None and p.last_run.duration_missing is True   # music duration is NOT video duration
        assert p.last_run.actual_cost_usd == pytest.approx(0.00705)
        assert p.last_run.media_url_expiry.startswith("2033-")

    def test_request_shape(self, clean_env):
        api = Api()
        _resolve(_provider(api))
        start = api.starts()[0]
        assert json.loads(start.content) == {"postUrls": [DY_URL]}   # maxItems is NOT an actor input field
        q = start.url.params
        assert q["maxItems"] == "1" and float(q["maxTotalChargeUsd"]) > 0
        assert 0 < int(q["waitForFinish"]) <= 60
        assert start.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(start.url)

    def test_running_run_is_polled_not_restarted(self, clean_env):
        api = Api(run={"id": "RUN1", "status": "RUNNING", "defaultDatasetId": "DS1"},
                  polls=[{"id": "RUN1", "status": "RUNNING"},
                         {"id": "RUN1", "status": "SUCCEEDED", "defaultDatasetId": "DS1", "usageTotalUsd": 0.006}])
        p = _provider(api)
        _resolve(p)
        assert len(api.starts()) == 1 and p.last_run.actual_cost_usd == pytest.approx(0.006)

    def test_deadline_aborts_the_run_and_never_starts_another(self, clean_env):
        t = {"now": 0.0}

        def clock():
            t["now"] += 40
            return t["now"]

        api = Api(run={"id": "RUN1", "status": "RUNNING"}, polls=[{"id": "RUN1", "status": "RUNNING"}] * 10)
        p = _provider(api, clock=clock)
        f = _failure(p)
        assert f.failure.category == "provider_timeout"
        assert len(api.starts()) == 1
        assert any(r.url.path.endswith("/abort") for r in api.requests)
        assert p.last_run.run_started and p.last_run.actual_cost_usd == pytest.approx(0.00005)


class TestFailureMapping:

    @pytest.mark.parametrize("status,cat", [(401, "provider_unavailable"), (402, "provider_unavailable"),
                                            (429, "upstream_rate_limited"), (500, "provider_unavailable")])
    def test_http_errors(self, clean_env, status, cat):
        p = _provider(Api(start_status=status))
        f = _failure(p)
        assert f.failure.category == cat and not p.last_run.run_started

    @pytest.mark.parametrize("status,cat", [("FAILED", "provider_unavailable"), ("ABORTED", "provider_unavailable"),
                                            ("TIMED-OUT", "provider_timeout")])
    def test_terminal_run_states(self, clean_env, status, cat):
        p = _provider(Api(run={"id": "R", "status": status, "usageTotalUsd": 0.00005}))
        assert _failure(p).failure.category == cat
        assert p.last_run.run_started

    def test_missing_token_never_calls_apify(self, clean_env):
        api = Api()
        p = ApifyProvider(apify_spec(), token="", transport=httpx.MockTransport(api.handler))
        assert not p.is_configured()
        assert _failure(p).failure.category == "provider_unavailable" and api.requests == []


class TestValidation:

    @pytest.mark.parametrize("items,cat", [
        ([], "parse_failed"),
        ([{**README_ITEM, "videoMeta": {"cover": "x"}}], "parse_failed"),            # no media url
        ([{**README_ITEM, "text": ""}], "parse_failed"),                              # no title
        ([{**README_ITEM, "videoMeta": {**README_ITEM["videoMeta"], "playUrl": "javascript:x"}}], "parse_failed"),
        ([{"error": "Post is private"}], "private_or_login_required"),
        ([{"error": "Video not found"}], "parse_failed"),
        ([{**README_ITEM, "videoMeta": {**README_ITEM["videoMeta"], "duration": -3}}], "parse_failed"),
        ([{**README_ITEM, "videoMeta": {**README_ITEM["videoMeta"], "duration": 99_999_999}}], "parse_failed"),  # 27.8 h (ms)
    ])
    def test_invalid_output_is_not_success(self, clean_env, items, cat):
        assert _failure(_provider(Api(items=items))).failure.category == cat

    def test_duration_required_when_configured(self, clean_env):
        assert _failure(_provider(Api(), require_duration=True)).failure.category == "parse_failed"

    def test_duration_ms_is_converted(self, clean_env):
        item = {**README_ITEM, "videoMeta": {**README_ITEM["videoMeta"], "duration": 15300}}
        assert _resolve(_provider(Api(items=[item]))).duration_sec == pytest.approx(15.3)

    def test_legacy_field_names_still_parse(self):
        parsed = parse_actor_item({"desc": "hello", "videoUrl": "https://v.example/a.mp4"})
        assert parsed.title == "hello" and parsed.media_url == "https://v.example/a.mp4"


class TestRedaction:

    def test_redact_strips_tokens_signed_urls_cookies(self, clean_env):
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", TOKEN)
        out = redact(f"Bearer {TOKEN} url={SIGNED} Cookie: sessionid=abc; ttwid=def")
        assert TOKEN not in out and "x-signature" not in out and "SIGSECRET" not in out
        assert "sessionid=abc" not in out

    def test_failure_detail_and_logs_carry_no_secret(self, flags_on, rc, caplog):
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", TOKEN)
        api = Api(start_status=500)
        native = FakeProvider("native_douyin", outcome="cookie_required")
        apify = _provider(api)
        caplog.set_level(logging.INFO, logger="app.china_access")
        router = ProviderRouter(factory(native, apify))
        with pytest.raises(Exception) as ei:
            asyncio.run(router.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        blobs = [caplog.text, json.dumps(router.attempts), str(ei.value),
                 json.dumps([f.model_dump() for f in ei.value.failures]),
                 json.dumps(rc.lrange("china:attempts:douyin", 0, -1))]
        for blob in blobs:
            assert TOKEN not in blob
            assert "x-signature" not in blob and "SIGSECRET" not in blob

    def test_success_attempt_record_has_no_media_url(self, flags_on, rc):
        api = Api()
        native = FakeProvider("native_douyin", outcome="cookie_required")
        router = ProviderRouter(factory(native, _provider(api)))
        asyncio.run(router.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        stored = json.dumps(rc.lrange("china:attempts:douyin", 0, -1))
        assert "douyinstatic" not in stored and DY_URL not in stored
        assert router.attempts[-1]["usable_media_url"] is True
        assert router.attempts[-1]["cost_source"] == "actual"
