"""
Phase 28 — Platform Policy (Single Source of Truth)
=====================================================
ALL per-platform configuration values live here.
No other module should hardcode platform-specific numeric thresholds.

Modules that previously maintained their own per-platform dicts:
  fairness_control.py     → 30+ FAIRNESS_CAP_* env vars
  wave_scheduler.py       → _PROFILES dict
  throughput_policy.py    → _BASE_RPM dict
  video_tasks.py          → _PLATFORM_WAVE_DELAYS dict
  obs_recorder.py         → _COOKIE_REQUIRED set

These now call get_platform_policy(platform) and read from the returned
PlatformPolicy dataclass.

Merge order (lowest wins, highest overrides):
  1. DEFAULTS dict  — conservative baseline for unknown platforms
  2. PLATFORM_PROFILES dict  — per-platform well-researched defaults
  3. Env var overrides  — POLICY_<PLATFORM>_<FIELD>=<value>  (backwards compat)
  4. Runtime overrides  — Redis (set via admin, no restart needed)
     → see runtime_overrides.py for the write path

All fields are documented inline. When in doubt, lean conservative.
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class PlatformPolicy:
    """Effective policy for one platform — all fields are resolved/merged values."""

    # ── Identity ─────────────────────────────────────────────────────────────
    platform:              str
    aggressiveness:        str    # "conservative" | "balanced" | "ample"
    cookie_required:       bool
    proxy_required:        bool

    # ── Throughput ceiling ───────────────────────────────────────────────────
    base_rpm:              float  # sustainable req/min across all workers

    # ── Wave scheduling ──────────────────────────────────────────────────────
    wave_size_min:         int
    wave_size_default:     int
    wave_size_max:         int
    wave_delay_min_s:      float  # seconds
    wave_delay_default_s:  float
    wave_delay_max_s:      float

    # ── Per-user fairness caps ────────────────────────────────────────────────
    fairness_single_cap:   int    # max active single downloads per user
    fairness_batch_cap:    int    # max active batch items per user

    # ── Block durations ───────────────────────────────────────────────────────
    soft_block_ttl_s:      int    # how long a soft block penalty lasts
    hard_block_ttl_s:      int    # how long a hard block recovery takes
    warmup_s:              int    # post-hard-block warmup window (score × 0.5×)

    # ── Alert thresholds ─────────────────────────────────────────────────────
    delayed_backlog_alert: int    # queue_depth above this → alert
    reduced_mode_fail_rate: float # fail_rate above this → enter reduced mode

    # ── Feature availability ─────────────────────────────────────────────────
    scoring_eligible:      bool = True   # can use Phase 27B scored selection
    adaptive_eligible:     bool = True   # can use Phase 27D adaptive wave

    def soft_cap(self) -> int:
        """Fairness soft cap (half of single cap, minimum 1)."""
        return max(1, self.fairness_single_cap // 2)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Static defaults for unknown/unclassified platforms ────────────────────────
_DEFAULTS = dict(
    aggressiveness="balanced",
    cookie_required=False,
    proxy_required=False,
    base_rpm=20.0,
    wave_size_min=3,
    wave_size_default=6,
    wave_size_max=12,
    wave_delay_min_s=3.0,
    wave_delay_default_s=5.0,
    wave_delay_max_s=15.0,
    fairness_single_cap=4,
    fairness_batch_cap=8,
    soft_block_ttl_s=300,
    hard_block_ttl_s=1800,
    warmup_s=3600,
    delayed_backlog_alert=20,
    reduced_mode_fail_rate=0.30,
    scoring_eligible=True,
    adaptive_eligible=True,
)

# ── Per-platform overrides from static research + operational experience ──────
_PLATFORM_PROFILES: dict[str, dict] = {
    # ── CONSERVATIVE — small cookie pools, aggressive rate limits ─────────────
    "instagram": dict(
        aggressiveness="conservative",
        cookie_required=True,
        proxy_required=False,
        base_rpm=8.0,
        wave_size_min=2, wave_size_default=4, wave_size_max=6,
        wave_delay_min_s=8.0, wave_delay_default_s=12.0, wave_delay_max_s=30.0,
        fairness_single_cap=2, fairness_batch_cap=4,
        soft_block_ttl_s=900,   # 15 min — IG soft blocks last a while
        hard_block_ttl_s=3600,  # 1h — IG hard blocks require session rotation
        warmup_s=7200,          # 2h post-hard-block warmup
        delayed_backlog_alert=10,
        reduced_mode_fail_rate=0.20,
    ),
    "twitter": dict(
        aggressiveness="conservative",
        cookie_required=True,
        proxy_required=False,
        base_rpm=15.0,
        wave_size_min=2, wave_size_default=4, wave_size_max=6,
        wave_delay_min_s=10.0, wave_delay_default_s=15.0, wave_delay_max_s=40.0,
        fairness_single_cap=2, fairness_batch_cap=4,
        soft_block_ttl_s=600,
        hard_block_ttl_s=3600,
        warmup_s=7200,
        delayed_backlog_alert=10,
        reduced_mode_fail_rate=0.20,
    ),
    # x is twitter alias
    "x": dict(
        aggressiveness="conservative",
        cookie_required=True,
        proxy_required=False,
        base_rpm=15.0,
        wave_size_min=2, wave_size_default=4, wave_size_max=6,
        wave_delay_min_s=10.0, wave_delay_default_s=15.0, wave_delay_max_s=40.0,
        fairness_single_cap=2, fairness_batch_cap=4,
        soft_block_ttl_s=600,
        hard_block_ttl_s=3600,
        warmup_s=7200,
        delayed_backlog_alert=10,
        reduced_mode_fail_rate=0.20,
    ),
    # ── BALANCED — moderate pool, manageable limits ────────────────────────────
    "reddit": dict(
        aggressiveness="balanced",
        cookie_required=True,
        proxy_required=False,
        base_rpm=25.0,
        wave_size_min=3, wave_size_default=6, wave_size_max=10,
        wave_delay_min_s=4.0, wave_delay_default_s=6.0, wave_delay_max_s=15.0,
        fairness_single_cap=3, fairness_batch_cap=6,
        soft_block_ttl_s=300,
        hard_block_ttl_s=1800,
        warmup_s=3600,
        delayed_backlog_alert=20,
        reduced_mode_fail_rate=0.30,
    ),
    "facebook": dict(
        aggressiveness="balanced",
        cookie_required=False,
        proxy_required=False,
        base_rpm=20.0,
        wave_size_min=3, wave_size_default=6, wave_size_max=10,
        wave_delay_min_s=5.0, wave_delay_default_s=8.0, wave_delay_max_s=20.0,
        fairness_single_cap=3, fairness_batch_cap=6,
        soft_block_ttl_s=300,
        hard_block_ttl_s=1800,
        warmup_s=3600,
        delayed_backlog_alert=20,
        reduced_mode_fail_rate=0.30,
    ),
    "bilibili": dict(
        aggressiveness="balanced",
        cookie_required=False,
        proxy_required=False,
        base_rpm=20.0,
        wave_size_min=3, wave_size_default=5, wave_size_max=8,
        wave_delay_min_s=4.0, wave_delay_default_s=6.0, wave_delay_max_s=15.0,
        fairness_single_cap=3, fairness_batch_cap=6,
        soft_block_ttl_s=300,
        hard_block_ttl_s=1800,
        warmup_s=3600,
        delayed_backlog_alert=20,
        reduced_mode_fail_rate=0.30,
    ),
    "threads": dict(
        aggressiveness="balanced",
        cookie_required=False,
        proxy_required=False,
        base_rpm=15.0,
        wave_size_min=3, wave_size_default=5, wave_size_max=8,
        wave_delay_min_s=4.0, wave_delay_default_s=6.0, wave_delay_max_s=15.0,
        fairness_single_cap=3, fairness_batch_cap=6,
        soft_block_ttl_s=300,
        hard_block_ttl_s=1800,
        warmup_s=3600,
        delayed_backlog_alert=20,
        reduced_mode_fail_rate=0.30,
    ),
    # ── AMPLE — API/search-proxied, no per-cookie scarcity ────────────────────
    "tiktok": dict(
        aggressiveness="ample",
        cookie_required=False,
        proxy_required=False,
        base_rpm=30.0,
        wave_size_min=5, wave_size_default=10, wave_size_max=18,
        wave_delay_min_s=2.0, wave_delay_default_s=3.0, wave_delay_max_s=8.0,
        fairness_single_cap=5, fairness_batch_cap=10,
        soft_block_ttl_s=60,
        hard_block_ttl_s=600,
        warmup_s=1200,
        delayed_backlog_alert=50,
        reduced_mode_fail_rate=0.40,
    ),
    "youtube": dict(
        aggressiveness="ample",
        cookie_required=False,
        proxy_required=True,    # proxy bandwidth is the actual bottleneck
        base_rpm=40.0,
        wave_size_min=3, wave_size_default=8, wave_size_max=15,
        wave_delay_min_s=3.0, wave_delay_default_s=5.0, wave_delay_max_s=12.0,
        fairness_single_cap=5, fairness_batch_cap=10,
        soft_block_ttl_s=120,
        hard_block_ttl_s=900,
        warmup_s=1800,
        delayed_backlog_alert=30,
        reduced_mode_fail_rate=0.35,
    ),
    "spotify": dict(
        aggressiveness="ample",
        cookie_required=False,
        proxy_required=False,
        base_rpm=60.0,
        wave_size_min=5, wave_size_default=10, wave_size_max=20,
        wave_delay_min_s=1.0, wave_delay_default_s=2.0, wave_delay_max_s=5.0,
        fairness_single_cap=8, fairness_batch_cap=16,
        soft_block_ttl_s=60,
        hard_block_ttl_s=600,
        warmup_s=1200,
        delayed_backlog_alert=50,
        reduced_mode_fail_rate=0.40,
    ),
    "soundcloud": dict(
        aggressiveness="ample",
        cookie_required=False,
        proxy_required=False,
        base_rpm=50.0,
        wave_size_min=5, wave_size_default=10, wave_size_max=20,
        wave_delay_min_s=1.0, wave_delay_default_s=2.0, wave_delay_max_s=5.0,
        fairness_single_cap=8, fairness_batch_cap=16,
        soft_block_ttl_s=60,
        hard_block_ttl_s=600,
        warmup_s=1200,
        delayed_backlog_alert=50,
        reduced_mode_fail_rate=0.40,
    ),
    "douyin": dict(
        aggressiveness="balanced",
        cookie_required=False,
        proxy_required=True,
        base_rpm=25.0,
        wave_size_min=3, wave_size_default=5, wave_size_max=8,
        wave_delay_min_s=4.0, wave_delay_default_s=6.0, wave_delay_max_s=15.0,
        fairness_single_cap=3, fairness_batch_cap=6,
        soft_block_ttl_s=300,
        hard_block_ttl_s=1800,
        warmup_s=3600,
        delayed_backlog_alert=20,
        reduced_mode_fail_rate=0.30,
    ),
    "pinterest": dict(
        aggressiveness="balanced",
        cookie_required=False,
        proxy_required=False,
        base_rpm=30.0,
        wave_size_min=3, wave_size_default=6, wave_size_max=10,
        wave_delay_min_s=3.0, wave_delay_default_s=5.0, wave_delay_max_s=12.0,
        fairness_single_cap=4, fairness_batch_cap=8,
        soft_block_ttl_s=120,
        hard_block_ttl_s=900,
        warmup_s=1800,
        delayed_backlog_alert=20,
        reduced_mode_fail_rate=0.30,
    ),
    "linkedin": dict(
        aggressiveness="conservative",
        cookie_required=False,
        proxy_required=False,
        base_rpm=15.0,
        wave_size_min=2, wave_size_default=4, wave_size_max=8,
        wave_delay_min_s=5.0, wave_delay_default_s=8.0, wave_delay_max_s=20.0,
        fairness_single_cap=3, fairness_batch_cap=6,
        soft_block_ttl_s=300,
        hard_block_ttl_s=1800,
        warmup_s=3600,
        delayed_backlog_alert=10,
        reduced_mode_fail_rate=0.25,
    ),
}

# ── Env var override prefix: POLICY_<PLATFORM_UPPER>_<FIELD_UPPER>=value ──────
# Example: POLICY_INSTAGRAM_FAIRNESS_SINGLE_CAP=3  (overrides static default 2)
def _env_overrides(platform: str, base: dict) -> dict:
    """Check for env var overrides matching POLICY_{PLATFORM}_{FIELD}."""
    prefix = f"POLICY_{platform.upper()}_"
    result = {}
    for k, v in base.items():
        env_key = prefix + k.upper()
        raw = os.environ.get(env_key)
        if raw is not None:
            try:
                if isinstance(v, bool):
                    result[k] = raw.lower() in ("1", "true", "yes")
                elif isinstance(v, int):
                    result[k] = int(raw)
                elif isinstance(v, float):
                    result[k] = float(raw)
                else:
                    result[k] = raw
            except (ValueError, TypeError) as e:
                log.warning("[PlatformPolicy] bad env override %s=%s: %s", env_key, raw, e)
    return result


# ── Runtime Redis overrides (written by admin via runtime_overrides.py) ────────
def _redis_overrides(platform: str) -> dict:
    """Read platform-level field overrides set via admin without restart."""
    try:
        from app.core.redis_client import get_redis
        rc  = get_redis()
        raw = rc.hgetall(f"p28:policy_override:{platform}") or {}
        result = {}
        for k_raw, v_raw in raw.items():
            k = k_raw.decode() if isinstance(k_raw, bytes) else k_raw
            v = v_raw.decode() if isinstance(v_raw, bytes) else v_raw
            try:
                # Try int, float, bool in sequence
                if v.lower() in ("true", "false"):
                    result[k] = v.lower() == "true"
                elif "." in v:
                    result[k] = float(v)
                else:
                    result[k] = int(v)
            except ValueError:
                result[k] = v
        return result
    except Exception:
        return {}


# ── Build effective policy (merge all layers) ──────────────────────────────────
def get_platform_policy(platform: str) -> PlatformPolicy:
    """
    Return the effective PlatformPolicy for this platform.
    Merge order: defaults < profile < env < runtime_redis.
    Never raises — falls back to defaults on any error.
    """
    try:
        base   = dict(_DEFAULTS)
        profile = _PLATFORM_PROFILES.get(platform, {})
        base.update(profile)
        base.update(_env_overrides(platform, base))
        base.update(_redis_overrides(platform))
        return PlatformPolicy(platform=platform, **{k: base[k] for k in _DEFAULTS})
    except Exception as e:
        log.warning("[PlatformPolicy] fallback to defaults for %s: %s", platform, e)
        return PlatformPolicy(platform=platform, **_DEFAULTS)


def get_all_platform_policies() -> dict[str, PlatformPolicy]:
    """Return policy for all known platforms. Admin/snapshot use."""
    known = list(_PLATFORM_PROFILES.keys())
    return {p: get_platform_policy(p) for p in known}
