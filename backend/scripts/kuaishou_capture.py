#!/usr/bin/env python3
"""
Kuaishou capture-and-verify tool
================================

Run this from a machine with a network path into mainland China (or with a
Chinese proxy) to find out whether the UNVERIFIED Kuaishou extractor works,
and to produce a REDACTED page capture that can become a real test fixture.

    cd backend
    KUAISHOU_PROXY_CN=http://user:pass@host:port \\
        python scripts/kuaishou_capture.py https://www.kuaishou.com/short-video/<id>

Options:
    --out FILE     where to write the redacted capture
                   (default: ./kuaishou_capture_<kind>_<timestamp>.html)
    --no-save      do not write a capture file
    --use-pool     also try the admin cookie pool (needs REDIS_URL); off by default

It uses the extractor's OWN network layer (same timeouts, redirect checks,
SSRF rules, proxy handling), runs EVERY parsing strategy, and prints PASS/FAIL
per step. It never prints the proxy URL, cookie values, or full signed media
URLs — only hostnames. The flag KUAISHOU_ENABLED is NOT needed for this tool.

Exit code: 0 when a strategy produced a post whose media URL passed
validation, 1 otherwise, 2 on bad usage.

Only use public videos you are allowed to access. Review the capture file
before committing it anywhere; redaction is pattern-based and best-effort.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import kuaishou_extractor as ks  # noqa: E402


# ── Redaction ─────────────────────────────────────────────────────────────

_SENSITIVE_KEYS = (
    "did", "webDid", "deviceId", "device_id", "cookie", "cookies", "token", "accessToken",
    "access_token", "refreshToken", "sign", "sig", "signature", "ticket", "session",
    "sessionId", "userId", "user_id", "userEid", "eid", "uid", "principalId", "authorId",
    "openId", "kwaiId", "userName", "user_name", "name", "headUrl", "headUrls", "avatar",
    "userSex", "phone", "mobile", "email", "ip", "clientIp", "client_ip", "serverIp",
    "share_info", "shareInfo", "shareToken", "expTag", "llsid", "webPageArea", "id",
)
_KEY_VALUE_RE = re.compile(
    r'("(?:' + "|".join(re.escape(k) for k in _SENSITIVE_KEYS) + r')"\s*:\s*)'
    r'("(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|\[[^\[\]]{0,4000}\]|\{[^{}]{0,4000}\})',
    re.IGNORECASE,
)
# Every query-string value, also when the separator is JSON-escaped (&).
_QUERY_VALUE_RE = re.compile(r'((?:\?|&|\\u0026|&amp;)[A-Za-z0-9_.\-]{1,64}=)[^&"\'\s\\<>]+')
_AUTHOR_KEY_RE = re.compile(r'(VisionVideoDetailAuthor:)[^"\\]+')
_LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def redact_capture(html: str) -> str:
    s = html or ""
    s = _KEY_VALUE_RE.sub(lambda m: m.group(1) + '"REDACTED"', s)
    s = _QUERY_VALUE_RE.sub(r"\1REDACTED", s)
    s = _AUTHOR_KEY_RE.sub(r"\1REDACTED", s)
    s = _LONG_HEX_RE.sub("REDACTEDHEX", s)
    return ks.redact(s)


# ── Report helpers ────────────────────────────────────────────────────────

_rows: list[tuple[str, str, str]] = []


def step(name: str, status: str, info: str = "") -> None:
    _rows.append((status, name, info))
    print(f"[{status:<4}] {name}{(': ' + info) if info else ''}", flush=True)


def host_of(u: str) -> str:
    try:
        return (urlparse(u).hostname or "").lower()
    except ValueError:
        return "?"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Capture and verify a public Kuaishou page.")
    ap.add_argument("url")
    ap.add_argument("--out")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--use-pool", action="store_true")
    args = ap.parse_args(argv)

    print("Kuaishou capture-and-verify — extractor status: UNVERIFIED scaffold\n")

    link = ks.parse_kuaishou_url(args.url)
    if link is None:
        step("URL recognised", "FAIL", "not a supported Kuaishou URL form")
        return 2
    step("URL recognised", "PASS", f"kind={link.kind} code={link.code}")

    proxy = ks._proxy_url()
    if proxy:
        step("Proxy configured", "PASS", f"{ks.PROXY_ENV} set (scheme {urlparse(proxy).scheme or '?'})")
    else:
        step("Proxy configured", "WARN", f"{ks.PROXY_ENV} not set — direct connection "
                                         "(expected to time out outside mainland China)")
    step("Feature flag", "INFO", f"{ks.FLAG_ENV}={'on' if ks.kuaishou_enabled() else 'off'} "
                                 "(not needed by this tool)")

    if not args.use_pool:
        ks._pool_cookies = lambda: {}

    rep = ks.ExtractionReport()
    err = None
    t0 = time.monotonic()
    try:
        ks.extract_post(link.url, report=rep)
    except ks.KuaishouError as e:
        err = e
    elapsed = time.monotonic() - t0

    f = rep.fetch
    if f is None:
        step("Fetch page", "FAIL", f"{err.error_code if err else '?'} after {elapsed:.1f}s"
                                   f"{(' — ' + err.detail) if err and err.detail else ''}")
        return _finish(False)
    step("Fetch page", "PASS",
         f"HTTP {f.status}, {f.bytes_read} bytes, hops={' -> '.join(f.hop_hosts)}, "
         f"proxy={'yes' if f.via_proxy else 'no'}, {elapsed:.1f}s")
    if f.set_cookies:
        step("Site-set cookies (names only)", "INFO", ", ".join(sorted(f.set_cookies)))
    if rep.retried_with_site_cookies:
        step("Retried once with site-set cookies", "INFO")

    if not args.no_save:
        out = args.out or f"kuaishou_capture_{link.kind}_{time.strftime('%Y%m%d_%H%M%S')}.html"
        header = (
            "<!-- REDACTED Kuaishou capture made by scripts/kuaishou_capture.py\n"
            f"     captured_at: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n"
            f"     input_kind: {link.kind}\n"
            f"     final_host: {host_of(f.final_url)}\n"
            f"     final_path: {urlparse(f.final_url).path}\n"
            "     Review before committing: redaction is best-effort. -->\n"
        )
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(header + redact_capture(f.text))
        step("Saved redacted capture", "PASS", out)

    photo_id = link.photo_id or ks.photo_id_from_url(f.final_url)
    for o in rep.outcomes:
        if o.ok:
            p = o.post
            n = len(p.video_urls) if p.post_type == "video" else len(p.image_urls)
            step(f"Strategy {o.name}", "PASS", f"{p.post_type}, {n} URL(s)")
        else:
            step(f"Strategy {o.name}", "FAIL", o.reason)
    step("Photo id used for matching", "INFO", photo_id or "(none)")

    post = rep.post
    if post is None:
        step("Extraction", "FAIL", f"{err.error_code}: {err.detail}" if err else "no post")
        return _finish(False)

    step("Chosen strategy", "PASS", post.strategy)
    step("Title", "PASS" if post.title else "WARN", post.title[:80] or "(empty)")
    step("Duration", "INFO", f"{post.duration_ms} ms")
    urls = post.video_urls if post.post_type == "video" else post.image_urls
    step("Media URL validation (https + public IP)", "PASS",
         f"{len(urls)} URL(s); host(s): {', '.join(sorted({host_of(u) for u in urls}))}")
    step("Thumbnail", "PASS" if post.thumbnail else "WARN",
         host_of(post.thumbnail) if post.thumbnail else "(none / rejected)")
    return _finish(True)


def _finish(ok: bool) -> int:
    print("\nSummary:", "PASS — a strategy produced validated media" if ok else "FAIL")
    print("Do NOT enable KUAISHOU_ENABLED on production from one PASS; see docs/KUAISHOU.md.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
