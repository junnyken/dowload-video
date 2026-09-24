"""
Active platform probes — "does this platform still work at all?"
================================================================
lane_observer already answers a different question: is our capacity under
pressure (cookies blocked, circuit open, throttle near limit). It derives that
from traffic, which means a platform nobody is currently using looks healthy
because it is silent — indistinguishable from how it looks the moment it breaks.
Of 21 platforms on the public list, the live API reported 5.

This module asks the platform directly, on a schedule, whether extraction still
works. Metadata only: yt-dlp with download=False, no bytes, no Cobalt, no Apify,
no proxy spend. It will not catch "metadata resolves but the media 404s", and
says so rather than implying full coverage — real user failures cover that side,
via the fetch_failed / download_failed events.

WHY MOST TARGETS SHIP EMPTY
---------------------------
A probe needs a sample URL per platform that is public and stable. Inventing
twenty of them would produce a monitor that reports false failures on day one,
and a monitor people learn to ignore is worse than no monitor. So only targets
that are genuinely known-stable ship as defaults; everything else reports
`not_configured`, which is neither healthy nor failed, and the operator fills
them in through the admin without a deploy.

Configured targets live in Redis under `probe:targets` as a JSON object
{platform: url}, overriding the defaults below.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from app.core.redis_client import get_redis

# Mirrors the user-facing list in frontend PlatformsPage.
PROBE_PLATFORMS = [
    "youtube", "tiktok", "douyin", "facebook", "instagram", "twitter",
    "threads", "reddit", "pinterest", "bilibili", "xiaohongshu", "lemon8",
    "snapchat", "vk", "twitch", "rumble", "odysee", "dailymotion",
    "soundcloud", "spotify", "podcast",
]

# Only URLs whose stability is not a guess. Everything else is left to the
# operator on purpose — see the module docstring.
_DEFAULT_TARGETS: Dict[str, str] = {
    # The first video ever uploaded to YouTube, April 2005, still public.
    "youtube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    # Verified by running the real probe against it on 2026-09-24, not assumed.
    "vk": "https://vk.com/video-22822305_456241864",
}

_TARGETS_KEY = "probe:targets"
_STATE_KEY = "probe:state"          # hash: platform -> json
_PROBE_TIMEOUT_SEC = 25
_STALE_AFTER_SEC = 3 * 3600         # a result older than this is not evidence

# Result statuses. `not_configured` is deliberately distinct from both ok and
# failed: "we never looked" must never render as "we looked and it was fine".
OK = "ok"
FAILED = "failed"
NOT_CONFIGURED = "not_configured"
STALE = "stale"


def get_targets() -> Dict[str, str]:
    """Configured targets, falling back to the built-in defaults."""
    targets = dict(_DEFAULT_TARGETS)
    try:
        raw = get_redis().get(_TARGETS_KEY)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode()
            stored = json.loads(raw)
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if isinstance(v, str) and v.strip():
                        targets[k] = v.strip()
                    elif k in targets:
                        # Explicit empty clears a default rather than keeping it.
                        targets.pop(k, None)
    except Exception:
        pass
    return targets


def set_target(platform: str, url: Optional[str]) -> Dict[str, str]:
    """Set or clear one platform's probe URL. Returns the full target map."""
    if platform not in PROBE_PLATFORMS:
        raise ValueError(f"Unknown platform: {platform}")
    rc = get_redis()
    try:
        raw = rc.get(_TARGETS_KEY)
        if isinstance(raw, bytes):
            raw = raw.decode()
        stored = json.loads(raw) if raw else {}
        if not isinstance(stored, dict):
            stored = {}
    except Exception:
        stored = {}
    stored[platform] = (url or "").strip()
    rc.set(_TARGETS_KEY, json.dumps(stored))
    return get_targets()


