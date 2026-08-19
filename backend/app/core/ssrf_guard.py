"""
SSRF guard
==========

Hardened replacement for the previous per-module `_assert_safe_url` helpers.

Two holes the old version had:

1. It only inspected the *literal* hostname. `http://127.0.0.1.nip.io:6379/`
   or any attacker-controlled domain with an A record pointing at 10.x/127.x
   sailed straight through, because the string wasn't an IP literal and wasn't
   in the four-entry name blocklist.
2. Callers then streamed the URL with `httpx.AsyncClient(follow_redirects=True)`.
   Validating hop 0 is worthless when hops 1..n are unchecked: a public URL
   that 302s to `http://169.254.169.254/latest/meta-data/` reached cloud
   metadata with the guard reporting success.

`assert_safe_url` now resolves the hostname and rejects if ANY returned
address falls in a non-public range. `safe_stream` follows redirects manually,
re-validating every hop.

Residual risk: DNS rebinding between our resolution and httpx's connect is not
addressed here (that needs connect-time IP pinning). The practical bypasses —
redirects and private-pointing hostnames — are closed.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from contextlib import asynccontextmanager
from typing import Iterable
from urllib.parse import urljoin, urlparse

from fastapi import HTTPException

# Hostnames that resolve inside the compose network / are never legitimate
# download sources. Kept as a belt-and-braces layer on top of the IP checks.
BLOCKED_HOSTNAMES = (
    "localhost", "redis", "cobalt-api", "backend", "celery",
    "worker", "flower", "postgres", "db", "metadata", "metadata.google.internal",
)

MAX_REDIRECTS = 5

# Short-lived DNS memo so a hot endpoint doesn't re-resolve on every request.
_DNS_TTL = 30.0
_dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}


def _blocked_reason(addr: ipaddress._BaseAddress) -> str | None:
    """Return a reason string if `addr` is not a public, routable address."""
    if addr.is_private:        # covers 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16, ::1, fc00::/7
        return "private/loopback/link-local address"
    if addr.is_loopback:
        return "loopback address"
    if addr.is_link_local:
        return "link-local address"
    if addr.is_reserved:
        return "reserved address"
    if addr.is_multicast:
        return "multicast address"
    if addr.is_unspecified:
        return "unspecified address"
    # Carrier-grade NAT (100.64.0.0/10) is not flagged private by ipaddress but
    # is routinely used for internal infrastructure.
    if isinstance(addr, ipaddress.IPv4Address) and addr in ipaddress.ip_network("100.64.0.0/10"):
        return "shared address space (CGNAT)"
    return None


def _resolve(hostname: str) -> tuple[str, ...]:
    """Resolve `hostname` to every A/AAAA record, memoised for _DNS_TTL seconds."""
    now = time.monotonic()
    hit = _dns_cache.get(hostname)
    if hit and now - hit[0] < _DNS_TTL:
        return hit[1]
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Unresolvable host — let the HTTP client fail naturally rather than
        # 400-ing a name that may just be a transient DNS blip.
        return ()
    addrs = tuple({info[4][0] for info in infos})
    _dns_cache[hostname] = (now, addrs)
    return addrs


def assert_safe_url(url: str, *, detail_factory=None) -> None:
    """
    Reject non-http(s) schemes, blocked hostnames, and any URL whose hostname
    resolves to a non-public address. Raises HTTPException(400).

    `detail_factory` lets callers keep their existing error-payload shape
    (routes.py uses `make_error("invalid_url")`, processing.py uses plain text).
    """
    def _fail(msg: str):
        detail = detail_factory(msg) if detail_factory else msg
        raise HTTPException(status_code=400, detail=detail)

    try:
        parsed = urlparse(url)
    except Exception:
        _fail("Invalid URL")
        return

    if parsed.scheme not in ("http", "https"):
        _fail("Only http/https URLs are allowed")

    hostname = (parsed.hostname or "").strip().rstrip(".").lower()
    if not hostname:
        _fail("Invalid URL")

    if any(hostname == b or hostname.endswith(f".{b}") for b in BLOCKED_HOSTNAMES):
        _fail("URL resolves to a blocked internal host")

    # IP literal → check directly, no DNS needed.
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        reason = _blocked_reason(addr)
        if reason:
            _fail(f"URL points at a {reason}")
        return

    # Domain name → every resolved address must be public.
    for raw in _resolve(hostname):
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            continue
        reason = _blocked_reason(addr)
        if reason:
            _fail(f"URL resolves to a {reason}")


@asynccontextmanager
async def safe_stream(client, method: str, url: str, *, max_redirects: int = MAX_REDIRECTS,
                      detail_factory=None, **kwargs):
    """
    `client.stream(...)` with manual, re-validated redirect following.

    Callers MUST NOT also pass follow_redirects=True to the client; this
    helper sets follow_redirects=False per request so no hop escapes the guard.
    Yields the httpx.Response for the first non-redirect hop.
    """
    kwargs.pop("follow_redirects", None)
    current = url

    for _ in range(max_redirects + 1):
        assert_safe_url(current, detail_factory=detail_factory)
        cm = client.stream(method, current, follow_redirects=False, **kwargs)
        resp = await cm.__aenter__()

        location = resp.headers.get("location")
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            await cm.__aexit__(None, None, None)
            current = urljoin(current, location)
            continue

        try:
            yield resp
        finally:
            await cm.__aexit__(None, None, None)
        return

    raise HTTPException(status_code=400, detail="Too many redirects")


async def safe_head(client, url: str, *, max_redirects: int = MAX_REDIRECTS,
                    detail_factory=None, **kwargs):
    """HEAD with the same manual, re-validated redirect handling as safe_stream."""
    kwargs.pop("follow_redirects", None)
    current = url

    for _ in range(max_redirects + 1):
        assert_safe_url(current, detail_factory=detail_factory)
        resp = await client.head(current, follow_redirects=False, **kwargs)
        location = resp.headers.get("location")
        if resp.status_code in (301, 302, 303, 307, 308) and location:
            current = urljoin(current, location)
            continue
        return resp

    raise HTTPException(status_code=400, detail="Too many redirects")
