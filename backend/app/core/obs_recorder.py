"""
Phase 27A — Observability Recorder
=====================================
Fire-and-forget Redis writers for the 27A instrumentation layer.
Called from downloader.py job lifecycle hooks.

IMPORTANT: This module ONLY writes counters and timestamps.
It does NOT change any scheduling, routing, or cookie-selection behavior.

Redis keys (p27a: namespace):
  p27a:fail:{platform}:{bucket}    STRING INCR TTL=3600  — failure reason counts (1h)
  p27a:allbusy:{platform}          ZSET score=ts         — all-cookies-busy events (1h)
  p27a:ts:{platform}:success       STRING ISO timestamp  — last success
  p27a:ts:{platform}:failure       STRING ISO timestamp  — last failure
  p27a:last_fail_reason:{platform} STRING bucket_name    — latest failure reason
"""

from __future__ import annotations

import time
import uuid
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

_BUCKET_TTL  = 3_600       # 1h rolling window for failure buckets
_ALLBUSY_TTL = 3_600
_TS_TTL      = 90 * 86400  # 90d — last success/failure timestamps


class FailureBucket(str, Enum):
    LOGIN_REQUIRED   = "login_required"
    SOFT_BLOCK       = "soft_block"
    HARD_BLOCK       = "hard_block"
    PROXY_MISSING    = "proxy_missing"
    PROXY_FAILED     = "proxy_failed"
    CIRCUIT_OPEN     = "circuit_open"
    ALL_COOKIES_BUSY = "all_cookies_busy"
    EXTRACTION_FAILED = "extraction_failed"
    TIMEOUT          = "timeout"
    UPSTREAM_DOWN    = "upstream_unavailable"
    PRIVATE_CONTENT  = "private_content"
    UNKNOWN          = "unknown"


def _classify_to_bucket(error_msg: str, context: Optional[dict] = None) -> str:
    """Map a raw error string → normalized FailureBucket.  Never raises."""
    if not error_msg:
        return FailureBucket.UNKNOWN

    ctx = context or {}
    msg = error_msg.lower()

    # Context-based overrides (highest priority)
    if ctx.get("circuit_was_open"):
        return FailureBucket.CIRCUIT_OPEN
    if ctx.get("all_cookies_busy"):
        return FailureBucket.ALL_COOKIES_BUSY
    if ctx.get("proxy_missing"):
        return FailureBucket.PROXY_MISSING

    # Failure classifier bridge
    try:
        from app.core.failure_classifier import classify_failure, FailureClass
        fc = classify_failure(error_msg)
        if fc == FailureClass.USER_ACTION:
            return FailureBucket.LOGIN_REQUIRED
        if fc == FailureClass.PERMANENT:
            return FailureBucket.PRIVATE_CONTENT
        if fc == FailureClass.TRANSIENT:
            return FailureBucket.TIMEOUT
    except Exception:
        pass

    # Fine-grained pattern matching for retryable cases
    if "proxy" in msg and ("auth" in msg or "407" in msg or "connect" in msg):
        return FailureBucket.PROXY_FAILED
    if "proxy" in msg and "none" in msg:
        return FailureBucket.PROXY_MISSING
    if "429" in msg or "rate limit" in msg or "too many" in msg:
        return FailureBucket.SOFT_BLOCK
    if "403" in msg or "ip ban" in msg or "ip has been blocked" in msg or "challenge" in msg:
        return FailureBucket.HARD_BLOCK
    if "no formats" in msg or "unable to extract" in msg or "unsupported url" in msg:
        return FailureBucket.EXTRACTION_FAILED
    if "502" in msg or "503" in msg or "service unavailable" in msg:
        return FailureBucket.UPSTREAM_DOWN

    return FailureBucket.UNKNOWN


