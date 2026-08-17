"""
Client IP extraction — single source of truth.

Takes the LAST entry of X-Forwarded-For, not the first. A well-behaved
reverse proxy only ever APPENDS the real client IP to this header before
forwarding upstream, so the last entry is the one nearest to us (added by
our own trusted proxy) — the first entry is whatever the original client
sent, which is attacker-controlled and trivially spoofable to bypass
IP-based quotas, rate limits, and the /admin IP allowlist.

Every place in this codebase that needs a client IP (quota, rate limiting,
audit log, admin allowlist) MUST go through this function instead of
reading request.client.host or the X-Forwarded-For header directly —
those two other paths route through uvicorn's --forwarded-allow-ips '*'
trust setting, which picks the FIRST (spoofable) entry.
"""

from fastapi import Request


def get_client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "127.0.0.1"
