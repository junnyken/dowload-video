"""
get_optional_user called by hand, not through FastAPI's injection.

Its signature is (request, credentials, x_api_key) and the last two carry
Depends(...) defaults. Those defaults are only ever replaced when FastAPI
resolves the dependency — call the function directly with fewer arguments and
they stay as raw Depends objects, which are truthy. The first branch then runs
_lookup_new_api_key(<Depends>) and dies on .startswith.

Two call sites did exactly that, with different symptoms:

  intelligence._require_user  — nothing caught it, so every endpoint requiring
    a user answered 500 whether or not the caller was signed in.
  container.queue_container   — an `except: pass` swallowed it, so the queue
    silently ran as anonymous and jobs were never attributed to the account
    that asked for them.
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _request(token: str | None = "jwt-abc", api_key: str | None = None):
    req = MagicMock()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if api_key:
        headers["X-API-Key"] = api_key
    req.headers = headers
    req.client.host = "127.0.0.1"
    return req


class TestGetOptionalUserIsCalledCorrectly:

    def test_the_real_function_raises_when_under_called(self):
        """Pin the trap itself, so nobody reintroduces the short call."""
        from app.core.auth_middleware import get_optional_user

        with pytest.raises(Exception):
            asyncio.new_event_loop().run_until_complete(
                get_optional_user(_request())
            )

    def test_intelligence_require_user_passes_all_three_arguments(self):
        import app.api.intelligence as intel
        src = inspect.getsource(intel._require_user)
        assert "X-API-Key" in src, (
            "x_api_key must be passed explicitly or it stays a Depends object"
        )

    def test_intelligence_require_user_returns_the_user(self):
        import app.api.intelligence as intel

        with patch("app.core.auth_middleware.get_optional_user",
                   new=AsyncMock(return_value={"id": "u-1", "email": "a@b.c"})):
            user = asyncio.new_event_loop().run_until_complete(
                intel._require_user(_request())
            )
        assert user["id"] == "u-1"

    def test_intelligence_require_user_401s_when_anonymous(self):
        """Not a 500. An auth check must fail as a refusal, not a fault."""
        import app.api.intelligence as intel
        from fastapi import HTTPException

        with patch("app.core.auth_middleware.get_optional_user",
                   new=AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                asyncio.new_event_loop().run_until_complete(
                    intel._require_user(_request(token=None))
                )
        assert exc.value.status_code == 401

    def test_container_queue_extracts_the_user_id_not_the_dict(self):
        import app.api.container as container
        src = inspect.getsource(container)
        marker = 'user_id = user.get("id") if user else None'
        assert marker in src, (
            "the whole user dict used to be assigned to a variable typed "
            "Optional[str] and passed on as the job's user_id"
        )
