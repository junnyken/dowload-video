"""Proxy pool priority + failover (19/10): the first proxy added is the main
one, the next ones are backups used only while it cools down after running out
of traffic; it was a random pick that ignored health."""
import fakeredis
import pytest


@pytest.fixture()
def fr(monkeypatch):
    import app.core.redis_client as rc
    import app.core.proxy_pool as pp
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rc, "get_redis", lambda: r)
    monkeypatch.setattr(pp, "get_redis", lambda: r)
    return r


MAIN, BACKUP = "http://u:p@gw.dataimpulse.com:823", "http://u:p@proxy-cheap.example:9000"


def test_main_proxy_first_always(fr):
    from app.core import proxy_pool as pp
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("youtube", BACKUP)
    assert {pp.get_proxy_from_pool("youtube") for _ in range(30)} == {MAIN}


def test_backup_while_main_cools_then_main_again(fr):
    from app.core import proxy_pool as pp
    from app.core.proxy_manager import mark_proxy_bad, _proxy_id
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("youtube", BACKUP)
    mark_proxy_bad(MAIN, ttl=60)
    assert pp.get_proxy_from_pool("youtube") == BACKUP
    fr.delete(f"proxy_bad:{_proxy_id(MAIN)}")          # cooldown over
    assert pp.get_proxy_from_pool("youtube") == MAIN


def test_all_cooled_returns_the_first(fr):
    from app.core import proxy_pool as pp
    from app.core.proxy_manager import mark_proxy_bad
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("youtube", BACKUP)
    mark_proxy_bad(MAIN)
    mark_proxy_bad(BACKUP)
    assert pp.get_proxy_from_pool("youtube") == MAIN


def test_empty_pool_falls_back_to_env_then_none(fr, monkeypatch):
    from app.core import proxy_pool as pp
    assert pp.get_proxy_from_pool("tiktok") is None
    monkeypatch.setenv("PROXY_POOL_TT", "http://env:1")
    assert pp.get_proxy_from_pool("tiktok") == "http://env:1"


def test_next_proxy_after_failure_cools_for_an_hour_and_alerts_once(fr, monkeypatch):
    from app.core import proxy_pool as pp
    from app.core.proxy_manager import _proxy_id
    import app.core.notifications as notif
    sent = []
    monkeypatch.setattr(notif, "send_telegram_message_sync", lambda m: sent.append(m))
    pp.add_proxy("youtube", MAIN)
    pp.add_proxy("youtube", BACKUP)
    assert pp.next_proxy_after_failure("youtube", MAIN, "407 traffic_exhausted") == BACKUP
    assert 3500 < fr.ttl(f"proxy_bad:{_proxy_id(MAIN)}") <= 3600
    assert len(sent) == 1 and "dự phòng" in sent[0]
    assert MAIN not in sent[0] and BACKUP not in sent[0]           # credentials never in Telegram
    pp.next_proxy_after_failure("youtube", MAIN, "407")
    assert len(sent) == 1                                          # once an hour


def test_no_backup_means_no_failover(fr):
    from app.core import proxy_pool as pp
    pp.add_proxy("youtube", MAIN)
    assert pp.next_proxy_after_failure("youtube", MAIN, "407") is None


def test_youtube_layer_uses_the_failover(fr):
    """The android_vr proxy loop asks for the next proxy instead of giving up."""
    import inspect
    from app.services import downloader
    src = inspect.getsource(downloader)
    assert 'next_proxy_after_failure as _npf' in src and '_npf("youtube", _yt_proxy' in src


def test_socks5h_credentials_are_masked_in_list(fr):
    import asyncio
    from app.api import admin
    from app.core import proxy_pool as pp
    pp.add_proxy("youtube", "socks5h://user9:topsecret@1.2.3.4:1080")
    out = asyncio.run(admin.proxy_pool_list("youtube"))
    assert "topsecret" not in str(out) and "user9" not in str(out)


def test_partner_quota_refund(fr):
    from datetime import datetime, timezone
    from app.core import partner_quota as pq
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    pq.consume("t1", "k1", 10, 10, now=now)
    pq.refund("t1", "k1", now=now)
    assert pq.usage("t1", "k1", now=now) == {"month": 0, "today_key": 0}
