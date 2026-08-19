"""
Regression tests for the SSRF guard (app/core/ssrf_guard.py).

Each test below corresponds to a bypass that worked against the previous
per-module `_assert_safe_url`, which inspected only the literal hostname and
then handed the URL to an httpx client with follow_redirects=True.
"""

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from app.core import ssrf_guard
from app.core.ssrf_guard import assert_safe_url, safe_stream


@pytest.fixture(autouse=True)
def _clear_dns_cache():
    ssrf_guard._dns_cache.clear()
    yield
    ssrf_guard._dns_cache.clear()


# ── Scheme handling ──────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/", "data:text/html,x"])
def test_rejects_non_http_schemes(url):
    with pytest.raises(HTTPException) as e:
        assert_safe_url(url)
    assert e.value.status_code == 400


# ── IP-literal ranges ────────────────────────────────────────────────

@pytest.mark.parametrize("host", [
    "127.0.0.1",            # loopback
    "10.0.0.1",             # RFC1918
    "192.168.1.1",          # RFC1918
    "172.16.0.1",           # RFC1918
    "169.254.169.254",      # cloud instance metadata
    "0.0.0.0",              # unspecified
    "100.64.0.1",           # CGNAT — NOT flagged by ipaddress.is_private
    "[::1]",                # IPv6 loopback
])
def test_rejects_private_ip_literals(host):
    with pytest.raises(HTTPException):
        assert_safe_url(f"http://{host}/latest/meta-data/")


def test_allows_public_ip_literal():
    assert_safe_url("https://8.8.8.8/")


# ── The DNS bypass: hostname that RESOLVES somewhere private ─────────
# The old guard only ran ipaddress.ip_address() on the hostname string, so
# `127.0.0.1.nip.io` (not an IP literal, not in the 4-name blocklist) passed
# and then connected to loopback.

def test_rejects_hostname_resolving_to_loopback(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("127.0.0.1",))
    with pytest.raises(HTTPException):
        assert_safe_url("http://127-0-0-1.nip.io:6379/")


def test_rejects_hostname_with_any_private_record(monkeypatch):
    # Split-horizon: one public answer is not enough to make a name safe.
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34", "10.1.2.3"))
    with pytest.raises(HTTPException):
        assert_safe_url("https://split.example.com/")


def test_allows_hostname_resolving_public(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))
    assert_safe_url("https://example.com/video.mp4")


def test_unresolvable_host_is_not_a_hard_error(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ())
    assert_safe_url("https://nx-domain.invalid/x")


# ── Internal service names ───────────────────────────────────────────

@pytest.mark.parametrize("host", ["localhost", "redis", "backend", "celery", "metadata.google.internal"])
def test_rejects_internal_service_names(host):
    with pytest.raises(HTTPException):
        assert_safe_url(f"http://{host}:6379/")


# ── The redirect bypass ──────────────────────────────────────────────
# A public URL that 302s to a private one used to reach the private target,
# because only hop 0 was ever validated.

async def _test_safe_stream_blocks_redirect_to_metadata(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.example.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, content=b"SECRET-CREDENTIALS")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as e:
            async with safe_stream(client, "GET", "https://evil.example.com/r"):
                pass
    assert e.value.status_code == 400


async def _test_safe_stream_follows_public_redirects(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/r":
            return httpx.Response(302, headers={"location": "https://cdn.example.com/final.mp4"})
        return httpx.Response(200, content=b"VIDEO")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        async with safe_stream(client, "GET", "https://example.com/r") as resp:
            assert resp.status_code == 200
            assert await resp.aread() == b"VIDEO"


async def _test_safe_stream_caps_redirect_chain(monkeypatch):
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/loop"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException):
            async with safe_stream(client, "GET", "https://example.com/loop"):
                pass


def test_safe_stream_blocks_redirect_to_metadata(monkeypatch):
    asyncio.run(_test_safe_stream_blocks_redirect_to_metadata(monkeypatch))


def test_safe_stream_follows_public_redirects(monkeypatch):
    asyncio.run(_test_safe_stream_follows_public_redirects(monkeypatch))


def test_safe_stream_caps_redirect_chain(monkeypatch):
    asyncio.run(_test_safe_stream_caps_redirect_chain(monkeypatch))
