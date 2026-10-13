"""
Public site links set from the admin (task #6331).

Owner rule: operational settings live in the admin, not in env. The first
link is the external status page (UptimeRobot / Better Stack): it must open
when vibe1 is down, so it is hosted elsewhere and the web footer only links
to it. Unset → the footer shows nothing (never a dead link).

Storage: Redis hash  site:links  name -> url
"""
from __future__ import annotations

import time
from typing import Dict, Optional
from urllib.parse import urlparse

KEY = "site:links"
STATUS_PAGE = "status_page_url"
NAMES = frozenset({STATUS_PAGE})
MAX_LEN = 300
_CACHE_TTL = 30.0
_cache: list = []   # [(expires_at, dict)]


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def validate(url: Optional[str]) -> Optional[str]:
    """Normalised https URL, None to clear. Raises ValueError otherwise."""
    u = (url or "").strip()
    if not u:
        return None
    if len(u) > MAX_LEN:
        raise ValueError("Địa chỉ quá dài")
    p = urlparse(u)
    if p.scheme != "https" or not p.hostname or any(c.isspace() for c in u):
        raise ValueError("Cần địa chỉ https:// đầy đủ")
    return u


def get_links() -> Dict[str, Optional[str]]:
    """Never raises: an unreadable Redis gives empty links."""
    now = time.monotonic()
    if _cache and _cache[0][0] > now:
        return dict(_cache[0][1])
    out: Dict[str, Optional[str]] = {n: None for n in NAMES}
    try:
        raw = _r().hgetall(KEY) or {}
        for k, v in raw.items():
            k = k.decode() if isinstance(k, bytes) else k
            v = v.decode() if isinstance(v, bytes) else v
            if k in NAMES and v:
                out[k] = v
    except Exception:  # noqa: BLE001
        pass
    _cache[:] = [(now + _CACHE_TTL, out)]
    return dict(out)


def set_link(name: str, url: Optional[str]) -> Optional[str]:
    if name not in NAMES:
        raise ValueError(f"Unknown link: {name}")
    clean = validate(url)
    rc = _r()
    if clean:
        rc.hset(KEY, name, clean)
    else:
        rc.hdel(KEY, name)
    _cache.clear()
    return clean
