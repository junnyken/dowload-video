"""
Outbound user-webhook delivery with SSRF protection (Phase 33-0, task #6254).

deliver_webhook_task used to `requests.post()` whatever URL a user had
registered — redirects followed, no address check — so a webhook pointed at
http(s)://10.x / 127.0.0.1 / 169.254.169.254, or at a public name that later
resolves there, made the worker send signed POSTs into the internal network.

Rules (the same address rules as app.core.ssrf_guard, applied strictly):
  * https only, no credentials in the URL, no internal compose hostnames;
  * every resolved address must be public (private, loopback, link-local,
    multicast, reserved, unspecified, CGNAT, cloud metadata); an unresolvable
    name is rejected, not waved through;
  * resolution is fresh (no cache) and repeated right before sending, and the
    request is sent to that exact validated IP (TLS SNI + certificate checked
    against the hostname), so DNS rebinding between check and connect cannot
    redirect it;
  * redirects are never followed; 10 s timeout; at most MAX_RESPONSE_BYTES of
    the response are read and the body is never stored or echoed.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from app.core.ssrf_guard import BLOCKED_HOSTNAMES, blocked_reason

DELIVERY_TIMEOUT_S = 10
MAX_RESPONSE_BYTES = 4096

# Covered by the link-local / ULA rules already; listed so the intent survives
# any future loosening of those rules.
_METADATA_ADDRS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),   # Alibaba Cloud metadata
}


class WebhookUrlError(ValueError):
    """The URL is not an acceptable webhook target. `permanent` = retrying
    cannot help (blocked address, bad scheme); False for DNS failures."""

    def __init__(self, message: str, *, permanent: bool = True):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class WebhookTarget:
    url: str
    host: str
    port: int
    path: str          # path + query, never empty
    ips: tuple[str, ...]


@dataclass
class DeliveryResult:
    status_code: int
    success: bool
    error: Optional[str]


def _resolve_fresh(host: str, port: int) -> tuple[str, ...]:
    """All A/AAAA records for `host`, uncached (rebinding-relevant)."""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return tuple(dict.fromkeys(info[4][0] for info in infos))


def _address_problem(raw: str) -> Optional[str]:
    try:
        addr = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return "unparseable address"
    if addr in _METADATA_ADDRS:
        return "cloud metadata address"
    # IPv4-mapped IPv6 (::ffff:10.0.0.1) → judge the IPv4 address.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return blocked_reason(addr)


def validate_webhook_url(url: str) -> WebhookTarget:
    """Parse, check and resolve `url`. Raises WebhookUrlError."""
    if not isinstance(url, str) or not url.strip():
        raise WebhookUrlError("webhook_url is empty")
    url = url.strip()
    if len(url) > 2048:
        raise WebhookUrlError("webhook_url is too long")
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError:
        raise WebhookUrlError("webhook_url is not a valid URL")
    if parts.scheme.lower() != "https":
        raise WebhookUrlError("webhook_url must start with https://")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise WebhookUrlError("webhook_url must not contain credentials")
    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise WebhookUrlError("webhook_url has no host")
    if any(host == b or host.endswith(f".{b}") for b in BLOCKED_HOSTNAMES):
        raise WebhookUrlError("webhook_url points at an internal host")

    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    try:
        ipaddress.ip_address(host)
        ips: tuple[str, ...] = (host,)
    except ValueError:
        try:
            ips = _resolve_fresh(host, port)
        except (socket.gaierror, UnicodeError, OSError):
            ips = ()
        if not ips:
            raise WebhookUrlError("webhook_url host does not resolve", permanent=False)

    for raw in ips:
        problem = _address_problem(raw)
        if problem:
            raise WebhookUrlError(f"webhook_url resolves to a {problem}")

    return WebhookTarget(url=url, host=host, port=port, path=path, ips=ips)


def _make_pool(target: WebhookTarget, ip: str, timeout: float):
    """HTTPS pool that connects to `ip` but speaks TLS for `target.host`."""
    import certifi
    import urllib3

    return urllib3.HTTPSConnectionPool(
        host=ip,
        port=target.port,
        server_hostname=target.host,
        assert_hostname=target.host,
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        timeout=urllib3.Timeout(connect=timeout, read=timeout),
        retries=False,
        maxsize=1,
        block=False,
    )


def post_webhook(url: str, body: bytes, headers: dict, *,
                 timeout: float = DELIVERY_TIMEOUT_S) -> DeliveryResult:
    """Validate (fresh DNS), then POST `body` to the validated IP.
    Raises WebhookUrlError when the target is not allowed."""
    target = validate_webhook_url(url)
    ip = target.ips[0]
    host_header = f"[{target.host}]" if ":" in target.host else target.host   # IPv6 literal
    if target.port != 443:
        host_header = f"{host_header}:{target.port}"
    send_headers = {**headers, "Host": host_header}

    pool = _make_pool(target, ip, timeout)
    try:
        resp = pool.urlopen(
            "POST", target.path, body=body, headers=send_headers,
            redirect=False, retries=False, preload_content=False,
            assert_same_host=False,
        )
        try:
            resp.read(MAX_RESPONSE_BYTES)   # bounded; content discarded
        except Exception:
            pass
        finally:
            try:
                resp.release_conn()
            except Exception:
                pass
        status = int(resp.status)
    finally:
        try:
            pool.close()
        except Exception:
            pass

    if 200 <= status < 300:
        return DeliveryResult(status, True, None)
    if 300 <= status < 400:
        return DeliveryResult(status, False, f"HTTP {status} (redirect not followed)")
    return DeliveryResult(status, False, f"HTTP {status}")
