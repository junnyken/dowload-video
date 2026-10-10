"""
Phase 33-0 (task #6254): user webhook delivery must not reach internal
addresses. DNS and the HTTP connection are faked — nothing leaves the test.
"""
from __future__ import annotations

import asyncio
import socket

import fakeredis
import pytest
from fastapi import HTTPException

from app.core import webhook_guard
from app.core.webhook_guard import WebhookUrlError, validate_webhook_url


# ── Fakes ─────────────────────────────────────────────────────────────

class FakeDNS:
    """getaddrinfo stand-in: host → list of answers, consumed in order (the
    last answer repeats) so a test can model DNS rebinding."""

    def __init__(self, table):
        self.table = {k: list(v) for k, v in table.items()}
        self.calls = []

    def __call__(self, host, port, *a, **k):
        self.calls.append(host)
        answers = self.table.get(host)
        if not answers:
            raise socket.gaierror("no such host")
        ips = answers.pop(0) if len(answers) > 1 else answers[0]
        out = []
        for ip in ips:
            fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
            out.append((fam, socket.SOCK_STREAM, 6, "", (ip, port)))
        return out


class FakeResp:
    def __init__(self, status, body=b"x" * 100_000):
        self.status = status
        self._body = body
        self.read_sizes = []

    def read(self, n=None):
        self.read_sizes.append(n)
        return self._body[: n or len(self._body)]

    def release_conn(self):
        pass


class FakePool:
    instances = []

    def __init__(self, target, ip, timeout, status=200):
        self.target, self.ip, self.timeout, self.status = target, ip, timeout, status
        self.requests = []
        self.resp = None
        FakePool.instances.append(self)

    def urlopen(self, method, path, **kw):
        self.requests.append((method, path, kw))
        self.resp = FakeResp(self.status)
        return self.resp

    def close(self):
        pass


@pytest.fixture
def net(monkeypatch):
    """Installs FakeDNS + FakePool; returns a helper to configure them."""
    FakePool.instances = []
    state = {"status": 200}
    dns = FakeDNS({"hooks.example.com": [["93.184.216.34"]]})
    monkeypatch.setattr(webhook_guard.socket, "getaddrinfo", dns)
    monkeypatch.setattr(webhook_guard, "_make_pool",
                        lambda target, ip, timeout: FakePool(target, ip, timeout, state["status"]))

    class Net:
        pass

    n = Net()
    n.dns, n.state = dns, state
    return n


# ── URL validation ────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://10.0.0.5/hook",                      # private
    "https://192.168.1.10/hook",
    "https://127.0.0.1/hook",                     # loopback
    "https://[::1]/hook",                         # IPv6 loopback
    "https://169.254.169.254/latest/meta-data/",  # cloud metadata
    "https://[fd00:ec2::254]/latest/meta-data/",  # AWS IPv6 metadata
    "https://[::ffff:10.0.0.1]/hook",             # IPv4-mapped private
    "https://100.64.0.1/hook",                    # CGNAT
    "https://224.0.0.1/hook",                     # multicast
    "https://0.0.0.0/hook",                       # unspecified
    "https://localhost/hook",
    "https://redis/hook",                         # compose service name
])
def test_internal_targets_rejected(net, url):
    with pytest.raises(WebhookUrlError) as ei:
        validate_webhook_url(url)
    assert ei.value.permanent


@pytest.mark.parametrize("url", [
    "http://hooks.example.com/hook",              # not https
    "https://user:pass@hooks.example.com/hook",   # credentials
    "https://token@hooks.example.com/hook",
    "ftp://hooks.example.com/hook",
    "https:///nohost",
])
def test_bad_scheme_or_credentials_rejected(net, url):
    with pytest.raises(WebhookUrlError):
        validate_webhook_url(url)


def test_hostname_resolving_to_private_rejected(net):
    net.dns.table["evil.example.com"] = [["93.184.216.34", "10.1.2.3"]]
    with pytest.raises(WebhookUrlError, match="private"):
        validate_webhook_url("https://evil.example.com/hook")


def test_unresolvable_host_rejected_not_waved_through(net):
    with pytest.raises(WebhookUrlError) as ei:
        validate_webhook_url("https://nxdomain.example.com/hook")
    assert ei.value.permanent is False


def test_public_https_accepted(net):
    t = validate_webhook_url("https://hooks.example.com/cb?x=1")
    assert t.host == "hooks.example.com" and t.port == 443
    assert t.path == "/cb?x=1" and t.ips == ("93.184.216.34",)


# ── Sending ───────────────────────────────────────────────────────────

