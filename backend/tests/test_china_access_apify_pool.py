"""
Apify token pool (task #6036): migration of the single admin token, rotation,
state transitions from Apify's documented error shapes, retry-on-next-token
only when no run started, per-entry ceilings, monthly recovery, alerts once,
no token anywhere, cost metrics, cache-hit counting, expiry-aware cache and
the back-compat endpoints. httpx.MockTransport + fakeredis only: no network,
no paid call.

Error shapes (Apify OpenAPI, POST /v2/acts/{actorId}/runs,
https://docs.apify.com/api/v2/act-runs-post):
  401 {"error":{"type":"invalid-token","message":"Authentication token is not valid."}}
  402 {"error":{"type":"x402-payment-required", ...}}  "the user has exceeded their usage
      limit, does not have enough credits, ..."
  403 {"error":{"type":"insufficient-permissions", ...}}
  429 {"error":{"type":"rate-limit-exceeded", ...}}
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services.china_platforms import (
    apify_pool,
    budget_guard,
    cost_metrics,
    request_cache,
    secret_store,
)
from app.services.china_platforms.adapters.douyin import apify_spec
from app.services.china_platforms.errors import ChinaAccessFailure
from app.services.china_platforms.normalized_models import (
    ChinaResolveRequest,
    NormalizedMediaFormat,
    NormalizedMediaResult,
)
from app.services.china_platforms.provider_router import ProviderRouter
from app.services.china_platforms.providers.apify_provider import ApifyProvider
from tests._china_fakes import (  # noqa: F401
    DY_URL, DY_URL2, PUBLIC_CTX, SIGNED, FakeProvider, clean_env, factory, flags_on, rc,
)
from tests.test_admin_download_metrics import admin  # noqa: F401

TOK_A = "apify_api_ORGAsecretTOKEN00000aaaa"
TOK_B = "apify_api_ORGBsecretTOKEN00000bbbb"
TOK_C = "apify_api_ORGCsecretTOKEN00000cccc"
TOK_LEGACY = "apify_api_LEGACYsecretTOKEN000llll"
TOK_ENV = "apify_api_ENVsecretTOKEN000000eeee"
ALL_TOKENS = (TOK_A, TOK_B, TOK_C, TOK_LEGACY, TOK_ENV)
POOL = "/api/v1/admin/china-platforms/apify/pool"
LEGACY = "/api/v1/admin/china-platforms/apify/token"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

E401 = (401, {"error": {"type": "invalid-token", "message": "Authentication token is not valid."}})
E402 = (402, {"error": {"type": "x402-payment-required",
                        "message": "Please provide X402-PAYMENT-SIGNATURE header with the payment."}})
E402_USAGE = (402, {"error": {"type": "not-enough-usage-to-run-paid-actor", "message": "Not enough usage"}})
E403 = (403, {"error": {"type": "insufficient-permissions",
                        "message": "You do not have permission to perform this action."}})
E429 = (429, {"error": {"type": "rate-limit-exceeded",
                        "message": "You have exceeded the rate limit. Please try again later."}})
E503 = (503, {"error": {"type": "internal-error", "message": "x"}})
RUN_OK = {"id": "RUN1", "status": "SUCCEEDED", "defaultDatasetId": "DS1", "usageTotalUsd": 0.00705}
ITEM = {"id": "1", "text": "a video", "authorMeta": {"name": "u"},
        "videoMeta": {"playUrl": SIGNED, "width": 720}}


def _no_secret(text: str):
    for bad in (*ALL_TOKENS, "secretTOKEN", "PROXYPASS"):
        assert bad not in text, bad


@pytest.fixture
def clock(monkeypatch):
    box = {"now": NOW}
    monkeypatch.setattr(budget_guard, "utcnow", lambda: box["now"])
    return box


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr("app.core.alerts.send_admin_alert", lambda level, title, body, *a, **k:
                        sent.append((level, title, body)))
    return sent


class Apify:
    """Fake Apify: per-token answer to the run start; the rest succeeds."""

    def __init__(self, start=None, run=None):
        self.start = dict(start or {})
        self.run = run or RUN_OK
        self.starts: list[str] = []
        self.requests: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        tok = req.headers.get("authorization", "").removeprefix("Bearer ")
        p = req.url.path
        if req.method == "POST" and p.endswith("/runs"):
            self.starts.append(tok)
            ans = self.start.get(tok, "ok")
            if ans == "timeout":
                raise httpx.ReadTimeout("start timed out")
            if ans != "ok":
                return httpx.Response(ans[0], json=ans[1])
            return httpx.Response(201, json={"data": self.run})
        if req.method == "GET" and "/actor-runs/" in p:
            return httpx.Response(200, json={"data": self.run})
        if req.method == "GET" and "/datasets/" in p:
            return httpx.Response(200, json=[ITEM])
        if p.endswith("/users/me"):
            if tok not in ALL_TOKENS:
                return httpx.Response(E401[0], json=E401[1])
            return httpx.Response(200, json={"data": {
                "username": f"org-{tok[-4:]}", "plan": {"id": "SCALE"}, "isPaying": True,
                "proxy": {"password": "PROXYPASS"},
                "effectivePlatformFeatures": {"ACTORS": {"isEnabled": True, "disabledReason": None,
                                                         "disabledReasonType": None}}}})
        if p.endswith("/users/me/limits"):
            return httpx.Response(200, json={"data": {
                "monthlyUsageCycle": {"startAt": "2026-10-01T00:00:00Z", "endAt": "2026-10-31T23:59:59Z"},
                "limits": {"maxMonthlyUsageUsd": 10}, "current": {"monthlyUsageUsd": 1.0}}})
        return httpx.Response(404)


def _info(used=1.0, limit=10.0, actors=True, cycle_end="2026-10-31T23:59:59Z"):
    return {"account": {"username": "org", "plan": "SCALE", "is_paying": True, "actors_enabled": actors},
            "usage": {"monthly_usage_usd": used, "max_monthly_usage_usd": limit,
                      "cycle_start": "2026-10-01T00:00:00Z", "cycle_end": cycle_end}}


def _add(token, label, priority=None, ceiling=None, **info_kw):
    return apify_pool.add(token, _info(**info_kw), label=label, priority=priority, ceiling=ceiling, ip=None)


def _router(api: Apify):
    prov = ApifyProvider(apify_spec(), transport=httpx.MockTransport(api.handler))
    return ProviderRouter(factory(prov)), prov


def _resolve(router, url=DY_URL, ctx=PUBLIC_CTX):
    return asyncio.run(router.resolve(ChinaResolveRequest(url=url), ctx))


# ── migration / mirrors ─────────────────────────────────────────────────────

class TestMigration:

    def test_single_admin_token_becomes_entry_1(self, clean_env, rc, clock):
        secret_store.store(TOK_LEGACY, {"account": {"username": "old-acc", "plan": "FREE"},
                                        "usage": {"monthly_usage_usd": 0.5, "max_monthly_usage_usd": 5}},
                           now_iso="2026-10-06T00:00:00+00:00", ip="1.2.3.4")
        views = apify_pool.list_views()
        assert len(views) == 1
        v = views[0]
        assert v["id"] == "1" and v["legacy_slot"] is True and v["last4"] == "llll" and v["source"] == "admin"
        assert v["account"]["username"] == "old-acc" and v["usage"]["max_monthly_usage_usd"] == 5
        assert v["state"] == "active" and v["eligible"] is True
        assert rc.hget(apify_pool.TOKENS_KEY, "1") == TOK_LEGACY
        # the legacy key stays (a code rollback still finds it)
        assert rc.get(secret_store.TOKEN_KEY) == TOK_LEGACY
        _no_secret(json.dumps(views))
        # idempotent
        assert [x["id"] for x in apify_pool.list_views()] == ["1"]

    def test_env_is_fallback_entry_last(self, clean_env, rc, clock):
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", TOK_ENV)
        _add(TOK_A, "Org A")
        views = apify_pool.list_views()
        assert [v["id"] for v in views] == ["1", "env"]
        env = views[1]
        assert env["source"] == "env" and env["priority"] == apify_pool.ENV_PRIORITY and env["last4"] == "eeee"
        assert rc.hget(apify_pool.TOKENS_KEY, "env") is None      # env token is never copied into Redis
        clean_env.delenv("CHINA_ACCESS_APIFY_TOKEN")
        assert [v["id"] for v in apify_pool.list_views()] == ["1"]

    def test_legacy_token_replaced_gets_new_entry_keeping_settings(self, clean_env, rc, clock):
        secret_store.store(TOK_LEGACY, {"account": {}}, now_iso="t", ip=None)
        apify_pool.update("1", {"label": "Org cũ", "priority": 5, "monthly_ceiling_usd": 3.0})
        secret_store.store(TOK_A, {"account": {}}, now_iso="t", ip=None)
        views = apify_pool.list_views()
        assert len(views) == 1 and views[0]["id"] == "2" and views[0]["last4"] == "aaaa"
        assert views[0]["label"] == "Org cũ" and views[0]["priority"] == 5 and views[0]["monthly_ceiling_usd"] == 3.0
        assert rc.hget(apify_pool.TOKENS_KEY, "1") is None

    def test_deleting_legacy_entry_removes_legacy_key(self, clean_env, rc, clock):
        secret_store.store(TOK_LEGACY, {"account": {}}, now_iso="t", ip=None)
        apify_pool.list_views()
        apify_pool.delete("1")
        assert rc.get(secret_store.TOKEN_KEY) is None and apify_pool.list_views() == []

    def test_redis_down_means_not_configured(self, clean_env, monkeypatch):
        from tests._china_fakes import down_redis
        monkeypatch.setattr("app.core.redis_client._client", down_redis())
        assert apify_pool.has_eligible() is False and apify_pool.pick(7100) is None


# ── rotation ────────────────────────────────────────────────────────────────

class TestRotation:

    def test_priority_then_most_remaining(self, clean_env, rc, clock):
        _add(TOK_A, "A", priority=100, used=8.0, limit=10.0)    # 2.00 left
        _add(TOK_B, "B", priority=100, used=1.0, limit=10.0)    # 9.00 left
        _add(TOK_C, "C", priority=50, used=9.5, limit=10.0)     # 0.50 left but higher priority
        assert apify_pool.pick(7100).entry_id == "3"
        apify_pool.update("3", {"enabled": False})
        assert apify_pool.pick(7100).entry_id == "2"

    def test_our_spend_since_refresh_counts_against_apify_figure(self, clean_env, rc, clock):
        _add(TOK_A, "A", used=1.0, limit=10.0)
        _add(TOK_B, "B", used=2.0, limit=10.0)
        # A has burned $3 (recorded by us) since its refresh → 6.00 left, B 8.00 left
        lease = apify_pool.pick(0, exclude={"2"})
        apify_pool.settle(lease, 3_000_000)
        views = {v["id"]: v for v in apify_pool.list_views()}
        assert views["1"]["apify_usage_now_usd"] == pytest.approx(4.0)
        assert views["1"]["remaining_usd"] == pytest.approx(6.0)
        assert apify_pool.pick(7100).entry_id == "2"

    def test_unknown_remaining_sorts_after_known(self, clean_env, rc, clock):
        apify_pool.add(TOK_A, {"account": {}, "usage": None}, label="A", priority=None, ceiling=None, ip=None)
        _add(TOK_B, "B", used=9.9, limit=10.0)
        assert apify_pool.pick(7100).entry_id == "2"

    def test_lease_repr_hides_token(self, clean_env, rc, clock):
        _add(TOK_A, "A")
        lease = apify_pool.pick(7100)
        assert TOK_A not in repr(lease) and TOK_A not in str(lease)


# ── classification from the documented shapes ─────────────────────────────

class TestClassify:

    @pytest.mark.parametrize("shape,state", [
        (E402, "exhausted"), (E402_USAGE, "exhausted"),
        ((400, {"error": {"type": "platform-feature-disabled", "message": "x"}}), "exhausted"),
        ((402, {}), "exhausted"),
        (E401, "invalid"), (E403, "invalid"),
        ((400, {"error": {"type": "user-disabled"}}), "invalid"),
        (E429, "cooldown"), (E503, "cooldown"),
        ((400, {"error": {"type": "concurrent-runs-limit-exceeded"}}), "cooldown"),
        ((400, {"error": {"type": "invalid-input"}}), None),
        ((404, {"error": {"type": "record-not-found"}}), None),
    ])
    def test_shapes(self, shape, state):
        status, body = shape
        assert apify_pool.classify_start_error(status, json.dumps(body))[0] == state

    def test_non_json_body(self):
        assert apify_pool.classify_start_error(402, "<html>")[0] == "exhausted"
        assert apify_pool.classify_start_error(500, "")[0] == "cooldown"


# ── provider + router: retry only when no run started ──────────────────────

class TestRetryOnNextToken:

    def test_exhausted_first_token_retries_same_video_on_next(self, flags_on, rc, clock, alerts):
        _add(TOK_A, "A", priority=1)
        _add(TOK_B, "B", priority=2)
        api = Apify(start={TOK_A: E402})
        router, prov = _router(api)
        res = _resolve(router)
        assert res.provider_name == "apify_douyin"
        assert api.starts == [TOK_A, TOK_B]            # refused, then ONE billed run
        views = {v["id"]: v for v in apify_pool.list_views()}
        assert views["1"]["state"] == "exhausted" and views["1"]["exhausted_until"].startswith("2026-10-31")
        assert views["1"]["spend_month_usd"] == 0 and views["1"]["calls_today"] == 0   # refund, no call
        assert views["2"]["spend_month_usd"] == pytest.approx(0.00705) and views["2"]["calls_today"] == 1
        a = router.attempts[-1]
        assert a["apify_entry"] == "2" and a["apify_refusals"] == ["1:exhausted"]
        assert budget_guard.snapshot(["douyin"], ["apify"])["providers"]["apify"]["calls_today"] == 1
        assert len([x for x in alerts if "hết tiền" in x[1]]) == 1
        _no_secret(json.dumps(router.attempts) + json.dumps(alerts))

    def test_invalid_first_token_retries(self, flags_on, rc, clock, alerts):
        _add(TOK_A, "A", priority=1)
        _add(TOK_B, "B", priority=2)
        api = Apify(start={TOK_A: E401})
        _resolve(_router(api)[0])
        assert api.starts == [TOK_A, TOK_B]
        assert {v["id"]: v["state"] for v in apify_pool.list_views()} == {"1": "invalid", "2": "active"}

    def test_retry_at_most_once(self, flags_on, rc, clock, alerts):
        for i, t in enumerate((TOK_A, TOK_B, TOK_C)):
            _add(t, f"O{i}", priority=i)
        api = Apify(start={TOK_A: E402, TOK_B: E402_USAGE})
        with pytest.raises(ChinaAccessFailure):
            _resolve(_router(api)[0])
        assert api.starts == [TOK_A, TOK_B]            # never a third account for one request
        # the reservation was refunded: no run started
        snap = budget_guard.snapshot(["douyin"], ["apify"])
        assert snap["providers"]["apify"]["spend_today_usd"] == 0

    def test_no_retry_after_run_started(self, flags_on, rc, clock):
        _add(TOK_A, "A", priority=1)
        _add(TOK_B, "B", priority=2)
        api = Apify(run={"id": "R", "status": "FAILED", "defaultDatasetId": "D", "usageTotalUsd": 0.001})
        with pytest.raises(ChinaAccessFailure):
            _resolve(_router(api)[0])
        assert api.starts == [TOK_A]
        v = {x["id"]: x for x in apify_pool.list_views()}
        assert v["1"]["calls_today"] == 1 and v["1"]["spend_month_usd"] == pytest.approx(0.001)
        assert v["2"]["calls_today"] == 0

    def test_no_retry_on_start_timeout(self, flags_on, rc, clock):
        _add(TOK_A, "A", priority=1)
        _add(TOK_B, "B", priority=2)
        api = Apify(start={TOK_A: "timeout"})
        with pytest.raises(ChinaAccessFailure):
            _resolve(_router(api)[0])
        assert api.starts == [TOK_A]
        v = {x["id"]: x for x in apify_pool.list_views()}
        # may have started: the estimate stays on the entry, like on the router budget
        assert v["1"]["spend_month_usd"] == pytest.approx(0.0071) and v["1"]["state"] == "active"

    @pytest.mark.parametrize("shape", [E429, E503])
    def test_cooldown_no_retry_then_recovers(self, flags_on, rc, clock, shape):
        _add(TOK_A, "A", priority=1)
        _add(TOK_B, "B", priority=2)
        api = Apify(start={TOK_A: shape})
        with pytest.raises(ChinaAccessFailure):
            _resolve(_router(api)[0])
        assert api.starts == [TOK_A]
        v = {x["id"]: x for x in apify_pool.list_views()}["1"]
        assert v["state"] == "cooldown" and v["eligible"] is False
        assert apify_pool.pick(7100).entry_id == "2"
        clock["now"] = NOW + timedelta(seconds=301)
        assert {x["id"]: x for x in apify_pool.list_views()}["1"]["state"] == "active"

    def test_retry_switch_off(self, flags_on, rc, clock):
        flags_on.setenv("CHINA_ACCESS_APIFY_POOL_RETRY_NEXT_TOKEN", "false")
        _add(TOK_A, "A", priority=1)
        _add(TOK_B, "B", priority=2)
        api = Apify(start={TOK_A: E402})
        with pytest.raises(ChinaAccessFailure):
            _resolve(_router(api)[0])
        assert api.starts == [TOK_A]

    def test_no_eligible_entry_skips_paid_route_and_alerts_once(self, flags_on, rc, clock, alerts):
        _add(TOK_A, "A")
        apify_pool.mark("1", "invalid", "test")
        api = Apify()
        for url in (DY_URL, DY_URL2):
            with pytest.raises(ChinaAccessFailure):
                _resolve(_router(api)[0], url=url)
        assert api.starts == []
        assert len([a for a in alerts if "không còn token" in a[1]]) == 1   # from the router's check
        apify_pool.pick(7100)
        assert len([a for a in alerts if "không còn token" in a[1]]) == 1
        # an eligible entry re-arms the alert
        _add(TOK_B, "B")
        assert apify_pool.pick(7100).entry_id == "2"
        apify_pool.mark("2", "invalid", "test")
        assert apify_pool.has_eligible() is False
        assert len([a for a in alerts if "không còn token" in a[1]]) == 2

    def test_empty_pool_does_not_alert(self, flags_on, rc, clock, alerts):
        assert apify_pool.has_eligible() is False and alerts == []


# ── ceilings, recovery, alerts ──────────────────────────────────────────────

class TestCeilingRecoveryAlerts:

    def test_per_entry_ceiling(self, clean_env, rc, clock, alerts):
        _add(TOK_A, "A", priority=1, ceiling=0.01)
        _add(TOK_B, "B", priority=2)
        l1 = apify_pool.pick(7100)
        assert l1.entry_id == "1"
        apify_pool.settle(l1, 7100)
        l2 = apify_pool.pick(7100)                 # 0.0142 > 0.01 → next entry
        assert l2.entry_id == "2"
        v = {x["id"]: x for x in apify_pool.list_views()}["1"]
        assert v["spend_month_usd"] == pytest.approx(0.0071)       # rolled back, not double counted
        assert v["eligible"] is True                                # 0.0071 < 0.01
        apify_pool.pick(7100)
        assert len([a for a in alerts if "trần" in a[1]]) == 1

    def test_ceiling_reached_marks_ineligible(self, clean_env, rc, clock):
        _add(TOK_A, "A", ceiling=0.01)
        apify_pool.settle(apify_pool.pick(7100), 10_000)
        v = apify_pool.list_views()[0]
        assert v["state"] == "active" and v["eligible"] is False and v["ineligible_reason"] == "entry_ceiling"
        assert v["remaining_usd"] == pytest.approx(0.0)

    def test_monthly_auto_recover_at_cycle_end(self, clean_env, rc, clock, alerts):
        _add(TOK_A, "A")
        apify_pool.mark("1", "exhausted", "http_402:x402-payment-required")
        assert apify_pool.pick(7100) is None
        clock["now"] = datetime(2026, 11, 1, 0, 0, 5, tzinfo=timezone.utc)   # after cycle end 10-31 23:59:59
        lease = apify_pool.pick(7100)
        assert lease is not None and lease.entry_id == "1"
        v = apify_pool.list_views()[0]
        assert v["state"] == "active" and v["state_reason"] == "auto_recovered_from_exhausted"

    def test_exhausted_without_cycle_recovers_next_utc_month(self, clean_env, rc, clock):
        apify_pool.add(TOK_A, {"account": {}, "usage": None}, label="A", priority=None, ceiling=None, ip=None)
        apify_pool.mark("1", "exhausted", "http_402")
        assert apify_pool.list_views()[0]["exhausted_until"].startswith("2026-11-01T00:00:00")

    def test_refresh_with_credit_recovers_and_at_limit_exhausts(self, clean_env, rc, clock, alerts):
        _add(TOK_A, "A")
        apify_pool.mark("1", "exhausted", "http_402")
        assert apify_pool.apply_refresh("1", _info(used=2.0, limit=10.0))["state"] == "active"
        v = apify_pool.apply_refresh("1", _info(used=10.0, limit=10.0))
        assert v["state"] == "exhausted" and v["state_reason"] == "usage_at_limit"
        v = apify_pool.apply_refresh("1", _info(used=3.0, actors=False))
        assert v["state"] == "exhausted"
        v = apify_pool.apply_refresh("1", None, rejected_code="invalid_token")
        assert v["state"] == "invalid"
        assert apify_pool.apply_refresh("1", None, rejected_code="apify_unreachable")["state"] == "invalid"

    def test_state_alerts_once_per_transition(self, clean_env, rc, clock, alerts):
        _add(TOK_A, "Org A")
        apify_pool.mark("1", "exhausted", "http_402")
        apify_pool.mark("1", "exhausted", "http_402")
        apify_pool.mark("1", "invalid", "http_401")
        apify_pool.mark("1", "invalid", "http_401")
        titles = [a[1] for a in alerts]
        assert sum("hết tiền" in t for t in titles) == 1 and sum("không dùng được" in t for t in titles) == 1
        assert all("Org A" in a[2] and "••••aaaa" in a[2] for a in alerts)
        _no_secret(json.dumps(alerts))

    def test_pool_low_alert_once(self, clean_env, rc, clock, alerts):
        _add(TOK_A, "A", used=8.5, limit=10.0)    # 1.5 of 10 left = 15 % < 20 %
        assert apify_pool.check_pool_low() is True
        assert apify_pool.check_pool_low() is False
        assert len([a for a in alerts if "sắp hết" in a[1]]) == 1

    def test_pool_not_low(self, clean_env, rc, clock, alerts):
        _add(TOK_A, "A", used=1.0, limit=10.0)
        assert apify_pool.check_pool_low() is False and alerts == []


# ── metrics ─────────────────────────────────────────────────────────────────

class TestMetrics:

    def test_rollups_and_projection(self, clean_env, rc, clock):
        _add(TOK_A, "A", used=1.0, limit=10.0)
        for days_ago in range(7):
            clock["now"] = NOW - timedelta(days=days_ago)
            apify_pool.settle(apify_pool.pick(0), 100_000)           # $0.10/day
            cost_metrics.record_paid("douyin", success=True, usable=None, recorded_micros=100_000)
        cost_metrics.record_paid("douyin", success=False, usable=False, recorded_micros=7_100)
        clock["now"] = NOW
        cost_metrics.record_paid("kuaishou", success=True, usable=True, recorded_micros=4_050)
        v = apify_pool.list_views()[0]
        assert v["spend_7d_usd"] == pytest.approx(0.7) and v["burn_per_day_usd"] == pytest.approx(0.1)
        # Apify figure 1.00 + 0.70 recorded since refresh → 8.30 left → 83 days at $0.10/day
        assert v["remaining_usd"] == pytest.approx(8.3) and v["projected_days_left"] == pytest.approx(83.0)
        rep = cost_metrics.platform_report(NOW)
        d = rep["platforms"]["douyin"]
        assert d["today"]["managed_calls"] == 1 and d["7d"]["managed_calls"] == 8
        assert d["7d"]["managed_success"] == 7 and d["7d"]["success_rate"] == pytest.approx(0.875)
        assert d["7d"]["spend_usd"] == pytest.approx(0.7071)
        assert d["7d"]["cost_per_success_usd"] == pytest.approx(0.7071 / 7, rel=1e-4)
        assert d["7d"]["usable_known"] == 1 and d["7d"]["usable_rate"] == 0.0
        assert rep["platforms"]["kuaishou"]["today"]["usable_rate"] == 1.0
        assert rep["totals"]["month"]["managed_calls"] == 9
        assert d["month"]["managed_calls"] == 8    # all 7 days are in October
        assert rc.ttl(cost_metrics.key(budget_guard.day_key(NOW), "douyin")) > 39 * 86400

    def test_summary_totals(self, clean_env, rc, clock):
        _add(TOK_A, "A", used=1.0, limit=10.0)
        _add(TOK_B, "B", used=5.0, limit=10.0, ceiling=2.0)
        s = apify_pool.summary()
        assert s["entries"] == 2 and s["eligible"] == 2
        assert s["capacity_usd"] == pytest.approx(12.0)              # 10 + min(10, 2)
        assert s["remaining_usd"] == pytest.approx(9.0 + 2.0)


# ── cache: hit counting + expiry ────────────────────────────────────────────

def _result(url_media: str) -> NormalizedMediaResult:
    return NormalizedMediaResult(
        platform="douyin", canonical_url=DY_URL, title="t", provider_name="apify_douyin", provider_mode="managed",
        formats=[NormalizedMediaFormat(format_id="v", ext="mp4", label="video", has_video=True,
                                       has_audio=True, source_url=url_media)])


class TestCache:

    def test_cache_hit_counted_with_saved_cost(self, flags_on, rc, clock):
        _add(TOK_A, "A")
        api = Apify()
        _resolve(_router(api)[0])
        hit = _resolve(_router(api)[0])
        assert hit.cache_hit is True and len(api.starts) == 1
        t = cost_metrics.platform_report(NOW)["platforms"]["douyin"]["today"]
        assert t["cache_hits"] == 1 and t["cache_hits_managed"] == 1
        assert t["saved_usd"] == pytest.approx(0.0071) and t["managed_calls"] == 1

    def test_native_hit_saves_nothing(self, flags_on, rc, clock):
        nat = FakeProvider("native_douyin")
        r = ProviderRouter(factory(nat))
        _resolve(r)
        _resolve(ProviderRouter(factory(nat)))
        t = cost_metrics.platform_report(NOW)["platforms"]["douyin"]["today"]
        assert t["cache_hits"] == 1 and t["cache_hits_managed"] == 0 and t["saved_usd"] == 0

    def test_ttl_never_past_media_expiry(self, clean_env, rc, clock):
        exp = int(NOW.timestamp()) + 1200
        res = _result(f"https://cdn.example/v.mp4?x-expires={exp}&x-signature=S")
        assert request_cache.cache_ttl_for(res, 1800) == 600                  # 1200 - 600 margin
        res_unknown = _result("https://cdn.example/v.mp4")
        assert request_cache.cache_ttl_for(res_unknown, 1800) == 1800

    def test_extended_ttl_only_with_known_expiry(self, clean_env, rc, clock):
        clean_env.setenv("CHINA_ACCESS_CACHE_EXTENDED_TTL_SEC", "21600")
        far = int(NOW.timestamp()) + 86400
        assert request_cache.cache_ttl_for(_result(f"https://c/v.mp4?x-expires={far}"), 1800) == 21600
        near = int(NOW.timestamp()) + 3600
        assert request_cache.cache_ttl_for(_result(f"https://c/v.mp4?x-expires={near}"), 1800) == 3000
        assert request_cache.cache_ttl_for(_result("https://c/v.mp4"), 1800) == 1800
        assert request_cache.cache_ttl_for(_result(f"https://c/v.mp4?x-expires={far}"), 0) == 0

    def test_expired_cached_result_is_not_served(self, clean_env, rc, clock):
        exp = int(NOW.timestamp()) + 1000
        res = _result(f"https://cdn.example/v.mp4?x-expires={exp}")
        request_cache.set_cached(res, "single_media", "h1", 1800)
        assert request_cache.get_cached("douyin", "single_media", "h1") is not None
        clock["now"] = NOW + timedelta(seconds=500)        # 500 s left < 600 s margin
        assert request_cache.get_cached("douyin", "single_media", "h1") is None
        assert rc.get(request_cache.cache_key("douyin", "single_media", "h1")) is None

    def test_free_route_first_by_default(self, clean_env):
        from app.services.china_platforms import registry
        for p in ("douyin", "xiaohongshu"):
            order = registry.effective_order(registry.get_policy(p), "single_media")
            assert order[0].startswith("native_"), order


# ── admin endpoints ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_apify(monkeypatch):
    import app.api.admin_china_platforms as mod
    api = Apify()
    t = httpx.MockTransport(api.handler)

    async def validator(token):
        return await secret_store.validate(token, transport=t)
    monkeypatch.setattr(mod, "_apify_validator", lambda: validator)
    return api


@pytest.fixture
def audit(monkeypatch):
    import app.api.admin_china_platforms as mod
    rows = []
    monkeypatch.setattr(mod, "log_admin_action", lambda req, action, **kw: rows.append((action, kw)))
    return rows


class TestEndpoints:

    def test_requires_admin(self, app, rc):
        assert app.get(POOL).status_code == 401
        assert app.post(POOL, json={"token": TOK_A, "label": "A", "reason": "abc"}).status_code == 401
        assert app.get("/api/v1/admin/china-platforms/apify/metrics").status_code == 401

    def test_full_cycle_never_echoes_token(self, app, admin, clean_env, rc, clock, mock_apify, audit, alerts,
                                           caplog):
        caplog.set_level(logging.DEBUG)
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", TOK_ENV)
        r = app.post(POOL, json={"token": f" {TOK_A} ", "label": "Tổ chức A", "priority": 10,
                                 "monthly_ceiling_usd": "4.5", "reason": "org A"})
        assert r.status_code == 200, r.text
        e = r.json()["entry"]
        assert e["label"] == "Tổ chức A" and e["last4"] == "aaaa" and e["priority"] == 10
        assert e["monthly_ceiling_usd"] == 4.5 and e["account"]["username"] == "org-aaaa"
        assert e["usage"]["max_monthly_usage_usd"] == 10 and e["state"] == "active"
        r2 = app.post(POOL, json={"token": TOK_B, "label": "Tổ chức B", "reason": "org B"})
        assert r2.status_code == 200
        dup = app.post(POOL, json={"token": TOK_A, "label": "again", "reason": "dup"})
        assert dup.status_code == 409
        lst = app.get(POOL)
        assert [x["id"] for x in lst.json()["entries"]] == ["1", "2", "env"]
        assert lst.json()["summary"]["entries"] == 3
        up = app.post(f"{POOL}/2", json={"enabled": False, "priority": 3, "reason": "pause B"})
        assert up.status_code == 200 and up.json()["entry"]["state"] == "disabled"
        assert app.post(f"{POOL}/2", json={"token": TOK_C, "reason": "swap"}).status_code == 400
        rf = app.post(f"{POOL}/1/refresh")
        assert rf.status_code == 200 and rf.json()["outcome"] == "ok"
        rall = app.post(f"{POOL}/refresh")
        assert rall.status_code == 200 and rall.json()["results"] == {"1": "ok", "2": "ok", "env": "ok"}
        m = app.get("/api/v1/admin/china-platforms/apify/metrics")
        assert m.status_code == 200 and set(m.json()["platforms"]) == {"douyin", "kuaishou", "xiaohongshu"}
        assert m.json()["pool"]["entries"] == 3
        assert app.request("DELETE", f"{POOL}/env", json={"reason": "no"}).status_code == 400
        d = app.request("DELETE", f"{POOL}/2", json={"reason": "gone"})
        assert d.status_code == 200 and d.json()["removed"] == {"id": "2", "label": "Tổ chức B", "last4": "bbbb"}
        assert app.request("DELETE", f"{POOL}/2", json={"reason": "gone"}).status_code == 404
        for resp in (r, r2, dup, lst, up, rf, rall, m, d, app.get(POOL), app.get(LEGACY)):
            _no_secret(resp.text)
        _no_secret(json.dumps(audit, default=str))
        _no_secret(caplog.text)
        acts = [a for a, _ in audit]
        assert acts.count("admin.china_access.apify_pool_added") == 2
        assert "admin.china_access.apify_pool_updated" in acts and "admin.china_access.apify_pool_deleted" in acts
        added = audit[0][1]["metadata"]
        assert added["new"] == {"id": "1", "label": "Tổ chức A", "last4": "aaaa"}

    def test_bad_bodies_never_echo_token(self, app, admin, clean_env, rc, clock, mock_apify, audit):
        r = app.post(POOL, json={"token": TOK_A, "label": "A"})             # no reason
        assert r.status_code == 400 and TOK_A not in r.text
        r = app.post(POOL, json={"token": TOK_A, "reason": "abc"})           # no label
        assert r.status_code == 400 and TOK_A not in r.text
        r = app.post(POOL, json={"token": TOK_A, "label": "A", "priority": "x", "reason": "abc"})
        assert r.status_code == 400 and TOK_A not in r.text
        r = app.post(POOL, json={"token": TOK_A, "label": "A", "monthly_ceiling_usd": -1, "reason": "abc"})
        assert r.status_code == 400 and TOK_A not in r.text
        r = app.post(POOL, json={"token": "apify_api_WRONGwrongWRONG0000qqqq", "label": "A", "reason": "abc"})
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_token"
        assert "WRONGwrong" not in r.text and "WRONGwrong" not in json.dumps(audit, default=str)
        r = app.post(POOL, json={"token": TOK_A, "label": "A", "reason": f"pasted {TOK_A}"})
        assert r.status_code == 200
        _no_secret(json.dumps(audit, default=str))
        assert app.post(POOL, content=b"nope", headers={"content-type": "application/json"}).status_code == 400
        assert app.request("DELETE", f"{POOL}/1", json={}).status_code == 400

    def test_back_compat_token_endpoints_drive_pool(self, app, admin, clean_env, rc, clock, mock_apify, audit):
        r = app.post(LEGACY, json={"token": TOK_LEGACY, "reason": "old flow"})
        assert r.status_code == 200
        entries = app.get(POOL).json()["entries"]
        assert len(entries) == 1 and entries[0]["legacy_slot"] and entries[0]["last4"] == "llll"
        g = app.get(LEGACY).json()
        assert g["source"] == "admin" and g["pool"]["entries"] == 1 and "deprecated" in g
        t = app.post(LEGACY + "/test")
        assert t.status_code == 200
        assert app.get(POOL).json()["entries"][0]["account"]["username"] == "org-llll"
        d = app.request("DELETE", LEGACY, json={"reason": "rotate"})
        assert d.status_code == 200 and app.get(POOL).json()["entries"] == []
        ov = app.get("/api/v1/admin/china-platforms").json()
        assert ov["apify_configured"] is False
