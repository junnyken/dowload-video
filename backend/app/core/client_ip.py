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


_API_KEY_PREFIXES = ("vidgrab_", "vg_", "vgp_")


def presented_api_key(request: Request) -> str | None:
    """The API key the caller sent (X-API-Key, or Bearer with a key prefix),
    or None. A Supabase session JWT is not an API key."""
    raw = (request.headers.get("x-api-key") or "").strip()
    if raw:
        return raw
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):].strip()
        if token.startswith(_API_KEY_PREFIXES):
            return token
    return None


def rate_limit_key(request: Request) -> str:
    """Limiter bucket: per API key when the caller sent one, else per IP.

    A partner's backend usually sits behind ONE IP: counting per IP capped
    every key behind it at the guest limits, whatever its plan. A key that
    turns out to be invalid gets 401 from the auth layer; an IP that keeps
    presenting invalid keys drops back to its IP bucket (bad_key_guard), so
    spraying random keys cannot mint fresh buckets."""
    key = presented_api_key(request)
    if key:
        # Only a key the auth layer has already resolved (bad_key_guard,
        # 10-min cache) gets its own bucket; anything else — a made-up key,
        # a key on a route that never checks it — counts against the IP.
        from app.core.bad_key_guard import is_known_valid_key, key_hash  # noqa: PLC0415
        if is_known_valid_key(key):
            return "apikey:" + key_hash(key)
    return get_client_ip(request)


def has_valid_api_key(request: Request) -> bool:
    key = presented_api_key(request)
    if not key:
        return False
    from app.core.bad_key_guard import is_known_valid_key  # noqa: PLC0415
    return is_known_valid_key(key)
