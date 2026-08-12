"""
Phase 27 — Throughput Policy
==============================
Defines the effective RPM ceiling per platform and exposes helpers to check
whether a new request would push the platform over its sustainable rate.

Effective RPM = base_rpm × cookie_multiplier × proxy_factor × circuit_factor
    - base_rpm        : empirically safe baseline (no cookies, no proxy)
    - cookie_mult     : each additional healthy cookie in the pool adds headroom
    - proxy_factor    : 1.0 if proxy available, 0.5 if not (for proxy-dependent platforms)
    - circuit_factor  : 0.2 if HALF (probe only), 0 if OPEN (blocked)

Tunables (env):
    P27_RPM_<PLATFORM>    override base rpm for a platform
    P27_COOKIE_MULT       cookie multiplier (default 1.4 per cookie, capped at 3.0×)
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

# Base sustainable RPM per platform (requests / 60s across all workers)
# Platform   base   notes
_BASE_RPM: dict[str, float] = {
    "youtube":    float(os.getenv("P27_RPM_YOUTUBE",    "40")),
    "tiktok":     float(os.getenv("P27_RPM_TIKTOK",     "30")),
    "facebook":   float(os.getenv("P27_RPM_FACEBOOK",   "20")),
    "instagram":  float(os.getenv("P27_RPM_INSTAGRAM",  "8")),
    "twitter":    float(os.getenv("P27_RPM_TWITTER",    "15")),
    "reddit":     float(os.getenv("P27_RPM_REDDIT",     "25")),
    "bilibili":   float(os.getenv("P27_RPM_BILIBILI",   "20")),
    "spotify":    float(os.getenv("P27_RPM_SPOTIFY",    "60")),  # search → YouTube, very lenient
    "soundcloud": float(os.getenv("P27_RPM_SOUNDCLOUD", "50")),
    "threads":    float(os.getenv("P27_RPM_THREADS",    "15")),
    "douyin":     float(os.getenv("P27_RPM_DOUYIN",     "25")),
    "pinterest":  float(os.getenv("P27_RPM_PINTEREST",  "30")),
}
_DEFAULT_RPM = float(os.getenv("P27_RPM_DEFAULT", "20"))

# Platforms that are severely rate-limited without a proxy
_PROXY_DEPENDENT = {"youtube", "instagram", "twitter", "douyin", "bilibili"}

# Per-extra-cookie RPM multiplier (capped)
_COOKIE_MULT     = float(os.getenv("P27_COOKIE_MULT",     "1.35"))
_COOKIE_MULT_CAP = float(os.getenv("P27_COOKIE_MULT_CAP", "3.0"))

_WINDOW_S = 60  # sliding RPM window


def get_base_rpm(platform: str) -> float:
    # Phase 28: prefer platform_policy (single source of truth), fall back to static dict
    try:
        from app.core.platform_policy import get_platform_policy
        return get_platform_policy(platform).base_rpm
    except Exception:
        return _BASE_RPM.get(platform, _DEFAULT_RPM)


def get_effective_rpm(platform: str) -> float:
    """
    Calculate current effective RPM ceiling for a platform factoring in
    cookie pool size, proxy availability, and circuit breaker state.
    """
    base = get_base_rpm(platform)

    # Cookie multiplier
    try:
        rc = get_redis()
        from app.core.cookie_pool import _COOLDOWN
        cookies = rc.lrange(f"cookie_pool:{platform}", 0, -1)
        healthy_cookies = 0
        for c in cookies:
            from app.core.cookie_pool import _hash
            h = _hash(c)
            if not rc.exists(f"cookie_health:{platform}:{h}"):
                healthy_cookies += 1
        extra = max(0, healthy_cookies - 1)
        mult = min(_COOKIE_MULT_CAP, 1.0 + extra * (_COOKIE_MULT - 1.0))
    except Exception:
        mult = 1.0

    # Proxy factor
    proxy_factor = 1.0
    if platform in _PROXY_DEPENDENT:
        try:
            from app.core.proxy_manager import get_proxy_for_platform
            proxy = get_proxy_for_platform(platform)
            if not proxy:
                proxy_factor = 0.5
        except Exception:
            pass

    # Circuit factor
    circuit_factor = 1.0
    try:
        from app.core.platform_circuit import get_state
        state = get_state(platform)
        if state == "open":
            circuit_factor = 0.0
        elif state == "half":
            circuit_factor = 0.2
    except Exception:
        pass

    return round(base * mult * proxy_factor * circuit_factor, 1)


def is_over_limit(platform: str) -> bool:
    """True if the platform has exceeded its effective RPM in the last 60s."""
    try:
        rc = get_redis()
        count = int(rc.get(f"throttle:{platform}") or 0)
        return count >= get_effective_rpm(platform)
    except Exception:
        return False


def get_all_throughput_snapshot() -> list[dict]:
    """Admin observability: per-platform throughput vs ceiling."""
    try:
        rc = get_redis()
        platforms = list(_BASE_RPM.keys())
        result = []
        for p in platforms:
            count = int(rc.get(f"throttle:{p}") or 0)
            ceiling = get_effective_rpm(p)
            result.append({
                "platform":     p,
                "rpm_current":  count,
                "rpm_ceiling":  ceiling,
                "utilization":  round(count / ceiling * 100) if ceiling > 0 else 0,
                "over_limit":   count >= ceiling,
            })
        return result
    except Exception:
        return []
