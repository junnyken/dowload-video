"""
Supabase Keep-Alive
======================
Free-tier Supabase projects auto-pause after 7 days with no API activity
(and can eventually be reclaimed) — this is exactly what silently wiped the
production database once already. A trivial read every 2 days, well under
that threshold, keeps the project "active" with near-zero cost.
"""
from __future__ import annotations

from app.core.celery_app import celery_app
from app.core.database import get_service_client
from app.core.structured_log import get_logger

logger = get_logger(__name__)


@celery_app.task(name="supabase_keepalive_ping", ignore_result=True)
def supabase_keepalive_ping() -> None:
    db = get_service_client()
    try:
        db.table("user_usage").select("id").limit(1).execute()
        logger.info("supabase_keepalive_ping: ok")
    except Exception as exc:
        # Never let this page anyone or crash beat — worst case is a missed
        # ping, which just gets retried on the next scheduled run.
        logger.warning("supabase_keepalive_ping: failed", extra={"error": str(exc)})
