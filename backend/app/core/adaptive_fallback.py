"""
Adaptive Fallback Tracking Service
====================================
Tracks per-(platform, fallback_layer) performance in Redis.
Used by the downloader to select the best available fallback layer
based on recent success rates, and to auto-manage demotions/promotions.

Redis key schema
----------------
vidgrab:fallback:{platform}:{layer}   — Hash with fields:
    success_count   int   cumulative successes (reset on TTL expiry)
    fail_count      int   cumulative failures
    total_latency_ms int  cumulative latency of successful attempts
    last_used       float unix timestamp
    last_success    float unix timestamp  (0 if never)
    last_failure    float unix timestamp  (0 if never)
    demoted         int   1 = demoted, 0 = healthy
    last_demotion_reason str

TTL: 7 days — counters roll over naturally.
"""

from __future__ import annotations

import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Key prefix
_PREFIX = "vidgrab:fallback"
# Counter TTL — 7 days; refreshed on every write so an active layer never expires mid-use
_TTL_SECONDS = 7 * 24 * 3600

# Auto-demotion / auto-promotion thresholds
_DEMOTE_MIN_ATTEMPTS = 20   # need at least N attempts before demoting
_DEMOTE_THRESHOLD = 0.20    # demote if success rate < 20 %
_PROMOTE_MIN_ATTEMPTS = 10  # need at least N attempts before promoting
_PROMOTE_THRESHOLD = 0.70   # promote if success rate >= 70 %


def _key(platform: str, layer: str) -> str:
    return f"{_PREFIX}:{platform}:{layer}"