def test_post_pins_validated_ip_and_disables_redirects(net):
    res = webhook_guard.post_webhook("https://hooks.example.com/cb", b"{}", {"X": "1"})
    assert res.success and res.status_code == 200
    (pool,) = FakePool.instances
    assert pool.ip == "93.184.216.34"                     # connects to the checked IP
    assert pool.target.host == "hooks.example.com"        # TLS/SNI for the name
    assert pool.timeout == 10
    method, path, kw = pool.requests[0]
    assert (method, path) == ("POST", "/cb")
    assert kw["redirect"] is False and kw["retries"] is False
    assert kw["headers"]["Host"] == "hooks.example.com"
    assert pool.resp.read_sizes == [webhook_guard.MAX_RESPONSE_BYTES]


def test_redirect_is_not_followed(net):
    net.state["status"] = 302
    res = webhook_guard.post_webhook("https://hooks.example.com/cb", b"{}", {})
    assert not res.success and "redirect not followed" in res.error
    assert len(FakePool.instances) == 1 and len(FakePool.instances[0].requests) == 1


def test_dns_rebinding_rejected_at_send(net):
    """Registration sees a public address; by delivery time the name points
    at the metadata service. The send must re-resolve and refuse."""
    net.dns.table["rebind.example.com"] = [["93.184.216.34"], ["169.254.169.254"]]
    validate_webhook_url("https://rebind.example.com/cb")          # registration
    with pytest.raises(WebhookUrlError):
        webhook_guard.post_webhook("https://rebind.example.com/cb", b"{}", {})
    assert FakePool.instances == []                               # nothing was sent


# ── deliver_webhook_task end to end ───────────────────────────────────

class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, row):
        self.row = row

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _Res([self.row] if self.row else [])


class _SB:
    def __init__(self, row):
        self.row = row

    def table(self, name):
        return _Q(self.row)


@pytest.fixture
def task_env(net, monkeypatch):
    rc = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", rc)

    def install(url):
        row = {"webhook_url": url, "secret_hash": "h" * 64, "is_active": True}
        monkeypatch.setattr("app.core.database.get_service_client", lambda: _SB(row))

    net.rc, net.install = rc, install
    return net


def _logs(rc):
    import json
    return [json.loads(x) for x in rc.lrange("webhook:logs:u1", 0, -1)]


def test_task_refuses_rebound_target_without_retry(task_env):
    from app.tasks.webhook_tasks import deliver_webhook_task
    task_env.dns.table["rebind.example.com"] = [["127.0.0.1"]]
    task_env.install("https://rebind.example.com/cb")
    deliver_webhook_task("u1", {"job_id": "j1"})        # no Retry raised
    assert FakePool.instances == []
    (entry,) = _logs(task_env.rc)
    assert entry["success"] is False and entry["error"].startswith("blocked:")


def test_task_delivers_to_public_target(task_env):
    from app.tasks.webhook_tasks import deliver_webhook_task
    task_env.install("https://hooks.example.com/cb")
    deliver_webhook_task("u1", {"job_id": "j1"})
    (pool,) = FakePool.instances
    assert pool.ip == "93.184.216.34"
    _, _, kw = pool.requests[0]
    assert kw["headers"]["X-VidGrab-Signature"].startswith("sha256=")
    (entry,) = _logs(task_env.rc)
    assert entry["success"] is True and entry["status_code"] == 200


# ── Registration ──────────────────────────────────────────────────────

def test_register_rejects_internal_url(net, monkeypatch):
    from app.api import webhook as webhook_api
    monkeypatch.setattr(webhook_api, "_require_pro", lambda user: None)
    inserted = []
    monkeypatch.setattr(webhook_api, "get_service_client", lambda: inserted.append(1))
    body = webhook_api.WebhookRegisterRequest(webhook_url="https://169.254.169.254/x")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(webhook_api.register_webhook(body, user={"id": "u1"}))
    assert ei.value.status_code == 400
    assert inserted == []


def test_partner_register_rejects_internal_url(net, monkeypatch):
    from app.api import partner as partner_api
    monkeypatch.setattr(partner_api, "_require_scope", lambda *a, **k: None)

    class _Tenant:
        tenant_id = "t1"

        def has_feature(self, f):
            return True

    body = partner_api.WebhookRegisterRequest(url="https://[::1]/x", events=["job.completed"])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(partner_api.register_webhook(body, tenant=_Tenant()))
    assert ei.value.status_code == 422


def test_partner_dispatcher_refuses_private_target(net):
    from app.services import webhook_dispatcher
    net.dns.table["p.example.com"] = [["10.0.0.9"]]
    ok = asyncio.run(webhook_dispatcher._deliver_to_endpoint(
        {"id": "e1", "url": "https://p.example.com/cb"}, b"{}", "sig"))
    assert ok is False and FakePool.instances == []
