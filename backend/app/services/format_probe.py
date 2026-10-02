"""
Measured format facts for TikWM streams
=======================================

TikWM hands back stream URLs and byte sizes, never dimensions or codecs, so the
format list used to print guesses. This module measures them for real with
ffprobe against the remote URL — ffprobe reads only the container header (the
moov box, ~a few hundred KB) over HTTP range requests, nothing is written to
disk.

It is called from POST /api/v1/formats/probe AFTER the format list has
rendered, so /fetch-link stays exactly as fast as before.

Security — this is an SSRF surface (the server opens a URL the client names):

1. Issued-only: fetch-link records every TikWM format URL it hands out
   (`register_issued_urls`). The probe refuses any URL it did not issue.
   If Redis is unreachable the check FAILS CLOSED (no probe), because we
   cannot prove issuance.
2. Host allowlist (defence in depth): https only, hostname must end in one of
   PROBE_HOST_SUFFIXES.
3. SSRF guard: every redirect hop is resolved via `assert_safe_url` (DNS ->
   reject private / loopback / link-local / CGNAT) AND re-checked against the
   allowlist. ffprobe is then handed the FINAL url so its own redirect
   following has nothing left to follow.
4. ffprobe is restricted to `-protocol_whitelist https,tls,tcp` and forced to
   the mp4 demuxer (`-f mp4`), so a playlist or `file:`/`http:` hop cannot be
   smuggled in, and the mov demuxer's external-reference loading is off by
   default.
5. Short timeouts: `-rw_timeout` 6 s per socket op, 10 s for the whole process
   (killed on expiry), bounded concurrency per worker.

Residual risk: DNS rebinding between our resolution and ffprobe's own connect
is not pinned (same residual as app.core.ssrf_guard); the allowlist limits it
to TikTok-owned names.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

MAX_URLS = 4
RW_TIMEOUT_US = 6_000_000          # ffprobe -rw_timeout, microseconds
PROC_TIMEOUT_S = 10.0              # whole ffprobe process
RESOLVE_TIMEOUT_S = 6.0            # redirect pre-resolution
MAX_REDIRECTS = 3
CACHE_TTL_MAX = 6 * 3600
ISSUED_TTL_MAX = 6 * 3600
_CONCURRENCY = 4

_ISSUED_PREFIX = "fmtprobe:issued:"
_RESULT_PREFIX = "fmtprobe:res:"

# Hosts seen on a live TikWM fetch-link (02-10-2026):
#   hdplay  v19-notes.tiktokcdn-us.com
#   play    v16m.tiktokcdn-us.com
#   wmplay  api16-normal-useast5.tiktokv.us  -> 302 -> v45-lite.tiktokcdn-us.com
#   music   v16-ies-music.tiktokcdn-us.com
# tiktokcdn.com / tiktokv.com / tiktokcdn-eu.com / tiktokv.eu are the non-US
# siblings of the same CDN (not observed in that one call); tikwm.com covers
# TikWM's own relay paths. Anything else is refused.
PROBE_HOST_SUFFIXES = (
    "tiktokcdn-us.com",
    "tiktokcdn.com",
    "tiktokcdn-eu.com",
    "tiktokv.us",
    "tiktokv.com",
    "tiktokv.eu",
    "tikwm.com",
)

_sem: Optional[asyncio.Semaphore] = None


class ProbeError(Exception):
    """A per-URL failure; the message is the short error code returned."""


def _get_redis():
    from app.core.redis_client import get_redis
    return get_redis()


def url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def host_allowed(host: str) -> bool:
    host = (host or "").strip().rstrip(".").lower()
    return any(host == s or host.endswith("." + s) for s in PROBE_HOST_SUFFIXES)


_HEX_TS = re.compile(r"^[0-9a-f]{8}$")


def url_expiry(url: str, now: Optional[float] = None) -> Optional[int]:
    """
    Unix time the signed CDN URL stops working, if the URL says so.

    Query params `x-expires` / `expires` / `expire` (decimal), or TikTok's
    path form /<sig>/<hex-ts>/video/... where the 8-hex segment is the expiry
    (verified live: 6abfa75b == fetch time + 6 h).
    """
    now = time.time() if now is None else now
    try:
        p = urlparse(url)
    except Exception:
        return None
    qs = {k.lower(): v for k, v in parse_qs(p.query).items()}
    for k in ("x-expires", "expires", "expire"):
        if k in qs:
            try:
                return int(qs[k][0])
            except (ValueError, IndexError):
                pass
    for seg in p.path.split("/")[1:4]:
        if _HEX_TS.match(seg):
            ts = int(seg, 16)
            # Only trust it if it is a plausible near-future timestamp.
            if now - 86400 < ts < now + 7 * 86400:
                return ts
    return None


def ttl_for(url: str, cap: int, now: Optional[float] = None) -> int:
    """Seconds to keep something about `url`: <= cap, <= the URL's own expiry."""
    now = time.time() if now is None else now
    exp = url_expiry(url, now)
    if exp is None:
        return cap
    return max(0, min(cap, int(exp - now)))


# ── issued-URL registry ────────────────────────────────────────────────

def register_issued_urls(urls: Iterable[str]) -> None:
    """Remember URLs fetch-link handed out so the probe can refuse others. Fail-soft."""
    try:
        rc = _get_redis()
        now = time.time()
        for u in urls:
            if not u or not u.startswith("https://"):
                continue
            ttl = ttl_for(u, ISSUED_TTL_MAX, now)
            if ttl > 0:
                rc.setex(_ISSUED_PREFIX + url_key(u), ttl, "1")
    except Exception as e:  # never break fetch-link over this
        print(f"[FormatProbe] register_issued_urls failed: {e}")


