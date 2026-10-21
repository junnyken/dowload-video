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
    added = C.store(NETSCAPE, "Chrome nhà", rc)
    assert added["names"] == ["UIFID", "odin_tt", "s_v_web_id", "ttwid"]
    raw = rc.hget(C.JARS_KEY, added["id"])
    assert "SECRET" not in raw and "GITHUB" not in raw                 # encrypted, other sites dropped
    st = C.status(rc)
    assert st["has_cookie"] and st["live"] == 1
    assert st["cookies"][0]["names"] == added["names"] and st["cookies"][0]["label"] == "Chrome nhà"
    assert "SECRET" not in json.dumps(st)


def _other(n):
    return NETSCAPE.replace("tt-SECRET", f"tt{n}-SECRET")


def test_pool_adds_replaces_same_ttwid_and_migrates_the_first_version(rc):  # noqa: F811
    from app.services.douyin_visitor import _enc
    rc.set(C.OLD_JAR_KEY, _enc(json.dumps({"cookies": {"ttwid": "old", "odin_tt": "o", "UIFID": "u"}})))
    rc.set(C.OLD_META_KEY, json.dumps({"saved_at": 1, "names": ["UIFID", "odin_tt", "ttwid"], "ok": 5}))
    st = C.status(rc)
    assert len(st["cookies"]) == 1 and st["cookies"][0]["ok"] == 5 and not rc.get(C.OLD_JAR_KEY)
    a = C.store(NETSCAPE, "", rc)
    b = C.store(NETSCAPE, "lại", rc)
    assert a["id"] == b["id"] and len(C.status(rc)["cookies"]) == 2
    C.store(_other(2), "", rc)
    assert len(C.status(rc)["cookies"]) == 3
    assert C.drop(a["id"], rc) and not C.drop(a["id"], rc)
    assert len(C.status(rc)["cookies"]) == 2


# ── listing ──────────────────────────────────────────────────────────────────

def test_lists_the_whole_channel_page_by_page(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc=rc)
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
    C.store(NETSCAPE, rc=rc)
    seen = []
    _pages(monkeypatch, n_pages=10, seen=seen)
    listed, _ = C.list_channel(SEC, 30, rc=rc)
    assert len(listed["videos"]) == 30 and listed["has_more"] is True and len(seen) == 2


def test_later_page_refused_keeps_what_was_read(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc=rc)
    _pages(monkeypatch, n_pages=5, refuse_from=2)
    listed, _ = C.list_channel(SEC, 500, rc=rc)
    assert len(listed["videos"]) == 34 and listed["has_more"] is True
    assert C.status(rc)["cookies"][0]["fail"] == 1


def test_first_page_refused_three_times_marks_the_cookie_dead(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc=rc)
    _pages(monkeypatch, refuse_from=0)
    for _ in range(3):
        assert C.list_channel(SEC, 500, use_cache=False, rc=rc) == (None, "empty 200")
    assert C.status(rc)["cookies"][0]["dead"] and not C.usable(rc)
    assert C.list_channel(SEC, 500, use_cache=False, rc=rc) == (None, "unusable")


def test_refused_cookie_moves_on_to_the_next_and_cookies_take_turns(rc, monkeypatch):  # noqa: F811
    a = C.store(NETSCAPE, "a", rc)["id"]
    b = C.store(_other(2), "b", rc)["id"]
    seen = []
    from curl_cffi import requests as cr

    def get(url, **kw):
        t = kw["cookies"]["ttwid"]
        seen.append(t)
        if t == "tt-SECRET":
            return _R("")                                              # cookie a is refused
        return _R({"status_code": 0, "has_more": 0, "max_cursor": 1, "aweme_list": [_aweme("7001")]})
    monkeypatch.setattr(cr, "get", get)
    for _ in range(4):
        listed, _why = C.list_channel(SEC, 500, use_cache=False, rc=rc)
        assert [v["id"] for v in listed["videos"]] == ["7001"]
    assert "tt2-SECRET" in seen and seen.count("tt-SECRET") >= 1        # both asked first in turn
    st = {c["id"]: c for c in C.status(rc)["cookies"]}
    assert st[b]["ok"] == 4 and st[a]["fail"] >= 1
    # a dies after 3 refusals, then only b is asked
    for _ in range(3):
        C._report(rc, a, False, "x")
    seen.clear()
    C.list_channel(SEC, 500, use_cache=False, rc=rc)
    assert seen == ["tt2-SECRET"]


def test_hourly_cap_and_switch_off(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, rc=rc)
    _pages(monkeypatch, n_pages=5)
    C.save_settings(per_hour=2, rc=rc)
    listed, _ = C.list_channel(SEC, 500, rc=rc)
    assert listed["pages"] == 2 and listed["has_more"] is True
    rc.delete(C.LIST_KEY.format(SEC))
    assert C.list_channel(SEC, 500, rc=rc) == (None, "rate_cap")
    C.save_settings(enabled=False, per_hour=100, rc=rc)
    assert C.list_channel(SEC, 500, use_cache=False, rc=rc) == (None, "unusable")


