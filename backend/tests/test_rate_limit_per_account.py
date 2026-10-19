"""Signed-in users are limited per ACCOUNT, not per office IP (19/10): a
session token the auth layer verified gets its own limiter bucket and skips
the per-IP daily quota. Partner keys show requests/minute usage to the admin."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

OFFICE_IP = "198.51.100.88"


def _req(headers=None, ip=OFFICE_IP):
    r = MagicMock()
    r.headers = Headers({"x-forwarded-for": ip, **(headers or {})})
    r.client = SimpleNamespace(host=ip)
    return r


@pytest.fixture()
def fr(monkeypatch):
    import app.core.redis_client as rc
    r = fakeredis.FakeRedis()
    monkeypatch.setattr(rc, "get_redis", lambda: r)
    return r


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


# ── rate_limit_key / has_verified_identity ───────────────────────────────────

def test_unverified_session_stays_on_the_ip(fr):
    from app.core.client_ip import has_verified_identity, rate_limit_key
    req = _req(_bearer("eyJ.unverified.jwt"))
    assert rate_limit_key(req) == OFFICE_IP
    assert not has_verified_identity(req)


def test_verified_session_gets_its_account_bucket(fr):
    from app.core.bad_key_guard import note_valid_session
    from app.core.client_ip import has_verified_identity, rate_limit_key
    note_valid_session("eyJ.alice.jwt", "user-alice")
    req = _req(_bearer("eyJ.alice.jwt"))
    assert rate_limit_key(req) == "user:user-alice"
    assert has_verified_identity(req)


def test_api_key_prefixed_bearer_is_not_a_session(fr):
    from app.core.bad_key_guard import note_valid_session
    from app.core.client_ip import rate_limit_key
    # even if something wrote it to the session cache, a key-shaped bearer is
    # never read as a session
    for tok in ("vgp_abc", "vidgrab_abc", "vg_abc"):
        note_valid_session(tok, "user-x")
        assert rate_limit_key(_req(_bearer(tok))) == OFFICE_IP


def test_redis_down_falls_back_to_ip(monkeypatch):
    import app.core.redis_client as rc
    from app.core.client_ip import has_verified_identity, rate_limit_key

    def boom():
        raise RuntimeError("redis down")
    monkeypatch.setattr(rc, "get_redis", boom)
    req = _req(_bearer("eyJ.alice.jwt"))
    assert rate_limit_key(req) == OFFICE_IP
    assert not has_verified_identity(req)


def test_session_cache_expires(fr):
    from app.core.bad_key_guard import VALID_SESSION, VALID_TTL, key_hash, note_valid_session
    note_valid_session("eyJ.t.jwt", "u1")
    assert 0 < fr.ttl(VALID_SESSION.format(h=key_hash("eyJ.t.jwt"))) <= VALID_TTL


def test_two_colleagues_behind_one_office_ip_have_separate_buckets(fr):
    from fastapi import APIRouter, FastAPI, Request
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from app.core.bad_key_guard import note_valid_session
    from app.core.client_ip import rate_limit_key

    lim = Limiter(key_func=rate_limit_key, storage_uri="memory://")
    r = APIRouter()

    @r.post("/heavy")
    @lim.limit("3/minute")
    async def heavy(request: Request):
        return {"ok": True}

    a = FastAPI()
    a.state.limiter = lim
    a.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    a.include_router(r)
    c = TestClient(a)

    def go(headers=None):
        return c.post("/heavy", headers={"x-forwarded-for": OFFICE_IP, **(headers or {})}).status_code

    note_valid_session("eyJ.alice.jwt", "alice")
    note_valid_session("eyJ.bob.jwt", "bob")
    assert [go(_bearer("eyJ.alice.jwt")) for _ in range(4)] == [200, 200, 200, 429]
    assert [go(_bearer("eyJ.bob.jwt")) for _ in range(3)] == [200, 200, 200]   # same IP, own bucket
    assert [go() for _ in range(3)] == [200, 200, 200]                          # guests: the IP bucket
    assert go() == 429
    assert go(_bearer("eyJ.forged.jwt")) == 429                                  # unverified → the IP


def test_daily_ip_quota_skips_verified_sessions():
    import inspect

    import app.main as m
    src = inspect.getsource(m.DailyIPQuotaMiddleware)
    assert "has_verified_identity(request)" in src


# ── Partner key requests/minute stats ────────────────────────────────────────

def test_rpm_stats_record_peak_and_refusals(fr, monkeypatch):
    import app.core.partner_auth as pa
    t0 = 1_800_000_000.0                        # fixed clock, minute boundary irrelevant
    monkeypatch.setattr("time.time", lambda: t0)
    results = [pa._per_minute_exceeded("k1", 5) for _ in range(7)]
    assert results[:5] == [None] * 5 and all(x for x in results[5:])
    s = pa.rpm_stats("k1", now=t0)
    assert s == {"now": 7, "peak_24h": 7, "refused_24h": 2}
    assert pa.rpm_stats("other", now=t0) == {"now": 0, "peak_24h": 0, "refused_24h": 0}


def test_rpm_peak_keeps_the_busiest_minute_over_hours(fr, monkeypatch):
    import app.core.partner_auth as pa
    clock = {"t": 1_800_000_000.0}
    monkeypatch.setattr("time.time", lambda: clock["t"])
    for _ in range(9):
        pa._per_minute_exceeded("k2", 100)
    clock["t"] += 3 * 3600                      # three hours later, a quiet minute
    for _ in range(2):
        pa._per_minute_exceeded("k2", 100)
    s = pa.rpm_stats("k2", now=clock["t"])
    assert s == {"now": 2, "peak_24h": 9, "refused_24h": 0}
    clock["t"] += 25 * 3600                     # out of the 24 h window
    assert pa.rpm_stats("k2", now=clock["t"])["peak_24h"] == 0


def test_rpm_stats_never_raises(monkeypatch):
    import app.core.partner_auth as pa
    import app.core.redis_client as rc

    def boom():
        raise RuntimeError("redis down")
    monkeypatch.setattr(rc, "get_redis", boom)
    assert pa.rpm_stats("k1") == {"now": 0, "peak_24h": 0, "refused_24h": 0}


def test_admin_key_list_includes_rpm(fr, monkeypatch):
    import asyncio

    import app.api.admin as admin
    rows = [{"id": "k1", "is_active": True, "requests_today": 0, "requests_this_month": 0}]
    q = MagicMock()
    for m in ("select", "eq", "order", "limit"):
        getattr(q, m).return_value = q
    q.execute.return_value = SimpleNamespace(data=rows)
    db = MagicMock()
    db.table.return_value = q
    monkeypatch.setattr(admin, "get_supabase_client", lambda: db)
    fr.set("partner:rpm:k1:0", 1)               # unrelated old window: ignored
    out = asyncio.run(admin.list_enterprise_api_keys(tenant_id=None, active_only=False, limit=10, _=None))
    assert out["api_keys"][0]["rpm"] == {"now": 0, "peak_24h": 0, "refused_24h": 0}
