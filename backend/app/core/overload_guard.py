"""
Overload guard — answer "server busy" fast instead of queueing into a timeout
(task #6397, Phase 34A-6/7).

13/10/2026: under load /fetch-link waited ~60–120 s and then failed with 504
extraction_timeout. A request that is going to fail anyway should fail in
milliseconds with a clear "try again in N seconds", not after a minute.

What is counted (per uvicorn worker process, in memory — no Redis on the hot
path):

  inflight   heavy requests (GUARDED_PATHS, POST) currently inside this process
  threads    extraction threads alive in this process (_run_with_timeout).
             A timed-out extraction is NOT stopped — the request answers 504
             and releases its download slot, but the yt-dlp thread keeps
             running and keeps eating CPU. Those orphans are exactly what the
             global slot counter (MAX_CONCURRENT_DOWNLOADS) cannot see.
             Threads older than OVERLOAD_THREAD_STALE_SEC are not counted, so a
             thread hung forever on a dead socket cannot lock the API out.

busy = inflight >= limit  OR  threads >= limit

The limit is per process. Default = 2 × MAX_CONCURRENT_DOWNLOADS (2 × 10 = 20):
the global Redis slot already caps admitted /fetch-link work at
MAX_CONCURRENT_DOWNLOADS across both uvicorn workers, so normal traffic never
gets one process to that many heavy requests or live extraction threads; only a
pile-up (requests waiting, orphaned threads) does. Admin can lower/raise it on
Admin → Công tắc khẩn cấp without a redeploy.

Never blocked: OPTIONS, non-guarded paths (/health, /ping, /platform-status,
/client/version, admin API…), and a request carrying a live admin session in
X-Admin-Token (checked only when the request would otherwise be refused).

On/off: admin switch "overload_guard" (admin_switches override) over env
OVERLOAD_GUARD_ENABLED (default on). Off → the middleware still counts but
never refuses.

Storage (Redis, all optional — any error → defaults):
  admin:overload:limit          int, admin-set per-process limit
  overload:rejected:<YYYY-MM-DD> int, refusals that day (best effort, TTL 3 d)
"""
from __future__ import annotations

import itertools
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SWITCH_KEY = "overload_guard"
LIMIT_KEY = "admin:overload:limit"
REJECTED_KEY_FMT = "overload:rejected:{day}"
CACHE_TTL_SEC = 3.0
LIMIT_MIN, LIMIT_MAX = 2, 500

API = "/api/v1"
# Heavy endpoints that do extraction / ffmpeg work inside the request.
GUARDED_PATHS = frozenset({
    f"{API}/fetch-link",
    f"{API}/fetch-threads",
    f"{API}/fetch-spotify",
    f"{API}/fetch-spotify-artist-tracks",
    f"{API}/trim",
    f"{API}/to-gif",
    f"{API}/formats/probe",
})
GUARDED_PREFIXES = (f"{API}/process/",)

ERROR_CODE = "server_busy"


# ── settings ────────────────────────────────────────────────────────────────

