"""
Channel Watch ("Theo dõi kênh") settings — Phase 33A, task #6257.

Every value comes from the environment and is read on each call (no cache),
so an env change on the server takes effect without a code change; the
defaults keep the feature OFF.

Flags
  WATCH_ENABLED                  false  master switch: off → every /watch
                                        endpoint answers 404 watch_disabled
                                        and the beat tick does nothing
  WATCH_KILL_SWITCH              false  emergency stop: no scan, no delivery,
                                        no new source; listing/removal still work
  WATCH_REQUIRE_ACCOUNT          true   guests are always refused in 33A (there
                                        is no guest mode); kept for the spec
  WATCH_PLATFORMS_ENABLED        ""     comma list of platforms allowed to be
                                        watched, e.g. "tiktok"
  WATCH_ADMIN_ONLY               true   only an admin session (X-Admin-Token)
                                        may add sources while testing

Limits
  WATCH_FREE_MAX_SOURCES               1
  WATCH_PRO_MAX_SOURCES                30      (pro, team, enterprise)
  WATCH_FREE_MIN_INTERVAL_SEC          86400
  WATCH_PRO_MIN_INTERVAL_SEC           21600
  WATCH_FREE_REPLACE_COOLDOWN_SEC      86400   after removing a source
  WATCH_PRO_REPLACE_COOLDOWN_SEC       3600
  WATCH_IP_DAILY_NEW_SOURCES           3       new sources per client IP per UTC day, all accounts
  WATCH_GLOBAL_FREE_SOURCES_CAP        200     active Free subscriptions, whole site
  WATCH_FREE_REQUIRE_VERIFIED_EMAIL    true
  WATCH_SCAN_BATCH_PER_MINUTE          20      sources enqueued per beat tick
  WATCH_ITEMS_PER_SCAN_DEFAULT         10      newest K items per scan (hard max 20)
  WATCH_GAP_FOLLOWUP_MAX_PAGES         2       extra pages when all K are new (hard max 5)
  WATCH_MAX_INTERVAL_SEC               259200  upper bound incl. failure backoff
  WATCH_PLATFORM_FLOOR_SEC             21600   shortest scan interval of any source
  WATCH_PLATFORM_FLOOR_SEC_<PLATFORM>          per-platform override (e.g. _TIKTOK)
  WATCH_DEGRADE_AFTER_FAILURES         5
  WATCH_SCAN_LOCK_TTL_SEC              300
"""

from __future__ import annotations

import os

HARD_MAX_ITEMS_PER_SCAN = 20
HARD_MAX_FOLLOWUP_PAGES = 5
JITTER = 0.12

# Platforms with a FREE native listing implemented in this phase. China
# platforms (paid access layer) are deliberately absent.
IMPLEMENTED_PLATFORMS = frozenset({"tiktok"})

PAID_TIERS = ("pro", "team", "enterprise")


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int, *, lo: int = 0) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(lo, int(raw))
    except ValueError:
        return default


def enabled() -> bool:
    return _flag("WATCH_ENABLED", False)


def kill_switch() -> bool:
    return _flag("WATCH_KILL_SWITCH", False)


def require_account() -> bool:
    return _flag("WATCH_REQUIRE_ACCOUNT", True)


def admin_only() -> bool:
    return _flag("WATCH_ADMIN_ONLY", True)


def platforms_enabled() -> frozenset:
    raw = os.getenv("WATCH_PLATFORMS_ENABLED") or ""
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())


def platform_enabled(platform: str) -> bool:
    return platform in IMPLEMENTED_PLATFORMS and platform in platforms_enabled()


def scanning_allowed() -> bool:
    """Beat tick, scans and deliveries run only when this is True."""
    return enabled() and not kill_switch()


def is_paid(tier: str) -> bool:
    return (tier or "free") in PAID_TIERS


def max_sources(tier: str) -> int:
    if is_paid(tier):
        return _int("WATCH_PRO_MAX_SOURCES", 30)
    return _int("WATCH_FREE_MAX_SOURCES", 1)


def min_interval_sec(tier: str) -> int:
    if is_paid(tier):
        return _int("WATCH_PRO_MIN_INTERVAL_SEC", 21600, lo=60)
    return _int("WATCH_FREE_MIN_INTERVAL_SEC", 86400, lo=60)


def replace_cooldown_sec(tier: str) -> int:
    if is_paid(tier):
        return _int("WATCH_PRO_REPLACE_COOLDOWN_SEC", 3600)
    return _int("WATCH_FREE_REPLACE_COOLDOWN_SEC", 86400)


def ip_daily_new_sources() -> int:
    return _int("WATCH_IP_DAILY_NEW_SOURCES", 3)


def global_free_cap() -> int:
    return _int("WATCH_GLOBAL_FREE_SOURCES_CAP", 200)


def free_requires_verified_email() -> bool:
    return _flag("WATCH_FREE_REQUIRE_VERIFIED_EMAIL", True)


def scan_batch_per_minute() -> int:
    return _int("WATCH_SCAN_BATCH_PER_MINUTE", 20, lo=1)


def items_per_scan() -> int:
    return min(HARD_MAX_ITEMS_PER_SCAN, _int("WATCH_ITEMS_PER_SCAN_DEFAULT", 10, lo=1))


def gap_followup_max_pages() -> int:
    return min(HARD_MAX_FOLLOWUP_PAGES, _int("WATCH_GAP_FOLLOWUP_MAX_PAGES", 2))


def max_interval_sec() -> int:
    return _int("WATCH_MAX_INTERVAL_SEC", 259200, lo=60)


def platform_floor_sec(platform: str) -> int:
    default = _int("WATCH_PLATFORM_FLOOR_SEC", 21600, lo=60)
    return _int(f"WATCH_PLATFORM_FLOOR_SEC_{(platform or '').upper()}", default, lo=60)


def degrade_after_failures() -> int:
    return _int("WATCH_DEGRADE_AFTER_FAILURES", 5, lo=1)


def scan_lock_ttl_sec() -> int:
    return _int("WATCH_SCAN_LOCK_TTL_SEC", 300, lo=30)
