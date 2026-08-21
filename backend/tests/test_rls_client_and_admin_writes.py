"""
Regression tests: server-side DB role, and admin writes that reported success
while changing nothing.
=============================================================================

All of these failed *silently* in production, which is why they need tests
rather than a code comment:

  A) The backend read the database through the anon key. It never attaches an
     end user's JWT to that client, so `auth.uid()` is NULL and `profiles`
     (policy: `USING (id = auth.uid()::TEXT)`) returned zero rows to every
     backend read and accepted zero rows on every write. The admin roster
     showed "0 tài khoản" over a table that held real accounts.

  B) The API-key auth path defaulted an unreadable profile to tier 'pro', so
     the RLS failure in (A) handed paid entitlements to anyone with a key.

  C) admin user-action 'revoke_api_keys' wrote a column name that does not
     exist (`active`; the column is `is_active`), so revoking a leaked key was
     rejected by PostgREST.

  D) 'set_tier' / '/update-user' returned success unconditionally. A write
     that matches no row is not an error to PostgREST — it returns an empty
     list — so a plan change that did nothing still read as done.

Run with:
    cd backend && python -m pytest tests/test_rls_client_and_admin_writes.py -q
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import MagicMock, patch

import pytest


# ═════════════════════════════════════════════════════════════════════
# A) The backend's client must use the service role when it is configured
# ═════════════════════════════════════════════════════════════════════

class TestBackendUsesServiceRole:

    def _reload_database(self, monkeypatch, anon: str, service: str):
        import app.core.database as db
        monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
        monkeypatch.setenv("SUPABASE_KEY", anon)
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", service)
        return importlib.reload(db)

    def test_uses_service_key_not_anon_key(self, monkeypatch):
        db = self._reload_database(monkeypatch, "anon-key", "service-key")
        with patch.object(db, "create_client") as mk:
            db.get_supabase_client()
        assert mk.call_args[0][1] == "service-key", (
            "backend queries must run as service_role; on the anon key every "
            "profiles read/write is silently dropped by RLS"
        )

    def test_falls_back_to_anon_when_service_key_absent(self, monkeypatch):
        db = self._reload_database(monkeypatch, "anon-key", "")
        with patch.object(db, "create_client") as mk:
            db.get_supabase_client()
        assert mk.call_args[0][1] == "anon-key"

    def test_anon_client_still_available_and_uses_anon_key(self, monkeypatch):
        db = self._reload_database(monkeypatch, "anon-key", "service-key")
        with patch.object(db, "create_client") as mk:
            db.get_anon_client()
        assert mk.call_args[0][1] == "anon-key"

    @pytest.fixture(autouse=True)
    def _restore_database_module(self):
        yield
        import app.core.database as db
        importlib.reload(db)


# ═════════════════════════════════════════════════════════════════════
# B) API-key auth must fail closed, not default to a paid tier
# ═════════════════════════════════════════════════════════════════════

class TestApiKeyTierFailsClosed:

    def _mock_sb_with_unreadable_profile(self):
        """Key row resolves; the profiles lookup raises (what RLS produced)."""
        sb = MagicMock()

        def _table(name):
            t = MagicMock()
            if name == "user_api_keys":
                t.select.return_value.eq.return_value.single.return_value.execute.return_value = (
                    MagicMock(data={"user_id": "u-1", "is_active": True})
                )
            elif name == "profiles":
                t.select.return_value.eq.return_value.single.return_value.execute.side_effect = (
                    Exception("PGRST116: no rows")
                )
            return t

        sb.table.side_effect = _table
        return sb

    def test_unreadable_profile_yields_free_not_pro(self, monkeypatch):
        import app.core.auth_middleware as am
        monkeypatch.setattr(am, "get_supabase_client",
                            lambda: self._mock_sb_with_unreadable_profile())
        user = am._lookup_api_key("some-legacy-key")
        assert user is not None, "the key itself is still valid"
        assert user["tier"] == "free", (
            "defaulting to 'pro' billed nothing and granted everything"
        )


# ═════════════════════════════════════════════════════════════════════
# C+D) Admin writes: right column, honest result
# ═════════════════════════════════════════════════════════════════════

def _admin_request(body: dict):
    """admin_user_action reads its payload off the Request itself."""
    req = MagicMock()
    req.headers = {}
    req.client.host = "127.0.0.1"

    async def _json():
        return body

    req.json = _json
    return req


class TestAdminUserActionWrites:

    def _run(self, sb, action, params=None):
        import app.api.admin as admin
        body = {"action": action, "user_id": "u-1", "params": params or {}}
        with patch.object(admin, "get_supabase_client", return_value=sb), \
             patch.object(admin, "log_admin_action"):
            return asyncio.new_event_loop().run_until_complete(
                admin.admin_user_action(_admin_request(body))
            )

    def test_revoke_api_keys_writes_is_active_column(self):
        sb = MagicMock()
        upd = sb.table.return_value.update
        upd.return_value.eq.return_value.execute.return_value = MagicMock(data=[{"id": "k1"}])

        result = self._run(sb, "revoke_api_keys")

        payload = upd.call_args[0][0]
        assert "is_active" in payload and payload["is_active"] is False
        assert "active" not in payload, "api_keys has no column named 'active'"
        assert result["success"] is True
        assert result["revoked"] == 1

    def test_set_tier_reports_failure_when_no_row_matched(self):
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[])          # PostgREST: matched nothing, not an error
        )

        result = self._run(sb, "set_tier", {"tier": "pro"})

        assert result["success"] is False, (
            "a tier change that touched no row must not report success"
        )

    def test_set_tier_succeeds_when_row_updated(self):
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = (
            MagicMock(data=[{"id": "u-1", "tier": "pro"}])
        )

        result = self._run(sb, "set_tier", {"tier": "pro"})

        assert result["success"] is True
        assert result["tier"] == "pro"


# ═════════════════════════════════════════════════════════════════════
# E) Paid-per-unit quotas must not hang off a client-supplied header
# ═════════════════════════════════════════════════════════════════════

class TestQuotaSubject:

    def _request(self, session_id: str, ip: str = "203.0.113.9"):
        req = MagicMock()
        req.headers = {"X-Session-ID": session_id, "X-Forwarded-For": ip}
        req.client.host = ip
        return req

    def test_anonymous_quota_keyed_to_ip_not_session_header(self):
        from app.api.transcript_translate import resolve_quota_subject
        loop = asyncio.new_event_loop()

        a = loop.run_until_complete(resolve_quota_subject(self._request("sess-aaa"), None))
        b = loop.run_until_complete(resolve_quota_subject(self._request("sess-bbb"), None))

        assert a == b, (
            "rotating X-Session-ID reset the daily cue quota, and every cue is "
            "a paid model call"
        )
        assert a.startswith("ip:")

    def test_signed_in_user_still_metered_per_account(self):
        from app.api.transcript_translate import resolve_quota_subject
        subject = asyncio.new_event_loop().run_until_complete(
            resolve_quota_subject(self._request("sess-aaa"), {"id": "user-42"})
        )
        assert subject == "user-42"


# ═════════════════════════════════════════════════════════════════════
# F) /history — was unauthenticated, unscoped, and globally destructive
# ═════════════════════════════════════════════════════════════════════

class TestHistoryScoping:
    """DELETE /api/v1/history/all took no auth and matched every row in
    download_jobs, so a single anonymous request wiped every account's history.
    GET /history had no owner filter, so any visitor read everyone's."""

    def _sb(self):
        sb = MagicMock()
        sb._q = sb.table.return_value.select.return_value.order.return_value
        sb._q.eq.return_value = sb._q
        sb._q.is_.return_value = sb._q
        sb._q.range.return_value = sb._q
        sb._q.execute.return_value = MagicMock(data=[])
        return sb

    def _run(self, coro_fn, sb, **kwargs):
        import app.api.routes as routes
        with patch.object(routes, "get_supabase_client", return_value=sb):
            return asyncio.new_event_loop().run_until_complete(coro_fn(**kwargs))

    def test_delete_all_requires_sign_in(self):
        import app.api.routes as routes
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            self._run(routes.delete_all_history, self._sb(), user=None)
        assert exc.value.status_code == 401

    def test_delete_all_is_scoped_to_the_caller(self):
        import app.api.routes as routes
        sb = MagicMock()
        delete_q = sb.table.return_value.delete.return_value
        delete_q.eq.return_value.execute.return_value = MagicMock(data=[{"id": "j1"}])

        result = self._run(routes.delete_all_history, sb, user={"id": "user-7"})

        assert delete_q.eq.call_args[0] == ("user_id", "user-7"), (
            "the delete must be filtered to the caller's own rows"
        )
        assert result["success"] is True

    def test_anonymous_history_read_excludes_owned_rows(self):
        import app.api.routes as routes
        sb = self._sb()
        self._run(routes.get_history, sb, limit=5, offset=0,
                  platform=None, status=None, user=None)
        assert sb._q.is_.call_args[0] == ("user_id", "null"), (
            "an anonymous caller must not be served signed-in users' history"
        )

    def test_signed_in_history_read_is_filtered_to_that_user(self):
        import app.api.routes as routes
        sb = self._sb()
        self._run(routes.get_history, sb, limit=5, offset=0,
                  platform=None, status=None, user={"id": "user-7"})
        assert ("user_id", "user-7") in [c[0] for c in sb._q.eq.call_args_list]
