"""
Job lifecycle helpers shared by the API, the Celery task and the sweeper:
quota refund (D1) and the single "transition to failed" path.

Refund idempotency comes from the DB itself: fail_job() updates the row
only while it is still in a non-terminal status, and only the caller whose
update actually matched a row refunds. A job can therefore be refunded at
most once even if the task, the sweeper and a DELETE race each other.
"""
from __future__ import annotations

import logging
from datetime import timezone

from app.services.asr import budget
from app.services.asr.types import ERROR_MESSAGES_VI

logger = logging.getLogger(__name__)

NON_TERMINAL = ("queued", "extracting_audio", "transcribing")
REFUND_RPC = "refund_transcript_asr_usage"


def now_iso() -> str:
    return budget.utcnow().astimezone(timezone.utc).isoformat()


def format_error(code: str, message: str | None = None) -> str:
    """error_message storage format: "[code] message" (031 has no code column)."""
    return f"[{code}] {message or ERROR_MESSAGES_VI.get(code, code)}"[:2000]


def parse_error(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    if raw.startswith("[") and "] " in raw:
        code, msg = raw[1:].split("] ", 1)
        if code and " " not in code:
            return code, msg
    return None, raw


def refund_quota(db, quota_key: str | None, minutes: float, usage_date: str | None) -> bool:
    """Give back reserved per-user minutes via migration 033's RPC. If the
    function does not exist yet (migration not applied) this logs and
    returns False — the job still fails cleanly, only the refund is lost."""
    if not quota_key or not minutes or minutes <= 0:
        return False
    params = {"p_user_id": quota_key, "p_minutes": round(float(minutes), 4)}
    if usage_date:
        params["p_usage_date"] = usage_date
    try:
        db.rpc(REFUND_RPC, params).execute()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "asr refund skipped for %s (%.2f min): %s — apply migration 033_transcript_asr_refund.sql",
            quota_key, minutes, exc,
        )
        return False


def refund_info_for(job_id: str, row: dict | None) -> tuple[str | None, float, str | None]:
    """(quota_key, minutes, usage_date) — from the Redis job meta written at
    creation, falling back to the row (user_id / duration / created date)."""
    meta = budget.meta_get(job_id)
    if meta.get("quota_key"):
        try:
            minutes = float(meta.get("minutes") or 0)
        except ValueError:
            minutes = 0.0
        return meta["quota_key"], minutes, meta.get("usage_date")
    row = row or {}
    try:
        minutes = float(row.get("duration_sec") or 0) / 60.0
    except (TypeError, ValueError):
        minutes = 0.0
    created = str(row.get("created_at") or "")[:10] or None
    return row.get("user_id"), minutes, created


def fail_job(db, job_id: str, code: str, message: str | None = None, *, row: dict | None = None) -> bool:
    """Transition a non-terminal job to failed and refund its quota. Returns
    True when this call performed the transition (and so the refund)."""
    try:
        resp = (
            db.table("transcript_asr_jobs")
            .update({"status": "failed", "error_message": format_error(code, message), "updated_at": now_iso()})
            .eq("id", job_id)
            .in_("status", list(NON_TERMINAL))
            .execute()
        )
        updated = resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.error("asr fail_job update failed for %s: %s", job_id, exc)
        return False
    if not updated:
        return False
    current = row or (updated[0] if isinstance(updated, list) and updated and isinstance(updated[0], dict) else None)
    quota_key, minutes, usage_date = refund_info_for(job_id, current)
    refund_quota(db, quota_key, minutes, usage_date)
    return True
