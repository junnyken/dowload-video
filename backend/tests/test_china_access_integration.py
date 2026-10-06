"""
Phase 32B-1 — the access layer's hooks into existing code.

The binding rule (owner decisions 2026-10-06): with every new flag OFF
(default) existing behaviour must not change. These tests drive the real
downloader / extractor / gate functions with the flags off and assert the
legacy calls are made exactly as before, then turn the flags on and check the
Douyin path goes through the layer while TikTok, Bilibili and generic links
never touch it. No network: every outbound call is stubbed.
"""
from __future__ import annotations

import asyncio
import contextvars
import csv
import json
from types import SimpleNamespace

import pytest

from app.services import douyin_extractor as dx
from app.services import downloader
from app.services.china_platforms import budget_guard, integration, provider_router
from app.services.china_platforms.benchmark_runner import CSV_FIELDS, run_benchmark
from app.services.china_platforms.errors import ChinaAccessFailure, make_failure
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    RequestContext,
    current_context,
    set_context,
)
from app.services.china_platforms.provider_router import ProviderRouter
from tests._china_fakes import (  # noqa: F401
    ADMIN_CTX, DY_URL, DY_URL2, SIGNED, FakeProvider, clean_env, factory, flags_on, media_result, rc,
)
from tests.test_admin_download_metrics import admin  # noqa: F401


class _Sentinel(BaseException):
    """BaseException so no `except Exception` in the downloader swallows it."""


def _must_not_be_called(*_a, **_k):
    raise AssertionError("China access layer was entered")


@pytest.fixture
def layer_spy(monkeypatch):
    """Records (and refuses) any touch of the router or a Redis-backed guard.
    Recorded, not only raised: the hook swallows guard errors by design (an
    unreadable kill switch sends the request to the legacy path), so a raise
    alone would hide a hook that ignored the env flags."""
    touched: list = []

    def spy(name):
        def _f(*_a, **_k):
            touched.append(name)
            raise AssertionError(f"China access layer was entered ({name})")
        return _f

    monkeypatch.setattr(provider_router.ProviderRouter, "resolve", spy("router"))
    monkeypatch.setattr(budget_guard, "global_killswitch_on", spy("global_killswitch"))
    monkeypatch.setattr(budget_guard, "platform_killswitch_on", spy("platform_killswitch"))
    yield touched
    assert touched == [], touched


@pytest.fixture
def legacy_douyin(monkeypatch):
    calls = {"native": [], "apify": []}

    def native(url, quality="video", user_cookies_file=None):
        calls["native"].append((url, quality, user_cookies_file))
        return {"title": "legacy", "direct_mp4_url": "", "provider": "yt-dlp"}

    def apify(url, quality="video"):
        calls["apify"].append((url, quality))
        return {"title": "legacy-apify", "direct_mp4_url": "", "provider": "apify"}

    monkeypatch.setattr(downloader, "extract_douyin_video_sync", native)
    monkeypatch.setattr("app.services.apify_service.extract_douyin_apify_sync", apify)
    return calls


# ── flags OFF: unchanged behaviour ──────────────────────────────────────────

