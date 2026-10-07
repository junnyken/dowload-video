"""
Task #6125 (PLAN-32E P1 step 4) — POST /admin/desktop/allowance: an admin
grants extra downloads for today or resets today's count, for the allowance a
machine is counted under (signed in → the account, guest → the machine).
Holds: admin only · grant raises the limit every check path reads (web check,
app claim, snapshot) and expires with the day · total grant capped · reset
sets used to 0 and leaves other requesters alone · bad input → 400, unknown
machine → 404, Redis down → 503 · audited · device list shows the grant.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import admin_desktop
from app.core import quotas
from app.main import app as fastapi_app
from tests._china_fakes import rc  # noqa: F401
from tests.test_admin_desktop import _env, admin, db, h, seed_devices  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
URL = "/api/v1/admin/desktop/allowance"
DEV = "/api/v1/admin/desktop/devices"
UID = "11111111-aaaa-bbbb-cccc-000000000001"


@pytest.fixture
def audit(monkeypatch):
    calls = []
    monkeypatch.setattr(admin_desktop, "log_admin_action",
                        lambda request, action, **kw: calls.append((action, kw)))
    return calls


def guest(n=1):
    return quotas.QuotaRequester(quotas.REQ_DEVICE, h(n)[:32])


def user():
    return quotas.QuotaRequester(quotas.REQ_USER, UID, tier="free")


def use(req, n, platform="tiktok"):
    for i in range(n):
        quotas.record_platform_download(req, platform, f"https://www.tiktok.com/@a/video/{i}{req.key[-4:]}")


def post(body):
    return client.post(URL, json=body)


def test_admin_only(rc, db):
    r = post({"device_id": h(1)[:16], "action": "reset", "reason": "khách báo lỗi"})
    assert r.status_code in (401, 403)


def test_grant_raises_todays_limit_for_a_guest_machine(rc, db, admin, audit, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", "5")
    seed_devices(db)
    use(guest(), 5)
    assert not quotas.check_platform_quota(guest(), "tiktok", "https://www.tiktok.com/@a/video/new")["allowed"]

    r = post({"device_id": h(1)[:16], "action": "grant", "amount": 3, "reason": "khách báo lỗi tải"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counted_as"] == "device" and body["bonus_today"] == 3
    assert body["limit_today"] == 8 and body["used_today"] == 5

    q = quotas.check_platform_quota(guest(), "tiktok", "https://www.tiktok.com/@a/video/new")
    assert q["allowed"] and q["remaining"] == 3
    assert quotas.platform_usage_snapshot(guest())["limit"] == 8
    # day-only: the key expires by the next UTC midnight
    ttl = rc.ttl(f"vidgrab:quota:bonus:{guest().key}:{quotas._utc_day()}")
    assert 0 < ttl <= 86400 + 60
    # other machines unchanged
    assert quotas.platform_limit(guest(3)) == 5

    action, kw = audit[0]
    assert action == "admin.desktop.allowance_grant" and kw["resource_id"] == h(1)[:16]
    assert kw["metadata"]["amount"] == 3 and kw["metadata"]["reason"] == "khách báo lỗi tải"
    assert h(1) not in str(kw)  # the full machine hash never reaches the log


def test_signed_in_machine_grants_the_account(rc, db, admin, audit, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_USER", "20")
    seed_devices(db)
    r = post({"device_id": h(2)[:16], "action": "grant", "amount": 10, "reason": "bù lỗi máy chủ"})
    assert r.status_code == 200 and r.json()["counted_as"] == "user"
    assert quotas.platform_limit(user()) == 30
    assert quotas.platform_bonus(guest(2)) == 0


def test_grant_total_is_capped(rc, db, admin, audit):
    seed_devices(db)
    for _ in range(5):
        r = post({"device_id": h(1)[:16], "action": "grant", "amount": 50, "reason": "thử trần"})
    assert r.json()["bonus_today"] == quotas.BONUS_DAY_MAX


def test_reset_sets_used_to_zero_only_for_that_machine(rc, db, admin, audit, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", "5")
    seed_devices(db)
    use(guest(1), 5)
    use(guest(3), 2)
    r = post({"device_id": h(1)[:16], "action": "reset", "reason": "đặt lại cho khách"})
    assert r.status_code == 200, r.text
    assert r.json()["used_today"] == 0 and r.json()["removed"] >= 2  # total + tiktok bucket
    assert quotas.platform_used(guest(1), quotas._TOTAL_BUCKET) == 0
    assert quotas.platform_used(guest(3), quotas._TOTAL_BUCKET) == 2
    assert audit[0][0] == "admin.desktop.allowance_reset"


def test_device_list_shows_the_grant(rc, db, admin, audit, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", "5")
    seed_devices(db)
    post({"device_id": h(1)[:16], "action": "grant", "amount": 4, "reason": "khách VIP thử"})
    row = next(d for d in client.get(DEV).json()["devices"] if d["id"] == h(1)[:16])
    assert row["today"]["limit"] == 9 and row["today"]["bonus"] == 4


@pytest.mark.parametrize("body,code", [
    ({"device_id": "xyz", "action": "grant", "amount": 1, "reason": "abc"}, "bad_device_id"),
    ({"device_id": "%" * 16, "action": "grant", "amount": 1, "reason": "abc"}, "bad_device_id"),
    ({"action": "grant", "amount": 1, "reason": "abc"}, "bad_device_id"),
    ({"device_id": "0" * 16, "action": "delete", "reason": "abc"}, "bad_action"),
    ({"device_id": "0" * 16, "action": "grant", "amount": 0, "reason": "abc"}, "bad_amount"),
    ({"device_id": "0" * 16, "action": "grant", "amount": 51, "reason": "abc"}, "bad_amount"),
    ({"device_id": "0" * 16, "action": "grant", "amount": "5", "reason": "abc"}, "bad_amount"),
    ({"device_id": "0" * 16, "action": "grant", "amount": True, "reason": "abc"}, "bad_amount"),
    ({"device_id": "0" * 16, "action": "reset", "reason": "ab"}, "reason_required"),
])
def test_bad_input(rc, db, admin, audit, body, code):
    seed_devices(db)
    r = post(body)
    assert r.status_code == 400 and r.json()["detail"]["error"] == code
    assert audit == []


def test_unknown_machine_404(rc, db, admin, audit):
    seed_devices(db)
    r = post({"device_id": "f" * 16, "action": "reset", "reason": "không có máy"})
    assert r.status_code == 404 and audit == []


def test_redis_down_503_and_not_audited(rc, db, admin, audit, monkeypatch):
    seed_devices(db)

    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(quotas, "grant_platform_bonus", boom)
    r = post({"device_id": h(1)[:16], "action": "grant", "amount": 2, "reason": "redis hỏng"})
    assert r.status_code == 503 and r.json()["detail"]["error"] == "redis_unavailable"
    assert audit == []


def test_bonus_read_failure_counts_as_zero(monkeypatch, rc):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", "5")
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: (_ for _ in ()).throw(ConnectionError()))
    assert quotas.platform_limit(guest()) == 5
