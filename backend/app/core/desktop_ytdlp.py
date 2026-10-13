"""
Approved yt-dlp for the Windows app (task #6333, Phase 34A-3).
==============================================================
The app bundles a pinned yt-dlp (desktop/binaries.lock.json). Platforms change
their anti-bot measures often, and an outdated bundled yt-dlp then breaks every
app user until a new app is released. An admin can approve a NEWER official
yt-dlp.exe here; the app downloads it, checks the sha256 given here and only
then uses it (falling back to its bundled copy on any problem).

Stored in Redis (one JSON string, no TTL), set from the admin page
"App Windows" — not from host env vars:

  version  "2026.10.10" (yt-dlp's YYYY.MM.DD[.N])
  url      exactly https://github.com/yt-dlp/yt-dlp/releases/download/<version>/yt-dlp.exe
  sha256   64 hex, copied from that release's SHA2-256SUMS
  enabled  default false. Off → GET /client/ytdlp answers 204 and the app goes
           back to its bundled yt-dlp (rollback = untick "enabled").

Nothing here downloads anything: the server never fetches yt-dlp itself.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

KEY = "vidgrab:desktop:ytdlp"
URL_PREFIX = "https://github.com/yt-dlp/yt-dlp/releases/download/"
ASSET = "yt-dlp.exe"
_VERSION = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})(?:\.(\d{1,6}))?$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class Invalid(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def valid_version(v: str) -> bool:
    m = _VERSION.match(v or "")
    if not m:
        return False
    month, day = int(m.group(2)), int(m.group(3))
    return 1 <= month <= 12 and 1 <= day <= 31


def expected_url(version: str) -> str:
    return f"{URL_PREFIX}{version}/{ASSET}"


def validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Raises Invalid; returns the normalized record."""
    if not isinstance(payload, dict):
        raise Invalid("bad_payload", "JSON object expected")
    version = str(payload.get("version") or "").strip()
    url = str(payload.get("url") or "").strip()
    sha = str(payload.get("sha256") or "").strip().lower()
    enabled = payload.get("enabled", False)
    if not isinstance(enabled, bool):
        raise Invalid("bad_enabled", "enabled must be true or false")
    if not valid_version(version):
        raise Invalid("bad_version", "version must look like 2026.10.10 (or 2026.10.10.1)")
    if not url:
        url = expected_url(version)
    if url != expected_url(version):
        raise Invalid("bad_url", f"url must be exactly {expected_url(version)}")
    if not _SHA.match(sha):
        raise Invalid("bad_sha256", "sha256 must be 64 hex characters (from SHA2-256SUMS)")
    return {"version": version, "url": url, "sha256": sha, "enabled": enabled}


def get() -> Optional[Dict[str, Any]]:
    """The stored record, or None (nothing set / unreadable / Redis down)."""
    try:
        raw = _r().get(KEY)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        rec = validate(data)
    except (ValueError, TypeError):
        return None
    rec["updated_at"] = data.get("updated_at")
    return rec


def put(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validates and stores; raises Invalid, or whatever Redis raises."""
    rec = validate(payload)
    rec["updated_at"] = datetime.now(timezone.utc).isoformat()
    _r().set(KEY, json.dumps(rec))
    return rec


def offer() -> Optional[Dict[str, str]]:
    """What the app gets: {version, url, sha256} when enabled, else None."""
    rec = get()
    if not rec or not rec["enabled"]:
        return None
    return {"version": rec["version"], "url": rec["url"], "sha256": rec["sha256"]}
