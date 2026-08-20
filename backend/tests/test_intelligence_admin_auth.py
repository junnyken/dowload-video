"""
The admin-gated routes in app/api/intelligence.py.

_require_admin called verify_admin without its required `request` argument, so
every one of these raised TypeError and answered 500 — verified against
production before the fix. It also forced bearer=None, leaving only the legacy
X-Admin-Token path, which the admin UI cannot produce: it authenticates with the
Bearer session token from POST /admin/login.

These assert the routes reject cleanly (401, not 500) and that a Bearer token is
actually consulted.
"""
import pytest

ADMIN_ROUTES = [
    "/api/v1/intelligence/playbooks",
    "/api/v1/intelligence/playbooks/history",
    "/api/v1/intelligence/auto-tune",
    "/api/v1/intelligence/automation-history",
]


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_rejects_with_401_not_500(app, path):
    r = app.get(path)
    assert r.status_code == 401, (
        f"{path} returned {r.status_code}; a missing credential must be a clean "
        "401, and 500 means the guard itself is broken"
    )


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_bearer_token_is_consulted(app, path):
    """A bad Bearer must be rejected as unauthorised, not blow up the handler."""
    r = app.get(path, headers={"Authorization": "Bearer not-a-real-session"})
    assert r.status_code == 401, f"{path} returned {r.status_code}"


def test_post_routes_reject_cleanly(app):
    for path, body in (
        ("/api/v1/intelligence/auto-tune/reset", None),
        ("/api/v1/intelligence/playbooks/execute", {"playbook_id": "x"}),
    ):
        r = app.post(path, json=body) if body else app.post(path)
        assert r.status_code in (401, 422), f"{path} returned {r.status_code}"
