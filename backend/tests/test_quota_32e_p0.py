"""
PLAN-32E P0 — measure right before the 14/10 gate.

G2: /fetch-link (X-VG-Source: desktop) and /client/douyin/video count an app
guest under its machine (dev:) — the bucket of its local claims — with the
same per-IP cap as /client/quota/claim, so a made-up device id mints nothing.
The web (no desktop source header) still uses ip:, even if it sends
X-VG-Device. Guest refund cap (owner decision 2): 2/day for machines and
guests, users keep 10. Per-kind stats + top machines by over-limit.
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
IP = "testclient"   # TestClient's peer address = get_client_ip()


def dev(n: int) -> str:
    return hashlib.sha256(f"machine-{n}".encode()).hexdigest()


def IG(i: int) -> str:
    return f"https://www.instagram.com/p/post{i}/"


def DEV_REQ(n: int) -> quotas.QuotaRequester:
    return quotas.QuotaRequester(quotas.REQ_DEVICE, dev(n)[:32])


ANON = quotas.QuotaRequester(quotas.REQ_ANON, IP)


@pytest.fixture(autouse=True)
def _env(monkeypatch, rc, tmp_path):
    import app.api.routes as routes
    from app.api import client_douyin, client_quota
    from app.core import disk_guardrail, ssrf_guard
    lims = {id(x): x for x in (limiter, client_quota.limiter, routes.limiter, client_douyin.limiter,
                                 getattr(fastapi_app.state, "limiter", None)) if x}.values()
    prev = [(x, x.enabled) for x in lims]
    for x in lims:
        x.enabled = False
    for k in ("CLIENT_QUOTA_MODE", "CLIENT_QUOTA_ENFORCE_FOR", "CLIENT_QUOTA_OFFLINE_GRACE",
              "CLIENT_QUOTA_REFUND_DAILY_MAX", "CLIENT_QUOTA_REFUND_DAILY_MAX_GUEST", "CLIENT_QUOTA_IP_MULT",
              "PLATFORM_DAILY_LIMIT_ANON", "PLATFORM_DAILY_LIMIT_USER", "QUOTA_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "true")
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "shadow")
    monkeypatch.setattr("app.core.database.get_service_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    # /fetch-link without disk / DNS / extractor / Supabase
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
    monkeypatch.setattr(routes, "_preflight_disk_check", lambda: None)
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))

    async def _ok(url, *a, **k):
        return {"title": "t", "direct_mp4_url": "https://cdn.example.com/v.mp4", "original_url": url}
    monkeypatch.setattr(routes, "extract_video_info", _ok)
    yield
    fastapi_app.dependency_overrides.pop(get_optional_user, None)
    for x, was in prev:
        x.enabled = was


def fetch(url: str, device: str | None = None, source: str | None = "desktop"):
    h = {}
    if device:
        h["X-VG-Device"] = device
    if source:
        h["X-VG-Source"] = source
    return client.post("/api/v1/fetch-link", json={"url": url, "quality": "video"}, headers=h)


def claim(url: str, device: str | None = None, **body):
    h = {"X-VG-Device": device} if device else {}
    return client.post("/api/v1/client/quota/claim", json={"url": url, **body}, headers=h)


def settle(cid: str, outcome: str, device: str | None = None):
    h = {"X-VG-Device": device} if device else {}
    return client.post("/api/v1/client/quota/settle", json={"claimId": cid, "outcome": outcome}, headers=h)


def total(req) -> int:
    return quotas.platform_used(req, quotas._TOTAL_BUCKET)


# ── G2: /fetch-link ──────────────────────────────────────────────────────

class TestFetchLinkDevice:

    def test_app_guest_counted_under_its_machine_not_ip(self, rc):
        r = fetch(IG(1), device=dev(1))
        assert r.status_code == 200, r.text[:300]
        assert total(DEV_REQ(1)) == 1 and total(ANON) == 0
        assert quotas.device_ip_used(IP) == 1                     # takes one slot of the IP cap

    def test_web_without_header_still_uses_ip(self, rc):
        assert fetch(IG(1), source=None).status_code == 200
        assert total(ANON) == 1 and quotas.device_ip_used(IP) == 0

    @pytest.mark.parametrize("source", [None, "web"])
    def test_device_header_ignored_without_desktop_source(self, rc, source):
        """Only the app sends X-VG-Device; from anything else it must not
        move the request out of the ip: bucket (it would be a free reset)."""
        assert fetch(IG(1), device=dev(1), source=source).status_code == 200
        assert total(ANON) == 1 and total(DEV_REQ(1)) == 0

    def test_claim_then_server_fallback_same_url_counts_once(self, rc):
        c = claim(IG(1), device=dev(1)).json()
        assert c["usedToday"] == 1
        assert fetch(IG(1), device=dev(1)).status_code == 200
        assert total(DEV_REQ(1)) == 1 and total(ANON) == 0
        assert quotas.device_ip_used(IP) == 1

    def test_made_up_device_ids_are_capped_per_ip(self, rc):
        """3 fresh machine ids × 5 = 15 = 5 × IP_MULT 3; the 4th fake id is
        refused although its own bucket is empty."""
        n = 0
        for d in range(3):
            for i in range(5):
                n += fetch(IG(d * 10 + i), device=dev(d)).status_code == 200
        assert n == 15 and quotas.device_ip_used(IP) == 15
        r = fetch(IG(99), device=dev(42))
        assert r.status_code == 429, r.text[:300]
        body = r.json()["detail"]
        assert body["error_code"] == "quota_exceeded_daily" and body["reason"] == "ip_limit"
        assert "Đăng nhập" in body["message"] and body["remaining"] == 0
        assert total(DEV_REQ(42)) == 0                           # nothing counted
        from app.core.redis_client import get_redis
        day = quotas._utc_day()
        assert get_redis().hget(f"vidgrab:stats:route:{day}", "kind:device|refused") == "1"

    def test_already_counted_url_passes_the_ip_cap(self, rc):
        assert fetch(IG(1), device=dev(1)).status_code == 200
        rc.set(quotas._device_ip_key(IP), 15)                    # IP now full (5 x 3)
        assert fetch(IG(1), device=dev(1)).status_code == 200    # retry of a paid URL
        assert fetch(IG(2), device=dev(1)).status_code == 429    # a new one is not

    def test_ip_cap_does_not_touch_signed_in_users(self, rc, monkeypatch):
        import app.api.routes as routes
        monkeypatch.setattr(routes, "increment_usage", lambda *a, **k: None)
        monkeypatch.setattr(routes, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("no db")))

        async def _noop(*a, **k):
            return None
        monkeypatch.setattr("app.core.metering.record_download", _noop)
        rc.set(quotas._device_ip_key(IP), 15)
        fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1", "tier": "free"}
        r = fetch(IG(1), device=dev(1))
        assert r.status_code == 200, r.text[:300]
        assert total(quotas.QuotaRequester(quotas.REQ_USER, "u-1")) == 1

    def test_machine_gets_five_on_the_server_route_then_429(self, rc):
        for i in range(5):
            assert fetch(IG(i), device=dev(1)).status_code == 200
        r = fetch(IG(9), device=dev(1))
        assert r.status_code == 429 and r.json()["detail"]["error_code"] == "quota_exceeded_daily"
        assert r.json()["detail"].get("reason") is None           # daily limit, not the IP cap

    def test_ok_stat_carries_the_kind(self, rc):
        fetch(IG(1), device=dev(1))
        h = rc.hgetall(f"vidgrab:stats:route:{quotas._utc_day()}")
        assert h["server|ok"] == "1" and h["kind:device|ok"] == "1"


# ── G2: /client/douyin/video ─────────────────────────────────────────────

DY = "https://www.douyin.com/video/7300000000000000009"


class TestDouyinVideoDevice:

    @pytest.fixture
    def layer(self, monkeypatch):
        """Douyin layer on, provider faked (no Apify)."""
        from app.services.china_platforms import integration
        from app.services.china_platforms.provider_router import ProviderRouter
        monkeypatch.setattr(integration, "_layer_active", lambda p: True)

        class Res:
            media_id, canonical_url, title, uploader = "1", DY, "t", "u"
            thumbnail_url, duration_sec, cache_hit = None, 1, False

            def primary_video_url(self):
                return "https://cdn.example.com/v.mp4"

            def audio_url(self):
                return None
        calls = []

        async def _resolve(self, req, ctx):
            calls.append(req.url)
            return Res()
        monkeypatch.setattr(ProviderRouter, "resolve", _resolve)
        monkeypatch.setattr("app.services.china_platforms.request_cache.media_expiry_ts", lambda r: None)
        return calls

    def _video(self, device=None, url=DY):
        h = {"X-VG-Device": device} if device else {}
        return client.post("/api/v1/client/douyin/video", json={"url": url}, headers=h)

    def test_counts_under_machine(self, rc, layer):
        r = self._video(device=dev(1))
        assert r.status_code == 200, r.text[:300]
        assert quotas.platform_used(DEV_REQ(1), "douyin") == 1 and total(ANON) == 0
        assert quotas.device_ip_used(IP) == 1

    def test_without_header_counts_under_ip(self, rc, layer):
        assert self._video().status_code == 200
        assert total(ANON) == 1 and quotas.device_ip_used(IP) == 0

    def test_fake_device_refused_at_ip_cap_before_paid_call(self, rc, layer):
        rc.set(quotas._device_ip_key(IP), 15)
        r = self._video(device=dev(42))
        assert r.status_code == 429 and r.json()["reason"] == "ip_limit"
        assert r.json()["error_code"] == "quota_exceeded_daily"
        assert layer == []                                        # no provider call
        assert total(DEV_REQ(42)) == 0

    def test_claim_then_douyin_same_url_counts_once(self, rc, layer):
        assert claim(DY, device=dev(1)).status_code == 200
        assert self._video(device=dev(1)).status_code == 200
        assert total(DEV_REQ(1)) == 1 and quotas.device_ip_used(IP) == 1


# ── the cap itself (negative control: must fail without it) ──────────────

def test_ip_cap_helper_is_what_refuses(monkeypatch, rc):
    """If ip_cap_exceeded always said no, the fake 4th machine would pass:
    proves the refusal above comes from the cap, not from something else."""
    from app.api import client_quota
    rc.set(quotas._device_ip_key(IP), 15)
    assert fetch(IG(1), device=dev(42)).status_code == 429
    monkeypatch.setattr(client_quota, "ip_cap_exceeded", lambda *a, **k: False)
    assert fetch(IG(1), device=dev(42)).status_code == 200


# ── guest refund cap (owner decision 2) ──────────────────────────────────

class TestGuestRefundCap:

    def test_machine_gets_two_refunds(self, rc):
        got = [settle(claim(IG(i), device=dev(1)).json()["claimId"], "failed", dev(1)).json()["refunded"]
               for i in range(4)]
        assert got == [True, True, False, False]

    def test_guest_ip_gets_two_refunds(self, rc):
        got = [settle(claim(IG(i)).json()["claimId"], "failed").json()["refunded"] for i in range(3)]
        assert got == [True, True, False]

    def test_user_keeps_ten(self, rc):
        fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1"}
        got = [settle(claim(IG(i)).json()["claimId"], "failed").json()["refunded"] for i in range(12)]
        assert got == [True] * 10 + [False, False]

    def test_guest_cap_env(self, rc, monkeypatch):
        monkeypatch.setenv("CLIENT_QUOTA_REFUND_DAILY_MAX_GUEST", "0")
        assert settle(claim(IG(1), device=dev(1)).json()["claimId"], "failed", dev(1)).json()["refunded"] is False

    def test_snapshot_reports_the_cap_of_the_requester(self, rc):
        assert client.get("/api/v1/client/quota", headers={"X-VG-Device": dev(1)}).json()["refundDailyMax"] == 2
        fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1"}
        assert client.get("/api/v1/client/quota").json()["refundDailyMax"] == 10


# ── per-kind stats + top machines ────────────────────────────────────────

class TestKindStats:

    def test_claims_counted_per_kind_and_over_top(self, rc):
        for i in range(7):                       # machine 1: 5 ok + 2 over (shadow)
            claim(IG(i), device=dev(1))
        claim(IG(50), device=dev(2))             # machine 2: 1 ok
        fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1"}
        claim(IG(60))                            # user: 1 ok
        cid = claim(IG(61)).json()["claimId"]
        settle(cid, "failed")                    # user: settle_failed + refunded
        fastapi_app.dependency_overrides.pop(get_optional_user, None)
        h = rc.hgetall(f"vidgrab:stats:route:{quotas._utc_day()}")
        assert h["kind:device|ok"] == "6" and h["kind:device|over_shadow"] == "2"
        assert h["kind:user|ok"] == "2" and h["kind:user|settle_failed"] == "1"
        assert h["kind:user|refunded"] == "1"
        assert h["local|ok"] == "8" and h["local|over_shadow"] == "2"     # route stats unchanged
        top = rc.zrevrange(f"vidgrab:stats:over_top:{quotas._utc_day()}", 0, -1, withscores=True)
        assert top == [(f"dev:{dev(1)[:32]}", 2.0)]
        assert 0 < rc.ttl(f"vidgrab:stats:over_top:{quotas._utc_day()}") <= 3 * 86400

    def test_retro_and_refused_per_kind(self, rc, monkeypatch):
        monkeypatch.setenv("CLIENT_QUOTA_MODE", "enforce")
        for i in range(5):
            claim(IG(i), device=dev(1))
        assert claim(IG(9), device=dev(1)).status_code == 429
        assert claim(IG(10), device=dev(1), retro=True).status_code == 200
        h = rc.hgetall(f"vidgrab:stats:route:{quotas._utc_day()}")
        assert h["kind:device|refused"] == "1" and h["kind:device|retro"] == "1"
