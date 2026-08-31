"""
Admin Security & Audit Hardening Tests
=======================================
Verifies:
  A) Admin auth: no insecure default password, IP allowlist, lockout
  B) verify_admin dependency behavior
  C) Audit helper functions (log_admin_action, log_access_denied, helpers)
  D) Sensitive action logging wired into endpoints
  E) Proxy URL masking in audit metadata
  F) Regression: entitlements / other auth unaffected

Run with:
    cd backend && source venv/bin/activate
    python -m pytest tests/test_admin_security.py -q
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ── Keep module-level admin config from leaking between tests ────────────────
# Several tests below reload app.api.admin inside a patched environment, or set
# _ADMIN_ALLOWED_IPS / _ADMIN_PASSWORD directly. Whatever the last one leaves
# behind used to persist for the rest of the session: an allowlist of
# {"10.0.0.1"} made every later admin request 403, which silently changed the
# meaning of any test that ran afterwards (it is how the intelligence auth tests
# started failing only in a full-suite run). One test even captured its
# "original" value after it had already been overwritten, so its finally block
# restored the polluted value.
@pytest.fixture(autouse=True)
def _restore_admin_module_config():
    import app.api.admin as _admin
    saved_ips = set(_admin._ADMIN_ALLOWED_IPS)
    saved_pw = _admin._ADMIN_PASSWORD
    yield
    _admin._ADMIN_ALLOWED_IPS = saved_ips
    _admin._ADMIN_PASSWORD = saved_pw


# ── Stub heavy modules before any app import ─────────────────────────────────
for _mod in (
    "realtime", "realtime.types", "realtime.connection",
    "supabase", "supabase.client", "supabase._sync", "supabase._async",
    "postgrest", "gotrue", "storage3",
    "app.main",
    "slowapi",
    "app.core.database",
    "app.tasks.video_tasks",
):
    sys.modules.setdefault(_mod, MagicMock())

import app.main as _main_stub  # noqa: E402
_main_stub.limiter = MagicMock()
_main_stub.limiter.limit = lambda *a, **kw: (lambda f: f)


# ═══════════════════════════════════════════════════════════════════
# A — Admin password configuration
# ═══════════════════════════════════════════════════════════════════

class TestAdminPasswordConfig:

    def test_no_insecure_default_password(self):
        """_ADMIN_PASSWORD must not fall back to the old hardcoded 'matbaosupport'."""
        import importlib, os
        # Without ADMIN_PASSWORD set, the value should be empty (not a known default)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ADMIN_PASSWORD", None)
            import app.api.admin as admin_mod
            importlib.reload(admin_mod)
            assert admin_mod._ADMIN_PASSWORD != "matbaosupport", (
                "Default password 'matbaosupport' must not be used"
            )

    def test_admin_password_read_from_env(self):
        import importlib, os
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "s3cr3t-test-pw"}):
            import app.api.admin as admin_mod
            importlib.reload(admin_mod)
            assert admin_mod._ADMIN_PASSWORD == "s3cr3t-test-pw"

    def test_ip_allowlist_parsed_from_env(self):
        import importlib, os
        with patch.dict(os.environ, {"ADMIN_ALLOWED_IPS": "10.0.0.1,10.0.0.2"}):
            import app.api.admin as admin_mod
            importlib.reload(admin_mod)
            assert "10.0.0.1" in admin_mod._ADMIN_ALLOWED_IPS
            assert "10.0.0.2" in admin_mod._ADMIN_ALLOWED_IPS

    def test_empty_allowlist_is_permissive(self):
        import importlib, os
        with patch.dict(os.environ, {"ADMIN_ALLOWED_IPS": ""}):
            import app.api.admin as admin_mod
            importlib.reload(admin_mod)
            assert len(admin_mod._ADMIN_ALLOWED_IPS) == 0


# ═══════════════════════════════════════════════════════════════════
# B — verify_admin dependency
# ═══════════════════════════════════════════════════════════════════

def _make_request(ip: str = "127.0.0.1", headers: dict | None = None):
    """Build a minimal mock FastAPI Request."""
    _hmap = {"User-Agent": "pytest"} | (headers or {})
    req = MagicMock()
    req.client.host = ip
    req.headers = MagicMock()
    req.headers.get = lambda k, d=None: _hmap.get(k, d)
    req.headers.__getitem__ = lambda self, k: _hmap[k]
    req.headers.__contains__ = lambda self, k: k in _hmap
    return req


class TestVerifyAdmin:

    def _make_redis(self, session_valid: bool = False, locked: bool = False):
        r = MagicMock()
        r.get.return_value = b"1" if session_valid else None
        r.exists.return_value = 1 if locked else 0
        r.expire = MagicMock()
        r.ttl.return_value = 900
        # Real Redis INCR returns an int; the bare MagicMock default would
        # break the `attempts >= threshold` comparison in the lockout path.
        r.incr.return_value = 1
        return r

    @pytest.fixture(autouse=True)
    def patch_redis(self):
        """Patch the Redis client used inside admin.py."""
        with patch("app.api.admin._redis") as mock:
            mock.return_value = self._make_redis(session_valid=True)
            yield mock

    def test_valid_bearer_session_passes(self, patch_redis):
        from fastapi.security import HTTPAuthorizationCredentials
        from app.api.admin import verify_admin

        bearer = MagicMock(spec=HTTPAuthorizationCredentials)
        bearer.credentials = "valid-token-123"

        async def _run():
            result = await verify_admin(
                request=_make_request(), legacy_token=None, bearer=bearer
            )
            assert result is None  # passes without raising

        asyncio.run(_run())

    def test_no_credentials_raises_401(self, patch_redis):
        patch_redis.return_value = self._make_redis(session_valid=False)
        from fastapi import HTTPException
        from app.api.admin import verify_admin

        async def _run():
            with pytest.raises(HTTPException) as exc_info:
                await verify_admin(request=_make_request(), legacy_token=None, bearer=None)
            assert exc_info.value.status_code == 401

        asyncio.run(_run())

    def test_ip_not_in_allowlist_raises_403(self, patch_redis):
        import importlib, os
        with patch.dict(os.environ, {"ADMIN_ALLOWED_IPS": "10.0.0.1"}):
            import app.api.admin as admin_mod
            importlib.reload(admin_mod)
            # Patch _ADMIN_ALLOWED_IPS directly to avoid full reload side effects
            admin_mod._ADMIN_ALLOWED_IPS = {"10.0.0.1"}

        from fastapi import HTTPException
        from app.api.admin import verify_admin
        import app.api.admin as admin_mod2
        original = admin_mod2._ADMIN_ALLOWED_IPS
        admin_mod2._ADMIN_ALLOWED_IPS = {"10.0.0.1"}

        async def _run():
            with pytest.raises(HTTPException) as exc_info:
                await verify_admin(
                    request=_make_request(ip="1.2.3.4"),
                    legacy_token=None, bearer=None,
                )
            assert exc_info.value.status_code == 403

        try:
            asyncio.run(_run())
        finally:
            admin_mod2._ADMIN_ALLOWED_IPS = original

    def test_correct_legacy_token_passes(self, patch_redis):
        patch_redis.return_value = self._make_redis(session_valid=False)
        import app.api.admin as admin_mod
        original_pw = admin_mod._ADMIN_PASSWORD
        original_ips = admin_mod._ADMIN_ALLOWED_IPS
        admin_mod._ADMIN_PASSWORD = "correct-pw"
        admin_mod._ADMIN_ALLOWED_IPS = set()  # no allowlist restriction

        from app.api.admin import verify_admin

        async def _run():
            result = await verify_admin(
                request=_make_request(), legacy_token="correct-pw", bearer=None
            )
            assert result is None

        try:
            asyncio.run(_run())
        finally:
            admin_mod._ADMIN_PASSWORD = original_pw
            admin_mod._ADMIN_ALLOWED_IPS = original_ips

    def test_wrong_legacy_token_raises_401(self, patch_redis):
        patch_redis.return_value = self._make_redis(session_valid=False)
        import app.api.admin as admin_mod
        original_pw = admin_mod._ADMIN_PASSWORD
        original_ips = admin_mod._ADMIN_ALLOWED_IPS
        admin_mod._ADMIN_PASSWORD = "correct-pw"
        admin_mod._ADMIN_ALLOWED_IPS = set()  # no allowlist restriction

        from fastapi import HTTPException
        from app.api.admin import verify_admin

        async def _run():
            with pytest.raises(HTTPException) as exc_info:
                await verify_admin(
                    request=_make_request(), legacy_token="wrong-pw", bearer=None
                )
            assert exc_info.value.status_code == 401

        try:
            asyncio.run(_run())
        finally:
            admin_mod._ADMIN_PASSWORD = original_pw
            admin_mod._ADMIN_ALLOWED_IPS = original_ips


# ═══════════════════════════════════════════════════════════════════
# C — Audit helper unit tests
# ═══════════════════════════════════════════════════════════════════

class TestAuditHelpers:

    def test_log_admin_action_calls_log_event(self):
        from app.core import audit

        with patch.object(audit, "log_event") as mock_log:
            req = _make_request(ip="1.2.3.4")
            audit.log_admin_action(
                req, "admin.user.tier_changed",
                resource_type="user", resource_id="u123",
                metadata={"new_tier": "pro"},
            )
            mock_log.assert_called_once()
            args, kwargs = mock_log.call_args
            assert args[0] == "admin.user.tier_changed"
            assert kwargs["actor_email"] == "admin"
            assert kwargs["resource_type"] == "user"
            assert kwargs["resource_id"] == "u123"
            assert kwargs["metadata"]["new_tier"] == "pro"

    def test_log_access_denied_calls_log_event(self):
        from app.core import audit

        with patch.object(audit, "log_event") as mock_log:
            req = _make_request()
            audit.log_access_denied(req, "/admin/login", reason="wrong_password")
            mock_log.assert_called_once()
            args, kwargs = mock_log.call_args
            assert args[0] == "access.denied"
            assert kwargs["resource_id"] == "/admin/login"
            assert kwargs["metadata"]["reason"] == "wrong_password"

    def test_log_admin_action_with_none_request(self):
        """Should not raise when request is None."""
        from app.core import audit
        with patch.object(audit, "log_event") as mock_log:
            audit.log_admin_action(None, "admin.test", metadata={"k": "v"})
            mock_log.assert_called_once()

    @staticmethod
    def _req(xff=None, client_host=None):
        """Request stub with case-insensitive headers, like Starlette's."""
        req = MagicMock()
        headers = {"x-forwarded-for": xff} if xff is not None else {}
        req.headers.get = lambda k, d=None: headers.get(k.lower(), d)
        if client_host is None:
            req.client = None
        else:
            req.client.host = client_host
        return req

    def test_extract_ip_takes_last_forwarded_entry_not_first(self):
        """
        X-Forwarded-For is append-only: our own reverse proxy adds the peer it
        actually saw as the LAST entry, so that is the only trustworthy one.
        Everything before it was supplied by the caller.

        This test used to assert the FIRST entry — the behaviour commit 336f67e
        removed precisely because it let anyone spoof their way past IP quotas,
        rate limits and the /admin allowlist. If it ever fails again, fix the
        test's expectation, not client_ip.get_client_ip.
        """
        from app.core.audit import _extract_ip
        assert _extract_ip(self._req(xff="203.0.113.1, 10.0.0.1")) == "10.0.0.1"

    def test_extract_ip_ignores_client_supplied_forwarded_prefix(self):
        """A caller injecting their own XFF cannot displace the proxy's entry."""
        from app.core.audit import _extract_ip
        spoofed = "1.2.3.4, 9.9.9.9, 203.0.113.77"
        assert _extract_ip(self._req(xff=spoofed, client_host="10.0.0.9")) == "203.0.113.77"

    def test_extract_ip_falls_back_to_client_host(self):
        from app.core.audit import _extract_ip
        assert _extract_ip(self._req(xff="", client_host="192.168.1.1")) == "192.168.1.1"

    def test_extract_ip_defaults_when_nothing_available(self):
        from app.core.audit import _extract_ip
        assert _extract_ip(self._req()) == "127.0.0.1"

    def test_extract_ip_returns_none_for_none_request(self):
        from app.core.audit import _extract_ip
        assert _extract_ip(None) is None

    def test_get_recent_admin_actions_returns_list_on_db_error(self):
        from app.core.audit import get_recent_admin_actions
        # get_service_client is imported inside the function from app.core.database
        with patch("app.core.database.get_service_client", side_effect=Exception("DB down")):
            result = get_recent_admin_actions()
        assert result == []

    def test_get_recent_access_denials_returns_list_on_db_error(self):
        from app.core.audit import get_recent_access_denials
        with patch("app.core.database.get_service_client", side_effect=Exception("DB down")):
            result = get_recent_access_denials()
        assert result == []


