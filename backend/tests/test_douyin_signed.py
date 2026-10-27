"""Douyin signed web API + shared visitor jar (owner 15/10)."""
import asyncio
import json

import fakeredis
import pytest

from app.services import douyin_extractor as d
from app.services import douyin_visitor as V
from app.services.douyin_sign import abogus, detail_request, web_signature

COOKIES = {"UIFID_TEMP": "uif-secret-1", "s_v_web_id": "verify_x", "ttwid": "tt-secret"}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


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


# ── signing ──────────────────────────────────────────────────────────────────

def test_web_signature_matches_reference_vector():
    # Golden value from the upstream websign.sign() with the same inputs.
    q, h = web_signature([("aweme_id", "7"), ("a_bogus", "a/b=")], "UIF", timestamp=1760000000)
    assert q == ("aweme_id=7&a_bogus=a%2Fb%3D&uifid=UIF&timestamp=1760000000"
                 "&x-secsdk-web-signature=6455d5db30716b651b898d3fa7e25338")
    assert h == {"uifid": "UIF", "x-secsdk-web-signature": "6455d5db30716b651b898d3fa7e25338",
                 "x-secsdk-web-expire": "1760000000"}


def test_a_bogus_decodes_as_well_formed():
    v = abogus.ABogus(UA).get_value("aweme_id=1&aid=6383")
    assert abogus.structure_error(v) is None


def test_detail_request_needs_visitor_id_and_numeric_id():
    assert detail_request("123", {"ttwid": "x"}, UA) is None
    assert detail_request("12a", COOKIES, UA) is None
    url, headers = detail_request("7696039395691101618", COOKIES, UA)
    assert url.startswith("https://www.douyin.com/aweme/v1/web/aweme/detail/?")
    assert "aweme_id=7696039395691101618" in url and "a_bogus=" in url and "verifyFp=verify_x" in url
    assert url.rsplit("&", 1)[1].startswith("x-secsdk-web-signature=")
    assert headers["uifid"] == "uif-secret-1" and headers["User-Agent"] == UA


# ── visitor jar ──────────────────────────────────────────────────────────────

def test_jar_is_encrypted_at_rest(rc):
    V.store_jar(UA, COOKIES, rc)
    raw = rc.get(V.JAR_KEY)
    assert raw.startswith("enc:v1:") and "uif-secret-1" not in raw
    assert V.get_jar(rc) == (UA, COOKIES)
    assert "uif-secret-1" not in json.dumps(V.status(rc))


def test_settings_never_return_the_key(rc):
    out = V.save_settings(mint_url="https://mint.example/", mint_key="k-secret", rc=rc)
    assert out["mint_url"] == "https://mint.example" and out["mint_key_set"] is True
    assert "k-secret" not in json.dumps(out) and "k-secret" not in rc.get(V.SETTINGS_KEY)
    with pytest.raises(ValueError):
        V.save_settings(mint_url="ftp://x", rc=rc)
    with pytest.raises(ValueError):
        V.save_settings(per_min=0, rc=rc)


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._b


def test_missing_jar_is_minted_once_then_cools_down(rc, monkeypatch):
    import httpx
    calls = []
    V.save_settings(mint_url="https://mint.example", mint_key="k", rc=rc)

    def post(url, headers, timeout):
        calls.append((url, headers["X-Mint-Key"]))
        return _Resp(200, {"ua": UA.replace("Chrome/", "HeadlessChrome/"), "cookies": COOKIES})

    monkeypatch.setattr(httpx, "post", post)
    ua, ck = V.get_jar(rc)
    assert calls == [("https://mint.example/mint/douyin", "k")] and ck == COOKIES and "Headless" not in ua
    assert V.get_jar(rc) is not None and len(calls) == 1      # reused, not re-minted

    rc.delete(V.JAR_KEY)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"ua": UA, "cookies": {"ttwid": "x"}}))
    assert V.get_jar(rc) is None
    assert "UIFID" in V.status(rc)["last_mint_error"] and V.status(rc)["cooling_down"]
    monkeypatch.setattr(httpx, "post", post)
    assert V.get_jar(rc) is None                               # cooldown: no new attempt


def test_no_mint_service_means_skip(rc):
    assert V.get_jar(rc) is None


def test_old_jar_is_replaced(rc, monkeypatch):
    import httpx
    V.save_settings(mint_url="https://m", mint_key="k", max_age_h=1, rc=rc)
    V.store_jar(UA, {"UIFID_TEMP": "old"}, rc)
    meta = json.loads(rc.get(V.META_KEY)); meta["minted_at"] -= 7200; rc.set(V.META_KEY, json.dumps(meta))
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"ua": UA, "cookies": COOKIES}))
    assert V.get_jar(rc)[1] == COOKIES


def test_refusals_drop_jar_then_pause_when_fresh_jar_also_refused(rc):
    V.store_jar(UA, COOKIES, rc)
    for _ in range(3):
        V.report(False, "HTTP 200 empty", rc)
    assert rc.get(V.JAR_KEY) is None and not rc.get(V.PAUSE_KEY)
    V.store_jar(UA, COOKIES, rc)                               # the re-mint
    for _ in range(3):
        V.report(False, "empty 200", rc)
    assert rc.get(V.PAUSE_KEY) and V.get_jar(rc) is None
    assert 0 < V.status(rc)["paused_for_s"] <= 30 * 60