def test_test_reads_two_pages(rc, monkeypatch):  # noqa: F811
    assert C.test("nope", rc)["reason"] == "no_cookie"
    cid = C.store(NETSCAPE, rc=rc)["id"]
    _pages(monkeypatch, n_pages=5)
    out = C.test(cid, rc)
    assert out["ok"] and out["pages"] == 2 and out["items"] == 36
    assert C.status(rc)["cookies"][0]["last_test"]["ok"] is True


def test_probe_all_kills_a_dead_cookie_at_once_and_revives_a_working_one(rc, monkeypatch):  # noqa: F811
    good = C.store(NETSCAPE, "good", rc)["id"]
    bad = C.store(_other(2), "bad", rc)["id"]
    for _ in range(3):
        C._report(rc, good, False, "old")                              # dead from before
    from curl_cffi import requests as cr
    monkeypatch.setattr(cr, "get", lambda url, **kw: _R("") if kw["cookies"]["ttwid"] == "tt2-SECRET" else
                        _R({"status_code": 0, "has_more": 1, "max_cursor": 1, "aweme_list": [_aweme("7001")]}))
    slept = []
    out = C.probe_all(rc, sleep=slept.append)
    assert slept == [C.PROBE_RETRY_S]                                  # the failure was asked twice
    assert out["tested"] == 2 and out["ok"] == 1 and out["dead"] == 1 and not out["ip_blocked"]
    st = {c["id"]: c for c in C.status(rc)["cookies"]}
    assert not st[good]["dead"] and st[bad]["dead"]                    # one probe is enough
    assert st[bad]["last_test"]["auto"] is True
    assert C.status(rc)["auto"]["tested"] == 2
    assert not C.probe_due(rc)                                         # next run in ~48 h
    rc.set(C.AUTO_KEY, json.dumps({"at": 1}))
    assert C.probe_due(rc)


def test_probe_all_does_not_kill_when_every_cookie_fails(rc, monkeypatch):  # noqa: F811
    C.store(NETSCAPE, "", rc)
    C.store(_other(2), "", rc)
    from curl_cffi import requests as cr
    monkeypatch.setattr(cr, "get", lambda url, **kw: _R(""))
    out = C.probe_all(rc, sleep=lambda s: None)
    assert out["ip_blocked"] and out["dead"] == 0
    assert all(not c["dead"] for c in C.status(rc)["cookies"])


def test_probe_task_is_scheduled():
    from app.core.celery_app import celery_app
    sched = celery_app.conf.beat_schedule["probe-douyin-channel-cookies-every-48h"]
    assert sched["task"] == "probe_douyin_channel_cookies"
    import app.tasks.video_tasks  # noqa: F401
    assert "probe_douyin_channel_cookies" in celery_app.tasks


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
    C.store(NETSCAPE, rc=rc)
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
    C.store(NETSCAPE, rc=rc)
    V.store_jar("Mozilla/5.0 Chrome/131.0.0.0", {"UIFID_TEMP": "visitor", "s_v_web_id": "v"}, rc)
    seen = []
    _pages(monkeypatch, n_pages=3, seen=seen)
    body = client.post("/api/v1/channel/preview", json={"url": PROFILE, "limit": 500}).json()
    assert body["hasMore"] is True and body["moreHint"] == "douyin_extension"
    assert seen == [(0, None)]                                         # the visitor jar, one page


def test_refused_cookie_falls_back_to_the_first_page(on, rc, monkeypatch, _no_user):  # noqa: F811
    from curl_cffi import requests as cr
    from app.services import douyin_visitor as V
    C.store(NETSCAPE, rc=rc)
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
    C.store(NETSCAPE, rc=rc)
    assert client.get("/api/v1/channel/preview/caps").json() == {"douyinWhole": True}
    C.save_settings(enabled=False, rc=rc)
    assert client.get("/api/v1/channel/preview/caps").json() == {"douyinWhole": False}


def test_admin_endpoints_never_return_values(rc, monkeypatch):  # noqa: F811
    from app.api.admin_china_platforms import verify_admin
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        base = "/api/v1/admin/china-platforms/douyin/channel-cookie"
        r = client.post(base, json={"cookies": "ttwid=SECRET-only"})
        assert r.status_code == 400 and "SECRET" not in r.text
        _pages(monkeypatch, n_pages=5)
        r = client.post(base, json={"cookies": NETSCAPE, "label": "Chrome nhà"})
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        assert r.json()["test"]["ok"] is True and "SECRET" not in r.text
        st = client.get(base).json()
        assert st["has_cookie"] and st["cookies"][0]["names"] and "SECRET" not in json.dumps(st)
        r = client.post(base + "/settings", json={"per_hour": 40, "test_url": "https://evil.example/x"})
        assert r.status_code == 400
        assert client.post(base + "/settings", json={"per_hour": 40}).json()["per_hour"] == 40
        assert client.post(f"{base}/{cid}/test").json()["pages"] == 2
        assert client.post(f"{base}/nope/test").status_code == 404
        assert client.delete(f"{base}/{cid}").json()["has_cookie"] is False
        assert client.delete(f"{base}/{cid}").status_code == 404
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)
