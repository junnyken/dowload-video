"""
Which Windows app version is "latest" — read from the GitHub Release itself.

Until 09/10/2026 every release meant editing DESKTOP_LATEST_VERSION and
DESKTOP_DOWNLOAD_URL on the hosting panel and redeploying; owner: "nếu có
0.11.0 thì tôi phải vào đó thay đổi nữa hả" (task #6172). Now publishing a
GitHub Release (not draft, not pre-release, marked latest) is the whole
release: /client/version, the 426 update gate, the /download page and the
admin page all follow it within CACHE_TTL_S.

Rules:
  * The env values stay as a FLOOR: GitHub wins only when its version is
    strictly newer than DESKTOP_LATEST_VERSION. GitHub unreachable / rate
    limited / malformed → the env values, exactly as before.
  * DESKTOP_RELEASE_SOURCE=env turns GitHub off (old behaviour).
  * Only an https asset under github.com/<repo>/releases/download/ ending in
    "_x64-setup.exe" is accepted as the download URL.
  * Never raises; one GitHub call per CACHE_TTL_S (Redis, else in-process),
    failures cached for FAIL_TTL_S so an outage is not hammered.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_REPO = "junnyken/dowload-video"
CACHE_KEY = "vidgrab:desktop_release:github"
CACHE_TTL_S = 600
FAIL_TTL_S = 120
NOTES_MAX = 500

_VERSION = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
_mem: Dict[str, Any] = {"at": 0.0, "ttl": 0, "val": None}


def parse_version(raw: Optional[str]) -> Optional[Tuple[int, int, int]]:
    m = _VERSION.match((raw or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def _repo() -> str:
    r = (os.environ.get("DESKTOP_RELEASE_REPO") or DEFAULT_REPO).strip()
    return r if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", r) else DEFAULT_REPO


def _notes_from_body(body: str) -> str:
    """The release's bullet lines ("- …"), joined — the first paragraph
    otherwise. Checksums and install hints stay on the GitHub page."""
    lines = [ln.strip() for ln in (body or "").splitlines()]
    bullets = [ln[2:].strip() for ln in lines if ln.startswith(("- ", "* "))]
    text = " ".join(b.rstrip(".") + "." for b in bullets if b) if bullets else next(
        (ln for ln in lines if ln and not ln.lower().startswith(("sha", "#"))), "")
    return text[:NOTES_MAX]


def parse_release(data: Any, repo: str) -> Optional[Dict[str, str]]:
    """GitHub /releases/latest JSON → {version, downloadUrl, notes} or None."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    ver = parse_version(str(data.get("tag_name") or ""))
    if not ver:
        return None
    version = ".".join(map(str, ver))
    prefix = f"https://github.com/{repo}/releases/download/"
    url = ""
    for a in data.get("assets") or []:
        u = (a or {}).get("browser_download_url") or ""
        if isinstance(u, str) and u.startswith(prefix) and u.endswith(f"_{version}_x64-setup.exe"):
            url = u
            break
    if not url:
        return None          # a release without the installer is not a release yet
    return {"version": version, "downloadUrl": url, "notes": _notes_from_body(str(data.get("body") or ""))}


def _cache_get() -> Tuple[bool, Optional[Dict[str, str]]]:
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        raw = get_redis().get(CACHE_KEY)
        if raw is not None:
            val = json.loads(raw if isinstance(raw, str) else raw.decode())
            return True, (val or None)
    except Exception:  # noqa: BLE001
        pass
    if time.time() - _mem["at"] < _mem["ttl"]:
        return True, _mem["val"]
    return False, None


def _cache_put(val: Optional[Dict[str, str]], ttl: int) -> None:
    _mem.update(at=time.time(), ttl=ttl, val=val)
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        get_redis().setex(CACHE_KEY, ttl, json.dumps(val or {}))
    except Exception:  # noqa: BLE001
        pass


def github_latest(fetch=None) -> Optional[Dict[str, str]]:
    """Cached; None when GitHub has nothing usable or cannot be read."""
    hit, val = _cache_get()
    if hit:
        return val
    repo = _repo()
    try:
        if fetch is None:
            import httpx  # noqa: PLC0415
            r = httpx.get(f"https://api.github.com/repos/{repo}/releases/latest",
                          headers={"Accept": "application/vnd.github+json",
                                   "User-Agent": "vidgrab-backend"}, timeout=4.0)
            data = r.json() if r.status_code == 200 else None
        else:
            data = fetch(repo)
        val = parse_release(data, repo)
    except Exception as exc:  # noqa: BLE001
        logger.info("desktop_release: GitHub unreadable: %s", type(exc).__name__)
        val = None
    _cache_put(val, CACHE_TTL_S if val else FAIL_TTL_S)
    return val


def current(fetch=None) -> Dict[str, str]:
    """{latest, downloadUrl, notes, source} — env floor, GitHub when newer."""
    env = os.environ.get
    out = {
        "latest": (env("DESKTOP_LATEST_VERSION") or "0.1.0").strip(),
        "downloadUrl": (env("DESKTOP_DOWNLOAD_URL") or "").strip(),
        "notes": env("DESKTOP_RELEASE_NOTES") or "",
        "source": "env",
    }
    if (env("DESKTOP_RELEASE_SOURCE") or "github").strip().lower() == "env":
        return out
    gh = github_latest(fetch)
    if gh and parse_version(gh["version"]) > (parse_version(out["latest"]) or (0, 0, 0)):
        out.update(latest=gh["version"], downloadUrl=gh["downloadUrl"],
                   notes=gh["notes"] or out["notes"], source="github")
    return out
