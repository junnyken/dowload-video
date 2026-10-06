"""
Automatic rollback of the managed route (Phase 32B-2 §9.5, Stage A — Douyin).

Triggers, evaluated after every DISPATCHED managed attempt (a skipped one —
budget, mode, kill switch, paid marker — never counts):

  a) CHINA_ACCESS_AUTO_ROLLBACK_PROBE_FAILURES (2) consecutive managed probe
     failures (origin="probe"; a success resets the counter);
  b) over the last CHINA_ACCESS_AUTO_ROLLBACK_WINDOW (10) managed attempts from
     real traffic and probes (benchmark runs excluded — they are measurements),
     the usable-media success rate is below
     CHINA_ACCESS_AUTO_ROLLBACK_MIN_USABLE_RATE (0.5). The window must be full.

Action: the runtime mode override (registry.MODE_OVERRIDE_KEY, the same key
the admin mode endpoint writes) is set to "benchmark" — only when the
effective mode is above it. It never raises a mode: "off" stays "off". The
change is audited (log_admin_action, reason "auto_rollback") and alerted on
Telegram. Counters are reset so a later manual re-raise starts clean.

Budget ceilings need no trigger here: budget_guard.precheck/reserve already
refuse every paid dispatch once a provider daily/monthly ceiling is reached.

Failures that are the link's fault, not the provider's (unsupported_url,
private_or_login_required) and policy outcomes (budget_exceeded,
platform_disabled) are ignored — same set as provider health.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services.china_platforms import registry, settings
from app.services.china_platforms.errors import NON_HEALTH_CATEGORIES

logger = logging.getLogger("app.china_access")

AUTO_ROLLBACK_PLATFORMS = frozenset({"douyin"})
ROLLBACK_TARGET = "benchmark"

PROBE_FAILS_KEY = "china:rollback:probe_fails:{platform}"
WINDOW_KEY = "china:rollback:window:{platform}"
LAST_KEY = "china:rollback:last:{platform}"
_TTL = 30 * 86400


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def record_managed_outcome(platform: str, provider: str, origin: str, *, success: bool,
                           usable: Optional[bool], category: Optional[str] = None) -> Optional[dict]:
    """Feed one dispatched managed attempt. Returns the rollback record when
    this call triggered one, else None. Never raises."""
    if platform not in AUTO_ROLLBACK_PLATFORMS or not settings.auto_rollback_enabled():
        return None
    if not success and category in NON_HEALTH_CATEGORIES:
        return None
    if origin == "benchmark":
        return None
    good = bool(success and usable is not False)
    try:
        r = _r()
        trigger = None
        if origin == "probe":
            k = PROBE_FAILS_KEY.format(platform=platform)
            if good:
                r.delete(k)
            else:
                n = int(r.incr(k))
                r.expire(k, _TTL)
                if n >= settings.auto_rollback_probe_failures():
                    trigger = f"{n} consecutive managed probe failures ({provider})"
        wk = WINDOW_KEY.format(platform=platform)
        size = settings.auto_rollback_window()
        r.lpush(wk, "1" if good else "0")
        r.ltrim(wk, 0, size - 1)
        r.expire(wk, _TTL)
        if trigger is None:
            vals = [v.decode() if isinstance(v, bytes) else v for v in (r.lrange(wk, 0, size - 1) or [])]
            if len(vals) >= size:
                rate = vals.count("1") / len(vals)
                if rate < settings.auto_rollback_min_usable_rate():
                    trigger = (f"managed usable-media rate {rate:.0%} over last {len(vals)} "
                               f"< {settings.auto_rollback_min_usable_rate():.0%} ({provider})")
        if trigger:
            return auto_rollback(platform, trigger)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access auto-rollback bookkeeping failed: %s", type(exc).__name__)
    return None


def auto_rollback(platform: str, detail: str) -> Optional[dict]:
    """Lower the runtime mode to "benchmark" if (and only if) it is above it."""
    prior = registry.effective_managed_mode(platform)
    if registry.rank(prior) <= registry.rank(ROLLBACK_TARGET):
        return None
    try:
        prior_override = registry.mode_override(platform)
    except Exception:  # noqa: BLE001
        prior_override = None
    r = _r()
    r.set(registry.MODE_OVERRIDE_KEY.format(platform=platform), ROLLBACK_TARGET)
    r.delete(PROBE_FAILS_KEY.format(platform=platform), WINDOW_KEY.format(platform=platform))
    new = registry.effective_managed_mode(platform)
    record = {"platform": platform, "prior": {"effective": prior, "override": prior_override},
              "new": {"effective": new, "override": ROLLBACK_TARGET},
              "reason": "auto_rollback", "detail": detail}
    try:
        import json  # noqa: PLC0415
        from app.services.china_platforms.budget_guard import utcnow  # noqa: PLC0415
        r.set(LAST_KEY.format(platform=platform),
              json.dumps({**record, "ts": utcnow().isoformat()}), ex=90 * 86400)
    except Exception:  # noqa: BLE001
        pass
    logger.error("china_access AUTO-ROLLBACK %s: %s → %s (%s)", platform, prior, new, detail)
    try:
        from app.core.audit import log_admin_action  # noqa: PLC0415
        log_admin_action(None, "admin.china_access.mode", resource_type="china_platform", resource_id=platform,
                         metadata={"prior": record["prior"], "new": record["new"], "reason": "auto_rollback",
                                   "detail": detail, "actor": "system"})
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access auto-rollback audit failed: %s", type(exc).__name__)
    try:
        from app.core.alerts import send_admin_alert  # noqa: PLC0415
        send_admin_alert(
            "critical",
            f"China access: tự hạ {platform} về benchmark",
            f"Chế độ managed {prior} → {new}. Lý do: {detail}. "
            f"Bật lại bằng POST /api/v1/admin/china-platforms/{platform}/mode sau khi kiểm tra.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access auto-rollback alert failed: %s", type(exc).__name__)
    return record


def last_rollback(platform: str) -> Optional[dict]:
    try:
        import json  # noqa: PLC0415
        raw = _r().get(LAST_KEY.format(platform=platform))
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def window_snapshot(platform: str) -> dict:
    try:
        r = _r()
        vals = r.lrange(WINDOW_KEY.format(platform=platform), 0, settings.auto_rollback_window() - 1) or []
        vals = [v.decode() if isinstance(v, bytes) else v for v in vals]
        fails = int(r.get(PROBE_FAILS_KEY.format(platform=platform)) or 0)
    except Exception:  # noqa: BLE001
        return {"redis_ok": False}
    return {"enabled": settings.auto_rollback_enabled() and platform in AUTO_ROLLBACK_PLATFORMS,
            "window_size": settings.auto_rollback_window(), "window_filled": len(vals),
            "window_usable_rate": round(vals.count("1") / len(vals), 4) if vals else None,
            "min_usable_rate": settings.auto_rollback_min_usable_rate(),
            "consecutive_probe_failures": fails,
            "probe_failure_limit": settings.auto_rollback_probe_failures(),
            "last_rollback": last_rollback(platform)}
