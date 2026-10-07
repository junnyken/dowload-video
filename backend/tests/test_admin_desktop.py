"""
Task #6087 (PLAN-32D §7.1) — admin "App Windows": /admin/desktop/devices and
/admin/desktop/stats. Holds: admin auth required · device list newest first
with today's usage under the right counter (user:<id> when signed in, else
dev:<32 hex>) · search by code / name / user / email · the full 64-hex hash
never leaves the server · table missing → storage_ready false · stats
aggregate the route×outcome hash over several days · days clamped to 1..31.
fakeredis + an in-memory Supabase fake, no network.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.admin import verify_admin
from app.core import quotas
from app.main import app as fastapi_app
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
DEV = "/api/v1/admin/desktop/devices"
STATS = "/api/v1/admin/desktop/stats"


def h(n: int) -> str:
    return hashlib.sha256(f"machine-{n}".encode()).hexdigest()


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


# ── in-memory Supabase ─────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


def _like(pattern: str, value) -> bool:
    rx = "".join(".*" if c in "*%" else re.escape(c) for c in pattern)
    return value is not None and re.fullmatch(rx, str(value), re.I | re.S) is not None


_OR_PART = re.compile(r"(\w+)\.(ilike|eq|in)\.(\([^)]*\)|[^,]+)")


class _Q:
    def __init__(self, db, name):
        self.db, self.name, self.f = db, name, []
        self._order, self._desc, self._range, self._limit, self.count = None, False, None, None, None

    def select(self, _cols="*", count=None):
        self.count = count
        return self

    def order(self, c, desc=False):
        self._order, self._desc = c, desc
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def in_(self, c, vs):
        self.f.append(lambda r: r.get(c) in vs)
        return self

    def ilike(self, c, p):
        self.f.append(lambda r: _like(p, r.get(c)))
        return self

    def gte(self, c, v):
        self.f.append(lambda r: r.get(c) is not None and r.get(c) >= v)
        return self

    def or_(self, expr):
        self.db.or_calls.append(expr)
        conds = []
        for col, op, val in _OR_PART.findall(expr):
            if op == "ilike":
                conds.append(lambda r, c=col, v=val: _like(v, r.get(c)))
            elif op == "eq":
                conds.append(lambda r, c=col, v=val: str(r.get(c)) == v)
            else:
                vals = val.strip("()").split(",")
                conds.append(lambda r, c=col, vs=vals: r.get(c) in vs)
        self.f.append(lambda r: any(fn(r) for fn in conds))
        return self

    def execute(self):
        if self.name == "desktop_devices" and self.db.broken:
            raise Exception(self.db.broken)
        rows = [dict(r) for r in self.db.tables.get(self.name, []) if all(fn(r) for fn in self.f)]
        if self._order:
            rows.sort(key=lambda r: r.get(self._order) or "", reverse=self._desc)
        total = len(rows)
        if self._range:
            rows = rows[self._range[0]: self._range[1] + 1]
        if self._limit is not None:
            rows = rows[: self._limit]
        return _Resp(rows, total if self.count else None)


class FakeDB:
    def __init__(self):
        self.tables = {"desktop_devices": [], "profiles": []}
        self.broken = None
        self.or_calls = []

    def table(self, name):
        return _Q(self, name)


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    monkeypatch.setattr("app.core.database.get_service_client", lambda: d)
    return d


@pytest.fixture
def admin():
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    yield
    fastapi_app.dependency_overrides.pop(verify_admin, None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("CLIENT_QUOTA_ENABLED", "CLIENT_QUOTA_MODE", "CLIENT_QUOTA_ENFORCE_FOR",
              "CLIENT_COOKIES_PLATFORMS", "CLIENT_SERVER_FALLBACK_PLATFORMS", "DESKTOP_LATEST_VERSION",
              "DESKTOP_MIN_VERSION", "PLATFORM_DAILY_LIMIT_ANON", "PLATFORM_DAILY_LIMIT_USER", "QUOTA_SCOPE"):
        monkeypatch.delenv(k, raising=False)


def seed_devices(db, now=None):
    now = now or datetime.now(timezone.utc)
    db.tables["desktop_devices"] = [
        {"device_hash": h(1), "display_name": "PC-KETOAN (Windows 10.0.26200)", "client_version": "0.8.0",
         "user_id": None, "last_ip": "203.0.113.5", "first_seen": iso(now - timedelta(days=3)),
         "last_seen": iso(now - timedelta(minutes=5))},
        {"device_hash": h(2), "display_name": "LAPTOP-AN", "client_version": "0.7.1",
         "user_id": "11111111-aaaa-bbbb-cccc-000000000001", "last_ip": "203.0.113.9",
         "first_seen": iso(now - timedelta(days=1)), "last_seen": iso(now - timedelta(minutes=1))},
        {"device_hash": h(3), "display_name": "OLD-PC", "client_version": "0.6.0",
         "user_id": None, "last_ip": "198.51.100.1", "first_seen": iso(now - timedelta(days=20)),
         "last_seen": iso(now - timedelta(days=2))},
    ]
    db.tables["profiles"] = [{"id": "11111111-aaaa-bbbb-cccc-000000000001", "email": "an@example.com",
                              "tier": "free", "billing_status": "none"}]


# ── auth ───────────────────────────────────────────────────────────────────

def test_auth_required(rc, db):
    for path in (DEV, STATS, f"{STATS}?days=3"):
        r = client.get(path)
        assert r.status_code in (401, 403), path
    assert client.get(DEV, headers={"X-Admin-Token": "wrong-guess"}).status_code in (401, 403, 429)


# ── devices ────────────────────────────────────────────────────────────────

def test_devices_shape_order_and_usage(rc, db, admin):
    seed_devices(db)
    dev1 = quotas.QuotaRequester(quotas.REQ_DEVICE, h(1)[:32])
    user = quotas.QuotaRequester(quotas.REQ_USER, "11111111-aaaa-bbbb-cccc-000000000001")
    for i in range(3):
        quotas.record_platform_download(dev1, "tiktok", f"https://www.tiktok.com/@a/video/{i}")
    for i in range(7):
        quotas.record_platform_download(user, "youtube", f"https://www.youtube.com/watch?v=v{i}")
    rc.set(f"vidgrab:quota:refund:{dev1.key}:{quotas._utc_day()}", 2)
    # the machine of the signed-in user also has a device counter: must NOT be shown
    quotas.record_platform_download(quotas.QuotaRequester(quotas.REQ_DEVICE, h(2)[:32]), "tiktok",
                                    "https://www.tiktok.com/@a/video/x")

    r = client.get(DEV)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["storage_ready"] is True and body["total"] == 3
    codes = [d["code"] for d in body["devices"]]
    assert codes == [h(2)[:8].upper(), h(1)[:8].upper(), h(3)[:8].upper()]  # last_seen desc
    d2, d1, d3 = body["devices"]
    assert d1["id"] == h(1)[:16] and d1["display_name"].startswith("PC-KETOAN")
    assert d1["today"] == {"counted_as": "device", "used": 3, "limit": 5, "refunds": 2, "retro": 0}
    assert d2["today"]["counted_as"] == "user" and d2["today"]["used"] == 7 and d2["today"]["limit"] == 20
    assert d2["user_email"] == "an@example.com" and d1["user_email"] is None
    assert d3["today"]["used"] == 0
    # the full 64-hex machine hash never leaves the server
    for n in (1, 2, 3):
        assert h(n) not in r.text and h(n)[:32] not in r.text


def test_devices_search(rc, db, admin):
    seed_devices(db)

    def codes(q):
        r = client.get(DEV, params={"q": q})
        assert r.status_code == 200, r.text
        return [d["code"] for d in r.json()["devices"]]

    assert codes(h(1)[:8].upper()) == [h(1)[:8].upper()]          # display code, any case
    assert codes(h(3)[:8].lower()) == [h(3)[:8].upper()]
    assert codes("laptop") == [h(2)[:8].upper()]                    # name, case-insensitive
    assert codes("11111111-aaaa") == [h(2)[:8].upper()]             # user id prefix
    assert codes("an@example.com") == [h(2)[:8].upper()]            # e-mail → user id
    assert codes("zzz-nothing") == []
    # PostgREST separators are stripped from the filter
    codes("a,b(c):d")
    assert all("(" not in e.split(".in.")[0] and ":" not in e for e in db.or_calls)


def test_devices_pagination(rc, db, admin):
    now = datetime.now(timezone.utc)
    db.tables["desktop_devices"] = [
        {"device_hash": h(i), "display_name": f"PC-{i}", "client_version": "0.8.0", "user_id": None,
         "last_ip": "203.0.113.1", "first_seen": iso(now), "last_seen": iso(now - timedelta(minutes=i))}
        for i in range(12)]
    a = client.get(DEV, params={"limit": 10, "offset": 0}).json()
    b = client.get(DEV, params={"limit": 10, "offset": 10}).json()
    assert a["total"] == 12 and len(a["devices"]) == 10 and len(b["devices"]) == 2
    assert client.get(DEV, params={"limit": 0}).status_code == 422
    assert client.get(DEV, params={"limit": 500}).status_code == 422


@pytest.mark.parametrize("err", ["{'code': 'PGRST205', 'message': \"Could not find the table "
                                 "'public.desktop_devices' in the schema cache\"}",
                                 'relation "desktop_devices" does not exist (42P01)'])
def test_storage_not_ready(rc, db, admin, err):
    db.broken = err
    r = client.get(DEV)
    assert r.status_code == 200
    assert r.json()["storage_ready"] is False and r.json()["devices"] == []
    s = client.get(STATS)
    assert s.status_code == 200 and s.json()["devices"]["storage_ready"] is False


def test_other_db_error_is_500_without_detail(rc, db, admin):
    db.broken = "connection reset by peer secret=abc"
    r = client.get(DEV)
    assert r.status_code == 500 and "secret" not in r.text


# ── stats ──────────────────────────────────────────────────────────────────

def _seed_stats(rc, day, **fields):
    for k, v in fields.items():
        route, outcome = k.split("__")
        rc.hset(f"vidgrab:stats:route:{day}", f"{route}|{outcome}", v)


def test_stats_aggregates_days(rc, db, admin, monkeypatch):
    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "true")
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "shadow")
    monkeypatch.setenv("CLIENT_COOKIES_PLATFORMS", "douyin,instagram")
    monkeypatch.setenv("CLIENT_SERVER_FALLBACK_PLATFORMS", "youtube")
    monkeypatch.setenv("DESKTOP_LATEST_VERSION", "0.8.0")
    now = datetime.now(timezone.utc)
    seed_devices(db, now)
    d0, d1, d2 = (quotas._utc_day(now - timedelta(days=i)) for i in range(3))
    _seed_stats(rc, d0, local__ok=10, local__over_shadow=4, local_cookie__ok=3, server__ok=2,
                local__settle_failed=1, local__retro=1)
    _seed_stats(rc, d1, local__ok=5, local__over_shadow=2, server__ok=3, local_cookie__over_shadow=1)
    _seed_stats(rc, d2, local__ok=100)                     # outside days=2
    rc.hset(f"vidgrab:stats:route:{d0}", "bogus|ok", 9)   # unknown route ignored
    rc.hset(f"vidgrab:stats:route:{d0}", "local|weird", 9)
    rc.set(f"vidgrab:quota:refund:dev:{'a' * 32}:{d0}", 2)
    rc.set(f"vidgrab:quota:refund:user:u1:{d0}", 1)

    r = client.get(STATS, params={"days": 2})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["days"] == 2 and [p["day"] for p in b["per_day"]] == [d0, d1]
    assert b["per_day"][0]["routes"]["local"]["ok"] == 10 and b["per_day"][0]["over_shadow"] == 4
    assert b["per_day"][1]["over_shadow"] == 3
    assert b["totals"]["local"]["ok"] == 15 and b["totals"]["server"]["ok"] == 5
    s = b["summary"]
    assert s["over_shadow"] == 7
    assert s["counted_local"] == 10 + 4 + 1 + 5 + 2 and s["counted_local_cookie"] == 4 and s["counted_server"] == 5
    assert s["local_share"] == round(26 / 31, 4)
    assert s["refunds_today"] == 3 and s["settle_failed"] == 1 and s["retro"] == 1
    f = b["flags"]
    assert f["client_quota_enabled"] is True and f["client_quota_mode"] == "shadow"
    assert f["cookie_platforms"] == ["douyin", "instagram"] and f["server_fallback_platforms"] == ["youtube"]
    assert f["desktop_latest_version"] == "0.8.0" and f["limit_anon"] == 5 and f["limit_user"] == 20
    assert b["devices"]["storage_ready"] is True and b["devices"]["total"] == 3
    assert b["devices"]["active_today"] >= 1  # seeded 1-5 minutes ago (may cross UTC midnight)
    # h(2) first seen yesterday, h(1) 3 days ago (outside), h(3) 20 days ago
    assert b["per_day"][1]["new_devices"] == 1 and b["devices"]["new_in_period"] == 1


@pytest.mark.parametrize("asked,got", [(0, 1), (-5, 1), (1, 1), (7, 7), (31, 31), (365, 31)])
def test_days_clamped(rc, db, admin, asked, got):
    b = client.get(STATS, params={"days": asked}).json()
    assert b["days"] == got and len(b["per_day"]) == got


def test_stats_empty_and_redis_down(db, admin, monkeypatch):
    class Down:
        def __getattr__(self, _):
            raise ConnectionError("down")
    monkeypatch.setattr("app.api.admin_desktop._r", lambda: Down())
    b = client.get(STATS).json()
    assert b["redis_ok"] is False and b["summary"]["over_shadow"] == 0 and b["summary"]["local_share"] is None
