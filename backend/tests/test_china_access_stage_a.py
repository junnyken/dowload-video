"""
Phase 32B-2 Stage A (Douyin only): usable-media validation, actual-cost
reconciliation, spend alerts, automatic rollback, watermark review gate and
the benchmark report/recommendation.

Mocks only: httpx.MockTransport for the CDN, an injected ffprobe runner,
fakeredis, FakeProvider. No network, no paid calls, no Telegram.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
import pytest

from app.services.china_platforms import (
    budget_guard,
    media_validation,
    registry,
    rollout_guard,
    watermark,
)
from app.services.china_platforms.benchmark_runner import (
    overall_recommendation,
    recommend,
    report_from_last,
    run_benchmark,
    summarize,
)
from app.services.china_platforms.errors import ChinaAccessFailure
from app.services.china_platforms.normalized_models import ChinaResolveRequest, RequestContext
from app.services.china_platforms.provider_router import ProviderRouter
from app.services.china_platforms.request_cache import url_hash
from tests._china_fakes import (  # noqa: F401
    ADMIN_CTX, DY_URL, DY_URL2, DY_URL3, PUBLIC_CTX, SIGNED, FakeProvider, clean_env, down_redis,
    factory, flags_on, rc,
)
from tests.test_admin_download_metrics import admin  # noqa: F401

CDN = "https://v3-dy.douyinvod.com/abc/video.mp4?x-expires=1999999999&x-signature=SIGSECRETvalue%3D"
PUBLIC_IP = ("93.184.216.34",)


# ── helpers ─────────────────────────────────────────────────────────────────

def _box(typ: bytes, payload_len: int) -> bytes:
    return (8 + payload_len).to_bytes(4, "big") + typ + b"\x00" * payload_len


def faststart_mp4(total_pad: int = 200_000) -> bytes:
    """ftyp + moov (complete) + mdat header and some payload."""
    head = _box(b"ftyp", 16) + _box(b"moov", 1000)
    mdat_len = total_pad
    return head + (8 + mdat_len).to_bytes(4, "big") + b"mdat" + b"\x01" * mdat_len


def ffprobe_json(video=True, duration="12.5") -> bytes:
    streams = [{"codec_type": "audio", "codec_name": "aac"}]
    if video:
        streams.insert(0, {"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280})
    return json.dumps({"streams": streams, "format": {"duration": duration}}).encode()


def runner(raw: bytes):
    seen = []

    async def _run(data: bytes) -> bytes:
        seen.append(len(data))
        return raw
    _run.seen = seen
    return _run


def cdn_transport(body: bytes, *, status=206, ctype="video/mp4", calls=None, total=None, redirect_to=None):
    calls = calls if calls is not None else []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if redirect_to:
            return httpx.Response(302, headers={"location": redirect_to})
        rng = request.headers.get("range", "bytes=0-")
        start, _, end = rng.split("=", 1)[1].partition("-")
        start = int(start)
        end = min(int(end) if end else len(body) - 1, len(body) - 1)
        chunk = body[start:end + 1]
        headers = {"content-type": ctype}
        if status == 206:
            headers["content-range"] = f"bytes {start}-{start + len(chunk) - 1}/{total or len(body)}"
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(chunk))
    return httpx.MockTransport(handler), calls


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr("app.core.ssrf_guard._resolve", lambda host: PUBLIC_IP)


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr("app.core.alerts.send_admin_alert",
                        lambda level, title, body, alert_key="": sent.append((level, title, body)))
    return sent


@pytest.fixture
def audit(monkeypatch):
    rows = []
    monkeypatch.setattr("app.core.audit.log_admin_action",
                        lambda req, action, **kw: rows.append((action, kw)))
    return rows


def _validate(url=CDN, **kw):
    return asyncio.run(media_validation.validate_media_url(url, platform="douyin", **kw))


# ── 1. usable-media validator ───────────────────────────────────────────────

class TestMediaValidation:

    def test_usable_mp4(self, public_dns):
        body = faststart_mp4()
        t, calls = cdn_transport(body)
        ff = runner(ffprobe_json())
        r = _validate(transport=t, ffprobe=ff)
        assert r["media_check"] == "usable" and r["usable_media_url"] is True
        assert r["media_url_http_status"] == 206 and r["media_content_type"] == "video/mp4"
        assert r["media_duration_sec"] == 12.5 and r["media_total_bytes"] == len(body)
        assert r["media_url_expiry_if_known"].startswith("2033-05-18")
        assert len(calls) == 1 and ff.seen and ff.seen[0] <= 2 * 1024 * 1024
        assert "SIGSECRET" not in json.dumps(r) and "douyinvod" not in json.dumps(r)

    def test_range_is_bounded(self, public_dns):
        body = faststart_mp4(total_pad=5_000_000)
        t, _ = cdn_transport(body, status=200)      # server ignores Range
        ff = runner(ffprobe_json())
        r = _validate(transport=t, ffprobe=ff, max_bytes=256 * 1024)
        assert r["media_check"] == "usable" and r["media_bytes_read"] == 256 * 1024

    def test_html_page_is_unusable(self, public_dns):
        t, _ = cdn_transport(b"<html>" + b"x" * 100_000, status=200, ctype="text/html; charset=utf-8")
        r = _validate(transport=t, ffprobe=runner(ffprobe_json()))
        assert r["media_check"] == "unusable" and r["usable_media_url"] is False
        assert r["media_check_reason"] == "not_media_content_type"

    def test_tiny_body_is_unusable(self, public_dns):
        t, _ = cdn_transport(faststart_mp4()[:500], total=500)
        r = _validate(transport=t, ffprobe=runner(ffprobe_json()))
        assert r["media_check"] == "unusable" and r["media_check_reason"] == "body_too_small"

    def test_http_error_is_unusable(self, public_dns):
        t, _ = cdn_transport(b"denied", status=403, ctype="text/plain")
        r = _validate(transport=t)
        assert r["media_check"] == "unusable" and r["media_url_http_status"] == 403
        assert r["media_check_reason"] == "http_403"

    def test_ffprobe_missing_is_not_checked_never_success(self, public_dns):
        t, _ = cdn_transport(faststart_mp4())
        r = _validate(transport=t, ffprobe_present=False)
        assert r["media_check"] == "not_checked" and r["usable_media_url"] is None
        assert r["media_check_reason"] == "ffprobe_unavailable"
        assert r["media_url_http_status"] == 206

    def test_ffprobe_binary_vanishing_is_not_checked(self, public_dns):
        from app.services.format_probe import ProbeError

        async def gone(data):
            raise ProbeError("ffprobe_unavailable")
        t, _ = cdn_transport(faststart_mp4())
        r = _validate(transport=t, ffprobe=gone)
        assert r["media_check"] == "not_checked" and r["usable_media_url"] is None

    def test_no_video_stream_is_unusable(self, public_dns):
        t, _ = cdn_transport(faststart_mp4())
        r = _validate(transport=t, ffprobe=runner(ffprobe_json(video=False)))
        assert r["media_check"] == "unusable" and r["media_check_reason"] == "ffprobe:no_video_stream"

    def test_implausible_duration_is_unusable(self, public_dns):
        t, _ = cdn_transport(faststart_mp4())
        r = _validate(transport=t, ffprobe=runner(ffprobe_json(duration="0")))
        assert r["media_check"] == "unusable" and r["media_check_reason"] == "implausible_duration"

    def test_moov_at_end_is_not_checked(self, public_dns):
        body = _box(b"ftyp", 16) + _box(b"mdat", 300_000) + _box(b"moov", 100)
        t, _ = cdn_transport(body)
        r = _validate(transport=t, ffprobe=runner(ffprobe_json()))
        assert r["media_check"] == "not_checked" and r["media_check_reason"] == "moov_not_in_prefix"

    def test_ssrf_blocked_host_is_never_fetched(self):
        t, calls = cdn_transport(faststart_mp4())
        r = _validate("http://169.254.169.254/latest/meta-data/?x-signature=abc", transport=t,
                      ffprobe=runner(ffprobe_json()))
        assert r["media_check"] == "unusable" and r["media_check_reason"] == "ssrf_blocked"
        assert calls == []

    def test_redirect_to_private_address_is_blocked(self, public_dns):
        t, calls = cdn_transport(b"", redirect_to="http://127.0.0.1:6379/")
        r = _validate(transport=t, ffprobe=runner(ffprobe_json()))
        assert r["media_check_reason"] == "ssrf_blocked" and len(calls) == 1

    def test_no_url(self):
        assert _validate("")["media_check_reason"] == "no_media_url"


# ── 2. actual-cost reconciliation ───────────────────────────────────────────

def _reserve(platform="douyin", est=0.0071):
    return budget_guard.reserve(platform, "admin",
                                budget_guard.PaidCandidate("apify_douyin", "apify", int(est * 1e6)))


def _spend(rc, day_only=False):
    d, m = budget_guard.day_key(), budget_guard.month_key()
    vals = [int(rc.get(budget_guard.k_provider_spend(d, "apify")) or 0),
            int(rc.get(budget_guard.k_platform_spend(d, "douyin")) or 0)]
    if not day_only:
        vals.append(int(rc.get(budget_guard.k_provider_month(m, "apify")) or 0))
    return vals


class TestReconciliation:

    def test_actual_below_estimate_adjusts_counters(self, flags_on, rc):
        out = budget_guard.reconcile(_reserve(), 0.004, True)
        assert out == {"estimated_cost_usd": 0.0071, "actual_cost_usd": 0.004,
                       "cost_delta_usd": -0.0031, "cost_flag": None}
        assert _spend(rc) == [4000, 4000, 4000]

    def test_overrun_over_50pct_is_flagged(self, flags_on, rc):
        out = budget_guard.reconcile(_reserve(), 0.0110, True)     # +55 %
        assert out["cost_flag"] == "over_estimate" and out["cost_delta_usd"] == pytest.approx(0.0039)
        assert _spend(rc) == [11000, 11000, 11000]
        out = budget_guard.reconcile(_reserve(), 0.0100, True)     # +41 %: not flagged
        assert out["cost_flag"] is None
        snap = budget_guard.reconcile_snapshot("apify")
        assert snap["runs"] == 2 and snap["flagged_over"] == 1
        assert snap["delta_usd"] == pytest.approx(0.0039 + 0.0029)

    def test_missing_actual_keeps_estimate_and_flags(self, flags_on, rc):
        out = budget_guard.reconcile(_reserve(), None, True)
        assert out["cost_flag"] == "actual_missing" and out["actual_cost_usd"] is None
        assert out["cost_delta_usd"] is None
        assert _spend(rc) == [7100, 7100, 7100]
        assert budget_guard.reconcile_snapshot("apify")["flagged_missing"] == 1

    def test_no_run_started_refunds_without_flag(self, flags_on, rc):
        out = budget_guard.reconcile(_reserve(), None, False)
        assert out["cost_flag"] is None and out["actual_cost_usd"] == 0.0
        assert _spend(rc) == [0, 0, 0]
        assert budget_guard.reconcile_snapshot("apify")["runs"] == 0

    def test_router_records_delta_and_flag(self, flags_on, rc, alerts):
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_x" * 2)
        n = FakeProvider("native_douyin", outcome="cookie_required")
        a = FakeProvider("apify_douyin", paid=True, cost=None)
        router = ProviderRouter(factory(n, a))
        asyncio.run(router.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        paid = router.attempts[-1]
        assert paid["provider"] == "apify_douyin" and paid["cost_flag"] == "actual_missing"
        assert paid["cost_source"] == "estimated"
        router = ProviderRouter(factory(n, FakeProvider("apify_douyin", paid=True, cost=0.003)))
        asyncio.run(router.resolve(ChinaResolveRequest(url=DY_URL2), PUBLIC_CTX))
        assert router.attempts[-1]["cost_delta_usd"] == pytest.approx(-0.0041)
        assert router.attempts[-1]["cost_flag"] is None


# ── 3. spend alerts ─────────────────────────────────────────────────────────

class TestSpendAlerts:

    def _set(self, rc, daily=None, monthly=None):
        if daily is not None:
            rc.set(budget_guard.k_provider_spend(budget_guard.day_key(), "apify"), daily)
        if monthly is not None:
            rc.set(budget_guard.k_provider_month(budget_guard.month_key(), "apify"), monthly)

    def test_each_threshold_fires_once_per_period(self, flags_on, rc, alerts):
        flags_on.setenv("CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD", "0.01")
        flags_on.setenv("CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD", "100")
        self._set(rc, daily=4000)
        assert budget_guard.check_spend_alerts("apify") == []
        self._set(rc, daily=5000)
        assert budget_guard.check_spend_alerts("apify") == [("daily", 50)]
        assert budget_guard.check_spend_alerts("apify") == []          # no spam
        self._set(rc, daily=8000)
        assert budget_guard.check_spend_alerts("apify") == [("daily", 80)]
        self._set(rc, daily=10000)
        assert budget_guard.check_spend_alerts("apify") == [("daily", 100)]
        assert budget_guard.check_spend_alerts("apify") == []
        assert [a[0] for a in alerts] == ["info", "warning", "critical"]
        assert "100%" in alerts[-1][1] and "$0.0100" in alerts[-1][2]

    def test_jump_claims_lower_thresholds_with_one_message(self, flags_on, rc, alerts):
        flags_on.setenv("CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD", "0.01")
        self._set(rc, daily=9000)
        assert budget_guard.check_spend_alerts("apify") == [("daily", 80)]
        assert len(alerts) == 1
        self._set(rc, daily=9500)
        assert budget_guard.check_spend_alerts("apify") == []

    def test_monthly_and_new_period(self, flags_on, rc, alerts, monkeypatch):
        flags_on.setenv("CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD", "1.00")
        monkeypatch.setattr(budget_guard, "utcnow", lambda: datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
        self._set(rc, monthly=600_000)
        assert budget_guard.check_spend_alerts("apify") == [("monthly", 50)]
        assert budget_guard.check_spend_alerts("apify") == []
        monkeypatch.setattr(budget_guard, "utcnow", lambda: datetime(2026, 11, 1, 0, 30, tzinfo=timezone.utc))
        self._set(rc, monthly=600_000)
        assert budget_guard.check_spend_alerts("apify") == [("monthly", 50)]

    def test_redis_down_sends_nothing(self, flags_on, monkeypatch, alerts):
        monkeypatch.setattr("app.core.redis_client._client", down_redis())
        assert budget_guard.check_spend_alerts("apify") == [] and alerts == []

    def test_router_triggers_alert_once(self, flags_on, rc, alerts):
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_xxxxxxxx")
        flags_on.setenv("CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD", "0.02")
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD", "1")
        for url in (DY_URL, DY_URL2):
            router = ProviderRouter(factory(FakeProvider("native_douyin", outcome="cookie_required"),
                                            FakeProvider("apify_douyin", paid=True, cost=0.006)))
            asyncio.run(router.resolve(ChinaResolveRequest(url=url), PUBLIC_CTX))
        # 0.006 → 30 %, 0.012 → 60 %: exactly one 50 % alert
        assert [a[0] for a in alerts] == ["info"] and "50%" in alerts[0][1]


# ── 4. automatic rollback ───────────────────────────────────────────────────

def _override(rc):
    return rc.get(registry.MODE_OVERRIDE_KEY.format(platform="douyin"))


class TestAutoRollback:

    def test_ceiling_reached_stops_managed_route(self, flags_on, rc):
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_xxxxxxxx")
        rc.set(budget_guard.k_provider_month(budget_guard.month_key(), "apify"), 5_000_000)   # = $5 ceiling
        calls = []
        r = ProviderRouter(factory(FakeProvider("native_douyin", outcome="cookie_required", calls=calls),
                                   FakeProvider("apify_douyin", paid=True, calls=calls)))
        with pytest.raises(ChinaAccessFailure) as ei:
            asyncio.run(r.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        assert ei.value.category == "budget_exceeded" and [c[0] for c in calls] == ["native_douyin"]
        rc.delete(budget_guard.k_provider_month(budget_guard.month_key(), "apify"))
        rc.set(budget_guard.k_provider_spend(budget_guard.day_key(), "apify"), 1_000_000)     # = $1 daily
        calls.clear()
        with pytest.raises(ChinaAccessFailure) as ei:
            asyncio.run(r.resolve(ChinaResolveRequest(url=DY_URL2), PUBLIC_CTX))
        assert ei.value.category == "budget_exceeded" and "apify_douyin" not in [c[0] for c in calls]

    def test_two_probe_failures_lower_mode_to_benchmark(self, flags_on, rc, alerts, audit):
        assert registry.effective_managed_mode("douyin") == "on"
        assert rollout_guard.record_managed_outcome("douyin", "apify_douyin", "probe", success=False,
                                                    usable=None, category="provider_timeout") is None
        rec = rollout_guard.record_managed_outcome("douyin", "apify_douyin", "probe", success=False,
                                                   usable=None, category="provider_timeout")
        assert rec and rec["reason"] == "auto_rollback"
        assert _override(rc) == "benchmark" and registry.effective_managed_mode("douyin") == "benchmark"
        assert audit[0][0] == "admin.china_access.mode"
        assert audit[0][1]["metadata"]["reason"] == "auto_rollback"
        assert audit[0][1]["metadata"]["new"]["effective"] == "benchmark"
        assert alerts and alerts[0][0] == "critical"
        assert rollout_guard.last_rollback("douyin")["prior"]["effective"] == "on"

    def test_probe_success_resets_consecutive_count(self, flags_on, rc, alerts, audit):
        for ok in (False, True, False):
            rollout_guard.record_managed_outcome("douyin", "apify_douyin", "probe", success=ok, usable=None,
                                                 category=None if ok else "provider_unavailable")
        assert _override(rc) is None and audit == []

    def test_low_usable_rate_over_window(self, flags_on, rc, alerts, audit):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "canary_admin")
        seq = [True] * 5 + [False] * 4      # 9 entries: window not full yet
        for ok in seq:
            rollout_guard.record_managed_outcome("douyin", "apify_douyin", "request", success=ok, usable=None,
                                                 category=None if ok else "provider_unavailable")
        assert _override(rc) is None
        # a success whose media URL is UNUSABLE counts as bad: 5/10 = 50 % → not below 50 %
        rollout_guard.record_managed_outcome("douyin", "apify_douyin", "request", success=True, usable=False)
        assert _override(rc) is None
        # one more bad: last 10 = 4 good / 6 bad = 40 % → rollback
        rollout_guard.record_managed_outcome("douyin", "apify_douyin", "worker", success=False, usable=None,
                                             category="provider_timeout")
        assert _override(rc) == "benchmark"
        assert registry.effective_managed_mode("douyin") == "benchmark"
        assert rollout_guard.window_snapshot("douyin")["window_filled"] == 0      # reset after rollback

    def test_never_raises_mode(self, flags_on, rc, alerts, audit):
        rc.set(registry.MODE_OVERRIDE_KEY.format(platform="douyin"), "off")
        for _ in range(3):
            rollout_guard.record_managed_outcome("douyin", "apify_douyin", "probe", success=False, usable=None,
                                                 category="provider_timeout")
        assert _override(rc) == "off" and registry.effective_managed_mode("douyin") == "off"
        assert audit == [] and alerts == []
        rc.delete(registry.MODE_OVERRIDE_KEY.format(platform="douyin"))
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "benchmark")
        for _ in range(3):
            rollout_guard.record_managed_outcome("douyin", "apify_douyin", "probe", success=False, usable=None,
                                                 category="provider_timeout")
        assert _override(rc) is None and audit == []

    def test_benchmark_and_user_link_failures_ignored(self, flags_on, rc, alerts, audit):
        for _ in range(12):
            rollout_guard.record_managed_outcome("douyin", "apify_douyin", "benchmark", success=False,
                                                 usable=None, category="provider_timeout")
            rollout_guard.record_managed_outcome("douyin", "apify_douyin", "probe", success=False,
                                                 usable=None, category="private_or_login_required")
        assert _override(rc) is None

    def test_other_platforms_untouched(self, flags_on, rc, alerts, audit):
        for _ in range(3):
            assert rollout_guard.record_managed_outcome("kuaishou", "x", "probe", success=False, usable=None,
                                                        category="provider_timeout") is None

    def test_disabled_by_env(self, flags_on, rc, alerts, audit):
        flags_on.setenv("CHINA_ACCESS_AUTO_ROLLBACK_ENABLED", "false")
        for _ in range(3):
            rollout_guard.record_managed_outcome("douyin", "apify_douyin", "probe", success=False, usable=None,
                                                 category="provider_timeout")
        assert _override(rc) is None

    def test_through_router_then_paid_route_closed_for_public(self, flags_on, rc, alerts, audit):
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_xxxxxxxx")
        flags_on.setenv("CHINA_ACCESS_AUTO_ROLLBACK_WINDOW", "2")
        calls = []

        def router():
            return ProviderRouter(factory(FakeProvider("native_douyin", outcome="cookie_required", calls=calls),
                                          FakeProvider("apify_douyin", paid=True, outcome="provider_unavailable",
                                                       calls=calls, cost=0.007)))
        for url in (DY_URL, DY_URL2):
            with pytest.raises(ChinaAccessFailure):
                asyncio.run(router().resolve(ChinaResolveRequest(url=url), PUBLIC_CTX))
        assert registry.effective_managed_mode("douyin") == "benchmark"
        calls.clear()
        with pytest.raises(ChinaAccessFailure):
            asyncio.run(router().resolve(ChinaResolveRequest(url=DY_URL3), PUBLIC_CTX))
        assert [c[0] for c in calls] == ["native_douyin"]     # no paid call after rollback

    def test_admin_can_raise_again_up_to_env_ceiling(self, app, admin, flags_on, rc, alerts, audit, monkeypatch):
        import app.api.admin_china_platforms as mod
        monkeypatch.setattr(mod, "log_admin_action", lambda *a, **k: None)
        rollout_guard.auto_rollback("douyin", "test")
        assert registry.effective_managed_mode("douyin") == "benchmark"
        r = app.post("/api/v1/admin/china-platforms/douyin/mode", json={"mode": "canary_admin", "reason": "recovered"})
        assert r.status_code == 200 and r.json()["new"]["effective"] == "canary_admin"
        detail = app.get("/api/v1/admin/china-platforms/douyin").json()
        assert detail["auto_rollback"]["last_rollback"]["reason"] == "auto_rollback"


# ── 5. watermark review gate ────────────────────────────────────────────────

def _hashes(n):
    return [url_hash(f"https://www.douyin.com/video/73000000000000{i:05d}") for i in range(n)]


class TestWatermark:

    def _review(self, states, provider="apify_douyin"):
        for h, st in zip(_hashes(len(states)), states):
            watermark.record_review("douyin", provider, h, st, "checked by eye", "2026-10-06T00:00:00+00:00")

    def test_gate_needs_ten_reviews(self, flags_on, rc):
        self._review(["watermark_free"] * 9)
        agg = watermark.aggregate("douyin", "apify_douyin")
        assert agg["reviewed"] == 9 and agg["watermark_state"] == "unknown" and not agg["gate_passed"]
        self._review(["watermark_free"] * 10)
        assert watermark.gated_state("douyin", "apify_douyin") == "watermark_free"

    def test_gate_needs_ninety_percent(self, flags_on, rc):
        self._review(["watermark_free"] * 8 + ["watermarked", "unknown"])
        agg = watermark.aggregate("douyin", "apify_douyin")
        assert agg["watermark_free_rate"] == 0.8 and agg["watermark_state"] == "unknown"
        assert agg["distribution"] == {"watermark_free": 8, "watermarked": 1, "unknown": 1}

    def test_gate_is_env_configurable(self, flags_on, rc):
        flags_on.setenv("CHINA_ACCESS_WATERMARK_MIN_REVIEWED", "2")
        flags_on.setenv("CHINA_ACCESS_WATERMARK_MIN_FREE_RATE", "0.5")
        self._review(["watermark_free", "watermarked"])
        assert watermark.gated_state("douyin", "apify_douyin") == "watermark_free"

    def test_router_result_state_follows_gate_ui_unchanged(self, flags_on, rc):
        from app.services.china_platforms.integration import _to_legacy_dict
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_xxxxxxxx")
        mk = lambda: ProviderRouter(factory(FakeProvider("native_douyin", outcome="cookie_required"),  # noqa: E731
                                            FakeProvider("apify_douyin", paid=True, cost=0.007)))
        res = asyncio.run(mk().resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        assert res.watermark_state == "unknown"
        self._review(["watermark_free"] * 10)
        router = mk()
        res = asyncio.run(router.resolve(ChinaResolveRequest(url=DY_URL2), PUBLIC_CTX))
        assert res.watermark_state == "watermark_free" and router.attempts[-1]["watermark_state"] == "watermark_free"
        assert "watermark" not in json.dumps(_to_legacy_dict(res, DY_URL2, "720p"))

    def test_review_endpoint(self, app, admin, flags_on, rc, monkeypatch):
        import app.api.admin_china_platforms as mod
        audit_rows = []
        monkeypatch.setattr(mod, "log_admin_action", lambda req, action, **kw: audit_rows.append((action, kw)))
        h = _hashes(1)[0]
        body = {"provider": "apify_douyin", "url_hash": h, "state": "watermark_free",
                "note": f"no logo; src {SIGNED}"}
        path = "/api/v1/admin/china-platforms/douyin/watermark-review"
        assert app.post(path, json=body).status_code == 404            # not a benchmark item
        watermark.register_items("douyin", [("apify_douyin", h)])
        assert app.post(path, json={**body, "state": "maybe"}).status_code == 400
        assert app.post(path, json={**body, "provider": "apify_kuaishou"}).status_code == 404
        assert app.post(path, json={k: v for k, v in body.items() if k != "note"}).status_code == 422
        r = app.post(path, json=body)
        assert r.status_code == 200 and r.json()["route"]["reviewed"] == 1
        assert r.json()["route"]["watermark_state"] == "unknown"
        assert audit_rows[-1][0] == "admin.china_access.watermark_review"
        assert "x-signature" not in json.dumps(audit_rows[-1][1])
        stored = rc.hget(watermark.REVIEWS_KEY.format(platform="douyin", provider="apify_douyin"), h)
        assert "SIGSECRET" not in stored and "x-signature" not in stored
        g = app.get("/api/v1/admin/china-platforms/douyin/watermark").json()
        assert g["ui_wording_changed"] is False
        assert {x["provider"] for x in g["routes"]} == {"apify_douyin", "native_douyin"}


# ── 6. report aggregates + recommendation rules ────────────────────────────

def _att(provider, outcome="success", media="usable", cost=0.0, lat=100, cat=None, flag=None, h=None,
         mode=None, reason=None):
    return {"provider": provider, "outcome": outcome, "latency_ms": lat, "actual_cost_usd": cost,
            "normalized_failure_category": cat, "media_check": media if outcome == "success" else "not_run",
            "media_check_reason": reason, "cost_flag": flag, "canonical_url_hash": h,
            "route_mode": mode or ("managed" if provider == "apify_douyin" else "native"),
            "duration_missing": False}


def _managed(n_ok, n_fail=0, *, media="usable", cost=0.007, cat="provider_unavailable", flag=None):
    return ([_att("apify_douyin", cost=cost, lat=1000 + i, media=media, flag=flag, h=f"h{i}") for i in range(n_ok)]
            + [_att("apify_douyin", "failure", cost=cost, lat=500, cat=cat) for _ in range(n_fail)])


def _native(n_ok, n_fail):
    return ([_att("native_douyin") for _ in range(n_ok)]
            + [_att("native_douyin", "failure", cat="cookie_required") for _ in range(n_fail)])


PAID = {"apify_douyin"}


class TestReport:

    def test_aggregates(self, flags_on, rc):
        att = _managed(8, 2) + [_att("apify_douyin", media="unusable", reason="http_403", cost=0.007)]
        att.append({**_att("apify_douyin", "skipped"), "normalized_failure_category": "budget_exceeded"})
        s = summarize(att, paid_routes=PAID, watermark_reviews={"apify_douyin": {"h0": {"state": "watermarked"}}})
        m = s["apify_douyin"]
        assert m["sample_size"] == 11 and m["skipped"] == 1 and m["skip_mix"] == {"budget_exceeded": 1}
        assert m["success_rate"] == round(9 / 11, 4) and m["usable_media_rate"] == round(8 / 11, 4)
        assert m["cost_per_attempt_usd"] == pytest.approx(0.007)
        assert m["cost_per_usable_success_usd"] == pytest.approx(0.077 / 8, abs=1e-6)
        assert m["p50_latency_ms"] == 1002 and m["p95_latency_ms"] == 1007
        assert m["failure_category_mix"] == {"provider_unavailable": 2}
        assert m["media_reasons"] == {"http_403": 1}
        assert m["watermark_distribution"] == {"watermark_free": 0, "watermarked": 1, "unknown": 0,
                                               "not_reviewed": 7}

    @pytest.mark.parametrize("att, expected, rule", [
        (_managed(10), "keep_benchmark", "R1"),
        (_managed(19, 1, media="not_checked"), "keep_benchmark", "R2"),
        (_managed(9, 11) + _native(0, 20), "reject", "M1"),
        (_managed(16, 5, cat="provider_timeout") + _native(0, 20), "reject", "M2"),
        (_managed(20, cost=0.02) + _native(0, 20), "reject", "M3"),
        (_managed(20) + _native(18, 2), "native_only", "M4"),
        (_managed(20, flag="actual_missing") + _native(0, 20), "keep_benchmark", "M5"),
        (_managed(19, 1) + _native(0, 20), "promote_canary_admin", "M6"),
        (_managed(19, 1) + _native(19, 6), "keep_benchmark", "M7"),
    ])
    def test_managed_rules(self, flags_on, att, expected, rule):
        s = summarize(att, paid_routes=PAID)
        assert s["apify_douyin"]["recommendation"] == expected
        assert s["apify_douyin"]["recommendation_reasons"][0].startswith(rule)

    @pytest.mark.parametrize("att, expected, rule", [
        (_native(18, 2), "native_only", "N1"),
        (_native(5, 15), "reject", "N2"),
        (_native(13, 7), "keep_benchmark", "N3"),
    ])
    def test_native_rules(self, flags_on, att, expected, rule):
        s = summarize(att, paid_routes=PAID)
        assert s["native_douyin"]["recommendation"] == expected
        assert s["native_douyin"]["recommendation_reasons"][0].startswith(rule)

    def test_overall(self, flags_on):
        assert overall_recommendation(summarize(_managed(19, 1) + _native(0, 20), paid_routes=PAID)
                                      )["recommendation"] == "promote_canary_admin"
        assert overall_recommendation(summarize(_managed(20) + _native(18, 2), paid_routes=PAID)
                                      )["recommendation"] == "native_only"
        assert overall_recommendation(summarize(_native(5, 15), paid_routes=PAID))["recommendation"] == "keep_benchmark"
        assert recommend({"sample_size": 0}, {})[0] == "keep_benchmark"

    def test_thresholds_from_env(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_BENCH_MIN_SAMPLE", "5")
        s = summarize(_managed(5) + _native(0, 5), paid_routes=PAID)
        assert s["apify_douyin"]["recommendation"] == "promote_canary_admin"


# ── 6b. end-to-end benchmark run + redaction ───────────────────────────────

class TestBenchmarkRun:

    def _make(self, calls):
        def make():
            return ProviderRouter(factory(
                FakeProvider("native_douyin", outcome="cookie_required", calls=calls),
                FakeProvider("apify_douyin", paid=True, calls=calls, cost=0.007)))
        return make

    def test_run_validates_media_and_reports_without_urls(self, flags_on, rc, tmp_path, alerts, caplog,
                                                          app, admin):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "benchmark")
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_SHOULDNOTLEAK1")
        seen = []

        async def validator(url, platform="douyin"):
            seen.append(url)
            # A hostile/buggy reason containing the signed URL must still be scrubbed.
            return media_validation._result("unusable", f"weird {url}", media_url_http_status=403,
                                            media_url_expiry_if_known=media_validation.expiry_iso(url))
        calls = []
        fx = [{"url": DY_URL, "case": "public_single"}, {"url": DY_URL2, "case": "short_url"}]
        caplog.set_level(logging.INFO, logger="app.china_access")
        rep = asyncio.run(run_benchmark("douyin", include_managed=True, fixtures=fx, out_dir=str(tmp_path),
                                        router_factory=self._make(calls), media_validator=validator))
        assert seen == [SIGNED, SIGNED]                      # only paid successes validated
        m = rep["summary"]["apify_douyin"]
        assert m["media_unusable"] == 2 and m["usable_media_rate"] == 0.0
        assert m["recommendation"] == "keep_benchmark"       # R1: sample 2 < 20
        assert rep["overall"]["recommendation"] == "keep_benchmark"
        assert rep["summary"]["native_douyin"]["usable_media_rate"] is None
        row = [a for a in rep["attempts"] if a["provider"] == "apify_douyin"][0]
        assert row["usable_media_url"] is False and row["media_url_present"] is True
        assert row["media_url_expiry_if_known"].startswith("2033")
        blobs = [(tmp_path / rep["files"]["json"]).read_text(encoding="utf-8"),
                 (tmp_path / rep["files"]["csv"]).read_text(encoding="utf-8"),
                 caplog.text, json.dumps(rc.get("china:benchmark:last_attempts:douyin")),
                 json.dumps(rc.get("china:benchmark:last:douyin"))]
        for b in blobs:
            for bad in ("x-signature", "x-expires=", "SIGSECRET", "SHOULDNOTLEAK", DY_URL, DY_URL2):
                assert bad not in b, bad
        # reviewable items registered; report recomputes with reviews
        h = row["canonical_url_hash"]
        assert watermark.is_benchmark_item("douyin", "apify_douyin", h)
        watermark.record_review("douyin", "apify_douyin", h, "watermark_free", "ok", "t")
        again = report_from_last("douyin")
        assert again["summary"]["apify_douyin"]["watermark_distribution"]["watermark_free"] == 1
        r = app.get("/api/v1/admin/china-platforms/douyin/benchmark/report")
        assert r.status_code == 200 and r.json()["overall"]["recommendation"] == "keep_benchmark"
        assert "x-signature" not in r.text and "SIGSECRET" not in r.text

    def test_report_endpoint_404_without_run(self, app, admin, flags_on, rc):
        assert app.get("/api/v1/admin/china-platforms/douyin/benchmark/report").status_code == 404

    def test_validation_can_be_turned_off(self, flags_on, rc, tmp_path):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "benchmark")
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_xxxxxxxx")
        flags_on.setenv("CHINA_ACCESS_BENCHMARK_VALIDATE_MEDIA", "false")

        async def boom(url, platform="douyin"):
            raise AssertionError("must not validate")
        rep = asyncio.run(run_benchmark("douyin", include_managed=True, fixtures=[{"url": DY_URL, "case": "c"}],
                                        write=False, router_factory=self._make([]), media_validator=boom))
        row = [a for a in rep["attempts"] if a["provider"] == "apify_douyin"][0]
        assert row["media_check"] == "not_run" and rep["media_validation"]["enabled"] is False


# ── router flag (default off) ───────────────────────────────────────────────

class TestRouterValidationFlag:

    def test_off_by_default_no_fetch(self, flags_on, rc, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("validator must not run")
        monkeypatch.setattr(media_validation, "validate_media_url", boom)
        router = ProviderRouter(factory(FakeProvider("native_douyin")))
        asyncio.run(router.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        assert router.attempts[-1]["media_check"] == "not_run"
        assert router.attempts[-1]["usable_media_url"] is True     # wave-1 meaning: URL present

    def test_on_unusable_falls_back(self, flags_on, rc, monkeypatch):
        flags_on.setenv("CHINA_ACCESS_ROUTER_VALIDATE_MEDIA", "true")
        flags_on.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_xxxxxxxx")
        verdicts = iter(["unusable", "usable"])

        async def fake(url, platform="douyin"):
            v = next(verdicts)
            return media_validation._result(v, "http_403" if v == "unusable" else None)
        monkeypatch.setattr(media_validation, "validate_media_url", fake)
        calls = []
        router = ProviderRouter(factory(FakeProvider("native_douyin", calls=calls),
                                        FakeProvider("apify_douyin", paid=True, calls=calls, cost=0.007)))
        res = asyncio.run(router.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        assert res.provider_name == "apify_douyin" and [c[0] for c in calls] == ["native_douyin", "apify_douyin"]
        assert router.attempts[0]["outcome"] == "failure" and router.attempts[0]["media_check"] == "unusable"
        assert router.attempts[1]["usable_media_url"] is True
