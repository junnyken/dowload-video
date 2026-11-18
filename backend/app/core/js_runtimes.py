"""
Which external JS runtimes yt-dlp can use in this container.

PhantomJS: yt-dlp 2026.08.19's iq.com (iQIYI) extractor runs its signature
code through PhantomJSwrapper (yt_dlp/extractor/openload.py), which looks for
an executable named `phantomjs` on PATH (check_executable('phantomjs', ['-v']))
and raises "PhantomJS not found" otherwise. `--js-runtimes` / Deno do not
apply to it (yt-dlp issue #17570, 2026-08-30). The Dockerfile installs the
official 2.1.1 build behind a wrapper; this module reports whether it is
there, once, so a missing runtime shows up in the startup log and /health
instead of as a failed iq.com download.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional

_cache: Optional[dict] = None
_deno_cache: Optional[dict] = None


def phantomjs_status(refresh: bool = False) -> dict:
    global _cache
    if _cache is not None and not refresh:
        return _cache
    path = shutil.which("phantomjs")
    version = None
    if path:
        try:
            out = subprocess.run([path, "-v"], capture_output=True, text=True, timeout=15)
            if out.returncode == 0:
                version = (out.stdout or "").strip().splitlines()[0] if out.stdout.strip() else None
        except Exception:  # noqa: BLE001
            version = None
    _cache = {"found": bool(path and version), "path": path, "version": version}
    return _cache


def deno_status(refresh: bool = False) -> dict:
    """Phase C7: Check Deno JS runtime availability for yt-dlp EJS / PO-token solver."""
    global _deno_cache
    if _deno_cache is not None and not refresh:
        return _deno_cache
    path = shutil.which("deno")
    version = None
    if path:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and (out.stdout or "").strip():
                first = (out.stdout or "").strip().splitlines()[0].strip()
                parts = first.split()
                version = parts[1] if len(parts) >= 2 and parts[0].lower() == "deno" else first
        except Exception:  # noqa: BLE001
            version = None
    _deno_cache = {"found": bool(path and version), "path": path, "version": version}
    return _deno_cache


def all_js_runtimes_status(refresh: bool = False) -> dict:
    """Phase C7: Aggregated JS runtime health (Deno + PhantomJS) for Admin Health / Ops."""
    deno = deno_status(refresh=refresh)
    pjs = phantomjs_status(refresh=refresh)
    warnings: list[str] = []
    if not deno["found"]:
        warnings.append("Deno NOT found — YouTube EJS challenge / PO-token solver unavailable")
    if not pjs["found"]:
        warnings.append("PhantomJS NOT found — iq.com (iQIYI) downloads will fail")
    return {
        "deno": deno,
        "phantomjs": pjs,
        "healthy": bool(deno["found"] and pjs["found"]),
        "iqiyi_ready": bool(pjs["found"]),
        "youtube_ejs_ready": bool(deno["found"]),
        "warnings": warnings,
    }


def startup_line() -> str:
    s = phantomjs_status()
    if s["found"]:
        return f"[Startup] PhantomJS {s['version']} found at {s['path']} (yt-dlp iq.com / iQIYI)"
    if s["path"]:
        return f"[Startup] PhantomJS at {s['path']} did not run — iq.com (iQIYI) downloads will fail"
    return "[Startup] PhantomJS NOT found — iq.com (iQIYI) downloads will fail with 'PhantomJS not found'"
