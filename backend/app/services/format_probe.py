"""
Measured format facts for TikWM streams
=======================================

TikWM hands back stream URLs and byte sizes, never dimensions or codecs, so the
format list used to print guesses. This module measures them for real: our own
code downloads a bounded PREFIX of the file (the moov box sits at the front of
TikTok's faststart MP4s) and ffprobe parses those bytes from stdin. Nothing is
written to disk.

It is called from POST /api/v1/formats/probe AFTER the format list has
rendered, so /fetch-link stays exactly as fast as before.

Security — this is an SSRF surface (the server opens a URL the client names):

1. Issued-only: fetch-link records every TikWM format URL it hands out
   (`register_issued_urls`). The probe refuses any URL it did not issue.
   If Redis is unreachable the check FAILS CLOSED (no probe), because we
   cannot prove issuance.
2. Host allowlist (defence in depth): https only, hostname must end in one of
   PROBE_HOST_SUFFIXES — re-checked on every redirect hop.
3. IP pinning: every hop's hostname is resolved exactly ONCE
   (`socket.getaddrinfo`), every returned address must pass
   `ssrf_guard.ip_is_public` (same rules as `assert_safe_url`), and the TCP
   connection goes to that validated IP literal. TLS still sends SNI = the
   hostname and verifies the certificate against the hostname
   (`extensions={"sni_hostname": host}` -> ssl `server_hostname`), and the
   Host header carries the hostname. No second DNS lookup exists for a
   rebinding answer to land in. Each hop uses a fresh client, so a pooled
   TLS session for one name is never reused for another. `trust_env=False`
   keeps proxy env vars from re-routing the connection.
4. Redirects are followed by hand (MAX_REDIRECTS), never by httpx.
5. ffprobe never touches the network: `-protocol_whitelist pipe`, input
   `pipe:0`, forced `-f mp4`. It only sees bytes we already fetched.
6. Byte budget: at most MAX_TOTAL_BYTES per URL, enforced while streaming
   (we stop reading once the budget is spent). Only one extra range fetch is
   allowed, and only to complete a moov box that starts inside the prefix;
   a moov placed after mdat gives `moov_not_in_prefix`.
7. Short timeouts: per-hop httpx timeout, an overall fetch deadline, and the
   ffprobe process is killed after PROC_TIMEOUT_S. Bounded concurrency.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
import time
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urljoin, urlparse

MAX_URLS = 4
PROC_TIMEOUT_S = 10.0              # whole ffprobe process
HOP_TIMEOUT_S = 6.0                # httpx read/write timeout per hop
CONNECT_TIMEOUT_S = 3.0            # per pinned-IP TCP+TLS connect attempt
MAX_CONNECT_TRIES = 2              # validated IPs tried per hop on connect failure
FETCH_TIMEOUT_S = 15.0             # whole prefix fetch (all hops)
PREFIX_BYTES = 2 * 1024 * 1024     # first range request: bytes=0-(PREFIX_BYTES-1)
MAX_TOTAL_BYTES = 4 * 1024 * 1024  # hard cap on body bytes read per URL
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


def parse_ffprobe_json(raw: bytes | str, size_bytes: Optional[int] = None) -> dict:
    """
    ffprobe -show_streams -show_format JSON -> the fields the UI shows.

    ffprobe now reads a prefix from a pipe, so it cannot know the file size.
    When the HTTP total size is known, bitrate = size*8/duration — the same
    figure ffprobe reported when it opened the URL itself.
    """
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
    duration = _num(fmt.get("duration")) or _num(v.get("duration"))
    bit_rate = None
    if size_bytes and duration:
        bit_rate = size_bytes * 8 / duration
    bit_rate = bit_rate or _num(fmt.get("bit_rate")) or _num(v.get("bit_rate"))
    return {
        "width": width,
        "height": height,
        "vcodec": v.get("codec_name"),
        "acodec": a.get("codec_name") if a else None,
        "bitrate_kbps": round(bit_rate / 1000) if bit_rate else None,
        "duration": round(duration, 2) if duration else None,
    }


# ── pinned, bounded fetch ─────────────────────────────────────────────

_REDIRECTS = (301, 302, 303, 307, 308)
_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.I)


def _tls_verify():
    """httpx `verify=` value. True = system/certifi CAs with hostname checking.
    Tests swap in an SSLContext that trusts a throwaway CA."""
    return True


def _new_client():
    import httpx
    return httpx.AsyncClient(
        timeout=httpx.Timeout(HOP_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
        verify=_tls_verify(),
        trust_env=False,          # no proxy env: the socket must go to the pinned IP
        follow_redirects=False,
    )


async def _resolve_pinned(host: str, port: int) -> list[str]:
    """
    Resolve `host` ONCE, require EVERY address to be public, and return them
    in resolver order. Connections for the hop go only to these addresses;
    this is the only DNS lookup for the hop.
    """
    if not host:
        raise ProbeError("invalid_url")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        # Allowlisted names are never IP literals; refuse rather than special-case.
        raise ProbeError("blocked_address")
    from app.core.ssrf_guard import ip_is_public
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM, socket.IPPROTO_TCP,
        )
    except (socket.gaierror, UnicodeError, OSError):
        raise ProbeError("dns_failed")
    addrs = [info[4][0] for info in infos]
    if not addrs:
        raise ProbeError("dns_failed")
    if not all(ip_is_public(a) for a in addrs):
        raise ProbeError("blocked_address")
    return list(dict.fromkeys(addrs))


class _Budget:
    def __init__(self, limit: int):
        self.left = limit
        self.read = 0


def _parse_total(resp) -> Optional[int]:
    if resp.status_code == 206:
        m = _CONTENT_RANGE.match(resp.headers.get("content-range", "").strip())
        if m and m.group(3) != "*":
            return int(m.group(3))
        return None
    cl = resp.headers.get("content-length")
    return int(cl) if cl and cl.isdigit() else None


async def _range_get(client, url: str, host: str, port: int, ip: str,
                     start: int, end: int, budget: _Budget, stop=None):
    """
    One GET to the pinned `ip` for `url`, asking for bytes start..end.

    Returns ("redirect", location) or ("data", bytes, total_size). Never reads
    more than min(end-start+1, budget.left) body bytes: the loop stops as soon
    as that many have arrived, whatever the server keeps sending. `stop(buf,
    total)` may end the read earlier (e.g. once the moov box is complete).
    """
    import httpx
    pinned = httpx.URL(url).copy_with(host=ip)
    host_header = host if port == 443 else f"{host}:{port}"
    headers = {
        "Host": host_header,
        "Range": f"bytes={start}-{end}",
        "Accept-Encoding": "identity",
    }
    async with client.stream("GET", pinned, headers=headers,
                             extensions={"sni_hostname": host}) as resp:
        if resp.status_code in _REDIRECTS:
            loc = resp.headers.get("location")
            if not loc:
                raise ProbeError(f"http_{resp.status_code}")
            return ("redirect", loc)
        if resp.status_code >= 400:
            raise ProbeError(f"http_{resp.status_code}")
        if resp.status_code not in (200, 206):
            raise ProbeError(f"http_{resp.status_code}")
        enc = resp.headers.get("content-encoding", "identity").strip().lower()
        if enc not in ("", "identity"):
            raise ProbeError("unexpected_encoding")
        if start > 0:
            m = _CONTENT_RANGE.match(resp.headers.get("content-range", "").strip())
            if resp.status_code != 206 or not m or int(m.group(1)) != start:
                raise ProbeError("range_not_supported")
        want = min(end - start + 1, budget.left)
        if want <= 0:
            raise ProbeError("byte_cap_exceeded")
        total = _parse_total(resp)
        buf = bytearray()
        async for chunk in resp.aiter_raw():
            buf += chunk[: want - len(buf)]
            if len(buf) >= want or (stop is not None and stop(buf, total)):
                break
        budget.left -= len(buf)
        budget.read += len(buf)
        return ("data", bytes(buf), total)


def scan_moov(buf: bytes, total: Optional[int] = None) -> tuple[str, int]:
    """
    Walk top-level MP4 boxes in `buf`.

    ("ok", end)           moov is complete inside buf, ending at `end`
    ("partial", end)      moov starts in buf but ends at `end` > len(buf)
    ("after_mdat", 0)     mdat comes before moov (moov at the end of the file)
    ("need_more", 0)      buf ends before any moov/mdat box header
    ("bad", 0)            not a sane box structure
    """
    pos, n = 0, len(buf)
    while pos + 8 <= n:
        size = int.from_bytes(buf[pos:pos + 4], "big")
        typ = bytes(buf[pos + 4:pos + 8])
        hdr = 8
        if size == 1:
            if pos + 16 > n:
                return ("need_more", 0)
            size = int.from_bytes(buf[pos + 8:pos + 16], "big")
            hdr = 16
        elif size == 0:          # box runs to end of file
            if total is None:
                return ("after_mdat", 0) if typ == b"mdat" else ("bad", 0)
            size = total - pos
        if size < hdr:
            return ("bad", 0)
        if typ == b"moov":
            box_end = pos + size
            return ("ok", box_end) if box_end <= n else ("partial", box_end)
        if typ == b"mdat":
            return ("after_mdat", 0)
        pos += size
    return ("need_more", 0)


def _moov_settled(buf, total) -> bool:
    """Stop reading the prefix once more bytes cannot change the verdict."""
    return scan_moov(buf, total)[0] in ("ok", "after_mdat", "bad")


async def _fetch_prefix(url: str) -> tuple[bytes, Optional[int], int]:
    """
    Follow redirects by hand with per-hop allowlist + resolve-once + pin, then
    fetch a bounded prefix that contains the whole moov box.

    Returns (bytes_for_ffprobe, total_file_size_or_None, body_bytes_read).
    """
    import httpx
    budget = _Budget(MAX_TOTAL_BYTES)
    current = url
    last_exc: Optional[BaseException] = None
    for _ in range(MAX_REDIRECTS + 1):
        p = urlparse(current)
        if p.scheme != "https":
            raise ProbeError("redirect_scheme_not_allowed")
        host = (p.hostname or "").strip().rstrip(".").lower()
        if not host_allowed(host):
            raise ProbeError("redirect_host_not_allowed")
        try:
            port = p.port or 443
        except ValueError:
            raise ProbeError("invalid_url")
        ips = await _resolve_pinned(host, port)
        # Fresh client per hop: a pooled TLS connection verified for one name
        # must never be reused for another name that happens to share an IP.
        async with _new_client() as client:
            r, ip = None, None
            for cand in ips[:MAX_CONNECT_TRIES]:
                try:
                    r = await _range_get(client, current, host, port, cand,
                                         0, PREFIX_BYTES - 1, budget, stop=_moov_settled)
                    ip = cand
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                    # Another address from the SAME validated answer; no re-resolve.
                    last_exc = e
            if r is None:
                raise ProbeError("connect_failed") from last_exc
            if r[0] == "redirect":
                current = urljoin(current, r[1])
                continue
            _, data, total = r
            verdict, moov_end = scan_moov(data, total)
            if verdict == "partial":
                if moov_end > MAX_TOTAL_BYTES or (total is not None and moov_end > total):
                    raise ProbeError("moov_not_in_prefix")
                r2 = await _range_get(client, current, host, port, ip,
                                      len(data), moov_end - 1, budget)
                if r2[0] != "data":
                    raise ProbeError("moov_not_in_prefix")
                data += r2[1]
                verdict, moov_end = scan_moov(data, total)
            if verdict == "bad":
                raise ProbeError("not_mp4")
            if verdict != "ok":
                raise ProbeError("moov_not_in_prefix")
            return data, total, budget.read
    raise ProbeError("too_many_redirects")


# ── ffprobe (stdin only) ──────────────────────────────────────────────

def _ffprobe_args() -> list[str]:
    return [
        "ffprobe", "-v", "error",
        "-protocol_whitelist", "pipe",
        "-f", "mp4",
        "-print_format", "json", "-show_streams", "-show_format",
        "-i", "pipe:0",
    ]


async def _run_ffprobe(data: bytes) -> bytes:
    try:
        proc = await asyncio.create_subprocess_exec(
            *_ffprobe_args(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        raise ProbeError("ffprobe_unavailable")
    try:
        out, _err = await asyncio.wait_for(proc.communicate(input=data), timeout=PROC_TIMEOUT_S)
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
            data, total, nread = await asyncio.wait_for(_fetch_prefix(url), timeout=FETCH_TIMEOUT_S)
            result = parse_ffprobe_json(await _run_ffprobe(data), size_bytes=total)
    except ProbeError as e:
        return {"url": url, "error": str(e)}
    except asyncio.TimeoutError:
        return {"url": url, "error": "timeout"}
    except Exception as e:
        print(f"[FormatProbe] unexpected error: {type(e).__name__}: {e}")
        return {"url": url, "error": "probe_failed"}
    print(f"[FormatProbe] {urlparse(url).hostname} {result['width']}x{result['height']} "
          f"{result['vcodec']} {nread} B in {time.monotonic() - t0:.2f}s")
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
