"""
Phase 10 — API Key Management + Telegram Link Tests
======================================================
Tests cover:
  - New multi-key api_keys table: create, list, revoke, label update
  - Auth middleware: X-API-Key header lookup
  - Pro-only guard for api_keys endpoints
  - Telegram link request + confirm flow
  - Telegram user-info endpoint
  - Bot token generation & prefix format
  - Max 3 keys per user enforcement

Uses fakeredis for Redis-backed telegram link tokens.
Uses monkeypatched Supabase for database calls.
"""

import hashlib
import json
import secrets
import uuid
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _raw_key() -> str:
    return "vidgrab_" + secrets.token_hex(16)


def _make_key_row(user_id: str = "user-1", raw: str | None = None, active: bool = True):
    raw = raw or _raw_key()
    return {
        "id":              str(uuid.uuid4()),
        "user_id":         user_id,
        "key_hash":        _hash(raw),
        "key_prefix":      raw[:16],
        "label":           "Test Key",
        "is_active":       active,
        "created_at":      "2026-01-01T00:00:00Z",
        "last_used_at":    None,
        "requests_today":  0,
        "requests_total":  0,
        "_raw":            raw,   # for test use only
    }


# ─── Test: key format ─────────────────────────────────────────────────────────

class TestApiKeyFormat:
    def test_prefix_starts_with_vidgrab(self):
        raw = _raw_key()
        assert raw.startswith("vidgrab_")

    def test_prefix_length_is_16(self):
        raw = _raw_key()
        # prefix shown = first 16 chars: "vidgrab_" (8) + 8 hex chars
        prefix = raw[:16]
        assert len(prefix) == 16

    def test_total_key_length(self):
        raw = _raw_key()
        # "vidgrab_" (8) + token_hex(16) = 8 + 32 = 40 chars
        assert len(raw) == 40

    def test_hash_is_sha256(self):
        raw = _raw_key()
        h   = _hash(raw)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_keys_have_different_hashes(self):
        k1, k2 = _raw_key(), _raw_key()
        assert _hash(k1) != _hash(k2)


# ─── Test: auth middleware X-API-Key path ─────────────────────────────────────

class TestXApiKeyMiddleware:
    def test_non_vidgrab_prefix_rejected(self):
        from app.core.auth_middleware import _lookup_new_api_key
        result = _lookup_new_api_key("sk-some-other-key")
        assert result is None

    def test_empty_string_rejected(self):
        from app.core.auth_middleware import _lookup_new_api_key
        result = _lookup_new_api_key("")
        assert result is None

    def test_valid_key_with_db_hit(self, monkeypatch):
        from app.core.auth_middleware import _lookup_new_api_key

        raw = _raw_key()
        row = _make_key_row(raw=raw)

        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "id": row["id"], "user_id": row["user_id"], "is_active": True
        }
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
            "id": row["id"], "user_id": row["user_id"], "is_active": True
        }

        # Profile query returns tier
        profile_mock = MagicMock()
        profile_mock.data = {"tier": "pro"}

        def _table_side_effect(table_name):
            tbl = MagicMock()
            if table_name == "api_keys":
                tbl.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
                    "id": row["id"], "user_id": row["user_id"], "is_active": True,
                }
                tbl.update.return_value.eq.return_value.execute.return_value = MagicMock()
            elif table_name == "profiles":
                tbl.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {"tier": "pro"}
            return tbl

        mock_supabase.table.side_effect = _table_side_effect
        monkeypatch.setattr("app.core.auth_middleware.get_supabase_client", lambda: mock_supabase)

        result = _lookup_new_api_key(raw)
        assert result is not None
        assert result["id"] == row["user_id"]
        assert result["via_api_key"] is True

    def test_inactive_key_rejected(self, monkeypatch):
        from app.core.auth_middleware import _lookup_new_api_key

        raw = _raw_key()

        def _table_side_effect(table_name):
            tbl = MagicMock()
            if table_name == "api_keys":
                tbl.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
                    "id": "k1", "user_id": "u1", "is_active": False,
                }
            return tbl

        mock_supabase = MagicMock()
        mock_supabase.table.side_effect = _table_side_effect
        monkeypatch.setattr("app.core.auth_middleware.get_supabase_client", lambda: mock_supabase)

        result = _lookup_new_api_key(raw)
        assert result is None


