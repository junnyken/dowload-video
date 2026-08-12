"""
Smart Defaults Service
======================
Remembers user download preferences per platform, stored entirely in Redis.
No Supabase calls — optimised for low-latency read on every download request.

Redis key layout
----------------
  vidgrab:prefs:{user_id}:{platform}   — per-user hash
  vidgrab:prefs:global:{platform}      — global aggregate hash

Hash fields
-----------
  quality               str   most-recently chosen quality
  last_success_quality  str   last quality that produced a successful download
  subtitle_enabled      str   "1" or "0"
  remove_watermark      str   "1" or "0"
  archive_pin           str   "1" or "0"
  usage_count           str   integer, incremented on every download
  last_used             str   ISO-8601 timestamp
  # global only
  quality_counts        str   JSON-encoded {quality: count} mapping
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.redis_client import get_redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality hierarchy — index 0 is highest
# ---------------------------------------------------------------------------
QUALITY_HIERARCHY: list[str] = [
    "4k",
    "1080p",
    "720p",
    "480p",
    "video_fast",
    "mp3_320",
    "mp3_128",
]

_QUALITY_RANK: dict[str, int] = {q: i for i, q in enumerate(QUALITY_HIERARCHY)}

DEFAULT_QUALITY = "720p"
DEFAULT_PLATFORM_QUALITY: dict[str, str] = {
    "youtube": "1080p",
    "tiktok": "720p",
    "instagram": "720p",
    "facebook": "720p",
    "twitter": "720p",
    "threads": "720p",
}

# Redis key TTL: 180 days — preferences should survive long idle periods
_PREFS_TTL_SECONDS = 180 * 24 * 3600

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _user_key(user_id: str, platform: str) -> str:
    return f"vidgrab:prefs:{user_id}:{platform}"


def _global_key(platform: str) -> str:
    return f"vidgrab:prefs:global:{platform}"


def _step_down_quality(quality: str) -> str:
    """Return the next lower quality tier, or the same tier if already lowest."""
    rank = _QUALITY_RANK.get(quality)
    if rank is None:
        return DEFAULT_QUALITY
    next_rank = min(rank + 1, len(QUALITY_HIERARCHY) - 1)
    return QUALITY_HIERARCHY[next_rank]


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value == "1"


def _platform_default_quality(platform: str) -> str:
    return DEFAULT_PLATFORM_QUALITY.get(platform.lower(), DEFAULT_QUALITY)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_defaults(user_id: Optional[str], platform: str) -> dict[str, Any]:
    """
    Return download defaults for this (user, platform) pair.

    Returns
    -------
    dict with keys:
        quality           str
        subtitle_enabled  bool
        remove_watermark  bool
        archive_pin       bool
        suggested         bool   True when values come from stored preferences
    """
    platform = platform.lower()
    r = get_redis()

    if user_id:
        key = _user_key(user_id, platform)
        try:
            raw = r.hgetall(key)
        except Exception:
            logger.exception("Redis error in get_defaults for user=%s platform=%s", user_id, platform)
            raw = {}

        if raw:
            quality = raw.get("quality") or _platform_default_quality(platform)
            return {
                "quality": quality,
                "download_subs": _parse_bool(raw.get("download_subs") or raw.get("subtitle_enabled"), False),
                "remove_watermark": _parse_bool(raw.get("remove_watermark"), False),
                "archive_pin": _parse_bool(raw.get("archive_pin"), False),
                "suggested": True,
            }

    # Fall back to global defaults
    gkey = _global_key(platform)
    try:
        raw_global = r.hgetall(gkey)
    except Exception:
        logger.exception("Redis error reading global prefs for platform=%s", platform)
        raw_global = {}

    if raw_global:
        # Derive most-common quality from quality_counts JSON
        counts_json = raw_global.get("quality_counts", "{}")
        try:
            counts: dict[str, int] = json.loads(counts_json)
        except (json.JSONDecodeError, TypeError):
            counts = {}

        if counts:
            best_quality = max(counts, key=lambda q: counts[q])
        else:
            best_quality = _platform_default_quality(platform)

        return {
            "quality": best_quality,
            "download_subs": _parse_bool(raw_global.get("download_subs") or raw_global.get("subtitle_enabled"), False),
            "remove_watermark": _parse_bool(raw_global.get("remove_watermark"), False),
            "archive_pin": _parse_bool(raw_global.get("archive_pin"), False),
            "suggested": bool(counts),
        }

    return {
        "quality": _platform_default_quality(platform),
        "download_subs": False,
        "remove_watermark": False,
        "archive_pin": False,
        "suggested": False,
    }


def record_choice(
    user_id: str,
    platform: str,
    quality: str,
    success: bool,
    options: dict[str, Any] | None = None,
) -> None:
    """
    Persist the user's download choice and update global aggregates.

    Parameters
    ----------
    user_id   : authenticated user identifier
    platform  : e.g. "youtube", "tiktok"
    quality   : chosen quality string
    success   : whether the download completed without error
    options   : optional dict that may include subtitle_enabled,
                remove_watermark, archive_pin
    """
    if options is None:
        options = {}

    platform = platform.lower()
    r = get_redis()
    now_iso = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # 1. Update per-user hash
    # ------------------------------------------------------------------
    ukey = _user_key(user_id, platform)
    try:
        pipe = r.pipeline()
        pipe.hset(ukey, "quality", quality)
        pipe.hset(ukey, "last_used", now_iso)
        pipe.hincrby(ukey, "usage_count", 1)

        if success:
            pipe.hset(ukey, "last_success_quality", quality)

        # Accept both download_subs (canonical) and subtitle_enabled (legacy alias)
        _sub_val = options.get("download_subs", options.get("subtitle_enabled"))
        if _sub_val is not None:
            pipe.hset(ukey, "download_subs", "1" if _sub_val else "0")
        if "remove_watermark" in options:
            pipe.hset(ukey, "remove_watermark", "1" if options["remove_watermark"] else "0")
        if "archive_pin" in options:
            pipe.hset(ukey, "archive_pin", "1" if options["archive_pin"] else "0")

        pipe.expire(ukey, _PREFS_TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.exception(
            "Redis error in record_choice (user hash) user=%s platform=%s", user_id, platform
        )

    # ------------------------------------------------------------------
    # 2. Update global aggregate hash (quality_counts)
    # ------------------------------------------------------------------
    gkey = _global_key(platform)
    try:
        # Use a Lua script for atomic read-modify-write on quality_counts
        _lua_update_global_counts(r, gkey, quality, options, now_iso)
    except Exception:
        logger.exception(
            "Redis error in record_choice (global hash) platform=%s", platform
        )


# Lua script: atomically increment quality count in the JSON blob
_LUA_UPDATE_COUNTS = """
local key     = KEYS[1]
local quality = ARGV[1]
local now     = ARGV[2]
local sub_en  = ARGV[3]
local rm_wm   = ARGV[4]
local arc_pin = ARGV[5]

