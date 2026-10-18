"""Partner API onboarding (18/10): keyed requests are rate-limited per key,
the Partner API is out of the per-IP limiter, jobs/day + jobs/month are
enforced atomically, suspended customers are refused, and the admin can
create a partner customer and issue / rotate / edit its keys."""
import asyncio
import collections
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers


def _req(headers=None, ip="203.0.113.50"):
    r = MagicMock()
    r.headers = Headers({"x-forwarded-for": ip, **(headers or {})})  # case-insensitive, like Starlette
    r.client = SimpleNamespace(host=ip)
    return r


@pytest.fixture()
def fr(monkeypatch):
    import app.core.redis_client as rc
    r = fakeredis.FakeRedis()
    monkeypatch.setattr(rc, "get_redis", lambda: r)
    return r


# ── rate_limit_key ───────────────────────────────────────────────────────────

def test_no_key_is_ip_bucket(fr):
    from app.core.client_ip import rate_limit_key
    assert rate_limit_key(_req()) == "203.0.113.50"


@pytest.mark.parametrize("headers", [
    {"X-API-Key": "vidgrab_abc"},
    {"Authorization": "Bearer vidgrab_abc"},
    {"Authorization": "Bearer vg_abc"},
    {"X-API-Key": "vgp_abc"},
])
def test_keyed_request_is_key_bucket(fr, headers):
    from app.core.client_ip import rate_limit_key
    k = rate_limit_key(_req(headers))
    assert k.startswith("apikey:") and "abc" not in k   # hashed, never the raw key


def test_web_session_jwt_stays_ip_bucket(fr):
    from app.core.client_ip import rate_limit_key
    assert rate_limit_key(_req({"Authorization": "Bearer eyJhbGciOi.x.y"})) == "203.0.113.50"


def test_ip_spraying_bad_keys_falls_back_to_ip_bucket(fr):
    from app.core import bad_key_guard
    from app.core.client_ip import rate_limit_key
    req = _req({"X-API-Key": "vidgrab_random"})
    for _ in range(bad_key_guard.BAD_KEY_LIMIT):
        bad_key_guard.note_bad_key(req)
    assert rate_limit_key(req) == "203.0.113.50"
    # another IP is unaffected
    assert rate_limit_key(_req({"X-API-Key": "vidgrab_random"}, ip="198.51.100.9")).startswith("apikey:")


def test_bad_key_guard_fails_open(monkeypatch):
    import app.core.redis_client as rc
    from app.core import bad_key_guard

    def boom():
        raise ConnectionError("redis down")
    monkeypatch.setattr(rc, "get_redis", boom)
    bad_key_guard.note_bad_key(_req())
    assert bad_key_guard.too_many_bad_keys(_req()) is False


def test_vidgrab_bad_key_is_counted(fr):
    from app.core import auth_middleware as am, bad_key_guard
    req = _req({"X-API-Key": "vidgrab_bad"})
    am._lookup_new_api_key, orig = (lambda k: None), am._lookup_new_api_key
    try:
        with pytest.raises(HTTPException):
            asyncio.run(am.get_optional_user(req, credentials=None, x_api_key="vidgrab_bad"))
    finally:
        am._lookup_new_api_key = orig
    assert int(fr.get(bad_key_guard._redis_key(req)) or 0) == 1


# ── limiter wiring (real app, in-memory limiter fallback) ─────────────────────

@pytest.fixture()
def client():
    import app.main as m
    return TestClient(m.app)


def _hit(client, path, n, headers=None, ip="198.51.100.77"):
    cnt = collections.Counter()
    h = {"x-forwarded-for": ip, **(headers or {})}
    for _ in range(n):
        cnt[client.get(path, headers=h).status_code] += 1
    return cnt


def test_partner_api_is_out_of_the_per_ip_limiter(client, fr):
    # 70 > the 60/min default: no 429 from the IP limiter, only the 401 of a
    # missing key. (On fastapi 0.141 the middleware skips included routers
    # anyway; main.py exempts the partner routes explicitly so it stays so.)
    import app.main as m
    # app.state.limiter, not m.limiter: test_admin_security replaces the module
    # attribute with a MagicMock and never restores it.
    assert "app.api.partner.submit_job" in m.app.state.limiter._exempt_routes
    assert _hit(client, "/api/v1/partner/usage", 70, ip="198.51.100.71") == {401: 70}