def _get_redis():
    # Late import to avoid circular imports at module load time.
    from app.core.redis_client import get_redis
    return get_redis()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_attempt(
    platform: str,
    layer: str,
    success: bool,
    latency_ms: int,
    error_type: Optional[str] = None,
) -> None:
    """
    Record one download attempt for (platform, layer).

    Parameters
    ----------
    platform    : e.g. "youtube", "tiktok"
    layer       : e.g. "layer0", "layer1", "scraperapi"
    success     : True if the attempt produced a usable result
    latency_ms  : wall-clock time for the attempt in milliseconds
    error_type  : optional short string describing the error class (logged only)
    """
    try:
        r = _get_redis()
        key = _key(platform, layer)
        now = time.time()

        pipe = r.pipeline()
        if success:
            pipe.hincrby(key, "success_count", 1)
            pipe.hincrby(key, "total_latency_ms", max(0, int(latency_ms)))
            pipe.hset(key, "last_success", now)
        else:
            pipe.hincrby(key, "fail_count", 1)
            pipe.hset(key, "last_failure", now)
            if error_type:
                pipe.hset(key, "last_error_type", error_type)

        pipe.hset(key, "last_used", now)
        # Ensure defaults exist for fields that may not have been set yet.
        pipe.hsetnx(key, "success_count", 0)
        pipe.hsetnx(key, "fail_count", 0)
        pipe.hsetnx(key, "total_latency_ms", 0)
        pipe.hsetnx(key, "last_success", 0)
        pipe.hsetnx(key, "last_failure", 0)
        pipe.hsetnx(key, "demoted", 0)
        # Refresh TTL on every write so an active layer never expires mid-week.
        pipe.expire(key, _TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.exception("adaptive_fallback.record_attempt failed for %s/%s", platform, layer)


def get_best_layer(platform: str, available_layers: list[str]) -> str:
    """
    Return the layer from *available_layers* with the best recent success rate.

    Selection rules (in priority order):
    1. Skip demoted layers entirely.
    2. Among healthy layers, pick the one with the highest success rate.
       Tie-break: lower average latency wins.
    3. If all layers are demoted, return the first one (last resort).
    4. If a layer has no recorded history it gets a neutral score (0.5) so it
       is tried before a known-bad layer but after a known-good one.

    Parameters
    ----------
    platform         : platform identifier
    available_layers : ordered list of layer names (caller's preferred order)

    Returns
    -------
    str — the chosen layer name
    """
    if not available_layers:
        raise ValueError("available_layers must be non-empty")

    try:
        r = _get_redis()
        scored: list[tuple[float, float, str]] = []  # (success_rate, avg_latency, layer)
        demoted_layers: list[str] = []

        for layer in available_layers:
            key = _key(platform, layer)
            data = r.hgetall(key)
            if not data:
                # No history — neutral score, very low average latency (treated as fast)
                scored.append((0.5, 0.0, layer))
                continue

            if int(data.get("demoted", 0)) == 1:
                demoted_layers.append(layer)
                continue

            s = int(data.get("success_count", 0))
            f = int(data.get("fail_count", 0))
            total = s + f
            rate = s / total if total > 0 else 0.5

            lat_total = int(data.get("total_latency_ms", 0))
            avg_lat = lat_total / s if s > 0 else float("inf")

            scored.append((rate, avg_lat, layer))

        if not scored:
            # All layers are demoted — return first as last resort
            logger.warning(
                "adaptive_fallback: all layers demoted for %s; using %s as last resort",
                platform, available_layers[0],
            )
            return available_layers[0]

        # Sort: highest success rate first; ties broken by lowest avg latency
        scored.sort(key=lambda t: (-t[0], t[1]))
        chosen = scored[0][2]
        logger.debug(
            "adaptive_fallback: platform=%s chosen=%s rate=%.2f",
            platform, chosen, scored[0][0],
        )
        return chosen

    except Exception:
        logger.exception("adaptive_fallback.get_best_layer failed; returning first layer")
        return available_layers[0]


def get_fallback_stats(platform: Optional[str] = None) -> dict:
    """
    Return stats for all tracked (platform, layer) pairs, optionally filtered
    to a single platform.

    Returns
    -------
    dict keyed by "<platform>:<layer>" with sub-dict of all hash fields plus
    computed "success_rate" and "avg_latency_ms".
    """
    try:
        r = _get_redis()
        pattern = f"{_PREFIX}:{platform}:*" if platform else f"{_PREFIX}:*"
        keys = r.keys(pattern)
        result: dict[str, dict] = {}

        pipe = r.pipeline()
        for k in keys:
            pipe.hgetall(k)
        raw_values = pipe.execute()

        for k, data in zip(keys, raw_values):
            # Strip prefix to get "<platform>:<layer>"
            short_key = k[len(_PREFIX) + 1:]
            s = int(data.get("success_count", 0))
            f = int(data.get("fail_count", 0))
            total = s + f
            lat_total = int(data.get("total_latency_ms", 0))

            enriched = dict(data)
            enriched["success_rate"] = round(s / total, 4) if total > 0 else None
            enriched["avg_latency_ms"] = round(lat_total / s, 1) if s > 0 else None
            enriched["total_attempts"] = total
            result[short_key] = enriched

        return result
    except Exception:
        logger.exception("adaptive_fallback.get_fallback_stats failed")
        return {}


def demote_layer(platform: str, layer: str, reason: str = "") -> None:
    """
    Manually demote a layer. Demoted layers are skipped by get_best_layer.

    Parameters
    ----------
    platform : platform identifier
    layer    : layer name
    reason   : human-readable explanation stored in Redis for observability
    """
    try:
        r = _get_redis()
        key = _key(platform, layer)
        pipe = r.pipeline()
        pipe.hset(key, "demoted", 1)
        pipe.hset(key, "last_demotion_reason", reason or "manual")
        pipe.hset(key, "last_demoted_at", time.time())
        pipe.hsetnx(key, "success_count", 0)
        pipe.hsetnx(key, "fail_count", 0)
        pipe.expire(key, _TTL_SECONDS)
        pipe.execute()
        logger.warning("adaptive_fallback: demoted %s/%s — %s", platform, layer, reason)
    except Exception:
        logger.exception("adaptive_fallback.demote_layer failed for %s/%s", platform, layer)


def promote_layer(platform: str, layer: str) -> None:
    """
    Clear the demoted flag for a layer, re-enabling it in get_best_layer.
    """
    try:
        r = _get_redis()
        key = _key(platform, layer)
        pipe = r.pipeline()
        pipe.hset(key, "demoted", 0)
        pipe.hdel(key, "last_demotion_reason")
        pipe.hset(key, "last_promoted_at", time.time())
        pipe.expire(key, _TTL_SECONDS)
        pipe.execute()
        logger.info("adaptive_fallback: promoted %s/%s", platform, layer)
    except Exception:
        logger.exception("adaptive_fallback.promote_layer failed for %s/%s", platform, layer)


def auto_manage_demotions() -> list[dict]:
    """
    Scan all tracked layers and apply automatic demotion / promotion rules.

    Rules
    -----
    - Demote  : success_rate < 20 % AND total_attempts >= 20
    - Promote : success_rate >= 70 % AND total_attempts >= 10  (even if currently demoted)

    Returns
    -------
    List of dicts describing each action taken:
      {"action": "demoted"|"promoted"|"skipped", "platform": ..., "layer": ...,
       "success_rate": ..., "total_attempts": ...}
    """
    actions: list[dict] = []
    try:
        r = _get_redis()
        keys = r.keys(f"{_PREFIX}:*")
        if not keys:
            return actions

        pipe = r.pipeline()
        for k in keys:
            pipe.hgetall(k)
        raw_values = pipe.execute()

        for k, data in zip(keys, raw_values):
            if not data:
                continue

            # Parse platform and layer from key  vidgrab:fallback:<platform>:<layer>
            parts = k.split(":")
            # parts = ["vidgrab", "fallback", platform, layer]
            # layer may itself contain colons (unlikely but guard):
            if len(parts) < 4:
                continue
            plat = parts[2]
            layer = ":".join(parts[3:])

            s = int(data.get("success_count", 0))
            f = int(data.get("fail_count", 0))
            total = s + f
            rate = s / total if total > 0 else None
            currently_demoted = int(data.get("demoted", 0)) == 1

            entry = {
                "platform": plat,
                "layer": layer,
                "success_rate": round(rate, 4) if rate is not None else None,
                "total_attempts": total,
            }

            if rate is not None and total >= _PROMOTE_MIN_ATTEMPTS and rate >= _PROMOTE_THRESHOLD:
                if currently_demoted:
                    promote_layer(plat, layer)
                    entry["action"] = "promoted"
                else:
                    entry["action"] = "skipped_healthy"
            elif rate is not None and total >= _DEMOTE_MIN_ATTEMPTS and rate < _DEMOTE_THRESHOLD:
                if not currently_demoted:
                    reason = f"auto: success_rate={rate:.1%} over {total} attempts"
                    demote_layer(plat, layer, reason)
                    entry["action"] = "demoted"
                else:
                    entry["action"] = "skipped_already_demoted"
            else:
                entry["action"] = "skipped"

            actions.append(entry)

    except Exception:
        logger.exception("adaptive_fallback.auto_manage_demotions failed")

    return actions
