"""
Live re-test of pooled cookies (cookie_probe.retest_cookie + admin routes).

No network: fakeredis stands in for Redis and every platform probe is a stub.
"""

import base64
import json
import os
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

fakeredis = pytest.importorskip("fakeredis")

SECRET = "SUPERSECRETVALUE123"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    import app.core.redis_client as rc
    fake = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(rc, "_client", fake, raising=False)
    fake.flushall()
    yield fake


def _netscape(expiry: int) -> str:
    line = f".tiktok.com\tTRUE\t/\tTRUE\t{expiry}\tsessionid\t{SECRET}\n"
    return base64.b64encode(line.encode()).decode()


def _add(platform="tiktok", expiry=None, label="acc"):
    from app.core import cookie_pool as cp
    b64 = _netscape(expiry if expiry is not None else int(time.time()) + 86400 * 30)
    cp.add_cookie(platform, b64, label=label)
    return b64, cp._hash(b64)


def _expire_by_date(platform, b64):
    """Past expiry in meta + the 'expired' mark, as the daily check leaves it."""
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    rc = get_redis()
    h = cp._hash(b64)
    meta = cp._get_meta(rc, platform, h)
    meta["expires_at"] = int(time.time()) - 86400 * 3
    cp._set_meta(rc, platform, h, meta)
    cp.mark_cookie_expired(platform, b64)
    return h


class Probe:
    def __init__(self, verdict, message="m"):
        self.verdict, self.message, self.calls = verdict, message, 0

    def __call__(self, cookie_b64, proxy):
        self.calls += 1
        return self.verdict, self.message


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch):
    import app.core.proxy_pool as pp
    monkeypatch.setattr(pp, "get_proxy_from_pool", lambda platform: None)


def test_expired_by_date_cookie_can_be_retested_and_success_clears_mark():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, _ = _add()
    h = _expire_by_date("tiktok", b64)
    assert get_redis().get(f"cookie_health:tiktok:{h}") == "expired"

    probe = Probe("ok", "TikTok xác nhận đang đăng nhập")
    res = retest_cookie("tiktok", h, probe=probe)

    assert res["status"] == "ok" and res["cleared_expired"] is True
    assert probe.calls == 1
    assert get_redis().get(f"cookie_health:tiktok:{h}") is None
    row = cp.get_expiry_report("tiktok")[0]
    assert row["health_status"] == "healthy"       # the date no longer wins ...
    assert row["usable"] is True
    assert row["last_test_status"] == "ok"
    # ... and pool selection no longer re-marks it from the stale date
    assert cp.get_cookie_from_pool("tiktok") == b64
    assert get_redis().get(f"cookie_health:tiktok:{h}") is None


def test_failure_keeps_the_mark_and_never_deletes():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, _ = _add()
    h = _expire_by_date("tiktok", b64)

    res = retest_cookie("tiktok", h, probe=Probe("rejected", "phiên không còn hiệu lực"))

    assert res["status"] == "rejected" and res["cleared_expired"] is False
    assert get_redis().get(f"cookie_health:tiktok:{h}") == "expired"
    assert get_redis().lrange("cookie_pool:tiktok", 0, -1) == [b64]   # still there
    row = cp.get_expiry_report("tiktok")[0]
    assert row["health_status"] == "expired"
    assert row["last_test_message"] == "phiên không còn hiệu lực"


def test_inconclusive_changes_nothing_but_the_record():
    from app.core.cookie_probe import retest_cookie
    from app.core.redis_client import get_redis
    b64, _ = _add()
    h = _expire_by_date("tiktok", b64)
    res = retest_cookie("tiktok", h, probe=Probe("inconclusive"))
    assert res["status"] == "inconclusive"
    assert get_redis().get(f"cookie_health:tiktok:{h}") == "expired"


def test_probe_exception_is_inconclusive_and_does_not_leak():
    from app.core.cookie_probe import retest_cookie
    b64, h = _add()

    def boom(c, proxy):
        raise RuntimeError(f"connect failed for sessionid={SECRET}")

    res = retest_cookie("tiktok", h, probe=boom)
    assert res["status"] == "inconclusive"
    assert SECRET not in json.dumps(res)


def test_disabled_cookie_is_never_probed():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    b64, h = _add()
    cp.mark_cookie_disabled("tiktok", b64, reason="tay")
    probe = Probe("ok")
    res = retest_cookie("tiktok", h, probe=probe)
    assert res["status"] == "skipped_disabled"
    assert probe.calls == 0


def test_platform_without_probe_is_unsupported_not_guessed():
    from app.core.cookie_probe import retest_cookie
    b64, h = _add(platform="douyin")
    res = retest_cookie("douyin", h)
    assert res["status"] == "unsupported"


def test_unknown_hash_is_not_found():
    from app.core.cookie_probe import retest_cookie
    assert retest_cookie("tiktok", "0" * 16, probe=Probe("ok"))["status"] == "not_found"


