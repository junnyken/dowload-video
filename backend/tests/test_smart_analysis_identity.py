"""Smart analysis identity (13/10): a client-chosen X-User-Id / user_id cookie
must not be accepted as identity; tier comes from the verified account."""
import asyncio

import pytest

from app.api import smart_analysis as sa


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_guest_without_verified_user_is_none():
    assert _run(sa.get_optional_user(user=None)) is None


def test_spoofed_header_is_ignored():
    """The dependency no longer takes the request at all, so a header or
    cookie cannot reach it; only auth_middleware's verified user can."""
    import inspect
    params = inspect.signature(sa.get_optional_user).parameters
    assert "request" not in params
    src = inspect.getsource(sa)
    assert 'headers.get("X-User-Id")' not in src
    assert 'cookies.get("user_id")' not in src


@pytest.mark.parametrize("tier", ["free", "pro", "team", "enterprise"])
def test_tier_from_profile(monkeypatch, tier):
    import app.core.quotas as q
    monkeypatch.setattr(q, "_get_tier", lambda uid: tier)
    u = _run(sa.get_optional_user(user={"id": "u1", "email": "a@b.c"}))
    assert u["id"] == "u1" and u["tier"] == tier


def test_profile_error_falls_back_to_free(monkeypatch):
    import app.core.quotas as q

    def boom(uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(q, "_get_tier", boom)
    assert _run(sa.get_optional_user(user={"id": "u1"}))["tier"] == "free"


def test_paid_tiers_get_full_results():
    res = {"trim_suggestions": [1, 2, 3]}
    for t in ("pro", "team", "enterprise"):
        assert sa._tier_filter_results(res, t) == res
    assert len(sa._tier_filter_results(res, "free")["trim_suggestions"]) == 1
