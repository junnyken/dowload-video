"""Extension session token (owner 22/10/2026): the extension stayed a guest an
hour after "Kết nối tài khoản" (the web's 1-hour Supabase token was never
refreshed). Now: a signed 30-day vgx_ token, renewable, revocable."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import extension_session as xs
from app.main import app as fastapi_app
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
USER = {"id": "11111111-2222-3333-4444-555555555555", "email": "a@example.com", "email_confirmed_at": "2026-10-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.delenv("EXTENSION_SESSION_KEY", raising=False)


def test_issue_and_verify(rc):
    out = xs.issue(USER["id"], USER["email"], now=1_000_000, email_confirmed_at=USER["email_confirmed_at"])
    assert out["token"].startswith("vgx_") and out["expiresAt"] == 1_000_000 + 30 * 24 * 3600
    u = xs.verify(out["token"], now=1_000_100)
    assert u["id"] == USER["id"] and u["email"] == USER["email"]
    assert u["email_confirmed_at"] == USER["email_confirmed_at"] and u["via_extension"] is True


def test_expired_tampered_other_key(rc, monkeypatch):
    tok = xs.issue(USER["id"], USER["email"], now=1_000_000)["token"]
    assert xs.verify(tok, now=1_000_000 + 30 * 24 * 3600) is None          # expired
    body, sig = tok[4:].split(".")
    assert xs.verify("vgx_" + body + "." + sig[:-2] + "AA", now=1_000_001) is None   # bad signature
    forged = xs._b64(b'{"sub":"someone-else","exp":9999999999,"gen":0}')
    assert xs.verify(f"vgx_{forged}.{sig}", now=1_000_001) is None         # payload swapped
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "rotated")
    assert xs.verify(tok, now=1_000_001) is None                           # key changed
    for junk in ("", "vgx_", "vgx_abc", "eyJhbGciOi.x.y", None):
        assert xs.verify(junk) is None


def test_no_key_no_tokens(rc, monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert xs.issue(USER["id"], USER["email"]) is None


def test_revoke_kills_older_tokens(rc):
    old = xs.issue(USER["id"], USER["email"])["token"]
    assert xs.verify(old)
    assert xs.revoke(USER["id"]) is True
    assert xs.verify(old) is None
    new = xs.issue(USER["id"], USER["email"])["token"]
    assert xs.verify(new)["id"] == USER["id"]


def _as(user):
    from app.core.auth_middleware import get_optional_user
    fastapi_app.dependency_overrides[get_optional_user] = lambda: user


def test_endpoint_issues_for_signed_in_only(rc):
    try:
        _as(None)
        r = client.post("/api/v1/extension/session")
        assert r.status_code == 401 and r.json()["error_code"] == "login_required"
        _as({**USER, "via_api_key": True, "token": "vidgrab_x"})
        assert client.post("/api/v1/extension/session").status_code == 401   # an API key is not a session
        _as({**USER, "token": "jwt"})
        r = client.post("/api/v1/extension/session")
        assert r.status_code == 200 and r.json()["token"].startswith("vgx_") and r.json()["email"] == USER["email"]
        assert xs.verify(r.json()["token"])["id"] == USER["id"]
        r2 = client.delete("/api/v1/extension/session")
        assert r2.status_code == 200 and r2.json()["revoked"] is True
        assert xs.verify(r.json()["token"]) is None
    finally:
        fastapi_app.dependency_overrides.clear()


def test_middleware_accepts_vgx_as_a_session(rc, monkeypatch):
    """A vgx_ Bearer is the user (not metered like an API key); an expired one is a guest."""
    import asyncio
    from fastapi.security import HTTPAuthorizationCredentials
    from app.core import auth_middleware as am

    async def _no_sync(_u):
        return None
    monkeypatch.setattr(am, "_ensure_app_user", _no_sync)
    tok = xs.issue(USER["id"], USER["email"])["token"]
    u = asyncio.run(am.get_optional_user(None, HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok), None))
    assert u["id"] == USER["id"] and u["token"] == tok and not u.get("via_api_key")
    xs.revoke(USER["id"])
    u = asyncio.run(am.get_optional_user(None, HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok), None))
    assert u is None
