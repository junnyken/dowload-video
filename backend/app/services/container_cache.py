"""
Container Cache — Phase 25 PR2
================================
Centralized Redis key design + helpers for the container discovery framework.

Key namespace: `container:*`   (distinct from legacy `vidgrab:container:*` in
container_discovery.py — both coexist; this module handles PR2 job snapshots)

Key patterns:
  container:job:{job_id}                       — DiscoveryJobSnapshot (30 min)
  container:data:{container_id}                — finalized ContainerMeta JSON (varies)
  container:summary:{platform}:{url_hash}      — lightweight summary (30 min)
  container:sections:{container_id}            — section list only (2× container TTL)
  container:expand:{container_id}:{ref}:{cur}  — expand result (30 min)
  container:lock:{url_hash}                    — NX lock for dedup (5 min)
  container:url_job:{url_hash}                 — url → active job_id (30 min)

All TTLs have conservative defaults; callers can override.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from app.core.redis_client import get_redis
from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus

logger = logging.getLogger(__name__)


# ── TTL constants ─────────────────────────────────────────────────────────────

JOB_TTL          = 1800    # 30 min — discovery jobs
JOB_META_TTL     = 7200    # 2 h    — job→container mapping (outlives snapshot for recovery)
LOCK_TTL         = 300     # 5 min  — in-flight lock
URL_JOB_TTL      = 1800    # 30 min — url → job_id mapping
EXPAND_TTL       = 1800    # 30 min — expand results
SUMMARY_TTL      = 1800    # 30 min — container summaries
QUEUE_DEDUP_TTL  = 60      # 60 s   — queue request idempotency window


# ── Key builders ─────────────────────────────────────────────────────────────

def url_hash(url: str) -> str:
    """Stable 24-char hash of a canonical URL — used in Redis keys."""
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def job_key(job_id: str) -> str:
    return f"container:job:{job_id}"


def data_key(container_id: str) -> str:
    return f"container:data:{container_id}"


def summary_key(platform: str, url: str) -> str:
    return f"container:summary:{platform}:{url_hash(url)}"


def sections_key(container_id: str) -> str:
    return f"container:sections:{container_id}"


def expand_key(container_id: str, ref_id: str, cursor: str = "") -> str:
    safe_ref = ref_id.replace(":", "_").replace("/", "_")[:32]
    safe_cur = cursor[:16] if cursor else "0"
    return f"container:expand:{container_id}:{safe_ref}:{safe_cur}"


def lock_key(url: str) -> str:
    return f"container:lock:{url_hash(url)}"


def url_job_key(url: str) -> str:
    return f"container:url_job:{url_hash(url)}"


def job_meta_key(job_id: str) -> str:
    """Long-lived key mapping job_id → container_id + url (outlives job snapshot for recovery)."""
    return f"container:job_meta:{job_id}"


def queue_dedup_key(req_hash: str) -> str:
    return f"container:queue_dedup:{req_hash}"


# ── Discovery job CRUD ────────────────────────────────────────────────────────

def save_job(job: DiscoveryJobSnapshot, ttl: int = JOB_TTL) -> None:
    """Persist a DiscoveryJobSnapshot to Redis."""
    try:
        rc = get_redis()
        rc.setex(job_key(job.job_id), ttl, job.model_dump_json())
    except Exception:
        pass


def get_job(job_id: str) -> Optional[DiscoveryJobSnapshot]:
    """Load a DiscoveryJobSnapshot from Redis. Returns None if expired/missing."""
    try:
        rc = get_redis()
        raw = rc.get(job_key(job_id))
        if not raw:
            return None
        return DiscoveryJobSnapshot.model_validate_json(raw)
    except Exception:
        return None


def patch_job(job_id: str, ttl: int = JOB_TTL, **fields) -> Optional[DiscoveryJobSnapshot]:
    """
    Load job, apply field patches, save back.
    Always updates `updated_at`.  Returns updated snapshot or None.
    """
    job = get_job(job_id)
    if job is None:
        return None
    fields["updated_at"] = time.time()
    updated = job.model_copy(update=fields)
    save_job(updated, ttl=ttl)
    return updated


def mark_job_stage(
    job_id: str,
    stage: str,
    progress_pct: int,
    message: str = "",
    status: Optional[str] = None,
) -> None:
    """Convenience: update stage/progress/message atomically."""
    kwargs: dict = {"stage": stage, "progress_pct": progress_pct}
    if message:
        kwargs["message"] = message
    if status:
        kwargs["status"] = status
    patch_job(job_id, **kwargs)


def expire_job(job_id: str) -> None:
    """Mark a job as expired (snapshot stays for 5 min for graceful frontend handling)."""
    patch_job(job_id, ttl=300, status=DiscoveryJobStatus.expired,
               message="Job đã hết hạn. Vui lòng thử lại.")


# ── In-flight lock (prevents duplicate discovery) ─────────────────────────────

def acquire_discovery_lock(url: str, job_id: str, ttl: int = LOCK_TTL) -> bool:
    """
    Try to acquire a NX lock for a canonical URL.
    Returns True if lock was acquired (caller should proceed).
    Returns False if another job is already discovering this URL.
    """
    try:
        rc = get_redis()
        return bool(rc.set(lock_key(url), job_id, nx=True, ex=ttl))
    except Exception:
        return True  # Fail open — let discovery proceed if Redis is unavailable


def release_discovery_lock(url: str) -> None:
    """Release the in-flight lock for a URL."""
    try:
        rc = get_redis()
        rc.delete(lock_key(url))
    except Exception:
        pass


def get_lock_holder(url: str) -> Optional[str]:
    """Return the job_id currently holding the lock for this URL, or None."""
    try:
        rc = get_redis()
        val = rc.get(lock_key(url))
        return val.decode() if isinstance(val, bytes) else val
    except Exception:
        return None


# ── URL → active job mapping ──────────────────────────────────────────────────

def set_active_job_for_url(url: str, job_id: str, ttl: int = URL_JOB_TTL) -> None:
    """Record which job_id is the latest for a canonical URL."""
    try:
        rc = get_redis()
        rc.setex(url_job_key(url), ttl, job_id)
    except Exception:
        pass


def get_active_job_for_url(url: str) -> Optional[str]:
    """Return the latest job_id for a canonical URL, or None."""
    try:
        rc = get_redis()
        val = rc.get(url_job_key(url))
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val
    except Exception:
        return None


# ── Expand result cache ───────────────────────────────────────────────────────

def cache_expand_result(
    container_id: str,
    ref_id: str,
    cursor: str,
    items: list,
    ttl: int = EXPAND_TTL,
) -> None:
    """Cache a list of ContainerItem dicts from an expand call."""
    try:
        rc = get_redis()
        rc.setex(expand_key(container_id, ref_id, cursor), ttl, json.dumps(items))
    except Exception:
        pass


def get_expand_result(
    container_id: str,
    ref_id: str,
    cursor: str = "",
) -> Optional[list]:
    """Return cached expand items or None."""
    try:
        rc = get_redis()
        raw = rc.get(expand_key(container_id, ref_id, cursor))
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ── Lightweight summary cache ─────────────────────────────────────────────────

def cache_summary(platform: str, url: str, summary_dict: dict, ttl: int = SUMMARY_TTL) -> None:
    """Cache a lightweight container summary dict (for fast /resolve-input enrichment)."""
    try:
        rc = get_redis()
        rc.setex(summary_key(platform, url), ttl, json.dumps(summary_dict))
    except Exception:
        pass


def get_summary(platform: str, url: str) -> Optional[dict]:
    """Return cached summary dict or None."""
    try:
        rc = get_redis()
        raw = rc.get(summary_key(platform, url))
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ── Job metadata (long-lived, for snapshot recovery) ─────────────────────────

def save_job_meta(job_id: str, container_id: str, url: str) -> None:
    """
    Persist job→container mapping with 2-hour TTL.
    Outlives the 30-min job snapshot so GET /discover-container/{job_id}
    can recover data even after snapshot expires.
    """
    try:
        rc = get_redis()
        rc.setex(
            job_meta_key(job_id),
            JOB_META_TTL,
            json.dumps({"container_id": container_id, "url": url}),
        )
    except Exception:
        pass


def get_job_meta(job_id: str) -> Optional[dict]:
    """Return {container_id, url} even after job snapshot expires. None if not found."""
    try:
        rc = get_redis()
        raw = rc.get(job_meta_key(job_id))
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ── Stale lock cleanup ────────────────────────────────────────────────────────

def cleanup_stale_lock(url: str) -> bool:
    """
    Release the discovery lock for a URL if the holder job is no longer active.
    Returns True if a stale lock was cleaned up.
    Safe to call from both API and Celery — no side effects if lock is valid.
    """
    try:
        rc = get_redis()
        holder = get_lock_holder(url)
        if not holder:
            return False
        job = get_job(holder)
        if job is None or job.status in (
            DiscoveryJobStatus.success,
            DiscoveryJobStatus.partial,
            DiscoveryJobStatus.failed,
            DiscoveryJobStatus.expired,
        ):
            rc.delete(lock_key(url))
            logger.info("container_cache stale_lock_cleaned url_hash=%s holder=%s", url_hash(url)[:12], holder)
            return True
        return False
    except Exception:
        return False


# ── Cache invalidation (full container + PR2 keys) ────────────────────────────

def invalidate_all_for_container(url: str, platform: str, container_id: str) -> None:
    """
    Invalidate all PR2 Redis keys associated with a container.
    Used by the refresh flow. Does NOT delete active lock/job keys.
    """
    try:
        rc = get_redis()
        keys_to_delete = [
            url_job_key(url),
            summary_key(platform, url),
            sections_key(container_id),
        ]
        # Pattern-delete expand cache entries
        expand_pattern = f"container:expand:{container_id}:*"
        expand_keys = rc.keys(expand_pattern)
        keys_to_delete.extend(
            k.decode() if isinstance(k, bytes) else k for k in expand_keys
        )
        if keys_to_delete:
            rc.delete(*keys_to_delete)
            logger.debug("container_cache invalidated %d keys for cid=%s", len(keys_to_delete), container_id[:12])
    except Exception:
        pass


# ── Expand key with filter/sort entropy ───────────────────────────────────────

def expand_key_v2(
    container_id: str,
    ref_id: str,
    cursor: str = "",
    filter_type: str = "",
    sort_by: str = "",
) -> str:
    """
    Expand cache key with full entropy to prevent cross-filter cache pollution.
    Backward-compatible: same result as expand_key() when filter/sort are empty.
    """
    safe_ref    = ref_id.replace(":", "_").replace("/", "_")[:32]
    safe_cur    = cursor[:16] if cursor else "0"
    safe_filter = filter_type[:8] if filter_type else ""
    safe_sort   = sort_by[:12] if sort_by else ""
    base = f"container:expand:{container_id}:{safe_ref}:{safe_cur}"
    suffix = f"{safe_filter}:{safe_sort}" if (safe_filter or safe_sort) else ""
    return f"{base}:{suffix}" if suffix else base


# ── Queue request idempotency ─────────────────────────────────────────────────

def check_queue_dedup(req_hash: str) -> Optional[str]:
    """Return existing batch_id if an identical queue request was made within QUEUE_DEDUP_TTL."""
    try:
        rc = get_redis()
        val = rc.get(queue_dedup_key(req_hash))
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val
    except Exception:
        return None


def set_queue_dedup(req_hash: str, batch_id: str, ttl: int = QUEUE_DEDUP_TTL) -> None:
    """Record a queue request hash → batch_id mapping for idempotency."""
    try:
        rc = get_redis()
        rc.setex(queue_dedup_key(req_hash), ttl, batch_id)
    except Exception:
        pass


# ── Batch source metadata (no DB schema change needed) ───────────────────────

def save_batch_source_meta(
    batch_id: str,
    container_id: str,
    platform: str,
    item_ids: list[str],
    section_ref: str = "",
) -> None:
    """Store source provenance for a batch — enables audit without DB schema changes."""
    try:
        rc = get_redis()
        data = {
            "source_container_id": container_id,
            "source_platform": platform,
            "source_section_ref": section_ref,
            "source_item_ids": item_ids[:200],  # cap to avoid huge values
            "item_count": len(item_ids),
        }
        rc.setex(f"container:batch_meta:{batch_id}", 7200, json.dumps(data))
    except Exception:
        pass


def get_batch_source_meta(batch_id: str) -> Optional[dict]:
    """Return source provenance for a batch, or None."""
    try:
        rc = get_redis()
        raw = rc.get(f"container:batch_meta:{batch_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None