def test_success_resets_the_refusal_chain(rc):
    V.store_jar(UA, COOKIES, rc)
    for _ in range(3):
        V.report(False, "x", rc)
    V.store_jar(UA, COOKIES, rc)
    V.report(True, rc=rc)
    for _ in range(3):
        V.report(False, "x", rc)
    assert not rc.get(V.PAUSE_KEY) and rc.get(V.JAR_KEY) is None


def test_rate_caps(rc):
    V.save_settings(per_min=2, rc=rc)
    assert [V.take_slot(rc) for _ in range(3)] == [True, True, False]


# ── extractor step ───────────────────────────────────────────────────────────

class _Http:
    def __init__(self, status, body):
        self.status_code, self.text = status, body


DETAIL = {"aweme_detail": {
    "desc": "奶爸计划", "music": {"play_url": {"url_list": ["https://a/m.mp3"]}},
    "video": {"duration": 61000, "cover": {"url_list": ["https://a/c.jpg"]},
              "play_addr": {"url_list": ["https://v/default"]},
              "bit_rate": [{"bit_rate": 900, "play_addr": {"height": 720, "url_list": ["https://v/720"]}},
                           {"bit_rate": 2000, "play_addr": {"height": 1080, "url_list": ["https://v/1080"]}}]}}}


def _with_http(rc, monkeypatch, resp):
    from curl_cffi import requests as cr
    V.store_jar(UA, COOKIES, rc)
    monkeypatch.setattr(cr, "get", lambda *a, **k: resp)


def test_signed_step_maps_detail(rc, monkeypatch):
    _with_http(rc, monkeypatch, _Http(200, json.dumps(DETAIL)))
    r = _run(d._try_signed_web_api("7696039395691101618"))
    # quality #4 (27/10): the sharpest H.264 bit_rate entry by default, not play_addr
    assert r["provider"] == "douyin_signed" and r["direct_mp4_url"] == "https://v/1080"
    assert r["duration"] == 61 and r["thumbnail_url"] == "https://a/c.jpg" and r["audio_url"] == "https://a/m.mp3"
    assert _run(d._try_signed_web_api("7696039395691101618", "video_4k"))["direct_mp4_url"] == "https://v/1080"
    assert _run(d._try_signed_web_api("7696039395691101618", "mp3_128"))["direct_mp4_url"] == "https://a/m.mp3"
    assert V.status(rc)["ok"] == 3


def test_filtered_video_is_not_a_refusal(rc, monkeypatch):
    _with_http(rc, monkeypatch, _Http(200, json.dumps({"aweme_detail": None, "filter_detail": {"aweme_id": "1"}})))
    assert _run(d._try_signed_web_api("1")) is None
    assert V.status(rc)["fail"] == 0


def test_empty_200_and_403_count_as_refusals(rc, monkeypatch):
    _with_http(rc, monkeypatch, _Http(200, ""))
    assert _run(d._try_signed_web_api("1")) is None
    from curl_cffi import requests as cr
    monkeypatch.setattr(cr, "get", lambda *a, **k: _Http(403, "Blocked by ArgusSecurityPlugin Uifid Not Found"))
    assert _run(d._try_signed_web_api("1")) is None
    assert V.status(rc)["fail"] == 2 and V.status(rc)["fail_streak"] == 2


def test_rate_cap_skips_without_calling(rc, monkeypatch):
    from curl_cffi import requests as cr
    V.store_jar(UA, COOKIES, rc)
    V.save_settings(per_min=1, rc=rc)
    V.take_slot(rc)
    monkeypatch.setattr(cr, "get", lambda *a, **k: pytest.fail("must not call Douyin over the cap"))
    assert _run(d._try_signed_web_api("1")) is None


def test_signed_step_runs_before_ytdlp(rc, monkeypatch):
    async def signed(vid, q):
        return {"provider": "douyin_signed", "direct_mp4_url": "https://v/x"}

    async def ytdlp(*a, **k):
        pytest.fail("yt-dlp must not run when the signed step succeeds")

    monkeypatch.setattr(d, "_try_signed_web_api", signed)
    monkeypatch.setattr(d, "_try_ytdlp", ytdlp)
    r = _run(d.extract_douyin_video("https://www.douyin.com/video/7696039395691101618"))
    assert r["provider"] == "douyin_signed"


def test_no_jar_falls_through_quietly(rc, monkeypatch):
    from curl_cffi import requests as cr
    monkeypatch.setattr(cr, "get", lambda *a, **k: pytest.fail("no jar → no call"))
    assert _run(d._try_signed_web_api("1")) is None


def test_mint_now_reports_success_despite_zero_counter(rc, monkeypatch):
    import httpx
    V.save_settings(mint_url="https://m", mint_key="k", rc=rc)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(200, {"ua": UA, "cookies": COOKIES}))
    out = V.mint_now(rc)
    assert out["ok"] is True and out["has_jar"] is True
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(502, {"code": "no_uifid"}))
    out = V.mint_now(rc)
    assert out["ok"] is False and "502" in out["last_mint_error"]