def test_main_limiter_counts_per_key():
    import app.main as m
    from app.core.client_ip import rate_limit_key
    assert m.app.state.limiter._key_func is rate_limit_key


def test_route_limit_is_per_key_not_per_ip(fr):
    # Same wiring as /fetch-link: an included router + @limiter.limit.
    from fastapi import APIRouter, FastAPI, Request
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
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
        return c.post("/heavy", headers={"x-forwarded-for": "198.51.100.73", **(headers or {})}).status_code

    assert [go() for _ in range(4)] == [200, 200, 200, 429]          # guests: per IP
    assert [go({"X-API-Key": "vidgrab_k1"}) for _ in range(4)] == [200, 200, 200, 429]
    assert go({"X-API-Key": "vidgrab_k2"}) == 200                     # another key, same IP


def test_api_info_states_the_real_limits(client):
    info = client.get("/api/v1/api-info").json()
    assert "30 requests/minute" in info["rate_limits"]["heavy"]
    assert "per API key" in info["rate_limits"]["default"]
    assert info["partner_api"]["base_path"] == "/api/v1/partner"
    assert "synchronous" in next(e for e in info["public_endpoints"]
                                 if e["path"] == "/api/v1/fetch-link")["description"]


# ── partner_quota ────────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


def test_monthly_cap_blocks_and_refusal_is_not_counted(fr):
    from app.core import partner_quota as pq
    for _ in range(3):
        assert pq.consume("t1", "k1", 100, 3, now=NOW)["allowed"]
    q = pq.consume("t1", "k1", 100, 3, now=NOW)
    assert not q["allowed"] and q["reason"] == "monthly" and q["reset_at"].startswith("2026-11-01")
    assert pq.usage("t1", "k1", now=NOW) == {"month": 3, "today_key": 3}


def test_daily_cap_blocks_and_rolls_back_the_month(fr):
    from app.core import partner_quota as pq
    assert pq.consume("t1", "k1", 2, 100, now=NOW)["allowed"]
    assert pq.consume("t1", "k1", 2, 100, now=NOW)["allowed"]
    q = pq.consume("t1", "k1", 2, 100, now=NOW)
    assert not q["allowed"] and q["reason"] == "daily" and q["reset_at"].startswith("2026-10-19")
    assert pq.usage("t1", "k1", now=NOW)["month"] == 2
    # another key of the same customer has its own day
    assert pq.consume("t1", "k2", 2, 100, now=NOW)["allowed"]


def test_unlimited_month(fr):
    from app.core import partner_quota as pq
    assert all(pq.consume("t1", "k1", 10_000, -1, now=NOW)["allowed"] for _ in range(50))


def test_quota_fails_open(monkeypatch):
    import app.core.redis_client as rc
    from app.core import partner_quota as pq

    def boom():
        raise ConnectionError("redis down")
    monkeypatch.setattr(rc, "get_redis", boom)
    assert pq.consume("t1", "k1", 1, 1, now=NOW)["allowed"]


def test_submit_job_checks_quota_429(fr):
    from app.api import partner
    from app.core.tenant import TenantContext, TenantPlan
    t = TenantContext(tenant_id="t9", workspace_id="", plan=TenantPlan.starter,
                      scopes=["write"], api_key_id="k9", rate_limit_per_day=10, monthly_cap=1)
    partner._check_job_quota(t)
    with pytest.raises(HTTPException) as e:
        partner._check_job_quota(t)
    assert e.value.status_code == 429 and e.value.detail["code"] == "monthly_quota_exceeded"


# ── TenantContext monthly cap ────────────────────────────────────────────────

def test_plan_limits_use_tenant_cap_except_enterprise():
    from app.core.tenant import TenantContext, TenantPlan
    base = dict(tenant_id="t", workspace_id="", scopes=[])
    assert TenantContext(plan=TenantPlan.starter, **base).plan_limits()["api_calls_per_month"] == 1_000
    assert TenantContext(plan=TenantPlan.growth, monthly_cap=7, **base).plan_limits()["api_calls_per_month"] == 7
    assert TenantContext(plan=TenantPlan.enterprise, monthly_cap=7, **base).plan_limits()["api_calls_per_month"] == -1


