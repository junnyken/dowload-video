"""API key auth (14/10): Bearer vidgrab_ works, a presented-but-bad key is
401 (not a silent guest), key management needs a web session, Team and
Enterprise may hold keys."""
import asyncio

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core import auth_middleware as am

GOOD = {"id": "u1", "email": "", "tier": "pro", "token": "x", "via_api_key": True}


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def _call(bearer=None, xkey=None):
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials=bearer) if bearer else None
    return _run(am.get_optional_user(None, credentials=cred, x_api_key=xkey))


@pytest.fixture()
def lookups(monkeypatch):
    monkeypatch.setattr(am, "_lookup_new_api_key", lambda k: GOOD if k == "vidgrab_good" else None)
    monkeypatch.setattr(am, "_lookup_api_key", lambda t: GOOD if t == "vg_good" else None)

    class _SB:
        class auth:
            @staticmethod
            def get_user(token):
                raise RuntimeError("not a jwt")
    monkeypatch.setattr(am, "get_supabase_client", lambda: _SB)


def test_no_credentials_is_guest(lookups):
    assert _call() is None


def test_x_api_key_good(lookups):
    assert _call(xkey="vidgrab_good")["id"] == "u1"


def test_bearer_vidgrab_good(lookups):
    assert _call(bearer="vidgrab_good")["id"] == "u1"


@pytest.mark.parametrize("kw", [{"xkey": "vidgrab_bad"}, {"xkey": "anything"},
                                {"bearer": "vidgrab_bad"}, {"bearer": "vg_bad"}])
def test_bad_key_is_401(lookups, kw):
    with pytest.raises(HTTPException) as e:
        _call(**kw)
    assert e.value.status_code == 401
    assert e.value.detail["error_code"] == "invalid_api_key"


def test_legacy_bearer_good(lookups):
    assert _call(bearer="vg_good")["id"] == "u1"


def test_stale_web_session_is_still_guest(lookups):
    # a JWT the server cannot validate must not lock the site's guest flow
    assert _call(bearer="eyJhbGciOi.stale.token") is None


def test_key_management_needs_session():
    from app.api.api_keys import _assert_session
    with pytest.raises(HTTPException) as e:
        _assert_session({"id": "u1", "via_api_key": True})
    assert e.value.status_code == 403
    _assert_session({"id": "u1"})   # web session passes


@pytest.mark.parametrize("tier,ok", [("free", False), ("pro", True), ("team", True), ("enterprise", True)])
def test_key_tiers(monkeypatch, tier, ok):
    import app.core.quotas as q
    from app.api import api_keys
    monkeypatch.setattr(q, "_get_tier", lambda uid: tier)
    if ok:
        api_keys._assert_pro("u1")
    else:
        with pytest.raises(HTTPException):
            api_keys._assert_pro("u1")


def test_legacy_generate_refuses_api_key_caller():
    from app.api.user import _require_pro
    with pytest.raises(HTTPException) as e:
        _require_pro({"id": "u1", "via_api_key": True})
    assert e.value.status_code == 403
