"""
Phase 27C — Per-User Per-Platform Fairness Control
====================================================
Tracks how many active downloads a given user has on each platform and enforces
per-platform concurrency caps to prevent one user from monopolizing scarce capacity.

This is NOT global fairness (that's fair_queue.py) — this is platform-specific:
  Instagram cap=2 per user is far tighter than TikTok cap=5 per user, because
  Instagram's cookie pool collapses under parallel abuse; TikTok's TikWM path
  handles concurrency better.

Platform tiers:
  SCARCE  — instagram, twitter     cap: single=2  batch=4
  MEDIUM  — reddit, facebook, bili cap: single=3  batch=6
  AMPLE   — tiktok, youtube, spot  cap: single=5  batch=10
  DEFAULT — everything else        cap: single=4  batch=8

Feature flags:
  FAIRNESS_CONTROL_ENABLED=false       master switch
  FAIRNESS_PLATFORMS=instagram         per-platform (empty=all when master ON)
  FAIRNESS_SHADOW_LOG=false            log decisions but don't enforce

Redis keys (p27c:):
  p27c:ua:{platform}:{user_key}       STRING INCR TTL=1800  — active count per user per platform
  p27c:uq_cnt:{platform}:{user_key}   STRING INCR TTL=3600  — queued count per user per platform
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional

from app.core.redis_client import get_redis

log = logging.getLogger(__name__)

# ── Feature flags ─────────────────────────────────────────────────────────────
_ENABLED      = os.getenv("FAIRNESS_CONTROL_ENABLED", "false").lower() in ("1", "true", "yes")
_SHADOW_LOG   = os.getenv("FAIRNESS_SHADOW_LOG",    "false").lower() in ("1", "true", "yes")
_raw_plat     = os.getenv("FAIRNESS_PLATFORMS", "")
_PLATFORMS: set[str] = {p.strip() for p in _raw_plat.split(",") if p.strip()}


def is_fairness_enabled(platform: str) -> bool:
    if not _ENABLED:
        return False
    return not _PLATFORMS or platform in _PLATFORMS


def is_shadow_mode() -> bool:
    return _SHADOW_LOG


# ── Platform cap configuration ────────────────────────────────────────────────
class _PlatformCap:
    def __init__(self, single: int, batch: int, soft_frac: float = 0.5):
        self.single    = single    # max active single downloads per user
        self.batch     = batch     # max active batch items per user
        self.soft_cap  = max(1, int(single * soft_frac))  # trigger ACCEPT_DELAYED
        self.soft_batch = max(1, int(batch * soft_frac))

_SCARCE_PLATFORMS = {"instagram", "twitter", "x"}
_MEDIUM_PLATFORMS = {"reddit", "facebook", "bilibili"}
_AMPLE_PLATFORMS  = {"tiktok", "youtube", "spotify", "soundcloud", "threads", "douyin"}

_CAPS: dict[str, _PlatformCap] = {
    # SCARCE
    "instagram": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_INSTAGRAM", "2")),
        int(os.getenv("FAIRNESS_CAP_INSTAGRAM_BATCH", "4")),
    ),
    "twitter": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_TWITTER", "2")),
        int(os.getenv("FAIRNESS_CAP_TWITTER_BATCH", "4")),
    ),
    # MEDIUM
    "reddit": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_REDDIT", "3")),
        int(os.getenv("FAIRNESS_CAP_REDDIT_BATCH", "6")),
    ),
    "facebook": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_FACEBOOK", "3")),
        int(os.getenv("FAIRNESS_CAP_FACEBOOK_BATCH", "6")),
    ),
    "bilibili": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_BILIBILI", "3")),
        int(os.getenv("FAIRNESS_CAP_BILIBILI_BATCH", "6")),
    ),
    # AMPLE
    "tiktok": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_TIKTOK", "5")),
        int(os.getenv("FAIRNESS_CAP_TIKTOK_BATCH", "10")),
    ),
    "youtube": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_YOUTUBE", "5")),
        int(os.getenv("FAIRNESS_CAP_YOUTUBE_BATCH", "10")),
    ),
    "spotify": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_SPOTIFY", "8")),
        int(os.getenv("FAIRNESS_CAP_SPOTIFY_BATCH", "16")),
    ),
    "soundcloud": _PlatformCap(
        int(os.getenv("FAIRNESS_CAP_SOUNDCLOUD", "8")),
        int(os.getenv("FAIRNESS_CAP_SOUNDCLOUD_BATCH", "16")),
    ),
}
_DEFAULT_SINGLE = int(os.getenv("FAIRNESS_CAP_DEFAULT", "4"))
_DEFAULT_BATCH  = int(os.getenv("FAIRNESS_CAP_DEFAULT_BATCH", "8"))
_DEFAULT_CAP    = _PlatformCap(_DEFAULT_SINGLE, _DEFAULT_BATCH)

_UA_TTL   = 1800   # 30min active slot TTL (auto-release on crash)
_UQ_TTL   = 3600   # 1h queued slot TTL


def get_cap(platform: str) -> _PlatformCap:
    # Phase 28: prefer platform_policy (single source of truth), fall back to static dict
    try:
        from app.core.platform_policy import get_platform_policy
        pol = get_platform_policy(platform)
        return _PlatformCap(pol.fairness_single_cap, pol.fairness_batch_cap)
    except Exception:
        return _CAPS.get(platform, _DEFAULT_CAP)


def get_user_active(platform: str, user_key: str) -> int:
    try:
        rc = get_redis()
        return int(rc.get(f"p27c:ua:{platform}:{user_key}") or 0)
    except Exception:
        return 0


def get_user_queued(platform: str, user_key: str) -> int:
    try:
        rc = get_redis()
        return int(rc.get(f"p27c:uq_cnt:{platform}:{user_key}") or 0)
    except Exception:
        return 0


def check_user_cap(
    platform: str, user_key: str, is_batch: bool = False
) -> dict:
    """
    Check whether the user is at or over cap for this platform.
    Returns a status dict used by admission_control.evaluate().
    Never raises — fails open on Redis error.
    """
    try:
        cap  = get_cap(platform)
        hard = cap.batch if is_batch else cap.single
        soft = cap.soft_batch if is_batch else cap.soft_cap
        active = get_user_active(platform, user_key)
        return {
            "active":   active,
            "hard_cap": hard,
            "soft_cap": soft,
            "over_hard": active >= hard,
            "over_soft": active >= soft,
        }
    except Exception as e:
        log.warning("[FairnessControl] check_user_cap error: %s", e)
        return {"active": 0, "hard_cap": 999, "soft_cap": 999, "over_hard": False, "over_soft": False}


def acquire_platform_slot(platform: str, user_key: str) -> bool:
    """
    Increment the user's active count for this platform.
    Returns True on success, False on Redis error (fail-open).
    Call release_platform_slot() in a finally block.
    """
    try:
        rc  = get_redis()
        key = f"p27c:ua:{platform}:{user_key}"
        rc.incr(key)
        rc.expire(key, _UA_TTL)
        return True
    except Exception as e:
        log.warning("[FairnessControl] acquire_platform_slot error: %s", e)
        return False


def release_platform_slot(platform: str, user_key: str) -> None:
    """Decrement the user's active count. Always call after job completion."""
    try:
        rc  = get_redis()
        key = f"p27c:ua:{platform}:{user_key}"
        if rc.decr(key) < 0:
            rc.set(key, 0)
    except Exception as e:
        log.debug("[FairnessControl] release_platform_slot error: %s", e)