# ── partner_auth ─────────────────────────────────────────────────────────────

def _partner_sb(key_row, tenant_row):
    sb = MagicMock()

    def table(name):
        t = MagicMock()
        data = {"tenant_api_keys": key_row, "tenants": tenant_row}.get(name, {})
        chain = t.select.return_value.eq.return_value
        chain.single.return_value.execute.return_value = SimpleNamespace(data=data)
        chain.maybe_single.return_value.execute.return_value = SimpleNamespace(data={})
        return t
    sb.table.side_effect = table
    return sb


KEY_ROW = {"id": "k1", "tenant_id": "t1", "is_active": True, "ip_allowlist": None,
           "expires_at": None, "rate_limit_per_min": 60, "rate_limit_per_day": 50}


def _auth(monkeypatch, tenant_row, key_row=KEY_ROW, headers=None):
    from app.core import partner_auth
    monkeypatch.setattr(partner_auth, "get_service_client", lambda: _partner_sb(key_row, tenant_row))
    async def _noop(_key_id):
        return None
    monkeypatch.setattr(partner_auth, "_update_key_usage", _noop)
    req = _req(headers or {"X-API-Key": "vgp_good"})
    return asyncio.run(partner_auth.get_partner_tenant(req))


def test_partner_tenant_carries_monthly_cap(monkeypatch, fr):
    t = _auth(monkeypatch, {"plan": "growth", "status": "active", "plan_api_calls_per_month": 1234})
    assert t.monthly_cap == 1234 and t.plan_limits()["api_calls_per_month"] == 1234


@pytest.mark.parametrize("status", ["suspended", "canceled"])
def test_inactive_tenant_is_refused(monkeypatch, fr, status):
    with pytest.raises(HTTPException) as e:
        _auth(monkeypatch, {"plan": "starter", "status": status})
    assert e.value.status_code == 403 and e.value.detail["code"] == "tenant_inactive"


def test_partner_bad_keys_then_429_before_lookup(monkeypatch, fr):
    from app.core import bad_key_guard, partner_auth
    sb = _partner_sb(None, {})
    monkeypatch.setattr(partner_auth, "get_service_client", lambda: sb)
    req = _req({"X-API-Key": "vgp_wrong"})
    for _ in range(bad_key_guard.BAD_KEY_LIMIT):
        with pytest.raises(HTTPException) as e:
            asyncio.run(partner_auth.get_partner_tenant(req))
        assert e.value.status_code == 401
    lookups = sb.table.call_count
    with pytest.raises(HTTPException) as e:
        asyncio.run(partner_auth.get_partner_tenant(req))
    assert e.value.status_code == 429 and e.value.detail["code"] == "too_many_invalid_keys"
    assert sb.table.call_count == lookups      # refused before touching the DB


# ── admin onboarding ─────────────────────────────────────────────────────────

class _FakeTable:
    def __init__(self, db, name):
        self.db, self.name, self.op, self.payload, self.filters = db, name, None, None, []

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def select(self, *_a, **_k):
        self.op = self.op or "select"
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def maybe_single(self):
        self.single_row = True
        return self

    def execute(self):
        rows = self.db.setdefault(self.name, [])
        match = [r for r in rows if all(r.get(c) == v for c, v in self.filters)]
        if self.op == "insert":
            row = {"id": f"{self.name}-{len(rows) + 1}", **self.payload}
            rows.append(row)
            return SimpleNamespace(data=[dict(row)], count=None)
        if self.op == "delete":
            for r in match:
                rows.remove(r)
            return SimpleNamespace(data=[dict(r) for r in match], count=None)
        if self.op == "update":
            for r in match:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in match], count=None)
        if getattr(self, "single_row", False):
            return SimpleNamespace(data=dict(match[0]) if match else None, count=None)
        return SimpleNamespace(data=[dict(r) for r in match], count=len(match))


