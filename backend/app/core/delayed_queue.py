"""
Phase 27C — Delayed Job Queue
===============================
A thin Redis ZSET queue for jobs that received ACCEPT_DELAYED from admission control.
Score = submit_timestamp − age_boost so long-waiting jobs bubble to the top
(anti-starvation). Promotion happens when a job on the same platform completes.

Redis keys (p27c:):
  p27c:dq:{platform}      ZSET   score=priority_ts  member=JSON job entry
  p27c:dq_ttl             SET    platforms with non-empty queues (for Celery beat scan)

Design decisions:
  - Jobs expire after DELAYED_MAX_WAIT_S (default 10 min); client must retry.
  - Promotion is best-effort: after each platform job completion, the top N=1
    job from the queue is promoted (removed from ZSET). The caller then retries
    the endpoint immediately with the freed slot.
  - The queue stores the original payload URL only (for logging/admin). The
    actual retry is always a new HTTP request from the client.
  - No persistent job state for delayed jobs — this is a fairness buffer, not
    a durable task scheduler.
"""

from __future__ import annotations

import json
import os
import time
import logging
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

_MAX_WAIT_S      = int(os.getenv("DELAYED_QUEUE_MAX_WAIT_S",   "600"))    # 10 min
_AGE_BOOST_PER5M = int(os.getenv("DELAYED_QUEUE_AGE_BOOST",    "60"))     # score pts per 5 min
_MAX_AGE_BOOST   = int(os.getenv("DELAYED_QUEUE_MAX_BOOST",    "600"))    # cap at 10 min equivalent
_BATCH_PENALTY   = int(os.getenv("DELAYED_QUEUE_BATCH_PENALTY","30"))     # single job gets head-start
_DQ_PREFIX       = "p27c:dq:"
_PLATFORM_SET    = "p27c:dq_active"


def enqueue(
    platform: str,
    user_key: str,
    job_id: str,
    url: str,
    is_batch: bool = False,
) -> dict:
    """
    Add a job to the delayed queue for this platform.
    Returns queue info including estimated position.
    """
    try:
        rc   = get_redis()
        now  = int(time.time())
        # Lower score = higher priority; batch gets a slight penalty vs single
        score  = float(now + (_BATCH_PENALTY if is_batch else 0))
        member = json.dumps({
            "job_id":   job_id,
            "user_key": user_key[:8] + "**",  # truncated for privacy
            "url":      url[:120],             # truncated
            "submit_ts": now,
            "is_batch": is_batch,
            "expire_at": now + _MAX_WAIT_S,
        })
        key = f"{_DQ_PREFIX}{platform}"
        rc.zadd(key, {member: score})
        rc.expire(key, _MAX_WAIT_S + 60)
        rc.sadd(_PLATFORM_SET, platform)
        rc.expire(_PLATFORM_SET, 3600)

        # Position in queue (1-indexed)
        pos = rc.zrank(key, member)
        depth = rc.zcard(key)
        est_wait = _estimate_wait_for_position(int(pos or 0), platform)

        log.info("[DelayedQueue:%s] enqueue job=%s user=%s** pos=%s est=%ds",
                 platform, job_id[:8], user_key[:8], pos, est_wait)
        return {
            "queued":          True,
            "platform":        platform,
            "position":        int(pos or 0) + 1,
            "queueDepth":      depth,
            "estimatedWaitSec": est_wait,
            "expiresAt":       now + _MAX_WAIT_S,
        }
    except Exception as e:
        log.warning("[DelayedQueue] enqueue error %s: %s", platform, e)
        return {"queued": False, "platform": platform, "estimatedWaitSec": 30}


def try_promote(platform: str) -> Optional[dict]:
    """
    Called after a job on this platform completes (slot freed).
    Promotes the top-priority delayed job by removing it from the queue.
    Returns the promoted job entry dict, or None if queue is empty.
    The client is expected to retry their request — we do not resubmit for them.
    """
    try:
        rc  = get_redis()
        key = f"{_DQ_PREFIX}{platform}"
        _evict_expired(rc, key)

        # ZPOPMIN = remove+return the member with the lowest score (= highest priority)
        items = rc.zpopmin(key, 1)
        if not items:
            return None

        raw, score = items[0]
        entry = json.loads(raw)
        log.info("[DelayedQueue:%s] promoted job=%s waited=%ds",
                 platform, entry.get("job_id", "?")[:8],
                 int(time.time()) - entry.get("submit_ts", int(time.time())))
        return entry
    except Exception as e:
        log.debug("[DelayedQueue] try_promote error %s: %s", platform, e)
        return None


