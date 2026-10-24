"""Device linking (owner 24/10/2026): Free 2 / paid 5 devices per account,
Free accounts on one device share one allowance, shadow for 3 days then
enforce. Drives app.core.device_link + app.core.quotas against fakeredis."""
from __future__ import annotations

import base64
import json
import time

import pytest

from app.core import device_link as dl
from app.core import quotas
from app.core.quotas import QuotaRequester, REQ_USER
from tests.test_admin_download_metrics import rc  # noqa: F401

D = ["a" * 32, "b" * 32, "c" * 32, "d" * 32, "e" * 32, "f" * 32]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    dl._touched.clear()
    monkeypatch.setenv("QUOTA_SCOPE", "per_platform")
    monkeypatch.delenv("PLATFORM_DAILY_LIMIT_USER", raising=False)


def jwt(auth_time: float) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"iat": auth_time + 999, "amr": [{"method": "password", "timestamp": auth_time}]}).encode()).decode().rstrip("=")
    return f"x.{body}.y"


def set_mode(rc, mode, enforce_at=None):  # noqa: F811
    dl.save_settings(mode=mode, enforce_at=enforce_at, rc=rc)


def test_first_read_starts_three_day_shadow(rc):  # noqa: F811
    now = 1_800_000_000
    s = dl.get_settings(rc, now)
    assert s["mode"] == "shadow" and s["effective_mode"] == "shadow"
    assert s["enforce_at"] == now + 3 * 86400 and s["free_max"] == 2 and s["paid_max"] == 5
    assert dl.get_settings(rc, now + 3 * 86400 + 1)["effective_mode"] == "enforce"


def test_shadow_only_logs_would_sign_out(rc):  # noqa: F811
    set_mode(rc, "shadow")
    for i, d in enumerate(D[:3]):
        assert dl.touch("u1", d, jwt(1), tier="free", rc=rc, now=1000 + i * 100) is None
    st = dl.stats(rc)
    assert st["events"][0]["kind"] == "would_sign_out" and st["events"][0]["dev"] == D[0][:8]
    # nobody is signed out
    assert dl.touch("u1", D[0], jwt(1), tier="free", rc=rc, now=2000) is None
    assert rc.hlen("vidgrab:devlink:rev:u1") == 0


def test_enforce_signs_out_oldest_free_device_then_readmits_on_new_sign_in(rc):  # noqa: F811
    set_mode(rc, "enforce")
    dl.touch("u1", D[0], jwt(10), tier="free", rc=rc, now=1000)
    dl.touch("u1", D[1], jwt(10), tier="free", rc=rc, now=1100)
    dl.touch("u1", D[2], jwt(10), tier="free", rc=rc, now=1200)   # third device
    r = dl.touch("u1", D[0], jwt(10), tier="free", rc=rc, now=1300)
    assert r and r["error_code"] == "device_signed_out" and r["max_devices"] == 2
    # signing in again on D0 (newer sign-in time) admits it and signs out D1 (oldest now)
    assert dl.touch("u1", D[0], jwt(1250), tier="free", rc=rc, now=1400) is None
    assert dl.touch("u1", D[1], jwt(10), tier="free", rc=rc, now=1500)["error_code"] == "device_signed_out"
    assert dl.touch("u1", D[2], jwt(10), tier="free", rc=rc, now=1600) is None


def test_paid_account_keeps_five_devices(rc):  # noqa: F811
    set_mode(rc, "enforce")
    for i, d in enumerate(D[:5]):
        assert dl.touch("p1", d, jwt(1), tier="pro", rc=rc, now=1000 + i * 100) is None
    assert dl.touch("p1", D[0], jwt(1), tier="pro", rc=rc, now=1600) is None
    dl.touch("p1", D[5], jwt(1), tier="pro", rc=rc, now=1700)      # sixth
    assert dl.touch("p1", D[1], jwt(1), tier="pro", rc=rc, now=1800)["max_devices"] == 5


def test_off_and_no_device_do_nothing(rc):  # noqa: F811
    set_mode(rc, "off")
    for i, d in enumerate(D):
        assert dl.touch("u1", d, jwt(1), tier="free", rc=rc, now=1000 + i) is None
    set_mode(rc, "enforce")
    assert dl.touch("u1", None, jwt(1), rc=rc) is None


def test_extension_token_auth_time_is_iat():
    body = base64.urlsafe_b64encode(json.dumps({"sub": "u", "iat": 1234}).encode()).decode().rstrip("=")
    assert dl.session_auth_time(f"vgx_{body}.sig") == 1234
    assert dl.session_auth_time(jwt(555)) == 555
    assert dl.session_auth_time("garbage") is None


