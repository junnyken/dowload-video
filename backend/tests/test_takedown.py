"""Task #6335 — takedown requests: validation, no raw IP stored, rate limit, admin."""
import json

import pytest
from fastapi.testclient import TestClient

from app.api.admin import verify_admin
from app.api import takedown
from app.main import app as fastapi_app
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
URL = "/api/v1/takedown"
ADMIN = "/api/v1/admin/takedown"
IP = "203.0.113.77"


def body(**kw):
    d = {"name": "Nguyễn A", "email": "a@example.com", "capacity": "owner",
         "content_url": "https://www.youtube.com/watch?v=abc", "related": "",
         "description": "Video của tôi bị đăng lại", "declaration": True, "website": ""}
    d.update(kw)
    return d


def post(b, ip=IP):
    return client.post(URL, json=b, headers={"X-Forwarded-For": ip})


@pytest.fixture
def admin():
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    yield
    fastapi_app.dependency_overrides.pop(verify_admin, None)


@pytest.fixture
def audit(monkeypatch):
    calls = []
    monkeypatch.setattr(takedown, "log_admin_action", lambda request, action, **kw: calls.append((action, kw)))
    return calls


@pytest.mark.parametrize("patch", [
    {"email": "not-an-email"}, {"name": "  "}, {"capacity": "x"},
    {"content_url": "http://insecure.example/x"}, {"content_url": "javascript:alert(1)"},
    {"description": "x" * 2001}, {"related": "y" * 2001}, {"declaration": False},
])
def test_validation_errors(rc, patch):
    assert post(body(**patch)).status_code == 422
    assert rc.llen(takedown.LIST_KEY) == 0


def test_stored_without_raw_ip(rc):
    r = post(body())
    assert r.status_code == 201 and r.json()["ok"]
    raw = rc.lrange(takedown.LIST_KEY, 0, -1)
    assert len(raw) == 1
    assert IP not in raw[0]
    rec = json.loads(raw[0])
    assert rec["status"] == "new" and rec["id"] == r.json()["id"] and rec["created_at"]
    assert len(rec["ip_hash"]) == 64


def test_honeypot_stores_nothing(rc):
    assert post(body(website="http://spam")).status_code == 201
    assert rc.llen(takedown.LIST_KEY) == 0


def test_rate_limit_per_ip(rc):
    for _ in range(5):
        assert post(body()).status_code == 201
    assert post(body()).status_code == 429
    assert post(body(), ip="198.51.100.9").status_code == 201


def test_list_is_capped(rc, monkeypatch):
    monkeypatch.setattr(takedown, "LIST_CAP", 3)
    monkeypatch.setattr(takedown, "RATE_LIMIT", 100)
    for i in range(5):
        post(body(name=f"n{i}"))
    assert rc.llen(takedown.LIST_KEY) == 3


def test_admin_auth_required(rc):
    assert client.get(ADMIN).status_code in (401, 403)
    assert client.post(f"{ADMIN}/abc/status", json={"status": "done"}).status_code in (401, 403)


def test_admin_list_newest_first_and_status_change(rc, admin, audit):
    a = post(body(name="first")).json()["id"]
    b = post(body(name="second")).json()["id"]
    items = client.get(ADMIN).json()["items"]
    assert [i["id"] for i in items] == [b, a]
    r = client.post(f"{ADMIN}/{a}/status", json={"status": "reviewing"})
    assert r.status_code == 200
    assert {i["id"]: i["status"] for i in client.get(ADMIN).json()["items"]} == {a: "reviewing", b: "new"}
    assert audit and audit[0][0] == "admin.takedown.status" and audit[0][1]["resource_id"] == a


def test_status_rejects_bad_value_and_unknown_id(rc, admin, audit):
    a = post(body()).json()["id"]
    assert client.post(f"{ADMIN}/{a}/status", json={"status": "bogus"}).status_code == 422
    assert client.post(f"{ADMIN}/nope/status", json={"status": "done"}).status_code == 404
    assert audit == []
