"""
Task #6256 — Web Push: encrypted + VAPID-signed delivery, endpoint allow-list
plus the webhook public-IP check, dead-subscription cleanup, clean "disabled"
mode without keys. No real network: DNS and the HTTPS pool are faked.
"""
from __future__ import annotations

import base64
import logging

import pytest

from app.core import push_sender, webhook_guard
from app.core.push_sender import PushEndpointError, check_endpoint_shape, validate_push_endpoint

FCM = "https://fcm.googleapis.com/fcm/send/abc:def"
MOZ = "https://updates.push.services.mozilla.com/wpush/v2/gAAAA"
APPLE = "https://web.push.apple.com/QGz1"
WNS = "https://wns2-par02p.notify.windows.com/w/?token=BQYAAA"


# ── Fakes ─────────────────────────────────────────────────────────────

class FakeDNS:
    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, host, port, *a, **k):
        self.calls.append(host)
        if host not in self.table:
            import socket
            raise socket.gaierror("nxdomain")
        return [(2, 1, 6, "", (ip, port)) for ip in self.table[host]]


class FakeResp:
    def __init__(self, status):
        self.status = status

    def read(self, n):
        return b""

    def release_conn(self):
        pass


class FakePool:
    instances: list = []
    status_by_path: dict = {}

    def __init__(self, target, ip, timeout):
        self.target, self.ip, self.timeout = target, ip, timeout
        self.requests = []
        FakePool.instances.append(self)

    def urlopen(self, method, path, **kw):
        self.requests.append((method, path, kw))
        return FakeResp(FakePool.status_by_path.get(path, 201))

    def close(self):
        pass


@pytest.fixture
def net(monkeypatch):
    FakePool.instances = []
    FakePool.status_by_path = {}
    dns = FakeDNS({
        "fcm.googleapis.com": ["142.250.1.95"],
        "updates.push.services.mozilla.com": ["34.117.1.1"],
        "web.push.apple.com": ["17.188.1.1"],
        "wns2-par02p.notify.windows.com": ["20.42.1.1"],
    })
    monkeypatch.setattr(webhook_guard.socket, "getaddrinfo", dns)
    monkeypatch.setattr(webhook_guard, "_make_pool",
                        lambda target, ip, timeout: FakePool(target, ip, timeout))
    return dns