# ═══════════════════════════════════════════════════════════════════
# D — Verify audit calls are wired into key endpoints
# ═══════════════════════════════════════════════════════════════════

class TestAuditWiring:
    """
    Verify that sensitive admin endpoint functions call log_admin_action.
    We patch log_admin_action and check it gets invoked.
    """

    def _mock_supabase(self):
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"id": "u1"}]
        sb.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        return sb

    def test_update_user_logs_tier_change(self):
        from app.api.admin import update_user, UpdateUserRequest

        req = UpdateUserRequest(user_id="u1", plan="pro")
        request = _make_request()

        with patch("app.api.admin.get_supabase_client", return_value=self._mock_supabase()):
            with patch("app.api.admin.log_admin_action") as mock_log:
                asyncio.run(update_user(req, request, _=None))

        mock_log.assert_called_once()
        action = mock_log.call_args[0][1]
        assert action == "admin.user.tier_changed"
        assert mock_log.call_args[1]["metadata"]["new_tier"] == "pro"

    def test_reset_quota_logs_action(self):
        from app.api.admin import reset_user_quota, ResetQuotaRequest

        req = ResetQuotaRequest(user_id="u2")
        request = _make_request()

        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("app.api.admin.get_supabase_client", return_value=sb):
            with patch("app.api.admin.log_admin_action") as mock_log:
                asyncio.run(reset_user_quota(req, request, _=None))

        mock_log.assert_called_once()
        action = mock_log.call_args[0][1]
        assert action == "admin.user.quota_reset"

    def test_youtube_toggle_logs_action(self):
        from app.api.admin import youtube_toggle, YouTubeToggleRequest

        req = YouTubeToggleRequest(enabled=False)
        request = _make_request()

        with patch("app.core.youtube_gate.set_youtube_enabled"):
            with patch("app.core.youtube_gate.dashboard_snapshot", return_value={}):
                with patch("app.api.admin.log_admin_action") as mock_log:
                    asyncio.run(youtube_toggle(req, request, _=None))

        mock_log.assert_called_once()
        action = mock_log.call_args[0][1]
        assert action == "admin.youtube.toggle"
        assert mock_log.call_args[1]["metadata"]["enabled"] is False

    def test_cookie_remove_logs_action(self):
        from app.api.admin import cookie_pool_remove, CookieRemoveRequest

        req = CookieRemoveRequest(platform="youtube", index=0)
        request = _make_request()

        with patch("app.core.cookie_pool.remove_cookie", return_value=2):
            with patch("app.api.admin.log_admin_action") as mock_log:
                asyncio.run(cookie_pool_remove(req, request, _=None))

        mock_log.assert_called_once()
        action = mock_log.call_args[0][1]
        assert action == "admin.cookie.removed"

    def test_proxy_add_masks_credentials_in_audit(self):
        from app.api.admin import proxy_pool_add, ProxyAddRequest

        req = ProxyAddRequest(platform="youtube", proxy_url="http://user123:pass456@proxy.host:8080")
        request = _make_request()

        with patch("app.core.proxy_pool.add_proxy", return_value=1):
            with patch("app.api.admin.log_admin_action") as mock_log:
                asyncio.run(proxy_pool_add(req, request, _=None))

        mock_log.assert_called_once()
        metadata = mock_log.call_args[1]["metadata"]
        proxy_masked = metadata.get("proxy_masked", "")
        # Credentials must be masked, host must be visible
        assert "user123" not in proxy_masked
        assert "pass456" not in proxy_masked
        assert "proxy.host" in proxy_masked

    def test_scraperapi_key_add_masks_key_in_audit(self):
        from app.api.admin import scraperapi_add_key, ScraperAPIKeyRequest

        req = ScraperAPIKeyRequest(key="abcdefghijklmnop")
        request = _make_request()

        with patch("app.core.scraperapi_pool.fetch_credits", return_value=1000):
            with patch("app.core.scraperapi_pool.add_key", return_value=2):
                with patch("app.api.admin.log_admin_action") as mock_log:
                    asyncio.run(scraperapi_add_key(req, request, _=None))

        mock_log.assert_called_once()
        metadata = mock_log.call_args[1]["metadata"]
        key_prefix = metadata.get("key_prefix", "")
        assert key_prefix.startswith("abcdefg")
        assert "ijklmnop" not in key_prefix


