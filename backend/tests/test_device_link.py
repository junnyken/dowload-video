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


def test_extension_sign_out_ends_only_that_device(rc, monkeypatch):  # noqa: F811
    from app.core import extension_session as xs
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    now = int(time.time()) - 100
    a = xs.issue("u1", "a@x", now=now, device=D[0])["token"]
    b = xs.issue("u1", "a@x", now=now, device=D[1])["token"]
    assert xs.revoke_device("u1", D[0])
    assert xs.verify(a) is None and xs.verify(b)["id"] == "u1"
    # signed in again on D0 later: a newer token works
    c = xs.issue("u1", "a@x", now=int(time.time()) + 5, device=D[0])["token"]
    assert xs.verify(c, now=int(time.time()) + 6)["id"] == "u1"


def test_my_devices_and_self_sign_out_any_mode(rc):  # noqa: F811
    set_mode(rc, "shadow")
    meta_web = {"kind": "web", "label": "Chrome · Windows"}
    dl.touch("u1", D[0], jwt(10), tier="free", rc=rc, now=time.time() - 50, meta=meta_web)
    dl.touch("u1", D[1], jwt(10), tier="free", rc=rc, now=time.time() - 10, meta={"kind": "app", "label": "App VidGrab · Windows"})
    lst = dl.my_devices("u1", D[1], rc=rc)
    assert [x["id"] for x in lst] == [D[1][:8], D[0][:8]] and lst[0]["current"] and lst[1]["label"] == "Chrome · Windows"
    assert lst[0]["kinds"] == ["app"] and lst[1]["kinds"] == ["web"]
    # the same PC seen from the extension: kinds merge, the browser label stays over the app's
    dl._touched.clear()
    dl.touch("u1", D[1], jwt(10), tier="free", rc=rc, meta={"kind": "extension", "label": "Chrome · Windows"})
    dl._touched.clear()
    dl.touch("u1", D[1], jwt(10), tier="free", rc=rc, meta={"kind": "app", "label": "App VidGrab · Windows"})
    first = dl.my_devices("u1", D[1], rc=rc)[0]
    assert first["kinds"] == ["extension", "app"] and first["label"] == "Chrome · Windows"
    assert dl.sign_out_device("u1", "zz", rc=rc) is None
    assert dl.sign_out_device("u1", D[0][:8], rc=rc) == D[0]
    r = dl.touch("u1", D[0], jwt(10), tier="free", rc=rc)      # shadow mode, still refused: the person did it
    assert r and r["error_code"] == "device_signed_out"
    assert dl.touch("u1", D[0], jwt(time.time() + 5), tier="free", rc=rc) is None   # signed in again


def test_describe_request():
    class R:
        def __init__(self, h): self.headers = h
    assert dl.describe_request(R({"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130 Safari/537"})) == {"kind": "web", "label": "Chrome · Windows"}
    assert dl.describe_request(R({"X-VG-Client": "0.19.4", "User-Agent": "x Windows"}))["kind"] == "app"
    assert dl.describe_request(R({"Origin": "chrome-extension://abc", "User-Agent": "Edg/1 Windows"}))["label"] == "Edge · Windows"