def test_per_cookie_cooldown_blocks_second_probe():
    from app.core.cookie_probe import retest_cookie
    b64, h = _add()
    probe = Probe("ok")
    assert retest_cookie("tiktok", h, probe=probe)["status"] == "ok"
    second = retest_cookie("tiktok", h, probe=probe)
    assert second["status"] == "rate_limited"
    assert second["reason"] == "cookie_cooldown" and second["retry_after_s"] > 0
    assert probe.calls == 1


def test_per_platform_gap_blocks_back_to_back_probes_on_one_platform():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    _, h1 = _add(label="a")
    b2 = base64.b64encode(b".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tother\n").decode()
    cp.add_cookie("tiktok", b2, label="b")
    h2 = cp._hash(b2)
    probe = Probe("ok")
    assert retest_cookie("tiktok", h1, probe=probe)["status"] == "ok"
    res = retest_cookie("tiktok", h2, probe=probe)
    assert res["status"] == "rate_limited" and res["reason"] == "platform_gap"
    assert probe.calls == 1


def test_hourly_cap_is_enforced_across_platforms(monkeypatch):
    import app.core.cookie_probe as cpr
    from app.core.redis_client import get_redis
    monkeypatch.setattr(cpr, "HOURLY_CAP", 2)
    probe = Probe("ok")
    hashes = []
    for plat in ("tiktok", "youtube", "reddit"):
        b64 = base64.b64encode(f".x.com\tTRUE\t/\tTRUE\t0\tn\t{plat}\n".encode()).decode()
        from app.core import cookie_pool as cp
        cp.add_cookie(plat, b64)
        hashes.append((plat, cp._hash(b64)))
    out = [cpr.retest_cookie(p, h, probe=probe)["status"] for p, h in hashes]
    assert out == ["ok", "ok", "rate_limited"]
    assert probe.calls == 2
    assert get_redis().get(f"cookie_retest_hour:{int(time.time() // 3600)}") is not None


def test_results_and_report_contain_no_cookie_values():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    b64, h = _add()
    res = retest_cookie("tiktok", h, probe=Probe("ok", "TikTok xác nhận đang đăng nhập"))
    blob = json.dumps(res) + json.dumps(cp.get_expiry_report("tiktok"))
    assert SECRET not in blob and b64 not in blob


def test_account_hint_never_exposes_a_secret_session_value():
    """sessionid / SID are credentials; only numeric account ids may be shown."""
    from app.core import cookie_pool as cp
    _add()
    assert cp.get_expiry_report("tiktok")[0]["account_hint"] == ""
    b64 = base64.b64encode(b".facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t1000123\n").decode()
    cp.add_cookie("facebook", b64)
    assert cp.get_expiry_report("facebook")[0]["account_hint"] == "1000123"


def test_real_probes_have_fixed_messages_and_cover_expected_platforms():
    from app.core import cookie_probe as cpr
    assert set(cpr.supported_platforms()) == {"youtube", "tiktok", "instagram", "reddit", "bilibili"}
    # cookie header only ever carries cookies of the platform's own domains
    b64 = base64.b64encode(
        b".tiktok.com\tTRUE\t/\tTRUE\t0\ta\t1\n.other.com\tTRUE\t/\tTRUE\t0\tb\t2\n"
    ).decode()
    assert cpr._cookie_header(b64, ("tiktok.com",)) == "a=1"


def test_probe_verdicts_from_mocked_http(monkeypatch):
    import app.core.cookie_probe as cpr

    class R:
        def __init__(self, status, payload=None, text=""):
            self.status_code, self._p, self.text = status, payload, text

        def json(self):
            if self._p is None:
                raise ValueError
            return self._p

    b64 = _netscape(0)
    monkeypatch.setattr(cpr, "_http_get", lambda *a, **k: R(200, {"data": {"isLogin": True}}))
    assert cpr._probe_bilibili(b64, None)[0] == cpr.OK
    monkeypatch.setattr(cpr, "_http_get", lambda *a, **k: R(200, {"code": -101, "data": {}}))
    assert cpr._probe_bilibili(b64, None)[0] == cpr.REJECTED
    monkeypatch.setattr(cpr, "_http_get", lambda *a, **k: R(200, text='x"LOGGED_IN":false'))
    assert cpr._probe_youtube(b64, None)[0] == cpr.REJECTED
    monkeypatch.setattr(cpr, "_http_get", lambda *a, **k: R(200, text="consent page"))
    assert cpr._probe_youtube(b64, None)[0] == cpr.INCONCLUSIVE
    monkeypatch.setattr(cpr, "_http_get", lambda *a, **k: R(429))
    assert cpr._probe_instagram(b64, None)[0] == cpr.INCONCLUSIVE
    monkeypatch.setattr(cpr, "_http_get", lambda *a, **k: R(200, {"data": {"user_id_str": "1"}}))
    assert cpr._probe_tiktok(b64, None)[0] == cpr.OK


# ── storage status ──────────────────────────────────────────────────────────

