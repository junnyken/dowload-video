"""21/10/2026: session lookups off the event loop, with a timeout and a short cache."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import fakeredis
import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core import auth_middleware as am


def _jwt(exp):
    body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    return r


def _client(calls, delay=0.0):
    def get_user(token):
        calls.append(token)
        time.sleep(delay)
        return SimpleNamespace(user=SimpleNamespace(id="u1", email="a@b.c", email_confirmed_at=None))
    return SimpleNamespace(auth=SimpleNamespace(get_user=get_user))


def _call(token):
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    return asyncio.run(am.get_optional_user(SimpleNamespace(), creds, None))


def test_a_verified_session_is_remembered_and_never_stores_the_token(rc, monkeypatch):
    calls = []
    monkeypatch.setattr(am, "get_supabase_client", lambda: _client(calls))
    tok = _jwt(time.time() + 3600)
    assert _call(tok)["id"] == "u1"
    assert _call(tok) == {"id": "u1", "email": "a@b.c", "email_confirmed_at": None, "token": tok}
    assert len(calls) == 1
    assert all(tok not in (rc.get(k) or "") and tok not in k for k in rc.keys())
    assert 0 < rc.ttl(am._session_key(tok)) <= am.SESSION_CACHE_S


def test_cache_never_outlives_the_token(rc, monkeypatch):
    calls = []
    monkeypatch.setattr(am, "get_supabase_client", lambda: _client(calls))
    tok = _jwt(time.time() + 3)                     # expires in 3 s: not cached
    _call(tok)
    _call(tok)
    assert len(calls) == 2


def test_slow_supabase_gives_a_guest_after_the_timeout(rc, monkeypatch):
    monkeypatch.setattr(am, "SESSION_LOOKUP_TIMEOUT_S", 0.2)
    monkeypatch.setattr(am, "get_supabase_client", lambda: _client([], delay=1.0))
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_jwt(time.time() + 3600))

    async def timed():
        t = time.monotonic()
        user = await am.get_optional_user(SimpleNamespace(), creds, None)
        return user, time.monotonic() - t
    user, took = asyncio.run(timed())   # (asyncio.run then waits for the thread)
    assert user is None and took < 0.9
