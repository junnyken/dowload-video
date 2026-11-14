"""
Universal Web Stream Capture highest-tier gate tests.

Verifies:
1. Only highest tiers (`team`, `enterprise`, `admin`) have `universal_stream=True`;
   `guest`, `free`, `starter`, `pro`, and `api` are `False`.
2. `plans.build_plans()` advertises `universal_stream=True` ONLY on `team`.
3. `POST /api/v1/client/quota/claim` refuses `streamCapture=True` and `.m3u8`/`.mpd`
   URLs with 403 `tier_required_feature` for guests and non-top-tier accounts
   (even when `CLIENT_QUOTA_ENABLED=false` or `CLIENT_QUOTA_MODE=shadow`), while
   allowing `team` and `admin`.
4. `GET /api/v1/client/quota` exposes `tier` and `universalStream`.
5. `POST /api/v1/fetch-link` refuses direct `.m3u8`/`.mpd` manifest URLs for
   non-top-tier users with 403 `tier_required_feature`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import entitlements, plans, quotas
from app.core.auth_middleware import get_optional_user
from app.main import app as fastapi_app, limiter
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _env(monkeypatch, rc):
    from app.api import client_quota
    lims = {id(x): x for x in (limiter, client_quota.limiter)}.values()
    prev = [(x, x.enabled) for x in lims]
    for x in lims:
        x.enabled = False
    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "true")
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "enforce")
    monkeypatch.setattr(plans, "_r", lambda: rc)
    monkeypatch.setattr(
        "app.core.database.get_service_client",
        lambda: (_ for _ in ()).throw(RuntimeError("no db")),
    )
    yield
    fastapi_app.dependency_overrides.pop(get_optional_user, None)
    for x, was in prev:
        x.enabled = was


def as_tier(monkeypatch, uid: str | None, tier: str | None):
    if uid is None:
        fastapi_app.dependency_overrides.pop(get_optional_user, None)
    else:
        fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": uid}
    monkeypatch.setattr("app.core.quotas._get_tier", lambda _uid: tier or "free")


def test_entitlement_matrix_highest_tier_only():
    for denied in (None, "", "guest", "free", "starter", "pro", "api"):
        assert entitlements.is_universal_stream_tier(denied) is False, f"{denied} must be denied"
    for allowed in ("team", "TEAM", "enterprise", "admin"):
        assert entitlements.is_universal_stream_tier(allowed) is True, f"{allowed} must be allowed"

    for tier_name in ("free", "starter", "pro", "api"):
        assert entitlements.PLAN_DEFS[tier_name]["features"]["universal_stream"] is False
    for tier_name in ("free", "starter", "pro"):
        assert quotas.TIER_PERMISSIONS[tier_name]["universal_stream"] is False
    for tier_name in ("team", "enterprise", "admin"):
        assert entitlements.PLAN_DEFS[tier_name]["features"]["universal_stream"] is True
        assert quotas.TIER_PERMISSIONS[tier_name]["universal_stream"] is True


def test_public_plans_table_marks_only_team():
    by_id = {t["id"]: t for t in plans.build_plans()["tiers"]}
    assert by_id["guest"]["universal_stream"] is False
    assert by_id["free"]["universal_stream"] is False
    assert by_id["starter"]["universal_stream"] is False
    assert by_id["pro"]["universal_stream"] is False
    assert by_id["team"]["universal_stream"] is True


def test_client_quota_snapshot_reports_universal_stream(monkeypatch):
    as_tier(monkeypatch, None, None)
    r_guest = client.get("/api/v1/client/quota")
    assert r_guest.status_code == 200
    assert r_guest.json()["universalStream"] is False

    as_tier(monkeypatch, "u-pro", "pro")
    r_pro = client.get("/api/v1/client/quota")
    assert r_pro.status_code == 200
    assert r_pro.json()["tier"] == "pro"
    assert r_pro.json()["universalStream"] is False

    as_tier(monkeypatch, "u-team", "team")
    r_team = client.get("/api/v1/client/quota")
    assert r_team.status_code == 200
    assert r_team.json()["tier"] == "team"
    assert r_team.json()["universalStream"] is True


@pytest.mark.parametrize(
    "uid,tier,expected_upsell",
    [
        (None, None, "signin"),
        ("u-free", "free", "upgrade"),
        ("u-starter", "starter", "upgrade"),
        ("u-pro", "pro", "upgrade"),
    ],
)
def test_client_claim_refuses_non_top_tier_for_stream_capture_and_m3u8(monkeypatch, uid, tier, expected_upsell):
    as_tier(monkeypatch, uid, tier)

    # 1. Explicit streamCapture=True (e.g. disguised .txt HLS or extensionless media stream)
    r1 = client.post(
        "/api/v1/client/quota/claim",
        json={"url": "https://cdn.example.com/hls/ep01/index.txt", "streamCapture": True},
    )
    assert r1.status_code == 403
    b1 = r1.json()
    assert b1["error_code"] == "tier_required_feature"
    assert b1["upsell"] == expected_upsell

    # 2. Direct .m3u8 URL even if client omitted streamCapture
    r2 = client.post(
        "/api/v1/client/quota/claim",
        json={"url": "https://cdn.example.com/vod/master.m3u8?token=abc"},
    )
    assert r2.status_code == 403
    assert r2.json()["error_code"] == "tier_required_feature"

    # 3. Normal social download remains unaffected!
    r_social = client.post(
        "/api/v1/client/quota/claim",
        json={"url": "https://www.tiktok.com/@creator/video/7400000000000000001"},
    )
    assert r_social.status_code == 200 and r_social.json()["allowed"] is True


def test_client_claim_enforces_tier_gate_even_when_quota_disabled_or_shadow(monkeypatch):
    as_tier(monkeypatch, "u-pro", "pro")

    monkeypatch.setenv("CLIENT_QUOTA_MODE", "shadow")
    r_shadow = client.post(
        "/api/v1/client/quota/claim",
        json={"url": "https://cdn.example.com/vod/master.m3u8", "streamCapture": True},
    )
    assert r_shadow.status_code == 403
    assert r_shadow.json()["error_code"] == "tier_required_feature"

    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "false")
    r_disabled = client.post(
        "/api/v1/client/quota/claim",
        json={"url": "https://cdn.example.com/vod/master.m3u8", "streamCapture": True},
    )
    assert r_disabled.status_code == 403
    assert r_disabled.json()["error_code"] == "tier_required_feature"


@pytest.mark.parametrize("tier", ["team", "enterprise", "admin"])
def test_client_claim_allows_top_tiers(monkeypatch, tier):
    as_tier(monkeypatch, f"u-{tier}", tier)
    r = client.post(
        "/api/v1/client/quota/claim",
        json={"url": "https://cdn.example.com/hls/ep01/index.txt", "streamCapture": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["allowed"] is True
    assert body["universalStream"] is True


def test_fetch_link_blocks_manifest_urls_for_non_top_tier(monkeypatch):
    monkeypatch.setattr("app.core.disk_guardrail.check_can_accept_job", lambda _jt: (True, ""))
    monkeypatch.setattr("app.api.routes._assert_safe_url", lambda _u: None)
    monkeypatch.setattr("app.api.routes._preflight_disk_check", lambda: None)
    as_tier(monkeypatch, "u-pro", "pro")
    r = client.post(
        "/api/v1/fetch-link",
        json={"url": "https://cdn.example.com/vod/ep01/master.m3u8"},
    )
    assert r.status_code == 403
    body = r.json()
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
    assert detail.get("error_code") == "tier_required_feature"
