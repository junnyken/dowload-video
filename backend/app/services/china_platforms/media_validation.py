"""
Usable-media validation (Phase 32B-2 §5.4, Stage A — Douyin).

A provider "success" only says the provider returned a title and an http(s)
URL. This module checks that the URL actually serves a playable video, from
this server, right now:

  1. SSRF guard on every hop (app/core/ssrf_guard.safe_stream: scheme, blocked
     internal names, every resolved address public, redirects re-validated).
  2. ONE bounded range request (bytes=0..MAX-1, default 2 MB); at most one more
     to complete an MP4 moov box that starts inside the prefix (cap 4 MB).
  3. HTTP status 200/206, a media content type (not text/html/json/xml), and a
     non-trivial body (total size, or bytes read, ≥ MIN bytes).
  4. ffprobe on the fetched bytes from stdin only (format_probe: no network,
     `-protocol_whitelist pipe`): a video stream and a plausible duration.

Results are honest:
  usable        every check passed, ffprobe included
  unusable      a check failed (blocked host, HTTP error, HTML page, tiny body,
                no video stream, implausible duration, network error)
  not_checked   the HTTP checks passed but ffprobe could not judge: ffprobe is
                not installed (local dev), or the moov box is not in the prefix.
                Never reported as success.

The signed media URL is never stored, logged or returned; only the status
code, the bare MIME type, byte counts, the duration, a reason code and the
expiry parsed from the URL are.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx

from app.services.china_platforms import settings

logger = logging.getLogger("app.china_access")

MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_SANE_DURATION_SEC = 6 * 3600

# Douyin's CDN rejects header-less requests (403) — same headers the existing
# Douyin download branch sends (downloader.py, "_cdn_headers").
PLATFORM_HEADERS: dict[str, dict[str, str]] = {
    "douyin": {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Referer": "https://www.douyin.com/",
        "Accept": "*/*",
    },
}
# Phase 32B-3: the same headers downloader._china_layer_server_copy sends, so
# a benchmark check measures what a real download would get. UNVERIFIED
# against the live CDNs (docs/china-access/09).
for _p, _ref in (("kuaishou", "https://www.kuaishou.com/"), ("xiaohongshu", "https://www.xiaohongshu.com/")):
    PLATFORM_HEADERS[_p] = {**PLATFORM_HEADERS["douyin"], "Referer": _ref}

_BAD_CT = ("text/", "application/json", "application/xml", "application/xhtml", "application/javascript")
_OK_CT_PREFIX = ("video/", "application/octet-stream", "binary/octet-stream", "application/mp4")

FfprobeRunner = Callable[[bytes], Awaitable[bytes]]

RESULT_FIELDS = (
    "media_check", "usable_media_url", "media_url_http_status", "media_content_type",
    "media_bytes_read", "media_total_bytes", "media_duration_sec", "media_check_reason",
    "media_url_expiry_if_known",
)


def expiry_iso(url: str) -> Optional[str]:
    """x-expires / expires / TikTok hex-path expiry → ISO UTC, else None."""
    try:
        from app.services.format_probe import url_expiry  # noqa: PLC0415
        ts = url_expiry(url)
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat() if ts else None
    except Exception:  # noqa: BLE001
        return None


def _result(check: str, reason: Optional[str], **kw) -> dict:
    out = {f: None for f in RESULT_FIELDS}
    out.update(kw)
    out["media_check"] = check
    out["media_check_reason"] = reason
    out["usable_media_url"] = {"usable": True, "unusable": False}.get(check)
    return out


def not_run() -> dict:
    """Shape used when validation was not attempted (no media URL, or off)."""
    return _result("not_run", None)


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


async def _default_ffprobe(data: bytes) -> bytes:
    from app.services.format_probe import _run_ffprobe  # noqa: PLC0415
    return await _run_ffprobe(data)


def _blocked_reason(exc) -> str:
    return "too_many_redirects" if "redirect" in str(getattr(exc, "detail", "")).lower() else "ssrf_blocked"


def _content_type(resp) -> str:
    return (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()


async def _read_range(client, url: str, start: int, end: int, headers: dict, cap: int):
    """One SSRF-guarded GET for bytes start..end. Returns (resp_meta, bytes)."""
    from app.core.ssrf_guard import safe_stream  # noqa: PLC0415
    h = dict(headers)
    h["Range"] = f"bytes={start}-{end}"
    h["Accept-Encoding"] = "identity"
    async with safe_stream(client, "GET", url, headers=h) as resp:
        meta = {
            "status": resp.status_code,
            "content_type": _content_type(resp),
            "total": None,
            "final_url": str(resp.url),
        }
        try:
            from app.services.format_probe import _parse_total  # noqa: PLC0415
            meta["total"] = _parse_total(resp)
        except Exception:  # noqa: BLE001
            meta["total"] = None
        buf = bytearray()
        if resp.status_code in (200, 206):
            want = min(end - start + 1, cap)
            async for chunk in resp.aiter_raw():
                buf += chunk[: want - len(buf)]
                if len(buf) >= want:
                    break
        return meta, bytes(buf)


async def validate_media_url(
    url: str,
    *,
    platform: str = "douyin",
    max_bytes: Optional[int] = None,
    min_bytes: Optional[int] = None,
    timeout_sec: Optional[float] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    ffprobe: Optional[FfprobeRunner] = None,
    ffprobe_present: Optional[bool] = None,
) -> dict:
    """Validate one resolved media URL. Never raises; never returns the URL."""
    max_bytes = int(max_bytes or settings.media_validate_max_bytes())
    min_bytes = int(min_bytes or settings.media_validate_min_bytes())
    timeout = float(timeout_sec or settings.media_validate_timeout_sec())
    expiry = expiry_iso(url) if url else None
    if not url or not url.lower().startswith(("http://", "https://")):
        return _result("unusable", "no_media_url", media_url_expiry_if_known=expiry)

    headers = PLATFORM_HEADERS.get(platform, {})
    kw: dict = {"timeout": timeout, "trust_env": False, "follow_redirects": False}
    if transport is not None:
        kw["transport"] = transport
    base = {"media_url_expiry_if_known": expiry}
    try:
        from fastapi import HTTPException  # noqa: PLC0415
        async with httpx.AsyncClient(**kw) as client:
            try:
                meta, data = await _read_range(client, url, 0, max_bytes - 1, headers, max_bytes)
            except HTTPException as he:
                return _result("unusable", _blocked_reason(he), **base)
            status, ctype, total = meta["status"], meta["content_type"], meta["total"]
            base.update(media_url_http_status=status, media_content_type=ctype or None,
                        media_total_bytes=total)
            if status not in (200, 206):
                return _result("unusable", f"http_{status}", media_bytes_read=0, **base)
            if ctype and (ctype.startswith(_BAD_CT) or not ctype.startswith(_OK_CT_PREFIX)):
                return _result("unusable", "not_media_content_type", media_bytes_read=len(data), **base)
            size_seen = total if total is not None else len(data)
            if size_seen < min_bytes or len(data) == 0:
                return _result("unusable", "body_too_small", media_bytes_read=len(data), **base)

            from app.services.format_probe import scan_moov  # noqa: PLC0415
            verdict, moov_end = scan_moov(data, total)
            if verdict == "partial" and status == 206 and moov_end <= MAX_TOTAL_BYTES:
                try:
                    _m2, more = await _read_range(client, meta["final_url"], len(data), moov_end - 1,
                                                  headers, MAX_TOTAL_BYTES - len(data))
                except HTTPException as he:
                    return _result("unusable", _blocked_reason(he), media_bytes_read=len(data), **base)
                data += more
                verdict, moov_end = scan_moov(data, total)
    except httpx.TimeoutException:
        return _result("unusable", "fetch_timeout", **base)
    except httpx.HTTPError as exc:
        return _result("unusable", f"fetch_error:{type(exc).__name__}", **base)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access media validation error: %s", type(exc).__name__)
        return _result("unusable", f"fetch_error:{type(exc).__name__}", **base)

    base["media_bytes_read"] = len(data)
    if verdict != "ok":
        # Cannot hand ffprobe a parseable prefix: moov after mdat, truncated, or
        # not an MP4 box layout. Honest "not_checked", not a failure.
        reason = {"after_mdat": "moov_not_in_prefix", "need_more": "moov_not_in_prefix",
                  "partial": "moov_not_in_prefix"}.get(verdict, "unrecognized_container")
        return _result("not_checked", reason, **base)

    present = ffprobe_available() if ffprobe_present is None else ffprobe_present
    if ffprobe is None and not present:
        return _result("not_checked", "ffprobe_unavailable", **base)
    runner = ffprobe or _default_ffprobe
    from app.services.format_probe import ProbeError, parse_ffprobe_json  # noqa: PLC0415
    try:
        raw = await runner(data)
        info = parse_ffprobe_json(raw, size_bytes=total)
    except ProbeError as exc:
        code = str(exc)
        if code == "ffprobe_unavailable":
            return _result("not_checked", "ffprobe_unavailable", **base)
        return _result("unusable", f"ffprobe:{code}", **base)
    except Exception as exc:  # noqa: BLE001
        return _result("unusable", f"ffprobe:{type(exc).__name__}", **base)
    duration = info.get("duration")
    base["media_duration_sec"] = duration
    if not duration or not (0 < float(duration) <= MAX_SANE_DURATION_SEC):
        return _result("unusable", "implausible_duration", **base)
    return _result("usable", None, **base)