def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def env_enabled() -> bool:
    raw = (os.getenv("OVERLOAD_GUARD_ENABLED") or "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "off")


def default_limit() -> int:
    """OVERLOAD_MAX_INFLIGHT if set, else 2 × MAX_CONCURRENT_DOWNLOADS."""
    explicit = _env_int("OVERLOAD_MAX_INFLIGHT", 0)
    if explicit > 0:
        return max(LIMIT_MIN, min(LIMIT_MAX, explicit))
    base = _env_int("MAX_CONCURRENT_DOWNLOADS", 10)
    return max(LIMIT_MIN, min(LIMIT_MAX, 2 * max(1, base)))


def retry_after_sec() -> int:
    return max(5, min(300, _env_int("OVERLOAD_RETRY_AFTER_SEC", 30)))


def stale_sec() -> int:
    return max(60, _env_int("OVERLOAD_THREAD_STALE_SEC", 900))


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


_lim_cache: list = [0.0, None]   # [expires_at, value]
_lim_lock = threading.Lock()


def reset_cache() -> None:
    with _lim_lock:
        _lim_cache[0], _lim_cache[1] = 0.0, None


def admin_limit_strict() -> Optional[int]:
    """Uncached read for the admin page; raises on Redis failure."""
    raw = _r().get(LIMIT_KEY)
    try:
        v = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        v = None
    return v if v is None else max(LIMIT_MIN, min(LIMIT_MAX, v))


def admin_limit() -> Optional[int]:
    """Admin-set limit or None. Cached CACHE_TTL_SEC; never raises."""
    now = time.monotonic()
    with _lim_lock:
        if _lim_cache[0] > now:
            return _lim_cache[1]
    try:
        v = admin_limit_strict()
    except Exception as exc:  # noqa: BLE001 — Redis down → default limit
        logger.warning("overload limit unreadable, using default: %s", type(exc).__name__)
        v = None
    with _lim_lock:
        _lim_cache[0], _lim_cache[1] = now + CACHE_TTL_SEC, v
    return v


def set_admin_limit(value: Optional[int]) -> None:
    """None clears the override. Raises on Redis failure / bad value."""
    if value is None:
        _r().delete(LIMIT_KEY)
    else:
        if not (LIMIT_MIN <= int(value) <= LIMIT_MAX):
            raise ValueError(value)
        _r().set(LIMIT_KEY, int(value))
    reset_cache()


def limit() -> int:
    v = admin_limit()
    return v if v is not None else default_limit()


def enabled() -> bool:
    from app.core import admin_switches as sw  # noqa: PLC0415
    return sw.resolve(SWITCH_KEY, env_enabled())


# ── counters ────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_inflight = 0
_threads: dict[int, float] = {}
_ids = itertools.count(1)
_rejected_local = 0


def inflight() -> int:
    with _lock:
        return _inflight


def live_threads() -> int:
    cutoff = time.monotonic() - stale_sec()
    with _lock:
        return sum(1 for t in _threads.values() if t >= cutoff)


@contextmanager
def extraction_thread():
    """Wrap the body of one extraction worker thread. Always unregisters."""
    with _lock:
        tid = next(_ids)
        _threads[tid] = time.monotonic()
    try:
        yield
    finally:
        with _lock:
            _threads.pop(tid, None)


def try_enter(lim: int, enforce: bool) -> bool:
    """Count one heavy request in. False (and nothing counted) when busy and
    enforce is on. The caller MUST call leave() after a True."""
    global _inflight
    cutoff = time.monotonic() - stale_sec()
    with _lock:
        if enforce:
            threads = sum(1 for t in _threads.values() if t >= cutoff)
            if _inflight >= lim or threads >= lim:
                return False
        _inflight += 1
        return True


def force_enter() -> None:
    global _inflight
    with _lock:
        _inflight += 1


def leave() -> None:
    global _inflight
    with _lock:
        _inflight = max(0, _inflight - 1)


def _reset_for_tests() -> None:
    global _inflight, _rejected_local
    with _lock:
        _inflight = 0
        _threads.clear()
        _rejected_local = 0
    reset_cache()


# ── refusals ────────────────────────────────────────────────────────────────

_redis_dead_until = [0.0]


def note_rejected() -> None:
    """Best effort; skips Redis for 30 s after a failure so a dead Redis does
    not add its socket timeout to every refusal."""
    global _rejected_local
    with _lock:
        _rejected_local += 1
    now = time.monotonic()
    if _redis_dead_until[0] > now:
        return
    try:
        key = REJECTED_KEY_FMT.format(day=datetime.now(timezone.utc).date().isoformat())
        r = _r()
        r.incr(key)
        r.expire(key, 3 * 86400)
    except Exception:  # noqa: BLE001
        _redis_dead_until[0] = now + 30


def rejected_today() -> Optional[int]:
    try:
        raw = _r().get(REJECTED_KEY_FMT.format(day=datetime.now(timezone.utc).date().isoformat()))
        return int(raw or 0)
    except Exception:  # noqa: BLE001
        return None


def is_guarded(method: str, path: str) -> bool:
    return method == "POST" and (path in GUARDED_PATHS or path.startswith(GUARDED_PREFIXES))


def busy_message(seconds: int) -> str:
    # wording: BA review
    return f"Hệ thống đang bận, vui lòng thử lại sau khoảng {seconds} giây."


def busy_body(seconds: int) -> dict:
    msg = busy_message(seconds)
    return {"error_code": ERROR_CODE, "message": msg, "detail": msg, "retry_after": seconds}


def status() -> dict:
    """For the admin page (this process only)."""
    return {"inflight": inflight(), "threads": live_threads(), "limit": limit(),
            "default_limit": default_limit(), "admin_limit": admin_limit(),
            "retry_after": retry_after_sec(), "rejected_today": rejected_today(),
            "rejected_this_process": _rejected_local}


# ── middleware ──────────────────────────────────────────────────────────────

from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402


def _is_admin(request: Request) -> bool:
    if not (request.headers.get("x-admin-token") or "").strip():
        return False
    try:
        from app.core.quotas import is_admin_request  # noqa: PLC0415
        return is_admin_request(request)
    except Exception:  # noqa: BLE001
        return False


class OverloadGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not is_guarded(request.method, request.url.path):
            return await call_next(request)
        try:
            enforce = enabled()
            lim = limit()
        except Exception:  # noqa: BLE001 — never let the guard itself fail a request
            enforce, lim = False, default_limit()
        if not try_enter(lim, enforce):
            if not _is_admin(request):
                note_rejected()
                secs = retry_after_sec()
                return JSONResponse(status_code=503, content=busy_body(secs),
                                    headers={"Retry-After": str(secs)})
            force_enter()
        try:
            return await call_next(request)
        finally:
            leave()