def free(uid, dev):
    return QuotaRequester(REQ_USER, uid, tier="free", device=dev)


def test_free_accounts_share_one_device_allowance(rc):  # noqa: F811
    set_mode(rc, "enforce")
    a, b = free("ua", D[0]), free("ub", D[0])
    for i in range(20):
        assert quotas.record_platform_download(a, "tiktok", f"https://t/{i}")
    q = quotas.check_platform_quota(b, "tiktok", "https://t/new")
    assert q["allowed"] is False and q["reason"] == "device_free_shared"
    # a video the device already fetched today is free for the second account too
    assert quotas.check_platform_quota(b, "tiktok", "https://t/3")["allowed"] is True
    # another device, another platform: unaffected
    assert quotas.check_platform_quota(free("ub", D[1]), "tiktok", "https://t/new")["allowed"] is True
    assert quotas.check_platform_quota(b, "instagram", "https://i/1")["allowed"] is True


def test_shadow_shares_nothing_but_logs(rc):  # noqa: F811
    set_mode(rc, "shadow")
    a, b = free("ua", D[0]), free("ub", D[0])
    for i in range(20):
        quotas.record_platform_download(a, "tiktok", f"https://t/{i}")
    assert quotas.check_platform_quota(b, "tiktok", "https://t/new")["allowed"] is True
    assert dl.stats(rc)["events"][0]["kind"] == "shared_would_block"


def test_paid_account_on_shared_device_not_limited(rc):  # noqa: F811
    set_mode(rc, "enforce")
    for i in range(20):
        quotas.record_platform_download(free("ua", D[0]), "tiktok", f"https://t/{i}")
    pro = QuotaRequester(REQ_USER, "up", tier="pro", device=D[0])
    assert quotas.check_platform_quota(pro, "tiktok", "https://t/new")["allowed"] is True


def test_batch_allowance_counts_device_bucket(rc):  # noqa: F811
    set_mode(rc, "enforce")
    for i in range(18):
        quotas.record_platform_download(free("ua", D[0]), "tiktok", f"https://t/{i}")
    ba = quotas.BatchAllowance(free("ub", D[0]))
    out = [ba.take("tiktok", f"https://t/n{i}") for i in range(4)]
    assert out[:2] == [None, None] and out[2] and "Thiết bị này" in out[2]


def test_refund_gives_the_device_slot_back(rc):  # noqa: F811
    set_mode(rc, "enforce")
    a = free("ua", D[0])
    for i in range(20):
        quotas.record_platform_download(a, "tiktok", f"https://t/{i}")
    quotas.refund_platform_download(a, "tiktok", quotas.url_fingerprint("tiktok", "https://t/0"))
    assert quotas.check_platform_quota(free("ub", D[0]), "tiktok", "https://t/new")["allowed"] is True


def test_wire_round_trip_keeps_device():
    rq = QuotaRequester(REQ_USER, "u9", device=D[2])
    back = QuotaRequester.from_key(rq.wire())
    assert back.key == "user:u9" and back.device == D[2]
    assert QuotaRequester.from_key("user:u9@nothex").device is None
    assert QuotaRequester.from_key("ip:1.2.3.4").key == "ip:1.2.3.4"


def test_redis_down_fails_open(monkeypatch):
    import fakeredis
    server = fakeredis.FakeServer()
    server.connected = False
    monkeypatch.setattr("app.core.redis_client._client", fakeredis.FakeRedis(server=server, decode_responses=True))
    assert dl.touch("u1", D[0], jwt(1), tier="free") is None


def test_http_extension_session_signed_out_device_gets_401(rc, monkeypatch):  # noqa: F811
    """Through the real auth dependency: vgx_ token + X-VG-Device."""
    from fastapi.testclient import TestClient
    from app.core import extension_session as xs
    from app.main import app as fastapi_app
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(dl, "_tier", lambda uid: "free")
    set_mode(rc, "enforce")
    client = TestClient(fastapi_app, raise_server_exceptions=False)
    uid = "11111111-2222-3333-4444-555555555555"
    tok = xs.issue(uid, "a@example.com", now=int(time.time()) - 60)["token"]
    full = [d * 2 for d in D]          # the header is 64 hex

    def call(dev):
        return client.post("/api/v1/extension/session", headers={"Authorization": f"Bearer {tok}", "X-VG-Device": dev})

    assert call(full[0]).status_code == 200
    time.sleep(0.01)
    assert call(full[1]).status_code == 200
    time.sleep(0.01)
    assert call(full[2]).status_code == 200          # third device: signs out the first
    r = call(full[0])
    assert r.status_code == 401 and r.json()["detail"]["error_code"] == "device_signed_out"
    # no header (old client): not linked, works
    assert client.post("/api/v1/extension/session", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
