"""
Admin overrides for emergency switches (task #6332 follow-up).

Owner rule: an operational switch must be operable from the admin pages. An
env change on Vibe Host needs a redeploy, which is useless during an incident.

These env-only switches get a Redis-backed override here:

  watch_kill_switch   env WATCH_KILL_SWITCH         on = Channel Watch stopped
  desktop_updater     env DESKTOP_UPDATER_ENABLED   on = in-app updates offered
  overload_guard      env OVERLOAD_GUARD_ENABLED    on = busy server answers 503 fast

Effective value = admin override when one is set, else the env value (current
behaviour is unchanged when nobody touched the admin page). "clear" removes
the override so the env value applies again.

The read path is cheap and never raises: one HGET, cached in-process for
CACHE_TTL_SEC (a failed read is cached too, so a dead Redis costs one timeout
per window, not one per call). Any Redis error → the env value.

Storage:
  admin:switch:override   hash  key -> "on" | "off"
  admin:switch:meta       hash  key -> JSON {"at", "by", "action", "old", "new"}
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

OVERRIDE_KEY = "admin:switch:override"
META_KEY = "admin:switch:meta"
CACHE_TTL_SEC = 3.0

WATCH_KILL = "watch_kill_switch"
DESKTOP_UPDATER = "desktop_updater"
OVERLOAD_GUARD = "overload_guard"   # task #6397, env OVERLOAD_GUARD_ENABLED
OVERRIDABLE = frozenset({WATCH_KILL, DESKTOP_UPDATER, OVERLOAD_GUARD})

_MISS = object()
_cache: dict[str, tuple[float, Optional[bool]]] = {}
_lock = threading.Lock()


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _read_override(key: str) -> Optional[bool]:
    raw = _r().hget(OVERRIDE_KEY, key)
    if raw == "on":
        return True
    if raw == "off":
        return False
    return None


def override(key: str) -> Optional[bool]:
    """True/False when an admin set the switch, None when not set or unreadable.
    Never raises."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key, _MISS)
    if hit is not _MISS and hit[0] > now:  # type: ignore[index]
        return hit[1]  # type: ignore[index]
    try:
        val = _read_override(key)
    except Exception as exc:  # noqa: BLE001 — Redis down → env value
        logger.warning("admin switch %s unreadable, using env: %s", key, type(exc).__name__)
        val = None
    with _lock:
        _cache[key] = (now + CACHE_TTL_SEC, val)
    return val


def override_strict(key: str) -> Optional[bool]:
    """Uncached read for the admin page; raises on Redis failure."""
    return _read_override(key)


def resolve(key: str, env_value: bool) -> bool:
    ov = override(key)
    return env_value if ov is None else ov


def set_override(key: str, state: str, by: str, old: Optional[bool], new: Optional[bool]) -> dict:
    """state: "on" | "off" | "clear". Raises on Redis failure."""
    if key not in OVERRIDABLE:
        raise KeyError(key)
    r = _r()
    if state == "clear":
        r.hdel(OVERRIDE_KEY, key)
    elif state in ("on", "off"):
        r.hset(OVERRIDE_KEY, key, state)
    else:
        raise ValueError(state)
    meta = write_meta(key, state, by, old, new)
    reset_cache()
    return meta


def write_meta(key: str, action: str, by: str, old, new) -> dict:
    meta = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "by": by, "action": action, "old": old, "new": new}
    _r().hset(META_KEY, key, json.dumps(meta))
    return meta


def read_meta(key: str) -> Optional[dict]:
    try:
        raw = _r().hget(META_KEY, key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None
