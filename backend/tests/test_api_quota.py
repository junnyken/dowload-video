"""Monthly API call quota (owner 14/10)."""
import asyncio
from datetime import datetime, timezone

import fakeredis
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core import api_quota, plans
from app.core import auth_middleware as am


@pytest.fixture()
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(api_quota, "_r", lambda: r)
    monkeypatch.setattr(plans, "_r", lambda: r)
    return r


NOW = datetime(2026, 10, 14, 5, 0, tzinfo=timezone.utc)


def test_defaults(rc):
    assert api_quota.limit_for_tier("pro") == 3000
    assert api_quota.limit_for_tier("team") == 10000
    assert api_quota.limit_for_tier("enterprise") == -1
    assert api_quota.limit_for_tier("free") == 0


def test_admin_setting_changes_limit(rc):
    plans.set_settings({"api_calls_month_pro": 5})
    assert api_quota.limit_for_tier("pro") == 5


def test_consume_until_limit_then_refuse(rc):
    plans.set_settings({"api_calls_month_pro": 3})
    got = [api_quota.consume("u1", "pro", NOW)["allowed"] for _ in range(4)]
    assert got == [True, True, True, False]
    u = api_quota.usage("u1", "pro", NOW)
    assert u["used"] == 3 and u["remaining"] == 0     # refused call not counted


def test_counter_is_per_month(rc):
    plans.set_settings({"api_calls_month_pro": 1})
    assert api_quota.consume("u1", "pro", NOW)["allowed"]
    assert not api_quota.consume("u1", "pro", NOW)["allowed"]
    nov = datetime(2026, 11, 1, 0, 0, 1, tzinfo=timezone.utc)
    assert api_quota.consume("u1", "pro", nov)["allowed"]


def test_reset_at_next_month():
    assert api_quota.reset_at(NOW).isoformat() == "2026-11-01T00:00:00+00:00"
    assert api_quota.reset_at(datetime(2026, 12, 31, tzinfo=timezone.utc)).isoformat() == "2027-01-01T00:00:00+00:00"


def test_enterprise_unlimited(rc):
    for _ in range(5):
        assert api_quota.consume("u2", "enterprise", NOW)["allowed"]


def test_fails_open_without_redis(monkeypatch):
    def boom():
        raise ConnectionError("down")
    monkeypatch.setattr(api_quota, "_r", boom)
    monkeypatch.setattr(plans, "_r", boom)
    assert api_quota.consume("u1", "pro", NOW)["allowed"] is True


def test_headers():
    h = api_quota.headers({"limit": 3000, "remaining": 2999, "reset_at": "2026-11-01T00:00:00+00:00"})
    assert h["X-API-Quota-Limit"] == "3000" and h["X-API-Quota-Remaining"] == "2999"
    assert h["X-API-Quota-Reset"] == str(int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()))
    assert api_quota.headers({"limit": -1, "remaining": None, "reset_at": "2026-11-01T00:00:00+00:00"})["X-API-Quota-Limit"] == "unlimited"


# ── through the auth dependency ────────────────────────────────────────────

GOOD = {"id": "u1", "email": "", "tier": "pro", "token": "x", "via_api_key": True}


class _Req:
    class state:
        pass


def _call(bearer=None, xkey=None, req=None):
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials=bearer) if bearer else None
    return asyncio.new_event_loop().run_until_complete(
        am.get_optional_user(req, credentials=cred, x_api_key=xkey))


@pytest.fixture()
def keys(monkeypatch, rc):
    import app.core.quotas as q
    monkeypatch.setattr(am, "_lookup_new_api_key", lambda k: dict(GOOD) if k == "vidgrab_good" else None)
    monkeypatch.setattr(am, "_lookup_api_key", lambda t: dict(GOOD) if t == "vg_good" else None)
    monkeypatch.setattr(q, "_get_tier", lambda uid: "pro")

    class _SB:
        class auth:
            @staticmethod
            def get_user(token):
                raise RuntimeError("no")
    monkeypatch.setattr(am, "get_supabase_client", lambda: _SB)


@pytest.mark.parametrize("kw", [{"xkey": "vidgrab_good"}, {"bearer": "vidgrab_good"}, {"bearer": "vg_good"}])
def test_every_key_path_is_metered(keys, kw):
    plans.set_settings({"api_calls_month_pro": 2})
    req = _Req()
    u = _call(req=req, **kw)
    assert u["api_quota"]["used"] == 1 and req.state.api_quota["remaining"] == 1
    _call(**kw)
    with pytest.raises(HTTPException) as e:
        _call(**kw)
    assert e.value.status_code == 429
    assert e.value.detail["error_code"] == "api_monthly_quota_exceeded"
    assert e.value.headers["X-API-Quota-Remaining"] == "0"


def test_session_traffic_not_metered(keys, rc):
    assert _call() is None
    assert not rc.keys("api_calls:*")


def test_plans_table_shows_monthly_api_calls(rc):
    t = {x["id"]: x for x in plans.build_plans()["tiers"]}
    assert t["pro"]["api_calls_per_month"] == 3000
    assert t["free"]["api_calls_per_month"] == 0 and t["guest"]["api_calls_per_month"] == 0


def test_bad_setting_rejected(rc):
    with pytest.raises(ValueError):
        plans.set_settings({"api_calls_month_pro": -5})


def test_headers_middleware_adds_quota_headers():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    async def ep(request):
        request.state.api_quota = {"limit": 3000, "remaining": 10, "reset_at": "2026-11-01T00:00:00+00:00"}
        return PlainTextResponse("ok")

    async def plain(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/k", ep), Route("/p", plain)])
    app.add_middleware(api_quota.ApiQuotaHeadersMiddleware)
    c = TestClient(app)
    r = c.get("/k")
    assert r.headers["x-api-quota-remaining"] == "10" and r.headers["x-api-quota-limit"] == "3000"
    assert "x-api-quota-limit" not in c.get("/p").headers
