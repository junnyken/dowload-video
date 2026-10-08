"""
Recent failed download attempts — the reason, kept where admins can read it.

Task #6148 (08/10/2026): an owner's extension download failed with HTTP 500
and the cause could not be found. The API's own logs are not readable from
the hosting panel (only the Celery worker's are), and they are wiped by
every redeploy (six on that day). The outcome counters only keep an error
CODE; "processing_failed" says nothing about why.

Every failed /fetch-link attempt (and every unhandled 5xx on /api/v1/) adds
one row here: Redis list ``vidgrab:recent_errors``, newest first, capped at
MAX_ROWS, 7-day TTL refreshed on write. The admin "/errors" endpoint reads it.

Rows hold no user id, no cookie, no token: the reason text is redacted
(proxy credentials, key=/token= query values, Bearer / cookie strings) and
the URL is reduced to host + path. Writing never raises — a Redis outage
must not change what the user gets.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

KEY = "vidgrab:recent_errors"
MAX_ROWS = 200
TTL_S = 7 * 24 * 3600
REASON_MAX = 600

# scheme://user:pass@host → scheme://***@host (yt-dlp prints proxy URLs)
_CRED_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
# ?api_key=… &token=… key=… sig=… (query strings and "k=v" in messages)
_SECRET_KV = re.compile(
    r"(?i)\b(api[_-]?key|apikey|key|token|access_token|auth|signature|sig|"
    r"password|passwd|pwd|session(?:id)?|sessionid|secret|x-amz-[a-z-]+)=([^&\s\"']+)")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
# Netscape cookie-file lines / Cookie headers
_COOKIE = re.compile(r"(?i)\b(cookie:?)\s*[^\n]{0,400}")
# long opaque blobs (JWTs, base64 cookie jars)
_BLOB = re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b|\b[A-Za-z0-9+/]{120,}={0,2}")


def redact(text: Any) -> str:
    s = str(text or "")
    s = _CRED_URL.sub(r"\1***@", s)
    s = _SECRET_KV.sub(lambda m: f"{m.group(1)}=***", s)
    s = _BEARER.sub(r"\1 ***", s)
    s = _COOKIE.sub(r"\1 ***", s)
    s = _BLOB.sub("***", s)
    return s[:REASON_MAX]


def url_brief(url: Optional[str]) -> str:
    """host + path, no query (ids and signatures live there), ≤160 chars."""
    try:
        p = urlsplit(str(url or "").strip())
        if not p.netloc:
            return redact(url)[:160]
        return (p.netloc + p.path)[:160]
    except Exception:
        return ""


def client_source(origin: Optional[str], vg_source: Optional[str]) -> str:
    """web | extension | app | api — where the request came from."""
    o = (origin or "").lower()
    v = (vg_source or "").lower()
    if v == "desktop" or o.startswith("tauri://") or "tauri.localhost" in o:
        return "app"
    if o.startswith("chrome-extension://") or o.startswith("moz-extension://"):
        return "extension"
    if o.startswith("http"):
        return "web"
    return "api"


def record(*, platform: str, status: int, error_code: str, reason: Any,
           url: Optional[str] = None, quality: Optional[str] = None,
           signed_in: Optional[bool] = None, source: str = "",
           user_cookies: bool = False, path: str = "/api/v1/fetch-link",
           rc=None) -> bool:
    """Add one row. Returns False (never raises) when it could not be kept."""
    try:
        row = {
            "ts": round(time.time(), 3),
            "path": path,
            "platform": platform or "other",
            "status": int(status or 0),
            "error_code": str(error_code or "")[:60],
            "reason": redact(reason),
            "url": url_brief(url),
            "quality": str(quality or "")[:30],
            "kind": None if signed_in is None else ("user" if signed_in else "guest"),
            "source": source,
            "user_cookies": bool(user_cookies),
        }
        if rc is None:
            from app.core.redis_client import get_redis
            rc = get_redis()
        pipe = rc.pipeline()
        pipe.lpush(KEY, json.dumps(row, ensure_ascii=False))
        pipe.ltrim(KEY, 0, MAX_ROWS - 1)
        pipe.expire(KEY, TTL_S)
        pipe.execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("recent_errors.record failed: %s", type(exc).__name__)
        return False


def read(limit: int = 50, platform: Optional[str] = None, rc=None) -> Optional[list]:
    """Newest first. None when Redis cannot be read (unknown, not empty)."""
    try:
        if rc is None:
            from app.core.redis_client import get_redis
            rc = get_redis()
        raw = rc.lrange(KEY, 0, MAX_ROWS - 1) or []
    except Exception:
        return None
    out = []
    for item in raw:
        try:
            row = json.loads(item.decode() if isinstance(item, bytes) else item)
        except Exception:
            continue
        if platform and row.get("platform") != platform:
            continue
        out.append(row)
        if len(out) >= max(1, min(int(limit), MAX_ROWS)):
            break
    return out