# ═══════════════════════════════════════════════════════════════════
# E — Security visibility endpoints exist
# ═══════════════════════════════════════════════════════════════════

class TestSecurityVisibilityEndpoints:

    def test_audit_log_endpoint_exists(self):
        from app.api.admin import router
        from fastapi.routing import APIRoute
        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/audit-log" in paths, "GET /audit-log endpoint must exist"

    def test_security_events_endpoint_exists(self):
        from app.api.admin import router
        from fastapi.routing import APIRoute
        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/security-events" in paths, "GET /security-events endpoint must exist"

    def test_audit_log_returns_entries(self):
        from app.api.admin import get_audit_log
        from app.core import audit

        fake_entries = [{"action": "admin.user.tier_changed", "created_at": "2026-01-01"}]
        with patch.object(audit, "get_recent_admin_actions", return_value=fake_entries):
            result = asyncio.run(get_audit_log(limit=10, _=None))
        assert result["success"] is True
        assert result["count"] == 1
        assert result["entries"] == fake_entries

    def test_security_events_returns_denied_list(self):
        from app.api.admin import get_security_events
        from app.core import audit

        fake_denials = [{"action": "access.denied", "metadata": {"reason": "wrong_password"}}]
        fake_redis = MagicMock()
        fake_redis.keys.return_value = []

        with patch.object(audit, "get_recent_access_denials", return_value=fake_denials):
            with patch("app.api.admin._redis", return_value=fake_redis):
                result = asyncio.run(get_security_events(limit=50, _=None))
        assert result["success"] is True
        assert result["denied_count"] == 1
        assert len(result["entries"]) == 1