def _was_issued(url: str) -> bool:
    try:
        return bool(_get_redis().exists(_ISSUED_PREFIX + url_key(url)))
    except Exception as e:
        # Fail closed: without Redis we cannot prove we issued this URL.
        print(f"[FormatProbe] issued check unavailable: {e}")
        return False


def check_url(url: Any) -> Optional[str]:
    """Static + registry checks. Returns an error code, or None if probe-able."""
    if not isinstance(url, str) or not url or len(url) > 4096:
        return "invalid_url"
    try:
        p = urlparse(url)
    except Exception:
        return "invalid_url"
    if p.scheme != "https":
        return "scheme_not_allowed"
    if not host_allowed(p.hostname or ""):
        return "host_not_allowed"
    if not _was_issued(url):
        return "not_issued"
    return None


# ── result cache ──────────────────────────────────────────────────────

def _cache_get(url: str) -> Optional[dict]:
    try:
        raw = _get_redis().get(_RESULT_PREFIX + url_key(url))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _cache_set(url: str, result: dict) -> None:
    try:
        ttl = ttl_for(url, CACHE_TTL_MAX)
        if ttl > 0:
            _get_redis().setex(_RESULT_PREFIX + url_key(url), ttl, json.dumps(result))
    except Exception:
        pass


# ── ffprobe ───────────────────────────────────────────────────────────

def _num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def parse_ffprobe_json(raw: bytes | str) -> dict:
    """ffprobe -show_streams -show_format JSON -> the fields the UI shows."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ProbeError("bad_probe_output")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        raise ProbeError("no_video_stream")
    width = _num(v.get("width"), int)
    height = _num(v.get("height"), int)
    if not width or not height:
        raise ProbeError("no_dimensions")
    bit_rate = _num(fmt.get("bit_rate")) or _num(v.get("bit_rate"))
    duration = _num(fmt.get("duration")) or _num(v.get("duration"))
    return {
        "width": width,
        "height": height,
        "vcodec": v.get("codec_name"),
        "acodec": a.get("codec_name") if a else None,
        "bitrate_kbps": round(bit_rate / 1000) if bit_rate else None,
        "duration": round(duration, 2) if duration else None,
    }


async def _resolve_final_url(url: str) -> str:
    """
    Follow redirects ourselves, validating every hop (https + allowlist +
    DNS/private-IP guard), and return the URL that answers with content.
    """
    import httpx
    from fastapi import HTTPException
    from app.core.ssrf_guard import assert_safe_url

    current = url
    async with httpx.AsyncClient(timeout=RESOLVE_TIMEOUT_S) as client:
        for _ in range(MAX_REDIRECTS + 1):
            p = urlparse(current)
            if p.scheme != "https":
                raise ProbeError("redirect_scheme_not_allowed")
            if not host_allowed(p.hostname or ""):
                raise ProbeError("redirect_host_not_allowed")
            try:
                assert_safe_url(current)
            except HTTPException:
                raise ProbeError("blocked_address")
            async with client.stream(
                "GET", current, headers={"Range": "bytes=0-0"}, follow_redirects=False,
            ) as resp:
                loc = resp.headers.get("location")
                if resp.status_code in (301, 302, 303, 307, 308) and loc:
                    current = urljoin(current, loc)
                    continue
                if resp.status_code >= 400:
                    raise ProbeError(f"http_{resp.status_code}")
                return current
    raise ProbeError("too_many_redirects")


def _ffprobe_args(url: str) -> list[str]:
    return [
        "ffprobe", "-v", "error",
        "-protocol_whitelist", "https,tls,tcp",
        "-rw_timeout", str(RW_TIMEOUT_US),
        "-f", "mp4",
        "-print_format", "json", "-show_streams", "-show_format",
        url,
    ]


async def _run_ffprobe(url: str) -> bytes:
    try:
        proc = await asyncio.create_subprocess_exec(
            *_ffprobe_args(url),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise ProbeError("ffprobe_unavailable")
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=PROC_TIMEOUT_S)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except Exception:
            pass
        raise ProbeError("timeout")
    if proc.returncode != 0:
        raise ProbeError("probe_failed")
    return out


async def probe_one(url: Any) -> dict:
    err = check_url(url)
    if err:
        return {"url": url if isinstance(url, str) else None, "error": err}
    cached = _cache_get(url)
    if cached is not None:
        return {"url": url, **cached, "cached": True}

    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_CONCURRENCY)
    t0 = time.monotonic()
    try:
        async with _sem:
            final = await asyncio.wait_for(_resolve_final_url(url), timeout=RESOLVE_TIMEOUT_S + 2)
            result = parse_ffprobe_json(await _run_ffprobe(final))
    except ProbeError as e:
        return {"url": url, "error": str(e)}
    except asyncio.TimeoutError:
        return {"url": url, "error": "timeout"}
    except Exception as e:
        print(f"[FormatProbe] unexpected error: {type(e).__name__}: {e}")
        return {"url": url, "error": "probe_failed"}
    print(f"[FormatProbe] {urlparse(url).hostname} {result['width']}x{result['height']} "
          f"{result['vcodec']} in {time.monotonic() - t0:.2f}s")
    _cache_set(url, result)
    return {"url": url, **result}


async def probe_urls(urls: list) -> list[dict]:
    # De-duplicate while keeping order; caller has already capped the count.
    seen, uniq = set(), []
    for u in urls:
        k = u if isinstance(u, str) else repr(u)
        if k not in seen:
            seen.add(k)
            uniq.append(u)
    return list(await asyncio.gather(*(probe_one(u) for u in uniq)))
