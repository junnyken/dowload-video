"""
Task #6125 (PLAN-32E §6) — POST /admin/desktop/ip/reset: an admin clears
today's app-guest counter of one network (the IP cap, reason "ip_limit"),
e.g. an office behind one NAT. Holds: admin only · the capped machine may
claim again · other IPs and each machine's own allowance unchanged · IP is
validated and normalised · reason required · Redis down → 503, not audited ·
audited on success.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import admin_desktop, client_quota
from app.core import quotas
from app.main import app as fastapi_app
from tests._china_fakes import rc  # noqa: F401
from tests.test_admin_allowance import audit, guest, use  # noqa: F401
from tests.test_admin_desktop import _env, admin, db  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
URL = "/api/v1/admin/desktop/ip/reset"
IP = "203.0.113.5"
OTHER = "198.51.100.1"
NEW = "https://www.tiktok.com/@a/video/999"


def fill_ip(ip: str, n: int):
    for _ in range(n):
        quotas.device_ip_add(ip, 1)


def test_admin_only(rc, db):
    assert client.post(URL, json={"ip": IP, "reason": "văn phòng"}).status_code in (401, 403)


def test_reset_lifts_the_ip_cap_for_that_network_only(rc, db, admin, audit, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", "5")
    cap = 5 * client_quota.ip_mult()
    fill_ip(IP, cap)
    fill_ip(OTHER, cap)
    use(guest(), 2)
    assert client_quota.ip_cap_exceeded(guest(), IP, "tiktok", NEW)

    r = client.post(URL, json={"ip": f"  {IP} ", "reason": "văn phòng chung NAT"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ip"] == IP and body["removed"] == cap and body["ip_cap"] == cap
    assert "reason" not in body

    assert not client_quota.ip_cap_exceeded(guest(), IP, "tiktok", NEW)
    assert client_quota.ip_cap_exceeded(guest(), OTHER, "tiktok", NEW)  # other network untouched
    assert quotas.platform_used(guest(), quotas._TOTAL_BUCKET) == 2  # machine's own count untouched

    action, kw = audit[0]
    assert action == "admin.desktop.ip_reset" and kw["resource_id"] == IP
    assert kw["metadata"]["removed"] == cap and kw["metadata"]["reason"] == "văn phòng chung NAT"


def test_nothing_to_reset_is_fine(rc, db, admin, audit):
    r = client.post(URL, json={"ip": "2001:db8:0:0::1", "reason": "kiểm tra"})
    assert r.status_code == 200 and r.json()["removed"] == 0
    assert r.json()["ip"] == "2001:db8::1"  # normalised like the stored key


@pytest.mark.parametrize("body,code", [
    ({"ip": "not-an-ip", "reason": "văn phòng"}, "bad_ip"),
    ({"ip": "203.0.113.5:443", "reason": "văn phòng"}, "bad_ip"),
    ({"reason": "văn phòng"}, "bad_ip"),
    ({"ip": IP}, "reason_required"),
    ({"ip": IP, "reason": "ab"}, "reason_required"),
])
def test_bad_input(rc, db, admin, audit, body, code):
    r = client.post(URL, json=body)
    assert r.status_code == 400 and r.json()["detail"]["error"] == code
    assert audit == []


def test_redis_down_503_and_not_audited(rc, db, admin, audit, monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(quotas, "reset_device_ip", boom)
    r = client.post(URL, json={"ip": IP, "reason": "redis hỏng"})
    assert r.status_code == 503 and r.json()["detail"]["error"] == "redis_unavailable"
    assert audit == []