class TestFlagsOffUnchanged:

    def test_douyin_download_path_calls_legacy_chain_identically(self, clean_env, rc, layer_spy, legacy_douyin):
        url = "https://www.douyin.com/jingxuan?modal_id=7300000000000000001"
        out = downloader._extract_video_info_impl(url, "video", user_cookies_file="/tmp/ck.txt")
        assert out["title"] == "legacy"
        assert legacy_douyin["native"] == [(DY_URL, "video", "/tmp/ck.txt")]
        assert legacy_douyin["apify"] == []

    def test_legacy_apify_branch_still_runs_when_apify_token_set(self, clean_env, rc, layer_spy, legacy_douyin):
        # Documents the trap in docs/china-access/06: APIFY_TOKEN alone turns
        # on the legacy, unbudgeted path — behaviour preserved, not endorsed.
        clean_env.setenv("APIFY_TOKEN", "legacy-token-value")
        out = downloader._extract_video_info_impl(DY_URL, "video")
        assert out["title"] == "legacy-apify" and legacy_douyin["apify"] == [(DY_URL, "video")]

    def test_extract_douyin_video_default_keeps_scraperapi(self, monkeypatch):
        seen = []

        async def none(*a, **k):
            return None

        async def scraper(url, quality="video"):
            seen.append(url)
            return None

        for name in ("_try_ytdlp", "_try_iesdouyin_share", "_try_tikwm"):
            monkeypatch.setattr(dx, name, none)
        monkeypatch.setattr(dx, "_try_scraperapi_ssr", scraper)
        with pytest.raises(ValueError) as ei:
            asyncio.run(dx.extract_douyin_video(DY_URL))
        assert str(ei.value) == dx.DOUYIN_COOKIE_REQUIRED_MSG and seen == [DY_URL]
        seen.clear()
        with pytest.raises(ValueError):
            asyncio.run(dx.extract_douyin_video(DY_URL, skip_scraperapi=True))
        assert seen == []

    def test_server_access_gate_unchanged(self, clean_env, rc, layer_spy):
        assert dx.douyin_server_access_available() is False   # empty pool, no tokens
        from app.api.routes import _douyin_cookie_gate
        exc = _douyin_cookie_gate([DY_URL], False, single=True)
        assert exc is not None and exc.status_code == 422 and exc.error_code == "cookie_required"
        assert _douyin_cookie_gate(["https://www.tiktok.com/@a/video/1"], False, single=True) is None

    def test_bind_request_context_is_a_no_op(self, clean_env):
        class Exploding:
            @property
            def headers(self):
                raise AssertionError("headers read while flags are off")
        assert integration.bind_request_context(Exploding(), None) is None
        assert current_context().requester_key == "unknown"

    def test_resolve_hook_returns_none(self, clean_env, layer_spy):
        assert integration.resolve_douyin_via_access_layer(DY_URL, DY_URL, "video") is None
        assert integration.managed_route_open_for_current_request("douyin") is False


# ── non-Douyin platforms never enter the layer, even with flags ON ──────────

class TestOtherPlatformsUntouched:

    @pytest.mark.parametrize("url,patch_target", [
        ("https://www.tiktok.com/@someone/video/7300000000000000009", "tikwm"),
        ("https://www.bilibili.com/video/BV1ECeJ65EZS", "bili"),
        ("https://vimeo.com/76979871", "ytdlp"),
    ])
    def test_not_routed(self, flags_on, monkeypatch, layer_spy, url, patch_target):
        async def tikwm(*a, **k):
            raise _Sentinel("tiktok path reached")

        def bili(*a, **k):
            raise _Sentinel("bilibili path reached")

        class YDL:
            def __init__(self, *a, **k):
                raise _Sentinel("yt-dlp path reached")

        monkeypatch.setattr(downloader, "_try_tikwm", tikwm)
        monkeypatch.setattr("app.services.bilibili_extractor._get_bili_cookies_file", bili)
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", YDL)
        monkeypatch.setattr(integration, "resolve_douyin_via_access_layer", _must_not_be_called)
        with pytest.raises(_Sentinel) as ei:
            downloader._extract_video_info_impl(url, "video")
        assert {"tikwm": "tiktok", "bili": "bilibili", "ytdlp": "yt-dlp"}[patch_target] in str(ei.value)


# ── flags ON: Douyin goes through the layer ─────────────────────────────────

@pytest.fixture
def no_cdn(monkeypatch):
    import httpx

    class NoNet:
        def __init__(self, *a, **k):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(httpx, "Client", NoNet)
    monkeypatch.setattr("app.core.proxy_manager.IPROYAL_PROXY_CN", "")


