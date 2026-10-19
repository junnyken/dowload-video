"""Proxy health (19/10): every admin proxy is checked, failures say why,
dead proxies are cooled so the pool fails over before a download hits them,
alive ones are released, state changes go to Telegram — never a credential."""
from types import SimpleNamespace

import fakeredis
import pytest

MAIN = "http://user1:secretA@gw.dataimpulse.com:823"
BACKUP = "http://user2:secretB@proxy-cheap.example:9000"


@pytest.fixture()
def fr(monkeypatch):
    import app.core.redis_client as rc
    import app.core.proxy_pool as pp
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rc, "get_redis", lambda: r)
    monkeypatch.setattr(pp, "get_redis", lambda: r)
    return r


@pytest.fixture()
def tg(monkeypatch):
    import app.core.notifications as n
    sent = []
    monkeypatch.setattr(n, "send_telegram_message_sync", lambda m: sent.append(m))
    return sent


def _fake_net(monkeypatch, behaviour):
    """behaviour: {proxy: 'ok' | 'yt_blocked' | Exception}"""
    from app.core import proxy_health as ph

    def get(url, proxy):
        b = behaviour[proxy]
        if isinstance(b, Exception):
            raise b
        if "ipinfo" in url:
            return SimpleNamespace(status_code=200, json=lambda: {"ip": "203.0.113.9", "country": "VN"}, text="")
        return SimpleNamespace(status_code=204 if b == "ok" else 403, text="")
    monkeypatch.setattr(ph, "_get", get)


@pytest.mark.parametrize("text,kind", [
    ("ProxyError 407 Proxy Authentication Required", "auth_failed"),
    ("CONNECT 407 TRAFFIC_EXHAUSTED", "traffic_exhausted"),
    ("Operation timed out after 15001 milliseconds", "timeout"),
    ("Failed to connect to proxy-cheap.example port 9000: Connection refused", "connect_failed"),
    ("Received HTTP code 502 from proxy after CONNECT tunnel", "tunnel_failed"),
])
def test_classify(text, kind):
    from app.core.proxy_health import classify_error
    assert classify_error(text) == kind


def test_dead_main_is_cooled_so_backup_serves_and_alerts_once(fr, tg, monkeypatch):
    from app.core import proxy_health as ph, proxy_pool as pp
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("youtube", BACKUP)
    _fake_net(monkeypatch, {MAIN: RuntimeError("CONNECT 407 TRAFFIC_EXHAUSTED"), BACKUP: "ok"})
    out = ph.run()
    assert out["total"] == 2 and out["alive"] == 1
    dead = next(r for r in out["results"] if r["index"] == 0)
    assert dead["kind"] == "traffic_exhausted" and dead["label"] == "Hết dung lượng"
    assert pp.get_proxy_from_pool("youtube") == BACKUP          # failover before any download
    assert len(tg) == 1 and "Proxy chết" in tg[0]
    for secret in ("secretA", "secretB", "user1", "user2"):
        assert secret not in str(out) and secret not in tg[0]
    ph.run()
    assert len(tg) == 1                                          # no repeat while still dead


def test_recovery_clears_cooldown_and_alerts(fr, tg, monkeypatch):
    from app.core import proxy_health as ph, proxy_pool as pp
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("youtube", BACKUP)
    _fake_net(monkeypatch, {MAIN: RuntimeError("Operation timed out"), BACKUP: "ok"})
    ph.run()
    _fake_net(monkeypatch, {MAIN: "ok", BACKUP: "ok"})
    out = ph.run()
    assert out["alive"] == 2 and pp.get_proxy_from_pool("youtube") == MAIN
    assert "sống lại" in tg[-1]


def test_youtube_pool_also_needs_youtube_to_answer(fr, tg, monkeypatch):
    from app.core import proxy_health as ph, proxy_pool as pp
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("tiktok", BACKUP)
    _fake_net(monkeypatch, {MAIN: "yt_blocked", BACKUP: "yt_blocked"})
    rows = {r["pool"]: r for r in ph.run()["results"]}
    assert rows["youtube"]["alive"] is False and rows["youtube"]["kind"] == "youtube_blocked"
    assert rows["tiktok"]["alive"] is True                       # YouTube is not tested for TikTok's


def test_a_proxy_in_two_pools_is_checked_once(fr, tg, monkeypatch):
    from app.core import proxy_health as ph, proxy_pool as pp
    calls = []
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("all", MAIN)
    _fake_net(monkeypatch, {MAIN: "ok"})
    real = ph._get
    monkeypatch.setattr(ph, "_get", lambda u, p: calls.append(u) or real(u, p))
    out = ph.run()
    assert len(out["results"]) == 2
    assert sum("ipinfo" in u for u in calls) == 1


def test_list_endpoint_shows_health_priority_and_cooldown(fr, tg, monkeypatch):
    import asyncio
    from app.api import admin
    from app.core import proxy_health as ph, proxy_pool as pp
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("youtube", BACKUP)
    _fake_net(monkeypatch, {MAIN: RuntimeError("407 Proxy Authentication Required"), BACKUP: "ok"})
    ph.run()
    out = asyncio.run(admin.proxy_pool_list("youtube"))
    a, b = out["proxies"]
    assert a["priority"] == "main" and a["health"]["alive"] is False and a["cooling_s"] > 0
    assert b["priority"] == "backup" and b["health"]["alive"] is True and b["cooling_s"] == 0
    assert "secretA" not in str(out)


# ── input normalisation (19/10: a curl tail was pasted into the pool) ────────

@pytest.mark.parametrize("raw,want", [
    ("http://u1:pw@thehub.proxy-cheap.com:8080 https://ipv4.icanhazip.com", "http://u1:pw@thehub.proxy-cheap.com:8080"),
    ("curl -v -x http://u1:pw@thehub.proxy-cheap.com:8080 https://ipv4.icanhazip.com", "http://u1:pw@thehub.proxy-cheap.com:8080"),
    ("thehub.proxy-cheap.com:8080:u1:pw", "http://u1:pw@thehub.proxy-cheap.com:8080"),
    ("u1:pw@gw.dataimpulse.com:823", "http://u1:pw@gw.dataimpulse.com:823"),
    ("  'socks5://u1:pw@1.2.3.4:1080/'  ", "socks5://u1:pw@1.2.3.4:1080"),
    ("1.2.3.4:3128", "http://1.2.3.4:3128"),
])
def test_normalize_proxy_url(raw, want):
    from app.core.proxy_pool import normalize_proxy_url
    assert normalize_proxy_url(raw) == want


@pytest.mark.parametrize("raw", ["", "   ", "https://ipv4.icanhazip.com/path", "thehub.proxy-cheap.com", "u:p@host:99999", "not a proxy"])
def test_normalize_rejects_garbage(raw):
    from app.core.proxy_pool import normalize_proxy_url
    with pytest.raises(ValueError):
        normalize_proxy_url(raw)


def test_unsupported_syntax_is_labelled():
    from app.core.proxy_health import classify_error, KIND_LABEL
    k = classify_error("ProxyError: Failed to perform, curl: (5) Unsupported proxy syntax in '?': Malformed")
    assert k == "invalid_format" and "định dạng" in KIND_LABEL[k]
