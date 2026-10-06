"""
Phase 32B-1 — guarded provider router (plan §8), budget guard (§9), dedupe,
kill switches, once-per-provider and fallback eligibility.

Why these exist: 2026-10-05 a bulk run created ~175 Douyin jobs in one second
and each retried 4+ times (1233 failures). With a paid actor in the chain that
pattern would have billed every attempt. These tests hold the guards that
make that impossible: budget before dispatch, one paid call per URL, no
fallback after budget_exceeded, kill switch first.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.china_platforms import budget_guard, registry, request_cache
from app.services.china_platforms.errors import AlreadyProcessing, ChinaAccessFailure
from app.services.china_platforms.normalized_models import ChinaResolveRequest, RequestContext
from app.services.china_platforms.provider_router import ProviderRouter
from tests._china_fakes import (  # noqa: F401
    ADMIN_CTX, DY_URL, DY_URL2, DY_URL3, PUBLIC_CTX, FakeProvider, clean_env, down_redis, factory,
    flags_on, rc,
)


def _req(url=DY_URL):
    return ChinaResolveRequest(url=url)


def _run(router, url=DY_URL, ctx=PUBLIC_CTX, **kw):
    return asyncio.run(router.resolve(_req(url), ctx, **kw))


def _fail(router, url=DY_URL, ctx=PUBLIC_CTX, **kw) -> ChinaAccessFailure:
    with pytest.raises(ChinaAccessFailure) as ei:
        _run(router, url, ctx, **kw)
    return ei.value


def _pair(native="ok", paid="ok", **paid_kw):
    calls: list = []
    n = FakeProvider("native_douyin", outcome=native, calls=calls)
    a = FakeProvider("apify_douyin", paid=True, outcome=paid, calls=calls, **paid_kw)
    return n, a, calls


def _names(calls):
    return [c[0] for c in calls]


# ── 1-4: hard guards ────────────────────────────────────────────────────────

class TestHardGuards:

    def test_master_off_is_platform_disabled_and_nothing_runs(self, clean_env, rc):
        n, a, calls = _pair()
        f = _fail(ProviderRouter(factory(n, a)))
        assert f.category == "platform_disabled" and calls == []

    def test_unknown_platform_is_unsupported_url(self, flags_on):
        f = _fail(ProviderRouter(factory()), url="https://example.com/v/1")
        assert f.category == "unsupported_url"

    @pytest.mark.parametrize("url", [
        "https://www.bilibili.com/video/BV1ECeJ65EZS",   # registered, NOT routed (correction #5)
        # Phase 32B-3: routable now, but disabled without their own
        # CHINA_ACCESS_<P>_ENABLED (flags_on enables Douyin only).
        "https://www.kuaishou.com/short-video/3x123",
        "https://www.xiaohongshu.com/explore/6a06c9360000000036001d5a",
        "https://www.lemon8-app.com/@u/123",
    ])
    def test_non_routable_platforms_are_disabled(self, flags_on, url):
        f = _fail(ProviderRouter(factory()), url=url)
        assert f.category == "platform_disabled"

    def test_platform_flag_off(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_ENABLED", "false")
        n, a, calls = _pair()
        assert _fail(ProviderRouter(factory(n, a))).category == "platform_disabled"
        assert calls == []

    def test_env_global_kill_switch(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_GLOBAL_KILL_SWITCH", "true")
        n, a, calls = _pair()
        assert _fail(ProviderRouter(factory(n, a))).category == "platform_disabled"
        assert calls == []

    def test_redis_global_and_platform_kill_switch(self, flags_on):
        n, a, calls = _pair()
        budget_guard.set_killswitch("global", True)
        assert _fail(ProviderRouter(factory(n, a))).category == "platform_disabled"
        budget_guard.set_killswitch("global", False)
        budget_guard.set_killswitch("douyin", True)
        assert _fail(ProviderRouter(factory(n, a))).category == "platform_disabled"
        assert calls == []
        budget_guard.set_killswitch("douyin", False)
        assert _run(ProviderRouter(factory(n, a))).provider_name == "native_douyin"

    def test_kill_switch_unreadable_fails_closed(self, flags_on, monkeypatch):
        monkeypatch.setattr("app.core.redis_client._client", down_redis())
        n, a, calls = _pair()
        assert _fail(ProviderRouter(factory(n, a))).category == "platform_disabled"
        assert calls == []


# ── guard ORDER ─────────────────────────────────────────────────────────────

class TestGuardOrder:

    def test_trace_follows_plan_order(self, flags_on):
        n, a, _ = _pair()
        r = ProviderRouter(factory(n, a))
        _run(r)
        assert r.trace == ["identify", "registry", "master", "platform", "budget_precheck",
                           "cache", "dedupe", "health", "provider:native_douyin"]

    def test_each_failed_guard_stops_before_the_next(self, flags_on, monkeypatch):
        seen: list = []
        real_g, real_p, real_pre = (budget_guard.global_killswitch_on,
                                     budget_guard.platform_killswitch_on, budget_guard.precheck)
        monkeypatch.setattr(budget_guard, "global_killswitch_on", lambda: seen.append("global") or real_g())
        monkeypatch.setattr(budget_guard, "platform_killswitch_on",
                            lambda p: seen.append("platform") or real_p(p))
        monkeypatch.setattr(budget_guard, "precheck", lambda *a: seen.append("precheck") or real_pre(*a))
        monkeypatch.setattr(request_cache, "get_cached", lambda *a: seen.append("cache") or None)
        n, a, calls = _pair()

        flags_on.setenv("CHINA_ACCESS_ENABLED", "false")          # 3 fails
        _fail(ProviderRouter(factory(n, a)))
        assert seen == []
        flags_on.setenv("CHINA_ACCESS_ENABLED", "true")
        budget_guard.set_killswitch("global", True)               # 3 fails (kill)
        _fail(ProviderRouter(factory(n, a)))
        assert seen == ["global"]
        budget_guard.set_killswitch("global", False)
        budget_guard.set_killswitch("douyin", True)               # 4 fails
        seen.clear()
        _fail(ProviderRouter(factory(n, a)))
        assert seen == ["global", "platform"]
        budget_guard.set_killswitch("douyin", False)
        seen.clear()
        _run(ProviderRouter(factory(n, a)))                      # 5-7 before 8
        assert seen == ["global", "platform", "precheck", "cache"]
        assert calls and calls[0][0] == "native_douyin"


# ── budget before dispatch / no fallback after budget_exceeded ──────────────

class TestBudget:

    def test_budget_denied_paid_never_dispatched_and_no_fallback(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT", "0")
        flags_on.setenv("CHINA_ACCESS_DOUYIN_PROVIDER_ORDER", "apify_douyin,native_douyin")
        n, a, calls = _pair()
        f = _fail(ProviderRouter(factory(n, a)))
        assert f.category == "budget_exceeded" and f.stop_reason == "budget"
        assert calls == []   # neither the paid call nor a fallback after it

    def test_native_first_then_budget_stops(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD", "0")
        n, a, calls = _pair(native="cookie_required")
        f = _fail(ProviderRouter(factory(n, a)))
        assert _names(calls) == ["native_douyin"]
        assert f.category == "budget_exceeded"
        assert f.has_category("cookie_required")   # integration keeps the Douyin cookie wording

    @pytest.mark.parametrize("env,val,ctx", [
        ("CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT", "0", PUBLIC_CTX),
        ("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_CALL_LIMIT", "0", PUBLIC_CTX),
        ("CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD", "0.001", PUBLIC_CTX),
        ("CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT", "0", PUBLIC_CTX),
        ("CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD", "0.001", PUBLIC_CTX),
        ("CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD", "0.001", PUBLIC_CTX),
    ])
    def test_every_level_denies_in_reserve_and_rolls_back(self, flags_on, rc, monkeypatch, env, val, ctx):
        flags_on.setenv(env, val)
        # Bypass the read-only pre-check to prove the atomic reserve itself denies.
        monkeypatch.setattr(budget_guard, "precheck", lambda *a: None)
        n, a, calls = _pair(native="cookie_required")
        f = _fail(ProviderRouter(factory(n, a)), ctx=ctx)
        assert f.category == "budget_exceeded"
        assert "apify_douyin" not in _names(calls)
        counters = {k: rc.get(k) for k in rc.keys("china:quota:*") + rc.keys("china:platform:*")
                    + rc.keys("china:provider:*")}
        assert all(int(v) == 0 for v in counters.values()), counters

    def test_reserve_counts_one_call_per_level(self, flags_on, rc):
        n, a, calls = _pair(native="cookie_required", cost=0.004)
        res = _run(ProviderRouter(factory(n, a)))
        assert res.provider_name == "apify_douyin"
        day, month = budget_guard.day_key(), budget_guard.month_key()
        assert rc.get(budget_guard.k_user(day, PUBLIC_CTX.requester_key, "douyin")) == "1"
        assert rc.get(budget_guard.k_platform_calls(day, "douyin")) == "1"
        assert rc.get(budget_guard.k_provider_calls(day, "apify")) == "1"
        # settle: actual 0.004 replaces the 0.0071 estimate everywhere
        assert rc.get(budget_guard.k_provider_spend(day, "apify")) == "4000"
        assert rc.get(budget_guard.k_provider_month(month, "apify")) == "4000"
        assert rc.get(budget_guard.k_platform_spend(day, "douyin")) == "4000"

    def test_no_run_started_refunds_estimate(self, flags_on, rc):
        n, a, _ = _pair(native="cookie_required", paid="provider_unavailable", run_started=False)
        _fail(ProviderRouter(factory(n, a)))
        assert rc.get(budget_guard.k_provider_spend(budget_guard.day_key(), "apify")) == "0"
        assert rc.get(budget_guard.k_provider_calls(budget_guard.day_key(), "apify")) == "1"

    def test_unknown_actual_cost_keeps_estimate(self, flags_on, rc):
        n, a, _ = _pair(native="cookie_required", cost=None)
        r = ProviderRouter(factory(n, a))
        _run(r)
        assert rc.get(budget_guard.k_provider_spend(budget_guard.day_key(), "apify")) == "7100"
        assert r.attempts[-1]["cost_source"] == "estimated"

    def test_redis_down_budget_fails_closed(self, monkeypatch):
        monkeypatch.setattr("app.core.redis_client._client", down_redis())
        cand = budget_guard.PaidCandidate("apify_douyin", "apify", 7100)
        assert budget_guard.precheck("douyin", "ip:x", [cand]).level == "budget_unavailable"
        with pytest.raises(budget_guard.BudgetDenied):
            budget_guard.reserve("douyin", "ip:x", cand)

    def test_quota_applies_to_paid_path_only(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT", "0")
        n, a, calls = _pair()
        assert _run(ProviderRouter(factory(n, a))).provider_name == "native_douyin"


# ── once per provider / paid marker / dedupe ────────────────────────────────

class TestNoDuplicatePaidCalls:

    def test_duplicate_names_in_order_run_once(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_PROVIDER_ORDER", "native_douyin,apify_douyin,native_douyin,apify_douyin")
        n, a, calls = _pair(native="cookie_required", paid="provider_unavailable")
        _fail(ProviderRouter(factory(n, a)))
        assert _names(calls) == ["native_douyin", "apify_douyin"]

    def test_same_url_never_billed_twice_within_ttl(self, flags_on):
        n, a, calls = _pair(native="cookie_required", paid="provider_unavailable")
        _fail(ProviderRouter(factory(n, a)))
        _fail(ProviderRouter(factory(n, a)))     # e.g. a Celery retry of the same job
        assert _names(calls).count("apify_douyin") == 1
        assert _names(calls).count("native_douyin") == 2

    def test_concurrent_identical_urls_one_paid_run(self, flags_on, rc):
        n, a, calls = _pair(native="cookie_required", delay=0.3)

        async def burst():
            routers = [ProviderRouter(factory(n, a), poll_interval=0.05) for _ in range(10)]
            return await asyncio.gather(*(r.resolve(_req(), PUBLIC_CTX) for r in routers),
                                        return_exceptions=True)

        results = asyncio.run(burst())
        assert _names(calls).count("apify_douyin") == 1
        assert rc.get(budget_guard.k_provider_calls(budget_guard.day_key(), "apify")) == "1"
        ok = [r for r in results if not isinstance(r, Exception)]
        assert ok and all(r.title == "t" for r in ok)
        assert all(isinstance(r, AlreadyProcessing) for r in results if isinstance(r, Exception))
        assert sum(1 for r in ok if r.cache_hit) == len(ok) - 1

    def test_duplicate_gets_already_processing_when_first_is_slow(self, flags_on, rc):
        flags_on.setenv("CHINA_ACCESS_DEDUPE_WAIT_SEC", "0")
        rc.set(request_cache.dedupe_key("douyin", "single_media", request_cache.url_hash(DY_URL)), "other")
        n, a, calls = _pair()
        with pytest.raises(AlreadyProcessing):
            _run(ProviderRouter(factory(n, a)))
        assert calls == []


# ── fallback eligibility ────────────────────────────────────────────────────

class TestFallback:

    @pytest.mark.parametrize("cat", ["cookie_required", "signature_or_verification_failed",
                                     "provider_timeout", "parse_failed", "unknown"])
    def test_eligible_categories_fall_back_to_managed(self, flags_on, cat):
        n, a, calls = _pair(native=cat)
        assert _run(ProviderRouter(factory(n, a))).provider_name == "apify_douyin"

    @pytest.mark.parametrize("cat", ["private_or_login_required", "unsupported_url", "geo_restricted"])
    def test_ineligible_categories_do_not_pay(self, flags_on, cat):
        n, a, calls = _pair(native=cat)
        f = _fail(ProviderRouter(factory(n, a)))
        assert f.category == cat and f.stop_reason == "not_fallback_eligible"
        assert _names(calls) == ["native_douyin"]


# ── managed mode gating ─────────────────────────────────────────────────────

class TestModes:

    @pytest.mark.parametrize("mode,ctx,expect_paid", [
        ("off", ADMIN_CTX, False),
        ("benchmark", ADMIN_CTX, False),
        ("canary_admin", PUBLIC_CTX, False),
        ("canary_admin", ADMIN_CTX, True),
        ("on", PUBLIC_CTX, True),
        ("on", RequestContext(requester_key="probe", origin="probe"), False),
        ("on", RequestContext(requester_key="user:1", origin="request", private=True), False),
        ("benchmark", RequestContext(requester_key="admin", origin="benchmark", allow_managed_benchmark=True), True),
        ("benchmark", RequestContext(requester_key="admin", origin="benchmark"), False),
    ])
    def test_mode_gate(self, flags_on, mode, ctx, expect_paid):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", mode)
        n, a, calls = _pair(native="cookie_required")
        try:
            _run(ProviderRouter(factory(n, a)), ctx=ctx)
        except ChinaAccessFailure as f:
            assert f.has_category("cookie_required")
        assert ("apify_douyin" in _names(calls)) is expect_paid

    def test_redis_override_only_lowers(self, flags_on, rc):
        flags_on.setenv("CHINA_ACCESS_DOUYIN_MANAGED_MODE", "canary_admin")
        rc.set("china:mode:douyin", "on")
        assert registry.effective_managed_mode("douyin") == "canary_admin"
        rc.set("china:mode:douyin", "off")
        assert registry.effective_managed_mode("douyin") == "off"

    def test_unconfigured_paid_provider_is_skipped(self, flags_on):
        n, a, calls = _pair(native="cookie_required", configured=False)
        f = _fail(ProviderRouter(factory(n, a)))
        assert f.category == "cookie_required" and "apify_douyin" not in _names(calls)


# ── cache + health ──────────────────────────────────────────────────────────

class TestCacheAndHealth:

    def test_success_is_cached(self, flags_on):
        n, a, calls = _pair()
        assert _run(ProviderRouter(factory(n, a))).cache_hit is False
        assert _run(ProviderRouter(factory(n, a))).cache_hit is True
        assert len(calls) == 1

    def test_private_context_is_not_cached(self, flags_on, rc):
        n, a, calls = _pair()
        ctx = RequestContext(requester_key="user:1", origin="request", private=True)
        _run(ProviderRouter(factory(n, a)), ctx=ctx)
        _run(ProviderRouter(factory(n, a)), ctx=ctx)
        assert len(calls) == 2 and rc.keys("china:cache:*") == []

    def test_degraded_provider_is_skipped_until_cooldown(self, flags_on):
        flags_on.setenv("CHINA_ACCESS_HEALTH_FAIL_THRESHOLD", "2")
        n, a, calls = _pair(native="cookie_required", paid="provider_unavailable")
        for url in (DY_URL, DY_URL2):
            _fail(ProviderRouter(factory(n, a)), url=url)
        r = ProviderRouter(factory(n, a))
        _fail(r, url=DY_URL3)
        assert _names(calls).count("apify_douyin") == 2
        assert any(x["provider"] == "apify_douyin" and x["health_state"] == "degraded" for x in r.attempts)

    def test_user_errors_do_not_degrade(self, flags_on):
        from app.services.china_platforms import provider_health
        flags_on.setenv("CHINA_ACCESS_HEALTH_FAIL_THRESHOLD", "1")
        n, a, _ = _pair(native="private_or_login_required")
        _fail(ProviderRouter(factory(n, a)))
        assert provider_health.snapshot("native_douyin", "douyin").state == "healthy"