def record_failure_reason(
    platform: str,
    error_msg: str,
    context: Optional[dict] = None,
) -> None:
    """
    Record one failure event.  Call from downloader failure path.
    context dict may contain: circuit_was_open, all_cookies_busy, proxy_missing
    """
    try:
        bucket = _classify_to_bucket(error_msg, context)
        rc = get_redis()
        ts = int(time.time())
        # Increment bucket counter (resets after 1h of inactivity)
        key = f"p27a:fail:{platform}:{bucket}"
        rc.incr(key)
        rc.expire(key, _BUCKET_TTL)
        # Store latest reason for constrained-reason fast lookup
        rc.setex(f"p27a:last_fail_reason:{platform}", _BUCKET_TTL, bucket)
        # Update failure timestamp
        now_iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        rc.setex(f"p27a:ts:{platform}:failure", _TS_TTL, now_iso)
    except Exception as e:
        log.debug(f"[obs_recorder] record_failure_reason error: {e}")


def record_success(platform: str) -> None:
    """Record that a job for this platform completed successfully."""
    try:
        rc = get_redis()
        now_iso = datetime.fromtimestamp(int(time.time()), tz=timezone.utc).isoformat()
        rc.setex(f"p27a:ts:{platform}:success", _TS_TTL, now_iso)
    except Exception as e:
        log.debug(f"[obs_recorder] record_success error: {e}")


def record_all_cookies_busy(platform: str) -> None:
    """
    Record an all-cookies-busy event.
    Called when get_cookie_from_pool returns a last-resort blocked cookie.
    """
    try:
        rc = get_redis()
        ts = time.time()
        key = f"p27a:allbusy:{platform}"
        member = uuid.uuid4().hex[:12]
        rc.zadd(key, {member: ts})
        rc.zremrangebyscore(key, 0, ts - _ALLBUSY_TTL)
        rc.expire(key, _ALLBUSY_TTL + 60)
    except Exception as e:
        log.debug(f"[obs_recorder] record_all_cookies_busy error: {e}")


def get_failure_buckets(platform: str) -> list[dict]:
    """Return 1h failure bucket counts for a platform (admin read)."""
    try:
        rc = get_redis()
        result = []
        for bucket in FailureBucket:
            key = f"p27a:fail:{platform}:{bucket.value}"
            count = int(rc.get(key) or 0)
            if count > 0:
                result.append({"bucket": bucket.value, "count": count})
        result.sort(key=lambda x: x["count"], reverse=True)
        return result
    except Exception:
        return []


def get_platform_failure_breakdown(platform: str, window_minutes: int = 15) -> dict:
    """
    Combine job success/error counts (from metrics, exact window) with
    soft/hard-block counts (from the rolling 1h failure buckets above — this
    recorder only keeps a 1h counter, not per-window history, so block counts
    are always over the last 1h regardless of `window_minutes`).
    """
    try:
        from app.core.metrics import get_job_metrics
        m = get_job_metrics(window_minutes=window_minutes)
        pdata = m.get("by_platform", {}).get(platform, {})
        success = pdata.get("succeeded", 0)
        error = pdata.get("failed", 0)
        buckets = {b["bucket"]: b["count"] for b in get_failure_buckets(platform)}
        return {
            "total":      success + error,
            "success":    success,
            "error":      error,
            "soft_block": buckets.get(FailureBucket.SOFT_BLOCK.value, 0),
            "hard_block": buckets.get(FailureBucket.HARD_BLOCK.value, 0),
        }
    except Exception:
        return {"total": 0, "success": 0, "error": 0, "soft_block": 0, "hard_block": 0}


def get_allbusy_count_1h(platform: str) -> int:
    """Return count of all-cookies-busy events in the last 1h."""
    try:
        rc = get_redis()
        cutoff = time.time() - 3600
        return rc.zcount(f"p27a:allbusy:{platform}", cutoff, "+inf")
    except Exception:
        return 0


def get_last_timestamps(platform: str) -> dict:
    """Return last success/failure ISO timestamps for a platform."""
    try:
        rc = get_redis()
        def _read(key: str) -> Optional[str]:
            raw = rc.get(key)
            return raw.decode() if isinstance(raw, bytes) else raw
        return {
            "lastSuccessAt": _read(f"p27a:ts:{platform}:success"),
            "lastFailureAt": _read(f"p27a:ts:{platform}:failure"),
        }
    except Exception:
        return {"lastSuccessAt": None, "lastFailureAt": None}
