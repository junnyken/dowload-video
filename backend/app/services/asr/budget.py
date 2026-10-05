"""
Global ASR spend guard (Redis), kill switch and per-job cost metadata.

Keys (UTC day, yyyymmdd):
  asr:spend:{day}          USD reserved/spent today      (INCRBYFLOAT, TTL 3d)
  asr:minutes:{day}        audio minutes reserved today  (INCRBYFLOAT, TTL 3d)
  asr:killswitch           "1" when an admin paused ASR  (no TTL)
  asr:ceiling_alerted:{day} once-per-day alert dedupe
  asr:job:{job_id}         hash: provider, quota_key, minutes, usage_date,
                           spend_day, est_cost, actual_cost, paid_calls,
                           started_ts, finished_ts, settled   (TTL 8d)

Migration 031's table has no cost/metadata column, so per-job cost lives in
the asr:job hash (choice reported to the owner; a column can come later).

Reservation model: the estimated cost + minutes are reserved at job creation
(refused if either the USD ceiling or the global minutes cap would be
exceeded). When the job ends, settle() replaces the estimate with the actual
cost of the provider calls made (0 if none were made).

Redis being unreachable at reservation time FAILS CLOSED (budget_unavailable)
— without the ledger the ceiling bounds nothing.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from app.services.asr import config
from app.services.asr.types import BudgetError

logger = logging.getLogger(__name__)

KILLSWITCH_KEY = "asr:killswitch"
_LEDGER_TTL = 3 * 86400
_META_TTL = 8 * 86400


def utcnow() -> datetime:
    """Single clock for ASR day boundaries — tests pin this."""
    return datetime.now(timezone.utc)


def day_key(now: datetime | None = None) -> str:
    return (now or utcnow()).astimezone(timezone.utc).strftime("%Y%m%d")


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def killswitch_on() -> bool:
    """Raises on Redis failure — callers decide fail-open/closed."""
    return bool(_r().get(KILLSWITCH_KEY))


def set_killswitch(on: bool) -> None:
    if on:
        _r().set(KILLSWITCH_KEY, "1")
    else:
        _r().delete(KILLSWITCH_KEY)


def reserve(cost_usd: float, minutes: float) -> str:
    """Check kill switch + ceiling + global minutes cap and reserve. Returns
    the ledger day. Raises BudgetError (asr_paused / spend_ceiling_reached /
    budget_unavailable). Order: kill switch, then ceiling."""
    try:
        r = _r()
        if r.get(KILLSWITCH_KEY):
            raise BudgetError(code="asr_paused")
        day = day_key()
        sk, mk = f"asr:spend:{day}", f"asr:minutes:{day}"
        new_spend = _f(r.incrbyfloat(sk, cost_usd))
        new_minutes = _f(r.incrbyfloat(mk, minutes))
        r.expire(sk, _LEDGER_TTL)
        r.expire(mk, _LEDGER_TTL)
    except BudgetError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("asr budget: redis unavailable: %s", exc)
        raise BudgetError(code="budget_unavailable") from exc

    ceiling, cap = config.spend_ceiling_usd(), config.global_daily_minutes_cap()
    if new_spend > ceiling + 1e-9 or new_minutes > cap + 1e-9:
        release(day, cost_usd, minutes)
        alert_ceiling_once(day, spend=new_spend - cost_usd, minutes=new_minutes - minutes)
        raise BudgetError(code="spend_ceiling_reached")
    return day


def release(day: str, cost_usd: float, minutes: float) -> None:
    try:
        r = _r()
        if cost_usd:
            r.incrbyfloat(f"asr:spend:{day}", -cost_usd)
        if minutes:
            r.incrbyfloat(f"asr:minutes:{day}", -minutes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("asr budget: release failed (%s): %s", day, exc)


def add_spend(day: str, cost_usd: float) -> float:
    """Add unplanned spend (e.g. a retried chunk); returns new total."""
    try:
        r = _r()
        total = _f(r.incrbyfloat(f"asr:spend:{day}", cost_usd))
        r.expire(f"asr:spend:{day}", _LEDGER_TTL)
        return total
    except Exception as exc:  # noqa: BLE001
        logger.warning("asr budget: add_spend failed: %s", exc)
        return 0.0


def would_exceed(day: str, extra_cost: float) -> bool:
    try:
        return _f(_r().get(f"asr:spend:{day}")) + extra_cost > config.spend_ceiling_usd() + 1e-9
    except Exception:  # noqa: BLE001
        return True  # fail closed: no unplanned paid retry without a ledger


def snapshot(day: str | None = None) -> dict:
    day = day or day_key()
    out = {
        "day_utc": day,
        "spend_usd": None,
        "minutes": None,
        "ceiling_usd": config.spend_ceiling_usd(),
        "minutes_cap": config.global_daily_minutes_cap(),
        "killswitch": None,
        "redis_ok": True,
    }
    try:
        r = _r()
        out["spend_usd"] = round(_f(r.get(f"asr:spend:{day}")), 6)
        out["minutes"] = round(_f(r.get(f"asr:minutes:{day}")), 3)
        out["killswitch"] = bool(r.get(KILLSWITCH_KEY))
    except Exception:  # noqa: BLE001
        out["redis_ok"] = False
    return out


def alert_ceiling_once(day: str, *, spend: float, minutes: float) -> None:
    try:
        if not _r().set(f"asr:ceiling_alerted:{day}", "1", nx=True, ex=2 * 86400):
            return
    except Exception:  # noqa: BLE001
        return  # no dedupe store -> do not risk an alert storm
    try:
        from app.core.alerts import send_admin_alert  # noqa: PLC0415
        send_admin_alert(
            "warning",
            "ASR: chạm trần chi phí ngày",
            f"Ngày {day} (UTC): ~${spend:.2f}/{config.spend_ceiling_usd():.2f} USD, "
            f"{minutes:.0f}/{config.global_daily_minutes_cap():.0f} phút. Job ASR mới bị từ chối.",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("asr ceiling alert failed: %s", exc)


# ── per-job metadata ────────────────────────────────────────────────────

def meta_set(job_id: str, **fields) -> None:
    try:
        r = _r()
        r.hset(f"asr:job:{job_id}", mapping={k: str(v) for k, v in fields.items() if v is not None})
        r.expire(f"asr:job:{job_id}", _META_TTL)
    except Exception as exc:  # noqa: BLE001
        logger.warning("asr meta_set failed for %s: %s", job_id, exc)


def meta_get(job_id: str) -> dict:
    try:
        return _r().hgetall(f"asr:job:{job_id}") or {}
    except Exception:  # noqa: BLE001
        return {}


def meta_get_many(job_ids: list[str]) -> dict[str, dict]:
    if not job_ids:
        return {}
    try:
        pipe = _r().pipeline()
        for jid in job_ids:
            pipe.hgetall(f"asr:job:{jid}")
        return {jid: (m or {}) for jid, m in zip(job_ids, pipe.execute())}
    except Exception:  # noqa: BLE001
        return {}


def settle(job_id: str, actual_cost_usd: float, *, paid_started: bool,
           already_added: float = 0.0, paid_calls: int | None = None) -> None:
    """Replace the reserved estimate by the actual cost — exactly once per
    job (HSETNX settled). `already_added` is unplanned spend (retries) that
    was pushed to the ledger during the run. Without paid calls the reserved
    minutes are released too."""
    try:
        r = _r()
        if not r.hsetnx(f"asr:job:{job_id}", "settled", "1"):
            return
        r.expire(f"asr:job:{job_id}", _META_TTL)
        meta = r.hgetall(f"asr:job:{job_id}") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("asr settle failed for %s: %s", job_id, exc)
        return
    day = meta.get("spend_day")
    if not day:
        return
    est = _f(meta.get("est_cost"))
    delta = actual_cost_usd - est - already_added
    try:
        if abs(delta) > 1e-12:
            r.incrbyfloat(f"asr:spend:{day}", delta)
        if not paid_started:
            r.incrbyfloat(f"asr:minutes:{day}", -_f(meta.get("minutes")))
        fields = {"actual_cost": f"{actual_cost_usd:.6f}", "finished_ts": f"{time.time():.3f}"}
        if paid_calls is not None:
            fields["paid_calls"] = str(paid_calls)
        r.hset(f"asr:job:{job_id}", mapping=fields)
    except Exception as exc:  # noqa: BLE001
        logger.warning("asr settle ledger update failed for %s: %s", job_id, exc)
