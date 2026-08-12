"""
Job Lease & Heartbeat — VidGrab Stability Phase
=================================================
Provides Redis-backed per-job heartbeat and lease primitives so that:

  1. Active workers prove liveness every HEARTBEAT_INTERVAL_SEC seconds.
  2. The stale job scanner can distinguish "actively running" from "actually dead"
     before re-queuing, avoiding false-positive duplicate executions.
  3. A simple lease (NX lock with worker_id) prevents two workers from processing
     the same job simultaneously after a recovery re-queue.

Redis key design (all in vidgrab: namespace)
─────────────────────────────────────────────
  vidgrab:job_hb:{job_id}       — ISO timestamp; TTL = HEARTBEAT_TTL_SEC
                                   Written every HEARTBEAT_INTERVAL_SEC by the
                                   worker thread.  Absent ⟹ worker is gone.

  vidgrab:job_lease:{job_id}    — worker_id string; TTL = LEASE_TTL_SEC
                                   Set with NX at task start.  Absent after
                                   expiry ⟹ safe for another worker to take over.

Usage (in process_video_task)
──────────────────────────────
    lease = JobLease(job_id, worker_id=self.request.id)
    if not lease.acquire():
        return          # another worker owns this job
    try:
        lease.start_heartbeat()   # background thread
        ...do work...
    finally:
        lease.stop()              # stop heartbeat + release lease
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

HEARTBEAT_INTERVAL_SEC = int(os.getenv("JOB_HB_INTERVAL_SEC", "20"))
HEARTBEAT_TTL_SEC      = int(os.getenv("JOB_HB_TTL_SEC",      "90"))   # 4.5× interval = safe margin
LEASE_TTL_SEC          = int(os.getenv("JOB_LEASE_TTL_SEC",   "900"))  # 15 min > hard task limit (12 min)
# A heartbeat older than this means the worker has likely died.
STALE_THRESHOLD_SEC    = int(os.getenv("JOB_STALE_THRESHOLD_SEC", "90"))


def _redis():
    from app.core.redis_client import get_redis
    return get_redis()


def _hb_key(job_id: str) -> str:
    return f"vidgrab:job_hb:{job_id}"


def _lease_key(job_id: str) -> str:
    return f"vidgrab:job_lease:{job_id}"


# ── Heartbeat primitives ───────────────────────────────────────────────────────

def set_heartbeat(job_id: str, ttl: int = HEARTBEAT_TTL_SEC) -> None:
    """Write (or refresh) the heartbeat key for `job_id`. Never raises."""
    try:
        rc = _redis()
        rc.setex(_hb_key(job_id), ttl, datetime.now(timezone.utc).isoformat())
    except Exception:
        pass


def get_heartbeat(job_id: str) -> Optional[str]:
    """Return the raw heartbeat timestamp string, or None if missing/expired."""
    try:
        raw = _redis().get(_hb_key(job_id))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw
    except Exception:
        return None


def clear_heartbeat(job_id: str) -> None:
    """Delete the heartbeat key (called when a task completes or is abandoned)."""
    try:
        _redis().delete(_hb_key(job_id))
    except Exception:
        pass


def is_alive(job_id: str, threshold_seconds: int = STALE_THRESHOLD_SEC) -> bool:
    """
    Return True if the job has a valid, recent heartbeat.
    Used by the stale scanner to skip jobs that are still actively running.
    """
    raw = get_heartbeat(job_id)
    if not raw:
        return False   # key expired or never written → worker is gone
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age < threshold_seconds
    except Exception:
        return False


# ── Lease primitives ──────────────────────────────────────────────────────────

def acquire_lease(job_id: str, worker_id: str, ttl: int = LEASE_TTL_SEC) -> bool:
    """
    Try to acquire an exclusive lease for `job_id`.
    Returns True if acquired (or already owned by same worker).
    Returns False if owned by a different worker.

    Uses Redis SET … NX (set-if-not-exists) so only one worker wins per job.
    """
    try:
        rc = _redis()
        key = _lease_key(job_id)
        # NX: only set if absent.  Returns True on success, None on collision.
        result = rc.set(key, worker_id, ex=ttl, nx=True)
        if result:
            return True
        # Already set — check if we own it
        existing = rc.get(key)
        if existing is None:
            # Key expired between SET and GET — try once more
            result2 = rc.set(key, worker_id, ex=ttl, nx=True)
            return bool(result2)
        existing_val = existing.decode() if isinstance(existing, bytes) else existing
        return existing_val == worker_id
    except Exception:
        return True  # Fail open: if Redis is down, let the task proceed


def renew_lease(job_id: str, worker_id: str, ttl: int = LEASE_TTL_SEC) -> bool:
    """
    Extend the lease TTL if we still own it.
    Returns True if still owner (or Redis is down), False if ownership was lost.
    """
    try:
        rc = _redis()
        key = _lease_key(job_id)
        existing = rc.get(key)
        if existing is None:
            # Expired; re-acquire
            result = rc.set(key, worker_id, ex=ttl, nx=True)
            return bool(result)
        existing_val = existing.decode() if isinstance(existing, bytes) else existing
        if existing_val != worker_id:
            return False  # lost ownership
        rc.expire(key, ttl)
        return True
    except Exception:
        return True  # Fail open


def release_lease(job_id: str, worker_id: str) -> None:
    """
    Release the lease if we own it.  No-op if already released or taken by another worker.
    """
    try:
        rc = _redis()
        key = _lease_key(job_id)
        existing = rc.get(key)
        if existing is None:
            return
        existing_val = existing.decode() if isinstance(existing, bytes) else existing
        if existing_val == worker_id:
            rc.delete(key)
    except Exception:
        pass


def get_lease_holder(job_id: str) -> Optional[str]:
    """Return current lease holder worker_id, or None if no active lease."""
    try:
        raw = _redis().get(_lease_key(job_id))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw
    except Exception:
        return None


def invalidate_lease(job_id: str) -> None:
    """Force-delete a lease (used by recovery when taking over an abandoned job)."""
    try:
        _redis().delete(_lease_key(job_id))
    except Exception:
        pass


# ── High-level JobLease context manager ───────────────────────────────────────

class JobLease:
    """
    Convenience wrapper that manages lease + heartbeat for a single task execution.

    Usage::

        lease = JobLease(job_id, worker_id=str(self.request.id))
        if not lease.acquire():
            return   # duplicate execution guarded off
        try:
            lease.start_heartbeat()
            # ... do work ...
        finally:
            lease.stop()
    """

    def __init__(self, job_id: str, worker_id: str,
                 lease_ttl: int = LEASE_TTL_SEC,
                 hb_interval: int = HEARTBEAT_INTERVAL_SEC,
                 hb_ttl: int = HEARTBEAT_TTL_SEC):
        self.job_id     = job_id
        self.worker_id  = worker_id
        self.lease_ttl  = lease_ttl
        self.hb_interval = hb_interval
        self.hb_ttl     = hb_ttl
        self._stop_event = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        self._acquired  = False

    def acquire(self) -> bool:
        """Acquire the lease. Returns False if another worker holds it."""
        self._acquired = acquire_lease(self.job_id, self.worker_id, self.lease_ttl)
        return self._acquired

    def start_heartbeat(self) -> None:
        """Spawn a daemon thread that refreshes the heartbeat key on interval."""
        if self._hb_thread and self._hb_thread.is_alive():
            return
        self._stop_event.clear()
        # Write immediately so scanner sees liveness right away
        set_heartbeat(self.job_id, self.hb_ttl)

        def _loop():
            while not self._stop_event.wait(self.hb_interval):
                set_heartbeat(self.job_id, self.hb_ttl)
                renew_lease(self.job_id, self.worker_id, self.lease_ttl)

        self._hb_thread = threading.Thread(target=_loop, daemon=True,
                                           name=f"hb-{self.job_id[:8]}")
        self._hb_thread.start()

    def stop(self) -> None:
        """Stop heartbeat thread, clear heartbeat key, release lease."""
        self._stop_event.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=2)
        clear_heartbeat(self.job_id)
        if self._acquired:
            release_lease(self.job_id, self.worker_id)
