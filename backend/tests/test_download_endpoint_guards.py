"""
HTTP-layer checks for the three GET endpoints that were unauthenticated,
unthrottled and SSRF-reachable: /proxy-download, /download-thumbnail,
/download-local.

Also pins the signature change: adding @limiter.limit required a
`request: Request` parameter — get that wrong and the route 500s on every
call, so these tests are as much a wiring check as a security check.
"""

import pytest


PRIVATE_TARGETS = [
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://127.0.0.1:6379/",
    "http://10.0.0.5/internal",
    "http://localhost:8000/api/v1/admin/stats",
    "file:///etc/passwd",
]


@pytest.mark.parametrize("target", PRIVATE_TARGETS)
def test_proxy_download_rejects_internal_targets(app, target):
    r = app.get("/api/v1/proxy-download", params={"url": target})
    assert r.status_code == 400, f"{target} was not rejected (got {r.status_code})"


@pytest.mark.parametrize("target", PRIVATE_TARGETS)
def test_download_thumbnail_rejects_internal_targets(app, target):
    r = app.get("/api/v1/download-thumbnail", params={"url": target})
    assert r.status_code == 400, f"{target} was not rejected (got {r.status_code})"


def test_proxy_download_requires_url(app):
    # Route wiring: a missing required query param must be a 422 from FastAPI,
    # not a 500 from a broken signature.
    r = app.get("/api/v1/proxy-download")
    assert r.status_code == 422


def test_download_local_still_blocks_path_traversal(app):
    r = app.get("/api/v1/download-local",
                params={"filepath": "../../../../etc/passwd", "filename": "x"})
    assert r.status_code == 403


def test_download_local_requires_params(app):
    r = app.get("/api/v1/download-local")
    assert r.status_code == 422


def test_trim_rejects_internal_source_url(app):
    # /trim downloaded payload.url with no SSRF guard at all before this fix.
    r = app.post("/api/v1/trim", json={
        "url": "http://169.254.169.254/latest/meta-data/",
        "start_time": 0, "end_time": 5,
    })
    assert r.status_code == 400


# ── Rate limiter must not be a hard availability dependency ───────────
# The test env has no Redis. Before in_memory_fallback_enabled/swallow_errors,
# that made every route 500 — including the health endpoints the extension and
# frontend poll to decide whether the server is up.

@pytest.mark.parametrize("path", ["/api/v1/ping", "/api/v1/platforms"])
def test_routes_survive_unreachable_rate_limit_store(app, path):
    r = app.get(path)
    assert r.status_code == 200, (
        f"{path} returned {r.status_code} with the limiter's Redis store down — "
        "the rate limiter must degrade, not take the API with it"
    )
