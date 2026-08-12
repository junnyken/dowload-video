"""
Phase 28 — Runtime Overrides
==============================
Redis-backed per-platform admin controls that take effect immediately without
container restart. Generalises the YouTube gate pattern (youtube_gate.py) to
all platforms.

What can be overridden at runtime:
  - Platform mode: healthy | constrained | degraded | reduced | paused
  - Adaptive wave scheduling: frozen or not
  - Scored cookie selection: frozen or not
  - Individual policy field values (fairness_single_cap, wave_size_max, etc.)

What cannot be overridden at runtime:
  - Circuit breaker thresholds (safety-critical — require deploy + review)
  - Hard limits that protect external services (base_rpm ceiling)

Merge priority (highest wins):
  runtime_overrides > env vars > platform_policy static defaults

Redis keys (p28:):
  p28:mode:{platform}              HASH  {mode, reason, set_at, expires_at}
  p28:freeze_adaptive:{platform}   STRING "1"  TTL=N
  p28:freeze_scoring:{platform}    STRING "1"  TTL=N
  p28:policy_override:{platform}   HASH  {field: value} (read by platform_policy.py)
  p28:global_safe_mode             HASH  {active, reason, set_at}
  p28:override_log                 LIST  LIFO cap=200

All write operations also write to the override audit log.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

_LOG_KEY  = "p28:override_log"
_LOG_CAP  = 200
_GSM_KEY  = "p28:global_safe_mode"

# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit(action: str, platform: str, value: str, reason: str, set_by: str = "admin") -> None:
    """Append an entry to the override audit log. Never raises."""
    try:
        rc    = get_redis()
        entry = json.dumps({
            "ts":       int(time.time()),
            "platform": platform,
            "action":   action,
            "value":    value,
            "reason":   reason,
            "set_by":   set_by,
        })
        rc.lpush(_LOG_KEY, entry)
        rc.ltrim(_LOG_KEY, 0, _LOG_CAP - 1)
        rc.expire(_LOG_KEY, 7 * 86400)
        log.info("[RuntimeOverride] platform=%s action=%s value=%s reason=%s",
                 platform, action, value, reason)
    except Exception as e:
        log.debug("[RuntimeOverride] audit write error: %s", e)


# ── Platform mode overrides ───────────────────────────────────────────────────

VALID_MODES = frozenset({"healthy", "constrained", "degraded", "reduced", "paused"})


def set_platform_mode(
    platform: str,
    mode: str,
    reason: str = "",
    ttl: Optional[int] = None,
    set_by: str = "admin",
) -> None:
    """
    Place a platform into a manual operating mode.
    mode must be one of VALID_MODES.
    ttl=None means no expiry (manual lift required).
    """
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Must be one of {sorted(VALID_MODES)}")
    rc  = get_redis()
    key = f"p28:mode:{platform}"
    val = {
        "mode":       mode,
        "reason":     reason,
        "set_at":     str(int(time.time())),
        "expires_at": str(int(time.time()) + ttl) if ttl else "",
    }
    rc.hset(key, mapping=val)
    if ttl:
        rc.expire(key, ttl)
    _audit("set_mode", platform, mode, reason, set_by)


def clear_platform_mode(platform: str, set_by: str = "admin") -> None:
    """Remove manual mode override — platform returns to auto-derived state."""
    rc = get_redis()
    rc.delete(f"p28:mode:{platform}")
    _audit("clear_mode", platform, "", "manual_clear", set_by)


def get_platform_mode_override(platform: str) -> Optional[dict]:
    """
    Return the active manual mode override for a platform, or None.
    Callers (admission_control, wave_scheduler) check this first before computing state.
    """
    try:
        rc  = get_redis()
        raw = rc.hgetall(f"p28:mode:{platform}")
        if not raw:
            return None
        result = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        # Honour expiry (belt-and-suspenders if Redis TTL wasn't set)
        exp = result.get("expires_at", "")
        if exp and int(exp) < int(time.time()):
            rc.delete(f"p28:mode:{platform}")
            return None
        return result
    except Exception:
        return None


# ── Adaptive wave freeze ───────────────────────────────────────────────────────

def freeze_adaptive(platform: str, reason: str = "", ttl: int = 3600, set_by: str = "admin") -> None:
    """
    Lock the wave scheduler to its current params for this platform.
    The scheduler will return cached state instead of computing new multipliers.
    """
    rc = get_redis()
    rc.setex(f"p28:freeze_adaptive:{platform}", ttl, "1")
    _audit("freeze_adaptive", platform, "1", reason, set_by)


def unfreeze_adaptive(platform: str, set_by: str = "admin") -> None:
    rc = get_redis()
    rc.delete(f"p28:freeze_adaptive:{platform}")
    _audit("unfreeze_adaptive", platform, "0", "manual_unfreeze", set_by)


def is_adaptive_frozen(platform: str) -> bool:
    try:
        rc = get_redis()
        return bool(rc.exists(f"p28:freeze_adaptive:{platform}"))
    except Exception:
        return False


# ── Scored-cookie freeze ───────────────────────────────────────────────────────

def freeze_scoring(platform: str, reason: str = "", ttl: int = 7200, set_by: str = "admin") -> None:
    """Disable Phase 27B scored selection for this platform (falls back to LRU)."""
    rc = get_redis()
    rc.setex(f"p28:freeze_scoring:{platform}", ttl, "1")
    _audit("freeze_scoring", platform, "1", reason, set_by)


def unfreeze_scoring(platform: str, set_by: str = "admin") -> None:
    rc = get_redis()
    rc.delete(f"p28:freeze_scoring:{platform}")
    _audit("unfreeze_scoring", platform, "0", "manual_unfreeze", set_by)


def is_scoring_frozen(platform: str) -> bool:
    try:
        rc = get_redis()
        return bool(rc.exists(f"p28:freeze_scoring:{platform}"))
    except Exception:
        return False


# ── Policy field overrides ────────────────────────────────────────────────────

def set_policy_field(
    platform: str,
    field: str,
    value,
    reason: str = "",
    ttl: Optional[int] = None,
    set_by: str = "admin",
) -> None:
    """
    Override a single platform_policy field at runtime.
    Written to p28:policy_override:{platform} HASH, which platform_policy.py reads.
    """
    rc  = get_redis()
    key = f"p28:policy_override:{platform}"
    rc.hset(key, field, str(value))
    if ttl:
        rc.expire(key, ttl)
    _audit(f"set_policy_{field}", platform, str(value), reason, set_by)


def clear_policy_field(platform: str, field: str, set_by: str = "admin") -> None:
    rc = get_redis()
    rc.hdel(f"p28:policy_override:{platform}", field)
    _audit(f"clear_policy_{field}", platform, "", "manual_clear", set_by)


def clear_all_policy_overrides(platform: str, set_by: str = "admin") -> None:
    rc = get_redis()
    rc.delete(f"p28:policy_override:{platform}")
    _audit("clear_all_policy_overrides", platform, "", "manual_clear", set_by)


# ── Global safe mode ──────────────────────────────────────────────────────────

def set_global_safe_mode(reason: str = "", set_by: str = "admin") -> None:
    """
    Emergency: forces all platforms into conservative mode.
    Adaptive scheduling and scored selection are frozen globally.
    """
    rc = get_redis()
    rc.hset(_GSM_KEY, mapping={"active": "1", "reason": reason, "set_at": str(int(time.time()))})
    _audit("set_global_safe_mode", "__global__", "1", reason, set_by)
    log.warning("[RuntimeOverride] GLOBAL safe mode ENABLED — reason: %s", reason)


def clear_global_safe_mode(set_by: str = "admin") -> None:
    rc = get_redis()
    rc.delete(_GSM_KEY)
    _audit("clear_global_safe_mode", "__global__", "0", "manual_clear", set_by)
    log.info("[RuntimeOverride] GLOBAL safe mode cleared")


def is_global_safe_mode() -> bool:
    try:
        rc  = get_redis()
        raw = rc.hget(_GSM_KEY, "active")
        return raw in (b"1", "1")
    except Exception:
        return False


def get_global_safe_mode_info() -> dict:
    try:
        rc  = get_redis()
        raw = rc.hgetall(_GSM_KEY) or {}
        return {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in raw.items()}
    except Exception:
        return {}


# ── Summary snapshot (admin) ──────────────────────────────────────────────────

def get_all_overrides() -> dict:
    """Admin: all active overrides across all platforms."""
    try:
        rc = get_redis()
        result = {
            "global_safe_mode": get_global_safe_mode_info(),
            "platforms":        {},
        }
        # Collect all p28: keys
        for key_pattern, action in [
            ("p28:mode:*",             "mode"),
            ("p28:freeze_adaptive:*",  "freeze_adaptive"),
            ("p28:freeze_scoring:*",   "freeze_scoring"),
            ("p28:policy_override:*",  "policy_override"),
        ]:
            for k in (rc.keys(key_pattern) or []):
                k_str = k.decode() if isinstance(k, bytes) else k
                platform = k_str.rsplit(":", 1)[-1]
                if platform == "global__":
                    continue
                if platform not in result["platforms"]:
                    result["platforms"][platform] = {}
                if action == "mode":
                    result["platforms"][platform]["mode"] = get_platform_mode_override(platform)
                elif action == "freeze_adaptive":
                    result["platforms"][platform]["adaptive_frozen"] = True
                elif action == "freeze_scoring":
                    result["platforms"][platform]["scoring_frozen"] = True
                elif action == "policy_override":
                    raw = rc.hgetall(k) or {}
                    result["platforms"][platform]["policy_fields"] = {
                        (fk.decode() if isinstance(fk, bytes) else fk):
                        (fv.decode() if isinstance(fv, bytes) else fv)
                        for fk, fv in raw.items()
                    }
        return result
    except Exception as e:
        log.warning("[RuntimeOverride] get_all_overrides error: %s", e)
        return {"global_safe_mode": {}, "platforms": {}}


def get_override_audit_log(limit: int = 20) -> list[dict]:
    """Admin: last N override events."""
    try:
        rc    = get_redis()
        items = rc.lrange(_LOG_KEY, 0, limit - 1) or []
        result = []
        for raw in items:
            try:
                result.append(json.loads(raw))
            except Exception:
                continue
        return result
    except Exception:
        return []
