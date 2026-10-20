"""Douyin profile listing through the signed web API (20/10)."""
import asyncio
import json

import fakeredis
import pytest

from app.services import douyin_extractor as d
from app.services import douyin_visitor as V
from app.services import downloader
from app.services.douyin_sign import post_request

COOKIES = {"UIFID_TEMP": "uif-secret-1", "s_v_web_id": "verify_x", "ttwid": "tt-secret"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
SEC = "MS4wLjABAAAAkQs2tRzfg_eQ94ErmIEe4ba2ADzMVA7N7PbV-92qtgw3tLxfFB15eEW4hYgnRYPh"
SHARE_URL = f"https://www.iesdouyin.com/share/user/{SEC}?with_sec_did=1&sec_uid={SEC}&from_ssr=1"


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    import app.core.redis_client as R
    monkeypatch.setattr(R, "get_redis", lambda: r)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key-for-encryption")
    from app.core import cookie_crypto
    for f in vars(cookie_crypto).values():
        if hasattr(f, "cache_clear"):
            f.cache_clear()
    return r


class _Http:
    def __init__(self, status, body):
        self.status_code, self.text = status, body


def _aweme(vid, *, images=False, play=True):
    a = {"aweme_id": vid, "desc": f"clip {vid}", "author": {"nickname": "MaodVlog"},
         "music": {"play_url": {"url_list": ["https://a/m.mp3"]}},
         "video": {"duration": 30000, "cover": {"url_list": ["https://a/c.jpg"]},
                   "play_addr": {"url_list": ["https://v/" + vid] if play else []},
                   "bit_rate": [{"bit_rate": 2000, "play_addr": {"height": 1080, "url_list": ["https://v/hd" + vid]}}]}}
    if images:
        a["images"] = [{"url_list": ["https://a/i.jpg"]}]
    return a


PAGE = {"status_code": 0, "has_more": 1, "max_cursor": 1786961099000,
        "aweme_list": [_aweme("7001"), _aweme("7002", images=True), _aweme("7003", play=False),
                       _aweme("7004"), _aweme("7001")]}


def _with_http(rc, monkeypatch, resp, seen=None):
    from curl_cffi import requests as cr
    V.store_jar(UA, COOKIES, rc)

    def get(url, **kw):
        if seen is not None:
            seen.append(url)
        return resp
    monkeypatch.setattr(cr, "get", get)


# ── signing ──────────────────────────────────────────────────────────────────

def test_post_request_signs_a_first_page_listing():
    assert post_request(SEC, {"ttwid": "x"}, UA) is None              # no visitor id
    assert post_request("bad uid/../", COOKIES, UA) is None
    url, headers = post_request(SEC, COOKIES, UA, 50)
    assert url.startswith("https://www.douyin.com/aweme/v1/web/aweme/post/?")
    assert f"sec_user_id={SEC}" in url and "max_cursor=0" in url and "count=50" in url
    assert "a_bogus=" in url and url.rsplit("&", 1)[1].startswith("x-secsdk-web-signature=")
    assert headers["Referer"] == f"https://www.douyin.com/user/{SEC}"


# ── listing step ─────────────────────────────────────────────────────────────

def test_listing_keeps_playable_videos_once_in_order(rc, monkeypatch):
    seen = []
    _with_http(rc, monkeypatch, _Http(200, json.dumps(PAGE)), seen)
    out, why = d.signed_profile_listing(SEC, 10)
    assert why == "" and out["channel_title"] == "MaodVlog" and out["has_more"] is True
    assert out["video_ids"] == ["7001", "7004"]
    assert out["videos"][1] == {"id": "7004", "title": "clip 7004", "thumbnail": "https://a/c.jpg",
                                "duration_sec": 30.0}
    assert len(seen) == 1 and V.status(rc)["ok"] == 1


def test_listing_respects_the_requested_count(rc, monkeypatch):
    _with_http(rc, monkeypatch, _Http(200, json.dumps(PAGE)))
    assert d.signed_profile_listing(SEC, 1)[0]["video_ids"] == ["7001"]


def test_listed_videos_skip_their_own_signed_call(rc, monkeypatch):
    seen = []
    _with_http(rc, monkeypatch, _Http(200, json.dumps(PAGE)), seen)
    d.signed_profile_listing(SEC, 10)
    V.save_settings(per_min=1, rc=rc)        # the listing used the only slot
    r = _run(d._try_signed_web_api("7004"))
    assert r["direct_mp4_url"] == "https://v/7004" and r["title"] == "clip 7004" and r["duration"] == 30
    assert _run(d._try_signed_web_api("7004", "video_4k"))["direct_mp4_url"] == "https://v/hd7004"
    assert len(seen) == 1                    # no detail call was made
    assert "uif-secret-1" not in json.dumps({k: rc.get(k) for k in rc.keys("douyin:signed:listed:*")})


def test_empty_200_counts_as_a_refusal(rc, monkeypatch):
    _with_http(rc, monkeypatch, _Http(200, '{"status_code":0}'))
    assert d.signed_profile_listing(SEC, 10) == (None, "empty")
    assert V.status(rc)["fail"] == 1


def test_no_jar_or_rate_cap_skips_without_calling(rc, monkeypatch):
    from curl_cffi import requests as cr
    monkeypatch.setattr(cr, "get", lambda *a, **k: pytest.fail("no call expected"))
    assert d.signed_profile_listing(SEC, 10) == (None, "no_jar")
    V.store_jar(UA, COOKIES, rc)
    V.save_settings(per_min=1, rc=rc)
    V.take_slot(rc)
    assert d.signed_profile_listing(SEC, 10) == (None, "rate_cap")


# ── channel scraper wiring ───────────────────────────────────────────────────

def _cap(monkeypatch, n):
    from app.services.china_platforms import channel_listing
    monkeypatch.setattr(channel_listing, "listing_cap", lambda ctx=None: n)


def test_channel_scraper_uses_signed_listing_first(rc, monkeypatch):
    _cap(monkeypatch, 100)
    _with_http(rc, monkeypatch, _Http(200, json.dumps(PAGE)))
    from app.services.china_platforms import integration
    monkeypatch.setattr(integration, "list_douyin_channel_via_access_layer",
                        lambda *a, **k: pytest.fail("Apify must not run"))
    out = downloader._scrape_douyin_channel(SHARE_URL, 20)
    assert out["channel_title"] == "MaodVlog" and out["total_queued"] == 2
    assert [e["url"] for e in out["entries"]] == ["https://www.douyin.com/video/7001",
                                                  "https://www.douyin.com/video/7004"]


def test_channel_scraper_caps_at_todays_allowance(rc, monkeypatch):
    _cap(monkeypatch, 1)
    _with_http(rc, monkeypatch, _Http(200, json.dumps(PAGE)))
    assert downloader._scrape_douyin_channel(SHARE_URL, 20)["total_queued"] == 1


def test_no_allowance_or_refusal_falls_through_to_apify(rc, monkeypatch):
    from app.services.china_platforms import integration
    apify = {"channel_title": "via apify", "entries": [], "total_found": 0, "total_queued": 0}
    monkeypatch.setattr(integration, "list_douyin_channel_via_access_layer", lambda *a, **k: apify)
    from curl_cffi import requests as cr

    _cap(monkeypatch, 0)
    monkeypatch.setattr(cr, "get", lambda *a, **k: pytest.fail("no call when nothing is allowed"))
    assert downloader._scrape_douyin_channel(SHARE_URL, 20) is apify

    _cap(monkeypatch, 100)
    _with_http(rc, monkeypatch, _Http(200, '{"status_code":0}'))
    assert downloader._scrape_douyin_channel(SHARE_URL, 20) is apify


# ── "use the extension" hint (20/10) ─────────────────────────────────────────

def test_hint_when_douyin_has_more_than_it_gave(rc, monkeypatch):
    _cap(monkeypatch, 100)
    _with_http(rc, monkeypatch, _Http(200, json.dumps(PAGE)))          # has_more=1, 2 playable
    out = downloader._scrape_douyin_channel(SHARE_URL, 100)
    assert out["more_hint"] == downloader.CHANNEL_HINT_DOUYIN_EXTENSION


def test_no_hint_when_our_own_cap_cut_the_list(rc, monkeypatch):
    _cap(monkeypatch, 1)                                                # allowance 1 < 2 listed
    _with_http(rc, monkeypatch, _Http(200, json.dumps(PAGE)))
    assert "more_hint" not in downloader._scrape_douyin_channel(SHARE_URL, 100)


def test_no_hint_when_the_channel_is_complete(rc, monkeypatch):
    _cap(monkeypatch, 100)
    _with_http(rc, monkeypatch, _Http(200, json.dumps({**PAGE, "has_more": 0})))
    assert "more_hint" not in downloader._scrape_douyin_channel(SHARE_URL, 100)


def test_channel_hint_store(rc):
    from app.core import channel_hints as H
    H.save_channel_hint("job-1", "douyin_extension", rc)
    H.save_channel_hint("job-2", "made_up", rc)                         # unknown hints are not stored
    assert H.channel_hints(["job-1", "job-2", "job-3"], rc) == {"job-1": "douyin_extension"}
    assert 0 < rc.ttl("channel:hint:job-1") <= 7 * 24 * 3600
    assert "tiện ích VidGrab" in H.hint_sentence("douyin_extension")
    assert H.hint_sentence("made_up") == ""


def test_jobs_endpoint_returns_the_hint_on_the_channel_row_only(rc, monkeypatch):
    from types import SimpleNamespace
    from app.api import routes
    from app.core.channel_hints import save_channel_hint
    rows = [
        {"id": "ch", "status": "success", "direct_mp4_url": None, "local_file_path": None,
         "original_url": SHARE_URL, "created_at": "2026-10-20T01:00:00+00:00"},
        {"id": "v1", "status": "success", "direct_mp4_url": "https://v/1", "local_file_path": None,
         "original_url": "https://www.douyin.com/video/7001", "created_at": "2026-10-20T01:00:01+00:00"},
    ]

    class _Q:
        def __getattr__(self, _name):
            return lambda *a, **k: self

        def execute(self):
            return SimpleNamespace(data=[dict(r) for r in rows])

    monkeypatch.setattr(routes, "get_supabase_client", lambda: SimpleNamespace(table=lambda _t: _Q()))
    save_channel_hint("ch", "douyin_extension", rc)
    save_channel_hint("v1", "douyin_extension", rc)       # a video row never shows it
    out = asyncio.new_event_loop().run_until_complete(routes.get_jobs_by_batch("b1"))
    by_id = {j["id"]: j for j in out["jobs"]}
    assert by_id["ch"]["channel_hint"] == "douyin_extension"
    assert "channel_hint" not in by_id["v1"]