def test_http_my_devices(rc, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from app.core import extension_session as xs
    from app.main import app as fastapi_app
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setattr(dl, "_tier", lambda uid: "free")
    set_mode(rc, "shadow")
    client = TestClient(fastapi_app, raise_server_exceptions=False)
    tok = xs.issue("22222222-2222-3333-4444-555555555555", "b@x", now=int(time.time()) - 60)["token"]
    h = {"Authorization": f"Bearer {tok}", "X-VG-Device": D[2] * 2, "User-Agent": "Mozilla Firefox/120 Linux"}
    r = client.get("/api/v1/devices", headers=h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["max_devices"] == 2 and j["devices"][0]["current"] and j["devices"][0]["label"] == "Firefox · Linux"
    assert client.post("/api/v1/devices/abcdef12/signout", headers=h).status_code == 404
    r = client.post(f"/api/v1/devices/{D[2][:8]}/signout", headers=h)
    assert r.status_code == 200 and r.json()["current"] is True
    assert client.get("/api/v1/devices", headers=h).status_code == 401
    assert client.get("/api/v1/devices").status_code == 401


def test_old_app_not_linked_until_0_19_4():
    class R:
        def __init__(self, h): self.headers = h
    assert dl.counts_as_device(R({})) is True
    assert dl.counts_as_device(R({"X-VG-Client": "0.19.3"})) is False
    assert dl.counts_as_device(R({"X-VG-Client": "0.19.4"})) is True
    assert dl.counts_as_device(R({"X-VG-Client": "0.20.0"})) is True
    assert dl.counts_as_device(R({"X-VG-Client": "junk"})) is False


# ── 26/10 bypass guards ─────────────────────────────────────────────────────

def guards_enforce(rc, **kw):  # noqa: F811
    dl.save_settings(mode="enforce", extra_enforce_at=None, rc=rc, **kw)


def test_guards_have_their_own_watch_period(rc):  # noqa: F811
    now = 1_800_000_000
    s = dl.get_settings(rc, now)
    assert s["extra_enforce_at"] == now + 3 * 86400 and s["extra_mode"] == "shadow"
    dl.save_settings(mode="enforce", rc=rc)          # devices enforce, guards still watching
    s = dl.get_settings(rc, now + 86400)
    assert s["effective_mode"] == "enforce" and s["extra_mode"] == "shadow"
    assert dl.get_settings(rc, now + 3 * 86400 + 1)["extra_mode"] == "enforce"


def test_email_family():
    f = dl.email_family
    assert f("Ten+abc@gmail.com") == f("t.e.n@googlemail.com") == f("ten@gmail.com")
    assert f("ten+1@matbao.com") == f("ten@matbao.com") != f("t.en@matbao.com")
    assert f("ten@gmail.com") != f("ten2@gmail.com") and f("no-at") is None


def test_family_shares_free_allowance(rc):  # noqa: F811
    guards_enforce(rc)
    dl.note_identity("a1", "ten@gmail.com", rc=rc)
    dl._touched.clear()
    assert dl.family_of("a1", rc=rc) is None                 # one account: nothing to share
    dl.note_identity("a2", "t.e.n+x@gmail.com", rc=rc)
    fam = dl.family_of("a1", rc=rc)
    assert fam and dl.family_of("a2", rc=rc) == fam
    a = QuotaRequester(REQ_USER, "a1", tier="free"); a.family = fam
    b = QuotaRequester(REQ_USER, "a2", tier="free"); b.family = fam
    for i in range(20):
        quotas.record_platform_download(a, "tiktok", f"https://t/{i}")
    q = quotas.check_platform_quota(b, "tiktok", "https://t/new")
    assert q["allowed"] is False and q["reason"] == "free_shared_family" and "hộp thư" in q["message"]


def test_extra_free_accounts_on_one_network_share(rc):  # noqa: F811
    guards_enforce(rc, net_free_accounts=3)
    net = dl.net_of("203.0.113.9")
    flags = [dl.network_extra(f"u{i}", net, rc=rc) for i in range(5)]
    assert flags == [False, False, False, True, True]
    assert dl.network_extra("u0", net, rc=rc) is False         # order is first use, stable
    extra = []
    for i in (3, 4):
        r = QuotaRequester(REQ_USER, f"u{i}", tier="free"); r.net_extra = net; extra.append(r)
    for i in range(20):
        quotas.record_platform_download(extra[0], "tiktok", f"https://t/{i}")
    q = quotas.check_platform_quota(extra[1], "tiktok", "https://t/new")
    assert q["allowed"] is False and q["reason"] == "free_shared_network"
    # the first three accounts are not in that bucket
    assert quotas.check_platform_quota(QuotaRequester(REQ_USER, "u1", tier="free"), "tiktok", "https://t/new")["allowed"]


def test_copied_device_id_splits_per_network(rc):  # noqa: F811
    guards_enforce(rc, clone_networks=4)
    dev = D[0]
    ips = ["198.51.1.1", "198.52.1.1", "198.53.1.1", "198.54.1.1"]
    outs = [dl.effective_device("u1", dev, ip, rc=rc) for ip in ips]
    assert outs == [dev] * 4                                  # 4 networks: still one device
    fifth = dl.effective_device("u1", dev, "198.55.1.1", rc=rc)
    again = dl.effective_device("u1", dev, "198.51.7.7", rc=rc)   # same /16 as the first
    assert fifth.startswith("x") and len(fifth) == 32 and fifth != dev
    assert again.startswith("x") and again != fifth
    assert dl.stats(rc)["events"][0]["kind"] == "clone_detected"


def test_copied_id_shadow_only_logs(rc):  # noqa: F811
    dl.save_settings(mode="enforce", rc=rc)                   # guards still in their watch period
    for i in range(6):
        assert dl.effective_device("u1", D[1], f"10{i}.20.1.1", rc=rc) == D[1]
    assert dl.stats(rc)["events"][0]["kind"] == "clone_would_split"


def test_no_device_id_counts_as_network_device(rc):  # noqa: F811
    guards_enforce(rc)
    p1 = dl.effective_device("u1", None, "203.0.113.5", rc=rc)
    p2 = dl.effective_device("u1", None, "203.0.113.5", rc=rc)
    p3 = dl.effective_device("u1", None, "203.0.113.6", rc=rc)
    assert p1 and p1.startswith("n") and p1 == p2 != p3
    # it takes a device slot: with 2 real devices a stripped call is the third
    dl.touch("u1", D[0], jwt(1), tier="free", rc=rc, now=time.time() - 30)
    dl.touch("u1", D[1], jwt(1), tier="free", rc=rc, now=time.time() - 20)
    dl.touch("u1", p1, jwt(1), tier="free", rc=rc, now=time.time() - 10)
    assert dl.touch("u1", D[0], jwt(1), tier="free", rc=rc)["error_code"] == "device_signed_out"


def test_old_extension_without_id_not_linked():
    class R:
        def __init__(self, h): self.headers = h
    assert dl.counts_as_device(R({"Origin": "chrome-extension://abc"})) is False
    assert dl.counts_as_device(R({"Origin": "chrome-extension://abc", "X-VG-Device": "a" * 64})) is True
    assert dl.counts_as_device(R({"Origin": "https://dvid.vibe1.tinhgon.xyz"})) is True


def test_wire_carries_family_and_network():
    rq = QuotaRequester(REQ_USER, "u9", device="n" + "1" * 31)
    rq.family, rq.net_extra = "a" * 16, "b" * 16
    back = QuotaRequester.from_key(rq.wire())
    assert (back.key, back.device, back.family, back.net_extra) == ("user:u9", "n" + "1" * 31, "a" * 16, "b" * 16)
    assert QuotaRequester.from_key(QuotaRequester(REQ_USER, "u9", device=D[0]).wire()).device == D[0]


def test_http_resolve_requester_links_everything(rc, monkeypatch):  # noqa: F811
    guards_enforce(rc, net_free_accounts=1)
    monkeypatch.setattr(quotas, "_get_tier", lambda uid: "free")
    dl.note_identity("f1", "ten@gmail.com", rc=rc)
    dl._touched.clear()
    dl.note_identity("f2", "ten+2@gmail.com", rc=rc)

    class Req:
        def __init__(self, h): self.headers = h
    dl.network_extra("first", dl.net_of("203.0.113.77"), rc=rc)
    rq = quotas.resolve_requester(Req({"X-VG-Device": D[3] * 2}), user_id="f2", ip="203.0.113.77", is_admin=False)
    assert rq.device == D[3] and rq.family == dl.email_family("ten@gmail.com") and rq.net_extra == dl.net_of("203.0.113.77")
