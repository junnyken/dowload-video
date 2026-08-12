"""
Phase 27 — Fair Queue & Admission Control
==========================================
Per-user concurrency caps and global queue depth governor.

Tiers:
    anonymous / free  → max 1 concurrent download
    pro               → max 3
    enterprise        → max 10 (configurable)
    admin             → unlimited

Global backpressure:
    When the Celery queue depth (vidgrab:queue:depth) exceeds P27_QUEUE_CAP,
    new BATCH-priority requests are rejected with HTTP 429 and a Retry-After.
    INTERACTIVE requests always pass (so the UI stays responsive).

Redis keys:
    p27:uq:{user_id}          INCR — active download count per user (TTL = 30min)
    p27:gq:depth              INCR — global in-flight count (TTL = 10min)
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

# Per-tier concurrency caps
_CAP: dict[str, int] = {
    "anonymous":  int(os.getenv("P27_CAP_ANON",        "1")),
    "free":       int(os.getenv("P27_CAP_FREE",        "1")),
    "pro":        int(os.getenv("P27_CAP_PRO",         "3")),
    "enterprise": int(os.getenv("P27_CAP_ENTERPRISE", "10")),
    "admin":      999_999,
}
_DEFAULT_CAP = int(os.getenv("P27_CAP_DEFAULT", "2"))

# Global queue governor
_QUEUE_CAP     = int(os.getenv("P27_QUEUE_CAP",     "120"))  # max in-flight globally
_QUEUE_CAP_TTL = 10 * 60   # safety auto-expire (10 min)
_USER_TTL      = 30 * 60   # per-user counter TTL

# Priority that is admission-controlled under backpressure
_BATCH_PRIORITY = 3   # matches queue_intelligence.Priority.BATCH


class AdmissionDenied(Exception):
    """Raised when a user or the global queue is at capacity."""
    def __init__(self, reason: str, retry_after: int = 30):
        self.reason = reason
        self.retry_after = retry_after
        super().__init__(f"admission_denied:{reason}")


def _get_cap(tier: str) -> int:
    return _CAP.get(tier, _DEFAULT_CAP)


def try_admit(user_id: str, tier: str = "free", priority: int = 1) -> bool:
    """
    Attempt to admit a new download request. Returns True on success.
    Raises AdmissionDenied on failure.

    Call release_slot() in a finally block to free the slot.
    """
    rc = get_redis()

    # Global backpressure — only throttles batch, never interactive
    if priority >= _BATCH_PRIORITY:
        try:
            depth = int(rc.get("p27:gq:depth") or 0)
            if depth >= _QUEUE_CAP:
                raise AdmissionDenied("global_queue_full", retry_after=60)
        except AdmissionDenied:
            raise
        except Exception:
            pass  # Redis error → fail-open

    # Per-user cap
    if user_id:
        ukey = f"p27:uq:{user_id}"
        try:
            n = rc.incr(ukey)
            rc.expire(ukey, _USER_TTL)
            cap = _get_cap(tier)
            if n > cap:
                rc.decr(ukey)
                raise AdmissionDenied(f"user_concurrent_limit:{cap}", retry_after=15)
        except AdmissionDenied:
            raise
        except Exception:
            pass  # Redis error → fail-open

    # Increment global counter
    try:
        gkey = "p27:gq:depth"
        rc.incr(gkey)
        rc.expire(gkey, _QUEUE_CAP_TTL)
    except Exception:
        pass

    return True


def release_slot(user_id: str) -> None:
    """Release one admission slot for the user and decrement global counter."""
    try:
        rc = get_redis()
        if user_id:
            ukey = f"p27:uq:{user_id}"
            if rc.decr(ukey) < 0:
                rc.set(ukey, 0)
        gkey = "p27:gq:depth"
        if rc.decr(gkey) < 0:
            rc.set(gkey, 0)
    except Exception:
        pass


def get_user_active(user_id: str) -> int:
    """Return current active download count for a user."""
    try:
        rc = get_redis()
        return int(rc.get(f"p27:uq:{user_id}") or 0)
    except Exception:
        return 0


def get_queue_depth() -> int:
    """Return current global in-flight count."""
    try:
        rc = get_redis()
        return int(rc.get("p27:gq:depth") or 0)
    except Exception:
        return 0


def get_fairness_snapshot() -> dict:
    """Admin observability: global depth + top active users."""
    try:
        rc = get_redis()
        depth = int(rc.get("p27:gq:depth") or 0)
        # Scan for top user keys
        keys = rc.keys("p27:uq:*") or []
        users = []
        for k in keys[:50]:  # cap scan
            uid = k.split(b"p27:uq:", 1)[-1].decode() if isinstance(k, bytes) else k.split("p27:uq:", 1)[-1]
            cnt = int(rc.get(k) or 0)
            if cnt > 0:
                users.append({"user_id": uid, "active": cnt})
        users.sort(key=lambda x: x["active"], reverse=True)
        return {
            "global_depth":  depth,
            "global_cap":    _QUEUE_CAP,
            "utilization":   round(depth / _QUEUE_CAP * 100) if _QUEUE_CAP else 0,
            "top_users":     users[:20],
        }
    except Exception:
        return {"global_depth": 0, "global_cap": _QUEUE_CAP, "utilization": 0, "top_users": []}
