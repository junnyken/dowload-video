"""
Provider health per (provider, platform) — plan §14.2, wave-1 subset.

  china:health:{provider}:{platform}   hash: consecutive_failures, last_failure_category,
                                       last_failure_ts, last_success_ts, paused

Derived state (no flapping: a degraded provider is skipped for a full
cooldown, then gets ONE recovery attempt; a success resets it):
  paused       admin set `paused`               → skipped
  degraded     ≥ threshold consecutive failures, inside cooldown → skipped
  recovery     ≥ threshold, cooldown elapsed    → allowed (one attempt)
  constrained  ≥ 2 consecutive failures         → allowed
  healthy      otherwise                        → allowed
User/policy outcomes (bad link, private item, budget, disabled) are not
provider faults and are not counted.
"""
from __future__ import annotations

import time
from typing import Optional

from app.services.china_platforms import settings
from app.services.china_platforms.errors import NON_HEALTH_CATEGORIES
from app.services.china_platforms.normalized_models import ProviderHealthSnapshot

_TTL = 30 * 86400


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def key(provider: str, platform: str) -> str:
    return f"china:health:{provider}:{platform}"


def _f(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def snapshot(provider: str, platform: str, now: Optional[float] = None) -> ProviderHealthSnapshot:
    now = now or time.time()
    try:
        h = _r().hgetall(key(provider, platform)) or {}
    except Exception:  # noqa: BLE001
        h = {}
    h = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
         for k, v in h.items()}
    fails = int(_f(h.get("consecutive_failures")) or 0)
    last_fail = _f(h.get("last_failure_ts"))
    threshold = settings.health_fail_threshold()
    if h.get("paused") == "1":
        state, eligible = "paused", False
    elif fails >= threshold and last_fail and now - last_fail < settings.health_cooldown_sec():
        state, eligible = "degraded", False
    elif fails >= threshold:
        state, eligible = "recovery", True
    elif fails >= 2:
        state, eligible = "constrained", True
    else:
        state, eligible = "healthy", True
    return ProviderHealthSnapshot(
        provider_name=provider, platform=platform, state=state,
        consecutive_failures=fails,
        last_failure_category=h.get("last_failure_category") or None,
        last_failure_ts=last_fail, last_success_ts=_f(h.get("last_success_ts")),
        eligible=eligible,
    )


def record_success(provider: str, platform: str) -> None:
    try:
        r = _r()
        r.hset(key(provider, platform), mapping={"consecutive_failures": 0, "last_success_ts": f"{time.time():.0f}"})
        r.expire(key(provider, platform), _TTL)
    except Exception:  # noqa: BLE001
        pass


def record_failure(provider: str, platform: str, category: str) -> None:
    if category in NON_HEALTH_CATEGORIES:
        return
    try:
        r = _r()
        r.hincrby(key(provider, platform), "consecutive_failures", 1)
        r.hset(key(provider, platform), mapping={
            "last_failure_category": category, "last_failure_ts": f"{time.time():.0f}"})
        r.expire(key(provider, platform), _TTL)
    except Exception:  # noqa: BLE001
        pass


def set_paused(provider: str, platform: str, paused: bool) -> None:
    r = _r()
    r.hset(key(provider, platform), "paused", "1" if paused else "0")
    r.expire(key(provider, platform), _TTL)
