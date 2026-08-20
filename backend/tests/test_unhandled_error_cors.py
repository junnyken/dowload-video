"""
An unhandled exception must come back as a normal HTTP 500, not a bare ASGI
error response.

Starlette's ServerErrorMiddleware sits outside CORSMiddleware, so an exception
that is not an HTTPException produced a 500 with no CORS headers. The browser
blocks that and the frontend reports "Failed to fetch" — a network-level message
for a server-side bug, with no status to show the user. Observed for real on the
admin Automation History page.
"""
import pytest
from fastapi import APIRouter


@pytest.fixture
def boom_route(app):
    """Mount a route that raises a plain (non-HTTP) exception."""
    from app.main import app as fastapi_app
    r = APIRouter()

    @r.get("/__boom__")
    async def _boom():
        raise RuntimeError("deliberate failure for the CORS test")

    fastapi_app.include_router(r)
    yield
    fastapi_app.router.routes = [
        rt for rt in fastapi_app.router.routes
        if getattr(rt, "path", None) != "/__boom__"
    ]


def test_unhandled_exception_returns_json_500(app, boom_route):
    r = app.get("/__boom__")
    assert r.status_code == 500
    assert r.json().get("detail") == "Internal server error"


def test_unhandled_exception_keeps_cors_headers(app, boom_route):
    """Without this the browser sees a network failure instead of the 500."""
    origin = "http://localhost:5173"      # in the configured allow-list
    r = app.get("/__boom__", headers={"Origin": origin})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == origin, (
        "500 lost its CORS headers; the browser will report 'Failed to fetch' "
        "and the real status never reaches the app"
    )


def test_internal_detail_is_not_leaked(app, boom_route):
    r = app.get("/__boom__")
    assert "deliberate failure" not in r.text