class _FakeDB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return _FakeTable(self.tables, name)


@pytest.fixture()
def admin_db(monkeypatch):
    from app.api import admin_partners
    db = _FakeDB()
    monkeypatch.setattr(admin_partners, "get_supabase_client", lambda: db)
    monkeypatch.setattr(admin_partners, "log_admin_action", lambda *a, **k: None)
    return db


def _run(c):
    return asyncio.run(c)


def test_create_tenant_with_plan_default_cap(admin_db):
    from app.api import admin_partners as ap
    t = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="Công ty ABC", plan="growth"), MagicMock()))
    assert t["plan_api_calls_per_month"] == 50_000 and t["status"] == "active"
    assert t["slug"].startswith("cong-ty-abc-")
    assert admin_db.tables["tenant_settings"][0]["tenant_id"] == t["id"]
    t2 = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="X", api_calls_per_month=-1), MagicMock()))
    assert t2["plan_api_calls_per_month"] == -1


def test_create_tenant_rejects_unknown_plan():
    from app.api import admin_partners as ap
    with pytest.raises(ValueError):
        ap.CreateTenantBody(name="X", plan="gold")


def test_issue_key_returns_raw_once_and_stores_only_hash(admin_db):
    from app.api import admin_partners as ap
    t = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="ABC"), MagicMock()))
    row = _run(ap.issue_partner_key(
        t["id"], ap.IssueKeyBody(label="prod", rate_limit_per_min=300, ip_allowlist=["198.51.100.7"]),
        MagicMock()))
    raw = row["raw_key"]
    assert raw.startswith("vgp_") and len(raw) == 68 and "key_hash" not in row
    stored = admin_db.tables["tenant_api_keys"][0]
    assert stored["key_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in str(stored)
    assert stored["rate_limit_per_min"] == 300 and stored["ip_allowlist"] == ["198.51.100.7"]
    assert set(stored["scopes"]) == {"read", "write", "batch", "webhook"}


def test_issue_key_unknown_tenant_404(admin_db):
    from app.api import admin_partners as ap
    with pytest.raises(HTTPException) as e:
        _run(ap.issue_partner_key("nope", ap.IssueKeyBody(), MagicMock()))
    assert e.value.status_code == 404


def test_bad_ip_in_allowlist_rejected():
    from app.api import admin_partners as ap
    with pytest.raises(ValueError):
        ap.IssueKeyBody(ip_allowlist=["not-an-ip"])


def test_rotate_changes_secret(admin_db):
    from app.api import admin_partners as ap
    t = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="ABC"), MagicMock()))
    first = _run(ap.issue_partner_key(t["id"], ap.IssueKeyBody(), MagicMock()))
    second = _run(ap.rotate_partner_key(first["id"], MagicMock()))
    assert second["raw_key"] != first["raw_key"]
    assert admin_db.tables["tenant_api_keys"][0]["key_hash"] == hashlib.sha256(second["raw_key"].encode()).hexdigest()


def test_update_key_null_clears_allowlist_and_omitted_fields_untouched(admin_db):
    from app.api import admin_partners as ap
    t = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="ABC"), MagicMock()))
    k = _run(ap.issue_partner_key(t["id"], ap.IssueKeyBody(ip_allowlist=["198.51.100.7"]), MagicMock()))
    out = _run(ap.update_partner_key(k["id"], ap.UpdateKeyBody(ip_allowlist=None, rate_limit_per_day=5),
                                     MagicMock()))
    assert out["ip_allowlist"] is None and out["rate_limit_per_day"] == 5
    assert out["rate_limit_per_min"] == 60 and out["label"] == "API Key"
    with pytest.raises(HTTPException) as e:
        _run(ap.update_partner_key(k["id"], ap.UpdateKeyBody(), MagicMock()))
    assert e.value.status_code == 422


def test_set_tenant_limits(admin_db):
    from app.api import admin_partners as ap
    t = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="ABC"), MagicMock()))
    _run(ap.set_partner_tenant_limits(t["id"], ap.TenantLimitsBody(api_calls_per_month=2500), MagicMock()))
    assert admin_db.tables["tenants"][0]["plan_api_calls_per_month"] == 2500


