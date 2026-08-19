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


# ── A guard rejection must be a real error status, not an empty 200 ───
# StreamingResponse commits status 200 the moment it flushes headers. When the
# SSRF check lived inside the body generator, a redirect onto an internal host
# was still blocked (0 bytes leaked) but reached the client as "200 OK" with an
# empty body — indistinguishable from a network fault. Verified live against
# production before this was fixed.

import httpx


def _redirect_transport(final_target):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "redirector.example.com":
            return httpx.Response(302, headers={"location": final_target})
        return httpx.Response(200, content=b"SHOULD-NEVER-BE-REACHED")
    return httpx.MockTransport(handler)


@pytest.mark.parametrize("endpoint", ["/api/v1/proxy-download", "/api/v1/download-thumbnail"])
def test_redirect_to_internal_returns_error_status_not_empty_200(app, monkeypatch, endpoint):
    from app.core import ssrf_guard
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))

    transport = _redirect_transport("http://169.254.169.254/latest/meta-data/")
    real_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs.pop("proxy", None)
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)

    r = app.get(endpoint, params={"url": "https://redirector.example.com/r"})
    assert r.status_code == 400, (
        f"{endpoint} returned {r.status_code}; a blocked redirect must be an "
        "explicit error, not a silent empty 200"
    )
    assert b"SHOULD-NEVER-BE-REACHED" not in r.content


# ── The extraction endpoints hand their URL straight to yt-dlp ────────
# /fetch-link, /bulk-download and /zip-stream had no SSRF guard at all: an
# internal URL reached the extractor and came back as a generic 500, making
# them blind request-forgery primitives against the internal network. Found
# by probing the live deployment after the first release of this work.

@pytest.fixture
def _disk_guardrail_usable(monkeypatch, tmp_path):
    """
    The disk guardrail middleware runs before these routes and mkdir()s
    DOWNLOAD_DIR, which defaults to /app/downloads — present in the container,
    not writable in the test env, where it 500s before the handler is reached.
    Point it at a temp dir so the request actually gets to the guard.
    """
    from app.core import disk_guardrail
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))


@pytest.mark.parametrize("target", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:6379/",
    "http://10.0.0.5/internal",
])
def test_fetch_link_rejects_internal_targets(app, _disk_guardrail_usable, target):
    r = app.post("/api/v1/fetch-link", json={"url": target, "quality": "video"})
    assert r.status_code == 400, f"{target} reached the extractor (got {r.status_code})"


def test_bulk_download_rejects_internal_target_in_list(app, _disk_guardrail_usable):
    r = app.post("/api/v1/bulk-download", json={
        "urls": ["https://www.tiktok.com/@x/video/1234567890123456789",
                 "http://169.254.169.254/latest/meta-data/"],
    })
    assert r.status_code == 400


def test_zip_stream_rejects_internal_target_in_list(app):
    r = app.post("/api/v1/zip-stream", json={
        "urls": ["http://127.0.0.1:6379/"],
    })
    assert r.status_code == 400
