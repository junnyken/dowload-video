#!/usr/bin/env python3
"""
Light load test for VidGrab — cheap read-only endpoints only (task #6397).

Hits GET /health, /api/v1/platform-status and /api/v1/client/version with a
small number of concurrent workers for a short time, then prints p50/p95
latency and error counts per endpoint. It never calls /fetch-link or anything
that downloads, extracts or writes.

    python scripts/loadtest_light.py --base http://127.0.0.1:8000
    python scripts/loadtest_light.py --base http://127.0.0.1:8000 -c 10 -d 20

Safety:
  * concurrency default 5, hard max 20;
  * duration default 10 s, hard max 60 s;
  * any host that is not local (localhost, 127.x, ::1, *.local, *.localhost)
    is treated as PRODUCTION: the script refuses to run unless
    --i-know-this-is-production is given, and then caps concurrency at 10.
    There is no staging — production is one container with 1.6 CPU.

Needs only the standard library and httpx.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import math
import sys
import time
from collections import Counter, defaultdict
from typing import Iterable, Optional
from urllib.parse import urlparse

DEFAULT_PATHS = ("/health", "/api/v1/platform-status", "/api/v1/client/version")
DEFAULT_CONCURRENCY = 5
MAX_CONCURRENCY = 20
PROD_MAX_CONCURRENCY = 10
DEFAULT_DURATION = 10.0
MAX_DURATION = 60.0
REQUEST_TIMEOUT = 15.0
ERROR_BACKOFF = 0.2


class Refused(Exception):
    """The run is not allowed (production without the flag, bad URL)."""


# ── safety ──────────────────────────────────────────────────────────────────

def is_local_host(host: Optional[str]) -> bool:
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return False
    if h in ("localhost", "testserver") or h.endswith(".localhost") or h.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return ip.is_loopback


def resolve_settings(base: str, concurrency: int, duration: float, i_know_prod: bool) -> dict:
    """Validate the target and clamp the load. Raises Refused."""
    u = urlparse(base)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise Refused(f"--base must be an http(s) URL, got {base!r}")
    prod = not is_local_host(u.hostname)
    if prod and not i_know_prod:
        raise Refused(
            f"{u.hostname} is not a local host, so it is treated as PRODUCTION. "
            "Re-run with --i-know-this-is-production if you really mean it "
            f"(concurrency is then capped at {PROD_MAX_CONCURRENCY}).")
    c = max(1, min(int(concurrency), MAX_CONCURRENCY))
    if prod:
        c = min(c, PROD_MAX_CONCURRENCY)
    d = max(1.0, min(float(duration), MAX_DURATION))
    return {"base": base.rstrip("/"), "production": prod, "concurrency": c, "duration": d}


# ── stats ───────────────────────────────────────────────────────────────────

def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None for no values."""
    xs = sorted(values)
    if not xs:
        return None
    rank = max(1, math.ceil(pct / 100.0 * len(xs)))
    return xs[min(rank, len(xs)) - 1]


class Stats:
    def __init__(self) -> None:
        self.latency_ms: dict[str, list[float]] = defaultdict(list)
        self.status: dict[str, Counter] = defaultdict(Counter)

    def add(self, path: str, status: Optional[int], ms: float) -> None:
        """status None = transport error (timeout, refused connection)."""
        self.latency_ms[path].append(ms)
        self.status[path]["error" if status is None else str(status)] += 1

    def summary(self) -> dict:
        out = {}
        for path in sorted(self.status):
            st = self.status[path]
            total = sum(st.values())
            ok = sum(n for k, n in st.items() if k.isdigit() and 200 <= int(k) < 400)
            lat = self.latency_ms[path]
            out[path] = {"requests": total, "ok": ok, "errors": total - ok,
                         "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95),
                         "status": dict(st)}
        return out

    def render(self, elapsed: float) -> str:
        lines = [f"{'endpoint':32} {'req':>6} {'err':>5} {'p50 ms':>8} {'p95 ms':>8}  status"]
        total = errors = 0
        for path, s in self.summary().items():
            total += s["requests"]
            errors += s["errors"]
            p50 = "-" if s["p50_ms"] is None else f"{s['p50_ms']:.0f}"
            p95 = "-" if s["p95_ms"] is None else f"{s['p95_ms']:.0f}"
            codes = " ".join(f"{k}:{v}" for k, v in sorted(s["status"].items()))
            lines.append(f"{path:32} {s['requests']:>6} {s['errors']:>5} {p50:>8} {p95:>8}  {codes}")
        rps = total / elapsed if elapsed > 0 else 0.0
        lines.append(f"total {total} requests, {errors} errors, {rps:.1f} req/s over {elapsed:.1f}s")
        return "\n".join(lines)


# ── runner ──────────────────────────────────────────────────────────────────

async def run(base: str, concurrency: int, duration: float, paths=DEFAULT_PATHS,
              transport=None, stats: Optional[Stats] = None) -> Stats:
    import httpx  # noqa: PLC0415

    stats = stats or Stats()
    deadline = time.monotonic() + duration
    headers = {"User-Agent": "vidgrab-loadtest-light/1"}

    async with httpx.AsyncClient(base_url=base, timeout=REQUEST_TIMEOUT, transport=transport,
                                 headers=headers, follow_redirects=False) as client:
        async def worker(i: int) -> None:
            n = i
            while time.monotonic() < deadline:
                path = paths[n % len(paths)]
                n += 1
                t0 = time.perf_counter()
                try:
                    r = await client.get(path)
                    code: Optional[int] = r.status_code
                except Exception:  # noqa: BLE001 — counted as a transport error
                    code = None
                stats.add(path, code, (time.perf_counter() - t0) * 1000.0)
                if code is None or code >= 500:
                    await asyncio.sleep(ERROR_BACKOFF)   # do not hammer a struggling server

        await asyncio.gather(*(worker(i) for i in range(concurrency)))
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000", help="API base URL")
    ap.add_argument("-c", "--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help=f"parallel workers (default {DEFAULT_CONCURRENCY}, max {MAX_CONCURRENCY})")
    ap.add_argument("-d", "--duration", type=float, default=DEFAULT_DURATION,
                    help=f"seconds (default {DEFAULT_DURATION:.0f}, max {MAX_DURATION:.0f})")
    ap.add_argument("--i-know-this-is-production", action="store_true",
                    help=f"allow a non-local host; concurrency capped at {PROD_MAX_CONCURRENCY}")
    a = ap.parse_args(argv)
    try:
        s = resolve_settings(a.base, a.concurrency, a.duration, a.i_know_this_is_production)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    if s["concurrency"] != a.concurrency:
        print(f"note: concurrency capped at {s['concurrency']}", file=sys.stderr)
    print(f"target {s['base']} ({'PRODUCTION' if s['production'] else 'local'}), "
          f"{s['concurrency']} workers, {s['duration']:.0f}s, paths: {', '.join(DEFAULT_PATHS)}")
    t0 = time.monotonic()
    stats = asyncio.run(run(s["base"], s["concurrency"], s["duration"]))
    print(stats.render(time.monotonic() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