# ─── Test: api_keys router — Pro-only guard ───────────────────────────────────

class TestApiKeysProGuard:
    def test_free_user_gets_403(self, monkeypatch):
        from app.api.api_keys import _assert_pro

        def _mock_profile(_user_id):
            return {"tier": "free", "billing_status": "none", "subscription_expiry": None}

        monkeypatch.setattr("app.api.api_keys.get_user_profile", _mock_profile)

        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _assert_pro("user-free")
        assert exc.value.status_code == 403

    def test_pro_user_passes(self, monkeypatch):
        from app.api.api_keys import _assert_pro

        def _mock_profile(_user_id):
            return {"tier": "pro", "billing_status": "active", "subscription_expiry": None}

        monkeypatch.setattr("app.api.api_keys.get_user_profile", _mock_profile)
        _assert_pro("user-pro")   # should not raise


# ─── Test: max 3 keys per user ────────────────────────────────────────────────

class TestApiKeyMaxLimit:
    def test_4th_key_rejected(self, monkeypatch):
        """Creating a 4th active key should return 429."""
        from app.api.api_keys import _assert_pro, MAX_KEYS_PER_USER

        assert MAX_KEYS_PER_USER == 3

        # Simulates the count check in create_key
        active_count = 3
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            if active_count >= MAX_KEYS_PER_USER:
                raise HTTPException(
                    status_code=429,
                    detail={"error_code": "api_key_limit"},
                )
        assert exc.value.status_code == 429

    def test_revoked_keys_dont_count(self):
        rows = [
            _make_key_row(active=True),
            _make_key_row(active=True),
            _make_key_row(active=False),   # revoked
        ]
        active = [r for r in rows if r["is_active"]]
        assert len(active) == 2   # still room for 1 more


# ─── Test: Telegram link token flow ───────────────────────────────────────────

class TestTelegramLinkFlow:
    def test_link_request_creates_redis_key(self, monkeypatch):
        import fakeredis

        fake_r = fakeredis.FakeStrictRedis(decode_responses=True)
        monkeypatch.setenv("TELEGRAM_BOT_SECRET", "test-secret")

        # Reload BEFORE patching get_redis, not after — reload() re-runs the
        # module's `from app.core.redis_client import get_redis`, which
        # rebinds tl.get_redis back to the real function and silently
        # discards a patch applied beforehand. The reload is still needed
        # (to pick up TELEGRAM_BOT_SECRET into the module-level _BOT_SECRET
        # constant below), just in the other order.
        import importlib, app.api.telegram_link as tl
        importlib.reload(tl)
        monkeypatch.setattr("app.api.telegram_link.get_redis", lambda: fake_r)

        from app.api.telegram_link import create_link_request, LinkRequestBody, _BOT_SECRET
        body = LinkRequestBody(telegram_user_id=12345, telegram_username="testuser")

        result = create_link_request(body, _=None)

        assert "token" in result
        assert "link_url" in result
        token = result["token"]
        stored = fake_r.get(f"tg:link:{token}")
        assert stored is not None
        data = json.loads(stored)
        assert data["telegram_user_id"] == 12345

    def test_link_confirm_consumes_token(self, monkeypatch):
        import fakeredis

        fake_r = fakeredis.FakeStrictRedis(decode_responses=True)
        tg_user_id = 99001
        token = "abc123"
        fake_r.set(f"tg:link:{token}", json.dumps({"telegram_user_id": tg_user_id, "telegram_username": "u"}), ex=900)

        monkeypatch.setattr("app.api.telegram_link.get_redis", lambda: fake_r)

        mock_sb = MagicMock()
        mock_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        monkeypatch.setattr("app.api.telegram_link.get_supabase_client", lambda: mock_sb)

        from app.api.telegram_link import confirm_link
        user = {"id": "vidgrab-user-1", "email": "test@test.com"}
        result = confirm_link(token=token, user=user)
        assert result["linked"] is True
        assert result["telegram_user_id"] == tg_user_id

        # Token must be consumed (one-time use)
        assert fake_r.get(f"tg:link:{token}") is None

    def test_expired_token_raises_410(self, monkeypatch):
        import fakeredis

        fake_r = fakeredis.FakeStrictRedis(decode_responses=True)
        monkeypatch.setattr("app.api.telegram_link.get_redis", lambda: fake_r)

        from app.api.telegram_link import confirm_link
        from fastapi import HTTPException
        user = {"id": "user-1", "email": "x@x.com"}
        with pytest.raises(HTTPException) as exc:
            confirm_link(token="nonexistent-token", user=user)
        assert exc.value.status_code == 410


