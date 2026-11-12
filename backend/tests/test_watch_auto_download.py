"""
Phase 33F — Channel Watch `auto_download` mode tests (TDD).
"""
from __future__ import annotations

import fakeredis
import pytest

from app.core import admin_switches, watch_config as cfg
from app.services import channel_watch as cw
from tests.test_channel_watch import CH, T0, World, api, env, login, post, vids, w  # noqa: F401


def test_auto_download_config_defaults_and_env_override(monkeypatch):
    monkeypatch.setattr(admin_switches, "_r", lambda: fakeredis.FakeRedis(decode_responses=True))
    admin_switches.reset_cache()
    monkeypatch.delenv("WATCH_AUTO_DOWNLOAD_TIERS", raising=False)

    assert cfg.auto_download_allowed("guest") is False
    assert cfg.auto_download_allowed("free") is False
    assert cfg.auto_download_allowed("starter") is True
    assert cfg.auto_download_allowed("pro") is True
    assert cfg.auto_download_allowed("team") is True
    assert cfg.auto_download_allowed("enterprise") is True
    assert cfg.auto_download_allowed("free", is_admin=True) is True

    monkeypatch.setenv("WATCH_AUTO_DOWNLOAD_TIERS", "pro, team")
    assert cfg.auto_download_allowed("starter") is False
    assert cfg.auto_download_allowed("pro") is True
    assert cfg.auto_download_allowed("team") is True
    assert cfg.auto_download_allowed("starter", is_admin=True) is True


def test_watch_status_includes_auto_download_allowed(app, w, api, env):  # noqa: F811
    login(api, uid="u_free", tier="free")
    r_free = app.get("/api/v1/watch/status")
    assert r_free.status_code == 200
    assert r_free.json()["auto_download_allowed"] is False
    assert r_free.json()["bulk_zip_allowed"] is False

    login(api, uid="u_starter", tier="starter")
    r_starter = app.get("/api/v1/watch/status")
    assert r_starter.status_code == 200
    assert r_starter.json()["auto_download_allowed"] is True
    assert r_starter.json()["bulk_zip_allowed"] is True

    login(api, uid="u_admin", tier="free", admin=True)
    r_admin = app.get("/api/v1/watch/status")
    assert r_admin.status_code == 200
    assert r_admin.json()["auto_download_allowed"] is True
    assert r_admin.json()["bulk_zip_allowed"] is True


def test_patch_and_add_auto_download_tier_gate(app, w, api, env):  # noqa: F811
    w.listings[CH] = vids(1, 2)

    # Free user (given 1 slot in test fixture) cannot add or patch to auto_download -> 402
    login(api, uid="u_free", tier="free")
    r_add_denied = post(app, CH, mode="auto_download")
    assert r_add_denied.status_code == 402
    body = r_add_denied.json()
    assert body["error_code"] == "auto_download_tier_required"
    assert "Tự động tải" in body["message"] or "gói" in body["message"]

    # Add with one_tap succeeds, then PATCH to auto_download returns 402
    r_ok = post(app, CH, mode="one_tap")
    assert r_ok.status_code == 201
    sub_id = r_ok.json()["subscription"]["id"]

    r_patch_denied = app.patch(f"/api/v1/watch/subscriptions/{sub_id}", json={"mode": "auto_download"})
    assert r_patch_denied.status_code == 402
    assert r_patch_denied.json()["error_code"] == "auto_download_tier_required"

    # Starter user CAN patch to auto_download and add directly with auto_download
    login(api, uid="u_free", tier="starter")
    r_patch_ok = app.patch(f"/api/v1/watch/subscriptions/{sub_id}", json={"mode": "auto_download"})
    assert r_patch_ok.status_code == 200
    assert r_patch_ok.json()["subscription"]["mode"] == "auto_download"

    # Admin with free profile tier is always allowed
    login(api, uid="u_admin", tier="free", admin=True)
    r_admin_add = post(app, CH, mode="auto_download")
    assert r_admin_add.status_code == 201
    assert r_admin_add.json()["subscription"]["mode"] == "auto_download"


def test_expired_tier_auto_downgrades_to_one_tap_on_items_and_subscriptions(app, w, api, env):  # noqa: F811
    w.listings[CH] = vids(1, 2)
    login(api, uid="u_paid", tier="starter")
    r = post(app, CH, mode="auto_download")
    assert r.status_code == 201
    sub_id = r.json()["subscription"]["id"]
    assert r.json()["subscription"]["mode"] == "auto_download"

    # Simulate subscription expiry -> user is now 'free'
    login(api, uid="u_paid", tier="free")

    # Hitting /watch/items downgrades auto_download -> one_tap in DB
    r_items = app.get("/api/v1/watch/items")
    assert r_items.status_code == 200

    r_subs = app.get("/api/v1/watch/subscriptions")
    assert r_subs.status_code == 200
    subs = r_subs.json()["subscriptions"]
    assert len(subs) == 1
    assert subs[0]["id"] == sub_id
    assert subs[0]["mode"] == "one_tap"