# ═══════════════════════════════════════════════════════════════════
# F — Regression: other auth + entitlements unaffected
# ═══════════════════════════════════════════════════════════════════

class TestRegressionNotBroken:

    def test_entitlements_check_feature_unaffected(self):
        from app.core.entitlements import check_feature
        assert check_feature("free", "logo_inpaint") is False
        assert check_feature("pro", "logo_inpaint") is True

    def test_flow_cleanup_require_pro_still_present(self):
        import inspect
        from app.api.flow_cleanup import _require_pro, upload_flow_video
        from fastapi import params as fp
        sig = inspect.signature(upload_flow_video)
        has_dep = any(
            isinstance(p.default, fp.Depends) and p.default.dependency is _require_pro
            for p in sig.parameters.values()
        )
        assert has_dep, "upload_flow_video must still have _require_pro Depends"

    def test_admin_router_still_has_stats_endpoint(self):
        from app.api.admin import router
        from fastapi.routing import APIRoute
        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/stats" in paths

    def test_admin_router_still_has_update_user_endpoint(self):
        from app.api.admin import router
        from fastapi.routing import APIRoute
        paths = [r.path for r in router.routes if isinstance(r, APIRoute)]
        assert "/update-user" in paths


# ═══════════════════════════════════════════════════════════════════
# X — X-Admin-Token header hardening (regression)
# ═══════════════════════════════════════════════════════════════════

