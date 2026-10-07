"""
Task #6126 (PLAN-32E §5.3, P2) — anomaly hints on the admin "App Windows"
page. Write side (app.core.desktop_signals) fed by the real app endpoints;
read side GET /admin/desktop/signals. Holds: claims / offline reports /
refunds / app versions / the IP cap leave the facts the list needs · each
signal appears at its threshold and not below it · the list is display only
(nothing refused) · Redis down → empty list with redis_ok false, writes never
break a claim · admin only. fakeredis + in-memory Supabase, no network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.admin import verify_admin
from app.core import desktop_signals, quotas
from app.core.auth_middleware import get_optional_user
from app.main import app as fastapi_app, limiter
from tests._china_fakes import rc  # noqa: F401
from tests.test_admin_desktop import FakeDB, h

client = TestClient(fastapi_app, raise_server_exceptions=False)
SIG = "/api/v1/admin/desktop/signals"
UID = "11111111-aaaa-bbbb-cccc-000000000001"


def V(i: int) -> str:
    return f"https://www.tiktok.com/@a/video/{7300000000000000000 + i}"


def day(n: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    d.tables["desktop_downloads"] = []
    monkeypatch.setattr("app.core.database.get_service_client", lambda: d)
    return d


@pytest.fixture(autouse=True)
def _env(monkeypatch, rc):
    from app.api import client_quota
    lims = {id(x): x for x in (limiter, client_quota.limiter)}.values()
    prev = [(x, x.enabled) for x in lims]
    for x in lims:
        x.enabled = False
    for k in ("CLIENT_QUOTA_ENFORCE_FOR", "CLIENT_QUOTA_ENFORCE_USERS", "CLIENT_QUOTA_OFFLINE_GRACE",
              "CLIENT_QUOTA_REFUND_DAILY_MAX", "CLIENT_QUOTA_IP_MULT", "PLATFORM_DAILY_LIMIT_ANON",
              "PLATFORM_DAILY_LIMIT_USER", "QUOTA_SCOPE", "CLIENT_UPDATE_GATE_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "true")
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "shadow")
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    yield
    fastapi_app.dependency_overrides.pop(verify_admin, None)
    fastapi_app.dependency_overrides.pop(get_optional_user, None)
    for x, was in prev:
        x.enabled = was


def claim(url, device=None, **body):
    hd = {"X-VG-Device": device, "X-VG-Client": "0.9.0", "X-VG-Source": "desktop"} if device else {}
    return client.post("/api/v1/client/quota/claim", json={"url": url, **body}, headers=hd)


def signals(**q):
    r = client.get(SIG, params=q)
    assert r.status_code == 200, r.text
    return r.json()


def by(sig, body):
    return [x for x in body["signals"] if x["signal"] == sig]


# ── write side, through the real endpoints ──────────────────────────────────

def test_claims_leave_the_facts(rc, db):
    d = day()
    for i in range(2):
        assert claim(V(i), device=h(1)).json()["allowed"]
    assert claim(V(9), device=h(1), retro=True).json()["allowed"]
    guest = quotas.QuotaRequester(quotas.REQ_DEVICE, h(1)[:32])
    assert int(rc.hget(f"vidgrab:sig:app_dl:{d}", guest.key)) == 3
    assert int(rc.hget(f"vidgrab:sig:retro:{d}", guest.key)) == 1
    ips = [m.decode() if isinstance(m, bytes) else m for m in rc.smembers(f"vidgrab:sig:ips:{d}")]
    assert len(ips) == 1
    assert rc.sismember(f"vidgrab:sig:ipdev:{ips[0]}:{d}", h(1)[:32])
    # app version of every app call (middleware), incl. the /client/quota ones
    assert int(rc.hget(f"vidgrab:sig:ver:{d}", "0.9.0")) >= 3
    assert 0 < rc.ttl(f"vidgrab:sig:app_dl:{d}") <= desktop_signals.SIGNAL_TTL_SEC


def test_signed_in_machine_records_its_accounts(rc, db):
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": UID}
    assert claim(V(1), device=h(2)).json()["allowed"]
    d = day()
    assert rc.sismember(f"vidgrab:sig:devs:{d}", h(2)[:32])
    assert rc.sismember(f"vidgrab:sig:devusers:{h(2)[:32]}:{d}", UID)


def test_refund_is_recorded(rc, db):
    cid = claim(V(1), device=h(1)).json()["claimId"]
    r = client.post("/api/v1/client/quota/settle", json={"claimId": cid, "outcome": "failed"},
                    headers={"X-VG-Device": h(1)})
    assert r.json()["refunded"] is True
    guest = quotas.QuotaRequester(quotas.REQ_DEVICE, h(1)[:32])
    assert int(rc.hget(f"vidgrab:sig:refund:{day()}", guest.key)) == 1


def test_ip_cap_hit_is_recorded_in_shadow_too(rc, db, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", "1")
    monkeypatch.setenv("CLIENT_QUOTA_IP_MULT", "1")
    assert claim(V(1), device=h(1)).json()["allowed"]
    r = claim(V(2), device=h(2)).json()      # 2nd machine, same IP: over the cap, shadow lets it through
    assert r["allowed"] and r["overLimit"]
    assert sum(int(v) for v in rc.hgetall(f"vidgrab:sig:iplimit:{day()}").values()) == 1


def test_writes_never_break_a_claim(rc, db, monkeypatch):
    monkeypatch.setattr(desktop_signals, "_r", lambda: (_ for _ in ()).throw(ConnectionError()))
    assert claim(V(1), device=h(1)).json()["allowed"]


# ── read side ───────────────────────────────────────────────────────────────

def test_admin_only(rc, db):
    fastapi_app.dependency_overrides.pop(verify_admin, None)
    assert client.get(SIG).status_code in (401, 403)


def test_many_machines_behind_one_ip_at_the_threshold(rc, db):
    for n in range(4):
        rc.sadd(f"vidgrab:sig:ipdev:203.0.113.5:{day(1)}", h(n)[:32])
    for n in range(3):
        rc.sadd(f"vidgrab:sig:ipdev:198.51.100.1:{day(1)}", h(n)[:32])   # below: not listed
    rc.sadd(f"vidgrab:sig:ips:{day(1)}", "203.0.113.5", "198.51.100.1")
    rows = by("ip_many_machines", signals())
    assert [r["subject"]["ip"] for r in rows] == ["203.0.113.5"]
    assert rows[0]["value"] == 4 and rows[0]["last_day"] == day(1)


def test_ip_cap_on_three_days(rc, db):
    for n in (0, 2):
        rc.hset(f"vidgrab:sig:iplimit:{day(n)}", "203.0.113.7", 2)
    assert by("ip_many_machines", signals()) == []                    # 2 days: not yet
    rc.hset(f"vidgrab:sig:iplimit:{day(4)}", "203.0.113.7", 1)
    rows = by("ip_many_machines", signals())
    assert rows[0]["ip_limit_days"] == 3 and rows[0]["days_hit"] == 3
    assert by("ip_many_machines", signals(days=3)) == []              # outside the window


def test_several_accounts_on_one_machine(rc, db):
    dev = h(5)[:32]
    rc.sadd(f"vidgrab:sig:devs:{day()}", dev)
    rc.sadd(f"vidgrab:sig:devusers:{dev}:{day()}", "u1", "u2")
    assert by("device_many_accounts", signals()) == []
    rc.sadd(f"vidgrab:sig:devusers:{dev}:{day()}", "u3")
    row = by("device_many_accounts", signals())[0]
    assert row["value"] == 3 and row["subject"] == {"kind": "device", "code": dev[:8].upper(),
                                                   "device_id": dev[:16]}


def test_offline_reports_at_the_grace_on_three_days(rc, db):
    key = f"dev:{h(6)[:32]}"
    for n in (0, 1):
        rc.hset(f"vidgrab:sig:retro:{day(n)}", key, 3)
    rc.hset(f"vidgrab:sig:retro:{day(2)}", key, 2)                    # under the grace (3)
    assert by("offline_repeat", signals()) == []
    rc.hset(f"vidgrab:sig:retro:{day(5)}", key, 3)
    assert by("offline_repeat", signals())[0]["days_hit"] == 3


def test_refunds_many_and_a_large_share(rc, db):
    a, b = f"dev:{h(7)[:32]}", f"dev:{h(8)[:32]}"
    rc.hset(f"vidgrab:sig:refund:{day()}", mapping={a: 3, b: 3})
    rc.hset(f"vidgrab:sig:app_dl:{day()}", mapping={a: 5, b: 20})    # b: 3/20 is a small share
    rows = by("refund_high", signals())
    assert [r["subject"]["device_id"] for r in rows] == [h(7)[:16]]
    assert rows[0]["value"] == 3 and rows[0]["downloads"] == 5
    rc.hset(f"vidgrab:sig:refund:{day()}", a, 2)                      # under the minimum
    assert by("refund_high", signals()) == []


def test_finished_downloads_beyond_what_the_server_let_through(rc, db):
    now = datetime.now(timezone.utc)
    db.tables["desktop_downloads"] = (
        [{"user_id": UID, "state": "completed", "finished_at": now.isoformat()} for _ in range(9)]
        + [{"user_id": UID, "state": "failed", "finished_at": now.isoformat()} for _ in range(5)])
    db.tables["profiles"] = [{"id": UID, "email": "an@example.com"}]
    db.tables["desktop_devices"] = [{"device_hash": h(9), "user_id": UID, "last_seen": now.isoformat()}]
    rc.hset(f"vidgrab:sig:app_dl:{day()}", f"user:{UID}", 6)         # 9 > 6 + 3? no
    assert by("unclaimed_downloads", signals()) == []
    rc.hset(f"vidgrab:sig:app_dl:{day()}", f"user:{UID}", 5)         # 9 > 5 + 3: listed
    row = by("unclaimed_downloads", signals())[0]
    assert row["value"] == 9 and row["downloads"] == 5
    assert row["subject"]["email"] == "an@example.com" and row["subject"]["device_id"] == h(9)[:16]


def test_versions_per_day_and_shape(rc, db):
    rc.hset(f"vidgrab:sig:ver:{day()}", mapping={"0.9.0": 4, "none": 2})
    body = signals(days=40)
    assert body["days"] == 31 and body["redis_ok"] and body["history_ok"]
    assert body["versions"][day()] == {"0.9.0": 4, "none": 2}
    assert body["thresholds"]["offline_grace"] == 3 and body["signals"] == []


def test_redis_down(db, monkeypatch):
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: (_ for _ in ()).throw(ConnectionError()))
    body = signals()
    assert body["redis_ok"] is False and body["signals"] == []