class TestFlagsOnDouyin:

    def test_layer_result_feeds_existing_download_code(self, flags_on, monkeypatch, legacy_douyin, no_cdn):
        flags_on.setenv("APIFY_TOKEN", "legacy-token-value")   # legacy branch must still be skipped

        async def fake_resolve(self, req, ctx=None, **kw):
            return media_result("apify_douyin", "managed", req.url)

        monkeypatch.setattr(provider_router.ProviderRouter, "resolve", fake_resolve)
        out = downloader._extract_video_info_impl(DY_URL, "video")
        assert out["provider"] == "apify_douyin" and out["direct_mp4_url"] == SIGNED
        assert legacy_douyin == {"native": [], "apify": []}

    def test_cookie_failure_keeps_existing_message_and_class(self, flags_on, monkeypatch, legacy_douyin):
        from app.core.failure_classifier import FailureClass, classify_failure

        async def fake_resolve(self, req, ctx=None, **kw):
            raise ChinaAccessFailure("douyin", "budget_exceeded", [
                make_failure("douyin", "native_douyin", "cookie_required", dx.DOUYIN_COOKIE_REQUIRED_MSG),
                make_failure("douyin", "apify_douyin", "budget_exceeded", "x")])

        monkeypatch.setattr(provider_router.ProviderRouter, "resolve", fake_resolve)
        with pytest.raises(ValueError) as ei:
            downloader._extract_video_info_impl(DY_URL, "video")
        assert dx.DOUYIN_COOKIE_REQUIRED_MSG in str(ei.value)
        assert classify_failure(str(ei.value)) == FailureClass.USER_ACTION

    def test_kill_switch_returns_to_legacy_path(self, flags_on, legacy_douyin, monkeypatch):
        monkeypatch.setattr(provider_router.ProviderRouter, "resolve", _must_not_be_called)
        budget_guard.set_killswitch("douyin", True)
        assert downloader._extract_video_info_impl(DY_URL, "video")["title"] == "legacy"
        assert len(legacy_douyin["native"]) == 1

    def test_native_provider_skips_scraperapi_and_passes_user_cookie(self, flags_on, monkeypatch):
        seen = {}

        async def fake_extract(url, quality="video", user_cookies_file=None, *, skip_scraperapi=False):
            seen.update(url=url, cookie=user_cookies_file, skip=skip_scraperapi)
            return {"title": "n", "direct_mp4_url": SIGNED, "duration": 12}

        monkeypatch.setattr(dx, "extract_douyin_video", fake_extract)
        out = integration.resolve_douyin_via_access_layer(DY_URL, DY_URL, "video", "/tmp/ck.txt")
        assert out["provider"] == "native_douyin"
        assert seen == {"url": DY_URL, "cookie": "/tmp/ck.txt", "skip": True}

    def test_run_with_timeout_propagates_context(self, flags_on):
        token = set_context(ADMIN_CTX)
        try:
            got = downloader._run_with_timeout(lambda: current_context().requester_key, timeout=5)
        finally:
            from app.services.china_platforms.normalized_models import reset_context
            reset_context(token)
        assert got == "admin"

    def test_probe_runs_marked_as_probe(self):
        from app.services.china_platforms.probes import run_as_probe
        assert run_as_probe(lambda: current_context().origin) == "probe"
        assert current_context().origin == "worker"


class TestRequestContext:

    def _req(self, **headers):
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host="203.0.113.9"))

    def test_admin_session_token_marks_admin(self, flags_on, rc):
        rc.set("admin:session:SESSIONTOKEN", "1")

        def run():
            integration.bind_request_context(self._req(**{"X-Admin-Token": "SESSIONTOKEN"}), None)
            return current_context()

        ctx = contextvars.copy_context().run(run)
        assert ctx.is_admin and ctx.requester_key == "admin"

    def test_raw_password_is_not_admin_here(self, flags_on, rc, monkeypatch):
        import app.api.admin as admin_mod
        monkeypatch.setattr(admin_mod, "_ADMIN_PASSWORD", "pw-secret")

        def run():
            integration.bind_request_context(self._req(**{"X-Admin-Token": "pw-secret"}), {"id": "u1"})
            return current_context()

        ctx = contextvars.copy_context().run(run)
        assert not ctx.is_admin and ctx.requester_key == "user:u1"

    def test_canary_admin_opens_cookie_gate_only_for_admin(self, flags_on, rc):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "canary_admin")
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "x" * 20)

        def as_ctx(ctx):
            set_context(ctx)
            return dx.douyin_server_access_available()

        assert contextvars.copy_context().run(as_ctx, ADMIN_CTX) is True
        assert contextvars.copy_context().run(
            as_ctx, RequestContext(requester_key="ip:1", origin="request")) is False


