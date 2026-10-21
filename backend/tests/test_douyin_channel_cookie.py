"""Douyin cookie for whole-channel listings (owner 21/10/2026). fakeredis, no network."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app, limiter
from app.services import douyin_channel_cookie as C
from tests._china_fakes import clean_env, flags_on, rc  # noqa: F401
from tests.test_client_douyin_api import on  # noqa: F401
from tests.test_douyin_channel_listing import PROFILE

client = TestClient(fastapi_app, raise_server_exceptions=False)
SEC = PROFILE.rsplit("/", 1)[1]

NETSCAPE = "\n".join([
    "# Netscape HTTP Cookie File",
    ".douyin.com\tTRUE\t/\tTRUE\t1827130477\tttwid\ttt-SECRET",
    ".douyin.com\tTRUE\t/\tFALSE\t1808903527\todin_tt\todin-SECRET",
    "#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t1811993373\tUIFID\tuif-SECRET",
    "www.douyin.com\tFALSE\t/\tFALSE\t1797645724\ts_v_web_id\tverify_x",
    ".github.com\tTRUE\t/\tTRUE\t1822462437\tuser_session\tGITHUB-SECRET",
    ".google.com\tTRUE\t/\tTRUE\t1827110522\tSID\tGOOGLE-SECRET",
])


@pytest.fixture(autouse=True)
def _keys(monkeypatch, rc):  # noqa: F811
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key-for-encryption")
    from app.core import cookie_crypto
    for f in vars(cookie_crypto).values():
        if hasattr(f, "cache_clear"):
            f.cache_clear()
    from app.api import client_douyin
    lims = {id(x): x for x in (limiter, client_douyin.limiter)}.values()
    prev = [(x, x.enabled) for x in lims]
    for x in lims:
        x.enabled = False
    monkeypatch.setattr(C, "PAGE_PAUSE_S", 0)
    yield
    for x, was in prev:
        x.enabled = was


def _aweme(vid, images=False):
    a = {"aweme_id": vid, "desc": f"clip {vid}", "author": {"nickname": "MaodVlog"},
         "video": {"duration": 30000, "cover": {"url_list": [f"https://a/{vid}.jpg"]},
                   "play_addr": {"url_list": [f"https://v/{vid}"]}}}
    if images:
        a["images"] = [{"url_list": ["https://a/i.jpg"]}]
    return a


class _R:
    def __init__(self, body, status=200):
        self.status_code, self.text = status, (json.dumps(body) if isinstance(body, dict) else body)


def _pages(monkeypatch, n_pages=3, per=18, refuse_from=None, seen=None):
    """Fake Douyin: page k answers ids k*100.., cursor = k; has_more until the last."""
    from curl_cffi import requests as cr

    def get(url, **kw):
        q = parse_qs(urlparse(url).query)
        cursor = int(q["max_cursor"][0])
        k = 0 if cursor == 0 else cursor
        if seen is not None:
            seen.append((k, kw.get("cookies", {}).get("ttwid")))
        if refuse_from is not None and k >= refuse_from:
            return _R("")
        ids = [str(7000000 + k * 100 + i) for i in range(per)]
        return _R({"status_code": 0, "has_more": int(k + 1 < n_pages), "max_cursor": k + 1,
                   "aweme_list": [_aweme(v, images=(i == 0)) for i, v in enumerate(ids)]})
    monkeypatch.setattr(cr, "get", get)


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parse_keeps_only_douyin_cookies():
    got = C.parse_cookies(NETSCAPE)
    assert got == {"ttwid": "tt-SECRET", "odin_tt": "odin-SECRET", "UIFID": "uif-SECRET", "s_v_web_id": "verify_x"}


def test_parse_json_export_and_header():
    j = json.dumps([{"domain": ".douyin.com", "name": "ttwid", "value": "a"},
                    {"domain": ".x.com", "name": "auth_token", "value": "no"}])
    assert C.parse_cookies(j) == {"ttwid": "a"}
    assert C.parse_cookies("ttwid=a; odin_tt=b; UIFID=c") == {"ttwid": "a", "odin_tt": "b", "UIFID": "c"}


def test_store_needs_ttwid_odin_and_uifid(rc):  # noqa: F811
    with pytest.raises(C.CookieError) as e:
        C.store("ttwid=a; UIFID=c", rc)
    assert "odin_tt" in str(e.value)
    with pytest.raises(C.CookieError):
        C.store(".github.com\tTRUE\t/\tTRUE\t1\tuser_session\tx", rc)
    names = C.store(NETSCAPE, rc)
    assert names == ["UIFID", "odin_tt", "s_v_web_id", "ttwid"]
    raw = rc.get(C.JAR_KEY)
    assert "SECRET" not in raw and "GITHUB" not in raw                 # encrypted, other sites dropped
    st = C.status(rc)
    assert st["has_cookie"] and st["names"] == names
    assert "SECRET" not in json.dumps(st)


# ── listing ──────────────────────────────────────────────────────────────────

def test_lists_the_whole_channel_page_by_page(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc)
    seen = []
    _pages(monkeypatch, n_pages=3, seen=seen)
    listed, why = C.list_channel(SEC, 500, rc=rc)
    assert why == "" and listed["pages"] == 3 and listed["has_more"] is False
    assert len(listed["videos"]) == 3 * 17                              # image posts left out
    assert [k for k, _ in seen] == [0, 1, 2] and all(t == "tt-SECRET" for _, t in seen)
    assert C.status(rc)["calls_this_hour"] == 3
    # a re-scan within 30 minutes is served from the cache
    from curl_cffi import requests as cr
    monkeypatch.setattr(cr, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    again, _ = C.list_channel(SEC, 20, rc=rc)
    assert len(again["videos"]) == 20 and again["has_more"] is True


def test_stops_at_the_limit(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc)
    seen = []
    _pages(monkeypatch, n_pages=10, seen=seen)
    listed, _ = C.list_channel(SEC, 30, rc=rc)
    assert len(listed["videos"]) == 30 and listed["has_more"] is True and len(seen) == 2


def test_later_page_refused_keeps_what_was_read(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc)
    _pages(monkeypatch, n_pages=5, refuse_from=2)
    listed, _ = C.list_channel(SEC, 500, rc=rc)
    assert len(listed["videos"]) == 34 and listed["has_more"] is True
    assert C.status(rc)["fail"] == 1


def test_first_page_refused_three_times_marks_the_cookie_dead(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc)
    _pages(monkeypatch, refuse_from=0)
    for _ in range(3):
        assert C.list_channel(SEC, 500, use_cache=False, rc=rc) == (None, "empty 200")
    assert C.status(rc)["dead"] and not C.usable(rc)
    assert C.list_channel(SEC, 500, use_cache=False, rc=rc) == (None, "unusable")


def test_hourly_cap_and_switch_off(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc)
    _pages(monkeypatch, n_pages=5)
    C.save_settings(per_hour=2, rc=rc)
    listed, _ = C.list_channel(SEC, 500, rc=rc)
    assert listed["pages"] == 2 and listed["has_more"] is True
    rc.delete(C.LIST_KEY.format(SEC))
    assert C.list_channel(SEC, 500, rc=rc) == (None, "rate_cap")
    C.save_settings(enabled=False, per_hour=100, rc=rc)
    assert C.list_channel(SEC, 500, use_cache=False, rc=rc) == (None, "unusable")


def test_test_reads_two_pages(rc, monkeypatch):  # noqa: F811
    assert C.test(rc)["reason"] == "no_cookie"
    C.store(NETSCAPE, rc)
    _pages(monkeypatch, n_pages=5)
    out = C.test(rc)
    assert out["ok"] and out["pages"] == 2 and out["items"] == 36
    assert C.status(rc)["last_test"]["ok"] is True


# ── API ──────────────────────────────────────────────────────────────────────

def _signed_in():
    from app.core.auth_middleware import get_optional_user
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-whole", "email": "w@example.com"}


@pytest.fixture
def _no_user():
    yield
    from app.core.auth_middleware import get_optional_user
    fastapi_app.dependency_overrides.pop(get_optional_user, None)


def test_signed_in_user_gets_the_whole_channel(on, rc, monkeypatch, _no_user):  # noqa: F811
    C.store(NETSCAPE, rc)
    _pages(monkeypatch, n_pages=3)
    _signed_in()
    body = client.post("/api/v1/channel/preview", json={"url": PROFILE, "limit": 500}).json()
    assert len(body["items"]) == 51 and body["hasMore"] is False and body["moreHint"] is None
    assert body["whole"] is True and body["channelTitle"] == "MaodVlog"
    app_body = client.post("/api/v1/client/douyin/channel", json={"url": PROFILE, "limit": 500}).json()
    assert len(app_body["items"]) == 51                                # the app gets it too
    assert on.starts() == []


def test_guest_keeps_the_first_page(on, rc, monkeypatch):  # noqa: F811
    from app.services import douyin_visitor as V
    C.store(NETSCAPE, rc)
    V.store_jar("Mozilla/5.0 Chrome/131.0.0.0", {"UIFID_TEMP": "visitor", "s_v_web_id": "v"}, rc)
    seen = []
    _pages(monkeypatch, n_pages=3, seen=seen)
    body = client.post("/api/v1/channel/preview", json={"url": PROFILE, "limit": 500}).json()
    assert body["hasMore"] is True and body["moreHint"] == "douyin_extension"
    assert seen == [(0, None)]                                         # the visitor jar, one page


def test_refused_cookie_falls_back_to_the_first_page(on, rc, monkeypatch, _no_user):  # noqa: F811
    from curl_cffi import requests as cr
    from app.services import douyin_visitor as V
    C.store(NETSCAPE, rc)
    V.store_jar("Mozilla/5.0 Chrome/131.0.0.0", {"UIFID_TEMP": "visitor", "s_v_web_id": "v"}, rc)

    def get(url, **kw):
        if kw.get("cookies", {}).get("ttwid"):
            return _R("")                                              # the admin cookie is refused
        return _R({"status_code": 0, "has_more": 1, "aweme_list": [_aweme("7001")]})
    monkeypatch.setattr(cr, "get", get)
    _signed_in()
    body = client.post("/api/v1/channel/preview", json={"url": PROFILE, "limit": 500}).json()
    assert [i["id"] for i in body["items"]] == ["7001"] and body["moreHint"] == "douyin_extension"


def test_caps_endpoint(rc):  # noqa: F811
    assert client.get("/api/v1/channel/preview/caps").json() == {"douyinWhole": False}
    C.store(NETSCAPE, rc)
    assert client.get("/api/v1/channel/preview/caps").json() == {"douyinWhole": True}


def test_admin_endpoints_never_return_values(rc, monkeypatch):  # noqa: F811
    from app.api.admin_china_platforms import verify_admin
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        base = "/api/v1/admin/china-platforms/douyin/channel-cookie"
        r = client.post(base, json={"cookies": "ttwid=SECRET-only"})
        assert r.status_code == 400 and "SECRET" not in r.text
        _pages(monkeypatch, n_pages=5)
        r = client.post(base, json={"cookies": NETSCAPE})
        assert r.status_code == 200, r.text
        assert r.json()["test"]["ok"] is True and "SECRET" not in r.text
        st = client.get(base).json()
        assert st["has_cookie"] and st["names"] and "SECRET" not in json.dumps(st)
        r = client.post(base + "/settings", json={"per_hour": 40, "test_url": "https://evil.example/x"})
        assert r.status_code == 400
        assert client.post(base + "/settings", json={"per_hour": 40}).json()["per_hour"] == 40
        assert client.post(base + "/test").json()["pages"] == 2
        assert client.delete(base).json()["has_cookie"] is False
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)
