"""VidGrab Desktop C1 — /client/history + /client/version. No network:
the Supabase service client and the auth user are faked in memory."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth_middleware import get_required_user
from app.main import app as fastapi_app

client = TestClient(fastapi_app, raise_server_exceptions=False)


class _Resp:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, db):
        self.db, self.f, self.op, self.payload = db, [], "select", None
        self._limit, self._desc, self.on_conflict = None, False, None

    def select(self, *_a, **_k): return self
    def upsert(self, rows, on_conflict=None):
        self.op, self.payload, self.on_conflict = "upsert", rows, on_conflict
        return self
    def eq(self, c, v): self.f.append(lambda r: r.get(c) == v); return self
    def in_(self, c, vs): self.f.append(lambda r: r.get(c) in vs); return self
    def lt(self, c, v): self.f.append(lambda r: r.get(c) < v); return self
    def order(self, c, desc=False): self._desc = desc; return self
    def limit(self, n): self._limit = n; return self

    def execute(self):
        if self.db.broken:
            raise Exception(self.db.broken)
        if self.op == "upsert":
            keys = self.on_conflict.split(",")
            for new in self.payload:
                hit = next((r for r in self.db.rows if all(r[k] == new[k] for k in keys)), None)
                if hit:
                    hit.update(new)
                else:
                    self.db.rows.append({"id": f"id-{len(self.db.rows)}", **new})
            return _Resp(self.payload)
        rows = [r for r in self.db.rows if all(f(r) for f in self.f)]
        rows.sort(key=lambda r: r["created_at"], reverse=self._desc)
        return _Resp([dict(r) for r in rows[: self._limit]])


class FakeDB:
    def __init__(self):
        self.rows, self.broken, self.tables = [], None, []

    def table(self, name):
        self.tables.append(name)
        return _Q(self)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app.main import limiter
    prev = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prev


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    monkeypatch.setattr("app.api.client_api._get_db", lambda: d)
    monkeypatch.setenv("CLIENT_API_ENABLED", "true")
    yield d


@pytest.fixture
def as_user():
    def _set(uid):
        fastapi_app.dependency_overrides[get_required_user] = lambda: {"id": uid}
    yield _set
    fastapi_app.dependency_overrides.pop(get_required_user, None)


def item(cid="c1", **kw):
    base = {"clientId": cid, "url": "https://youtu.be/x", "title": "T", "platform": "youtube",
            "formatLabel": "1080p", "fileSize": 123, "state": "completed", "errorCode": None,
            "finishedAt": "2026-10-06T01:02:03Z"}
    base.update(kw)
    return base


def post(items, **kw):
    return client.post("/api/v1/client/history",
                       json={"deviceId": "dev", "clientVersion": "0.1.0", "items": items}, **kw)


def test_flag_off_503_before_auth(monkeypatch):
    monkeypatch.delenv("CLIENT_API_ENABLED", raising=False)
    for r in (post([item()]), client.get("/api/v1/client/history")):
        assert r.status_code == 503
        assert r.json()["error_code"] == "client_api_disabled"


def test_auth_required(db):
    assert post([item()]).status_code == 401
    assert client.get("/api/v1/client/history").status_code == 401


def test_post_ok_and_fields_stored(db, as_user):
    as_user("A")
    r = post([item("c1"), item("c2", state="failed", errorCode="E1")])
    assert r.status_code == 200
    assert r.json() == {"accepted": 2, "ids": ["c1", "c2"]}
    assert {x["user_id"] for x in db.rows} == {"A"}
    assert db.rows[0]["device_id"] == "dev" and db.rows[0]["format_label"] == "1080p"


def test_too_many_items(db, as_user):
    as_user("A")
    assert post([item(f"c{i}") for i in range(101)]).status_code == 422
    assert post([item(f"c{i}") for i in range(100)]).status_code == 200


@pytest.mark.parametrize("bad", [
    {"url": "ftp://x/y"}, {"url": "javascript:alert(1)"}, {"url": "file:///C:/a.mp4"},
    {"url": "https://x/" + "a" * 2048}, {"title": "t" * 501}, {"state": "downloading"},
    {"clientId": ""}, {"fileSize": -1},
])
def test_validation_rejects(db, as_user, bad):
    as_user("A")
    assert post([item(**bad)]).status_code == 422
    assert db.rows == []


def test_path_field_ignored(db, as_user):
    as_user("A")
    r = post([item(filePath="C:\\Users\\bob\\v.mp4", savedTo="D:\\x", path="/home/x")])
    assert r.status_code == 200
    stored = db.rows[0]
    assert "C:" not in repr(stored) and "/home/x" not in repr(stored)
    assert not any(k in stored for k in ("filePath", "file_path", "path", "savedTo"))
    got = client.get("/api/v1/client/history").json()["items"][0]
    assert "filePath" not in got and "path" not in got


def test_upsert_idempotent_keeps_created_at(db, as_user):
    as_user("A")
    post([item("c1", title="old")])
    created = db.rows[0]["created_at"]
    r = post([item("c1", title="new"), item("c1", title="newest")])  # dup inside batch too
    assert r.json() == {"accepted": 1, "ids": ["c1"]}
    assert len(db.rows) == 1
    assert db.rows[0]["title"] == "newest" and db.rows[0]["created_at"] == created


def test_owner_isolation(db, as_user):
    as_user("A"); post([item("a1")])
    as_user("B"); post([item("b1")]); post([item("a1", title="B's copy")])
    ids = [x["clientId"] for x in client.get("/api/v1/client/history").json()["items"]]
    assert sorted(ids) == ["a1", "b1"]  # B has its own a1 row, A's row untouched
    as_user("A")
    items = client.get("/api/v1/client/history").json()["items"]
    assert [x["clientId"] for x in items] == ["a1"] and items[0]["title"] == "T"


def test_get_limit_cap_and_before_cursor(db, as_user):
    as_user("A")
    post([item(f"c{i}") for i in range(5)])
    r = client.get("/api/v1/client/history?limit=2").json()
    assert [x["clientId"] for x in r["items"]] == ["c4", "c3"] and r["nextBefore"]
    r2 = client.get("/api/v1/client/history", params={"limit": 10, "before": r["nextBefore"]}).json()
    assert [x["clientId"] for x in r2["items"]] == ["c2", "c1", "c0"] and "nextBefore" not in r2
    assert client.get("/api/v1/client/history?limit=99999").status_code == 200
    assert client.get("/api/v1/client/history?before=garbage").status_code == 422


@pytest.mark.parametrize("msg", [
    'relation "public.desktop_downloads" does not exist',
    "Could not find the table 'public.desktop_downloads' in the schema cache (PGRST205)",
])
def test_storage_not_ready(db, as_user, msg):
    as_user("A")
    db.broken = msg
    for r in (post([item()]), client.get("/api/v1/client/history")):
        assert r.status_code == 503 and r.json()["error_code"] == "storage_not_ready"


def test_other_storage_error_is_500_not_leaky(db, as_user):
    as_user("A")
    db.broken = "connection refused secret-host"
    r = client.get("/api/v1/client/history")
    assert r.status_code == 500 and "secret-host" not in r.text


def test_version_defaults_and_env(monkeypatch):
    for k in ("DESKTOP_LATEST_VERSION", "DESKTOP_MIN_VERSION", "DESKTOP_DOWNLOAD_URL", "DESKTOP_RELEASE_NOTES",
              "CLIENT_QUOTA_ENABLED", "CLIENT_QUOTA_MODE", "CLIENT_QUOTA_OFFLINE_GRACE",
              "CLIENT_COOKIES_PLATFORMS"):
        monkeypatch.delenv(k, raising=False)
    r = client.get("/api/v1/client/version")
    assert r.status_code == 200
    feats = {"clientQuota": False, "clientQuotaMode": "shadow", "offlineGrace": 3, "cookiePlatforms": []}
    assert r.json() == {"latest": "0.1.0", "minSupported": "0.1.0", "notes": "", "downloadUrl": "",
                        "features": feats}
    monkeypatch.setenv("DESKTOP_LATEST_VERSION", "0.2.0")
    monkeypatch.setenv("DESKTOP_MIN_VERSION", "0.1.5")
    monkeypatch.setenv("DESKTOP_DOWNLOAD_URL", "https://example.com/setup.exe")
    monkeypatch.setenv("DESKTOP_RELEASE_NOTES", "Fixes")
    assert client.get("/api/v1/client/version").json() == {
        "latest": "0.2.0", "minSupported": "0.1.5", "notes": "Fixes", "downloadUrl": "https://example.com/setup.exe",
        "features": feats}


def test_cookie_platforms_are_allowlisted_and_ordered(monkeypatch):
    monkeypatch.setenv("CLIENT_COOKIES_PLATFORMS", " Instagram,douyin, evil.com ,douyin")
    assert client.get("/api/v1/client/version").json()["features"]["cookiePlatforms"] == ["douyin", "instagram"]
