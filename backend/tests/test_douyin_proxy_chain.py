"""Douyin free steps use the admin "douyin" proxy before the managed (Apify)
fallback (owner 15/10)."""
import asyncio

from app.services import douyin_extractor as d


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_direct_success_never_uses_proxy(monkeypatch):
    calls = []

    async def once(vid, q, proxy):
        calls.append(proxy)
        return {"provider": "iesdouyin", "direct_mp4_url": "https://x/play"}

    monkeypatch.setattr(d, "_try_iesdouyin_share_once", once)
    monkeypatch.setattr(d, "_douyin_pool_proxy", lambda: "http://u:p@h:1")
    assert _run(d._try_iesdouyin_share("123"))["provider"] == "iesdouyin"
    assert calls == [None]


def test_proxy_retry_after_direct_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(d, "_proxy_may_help", lambda: True)

    async def once(vid, q, proxy):
        calls.append(proxy)
        return {"provider": "iesdouyin", "direct_mp4_url": "https://x/play"} if proxy else None

    monkeypatch.setattr(d, "_try_iesdouyin_share_once", once)
    monkeypatch.setattr(d, "_douyin_pool_proxy", lambda: "http://u:p@h:1")
    r = _run(d._try_iesdouyin_share("123"))
    assert calls == [None, "http://u:p@h:1"] and r["provider"] == "iesdouyin_proxy"


def test_no_proxy_configured_skips_retry(monkeypatch):
    calls = []

    async def once(vid, q, proxy):
        calls.append(proxy)
        return None

    monkeypatch.setattr(d, "_try_iesdouyin_share_once", once)
    monkeypatch.setattr(d, "_douyin_pool_proxy", lambda: None)
    assert _run(d._try_iesdouyin_share("123")) is None
    assert calls == [None]


def test_pool_proxy_preferred_over_env(monkeypatch):
    import app.core.proxy_pool as pp
    monkeypatch.setattr(pp, "get_proxy_from_pool", lambda p: "http://pool:1" if p == "douyin" else None)
    assert d._douyin_pool_proxy() == "http://pool:1"


def test_env_fallback_when_pool_empty(monkeypatch):
    import app.core.proxy_pool as pp
    import app.core.proxy_manager as pm
    monkeypatch.setattr(pp, "get_proxy_from_pool", lambda p: None)
    monkeypatch.setattr(pm, "IPROYAL_PROXY_CN", "http://env:2")
    assert d._douyin_pool_proxy() == "http://env:2"


def test_no_retry_when_measurement_says_proxies_do_not_help(monkeypatch):
    calls = []

    async def once(vid, q, proxy):
        calls.append(proxy)
        return None

    monkeypatch.setattr(d, "_try_iesdouyin_share_once", once)
    monkeypatch.setattr(d, "_douyin_pool_proxy", lambda: "http://u:p@h:1")
    monkeypatch.setattr(d, "_proxy_may_help", lambda: False)
    assert _run(d._try_iesdouyin_share("123")) is None
    assert calls == [None]


def test_proxy_may_help_reads_last_report(monkeypatch):
    import json
    import fakeredis
    import app.core.redis_client as rcmod
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rcmod, "get_redis", lambda: r)
    assert d._proxy_may_help() is True                      # never measured
    r.set("china:proxy_test:last", json.dumps({"proxies": [{"douyin_meta": False}]}))
    assert d._proxy_may_help() is False
    r.set("china:proxy_test:last", json.dumps({"proxies": [{"douyin_meta": False}, {"douyin_meta": True}]}))
    assert d._proxy_may_help() is True
