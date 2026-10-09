"""Task #6223 — delete several pool cookies at once, by hash.

Removing by index shifts the cookies after the first one, so a multi-delete
by index removes the wrong cookies. The batch endpoint removes by hash.
"""
import fakeredis
import pytest

from app.api.admin import verify_admin
from app.core import cookie_pool as cp
from app.main import app as fastapi_app


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cp, "get_redis", lambda: r)
    return r


def _seed(rc, platform="youtube", n=4):
    vals = [f"Y29va2llLXt7aX19LXtpfQ=={i}" for i in range(n)]
    for v in vals:
        rc.rpush(f"cookie_pool:{platform}", v)
        h = cp._hash(v)
        rc.set(f"cookie_health:{platform}:{h}", "expired")
        rc.set(f"cookie_meta:{platform}:{h}", "{}")
    return [cp._hash(v) for v in vals]


def test_removes_exactly_the_given_hashes(rc):
    h = _seed(rc)
    out = cp.remove_cookies_by_hash("youtube", [h[0], h[2], "nope"])
    assert out == {"removed": [h[0], h[2]], "missing": ["nope"], "pool_size": 2}
    left = [cp._hash(c) for c in rc.lrange("cookie_pool:youtube", 0, -1)]
    assert left == [h[1], h[3]]                                 # not shifted onto the wrong ones
    assert rc.get(f"cookie_health:youtube:{h[0]}") is None and rc.get(f"cookie_meta:youtube:{h[2]}") is None
    assert rc.get(f"cookie_health:youtube:{h[1]}") == "expired"


def test_other_platforms_untouched(rc):
    hy = _seed(rc, "youtube", 2)
    ht = _seed(rc, "tiktok", 2)
    cp.remove_cookies_by_hash("youtube", hy + ht)
    assert rc.llen("cookie_pool:youtube") == 0 and rc.llen("cookie_pool:tiktok") == 2


@pytest.fixture
def client(rc, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr("app.api.admin.log_admin_action", lambda *a, **k: None)
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_endpoint(client, rc):
    h = _seed(rc)
    r = client.post("/api/v1/admin/cookies/remove-batch", json={"platform": "youtube", "hashes": h})
    assert r.status_code == 200 and r.json()["removed"] == h and r.json()["pool_size"] == 0
    assert client.post("/api/v1/admin/cookies/remove-batch",
                       json={"platform": "nope", "hashes": h}).status_code == 400
    assert client.post("/api/v1/admin/cookies/remove-batch",
                       json={"platform": "youtube", "hashes": []}).status_code == 400


def test_endpoint_requires_admin(rc):
    from fastapi.testclient import TestClient
    r = TestClient(fastapi_app).post("/api/v1/admin/cookies/remove-batch",
                                     json={"platform": "youtube", "hashes": ["x"]})
    assert r.status_code in (401, 403)