# ── admin endpoints ─────────────────────────────────────────────────────────

class TestAdminEndpoints:

    def test_requires_admin(self, app, rc):
        assert app.get("/api/v1/admin/china-platforms").status_code == 401
        assert app.post("/api/v1/admin/china-platforms/douyin/kill-switch",
                        json={"on": True, "reason": "test"}).status_code == 401

    def test_overview_and_detail_have_no_secrets(self, app, admin, flags_on, rc):
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_SHOULDNOTLEAK123")
        native = FakeProvider("native_douyin", outcome="cookie_required")
        apify = FakeProvider("apify_douyin", paid=True)
        asyncio.run(ProviderRouter(factory(native, apify)).resolve(
            ChinaResolveRequest(url=DY_URL),
            RequestContext(requester_key="ip:1", origin="request")))
        for path in ("/api/v1/admin/china-platforms", "/api/v1/admin/china-platforms/douyin",
                     "/api/v1/admin/china-platforms/costs", "/api/v1/admin/china-platforms/douyin/benchmark"):
            r = app.get(path)
            assert r.status_code == 200, (path, r.text)
            assert "SHOULDNOTLEAK" not in r.text and "x-signature" not in r.text and "douyinstatic" not in r.text
        detail = app.get("/api/v1/admin/china-platforms/douyin").json()
        assert detail["recent_attempts"] and detail["managed_mode"]["effective"] == "on"
        costs = app.get("/api/v1/admin/china-platforms/costs").json()
        assert costs["providers"]["apify"]["calls_today"] == 1

    def test_kill_switch_and_mode_are_audited(self, app, admin, flags_on, rc, monkeypatch):
        import app.api.admin_china_platforms as mod
        audit = []
        monkeypatch.setattr(mod, "log_admin_action", lambda req, action, **kw: audit.append((action, kw)))
        r = app.post("/api/v1/admin/china-platforms/douyin/kill-switch", json={"on": True, "reason": "incident"})
        assert r.status_code == 200 and r.json() == {"scope": "douyin", "prior": False, "new": True}
        assert budget_guard.platform_killswitch_on("douyin")
        r = app.post("/api/v1/admin/china-platforms/global/kill-switch", json={"on": True, "reason": "incident"})
        assert r.status_code == 200 and budget_guard.global_killswitch_on()
        r = app.post("/api/v1/admin/china-platforms/douyin/mode", json={"mode": "canary_admin", "reason": "canary"})
        assert r.status_code == 200 and r.json()["new"]["effective"] == "canary_admin"
        assert [a[0] for a in audit] == ["admin.china_access.killswitch", "admin.china_access.killswitch",
                                         "admin.china_access.mode"]
        assert all({"prior", "new", "reason"} <= set(a[1]["metadata"]) for a in audit)

    def test_mode_cannot_exceed_env_ceiling(self, app, admin, flags_on, rc):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "benchmark")
        r = app.post("/api/v1/admin/china-platforms/douyin/mode", json={"mode": "on", "reason": "expand"})
        assert r.status_code == 409
        r = app.post("/api/v1/admin/china-platforms/bilibili/mode", json={"mode": "on", "reason": "x" * 5})
        assert r.status_code == 409

    def test_reason_required(self, app, admin, flags_on, rc):
        assert app.post("/api/v1/admin/china-platforms/douyin/kill-switch", json={"on": True}).status_code == 422

    def test_cache_reset(self, app, admin, flags_on, rc):
        from app.services.china_platforms import request_cache
        h = request_cache.url_hash(DY_URL)
        request_cache.set_cached(media_result("native_douyin"), "single_media", h, 600)
        r = app.post("/api/v1/admin/china-platforms/douyin/cache/reset", json={"url_hash": h, "reason": "bad"})
        assert r.status_code == 200 and r.json()["deleted"] is True

    def test_benchmark_run_endpoint_is_locked_and_audited(self, app, admin, flags_on, rc, monkeypatch):
        import app.api.admin_china_platforms as mod
        runs = []

        async def fake_run(platform, **kw):
            runs.append((platform, kw))
            return {}

        monkeypatch.setattr(mod, "run_benchmark", fake_run)
        r = app.post("/api/v1/admin/china-platforms/douyin/benchmark/run",
                     json={"include_managed": False, "max_urls": 5, "reason": "baseline"})
        assert r.status_code == 200 and runs == [("douyin", {"include_managed": False, "max_urls": 5})]
        rc.set("china:benchmark:lock:douyin", "1")
        r = app.post("/api/v1/admin/china-platforms/douyin/benchmark/run",
                     json={"include_managed": False, "max_urls": 5, "reason": "baseline"})
        assert r.status_code == 409


