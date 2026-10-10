"""
Channel Watch Celery tasks — Phase 33A, task #6257.

  watch_scan_tick           beat, every 60 s: enqueue due sources (batch of
                            WATCH_SCAN_BATCH_PER_MINUTE) + a delivery run when
                            deliveries are pending. No-op unless WATCH_ENABLED
                            and not WATCH_KILL_SWITCH.
  scan_watch_source         one source scan (app.services.channel_watch.scan_source)
  deliver_watch_deliveries  one push digest per subscription

Queue: all three go to the default 'celery' queue (see celery_app.task_routes).
It is consumed by the production worker (docker-entrypoint.sh -Q …,celery) and
by every worker definition in the compose files, unlike 'light'/'bulk'; the
work is short and network-bound (one free listing request per scan, bounded
follow-up), and adding a queue would need a deploy-config change to be
consumed at all.
"""

from __future__ import annotations

import logging

from app.core.celery_app import celery_app
from app.core import watch_config as cfg

logger = logging.getLogger(__name__)


@celery_app.task(name="watch_scan_tick", ignore_result=True)
def watch_scan_tick():
    if not cfg.scanning_allowed():
        return {"enqueued": 0, "skipped": "disabled"}
    from app.services import channel_watch as cw

    try:
        return cw.tick(
            enqueue_scan=lambda sid: scan_watch_source.delay(sid),
            enqueue_delivery=lambda: deliver_watch_deliveries.delay(),
        )
    except Exception as e:
        logger.warning("[Watch] tick failed: %s", type(e).__name__)
        return {"enqueued": 0, "error": type(e).__name__}


@celery_app.task(name="scan_watch_source", ignore_result=True,
                 soft_time_limit=150, time_limit=180)
def scan_watch_source(source_id: str):
    from app.services import channel_watch as cw

    try:
        result = cw.scan_source(source_id)
    finally:
        try:
            cw.release_enqueued(cw._rc(), source_id)
        except Exception:
            pass
    if result.get("deliveries"):
        deliver_watch_deliveries.delay(source_id=source_id)
    return result


@celery_app.task(name="deliver_watch_deliveries", ignore_result=True,
                 soft_time_limit=120, time_limit=150)
def deliver_watch_deliveries(source_id: str | None = None):
    from app.services import channel_watch as cw

    return cw.deliver_pending(source_id=source_id)
