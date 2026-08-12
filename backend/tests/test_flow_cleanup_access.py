"""
Flow-Cleanup Access Control Tests
==================================
Verify that logo-inpaint endpoints are gated behind Pro tier.

Run with:
    cd backend && source venv/bin/activate
    python -m pytest tests/test_flow_cleanup_access.py -q
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Stub heavy imports before any app module is touched ──────────────────────
for _mod in (
    "realtime", "realtime.types", "realtime.connection",
    "supabase", "supabase.client", "supabase._sync", "supabase._async",
    "postgrest", "gotrue", "storage3",
    "app.main",
    "slowapi",
    "app.core.database",
):
    sys.modules.setdefault(_mod, MagicMock())

# Stub limiter so @limiter.limit returns an identity decorator
import app.main as _main_stub  # noqa: E402
_main_stub.limiter = MagicMock()
_main_stub.limiter.limit = lambda *a, **kw: (lambda f: f)


# ═══════════════════════════════════════════════════════════════════
# A — Entitlements / feature-check unit tests
# ═══════════════════════════════════════════════════════════════════

class TestEntitlementsLogic:

    def test_free_tier_logo_inpaint_is_false(self):
        from app.core.entitlements import check_feature
        assert check_feature("free", "logo_inpaint") is False

    def test_pro_tier_logo_inpaint_is_true(self):
        from app.core.entitlements import check_feature
        assert check_feature("pro", "logo_inpaint") is True

    def test_team_tier_logo_inpaint_is_true(self):
        from app.core.entitlements import check_feature
        assert check_feature("team", "logo_inpaint") is True

    def test_enterprise_tier_logo_inpaint_is_true(self):
        from app.core.entitlements import check_feature
        assert check_feature("enterprise", "logo_inpaint") is True

    def test_api_tier_logo_inpaint_is_true(self):
        from app.core.entitlements import check_feature
        assert check_feature("api", "logo_inpaint") is True

    def test_none_tier_defaults_to_free(self):
        from app.core.entitlements import check_feature
        assert check_feature(None, "logo_inpaint") is False

    def test_unknown_tier_defaults_to_free(self):
        from app.core.entitlements import check_feature
        assert check_feature("starter", "logo_inpaint") is False


# ═══════════════════════════════════════════════════════════════════
# B — _require_pro dependency unit tests
# ═══════════════════════════════════════════════════════════════════

class TestRequireProDependency:

    @pytest.fixture()
    def fake_request(self):
        return MagicMock()

    def test_guest_raises_401(self, fake_request):
        from fastapi import HTTPException
        from app.api.flow_cleanup import _require_pro

        async def _run():
            with pytest.raises(HTTPException) as exc_info:
                await _require_pro(fake_request, user=None)
            assert exc_info.value.status_code == 401

        asyncio.run(_run())

    def test_api_key_user_free_tier_raises_402(self, fake_request):
        from fastapi import HTTPException
        from app.api.flow_cleanup import _require_pro

        free_user = {"id": "u1", "tier": "free", "via_api_key": True}

        async def _run():
            with pytest.raises(HTTPException) as exc_info:
                await _require_pro(fake_request, user=free_user)
            assert exc_info.value.status_code == 402
            detail = exc_info.value.detail
            assert detail["error_code"] == "tier_required_feature"
            assert detail["feature"] == "logo_inpaint"
            assert detail["required_plan"] == "pro"

        asyncio.run(_run())

    def test_api_key_user_pro_tier_passes(self, fake_request):
        from app.api.flow_cleanup import _require_pro

        pro_user = {"id": "u2", "tier": "pro", "via_api_key": True}

        async def _run():
            result = await _require_pro(fake_request, user=pro_user)
            assert result is None

        asyncio.run(_run())

    def test_api_key_user_team_tier_passes(self, fake_request):
        from app.api.flow_cleanup import _require_pro

        team_user = {"id": "u3", "tier": "team", "via_api_key": True}

        async def _run():
            result = await _require_pro(fake_request, user=team_user)
            assert result is None

        asyncio.run(_run())

    def test_api_key_user_enterprise_tier_passes(self, fake_request):
        from app.api.flow_cleanup import _require_pro

        ent_user = {"id": "u4", "tier": "enterprise", "via_api_key": True}

        async def _run():
            result = await _require_pro(fake_request, user=ent_user)
            assert result is None

        asyncio.run(_run())

    def test_api_key_user_api_tier_passes(self, fake_request):
        from app.api.flow_cleanup import _require_pro

        api_user = {"id": "u5", "tier": "api", "via_api_key": True}

        async def _run():
            result = await _require_pro(fake_request, user=api_user)
            assert result is None

        asyncio.run(_run())

    def test_jwt_user_no_tier_does_db_lookup(self, fake_request):
        """JWT users arrive without tier in dict — dependency must look it up."""
        from fastapi import HTTPException
        from app.api.flow_cleanup import _require_pro

        jwt_free_user = {"id": "jwt-user-id", "email": "x@test.com", "token": "tok"}
        fake_ent = {"tier": "free", "features": {"logo_inpaint": False}}

        async def _run():
            with patch(
                "app.api.flow_cleanup.get_entitlement",
                new=AsyncMock(return_value=fake_ent),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await _require_pro(fake_request, user=jwt_free_user)
            assert exc_info.value.status_code == 402

        asyncio.run(_run())

    def test_jwt_user_pro_tier_from_db_passes(self, fake_request):
        from app.api.flow_cleanup import _require_pro

        jwt_pro_user = {"id": "jwt-pro-id", "email": "pro@test.com", "token": "tok"}
        fake_ent = {"tier": "pro", "features": {"logo_inpaint": True}}

        async def _run():
            with patch(
                "app.api.flow_cleanup.get_entitlement",
                new=AsyncMock(return_value=fake_ent),
            ):
                result = await _require_pro(fake_request, user=jwt_pro_user)
            assert result is None

        asyncio.run(_run())

    def test_db_lookup_failure_defaults_to_free(self, fake_request):
        """If DB lookup throws, tier defaults to 'free' → 402."""
        from fastapi import HTTPException
        from app.api.flow_cleanup import _require_pro

        jwt_user = {"id": "u-broken", "email": "err@test.com", "token": "tok"}

        async def _run():
            with patch(
                "app.api.flow_cleanup.get_entitlement",
                new=AsyncMock(side_effect=Exception("DB down")),
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await _require_pro(fake_request, user=jwt_user)
            assert exc_info.value.status_code == 402

        asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════════
# C — Endpoint function-signature wiring verification
# (Depends in function params → checked via inspect.signature)
# ═══════════════════════════════════════════════════════════════════

def _has_dep(fn, dep_callable) -> bool:
    """Return True if the function has a parameter whose Depends.dependency is dep_callable."""
    from fastapi import params as fastapi_params
    sig = inspect.signature(fn)
    for param in sig.parameters.values():
        dflt = param.default
        if isinstance(dflt, fastapi_params.Depends) and dflt.dependency is dep_callable:
            return True
    return False


class TestEndpointGatingWired:

    def test_upload_has_require_pro_dep(self):
        from app.api.flow_cleanup import upload_flow_video, _require_pro
        assert _has_dep(upload_flow_video, _require_pro), \
            "upload_flow_video must have Depends(_require_pro) in its signature"

    def test_preview_frame_has_require_pro_dep(self):
        from app.api.flow_cleanup import preview_frame_cleanup, _require_pro
        assert _has_dep(preview_frame_cleanup, _require_pro), \
            "preview_frame_cleanup must have Depends(_require_pro) in its signature"

    def test_process_has_require_pro_dep(self):
        from app.api.flow_cleanup import process_flow_cleanup, _require_pro
        assert _has_dep(process_flow_cleanup, _require_pro), \
            "process_flow_cleanup must have Depends(_require_pro) in its signature"

    def test_from_local_has_require_pro_dep(self):
        from app.api.flow_cleanup import cleanup_from_local_path, _require_pro
        assert _has_dep(cleanup_from_local_path, _require_pro), \
            "cleanup_from_local_path must have Depends(_require_pro) in its signature"

    def test_get_frame_no_pro_dep(self):
        """Read-only frame serve endpoint must NOT require Pro."""
        from app.api.flow_cleanup import get_preview_frame, _require_pro
        assert not _has_dep(get_preview_frame, _require_pro), \
            "get_preview_frame must NOT have Depends(_require_pro)"

    def test_no_logo_no_pro_dep(self):
        """Record-only endpoint must NOT require Pro."""
        from app.api.flow_cleanup import record_no_logo, _require_pro
        assert not _has_dep(record_no_logo, _require_pro), \
            "record_no_logo must NOT have Depends(_require_pro)"


# ═══════════════════════════════════════════════════════════════════
# D — Regression: entitlements module intact for other features
# ═══════════════════════════════════════════════════════════════════

class TestEntitlementsNotBroken:

    def test_bulk_zip_still_gated_pro(self):
        from app.core.entitlements import check_feature
        assert check_feature("free", "bulk_zip") is False
        assert check_feature("pro", "bulk_zip") is True

    def test_youtube_video_still_gated_pro(self):
        from app.core.entitlements import check_feature
        assert check_feature("free", "youtube_video") is False
        assert check_feature("pro", "youtube_video") is True

    def test_free_limits_unaffected(self):
        from app.core.entitlements import PLAN_DEFS
        assert "downloads_per_day" in PLAN_DEFS["free"]["limits"]
        assert "features" in PLAN_DEFS["free"]

    def test_plan_defs_logo_inpaint_unchanged(self):
        from app.core.entitlements import PLAN_DEFS
        assert PLAN_DEFS["free"]["features"]["logo_inpaint"] is False
        assert PLAN_DEFS["pro"]["features"]["logo_inpaint"] is True
        assert PLAN_DEFS["team"]["features"]["logo_inpaint"] is True
        assert PLAN_DEFS["enterprise"]["features"]["logo_inpaint"] is True
        assert PLAN_DEFS["api"]["features"]["logo_inpaint"] is True