# ── partner job bridge ───────────────────────────────────────────────────────

def test_failed_job_writes_error_message_column(monkeypatch):
    """partner_jobs has error_message, not error: writing "error" made the
    whole update fail and the job stayed queued forever."""
    from app.tasks import partner_tasks
    import app.services.webhook_dispatcher as wd

    updates = []
    db = MagicMock()

    def table(name):
        t = MagicMock()
        if name == "download_jobs":
            t.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(
                data=[{"status": "failed", "error_message": "Video không còn tồn tại"}])
        else:
            def upd(payload):
                updates.append(payload)
                return MagicMock()
            t.update.side_effect = upd
        return t
    db.table.side_effect = table
    monkeypatch.setattr(partner_tasks, "get_service_client", lambda: db)

    async def _no_dispatch(*a, **k):
        return None
    monkeypatch.setattr(wd, "dispatch_event", _no_dispatch)

    partner_tasks.sync_partner_job_task.run("pj1", "dj1", "t1")
    assert updates and updates[0]["status"] == "failed"
    assert updates[0]["error_message"] == "Video không còn tồn tại"
    assert "error" not in updates[0]


# ── validate-urls ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,platform", [
    ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "youtube"),
    ("https://youtu.be/jNQXAC9IVRw", "youtube"),
    ("https://www.tiktok.com/@a/video/7300000000000000000", "tiktok"),
    ("https://v.douyin.com/abc/", "douyin"),
    ("https://www.facebook.com/watch?v=1", "facebook"),
    ("https://x.com/a/status/1", "twitter"),
    ("https://www.bilibili.com/video/BV1xx411c7mD", "bilibili"),
])
def test_validate_urls_accepts_the_main_platforms(url, platform):
    """These all came back "unsupported" (the registry only holds secondary
    extractors) while a job for the same link downloaded fine — measured on
    production 18/10 with "Me at the zoo"."""
    from app.services.extractor_registry import REGISTRY
    r = REGISTRY.validate_batch_url(url)
    assert r["status"] == "valid" and r["platform"] == platform
    assert r["support_level"] and r["source_type"]


def test_validate_urls_unknown_site_is_not_refused():
    from app.services.extractor_registry import REGISTRY
    r = REGISTRY.validate_batch_url("https://example.com/x")
    assert r["status"] == "unknown" and r["platform"] is None


def test_validate_urls_batch_counts():
    from app.services.extractor_registry import REGISTRY
    out = REGISTRY.validate_batch(["https://youtu.be/jNQXAC9IVRw", "https://youtu.be/jNQXAC9IVRw",
                                   "https://example.com/x", "not a url"])
    assert out["counts"] == {"valid": 1, "duplicate": 1, "unknown": 1, "unsupported": 0, "invalid": 1}
    assert "1 chưa nhận diện" in out["summary"]


def test_delete_key(admin_db):
    from app.api import admin_partners as ap
    t = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="ABC"), MagicMock()))
    k = _run(ap.issue_partner_key(t["id"], ap.IssueKeyBody(), MagicMock()))
    assert _run(ap.delete_partner_key(k["id"], MagicMock()))["success"]
    assert admin_db.tables["tenant_api_keys"] == []
    with pytest.raises(HTTPException) as e:
        _run(ap.delete_partner_key(k["id"], MagicMock()))
    assert e.value.status_code == 404


def test_delete_partner_tenant(admin_db):
    from app.api import admin_partners as ap
    t = _run(ap.create_partner_tenant(ap.CreateTenantBody(name="ABC"), MagicMock()))
    assert _run(ap.delete_partner_tenant(t["id"], MagicMock()))["success"]
    assert admin_db.tables["tenants"] == []


def test_web_linked_tenant_cannot_be_deleted(admin_db):
    from app.api import admin_partners as ap
    admin_db.tables["tenants"] = [{"id": "t-web", "name": "R27", "workspace_id": "ws-1"}]
    with pytest.raises(HTTPException) as e:
        _run(ap.delete_partner_tenant("t-web", MagicMock()))
    assert e.value.status_code == 409
    assert admin_db.tables["tenants"]          # still there
