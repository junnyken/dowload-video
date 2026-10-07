"""
Task #6087 (PLAN-32D P0) — /client/quota/* : Windows app downloads count toward
the daily allowance (guest 5 / signed-in 20, total across platforms).

Holds: flag off → 503 (app unchanged) · shadow never refuses but counts ·
enforce refuses guest (429, upsell signin) and user (403, upsell upgrade) ·
a machine (X-VG-Device) is its own guest bucket, capped per IP at 5 × 3 ·
same URL same day counts once · settle refunds a failure (≤ 10/day, own
claims only, once) · retro claims only CLIENT_QUOTA_OFFLINE_GRACE a day ·
claim-batch for channels · app and web share a signed-in user's 20.
fakeredis + TestClient, no network.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.core import quotas
from app.core.auth_middleware import get_optional_user
from app.main import app as fastapi_app, limiter
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)


def dev(n: int) -> str:
    return hashlib.sha256(f"machine-{n}".encode()).hexdigest()


@pytest.fixture(autouse=True)
def _env(monkeypatch, rc):
    from app.api import client_quota
    lims = {id(x): x for x in (limiter, client_quota.limiter)}.values()
    prev = [(x, x.enabled) for x in lims]
    for x in lims:
        x.enabled = False
    for k in ("CLIENT_QUOTA_MODE", "CLIENT_QUOTA_ENFORCE_FOR", "CLIENT_QUOTA_OFFLINE_GRACE",
              "CLIENT_QUOTA_REFUND_DAILY_MAX", "CLIENT_QUOTA_IP_MULT", "PLATFORM_DAILY_LIMIT_ANON",
              "PLATFORM_DAILY_LIMIT_USER", "QUOTA_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "true")
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "enforce")
    # device upsert: no Supabase in tests (best effort path must not break)
    monkeypatch.setattr("app.core.database.get_service_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    yield
    fastapi_app.dependency_overrides.pop(get_optional_user, None)
    for x, was in prev:
        x.enabled = was


def as_user(uid: str):
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": uid}


def claim(url: str, device: str | None = None, **body):
    h = {"X-VG-Device": device} if device else {}
    return client.post("/api/v1/client/quota/claim", json={"url": url, **body}, headers=h)


def V(i: int) -> str:
    return f"https://www.tiktok.com/@a/video/{7300000000000000000 + i}"


class TestFlags:

    def test_off_answers_503_everywhere(self, monkeypatch):
        monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "false")
        assert claim(V(1)).json()["error_code"] == "client_quota_disabled"
        for r in (client.get("/api/v1/client/quota"),
                  client.post("/api/v1/client/quota/settle", json={"claimId": "x" * 24, "outcome": "failed"}),
                  client.post("/api/v1/client/quota/claim-batch", json={"items": [{"url": V(1)}]})):
            assert r.status_code == 503

    def test_shadow_counts_but_never_refuses(self, monkeypatch):
        monkeypatch.setenv("CLIENT_QUOTA_MODE", "shadow")
        for i in range(7):
            r = claim(V(i), device=dev(1))
            assert r.status_code == 200 and r.json()["allowed"] is True
        last = r.json()
        # shadow measures real use: past the limit is still counted
        assert last["overLimit"] is True and last["mode"] == "shadow" and last["usedToday"] == 7


class TestEnforce:

    def test_machine_guest_gets_five_then_signin_prompt(self):
        for i in range(5):
            assert claim(V(i), device=dev(1)).json()["remaining"] == 4 - i
        r = claim(V(9), device=dev(1))
        body = r.json()
        assert r.status_code == 429 and body["error_code"] == "quota_exceeded_daily"
        assert body["upsell"] == "signin" and "Đăng nhập" in body["detail"]

    def test_same_url_counts_once(self):
        a, b = claim(V(1), device=dev(1)).json(), claim(V(1), device=dev(1)).json()
        assert a["usedToday"] == 1 and b["usedToday"] == 1 and b["alreadyCounted"] is True

    def test_user_gets_twenty_and_upgrade_prompt_shared_with_web(self):
        as_user("u-1")
        req = quotas.QuotaRequester(quotas.REQ_USER, "u-1")
        quotas.record_platform_download(req, "youtube", "https://www.youtube.com/watch?v=web1")  # web download
        for i in range(19):
            assert claim(V(i)).status_code == 200
        r = claim(V(99))
        assert r.status_code == 403 and r.json()["upsell"] == "upgrade"

    def test_machines_behind_one_ip_capped_at_15(self):
        n = 0
        for d in range(4):
            for i in range(5):
                r = claim(V(d * 10 + i), device=dev(d))
                n += r.status_code == 200
        assert n == 15
        assert r.status_code == 429 and r.json()["reason"] == "ip_limit"

    def test_invalid_device_header_is_ignored_and_ip_bucket_used(self):
        r = claim(V(1), device="not-a-hash")
        assert r.json()["requester"] == "anon"

    def test_enforce_for_subset(self, monkeypatch):
        monkeypatch.setenv("CLIENT_QUOTA_ENFORCE_FOR", "user")
        for i in range(6):
            assert claim(V(i), device=dev(1)).status_code == 200   # devices only shadowed

    def test_bad_url(self):
        assert claim("ftp://x/y", device=dev(1)).status_code == 400


class TestSettle:

    def _settle(self, cid, outcome, device=None):
        h = {"X-VG-Device": device} if device else {}
        return client.post("/api/v1/client/quota/settle", json={"claimId": cid, "outcome": outcome}, headers=h)

    def test_failed_download_is_refunded_once(self):
        cid = claim(V(1), device=dev(1)).json()["claimId"]
        r = self._settle(cid, "failed", dev(1)).json()
        assert r["refunded"] is True and r["usedToday"] == 0
        assert self._settle(cid, "failed", dev(1)).json()["refunded"] is False   # once
        assert claim(V(1), device=dev(1)).json()["usedToday"] == 1                # retry counts again

    def test_completed_is_not_refunded(self):
        cid = claim(V(1), device=dev(1)).json()["claimId"]
        assert self._settle(cid, "completed", dev(1)).json()["refunded"] is False

    def test_cannot_refund_someone_elses_claim(self):
        cid = claim(V(1), device=dev(1)).json()["claimId"]
        assert self._settle(cid, "failed", dev(2)).json()["refunded"] is False
        assert claim(V(2), device=dev(1)).json()["usedToday"] == 2

    def test_refunds_capped_per_day(self, monkeypatch):
        monkeypatch.setenv("CLIENT_QUOTA_REFUND_DAILY_MAX", "2")
        got = [self._settle(claim(V(i), device=dev(1)).json()["claimId"], "failed", dev(1)).json()["refunded"]
               for i in range(4)]
        assert got == [True, True, False, False]

    def test_refund_frees_the_ip_cap_too(self):
        first = None
        for d in range(3):
            for i in range(5):
                c = claim(V(d * 10 + i), device=dev(d)).json()
                first = first or c["claimId"]
        assert claim(V(99), device=dev(5)).json()["reason"] == "ip_limit"
        assert self._settle(first, "failed", dev(0)).json()["refunded"] is True
        assert claim(V(99), device=dev(5)).status_code == 200


class TestRetroAndBatch:

    def test_retro_skips_the_check_only_grace_times(self, monkeypatch):
        monkeypatch.setenv("CLIENT_QUOTA_OFFLINE_GRACE", "2")
        for i in range(5):
            claim(V(i), device=dev(1))
        r1 = claim(V(10), device=dev(1), retro=True)
        r2 = claim(V(11), device=dev(1), retro=True)
        r3 = claim(V(12), device=dev(1), retro=True)
        assert r1.status_code == 200 and r2.status_code == 200 and r3.status_code == 429

    def test_batch_cuts_at_the_allowance(self):
        items = [{"url": V(i)} for i in range(8)]
        r = client.post("/api/v1/client/quota/claim-batch", json={"items": items},
                        headers={"X-VG-Device": dev(1)}).json()
        allowed = [x["allowed"] for x in r["items"]]
        assert allowed == [True] * 5 + [False] * 3 and r["remaining"] == 0

    def test_snapshot_shows_device_code(self):
        claim(V(1), device=dev(7))
        r = client.get("/api/v1/client/quota", headers={"X-VG-Device": dev(7)}).json()
        assert r["deviceCode"] == dev(7)[:8].upper() and r["usedToday"] == 1 and r["limit"] == 5
        assert r["enforced"] is True and r["offlineGrace"] == 3
