"""
Daily Redis SET NX EX idempotency locks for periodic Celery Beat tasks.

Prevents duplicate execution when Celery Beat ticks twice (e.g. container
overlap during redeploy, clock skew, or manual + scheduled trigger) on tasks
that mutate billing/quota state, run full DB backups, or send daily/weekly
Telegram digests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

DAILY_LOCK_TTL_SEC = 90_000  # 25 hours — covers full UTC day plus clock skew

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def _get_redis():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def daily_lock_key(task_name: str, now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return f"vidgrab:daily_lock:{task_name}:{dt.strftime('%Y-%m-%d')}"


def acquire_daily_lock(
    task_name: str,
    *,
    ttl_sec: int = DAILY_LOCK_TTL_SEC,
    now: Optional[datetime] = None,
) -> Tuple[bool, str, Optional[str]]:
    """Atomically claim today's execution slot via `SET key token NX EX ttl_sec`.

    Returns `(acquired, key, token)`. Fails closed (`acquired=False`) if Redis
    is unreachable so duplicate Beat processes cannot double-run non-idempotent
    tasks during a transient Redis blip.
    """
    key = daily_lock_key(task_name, now=now)
    token = uuid.uuid4().hex
    try:
        rc = _get_redis()
        ok = rc.set(key, token, nx=True, ex=int(ttl_sec))
        if ok:
            return True, key, token
        return False, key, None
    except Exception as exc:  # noqa: BLE001
        print(f"[PeriodicLock] Could not acquire lock {key} ({type(exc).__name__}); skipping.")
        return False, key, None


def release_daily_lock(key: Optional[str], token: Optional[str]) -> bool:
    """Release `key` only if still owned by `token` (used when a task fails before completing)."""
    if not key or not token:
        return False
    try:
        rc = _get_redis()
        try:
            return bool(rc.eval(_RELEASE_LUA, 1, key, token))
        except Exception:
            cur = rc.get(key)
            if isinstance(cur, bytes):
                cur = cur.decode("utf-8", errors="replace")
            if cur == token:
                return bool(rc.delete(key))
            return False
    except Exception as exc:  # noqa: BLE001
        print(f"[PeriodicLock] Could not release lock {key} ({type(exc).__name__}).")
        return False
