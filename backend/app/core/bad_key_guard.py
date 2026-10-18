"""
Bad API key guard — per-IP counter of presented-but-invalid API keys.

Requests that carry an API key are rate-limited per KEY rather than per IP
(app.core.client_ip.rate_limit_key), and the Partner API is exempt from the
per-IP limiter altogether. Without this guard a caller could spray random
keys: every new key is a fresh, empty bucket, and every attempt costs a
database lookup. Once an IP has presented BAD_KEY_LIMIT invalid keys in the
current minute:
  - rate_limit_key() falls back to that IP's bucket (the old per-IP limits);
  - the Partner API answers 429 before looking the key up.

Fails OPEN on Redis errors, like every other counter in this service.
"""
from __future__ import annotations

import time

from fastapi import Request

from app.core.client_ip import get_client_ip

BAD_KEY_LIMIT = 20
_TTL = 120


def _redis_key(request: Request) -> str:
    return f"badkey:{get_client_ip(request)}:{int(time.time() // 60)}"


def note_bad_key(request: Request | None) -> None:
    if request is None:
        return
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        r = get_redis()
        k = _redis_key(request)
        if r.incr(k) == 1:
            r.expire(k, _TTL)
    except Exception:  # noqa: BLE001
        pass


def too_many_bad_keys(request: Request | None) -> bool:
    if request is None:
        return False
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        raw = get_redis().get(_redis_key(request))
        return int(raw or 0) >= BAD_KEY_LIMIT
    except Exception:  # noqa: BLE001
        return False
