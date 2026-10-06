"""
One free redirect lookup for share links (v.kuaishou.com, xhslink.com).

Only the first hop is read: the request goes to the share-link host the
adapter already validated, redirects are NOT followed, and the Location value
is only parsed, never fetched here. So the lookup cannot be pointed at another
host (no SSRF surface). Any failure returns None and the caller keeps the
share link as it is (both chosen actors accept share links).

Measured from the workspace on 2026-10-06: v.kuaishou.com answered a 302 in
0.3 s (www.kuaishou.com itself times out); xhslink.com answered a 302 in 6 s.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.services.china_platforms import settings

logger = logging.getLogger("app.china_access")

_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


async def first_redirect(url: str, *, transport: Optional[httpx.AsyncBaseTransport] = None) -> Optional[str]:
    kw = {
        "follow_redirects": False,
        "timeout": settings.short_link_timeout_sec(),
        "headers": {"User-Agent": _UA},
        "trust_env": False,
    }
    if transport is not None:
        kw["transport"] = transport
    try:
        async with httpx.AsyncClient(**kw) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        logger.info("china_access: short-link lookup failed: %s", type(exc).__name__)
        return None
    if resp.status_code not in (301, 302, 303, 307, 308):
        return None
    loc = resp.headers.get("location") or ""
    if loc.startswith("//"):
        loc = "https:" + loc
    return loc if loc.lower().startswith(("http://", "https://")) else None