def probe_once(url: str, timeout: int = _PROBE_TIMEOUT_SEC) -> Dict[str, Any]:
    """
    Resolve metadata for one URL. No download, no paid provider.

    Runs yt-dlp as a SUBPROCESS rather than importing it, purely to get a hard
    wall-clock bound. The in-process version passed `socket_timeout`, which caps
    each socket read and not the call: a podcast feed probe measured at 295
    seconds against a 15-second setting, because the extractor kept making fresh
    requests that each stayed under the per-read limit. One platform hanging
    like that stalls the whole sweep, which is the opposite of what a monitor is
    for. subprocess.run(timeout=) kills the process outright.

    Returns {ok, reason, ms, title}. Never raises: a probe that throws reports
    nothing, and a monitor that goes quiet on failure is the thing this module
    exists to replace.
    """
    started = time.time()

    def _done(ok: bool, reason: Optional[str], title: Optional[str] = None) -> Dict[str, Any]:
        return {
            "ok": ok,
            # Truncated: extractor errors can embed a whole HTML page, and the
            # reason is stored in Redis and rendered in the admin.
            "reason": reason[:200] if reason else None,
            "ms": int((time.time() - started) * 1000),
            "title": title,
        }

    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "yt_dlp",
                "-J",                      # metadata as JSON
                "--skip-download",
                "--no-warnings",
                "--no-playlist",
                "--socket-timeout", str(max(5, timeout // 2)),
                url,
            ],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _done(False, f"timeout_after_{timeout}s")
    except Exception as exc:
        return _done(False, f"{type(exc).__name__}: {exc}")

    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        last = err.splitlines()[-1] if err else f"exit_{proc.returncode}"
        return _done(False, last)

    raw = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    if not raw:
        return _done(False, "no_metadata_returned")
    try:
        info = json.loads(raw.splitlines()[0])
    except Exception:
        return _done(False, "unparseable_metadata")
    if not isinstance(info, dict) or not info:
        return _done(False, "no_metadata_returned")
    return _done(True, None, (info.get("title") or "")[:120] or None)


def record_probe(platform: str, result: Dict[str, Any]) -> None:
    payload = {
        "status": OK if result.get("ok") else FAILED,
        "reason": result.get("reason"),
        "ms": result.get("ms"),
        "title": result.get("title"),
        "at": int(time.time()),
    }
    try:
        get_redis().hset(_STATE_KEY, platform, json.dumps(payload))
    except Exception:
        pass


def get_probe_states() -> Dict[str, Dict[str, Any]]:
    """
    Current probe state for every platform on the list.

    A platform with no target reports not_configured; one whose last result is
    older than _STALE_AFTER_SEC reports stale. Neither is reported as healthy —
    an old success is not evidence about now.
    """
    targets = get_targets()
    stored: Dict[str, Any] = {}
    try:
        raw = get_redis().hgetall(_STATE_KEY) or {}
        for k, v in raw.items():
            key = k.decode() if isinstance(k, bytes) else k
            val = v.decode() if isinstance(v, bytes) else v
            try:
                stored[key] = json.loads(val)
            except Exception:
                pass
    except Exception:
        pass

    now = int(time.time())
    out: Dict[str, Dict[str, Any]] = {}
    for platform in PROBE_PLATFORMS:
        target = targets.get(platform)
        if not target:
            out[platform] = {"status": NOT_CONFIGURED, "target": None,
                             "reason": None, "at": None, "ms": None, "age_s": None}
            continue
        rec = stored.get(platform)
        if not rec or not rec.get("at"):
            out[platform] = {"status": STALE, "target": target, "reason": "never_probed",
                             "at": None, "ms": None, "age_s": None}
            continue
        age = now - int(rec["at"])
        status = rec.get("status")
        if age > _STALE_AFTER_SEC:
            status = STALE
        out[platform] = {
            "status": status,
            "target": target,
            "reason": rec.get("reason") if status != STALE else f"last_result_{age}s_old",
            "at": rec.get("at"),
            "ms": rec.get("ms"),
            "age_s": age,
        }
    return out