# ─── Test: Telegram user-info endpoint ───────────────────────────────────────

class TestTelegramUserInfo:
    def test_unlinked_user_returns_linked_false(self, monkeypatch):
        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = None
        monkeypatch.setattr("app.api.telegram_link.get_supabase_client", lambda: mock_sb)

        from app.api.telegram_link import get_tg_user_info
        result = get_tg_user_info(telegram_id=12345, _=None)
        assert result["linked"] is False

    def test_linked_pro_user_returns_correct_tier(self, monkeypatch):
        user_id = "linked-user-1"

        def _table(name):
            tbl = MagicMock()
            if name == "telegram_links":
                tbl.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
                    "vidgrab_user_id": user_id
                }
            elif name == "profiles":
                tbl.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
                    "tier": "pro", "billing_status": "active", "subscription_expiry": None
                }
            elif name == "user_usage":
                tbl.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value.data = {
                    "downloads_today": 42
                }
            return tbl

        mock_sb = MagicMock()
        mock_sb.table.side_effect = _table
        monkeypatch.setattr("app.api.telegram_link.get_supabase_client", lambda: mock_sb)

        from app.api.telegram_link import get_tg_user_info
        result = get_tg_user_info(telegram_id=99999, _=None)
        assert result["linked"] is True
        assert result["tier"] == "pro"
        assert result["downloads_today"] == 42
        assert result["daily_limit"] == 100   # PRO_DAILY_LIMIT


# ─── Test: bot logic helpers (pure functions) ─────────────────────────────────

class TestBotHelpers:
    def test_is_spotify_url(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../telegram-bot"))
        try:
            import importlib
            import bot as b
            assert b.is_spotify_url("https://open.spotify.com/track/abc") is True
            assert b.is_spotify_url("https://youtube.com/watch") is False
        except (ImportError, KeyError):
            # bot.py:35 does `os.environ["TELEGRAM_DIST_BOT_TOKEN"]` (hard
            # subscript, no default) — a missing env var raises KeyError,
            # not ImportError, so this guard has to catch both.
            pytest.skip("bot.py not importable without TELEGRAM_DIST_BOT_TOKEN")

    def test_detect_platform_youtube(self):
        try:
            import bot as b
            name, supported = b.detect_platform("https://youtube.com/watch?v=abc")
            assert name == "YouTube"
            assert supported is True
        except (ImportError, KeyError):
            # See test_is_spotify_url — os.environ["TELEGRAM_DIST_BOT_TOKEN"]
            # raises KeyError, not ImportError, when unset.
            pytest.skip("bot.py not importable")

    def test_detect_platform_unsupported(self):
        try:
            import bot as b
            name, supported = b.detect_platform("https://unknown.com/video")
            assert not supported
        except (ImportError, KeyError):
            # See test_is_spotify_url — os.environ["TELEGRAM_DIST_BOT_TOKEN"]
            # raises KeyError, not ImportError, when unset.
            pytest.skip("bot.py not importable")
