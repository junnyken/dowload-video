"""
Budget / quota guard for paid China-access providers (plan §9).

Same pattern as app/services/asr/budget.py — kill switch first, reserve then
check then roll back, fail CLOSED when Redis is unreachable, TTL'd UTC-day
keys, one alert per day — but integer micro-USD (INCRBY) instead of
INCRBYFLOAT, so ceilings do not drift with float rounding.

Levels, all checked before any paid dispatch:
  1. user/IP quota      china:quota:user:{day}:{requester}:{platform}
  2. platform           china:platform:calls|spend_usd_micros:{day}:{platform}
  3. provider (vendor)  china:provider:calls|spend_usd_micros:{day}:{provider}
                        china:provider:spend_month_usd_micros:{yyyymm}:{provider}
  4. global/platform kill switch  china:killswitch:global | :platform:{p}
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.services.china_platforms import settings

logger = logging.getLogger("app.china_access")

GLOBAL_KILL_KEY = "china:killswitch:global"
PLATFORM_KILL_KEY = "china:killswitch:platform:{platform}"
_DAY_TTL = 3 * 86400
_MONTH_TTL = 40 * 86400


class BudgetDenied(Exception):
    def __init__(self, level: str, detail: str):
        self.level = level
        self.detail = detail
        super().__init__(f"{level}: {detail}")


def utcnow() -> datetime:
    """Single clock for day/month boundaries — tests pin this."""
    return datetime.now(timezone.utc)


def day_key(now: Optional[datetime] = None) -> str:
    return (now or utcnow()).astimezone(timezone.utc).strftime("%Y%m%d")


def month_key(now: Optional[datetime] = None) -> str:
    return (now or utcnow()).astimezone(timezone.utc).strftime("%Y%m")


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _i(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


# ── keys ────────────────────────────────────────────────────────────────────

def k_user(day: str, requester: str, platform: str) -> str:
    return f"china:quota:user:{day}:{requester}:{platform}"


def k_platform_calls(day: str, platform: str) -> str:
    return f"china:platform:calls:{day}:{platform}"


def k_platform_spend(day: str, platform: str) -> str:
    return f"china:platform:spend_usd_micros:{day}:{platform}"


def k_provider_calls(day: str, provider: str) -> str:
    return f"china:provider:calls:{day}:{provider}"


def k_provider_spend(day: str, provider: str) -> str:
    return f"china:provider:spend_usd_micros:{day}:{provider}"


def k_provider_month(month: str, provider: str) -> str:
    return f"china:provider:spend_month_usd_micros:{month}:{provider}"


# ── kill switches ───────────────────────────────────────────────────────────

def global_killswitch_on() -> bool:
    """Env or Redis. Raises on Redis failure — callers decide."""
    if settings.env_global_killswitch():
        return True
    return bool(_r().get(GLOBAL_KILL_KEY))


def platform_killswitch_on(platform: str) -> bool:
    return bool(_r().get(PLATFORM_KILL_KEY.format(platform=platform)))


def set_killswitch(scope: str, on: bool) -> None:
    key = GLOBAL_KILL_KEY if scope == "global" else PLATFORM_KILL_KEY.format(platform=scope)
    if on:
        _r().set(key, "1")
    else:
        _r().delete(key)


# ── limits ──────────────────────────────────────────────────────────────────

def requester_limit(requester: str) -> int:
    if requester == "admin":
        return settings.admin_daily_limit()
    if requester.startswith("user:"):
        return settings.free_daily_limit()
    return settings.anon_daily_limit()   # ip:* and the shared "unknown" bucket


@dataclass
class PaidCandidate:
    provider_name: str
    budget_class: str
    est_micros: int


@dataclass
class Reservation:
    day: str
    month: str
    platform: str
    budget_class: str
    requester: str
    est_micros: int
    done: list = field(default_factory=list)   # (key, amount) increments applied
    recorded_micros: Optional[int] = None      # set by settle(): what this call now counts for


def precheck(platform: str, requester: str, candidates: list[PaidCandidate]) -> Optional[BudgetDenied]:
    """Plan §8.1 steps 5-7, READ-ONLY, in order. None = paid path allowed.
    Redis failure → denied (fail closed)."""
    if not candidates:
        return None
    try:
        r = _r()
        day, month = day_key(), month_key()
        # 5. user/IP quota (paid path only — owner correction #8)
        if _i(r.get(k_user(day, requester, platform))) >= requester_limit(requester):
            return BudgetDenied("user_quota", f"{requester.split(':', 1)[0]} daily managed limit")
        # 6. platform calls + spend
        est_max = max(c.est_micros for c in candidates)
        if _i(r.get(k_platform_calls(day, platform))) >= settings.platform_managed_daily_call_limit(platform):
            return BudgetDenied("platform_calls", f"{platform} daily managed call limit")
        if _i(r.get(k_platform_spend(day, platform))) + est_max > settings.platform_managed_daily_spend_micros(platform):
            return BudgetDenied("platform_spend", f"{platform} daily spend ceiling")
        # 7. provider daily calls/spend + monthly spend (each candidate vendor)
        for c in candidates:
            if _i(r.get(k_provider_calls(day, c.budget_class))) >= settings.provider_daily_call_limit(c.budget_class):
                return BudgetDenied("provider_calls", f"{c.budget_class} daily call limit")
            if _i(r.get(k_provider_spend(day, c.budget_class))) + c.est_micros > settings.provider_daily_spend_micros(c.budget_class):
                return BudgetDenied("provider_spend", f"{c.budget_class} daily spend ceiling")
            if _i(r.get(k_provider_month(month, c.budget_class))) + c.est_micros > settings.provider_monthly_spend_micros(c.budget_class):
                return BudgetDenied("provider_month", f"{c.budget_class} monthly spend ceiling")
    except Exception as exc:  # noqa: BLE001
        logger.error("china_access budget precheck: redis unavailable: %s", type(exc).__name__)
        return BudgetDenied("budget_unavailable", "redis unavailable")
    return None


def reserve(platform: str, requester: str, cand: PaidCandidate) -> Reservation:
    """Atomically count this paid call against every level, or raise
    BudgetDenied and roll back whatever was incremented. Fails closed."""
    day, month = day_key(), month_key()
    res = Reservation(day=day, month=month, platform=platform, budget_class=cand.budget_class,
                      requester=requester, est_micros=cand.est_micros)
    steps = (
        ("user_quota", k_user(day, requester, platform), 1, requester_limit(requester), _DAY_TTL),
        ("platform_calls", k_platform_calls(day, platform), 1, settings.platform_managed_daily_call_limit(platform), _DAY_TTL),
        ("provider_calls", k_provider_calls(day, cand.budget_class), 1, settings.provider_daily_call_limit(cand.budget_class), _DAY_TTL),
        ("platform_spend", k_platform_spend(day, platform), cand.est_micros, settings.platform_managed_daily_spend_micros(platform), _DAY_TTL),
        ("provider_spend", k_provider_spend(day, cand.budget_class), cand.est_micros, settings.provider_daily_spend_micros(cand.budget_class), _DAY_TTL),
        ("provider_month", k_provider_month(month, cand.budget_class), cand.est_micros, settings.provider_monthly_spend_micros(cand.budget_class), _MONTH_TTL),
    )
    try:
        r = _r()
        for level, key, amount, limit, ttl in steps:
            new = _i(r.incrby(key, amount))
            r.expire(key, ttl)
            res.done.append((key, amount))
            if new > limit:
                rollback(res)
                if level in ("platform_spend", "provider_spend", "provider_month"):
                    alert_ceiling_once(level, platform, cand.budget_class)
                raise BudgetDenied(level, f"{level} limit reached")
    except BudgetDenied:
        raise
    except Exception as exc:  # noqa: BLE001
        rollback(res)
        logger.error("china_access budget reserve: redis unavailable: %s", type(exc).__name__)
        raise BudgetDenied("budget_unavailable", "redis unavailable") from exc
    return res


def rollback(res: Reservation) -> None:
    try:
        r = _r()
        for key, amount in res.done:
            r.decrby(key, amount)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access budget rollback failed: %s", type(exc).__name__)
    res.done = []


def settle(res: Reservation, actual_usd: Optional[float], run_started: bool) -> int:
    """Replace the reserved estimate with what was actually charged.
    No run started → the spend estimate is refunded (calls/quota stay counted:
    an attempt was made). Unknown actual cost → the estimate stays.
    Returns the micro-USD recorded for this call."""
    if not run_started:
        actual_micros = 0
    elif actual_usd is None:
        res.recorded_micros = res.est_micros
        return res.est_micros
    else:
        actual_micros = settings.usd_to_micros(actual_usd)
        # 2026-10-06 first live canary: 3 Douyin runs reported usageTotalUsd
        # $0.00005 each (the run-start fee) while the actor's pay-per-event
        # price is ~$0.007 per result — the per-result charge did not show
        # up in usageTotalUsd. Settling down to it would let the daily
        # ceiling admit ~140x the intended runs. So by default a reported
        # cost below the estimate never lowers the recorded spend; only a
        # higher one raises it.
        if settings.cost_floor_at_estimate() and actual_micros < res.est_micros:
            actual_micros = res.est_micros
    res.recorded_micros = actual_micros
    delta = actual_micros - res.est_micros
    if delta:
        try:
            r = _r()
            r.incrby(k_platform_spend(res.day, res.platform), delta)
            r.incrby(k_provider_spend(res.day, res.budget_class), delta)
            r.incrby(k_provider_month(res.month, res.budget_class), delta)
        except Exception as exc:  # noqa: BLE001
            logger.warning("china_access budget settle failed: %s", type(exc).__name__)
    return actual_micros


def alert_ceiling_once(level: str, platform: str, budget_class: str) -> None:
    day = day_key()
    try:
        if not _r().set(f"china:ceiling_alerted:{day}:{level}:{budget_class}", "1", nx=True, ex=2 * 86400):
            return
    except Exception:  # noqa: BLE001
        return   # no dedupe store → do not risk an alert storm
    try:
        from app.core.alerts import send_admin_alert  # noqa: PLC0415
        send_admin_alert(
            "warning",
            "China access: chạm trần chi phí",
            f"Ngày {day} (UTC): {platform}/{budget_class} chạm mức {level}. Lượt gọi trả phí mới bị từ chối.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access ceiling alert failed: %s", type(exc).__name__)


def snapshot(platforms: list[str], providers: list[str]) -> dict:
    day, month = day_key(), month_key()
    out: dict = {"day_utc": day, "month_utc": month, "redis_ok": True, "platforms": {}, "providers": {}}
    try:
        r = _r()
        for p in platforms:
            out["platforms"][p] = {
                "managed_calls_today": _i(r.get(k_platform_calls(day, p))),
                "managed_call_limit": settings.platform_managed_daily_call_limit(p),
                "spend_today_usd": _i(r.get(k_platform_spend(day, p))) / 1e6,
                "spend_ceiling_usd": settings.platform_managed_daily_spend_micros(p) / 1e6,
            }
        for b in providers:
            out["providers"][b] = {
                "calls_today": _i(r.get(k_provider_calls(day, b))),
                "call_limit": settings.provider_daily_call_limit(b),
                "spend_today_usd": _i(r.get(k_provider_spend(day, b))) / 1e6,
                "daily_ceiling_usd": settings.provider_daily_spend_micros(b) / 1e6,
                "spend_month_usd": _i(r.get(k_provider_month(month, b))) / 1e6,
                "monthly_ceiling_usd": settings.provider_monthly_spend_micros(b) / 1e6,
            }
        out["global_killswitch"] = global_killswitch_on()
    except Exception:  # noqa: BLE001
        out["redis_ok"] = False
    return out


# ── Phase 32B-2 Stage A: actual-cost reconciliation + spend alerts ─────────

def k_reconcile(day: str, provider: str) -> str:
    return f"china:reconcile:{day}:{provider}"


def reconcile(res: Reservation, actual_usd: Optional[float], run_started: bool) -> dict:
    """settle() + a record of how far the estimate was off.

    flag:
      None            actual known and within the overrun ratio, or no run started
      "actual_missing" a run started but the vendor gave no usageTotalUsd — the
                       estimate stays reserved (settle) and the run is flagged
      "over_estimate"  actual > estimate × CHINA_ACCESS_COST_OVERRUN_FLAG_PCT
    Daily per-provider tallies live in china:reconcile:{day}:{provider}
    (runs, flagged_over, flagged_missing, delta_usd_micros)."""
    recorded = settle(res, actual_usd, run_started)
    est = res.est_micros
    if not run_started:
        out = {"estimated_cost_usd": est / 1e6, "actual_cost_usd": 0.0, "cost_delta_usd": -est / 1e6,
               "cost_flag": None}
    elif actual_usd is None:
        out = {"estimated_cost_usd": est / 1e6, "actual_cost_usd": None, "cost_delta_usd": None,
               "cost_flag": "actual_missing"}
    else:
        delta = recorded - est
        over = recorded > est * settings.cost_overrun_flag_ratio() if est > 0 else recorded > 0
        out = {"estimated_cost_usd": est / 1e6, "actual_cost_usd": recorded / 1e6,
               "cost_delta_usd": round(delta / 1e6, 6), "cost_flag": "over_estimate" if over else None}
    if run_started:
        try:
            r = _r()
            k = k_reconcile(res.day, res.budget_class)
            r.hincrby(k, "runs", 1)
            if out["cost_flag"] == "over_estimate":
                r.hincrby(k, "flagged_over", 1)
            elif out["cost_flag"] == "actual_missing":
                r.hincrby(k, "flagged_missing", 1)
            if out["cost_delta_usd"] is not None:
                r.hincrby(k, "delta_usd_micros", int(round(out["cost_delta_usd"] * 1e6)))
            r.expire(k, _MONTH_TTL)
        except Exception as exc:  # noqa: BLE001
            logger.warning("china_access reconcile record failed: %s", type(exc).__name__)
    if out["cost_flag"]:
        logger.warning("china_access cost %s: provider=%s est=%.6f actual=%s", out["cost_flag"],
                       res.budget_class, est / 1e6, out["actual_cost_usd"])
    return out


def reconcile_snapshot(provider: str, day: Optional[str] = None) -> dict:
    day = day or day_key()
    try:
        h = _r().hgetall(k_reconcile(day, provider)) or {}
    except Exception:  # noqa: BLE001
        return {"day_utc": day, "redis_ok": False}
    return {"day_utc": day, "runs": _i(h.get("runs")), "flagged_over": _i(h.get("flagged_over")),
            "flagged_missing": _i(h.get("flagged_missing")),
            "delta_usd": _i(h.get("delta_usd_micros")) / 1e6}


def k_spend_alert(period: str, period_key: str, provider: str, pct: int) -> str:
    return f"china:spend_alert:{period}:{period_key}:{provider}:{pct}"


def check_spend_alerts(budget_class: str) -> list[tuple[str, int]]:
    """Telegram alert when the provider's daily or monthly spend crosses 50/80/100%
    of its ceiling. One alert per threshold per period: SET NX on
    china:spend_alert:{daily|monthly}:{period}:{provider}:{pct}. When several
    thresholds are crossed at once all are claimed and ONE message names the
    highest. Redis unreadable → no alert (no storm). Returns what was sent."""
    if not settings.spend_alerts_enabled():
        return []
    sent: list[tuple[str, int]] = []
    day, month = day_key(), month_key()
    try:
        r = _r()
        periods = (
            ("daily", day, _i(r.get(k_provider_spend(day, budget_class))),
             settings.provider_daily_spend_micros(budget_class), 2 * 86400),
            ("monthly", month, _i(r.get(k_provider_month(month, budget_class))),
             settings.provider_monthly_spend_micros(budget_class), _MONTH_TTL),
        )
        for period, pkey, spent, ceiling, ttl in periods:
            if ceiling <= 0:
                continue
            pct_used = spent * 100.0 / ceiling
            newly = [t for t in settings.SPEND_ALERT_THRESHOLDS
                     if pct_used >= t and r.set(k_spend_alert(period, pkey, budget_class, t), "1", nx=True, ex=ttl)]
            if newly:
                top = max(newly)
                sent.append((period, top))
                _send_spend_alert(period, pkey, budget_class, top, spent, ceiling)
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access spend alert check failed: %s", type(exc).__name__)
    return sent


def _send_spend_alert(period: str, pkey: str, provider: str, pct: int, spent: int, ceiling: int) -> None:
    level = "critical" if pct >= 100 else ("warning" if pct >= 80 else "info")
    label = "ngày" if period == "daily" else "tháng"
    try:
        from app.core.alerts import send_admin_alert  # noqa: PLC0415
        send_admin_alert(
            level,
            f"China access: chi phí {provider} đạt {pct}% trần {label}",
            f"Kỳ {pkey} (UTC): đã dùng ${spent / 1e6:.4f} / trần ${ceiling / 1e6:.2f}."
            + (" Lượt gọi trả phí mới sẽ bị từ chối." if pct >= 100 else ""),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("china_access spend alert send failed: %s", type(exc).__name__)