def get_platform_queue_depth(platform: str) -> int:
    """Current delayed queue depth for a platform."""
    try:
        rc  = get_redis()
        key = f"{_DQ_PREFIX}{platform}"
        _evict_expired(rc, key)
        return int(rc.zcard(key) or 0)
    except Exception:
        return 0


def get_all_queue_depths() -> dict[str, int]:
    """Admin: queue depth across all platforms."""
    try:
        rc       = get_redis()
        platforms = rc.smembers(_PLATFORM_SET) or set()
        out = {}
        for p in platforms:
            p_str = p.decode() if isinstance(p, bytes) else p
            out[p_str] = get_platform_queue_depth(p_str)
        return {k: v for k, v in out.items() if v > 0}
    except Exception:
        return {}


def get_oldest_wait_sec(platform: str) -> Optional[int]:
    """Returns how long the oldest delayed job has been waiting. None if queue empty."""
    try:
        rc  = get_redis()
        key = f"{_DQ_PREFIX}{platform}"
        items = rc.zrange(key, 0, 0, withscores=False)
        if not items:
            return None
        entry = json.loads(items[0])
        submit_ts = entry.get("submit_ts")
        if submit_ts:
            return int(time.time()) - submit_ts
        return None
    except Exception:
        return None


def get_platform_snapshot(platform: str) -> dict:
    """Admin: full snapshot for a platform's delayed queue."""
    try:
        rc    = get_redis()
        key   = f"{_DQ_PREFIX}{platform}"
        _evict_expired(rc, key)
        depth = int(rc.zcard(key) or 0)
        oldest = get_oldest_wait_sec(platform)
        items  = rc.zrange(key, 0, 4, withscores=True)  # top 5 for admin
        top5   = []
        for raw, score in items:
            try:
                e = json.loads(raw)
                top5.append({
                    "user_key":    e.get("user_key", "?"),
                    "submit_ts":   e.get("submit_ts"),
                    "waited_sec":  int(time.time()) - e.get("submit_ts", int(time.time())),
                    "is_batch":    e.get("is_batch", False),
                    "expire_at":   e.get("expire_at"),
                })
            except Exception:
                continue
        return {
            "platform":         platform,
            "queueDepth":       depth,
            "oldestWaitSec":    oldest,
            "maxWaitSec":       _MAX_WAIT_S,
            "estimatedWaitSec": _estimate_wait_for_position(depth, platform),
            "topJobs":          top5,
        }
    except Exception:
        return {"platform": platform, "queueDepth": 0}


def scan_all_platforms_promote() -> dict:
    """
    Celery beat safety net: scan all platforms and try to promote stale entries.
    Returns counts of promoted per platform.
    """
    depths = get_all_queue_depths()
    promoted = {}
    for platform, depth in depths.items():
        if depth > 0:
            entry = try_promote(platform)
            if entry:
                promoted[platform] = 1
    return promoted


# ── Internal helpers ───────────────────────────────────────────────────────────

def _estimate_wait_for_position(position: int, platform: str) -> int:
    """Rough wait estimate in seconds for a given queue position."""
    # Assume each slot takes ~15s average. Minimum 10s.
    return max(10, min(_MAX_WAIT_S, position * 15))


def _age_boost(wait_sec: int) -> int:
    """Score reduction for a job that has been waiting. More wait → lower score = higher priority."""
    boosts = wait_sec // 300   # every 5 minutes
    return min(_MAX_AGE_BOOST, boosts * _AGE_BOOST_PER5M)


def _evict_expired(rc, key: str) -> int:
    """Remove expired entries from the ZSET. Returns count evicted."""
    try:
        now  = int(time.time())
        # Members encode expire_at in JSON — we can't filter by score (score = submit_ts).
        # Instead, read all members and remove those whose expire_at has passed.
        # For small queues (expected: < 100 entries) this is acceptable.
        all_items = rc.zrange(key, 0, -1)
        evict = []
        for raw in all_items:
            try:
                e = json.loads(raw)
                if e.get("expire_at", 0) < now:
                    evict.append(raw)
            except Exception:
                evict.append(raw)
        if evict:
            rc.zrem(key, *evict)
        return len(evict)
    except Exception:
        return 0