def increment_queued(platform: str, user_key: str) -> None:
    try:
        rc = get_redis()
        key = f"p27c:uq_cnt:{platform}:{user_key}"
        rc.incr(key)
        rc.expire(key, _UQ_TTL)
    except Exception:
        pass


def decrement_queued(platform: str, user_key: str) -> None:
    try:
        rc = get_redis()
        key = f"p27c:uq_cnt:{platform}:{user_key}"
        if rc.decr(key) < 0:
            rc.set(key, 0)
    except Exception:
        pass


def get_platform_active_total(platform: str) -> int:
    """Aggregate active count across ALL users for a platform (admin)."""
    try:
        rc   = get_redis()
        keys = rc.keys(f"p27c:ua:{platform}:*") or []
        return sum(int(rc.get(k) or 0) for k in keys)
    except Exception:
        return 0


def get_fairness_snapshot(platform: str) -> dict:
    """Admin: per-platform per-user fairness summary (anonymized)."""
    try:
        rc   = get_redis()
        cap  = get_cap(platform)
        keys = rc.keys(f"p27c:ua:{platform}:*") or []
        users = []
        total = 0
        for k in keys[:100]:
            cnt = int(rc.get(k) or 0)
            if cnt > 0:
                uid = (k.decode() if isinstance(k, bytes) else k).split(":")[-1]
                users.append({"user_key": uid[:8] + "**", "active": cnt})
                total += cnt
        users.sort(key=lambda x: x["active"], reverse=True)
        return {
            "platform":       platform,
            "totalActive":    total,
            "singleHardCap":  cap.single,
            "singleSoftCap":  cap.soft_cap,
            "batchHardCap":   cap.batch,
            "topUsers":       users[:10],
        }
    except Exception:
        return {"platform": platform, "totalActive": 0}
