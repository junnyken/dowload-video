"""
Partner Job Bridge — Celery
============================
`process_video_task` (the core download engine) only knows how to read/write
the `download_jobs` table — it has no concept of the Phase 16 Partner API's
separate `partner_jobs` table. Rather than invasively thread a "target table"
parameter through the ~1800-line, heavily-tested `process_video_task`, this
module bridges the two: `partner.submit_job()` dispatches the unmodified
`process_video_task` against a normal shadow `download_jobs` row, then
dispatches `sync_partner_job_task` here to poll that row and mirror its
terminal state (+ webhook) into `partner_jobs`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.core.celery_app import celery_app
from app.core.database import get_service_client
from app.core.structured_log import get_logger

logger = get_logger(__name__)

_POLL_INTERVAL_SEC = 5
# process_video_task's own hard time limit is 6 min (VIDEO_TASK_HARD_LIMIT) —
# poll a bit past that so we don't give up before it could still finish.
_MAX_POLL_ATTEMPTS = 90  # 90 * 5s = 7.5 min


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@celery_app.task(
    name="sync_partner_job_task",
    bind=True,
    queue="light",
)
def sync_partner_job_task(self, partner_job_id: str, download_job_id: str, tenant_id: str) -> None:
    """Poll the shadow download_jobs row; mirror its terminal state into partner_jobs + fire webhook."""
    db = get_service_client()

    try:
        dj_resp = (
            db.table("download_jobs")
            .select("status, direct_mp4_url, local_file_path, title, file_size_mb, "
                    "thumbnail_url, error_message")
            .eq("id", download_job_id)
            .execute()
        )
        row = dj_resp.data[0] if dj_resp.data else None
    except Exception as exc:
        logger.warning(
            "sync_partner_job_task: failed to read download_jobs, retrying",
            extra={"partner_job_id": partner_job_id, "error": str(exc)},
        )
        raise self.retry(countdown=_POLL_INTERVAL_SEC, max_retries=_MAX_POLL_ATTEMPTS)

    status = (row or {}).get("status")
    if status not in ("success", "failed"):
        # Still running (or shadow row not committed yet) — poll again later.
        raise self.retry(countdown=_POLL_INTERVAL_SEC, max_retries=_MAX_POLL_ATTEMPTS)

    if status == "success":
        result = {
            "direct_mp4_url": row.get("direct_mp4_url") or row.get("local_file_path"),
            "title": row.get("title"),
            "file_size_mb": row.get("file_size_mb"),
            "thumbnail_url": row.get("thumbnail_url"),
        }
        update = {"status": "done", "result": result, "updated_at": _now_iso()}
        event_type = "job.completed"
        payload = {"job_id": partner_job_id, "status": "done", "result": result}
    else:
        error_message = (row or {}).get("error_message") or "Download failed"
        update = {"status": "failed", "error": error_message, "updated_at": _now_iso()}
        event_type = "job.failed"
        payload = {"job_id": partner_job_id, "status": "failed", "error": error_message}

    try:
        db.table("partner_jobs").update(update).eq("id", partner_job_id).execute()
    except Exception as exc:
        logger.error(
            "sync_partner_job_task: failed to update partner_jobs",
            extra={"partner_job_id": partner_job_id, "error": str(exc)},
        )

    try:
        from app.services.webhook_dispatcher import dispatch_event
        asyncio.run(dispatch_event(tenant_id, event_type, payload))
    except Exception as exc:
        logger.warning(
            "sync_partner_job_task: webhook dispatch failed",
            extra={"partner_job_id": partner_job_id, "event": event_type, "error": str(exc)},
        )
