"""
Cookie Capability Resolver — Phase 26-B
=========================================
Evaluates runtime cookie state for cookie-gated platforms and returns a
deterministic capability dict used by discover/expand/queue pre-flight gates
and the /platforms/capabilities endpoint.

Supported platforms:
  instagram  — requires cookie for profile/media discovery
  twitter    — requires cookie for timeline/thread discovery
  reddit     — public subreddits work without cookie (partial state)

Cookie states:
  cookie_healthy     pool has ≥1 usable cookie (or env-fallback present)
  cookie_unavailable pool empty AND no env-var fallback
  cookie_expired     all cookies expired (min_days_left ≤ 0)
  cookie_blocked     all cookies hard-blocked in Redis
  cookie_partial     Reddit only: public content works, gated content doesn't

Cache: 30-second in-process dict per platform.
       Call invalidate_cache(platform) after admin adds/removes cookies.
"""
from __future__ import annotations

import os
import time
from typing import Optional

_COOKIE_PLATFORMS = {"instagram", "twitter", "reddit"}

# In-process cache: {platform: (timestamp, capability_dict)}
_cache: dict[str, tuple[float, dict]] = {}
_TTL = 30.0


# ── Low-level pool introspection ──────────────────────────────────────────────

def _platform_pool_status(platform: str) -> dict:
    """
    Read pool health for a single platform directly from Redis.
    Returns a dict with keys: total, healthy, in_cooldown, soft_blocked,
    hard_blocked, min_days_left.
    """
    try:
        from app.core.redis_client import get_redis
        import time as _time
    except Exception:
        return {}

    try:
        rc = get_redis()
        cookies = rc.lrange(f"cookie_pool:{platform}", 0, -1)
        if not cookies:
            return {"total": 0}

        import hashlib
        now = int(_time.time())
        healthy = in_cooldown = soft_blocked = hard_blocked = 0
        min_days: Optional[int] = None

        for c in cookies:
            h = hashlib.sha256(c.encode() if isinstance(c, str) else c).hexdigest()[:16]
            health = rc.get(f"cookie_health:{platform}:{h}")
            if health == "hard":
                hard_blocked += 1
            elif health in ("soft", "blocked"):
                soft_blocked += 1
            elif rc.exists(f"cookie_cooldown:{platform}:{h}"):
                in_cooldown += 1
            else:
                healthy += 1

            # Parse expiry from meta
            raw_meta = rc.get(f"cookie_meta:{platform}:{h}")
            if raw_meta:
                try:
                    import json
                    meta = json.loads(raw_meta)
                    exp = meta.get("expires_at", 0)
                    if exp > 0:
                        days = max(0, (exp - now) // 86400)
                        if min_days is None or days < min_days:
                            min_days = days
                except Exception:
                    pass

        return {
            "total":        len(cookies),
            "healthy":      healthy,
            "in_cooldown":  in_cooldown,
            "soft_blocked": soft_blocked,
            "hard_blocked": hard_blocked,
            "min_days_left": min_days,
        }
    except Exception:
        return {}


def _has_env_fallback(platform: str) -> bool:
    return bool(os.environ.get(f"{platform.upper()}_COOKIES_B64", "").strip())


# ── State evaluation ──────────────────────────────────────────────────────────

def _evaluate_state(platform: str) -> str:
    """Derive a single cookie state string for the platform. No cache."""

    # Reddit: public subreddits always work without cookie
    if platform == "reddit":
        return "cookie_partial"

    info = _platform_pool_status(platform)
    total       = info.get("total", 0)
    healthy     = info.get("healthy", 0)
    in_cooldown = info.get("in_cooldown", 0)
    hard_blocked = info.get("hard_blocked", 0)
    min_days    = info.get("min_days_left")

    if total == 0:
        return "cookie_healthy" if _has_env_fallback(platform) else "cookie_unavailable"

    # All cookies expired
    if min_days is not None and min_days <= 0 and not _has_env_fallback(platform):
        return "cookie_expired"

    # All cookies hard-blocked
    if hard_blocked >= total:
        return "cookie_blocked"

    # Has at least one healthy or cooldown-only cookie
    if healthy + in_cooldown > 0:
        return "cookie_healthy"

    # Fallback: all soft-blocked but env exists
    if _has_env_fallback(platform):
        return "cookie_healthy"

    return "cookie_blocked"


# ── Capability dict builders ──────────────────────────────────────────────────

_COOKIE_BLOCK_MESSAGES = {
    "cookie_unavailable": "Chưa cấu hình cookie cho nền tảng này. Liên hệ admin để thêm cookie.",
    "cookie_expired":     "Cookie đã hết hạn. Vui lòng cập nhật cookie trong Cài đặt.",
    "cookie_blocked":     "Cookie tạm thời bị khoá (rate-limit / bot-detect). Thử lại sau.",
    "cookie_partial":     "Một phần nội dung cần đăng nhập để xem đầy đủ.",
}


def _build_capability(platform: str, state: str) -> dict:
    usable  = (state == "cookie_healthy")
    partial = (state == "cookie_partial")
    reason  = None if usable else state

    if platform == "reddit":
        # Reddit: public always accessible; partial means some gated content exists
        return {
            "can_resolve_input":        True,
            "can_discover_container":   True,
            "can_expand":               True,
            "can_queue_container_items": True,
            "can_single_download":      True,
            "requires_cookie":          False,
            "availability_reason":      "cookie_partial",
            "cookie_state":             "cookie_partial",
            "message":                  _COOKIE_BLOCK_MESSAGES["cookie_partial"],
        }

    return {
        "can_resolve_input":        True,
        "can_discover_container":   usable,
        "can_expand":               usable,
        "can_queue_container_items": usable,
        "can_single_download":      usable,
        "requires_cookie":          True,
        "availability_reason":      reason,
        "cookie_state":             state,
        "message":                  _COOKIE_BLOCK_MESSAGES.get(state) if not usable else None,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(platform: str) -> dict:
    """
    Return capability dict for *platform* with 30s in-process cache.
    Thread-safe enough for single-process FastAPI (GIL protects dict ops).
    """
    if platform not in _COOKIE_PLATFORMS:
        return {
            "can_resolve_input":        True,
            "can_discover_container":   True,
            "can_expand":               True,
            "can_queue_container_items": True,
            "can_single_download":      True,
            "requires_cookie":          False,
            "availability_reason":      None,
            "cookie_state":             None,
            "message":                  None,
        }

    cached = _cache.get(platform)
    if cached and (time.time() - cached[0]) < _TTL:
        return cached[1]

    state = _evaluate_state(platform)
    cap   = _build_capability(platform, state)
    _cache[platform] = (time.time(), cap)
    return cap


def get_cookie_state(platform: str) -> str:
    """Convenience: return state string only."""
    return evaluate(platform).get("cookie_state") or "cookie_unavailable"


def is_cookie_healthy(platform: str) -> bool:
    """Return True if cookie is usable (healthy or partial)."""
    state = get_cookie_state(platform)
    return state in ("cookie_healthy", "cookie_partial")


def invalidate_cache(platform: Optional[str] = None) -> None:
    """
    Bust the 30s cache — call this after admin cookie add/remove so the
    next request sees fresh state without waiting for TTL expiry.
    """
    if platform:
        _cache.pop(platform, None)
    else:
        _cache.clear()
