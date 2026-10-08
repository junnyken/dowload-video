"""Task #6150 — ScraperAPI can be switched off from the admin page.

Until 08/10 an empty key pool was re-imported from SCRAPERAPI_API_KEY on
every read: removing the last key brought it straight back. And the
Overview kept showing the last balance ("741 Critical") from
provider_status after the keys were gone.

  [1] env seeds the pool once; emptied pool stays empty (env still set)
  [2] remove_all_keys / remove_key of the last key → no active key → every
      ScraperAPI layer skips (proxy URL '', SSR url '')
  [3] Redis down → no key (not the env key)
  [4] add_key after clearing switches it back on
  [5] admin DELETE /scraperapi/keys empties it
"""
import fakeredis
import pytest

from app.api.admin import verify_admin
from app.core import scraperapi_pool as sp
from app.main import app as fastapi_app


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(sp, "get_redis", lambda: r)
    monkeypatch.setenv("SCRAPERAPI_API_KEY", "envkeyAAAAAAAA,envkeyBBBBBBBB")
    return r


def test_env_seeds_once_and_an_emptied_pool_stays_empty(rc):
    assert sp.get_all_keys() == ["envkeyAAAAAAAA", "envkeyBBBBBBBB"]
    assert sp.remove_key(0) == 1
    assert sp.remove_key(0) == 0
    assert sp.get_all_keys() == []                 # env is NOT re-imported
    assert sp.get_active_key() == ""


def test_remove_all_keys_turns_every_layer_off(rc):
    assert sp.get_active_key() == "envkeyAAAAAAAA"
    assert sp.remove_all_keys() == 2
    assert sp.get_all_keys() == [] and sp.get_active_key() == ""
    assert sp.scraperapi_proxy("CN") == "" and sp.scraperapi_url("https://www.douyin.com/video/1") == ""
    assert sp.fetch_all_credits() == []            # no 6-hourly credit check, no 400 in the log


def test_remove_all_before_any_read_does_not_resurrect_env(rc):
    sp.remove_all_keys()                           # first touch ever
    assert sp.get_all_keys() == []


def test_redis_down_means_no_key_not_the_env_key(monkeypatch):
    monkeypatch.setenv("SCRAPERAPI_API_KEY", "envkeyAAAAAAAA")

    def _down():
        raise ConnectionError("redis down")
    monkeypatch.setattr(sp, "get_redis", _down)
    assert sp.get_all_keys() == []
    assert sp.get_active_key() == ""


def test_adding_a_key_switches_it_back_on(rc):
    sp.remove_all_keys()
    assert sp.add_key("newkeyCCCCCCCC") == 1
    assert sp.get_active_key() == "newkeyCCCCCCCC"


def test_first_boot_without_env_is_empty(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(sp, "get_redis", lambda: r)
    monkeypatch.delenv("SCRAPERAPI_API_KEY", raising=False)
    assert sp.get_all_keys() == []
    monkeypatch.setenv("SCRAPERAPI_API_KEY", "lateenvkeyDDDD")   # env set after the first read
    assert sp.get_all_keys() == []                 # seeded once: the admin page is the source of truth


@pytest.fixture
def admin_client(rc):
    from fastapi.testclient import TestClient
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_admin_delete_all_keys(admin_client, rc, monkeypatch):
    monkeypatch.setattr("app.api.admin.log_admin_action", lambda *a, **k: None)
    assert len(sp.get_all_keys()) == 2
    r = admin_client.delete("/api/v1/admin/scraperapi/keys")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["removed"] == 2
    assert sp.get_all_keys() == []
    r = admin_client.get("/api/v1/admin/scraperapi/keys")
    assert r.status_code == 200 and r.json()["key_count"] == 0
