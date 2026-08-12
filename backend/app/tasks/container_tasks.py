"""
Container Discovery Tasks — Phase 25 PR2
==========================================
Celery tasks for async container discovery + lazy section expansion.

These tasks are separate from video_tasks.py to keep concern boundaries clear.
They use the PR2 Redis key design (container_cache.py) and write
DiscoveryJobSnapshot snapshots so the frontend can poll stage-by-stage.

Task names are registered as:
  app.tasks.container_tasks.discover_container_task
  app.tasks.container_tasks.expand_container_section_task

The legacy `discover_container_task` in video_tasks.py remains for
backward-compat with any already-queued Celery jobs.

Stage flow:
  queued → resolving → discovering → assemble_sections → finalize → success
                                           ↓ (on partial timeout)
                                         partial
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict
from typing import Optional

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.schemas.container_discovery import (
    DiscoveryError,
    DiscoveryJobSnapshot,
    DiscoveryJobStatus,
    DiscoveryStage,
    DiscoveryStats,
    STAGE_PROGRESS,
)
from app.services.container_cache import (
    cache_expand_result,
    cleanup_stale_lock,
    get_active_job_for_url,
    get_expand_result,
    get_job,
    get_lock_holder,
    lock_key,
    mark_job_stage,
    patch_job,
    release_discovery_lock,
    url_hash,
)
from app.services.container_discovery import (
    cache_container,
    get_cached_container,
    track_container_metric,
    update_container_fields,
)

logger = logging.getLogger(__name__)


def _make_summary_from_meta(meta) -> dict:
    """Extract a lightweight summary dict from ContainerMeta for the snapshot."""
    return {
        "container_id": meta.container_id,
        "platform": meta.platform,
        "container_type": meta.container_type,
        "title": meta.title,
        "description": meta.description,
        "avatar": meta.avatar,
        "item_count": meta.item_count,
        "status": meta.status,
        "support_level": meta.support_level,
        "warning": meta.warning,
    }


def _make_stats_from_meta(meta) -> DiscoveryStats:
    sections_ready = sum(1 for s in meta.sections if s.items_loaded)
    items_loaded = sum(len(s.items) for s in meta.sections)
    expandables = sum(1 for s in meta.sections if not s.items_loaded and s.children)
    return DiscoveryStats(
        sections_ready=sections_ready,
        sections_total=len(meta.sections),
        items_loaded=items_loaded,
        items_estimated=meta.item_count,
        expandables=expandables,
    )


@celery_app.task(
    name="app.tasks.container_tasks.discover_container_task",
    bind=True,
    max_retries=2,
    soft_time_limit=120,
    time_limit=150,
    queue="bulk",
)
def discover_container_task(
    self,
    job_id: str,
    url: str,
    container_id: str,
    max_items: int = 100,
    include_sections: Optional[list] = None,
    _raw_input: str = "",
) -> None:
    """
    Async container discovery with stage-by-stage progress tracking.

    Writes DiscoveryJobSnapshot to Redis at each stage so frontend can poll.
    On completion, also updates the ContainerMeta via container_discovery.py
    (backward-compat with GET /container/{container_id}).
    """
    from app.services.container_expanders import get_expander

    def _stage(stage: DiscoveryStage, msg: str, status: Optional[DiscoveryJobStatus] = None):
        mark_job_stage(
            job_id,
            stage=stage,
            progress_pct=STAGE_PROGRESS.get(stage, 50),
            message=msg,
            status=status.value if status else None,
        )

    _started_at = time.time()
    logger.info("discover_start job_id=%s url=%.80s retry=%d", job_id, url, self.request.retries)

    try:
        patch_job(job_id, processing_started_at=_started_at)

        # ── Stage: resolving ─────────────────────────────────────────────────
        _stage(DiscoveryStage.resolve_capability,
               "Đang nhận diện nguồn…",
               DiscoveryJobStatus.resolving)

        meta = get_cached_container(url)
        platform = meta.platform if meta else "unknown"
        source_type = meta.container_type if meta else "unknown"
        logger.debug("discover_resolved job_id=%s platform=%s source_type=%s", job_id, platform, source_type)

        # ── Stage: load cache ────────────────────────────────────────────────
        _stage(DiscoveryStage.load_cache, "Đang kiểm tra cache…")

        expander = get_expander(platform)
        if expander is None:
            patch_job(
                job_id,
                status=DiscoveryJobStatus.failed,
                progress_pct=0,
                message=f"Platform '{platform}' chưa hỗ trợ container.",
                error=DiscoveryError(
                    code="C030",
                    message=f"No expander for platform: {platform}",
                    retryable=False,
                ).model_dump(),
            )
            update_container_fields(url, status="failed", error_code="C030",
                                     error_message=f"Platform '{platform}' chưa có expander.")
            track_container_metric(platform, "discover_fail")
            release_discovery_lock(url)
            return

        # ── Stage: fetch summary ─────────────────────────────────────────────
        _stage(DiscoveryStage.fetch_summary,
               "Đang lấy thông tin chính…",
               DiscoveryJobStatus.discovering)

        update_container_fields(url, status="discovering")

        # ── Stage: fetch preview ─────────────────────────────────────────────
        _stage(DiscoveryStage.fetch_preview, "Đang dựng danh sách xem trước…")

        result = expander.discover(url, max_items=max_items, include_sections=include_sections)

        # ── Stage: assemble sections ──────────────────────────────────────────
        _stage(DiscoveryStage.assemble_sections, "Đang xếp nội dung vào danh sách…")

        # Preserve stable container_id
        result.container_id = container_id
        result.discovered_at = time.time()

        # Save full ContainerMeta (backward compat)
        cache_container(result)

        # ── Stage: finalize ──────────────────────────────────────────────────
        is_partial = result.status == "partial"
        final_status = DiscoveryJobStatus.partial if is_partial else DiscoveryJobStatus.success

        stats = _make_stats_from_meta(result)
        summary = _make_summary_from_meta(result)

        # Sections as plain dicts for snapshot
        sections_dicts = []
        for s in result.sections:
            sd = asdict(s)
            # Truncate items to first 5 in snapshot (full data is in ContainerMeta)
            sd["items"] = sd.get("items", [])[:5]
            sections_dicts.append(sd)

        patch_job(
            job_id,
            status=final_status,
            progress_pct=100,
            stage=DiscoveryStage.finalize,
            message="Đã hoàn tất." if not is_partial else "Hoàn thành một phần.",
            partial=is_partial,
            summary=summary,
            sections=sections_dicts,
            stats=stats.model_dump(),
        )

        elapsed = time.time() - _started_at
        logger.info(
            "discover_success job_id=%s platform=%s status=%s items=%d time=%.1fs",
            job_id, platform, final_status, stats.items_loaded, elapsed,
        )
        track_container_metric(platform, "discover_ok")
        release_discovery_lock(url)

    except SoftTimeLimitExceeded:
        _platform = locals().get("platform", "unknown")
        elapsed = time.time() - _started_at
        logger.warning("discover_timeout job_id=%s platform=%s time=%.1fs", job_id, _platform, elapsed)
        meta_now = get_cached_container(url)
        partial_summary = _make_summary_from_meta(meta_now) if meta_now else None
        patch_job(
            job_id,
            status=DiscoveryJobStatus.partial,
            progress_pct=70,
            message="Quét chưa hoàn thành (timeout). Kết quả hiện có vẫn dùng được.",
            partial=True,
            terminal_reason="timeout",
            summary=partial_summary,
            warnings=["container_discovery_timeout"],
        )
        update_container_fields(url, status="partial",
                                error_code="C051",
                                error_message="Timeout — partial results saved.")
        track_container_metric(_platform, "discover_timeout")
        release_discovery_lock(url)

    except Exception as exc:
        _platform = locals().get("platform", "unknown")
        err_msg = str(exc)[:200]
        is_retryable = self.request.retries < self.max_retries
        logger.error(
            "discover_error job_id=%s platform=%s retry=%s/%s exc=%.200s",
            job_id, _platform, self.request.retries, self.max_retries, err_msg,
        )
        patch_job(
            job_id,
            status=DiscoveryJobStatus.failed if not is_retryable else DiscoveryJobStatus.discovering,
            progress_pct=0,
            message=f"Lỗi: {err_msg}",
            terminal_reason="error" if not is_retryable else None,
            error=DiscoveryError(
                code="C001",
                message=err_msg,
                retryable=is_retryable,
            ).model_dump(),
        )
        try:
            update_container_fields(url, status="failed",
                                    error_code="C001", error_message=err_msg)
            track_container_metric(_platform, "discover_fail")
        except Exception:
            pass
        release_discovery_lock(url)
        raise self.retry(exc=exc, countdown=5)


@celery_app.task(
    name="app.tasks.container_tasks.expand_container_section_task",
    bind=True,
    max_retries=1,
    soft_time_limit=60,
    time_limit=75,
    queue="bulk",
)
def expand_container_section_task(
    self,
    container_id: str,
    url: str,
    section_key: str,
    child_id: str = "",
    cursor: str = "",
) -> None:
    """
    Lazy expand a section or child collection of a discovered container.

    Result is written to:
    1. container_cache expand_key (fast future lookups)
    2. ContainerMeta sections (so GET /container/{id} returns full data)
    """
    from dataclasses import asdict
    from app.services.container_expanders import get_expander

    try:
        # Cache hit check
        cached = get_expand_result(container_id, section_key + child_id, cursor)
        if cached is not None:
            track_container_metric("cached", "expand_ok")
            return  # Already in cache, nothing to do

        meta = get_cached_container(url)
        if not meta:
            return  # Container expired — can't expand

        expander = get_expander(meta.platform)
        if expander is None or not hasattr(expander, "expand_section"):
            return

        new_items = expander.expand_section(url, section_key, child_id)

        # Update ContainerMeta section
        for s in meta.sections:
            if s.key == section_key:
                s.items = new_items
                s.items_loaded = True
                s.item_count = len(new_items)
                break
        cache_container(meta)

        # Also write to expand cache for sub-second future lookups
        cache_expand_result(
            container_id,
            section_key + child_id,
            cursor,
            [asdict(i) for i in new_items],
        )

        track_container_metric(meta.platform, "expand_ok")

    except SoftTimeLimitExceeded:
        logger.warning("expand_timeout container_id=%s section=%s", container_id[:12], section_key)
        track_container_metric("unknown", "expand_timeout")

    except Exception as exc:
        logger.error("expand_error container_id=%s section=%s exc=%.200s", container_id[:12], section_key, str(exc))
        track_container_metric("unknown", "expand_fail")
        raise self.retry(exc=exc, countdown=3)


# ── Periodic maintenance ──────────────────────────────────────────────────────

@celery_app.task(
    name="app.tasks.container_tasks.cleanup_stale_container_jobs",
    queue="light",
    soft_time_limit=30,
    time_limit=45,
)
def cleanup_stale_container_jobs() -> dict:
    """
    Periodic: clean up stale discovery locks whose holder jobs are no longer active.

    A lock becomes stale when:
    - The Celery worker died mid-task → job snapshot expired but lock is still set
    - Max retries exhausted without releasing lock

    Safe to run every 10 minutes. Does NOT touch healthy in-flight jobs.
    """
    from app.core.redis_client import get_redis

    cleaned_locks = 0
    checked = 0

    try:
        rc = get_redis()
        # Scan all container:lock:* keys
        cursor = 0
        while True:
            cursor, keys = rc.scan(cursor, match="container:lock:*", count=200)
            for key in keys:
                checked += 1
                raw_holder = rc.get(key)
                if raw_holder is None:
                    continue
                holder = raw_holder.decode() if isinstance(raw_holder, bytes) else raw_holder
                job = get_job(holder)
                if job is None or job.status in (
                    DiscoveryJobStatus.success,
                    DiscoveryJobStatus.partial,
                    DiscoveryJobStatus.failed,
                    DiscoveryJobStatus.expired,
                ):
                    rc.delete(key)
                    cleaned_locks += 1
                    logger.info("maintenance stale_lock_removed key=%s holder=%s", key, holder)
            if cursor == 0:
                break
    except Exception as exc:
        logger.error("maintenance cleanup_failed exc=%.200s", str(exc))
        return {"error": str(exc)}

    logger.info("maintenance done checked=%d cleaned_locks=%d", checked, cleaned_locks)
    return {"checked": checked, "cleaned_locks": cleaned_locks}