# ── benchmark harness ───────────────────────────────────────────────────────

async def _fake_validator(url, platform="douyin"):
    # No network in tests: media validation is covered in test_china_access_stage_a.py.
    from app.services.china_platforms.media_validation import _result
    return _result("usable", None, media_url_http_status=206, media_duration_sec=10.0)


class TestBenchmark:

    def _routers(self, calls, paid_outcome="ok"):
        def make():
            return ProviderRouter(factory(
                FakeProvider("native_douyin", outcome="cookie_required", calls=calls),
                FakeProvider("apify_douyin", paid=True, outcome=paid_outcome, calls=calls, cost=0.007)))
        return make

    def test_native_only_writes_json_and_csv(self, flags_on, tmp_path):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "benchmark")
        calls = []
        fx = [{"url": DY_URL, "case": "public_single"}, {"url": DY_URL2, "case": "short_url"}]
        rep = asyncio.run(run_benchmark("douyin", fixtures=fx, out_dir=str(tmp_path),
                                        router_factory=self._routers(calls),
                                        media_validator=_fake_validator))
        assert [c[0] for c in calls] == ["native_douyin", "native_douyin"]   # no paid call
        with open(tmp_path / rep["files"]["csv"], encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        assert list(rows[0].keys()) == CSV_FIELDS and len(rows) == 2
        assert rows[0]["case"] == "public_single" and rows[0]["retry_count"] == "0"
        data = json.loads((tmp_path / rep["files"]["json"]).read_text(encoding="utf-8"))
        assert data["summary"]["native_douyin"]["error_mix"] == {"cookie_required": 2}
        assert DY_URL not in (tmp_path / rep["files"]["json"]).read_text(encoding="utf-8")

    def test_managed_only_when_requested_and_stops_on_budget(self, flags_on, tmp_path):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "benchmark")
        flags_on.setenv("CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT", "1")
        calls = []
        fx = [{"url": u, "case": "c"} for u in (DY_URL, DY_URL2, "https://www.douyin.com/video/7300000000000000003")]
        rep = asyncio.run(run_benchmark("douyin", include_managed=True, fixtures=fx, out_dir=str(tmp_path),
                                        router_factory=self._routers(calls),
                                        media_validator=_fake_validator))
        assert [c[0] for c in calls].count("apify_douyin") == 1
        s = rep["summary"]["apify_douyin"]
        assert s["successes"] == 1 and s["cost_per_success_usd"] == pytest.approx(0.007)
        assert any(a["normalized_failure_category"] == "budget_exceeded" for a in rep["attempts"])

    def test_managed_not_called_when_mode_off(self, flags_on, tmp_path):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "off")
        calls = []
        asyncio.run(run_benchmark("douyin", include_managed=True, fixtures=[{"url": DY_URL, "case": "c"}],
                                  out_dir=str(tmp_path), router_factory=self._routers(calls),
                                        media_validator=_fake_validator))
        assert "apify_douyin" not in [c[0] for c in calls]

    def test_fixtures_come_from_config(self, flags_on, tmp_path):
        from app.services.china_platforms.benchmark_runner import load_fixtures
        f = tmp_path / "fx.json"
        f.write_text(json.dumps({"douyin": [{"url": DY_URL2, "case": "long_media"}]}), encoding="utf-8")
        flags_on.setenv("CHINA_ACCESS_BENCHMARK_DOUYIN_URLS", f"{DY_URL},\n{DY_URL}")
        flags_on.setenv("CHINA_ACCESS_BENCHMARK_FIXTURES_FILE", str(f))
        assert load_fixtures("douyin") == [{"url": DY_URL, "case": "env"}, {"url": DY_URL2, "case": "long_media"}]
