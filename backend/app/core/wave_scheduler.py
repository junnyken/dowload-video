"""
Phase 27D — Adaptive Wave Scheduler
=====================================
Computes per-platform (wave_size, wave_delay) at request time by combining:
  - Global base from auto_tuner.get_param("wave_size") [Phase 12]
  - Platform aggressiveness profile (conservative / balanced / ample)
  - Lane health multiplier from lane_observer [Phase 27A]
  - Adaptation cooldown to prevent flapping

This module is pure computation + Redis reads — it does NOT dispatch Celery tasks.
The caller (scrape_channel_task) is responsible for using the returned params.

Feature flags:
  ADAPTIVE_WAVE_SCHEDULING_ENABLED=false  master switch (fail-open to static)
  ADAPTIVE_PLATFORMS=instagram            per-platform (empty = all when master ON)
  ADAPTIVE_MODE_SHADOW_ONLY=false         compute but use static values for dispatch
  ADAPTIVE_SCHEDULER_LOG_DECISIONS=false  log every decision

Redis keys (p27d:):
  p27d:wave:{platform}      HASH  TTL=300   current state snapshot
  p27d:cooldown:{platform}  STRING TTL=Ns   flap-prevention cooldown
  p27d:history:{platform}   LIST  LIFO 50   decision history
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional

log = logging.getLogger(__name__)

# ── Feature flags ──────────────────────────────────────────────────────────────
_ENABLED    = os.getenv("ADAPTIVE_WAVE_SCHEDULING_ENABLED", "false").lower() in ("1", "true", "yes")
_SHADOW     = os.getenv("ADAPTIVE_MODE_SHADOW_ONLY",        "false").lower() in ("1", "true", "yes")
_LOG_DECS   = os.getenv("ADAPTIVE_SCHEDULER_LOG_DECISIONS", "false").lower() in ("1", "true", "yes")
_raw_plat   = os.getenv("ADAPTIVE_PLATFORMS", "")
_PLATFORMS: set[str] = {p.strip() for p in _raw_plat.split(",") if p.strip()}

_COOLDOWN_S          = int(os.getenv("ADAPTIVE_WAVE_COOLDOWN_S",            "300"))
_HARD_BLOCK_COOLDOWN = int(os.getenv("ADAPTIVE_WAVE_HARD_BLOCK_COOLDOWN_S", "900"))


def is_adaptive_enabled(platform: str) -> bool:
    if not _ENABLED:
        return False
    return not _PLATFORMS or platform in _PLATFORMS


# ── Platform aggressiveness profiles ──────────────────────────────────────────
@dataclass
class _Profile:
    name:          str
    min_wave:      int
    default_wave:  int
    max_wave:      int
    min_delay:     float   # seconds
    default_delay: float
    max_delay:     float
    cookie_required: bool = True


_PROFILES: dict[str, _Profile] = {
    # ── CONSERVATIVE — small cookie pool, aggressive rate limits ──────────────
    "instagram": _Profile("conservative",  min_wave=2, default_wave=4, max_wave=6,  min_delay=8,  default_delay=12, max_delay=30,  cookie_required=True),
    "twitter":   _Profile("conservative",  min_wave=2, default_wave=4, max_wave=6,  min_delay=10, default_delay=15, max_delay=40,  cookie_required=True),
    "x":         _Profile("conservative",  min_wave=2, default_wave=4, max_wave=6,  min_delay=10, default_delay=15, max_delay=40,  cookie_required=True),
    # ── BALANCED — moderate pool, manageable limits ────────────────────────────
    "reddit":    _Profile("balanced",      min_wave=3, default_wave=6, max_wave=10, min_delay=4,  default_delay=6,  max_delay=15,  cookie_required=True),
    "facebook":  _Profile("balanced",      min_wave=3, default_wave=6, max_wave=10, min_delay=5,  default_delay=8,  max_delay=20,  cookie_required=False),
    "bilibili":  _Profile("balanced",      min_wave=3, default_wave=5, max_wave=8,  min_delay=4,  default_delay=6,  max_delay=15,  cookie_required=False),
    "threads":   _Profile("balanced",      min_wave=3, default_wave=5, max_wave=8,  min_delay=4,  default_delay=6,  max_delay=15,  cookie_required=False),
    # ── AMPLE — API/search-proxied, no per-cookie scarcity ────────────────────
    "tiktok":    _Profile("ample",         min_wave=5, default_wave=10, max_wave=18, min_delay=2,  default_delay=3,  max_delay=8,   cookie_required=False),
    "youtube":   _Profile("ample",         min_wave=3, default_wave=8,  max_wave=15, min_delay=3,  default_delay=5,  max_delay=12,  cookie_required=False),
    "spotify":   _Profile("ample",         min_wave=5, default_wave=10, max_wave=20, min_delay=1,  default_delay=2,  max_delay=5,   cookie_required=False),
    "soundcloud":_Profile("ample",         min_wave=5, default_wave=10, max_wave=20, min_delay=1,  default_delay=2,  max_delay=5,   cookie_required=False),
}
_DEFAULT_PROFILE = _Profile("balanced",    min_wave=3, default_wave=6,  max_wave=12, min_delay=3,  default_delay=5,  max_delay=15,  cookie_required=False)

# Lane state → (wave_multiplier, delay_multiplier)
_LANE_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "healthy":     (1.00, 1.00),
    "constrained": (0.75, 1.35),
    "degraded":    (0.50, 2.00),
    "paused":      (0.25, 3.00),
    "disabled":    (0.00, 0.00),  # cancel wave entirely
}


@dataclass
class WaveParams:
    platform:        str
    wave_size:       int
    wave_delay:      float
    mode:            str     # aggressive|balanced|conservative|reduced|emergency|disabled
    adaptive_active: bool    # True = adaptive fired; False = static fallback
    shadow_only:     bool
    reason:          str
    profile:         str
    lane_state:      str
    healthy_cookies: int
    base_size:       int     # auto_tuner global value
    ts:              float   # Unix time of computation

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = int(self.ts)
        return d


def _get_policy_profile(platform: str) -> "_Profile":
    """Return a _Profile built from platform_policy.py; falls back to local _PROFILES dict."""
    try:
        from app.core.platform_policy import get_platform_policy
        pp = get_platform_policy(platform)
        return _Profile(
            name=pp.aggressiveness,
            min_wave=pp.wave_size_min,
            default_wave=pp.wave_size_default,
            max_wave=pp.wave_size_max,
            min_delay=pp.wave_delay_min_s,
            default_delay=pp.wave_delay_default_s,
            max_delay=pp.wave_delay_max_s,
            cookie_required=pp.cookie_required,
        )
    except Exception:
        return _PROFILES.get(platform, _DEFAULT_PROFILE)


def _static_params(platform: str) -> WaveParams:
    """Returns static (non-adaptive) params from platform_policy (Phase 28 single source)."""
    p = _get_policy_profile(platform)
    # Best guess: use auto_tuner global if available, otherwise profile default
    base = p.default_wave
    try:
        from app.core.auto_tuner import get_param  # type: ignore[import]
        base = get_param("wave_size")
    except Exception:
        pass
    wave_size  = max(p.min_wave, min(base, p.max_wave))
    wave_delay = p.default_delay  # use profile default; avoids import of video_tasks
    return WaveParams(
        platform=platform, wave_size=wave_size, wave_delay=wave_delay,
        mode="balanced", adaptive_active=False, shadow_only=False,
        reason="adaptive_disabled", profile=p.name,
        lane_state="unknown", healthy_cookies=-1,
        base_size=base, ts=time.time(),
    )


def get_wave_params(platform: str) -> WaveParams:
    """
    Compute effective (wave_size, wave_delay) for this platform dispatch.
    Returns static defaults on any error (fail-open).
    Phase 28: checks runtime_overrides first (global safe mode + adaptive freeze).
    """
    # Phase 28: global safe mode → all platforms use static (most conservative) params
    try:
        from app.core.runtime_overrides import is_global_safe_mode, is_adaptive_frozen
        if is_global_safe_mode() or is_adaptive_frozen(platform):
            p = _get_policy_profile(platform)
            return WaveParams(
                platform=platform,
                wave_size=p.wave_size_min, wave_delay=p.wave_delay_max_s,
                mode="reduced", adaptive_active=False, shadow_only=False,
                reason="p28_frozen", profile=p.aggressiveness,
                lane_state="unknown", healthy_cookies=-1,
                base_size=p.wave_size_default, ts=time.time(),
            )
    except Exception:
        pass
    if not is_adaptive_enabled(platform):
        return _static_params(platform)

    try:
        return _compute_adaptive(platform)
    except Exception as e:
        log.warning("[WaveScheduler] error for %s, using static: %s", platform, e)
        return _static_params(platform)


def _compute_adaptive(platform: str) -> WaveParams:
    from app.core.redis_client import get_redis
    p  = _get_policy_profile(platform)   # Phase 28: reads from platform_policy
    rc = get_redis()

    # ── 1. Global base from auto_tuner ────────────────────────────────────────
    base_wave = p.default_wave
    try:
        from app.core.auto_tuner import get_param
        base_wave = get_param("wave_size")
    except Exception:
        pass

    # ── 2. Lane state and cookie health from 27A ──────────────────────────────
    lane_state      = "healthy"
    healthy_cookies = -1
    try:
        from app.core.lane_observer import observe_platform
        obs = observe_platform(platform)
        lane_state      = obs.get("laneState", "healthy")
        healthy_cookies = obs.get("healthyCookies", -1)
    except Exception:
        pass

    # ── 3. Check cooldown (prevent flapping on rapid downshifts) ──────────────
    in_cooldown = bool(rc.exists(f"p27d:cooldown:{platform}"))
    if in_cooldown and lane_state not in ("disabled",):
        # In cooldown → hold last known state without further downshift
        cached = rc.hgetall(f"p27d:wave:{platform}")
        if cached:
            def _d(k): return (cached.get(k.encode()) or cached.get(k) or b"").decode()
            prev_wave  = int(_d("wave_size") or base_wave)
            prev_delay = float(_d("wave_delay") or p.default_delay)
            prev_mode  = _d("mode") or "balanced"
            params = WaveParams(
                platform=platform, wave_size=prev_wave, wave_delay=prev_delay,
                mode=prev_mode, adaptive_active=True, shadow_only=_SHADOW,
                reason="cooldown_hold", profile=p.name,
                lane_state=lane_state, healthy_cookies=healthy_cookies,
                base_size=base_wave, ts=time.time(),
            )
            _log_decision(params, prev_wave, prev_delay)
            return params

    # ── 4. Apply lane multiplier ───────────────────────────────────────────────
    wave_mult, delay_mult = _LANE_MULTIPLIERS.get(lane_state, (1.0, 1.0))

    if wave_mult == 0.0:
        # Platform is disabled — cancel wave
        params = WaveParams(
            platform=platform, wave_size=0, wave_delay=0.0,
            mode="disabled", adaptive_active=True, shadow_only=_SHADOW,
            reason=f"lane_{lane_state}", profile=p.name,
            lane_state=lane_state, healthy_cookies=healthy_cookies,
            base_size=base_wave, ts=time.time(),
        )
        _persist_state(rc, platform, params)
        _log_decision(params, 0, 0)
        return params

    # Apply profile caps first, then lane multiplier
    capped_base = max(p.min_wave, min(base_wave, p.max_wave))
    raw_wave    = capped_base * wave_mult
    wave_size   = max(p.min_wave, min(p.max_wave, math.ceil(raw_wave)))

    base_delay  = p.default_delay
    raw_delay   = base_delay * delay_mult
    wave_delay  = round(max(p.min_delay, min(p.max_delay, raw_delay)), 1)

    # ── 5. Cookie-scarcity override ───────────────────────────────────────────
    reason = f"lane_{lane_state}"
    if p.cookie_required and healthy_cookies >= 0:
        if healthy_cookies == 0:
            wave_size  = p.min_wave
            wave_delay = p.max_delay
            reason     = "zero_cookies"
        elif healthy_cookies < 3 and wave_size > healthy_cookies:
            wave_size  = max(p.min_wave, healthy_cookies)
            wave_delay = min(p.max_delay, wave_delay * 1.5)
            reason     = f"cookie_scarce_{healthy_cookies}"

    # ── 6. Mode name ──────────────────────────────────────────────────────────
    if wave_mult <= 0.25:
        mode = "emergency"
    elif wave_mult <= 0.5:
        mode = "reduced"
    elif wave_mult <= 0.75:
        mode = "conservative"
    elif wave_size >= p.default_wave:
        mode = "aggressive" if wave_size > p.default_wave else "balanced"
    else:
        mode = "balanced"

    params = WaveParams(
        platform=platform, wave_size=wave_size, wave_delay=wave_delay,
        mode=mode, adaptive_active=True, shadow_only=_SHADOW,
        reason=reason, profile=p.name,
        lane_state=lane_state, healthy_cookies=healthy_cookies,
        base_size=base_wave, ts=time.time(),
    )

    # ── 7. Persist + cooldown (only on significant change) ───────────────────
    _persist_state(rc, platform, params)
    _maybe_set_cooldown(rc, platform, lane_state, reason)
    _log_decision(params, capped_base, base_delay)

    return params


def _persist_state(rc, platform: str, params: WaveParams) -> None:
    try:
        key = f"p27d:wave:{platform}"
        d   = {k: str(v) for k, v in params.to_dict().items()}
        rc.hset(key, mapping=d)
        rc.expire(key, 300)
        # Append to history (LIFO, capped 50)
        hist_key = f"p27d:history:{platform}"
        entry = json.dumps({
            "ts":         int(params.ts),
            "wave_size":  params.wave_size,
            "wave_delay": params.wave_delay,
            "mode":       params.mode,
            "reason":     params.reason,
            "lane_state": params.lane_state,
        })
        rc.lpush(hist_key, entry)
        rc.ltrim(hist_key, 0, 49)
        rc.expire(hist_key, 86400)
    except Exception:
        pass


def _maybe_set_cooldown(rc, platform: str, lane_state: str, reason: str) -> None:
    try:
        cooldown = _HARD_BLOCK_COOLDOWN if "hard_block" in reason else _COOLDOWN_S
        if lane_state in ("degraded", "paused", "disabled"):
            rc.setex(f"p27d:cooldown:{platform}", cooldown, "1")
    except Exception:
        pass


def _log_decision(params: WaveParams, prev_size: int, prev_delay: float) -> None:
    if not _LOG_DECS:
        return
    log.info(
        "[WaveScheduler:%s] profile=%s lane=%s cookies=%d "
        "→ mode=%s wave_size=%d delay=%.1fs (base=%d/%.1fs) reason=%s shadow=%s",
        params.platform, params.profile, params.lane_state, params.healthy_cookies,
        params.mode, params.wave_size, params.wave_delay,
        prev_size, prev_delay, params.reason, params.shadow_only,
    )


# ── Admin helpers ──────────────────────────────────────────────────────────────

def get_platform_wave_snapshot(platform: str) -> dict:
    """Admin: current state + history for a platform."""
    try:
        from app.core.redis_client import get_redis
        rc  = get_redis()
        raw = rc.hgetall(f"p27d:wave:{platform}") or {}
        current = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        # history: last 10
        hist_raw = rc.lrange(f"p27d:history:{platform}", 0, 9) or []
        history  = []
        for h in hist_raw:
            try:
                history.append(json.loads(h))
            except Exception:
                continue
        ttl = rc.ttl(f"p27d:cooldown:{platform}")
        return {
            "platform":          platform,
            "current":           current,
            "history":           history,
            "inCooldown":        ttl > 0,
            "cooldownExpiresIn": ttl if ttl > 0 else None,
            "adaptiveEnabled":   is_adaptive_enabled(platform),
            "shadowOnly":        _SHADOW,
        }
    except Exception:
        return {"platform": platform, "current": {}, "history": []}


def get_all_wave_snapshots() -> list[dict]:
    """Admin: wave state for all platforms with active keys."""
    try:
        from app.core.redis_client import get_redis
        rc   = get_redis()
        keys = rc.keys("p27d:wave:*") or []
        out  = []
        for k in keys:
            k_str = k.decode() if isinstance(k, bytes) else k
            platform = k_str.split(":", 2)[-1]
            snap = get_platform_wave_snapshot(platform)
            out.append(snap)
        return sorted(out, key=lambda x: x.get("current", {}).get("mode", ""), reverse=True)
    except Exception:
        return []