local raw = redis.call('HGET', key, 'quality_counts')
local counts = {}
if raw then
    -- minimal JSON number parse; we store {"q":n,...}
    local json_str = raw
    -- find existing count for this quality
    local pat = '"' .. quality .. '":(%d+)'
    local cur = string.match(json_str, pat)
    if cur then
        local new_val = tonumber(cur) + 1
        json_str = string.gsub(json_str, pat, '"' .. quality .. '":' .. new_val, 1)
    else
        -- append new entry
        if json_str == '{}' then
            json_str = '{"' .. quality .. '":1}'
        else
            json_str = string.sub(json_str, 1, -2) .. ',"' .. quality .. '":1}'
        end
    end
    redis.call('HSET', key, 'quality_counts', json_str)
else
    redis.call('HSET', key, 'quality_counts', '{"' .. quality .. '":1}')
end

redis.call('HSET', key, 'last_used', now)

if sub_en ~= '' then redis.call('HSET', key, 'subtitle_enabled', sub_en) end
if rm_wm  ~= '' then redis.call('HSET', key, 'remove_watermark',  rm_wm)  end
if arc_pin ~= '' then redis.call('HSET', key, 'archive_pin',       arc_pin) end

return 1
"""

_lua_script_sha: Optional[str] = None


def _lua_update_global_counts(
    r: Any,
    gkey: str,
    quality: str,
    options: dict[str, Any],
    now_iso: str,
) -> None:
    """Run atomic Lua update on the global hash; fall back to Python if Lua fails."""
    global _lua_script_sha

    sub_en = ("1" if options["subtitle_enabled"] else "0") if "subtitle_enabled" in options else ""
    rm_wm = ("1" if options["remove_watermark"] else "0") if "remove_watermark" in options else ""
    arc_pin = ("1" if options["archive_pin"] else "0") if "archive_pin" in options else ""

    argv = [quality, now_iso, sub_en, rm_wm, arc_pin]

    try:
        if _lua_script_sha is None:
            _lua_script_sha = r.script_load(_LUA_UPDATE_COUNTS)
        r.evalsha(_lua_script_sha, 1, gkey, *argv)
        r.expire(gkey, _PREFS_TTL_SECONDS)
        return
    except Exception:
        # Lua unavailable or NOSCRIPT — fall back to non-atomic Python path
        _lua_script_sha = None
        logger.debug("Lua script unavailable, falling back to Python for global prefs update")

    # Python fallback (slightly racy but acceptable for aggregates)
    raw = r.hget(gkey, "quality_counts") or "{}"
    try:
        counts: dict[str, int] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        counts = {}
    counts[quality] = counts.get(quality, 0) + 1

    pipe = r.pipeline()
    pipe.hset(gkey, "quality_counts", json.dumps(counts))
    pipe.hset(gkey, "last_used", now_iso)
    if sub_en:
        pipe.hset(gkey, "subtitle_enabled", sub_en)
    if rm_wm:
        pipe.hset(gkey, "remove_watermark", rm_wm)
    if arc_pin:
        pipe.hset(gkey, "archive_pin", arc_pin)
    pipe.expire(gkey, _PREFS_TTL_SECONDS)
    pipe.execute()


def suggest_quality(user_id: str, platform: str) -> str:
    """
    Suggest the best quality for the next download attempt.

    Logic
    -----
    1. If last_success_quality is stored → return it.
    2. Else if quality is stored but last download failed → step down one tier.
    3. Else → return platform default.
    """
    platform = platform.lower()
    r = get_redis()

    try:
        raw = r.hmget(_user_key(user_id, platform), "quality", "last_success_quality")
    except Exception:
        logger.exception("Redis error in suggest_quality user=%s platform=%s", user_id, platform)
        return _platform_default_quality(platform)

    current_quality: Optional[str] = raw[0]
    last_success: Optional[str] = raw[1]

    if last_success:
        return last_success

    if current_quality:
        # We have a stored preference but no recorded success — be conservative
        return _step_down_quality(current_quality)

    return _platform_default_quality(platform)


def get_all_user_prefs(user_id: str) -> dict[str, dict[str, Any]]:
    """
    Return preferences for all platforms that have stored data for this user.

    Returns
    -------
    dict keyed by platform name, values are the raw hash dicts with
    booleans decoded.
    """
    r = get_redis()
    pattern = f"vidgrab:prefs:{user_id}:*"

    try:
        keys = r.keys(pattern)
    except Exception:
        logger.exception("Redis error in get_all_user_prefs user=%s", user_id)
        return {}

    if not keys:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for key in keys:
        # Extract platform from key suffix
        platform = key.split(":")[-1]
        try:
            raw = r.hgetall(key)
        except Exception:
            logger.exception("Redis error reading key=%s", key)
            continue

        if raw:
            result[platform] = {
                "quality": raw.get("quality", _platform_default_quality(platform)),
                "last_success_quality": raw.get("last_success_quality"),
                "subtitle_enabled": _parse_bool(raw.get("subtitle_enabled"), False),
                "remove_watermark": _parse_bool(raw.get("remove_watermark"), False),
                "archive_pin": _parse_bool(raw.get("archive_pin"), False),
                "usage_count": int(raw.get("usage_count", 0)),
                "last_used": raw.get("last_used"),
            }

    return result


def clear_user_prefs(user_id: str, platform: Optional[str] = None) -> None:
    """
    Delete stored preferences for a user.

    Parameters
    ----------
    user_id  : user whose prefs to clear
    platform : if given, clear only that platform; otherwise clear all platforms
    """
    r = get_redis()

    if platform:
        key = _user_key(user_id, platform.lower())
        try:
            r.delete(key)
        except Exception:
            logger.exception("Redis error in clear_user_prefs user=%s platform=%s", user_id, platform)
        return

    pattern = f"vidgrab:prefs:{user_id}:*"
    try:
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
    except Exception:
        logger.exception("Redis error in clear_user_prefs (all) user=%s", user_id)
