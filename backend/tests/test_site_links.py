"""Task #6331: status page link set from the admin, shown publicly."""
import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import site_links


@pytest.fixture()
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(site_links, "_r", lambda: r)
    site_links._cache.clear()
    yield r
    site_links._cache.clear()


@pytest.mark.parametrize("bad", ["https://stats.uptimerobot.com/...", "https://stats.uptimerobot.com/…",
                                 "http://stats.uptimerobot.com/x", "javascript:alert(1)",
                                 "stats.uptimerobot.com/x", "https://", "https://a b.com/x",
                                 "https://x.com/" + "a" * 400])
def test_rejects_non_https_or_malformed(bad):
    with pytest.raises(ValueError):
        site_links.validate(bad)


def test_set_get_clear(rc):
    assert site_links.get_links() == {"status_page_url": None}
    site_links.set_link("status_page_url", " https://stats.uptimerobot.com/AbC ")
    assert site_links.get_links()["status_page_url"] == "https://stats.uptimerobot.com/AbC"
    site_links.set_link("status_page_url", "")
    assert site_links.get_links()["status_page_url"] is None


def test_unreadable_redis_gives_empty(monkeypatch):
    site_links._cache.clear()

    def boom():
        raise ConnectionError("down")

    monkeypatch.setattr(site_links, "_r", boom)
    assert site_links.get_links() == {"status_page_url": None}
    site_links._cache.clear()


def _app(monkeypatch, admin_ok=True):
    from app.api import site_links as api
    verify_admin = api.verify_admin   # the object the routes depend on (admin may be reloaded by other tests)
    app = FastAPI()
    app.include_router(api.public_router, prefix="/api/v1")
    app.include_router(api.admin_router, prefix="/api/v1/admin")
    if admin_ok:
        app.dependency_overrides[verify_admin] = lambda: True
    monkeypatch.setattr(api, "log_admin_action", lambda *a, **k: None)
    return TestClient(app)


def test_public_endpoint_and_admin_set(rc, monkeypatch):
    c = _app(monkeypatch)
    assert c.get("/api/v1/site-links").json() == {"status_page_url": None}
    r = c.post("/api/v1/admin/site-links", json={"status_page_url": "https://stats.uptimerobot.com/AbC"})
    assert r.status_code == 200
    assert c.get("/api/v1/site-links").json()["status_page_url"] == "https://stats.uptimerobot.com/AbC"
    assert c.post("/api/v1/admin/site-links", json={"status_page_url": "http://x"}).status_code == 422


def test_admin_set_requires_admin(rc, monkeypatch):
    c = _app(monkeypatch, admin_ok=False)
    r = c.post("/api/v1/admin/site-links", json={"status_page_url": "https://x.com/s"})
    assert r.status_code in (401, 403)
    assert site_links.get_links()["status_page_url"] is None


def test_stored_placeholder_is_hidden(rc):
    rc.hset(site_links.KEY, "status_page_url", "https://stats.uptimerobot.com/...")
    assert site_links.get_links()["status_page_url"] is None