@pytest.fixture
def vapid_env(monkeypatch):
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    monkeypatch.setenv("PUSH_VAPID_PRIVATE_KEY", base64.urlsafe_b64encode(raw).rstrip(b"=").decode())
    monkeypatch.setenv("PUSH_VAPID_SUBJECT", "mailto:ops@example.com")
    for k in ("PUSH_VAPID_PUBLIC_KEY", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY", "VAPID_SUBJECT"):
        monkeypatch.delenv(k, raising=False)
    return v


@pytest.fixture
def no_vapid(monkeypatch):
    for k in ("PUSH_VAPID_PRIVATE_KEY", "VAPID_PRIVATE_KEY", "PUSH_VAPID_PUBLIC_KEY",
              "VAPID_PUBLIC_KEY", "PUSH_VAPID_SUBJECT", "VAPID_SUBJECT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(push_sender, "_logged", set())


def _browser_keys(with_private=False):
    """A real browser-side P-256 key pair + auth secret (what PushManager makes)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import os
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962,
                                      serialization.PublicFormat.UncompressedPoint)
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    secret = os.urandom(16)
    if with_private:
        return enc(pub), enc(secret), k, secret
    return enc(pub), enc(secret)


class _Res:
    def __init__(self, data):
        self.data = data
        self.count = len(data)


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, []

    def select(self, *a, **k):
        self.op = "select"
        return self

    def delete(self):
        self.op = "delete"
        return self

    def upsert(self, row, on_conflict=None):
        self.op, self.payload = "upsert", row
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        hit = [r for r in rows if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        if self.op == "delete":
            self.db[self.table] = [r for r in rows if r not in hit]
            return _Res(hit)
        if self.op == "upsert":
            p = dict(self.payload)
            for r in rows:
                if r["user_id"] == p["user_id"] and r["endpoint"] == p["endpoint"]:
                    r.update(p)
                    return _Res([r])
            p.setdefault("id", f"sub-{len(rows) + 1}")
            rows.append(p)
            return _Res([p])
        return _Res([dict(r) for r in hit])


class FakeSB:
    def __init__(self):
        self.db: dict[str, list] = {}

    def table(self, name):
        return _Q(self.db, name)


# ── Endpoint allow-list (negative controls) ──────────────────────────

@pytest.mark.parametrize("endpoint", [FCM, MOZ, APPLE, WNS])
def test_known_push_services_accepted(net, endpoint):
    t = validate_push_endpoint(endpoint)
    assert t.ips and t.host in endpoint


@pytest.mark.parametrize("endpoint", [
    "http://fcm.googleapis.com/fcm/send/x",            # not https
    "https://evil.example.com/fcm/send/x",             # not a push service
    "https://fcm.googleapis.com.evil.example.com/x",   # suffix trick
    "https://evilfcm.googleapis.com/x",                # not the exact host
    "https://notify.windows.com/x",                    # bare suffix, no node
    "https://xnotify.windows.com/x",                   # suffix without the dot
    "https://push.apple.com.attacker.net/x",
    "https://user:pw@fcm.googleapis.com/x",            # credentials
    "https://fcm.googleapis.com:8443/x",               # non-default port
    "https://127.0.0.1/x",
    "https://169.254.169.254/latest/meta-data/",
    "https://localhost/x",
    "https://redis/x",
    "",
])
def test_non_push_endpoints_rejected(net, endpoint):
    with pytest.raises(PushEndpointError):
        validate_push_endpoint(endpoint)
    assert net.calls == []                              # rejected before any DNS


def test_allowlisted_host_resolving_private_is_rejected(net):
    """Allow-listed name but the address check still applies (poisoned DNS /
    rebinding)."""
    net.table["fcm.googleapis.com"] = ["10.0.0.7"]
    with pytest.raises(PushEndpointError) as ei:
        validate_push_endpoint(FCM)
    assert ei.value.permanent


def test_shape_check_is_dns_free():
    assert check_endpoint_shape(FCM) == "fcm.googleapis.com"


# ── Disabled without keys ─────────────────────────────────────────────

def test_send_push_disabled_without_keys(no_vapid, net, monkeypatch, caplog):
    sb = FakeSB()
    pub, auth = _browser_keys()
    sb.db["push_subscriptions"] = [{"id": "s1", "user_id": "u1", "endpoint": FCM,
                                    "p256dh": pub, "auth_key": auth}]
    monkeypatch.setattr("app.core.database.get_service_client", lambda: sb)
    with caplog.at_level(logging.WARNING, logger="app.core.push_sender"):
        assert push_sender.send_push("u1", {"title": "t"}) == 0
        assert push_sender.send_push("u1", {"title": "t"}) == 0
    assert push_sender.public_key() == ""
    assert sum("Web Push disabled" in r.getMessage() for r in caplog.records) == 1
    assert FakePool.instances == []


# ── Sending ───────────────────────────────────────────────────────────

def test_send_push_encrypts_signs_and_cleans_dead_subscriptions(vapid_env, net, monkeypatch):
    sb = FakeSB()
    pub, auth, browser_key, secret = _browser_keys(with_private=True)
    sb.db["push_subscriptions"] = [
        {"id": "ok", "user_id": "u1", "endpoint": FCM, "p256dh": pub, "auth_key": auth},
        {"id": "gone", "user_id": "u1", "endpoint": MOZ, "p256dh": pub, "auth_key": auth},
        # A row stored before the allow-list existed: never contacted, dropped.
        {"id": "legacy", "user_id": "u1", "endpoint": "https://10.0.0.5/steal", "p256dh": pub, "auth_key": auth},
        {"id": "other", "user_id": "u2", "endpoint": APPLE, "p256dh": pub, "auth_key": auth},
    ]
    FakePool.status_by_path = {"/wpush/v2/gAAAA": 410}
    monkeypatch.setattr("app.core.database.get_service_client", lambda: sb)

    assert push_sender.send_push("u1", {"title": "VidGrab", "body": "x"}) == 1

    assert {p.target.host for p in FakePool.instances} == {"fcm.googleapis.com",
                                                           "updates.push.services.mozilla.com"}
    fcm = next(p for p in FakePool.instances if p.target.host == "fcm.googleapis.com")
    assert fcm.ip == "142.250.1.95"                               # pinned to checked IP
    method, path, kw = fcm.requests[0]
    assert (method, path) == ("POST", "/fcm/send/abc:def")
    hdrs = {k.lower(): v for k, v in kw["headers"].items()}
    assert hdrs["content-encoding"] == "aes128gcm"
    assert hdrs["authorization"].startswith("vapid t=")           # VAPID signed
    assert push_sender.public_key() in hdrs["authorization"]
    assert hdrs["ttl"] == str(push_sender.PUSH_TTL_SECONDS)
    assert b"VidGrab" not in kw["body"]                           # encrypted payload
    import http_ece, json
    plain = http_ece.decrypt(kw["body"], private_key=browser_key, auth_secret=secret,
                             version="aes128gcm")
    assert json.loads(plain) == {"title": "VidGrab", "body": "x"}  # browser can read it
    assert kw["redirect"] is False

    left = {r["id"] for r in sb.db["push_subscriptions"]}
    assert left == {"ok", "other"}                                # 410 + legacy removed


def test_public_key_matches_private(vapid_env):
    from cryptography.hazmat.primitives import serialization
    raw = vapid_env.public_key.public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
    assert push_sender.public_key() == base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ── API ───────────────────────────────────────────────────────────────

@pytest.fixture
def push_api(monkeypatch):
    from app.api import push as push_mod
    sb = FakeSB()
    monkeypatch.setattr(push_mod, "get_service_client", lambda: sb)
    user = {"id": None}
    monkeypatch.setattr(push_mod, "_get_user_id", lambda request: user["id"])
    return sb, user


def test_subscribe_requires_login(app, push_api, net):
    pub, auth = _browser_keys()
    r = app.post("/api/v1/push/subscribe", json={"endpoint": FCM, "keys": {"p256dh": pub, "auth": auth}})
    assert r.status_code == 401


def test_subscribe_accepts_browser_shape_and_ties_to_user(app, push_api, net):
    sb, user = push_api
    user["id"] = "u-42"
    pub, auth = _browser_keys()
    r = app.post("/api/v1/push/subscribe",
                 json={"endpoint": FCM, "expirationTime": None, "keys": {"p256dh": pub, "auth": auth}})
    assert r.status_code == 200, r.text
    (row,) = sb.db["push_subscriptions"]
    assert row["user_id"] == "u-42" and row["endpoint"] == FCM and row["auth_key"] == auth


def test_subscribe_rejects_internal_endpoint(app, push_api, net):
    sb, user = push_api
    user["id"] = "u-42"
    pub, auth = _browser_keys()
    for ep in ("https://169.254.169.254/latest/meta-data/", "https://evil.example.com/x"):
        r = app.post("/api/v1/push/subscribe", json={"endpoint": ep, "keys": {"p256dh": pub, "auth": auth}})
        assert r.status_code == 400
    assert sb.db.get("push_subscriptions", []) == []


def test_vapid_key_endpoint(app, vapid_env):
    assert app.get("/api/v1/push/vapid-key").json() == {"public_key": push_sender.public_key()}


def test_zip_completion_push_wiring_imports():
    """video_tasks used to import a send_push_notification that did not exist."""
    import inspect
    from app.tasks import video_tasks
    src = inspect.getsource(video_tasks)
    assert "from app.core.push_sender import send_push" in src
    assert "import send_push_notification" not in src