class TestLegacyHeaderHardening:
    """X-Admin-Token carries EITHER a session token or the raw password, and
    used to be compared only against the password with no lockout at all."""

    def _redis(self, session_valid: bool = False, locked: bool = False, attempts: int = 1):
        r = MagicMock()
        r.get.return_value = b"1" if session_valid else None
        r.exists.return_value = 1 if locked else 0
        r.ttl.return_value = 900
        r.incr.return_value = attempts
        return r

    def _pin(self, admin_mod, pw="correct-pw"):
        admin_mod._ADMIN_PASSWORD = pw
        admin_mod._ADMIN_ALLOWED_IPS = set()

    def test_session_token_in_legacy_header_is_accepted(self):
        """Panels hold the /admin/login token in a var named `adminToken` and
        send it in this header; the same value goes out as a Bearer token
        elsewhere. Comparing it only to the password 401'd every such call."""
        import app.api.admin as admin_mod
        from app.api.admin import verify_admin

        pw, ips = admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS
        try:
            self._pin(admin_mod)
            with patch("app.api.admin._redis", return_value=self._redis(session_valid=True)):
                async def _run():
                    assert await verify_admin(
                        _make_request(), legacy_token="live-session-token", bearer=None
                    ) is None
                asyncio.run(_run())
        finally:
            admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS = pw, ips

    def test_wrong_legacy_token_is_counted(self):
        """A guess through this header must count toward the same lockout as
        /admin/login, or the lockout is bypassed by switching headers."""
        import app.api.admin as admin_mod
        from fastapi import HTTPException
        from app.api.admin import verify_admin

        pw, ips = admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS
        r = self._redis(attempts=1)
        try:
            self._pin(admin_mod)
            with patch("app.api.admin._redis", return_value=r):
                async def _run():
                    with pytest.raises(HTTPException) as exc:
                        await verify_admin(_make_request(), legacy_token="wrong", bearer=None)
                    assert exc.value.status_code == 401
                asyncio.run(_run())
            assert r.incr.called, "failed guess was not counted"
        finally:
            admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS = pw, ips

    def test_legacy_token_arms_lockout_at_threshold(self):
        import app.api.admin as admin_mod
        from fastapi import HTTPException
        from app.api.admin import verify_admin

        pw, ips = admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS
        r = self._redis(attempts=admin_mod._MAX_ATTEMPTS)
        try:
            self._pin(admin_mod)
            with patch("app.api.admin._redis", return_value=r):
                async def _run():
                    with pytest.raises(HTTPException):
                        await verify_admin(_make_request(), legacy_token="wrong", bearer=None)
                asyncio.run(_run())
            assert any("admin:lockout:" in str(c) for c in r.setex.call_args_list), \
                "lockout never armed after reaching the attempt threshold"
        finally:
            admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS = pw, ips

    def test_locked_out_ip_gets_429_on_legacy_header(self):
        import app.api.admin as admin_mod
        from fastapi import HTTPException
        from app.api.admin import verify_admin

        pw, ips = admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS
        try:
            self._pin(admin_mod)
            with patch("app.api.admin._redis", return_value=self._redis(locked=True)):
                async def _run():
                    with pytest.raises(HTTPException) as exc:
                        await verify_admin(_make_request(), legacy_token="wrong", bearer=None)
                    assert exc.value.status_code == 429
                asyncio.run(_run())
        finally:
            admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS = pw, ips

    def test_redis_outage_does_not_lock_admins_out(self):
        """Lockout state is unreadable during an outage. The rate limit degrades
        open, the password check stays authoritative, and nothing 500s."""
        import app.api.admin as admin_mod
        from app.api.admin import verify_admin

        pw, ips = admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS
        r = MagicMock()
        r.get.side_effect = ConnectionError("redis down")
        r.exists.side_effect = ConnectionError("redis down")
        r.incr.side_effect = ConnectionError("redis down")
        try:
            self._pin(admin_mod)
            with patch("app.api.admin._redis", return_value=r):
                async def _run():
                    assert await verify_admin(
                        _make_request(), legacy_token="correct-pw", bearer=None
                    ) is None
                asyncio.run(_run())
        finally:
            admin_mod._ADMIN_PASSWORD, admin_mod._ADMIN_ALLOWED_IPS = pw, ips


class TestPasswordComparison:

    def test_uses_constant_time_compare(self):
        """`==` leaks the correct prefix length through response timing."""
        import inspect
        import app.api.admin as admin_mod
        assert "compare_digest" in inspect.getsource(admin_mod._password_matches)

    def test_empty_password_never_matches(self):
        import app.api.admin as admin_mod
        original = admin_mod._ADMIN_PASSWORD
        try:
            admin_mod._ADMIN_PASSWORD = ""
            assert admin_mod._password_matches("") is False
            assert admin_mod._password_matches("anything") is False
        finally:
            admin_mod._ADMIN_PASSWORD = original

    def test_correct_password_matches(self):
        import app.api.admin as admin_mod
        original = admin_mod._ADMIN_PASSWORD
        try:
            admin_mod._ADMIN_PASSWORD = "s3cr3t"
            assert admin_mod._password_matches("s3cr3t") is True
            assert admin_mod._password_matches("s3cr3") is False
        finally:
            admin_mod._ADMIN_PASSWORD = original
