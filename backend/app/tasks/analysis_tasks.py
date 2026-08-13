"""
Analysis Tasks — Celery
========================
Celery tasks for async media analysis.
Runs in the 'analysis' queue on a dedicated worker.

Task: analyze_media_task(job_id)
  - Loads analysis_jobs row
  - Runs requested analyses
  - Stores results in analysis_results
  - Updates job status
  - Sends webhook if partner request (via webhook_dispatcher)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.core.celery_app import celery_app
from app.core.database import get_service_client
from app.core.structured_log import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_media_path(job: dict) -> str | None:
    """
    Resolve the local filesystem path for the media file.

    Priority:
    1. Explicit media_path column (must exist on disk)
    2. media_url pointing into /app/downloads/
    3. None (remote URL — cannot analyse in v1)
    """
    media_path: str = job.get("media_path") or ""
    if media_path:
        if os.path.isfile(media_path):
            return media_path
        logger.warning("media_path set but file not found", extra={"path": media_path})
        return None

    media_url: str = job.get("media_url") or ""
    if media_url and media_url.startswith("/app/downloads/"):
        if os.path.isfile(media_url):
            return media_url
        logger.warning("media_url points to downloads but file not found", extra={"url": media_url})
        return None

    return None


def _load_media_metadata(job: dict) -> dict:
    """
    Load yt-dlp metadata from a .info.json sidecar file if it exists.

    Looks for <media_path_without_ext>.info.json or <media_path>.info.json.
    Returns empty dict if not found or not parseable.
    """
    media_path: str = job.get("media_path") or ""
    if not media_path:
        return {}

    candidates = [
        media_path + ".info.json",
        os.path.splitext(media_path)[0] + ".info.json",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.debug("Loaded info.json sidecar", extra={"path": candidate})
                return data
            except Exception as exc:
                logger.warning(
                    "Failed to parse info.json sidecar",
                    extra={"path": candidate, "error": str(exc)},
                )
    return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Main analysis task
# ---------------------------------------------------------------------------

@celery_app.task(
    name="analyze_media_task",
    queue="analysis",
    soft_time_limit=300,
    time_limit=360,
    acks_late=True,
)
def analyze_media_task(job_id: str) -> dict:
    """
    Run all requested analyses for an analysis_jobs row.

    Steps:
      1. Load job from Supabase
      2. Mark status = 'processing'
      3. Resolve media path and load metadata
      4. Run media_analyzer.build_analysis()
      5. Run per-analysis modules based on analyses_requested
      6. Upsert results into analysis_results
      7. Mark job status = 'done'
      8. Update analysis_usage counters
      9. Fire webhook if tenant exists
    """
    db = get_service_client()

    # ------------------------------------------------------------------
    # 1. Load job
    # ------------------------------------------------------------------
    try:
        resp = (
            db.table("analysis_jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        job: dict = resp.data
    except Exception as exc:
        logger.error(
            "analyze_media_task: failed to load job",
            extra={"job_id": job_id, "error": str(exc)},
        )
        raise

    if not job:
        logger.error("analyze_media_task: job not found", extra={"job_id": job_id})
        return {"status": "not_found", "job_id": job_id}

    # ------------------------------------------------------------------
    # 2. Mark processing
    # ------------------------------------------------------------------
    try:
        db.table("analysis_jobs").update(
            {"status": "processing", "updated_at": _now_iso()}
        ).eq("id", job_id).execute()
    except Exception as exc:
        logger.warning(
            "analyze_media_task: could not set processing status",
            extra={"job_id": job_id, "error": str(exc)},
        )

    _started_at = datetime.now(timezone.utc)

    try:
        # ------------------------------------------------------------------
        # 3. Resolve media path + metadata
        # ------------------------------------------------------------------
        media_path = _get_media_path(job)
        print(f"[R34-DEBUG] job.media_path={job.get('media_path')!r} "
              f"job.media_url={job.get('media_url')!r} "
              f"resolved_media_path={media_path!r} "
              f"isfile(job.media_path)={os.path.isfile(job.get('media_path') or '')}")
        metadata = _load_media_metadata(job)

        # Merge any metadata stored on the job itself
        stored_meta: dict = job.get("metadata") or {}
        if isinstance(stored_meta, str):
            try:
                stored_meta = json.loads(stored_meta)
            except Exception:
                stored_meta = {}
        merged_meta: dict = {**stored_meta, **metadata}

        analyses_requested: list[str] = job.get("analyses_requested") or []
        if isinstance(analyses_requested, str):
            try:
                analyses_requested = json.loads(analyses_requested)
            except Exception:
                analyses_requested = [analyses_requested]

        platform: str = (
            merged_meta.get("platform")
            or merged_meta.get("extractor_key")
            or job.get("platform")
            or ""
        ).lower()
        title: str = merged_meta.get("title") or job.get("title") or ""
        description: str = merged_meta.get("description") or ""
        uploader: str = merged_meta.get("uploader") or ""

        # ------------------------------------------------------------------
        # 4. Build base analysis from media file
        # ------------------------------------------------------------------
        analysis: dict = {}
        if media_path:
            try:
                from app.core.media_analyzer import build_analysis  # type: ignore

                analysis = build_analysis(media_path)
                print(f"[R34-DEBUG] build_analysis keys={list(analysis.keys())} "
                      f"probe={analysis.get('probe')} signals_used={analysis.get('signals_used')} "
                      f"fallback_used={analysis.get('fallback_used')} warnings={analysis.get('warnings')} "
                      f"scenes_len={len(analysis.get('scenes', []))} "
                      f"audio_peaks_len={len(analysis.get('audio_peaks', []))} "
                      f"motion_len={len(analysis.get('motion', []))}")
            except Exception as exc:
                logger.warning(
                    "analyze_media_task: build_analysis failed",
                    extra={"job_id": job_id, "media_path": media_path, "error": str(exc)},
                )
        else:
            logger.info(
                "analyze_media_task: no local media path, skipping media analysis",
                extra={"job_id": job_id},
            )

        # ------------------------------------------------------------------
        # 5. Run per-analysis modules
        # ------------------------------------------------------------------
        results: dict[str, Any] = {}

        if "trim" in analyses_requested:
            try:
                from app.services.smart_trim import suggest_all_trim_modes  # type: ignore

                results["trim"] = suggest_all_trim_modes(analysis)
            except Exception as exc:
                logger.warning(
                    "analyze_media_task: trim analysis failed",
                    extra={"job_id": job_id, "error": str(exc)},
                )
                results["trim"] = {"error": str(exc)}

        if "clips" in analyses_requested:
            try:
                from app.services.smart_clips import detect_highlights  # type: ignore

                results["clips"] = detect_highlights(analysis)
            except Exception as exc:
                logger.warning(
                    "analyze_media_task: clips analysis failed",
                    extra={"job_id": job_id, "error": str(exc)},
                )
                results["clips"] = {"error": str(exc)}

        if "gif" in analyses_requested:
            try:
                from app.services.smart_gif import suggest_gif_segments  # type: ignore

                results["gif"] = suggest_gif_segments(analysis)
            except Exception as exc:
                logger.warning(
                    "analyze_media_task: gif analysis failed",
                    extra={"job_id": job_id, "error": str(exc)},
                )
                results["gif"] = {"error": str(exc)}

        if "metadata" in analyses_requested:
            try:
                from app.services.smart_metadata import (
                    clean_filename,
                    suggest_tags,
                    enhance_mp3_tags,
                )

                tag_result = suggest_tags(
                    platform=platform,
                    title=title,
                    description=description,
                    uploader=uploader,
                )
                filename_result = clean_filename(title, metadata=merged_meta)
                mp3_result = enhance_mp3_tags(merged_meta)
                results["metadata"] = {
                    "tags": tag_result,
                    "filename": filename_result,
                    "mp3_tags": mp3_result,
                }
            except Exception as exc:
                logger.warning(
                    "analyze_media_task: metadata analysis failed",
                    extra={"job_id": job_id, "error": str(exc)},
                )
                results["metadata"] = {"error": str(exc)}

        if "summary" in analyses_requested:
            try:
                from app.services.smart_summary import suggest_summary

                results["summary"] = suggest_summary(analysis, metadata=merged_meta)
            except Exception as exc:
                logger.warning(
                    "analyze_media_task: summary analysis failed",
                    extra={"job_id": job_id, "error": str(exc)},
                )
                results["summary"] = {"error": str(exc)}

        # ------------------------------------------------------------------
        # 6. Upsert analysis_results — map the per-analysis staging dict onto
        # the typed columns analysis_results actually has (see migration 013).
        # ------------------------------------------------------------------
        _warnings = [
            f"{key}: {val['error']}"
            for key, val in results.items()
            if isinstance(val, dict) and "error" in val
        ]
        _processing_ms = int(
            (datetime.now(timezone.utc) - _started_at).total_seconds() * 1000
        )
        try:
            db.table("analysis_results").upsert(
                {
                    "job_id": job_id,
                    "trim_suggestions": results.get("trim") if isinstance(results.get("trim"), list) else [],
                    "clip_suggestions": results.get("clips") if isinstance(results.get("clips"), list) else [],
                    "gif_suggestions": results.get("gif") if isinstance(results.get("gif"), list) else [],
                    "metadata_suggestions": results.get("metadata") if isinstance(results.get("metadata"), dict) else {},
                    "summary_suggestions": results.get("summary") if isinstance(results.get("summary"), dict) else {},
                    "warnings": _warnings,
                    "fallback_used": not media_path,
                    "processing_time_ms": _processing_ms,
                    "created_at": _now_iso(),
                },
                on_conflict="job_id",
            ).execute()
        except Exception as exc:
            logger.error(
                "analyze_media_task: failed to upsert analysis_results",
                extra={"job_id": job_id, "error": str(exc)},
            )
            raise

        # ------------------------------------------------------------------
        # 7. Mark job done
        # ------------------------------------------------------------------
        db.table("analysis_jobs").update(
            {"status": "done", "updated_at": _now_iso()}
        ).eq("id", job_id).execute()

        # ------------------------------------------------------------------
        # 8. Update usage counters
        # ------------------------------------------------------------------
        try:
            tenant_id: str = job.get("tenant_id") or ""
            user_id: str = job.get("user_id") or ""
            usage_row = {
                "job_id": job_id,
                "tenant_id": tenant_id or None,
                "user_id": user_id or None,
                "analyses_count": len(analyses_requested),
                "analyses_types": analyses_requested,
                "created_at": _now_iso(),
            }
            db.table("analysis_usage").insert(usage_row).execute()
        except Exception as exc:
            # Non-fatal — log and continue
            logger.warning(
                "analyze_media_task: failed to update analysis_usage",
                extra={"job_id": job_id, "error": str(exc)},
            )

        # Phase 20 metering event
        try:
            from app.core.metering import record_ai_analysis
            import asyncio
            asyncio.run(record_ai_analysis(
                user_id or "",
                job_id=job_id,
                media_minutes=None,
            ))
        except Exception:
            pass

        # ------------------------------------------------------------------
        # 9. Fire webhook if tenant
        # ------------------------------------------------------------------
        tenant_id_final: str = job.get("tenant_id") or ""
        if tenant_id_final:
            try:
                from app.services.webhook_dispatcher import emit_job_completed  # type: ignore
                import asyncio

                asyncio.run(emit_job_completed(job_id=job_id, tenant_id=tenant_id_final, results=results))
            except Exception as exc:
                logger.warning(
                    "analyze_media_task: webhook dispatch failed",
                    extra={"job_id": job_id, "tenant_id": tenant_id_final, "error": str(exc)},
                )

        logger.info(
            "analyze_media_task: completed",
            extra={"job_id": job_id, "analyses": analyses_requested},
        )
        return {"status": "done", "job_id": job_id, "analyses": list(results.keys())}

    except Exception as exc:
        # ------------------------------------------------------------------
        # Failure handler
        # ------------------------------------------------------------------
        logger.error(
            "analyze_media_task: unhandled exception",
            extra={"job_id": job_id, "error": str(exc)},
            exc_info=True,
        )
        try:
            db.table("analysis_jobs").update(
                {
                    "status": "failed",
                    "error_message": str(exc)[:2000],
                    "updated_at": _now_iso(),
                }
            ).eq("id", job_id).execute()
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# Expiry beat task
# ---------------------------------------------------------------------------

@celery_app.task(
    name="expire_analysis_jobs_task",
    queue="analysis",
    ignore_result=True,
)
def expire_analysis_jobs_task() -> None:
    """
    Delete expired analysis jobs and their results.

    Finds jobs WHERE expires_at < NOW() AND status NOT IN ('failed').
    Removes analysis_results first, then analysis_jobs.
    """
    db = get_service_client()
    now_iso = _now_iso()

    try:
        # Fetch expired job IDs
        resp = (
            db.table("analysis_jobs")
            .select("id")
            .lt("expires_at", now_iso)
            .not_.in_("status", ["failed"])
            .execute()
        )
        rows: list[dict] = resp.data or []
        if not rows:
            logger.info("expire_analysis_jobs_task: no expired jobs found")
            return

        job_ids = [r["id"] for r in rows]
        logger.info(
            "expire_analysis_jobs_task: expiring jobs",
            extra={"count": len(job_ids)},
        )

        # Delete results first (explicit, even if CASCADE is set)
        try:
            db.table("analysis_results").delete().in_("job_id", job_ids).execute()
        except Exception as exc:
            logger.warning(
                "expire_analysis_jobs_task: failed to delete analysis_results",
                extra={"error": str(exc)},
            )

        # Delete jobs
        db.table("analysis_jobs").delete().in_("id", job_ids).execute()

        logger.info(
            "expire_analysis_jobs_task: deleted expired jobs",
            extra={"count": len(job_ids)},
        )

    except Exception as exc:
        logger.error(
            "expire_analysis_jobs_task: failed",
            extra={"error": str(exc)},
            exc_info=True,
        )