def test_storage_status_reports_real_persistence_info(monkeypatch):
    from app.core.cookie_probe import storage_status
    from app.core.redis_client import get_redis
    _add()
    monkeypatch.setattr(get_redis(), "info", lambda section=None: {
        "aof_enabled": 1, "rdb_last_save_time": 1790000000,
        "rdb_last_bgsave_status": "ok", "aof_last_write_status": "ok"})
    st = storage_status()
    assert st["store"] == "redis" and st["total_cookies"] == 1
    assert st["readable"] is True
    assert st["aof_enabled"] is True and st["rdb_last_save_time"] == 1790000000
    assert st["rdb_last_bgsave_status"] == "ok"


def test_storage_status_is_honest_when_info_is_forbidden(monkeypatch):
    from app.core.cookie_probe import storage_status
    from app.core.redis_client import get_redis
    import redis
    _add()
    rc = get_redis()

    def deny(*a, **k):
        raise redis.exceptions.ResponseError("unknown command 'INFO'")

    monkeypatch.setattr(rc, "info", deny)
    st = storage_status()
    assert st["readable"] is False
    assert st["message"] == "Không đọc được trạng thái lưu bền"
    assert st["total_cookies"] == 1
    assert "aof_enabled" not in st


# ── route wiring ────────────────────────────────────────────────────────────

def test_routes_are_behind_verify_admin_and_audit_logged():
    from app.api import admin
    routes = {r.path: r for r in admin.router.routes if hasattr(r, "dependant")}
    for path in ("/cookies/retest/{platform}/{cookie_hash}",
                 "/cookies/retest-info", "/cookies/storage-status"):
        r = next(x for p, x in routes.items() if p.endswith(path))
        deps = [d.call for d in r.dependant.dependencies]
        assert admin.verify_admin in deps, path
    import inspect
    assert "log_admin_action" in inspect.getsource(admin.cookie_pool_retest)


# ── rejected takes the cookie out of rotation ────────────────────────────────

def test_rejected_takes_active_cookie_out_of_rotation_and_report_shows_it():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, h = _add()
    assert cp.get_expiry_report("tiktok")[0]["usable"] is True

    msg = "TikTok báo phiên đăng nhập không còn hiệu lực"
    res = retest_cookie("tiktok", h, probe=Probe("rejected", msg))

    assert res["status"] == "rejected"
    assert get_redis().get(f"cookie_health:tiktok:{h}") == "expired"
    assert get_redis().lrange("cookie_pool:tiktok", 0, -1) == [b64]   # never deleted
    row = cp.get_expiry_report("tiktok")[0]
    assert row["health_status"] == "expired" and row["usable"] is False
    assert row["expired_reason"] == msg and row["last_test_status"] == "rejected"
    assert cp.get_cookie_from_pool("tiktok") is None
    assert cp.has_selectable_cookie("tiktok") is False


def test_rejected_overrides_a_temporary_block():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, h = _add()
    cp.mark_cookie_soft_blocked("tiktok", b64)
    retest_cookie("tiktok", h, probe=Probe("rejected"))
    assert get_redis().get(f"cookie_health:tiktok:{h}") == "expired"


def test_inconclusive_leaves_active_cookie_selectable():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, h = _add()
    retest_cookie("tiktok", h, probe=Probe("inconclusive", "timeout"))
    assert get_redis().get(f"cookie_health:tiktok:{h}") is None
    assert cp.get_cookie_from_pool("tiktok") == b64
    assert cp.get_expiry_report("tiktok")[0]["usable"] is True


def test_later_ok_restores_a_cookie_rejected_by_retest():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, h = _add()
    retest_cookie("tiktok", h, probe=Probe("rejected", "x"))
    get_redis().delete(f"cookie_retest_cd:tiktok:{h}", "cookie_retest_gap:tiktok")
    res = retest_cookie("tiktok", h, probe=Probe("ok", "ok"))
    assert res["status"] == "ok" and res["cleared_expired"] is True
    row = cp.get_expiry_report("tiktok")[0]
    assert row["health_status"] == "healthy" and row["usable"] and row["expired_reason"] == ""
    assert cp.get_cookie_from_pool("tiktok") == b64


def test_admin_disabled_cookie_is_not_changed_by_rejected_or_ok():
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, h = _add()
    cp.mark_cookie_disabled("tiktok", b64, "manual")
    assert retest_cookie("tiktok", h, probe=Probe("rejected"))["status"] == "skipped_disabled"
    assert get_redis().get(f"cookie_health:tiktok:{h}") == "disabled"


def test_disabled_during_probe_is_not_overwritten(monkeypatch):
    from app.core.cookie_probe import retest_cookie
    from app.core import cookie_pool as cp
    from app.core.redis_client import get_redis
    b64, h = _add()

    def probe(c, p):
        cp.mark_cookie_disabled("tiktok", b64, "manual")   # admin acts mid-probe
        return "rejected", "m"
    retest_cookie("tiktok", h, probe=probe)
    assert get_redis().get(f"cookie_health:tiktok:{h}") == "disabled"
