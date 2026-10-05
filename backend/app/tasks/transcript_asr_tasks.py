"""
Transcript ASR Tasks — Celery
================================
Auto-generate a subtitle file from a downloaded video's audio via the
configured ASR provider (app.services.asr: Gemini or Whisper). Runs on the
'analysis' queue (in docker-compose.small.yml that queue is consumed by the
downloads worker — a long ASR job occupies one of its slots).

Task: transcribe_video_task(job_id)
  - Refuses to run twice: only a job in status 'queued' is processed. A job
    found in extracting_audio/transcribing was interrupted (worker died and
    the message was redelivered) — it is failed + refunded, NEVER re-billed.
  - ffmpeg mono mp3 48k → chunks (ASR_CHUNK_SEC, default 600s) → provider →
    offsets merged → SRT (+ JSON sidecar with segments/speakers).
  - Time limits from env (ASR_TASK_SOFT_LIMIT_SEC / ASR_TASK_HARD_LIMIT_SEC,
    default 1500 / 1620 — sized for a 45-min job, below the broker's 1800s
    visibility timeout).
  - Self-requeue on soft timeout ONLY if no provider call was made yet.
    After a paid call has started there is no automatic retry (D3).
  - Any failure → status failed with "[code] message" + per-user quota
    refund (D1). Spend ledger is settled to the actual cost.

Task: sweep_stuck_transcript_asr_jobs_task — beat, every 10 min: non-terminal
jobs not updated for longer than the hard limit + 5 min → failed + refund.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.database import get_service_client
from app.core.structured_log import get_logger
from app.services.asr import budget, config
from app.services.asr.jobs import NON_TERMINAL, fail_job, now_iso
from app.services.asr.types import AsrError, NoSpeechError

logger = get_logger(__name__)

_MAX_TRANSIENT_RETRIES = 3
_MAX_DURATION_SEC = 45 * 60

_DOWNLOADS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)


def _now_iso() -> str:
    return now_iso()


def sidecar_json_path(result_path: str) -> str:
    base, _ = os.path.splitext(result_path)
    return base + ".json"


def _update(db, job_id: str, fields: dict) -> None:
    fields = {**fields, "updated_at": _now_iso()}
    db.table("transcript_asr_jobs").update(fields).eq("id", job_id).execute()


@celery_app.task(
    name="transcribe_video_task",
    queue="analysis",
    soft_time_limit=config.task_soft_limit_sec(),
    time_limit=config.task_hard_limit_sec(),
    acks_late=True,
)
def transcribe_video_task(job_id: str) -> dict:
    db = get_service_client()

    try:
        resp = db.table("transcript_asr_jobs").select("*").eq("id", job_id).single().execute()
        job: dict = resp.data
    except Exception as exc:
        logger.error("transcribe_video_task: failed to load job", extra={"job_id": job_id, "error": str(exc)})
        raise

    if not job:
        logger.error("transcribe_video_task: job not found", extra={"job_id": job_id})
        return {"status": "not_found", "job_id": job_id}

    status = job.get("status")
    if status in ("done", "failed"):
        return {"status": f"already_{status}", "job_id": job_id}
    if status != "queued":
        # A previous delivery got past 'queued' and never finished: the worker
        # died. Running again could bill the provider twice — fail + refund.
        fail_job(db, job_id, "interrupted", row=job)
        budget.settle(job_id, float(budget.meta_get(job_id).get("est_cost") or 0), paid_started=True)
        return {"status": "failed", "job_id": job_id, "error_code": "interrupted"}

    from app.services.asr import get_provider  # noqa: PLC0415
    from app.services.asr.chunking import extract_audio, probe_duration_sec  # noqa: PLC0415
    from app.services.asr.pipeline import CostTracker, transcribe_chunked  # noqa: PLC0415
    from app.core.local_download import new_download_path  # noqa: PLC0415

    meta = budget.meta_get(job_id)
    tracker = CostTracker(spend_day=meta.get("spend_day"))
    budget.meta_set(job_id, started_ts=f"{datetime.now(timezone.utc).timestamp():.3f}")
    diarize = config.diarization_enabled()

    video_path = job["video_local_path"]
    # video_local_path points into the SHARED downloads dir — never delete it.
    audio_path = new_download_path(_DOWNLOADS_DIR, "asr_audio_", ".mp3")
    requeued = False

    try:
        if not os.path.isfile(video_path):
            raise AsrError(code="source_missing")

        provider = get_provider(meta.get("provider") or None)

        duration_sec = probe_duration_sec(video_path)
        if duration_sec > _MAX_DURATION_SEC:
            raise AsrError(
                f"Video dài {duration_sec/60:.1f} phút, vượt giới hạn {_MAX_DURATION_SEC//60} phút cho tạo phụ đề tự động.",
                code="too_long",
            )

        _update(db, job_id, {"status": "extracting_audio", "duration_sec": duration_sec, "progress_pct": 10})
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        extract_audio(video_path, audio_path)

        _update(db, job_id, {"status": "transcribing", "progress_pct": 20})

        def _progress(done: int, total: int) -> None:
            try:
                _update(db, job_id, {"progress_pct": 20 + int(75 * done / max(total, 1))})
            except Exception:  # noqa: BLE001
                pass

        result = transcribe_chunked(
            provider, audio_path, duration_sec,
            work_dir=os.path.dirname(audio_path), tracker=tracker,
            diarize=diarize, progress_cb=_progress,
        )
        if not result.segments:
            raise NoSpeechError()

        from app.services.asr_service import segments_to_cues  # noqa: PLC0415
        from app.services.subtitle_format import serialize_srt  # noqa: PLC0415

        result_path = new_download_path(_DOWNLOADS_DIR, "asr_result_", ".srt")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(serialize_srt(segments_to_cues(result.segments, with_speaker=diarize)))
        with open(sidecar_json_path(result_path), "w", encoding="utf-8") as f:
            json.dump({
                "language": result.language,
                "duration_sec": duration_sec,
                "provider": provider.name,
                "segments": [
                    {"start": s.start, "end": s.end, "text": s.text,
                     **({"speaker": s.speaker} if diarize and s.speaker else {})}
                    for s in result.segments
                ],
            }, f, ensure_ascii=False)

        _update(db, job_id, {
            "status": "done",
            "result_path": result_path,
            "detected_language": result.language,
            "progress_pct": 100,
            "error_message": None,
        })
        logger.info("transcribe_video_task: completed",
                    extra={"job_id": job_id, "cue_count": len(result.segments), "cost": tracker.actual_cost})
        return {"status": "done", "job_id": job_id, "cue_count": len(result.segments)}

    except SoftTimeLimitExceeded:
        retry_count = int(job.get("timeout_retry_count") or 0)
        if not tracker.paid_started and retry_count < _MAX_TRANSIENT_RETRIES:
            try:
                _update(db, job_id, {
                    "status": "queued",
                    "timeout_retry_count": retry_count + 1,
                    "error_message": f"Tạm dừng (timeout), tự động thử lại ({retry_count + 1}/{_MAX_TRANSIENT_RETRIES})...",
                })
                transcribe_video_task.apply_async(args=[job_id])
                requeued = True
                return {"status": "requeued", "job_id": job_id, "retry_count": retry_count + 1}
            except Exception as requeue_exc:  # noqa: BLE001
                logger.error("transcribe_video_task: requeue failed", extra={"error": str(requeue_exc)})
        fail_job(db, job_id, "timeout", row=job)
        return {"status": "failed", "job_id": job_id, "error_code": "timeout"}

    except AsrError as exc:
        logger.warning("transcribe_video_task: failed",
                       extra={"job_id": job_id, "error_code": exc.code, "error": exc.message})
        fail_job(db, job_id, exc.code, exc.message, row=job)
        return {"status": "failed", "job_id": job_id, "error_code": exc.code}

    except Exception as exc:  # noqa: BLE001
        logger.error("transcribe_video_task: unhandled exception",
                     extra={"job_id": job_id, "error": str(exc)}, exc_info=True)
        fail_job(db, job_id, "internal_error", str(exc)[:500], row=job)
        return {"status": "failed", "job_id": job_id, "error_code": "internal_error"}

    finally:
        if not requeued:
            budget.settle(job_id, tracker.actual_cost, paid_started=tracker.paid_started,
                          already_added=tracker.extra_reserved, paid_calls=tracker.paid_calls)
        try:
            if os.path.isfile(audio_path):
                os.remove(audio_path)
        except Exception:  # noqa: BLE001
            pass


@celery_app.task(name="sweep_stuck_transcript_asr_jobs_task", queue="analysis", ignore_result=True)
def sweep_stuck_transcript_asr_jobs_task() -> dict:
    """Fail + refund jobs stuck in a non-terminal status past the hard limit
    (hard kill, lost message, worker gone). Jobs still 'queued'/'extracting'
    never made a provider call, so their reserved spend is released; a
    'transcribing' one keeps its estimate as spent (conservative)."""
    db = get_service_client()
    cutoff = (budget.utcnow() - timedelta(seconds=config.stuck_after_sec())).isoformat()
    swept = 0
    try:
        resp = (
            db.table("transcript_asr_jobs")
            .select("id, user_id, status, duration_sec, created_at, updated_at")
            .in_("status", list(NON_TERMINAL))
            .lt("updated_at", cutoff)
            .limit(200)
            .execute()
        )
        rows = resp.data or []
    except Exception as exc:  # noqa: BLE001
        logger.error("sweep_stuck_transcript_asr_jobs_task: query failed", extra={"error": str(exc)})
        return {"swept": 0}

    for row in rows:
        if fail_job(db, row["id"], "stuck_timeout", row=row):
            swept += 1
            meta = budget.meta_get(row["id"])
            paid = row.get("status") == "transcribing"
            budget.settle(row["id"], float(meta.get("est_cost") or 0) if paid else 0.0, paid_started=paid)
    if swept:
        logger.warning("sweep_stuck_transcript_asr_jobs_task: swept", extra={"count": swept})
    return {"swept": swept}


@celery_app.task(name="expire_transcript_asr_jobs_task", queue="analysis", ignore_result=True)
def expire_transcript_asr_jobs_task() -> None:
    """Delete expired transcript_asr_jobs rows and their result files (never
    the source video — that belongs to the download feature's own lifecycle)."""
    db = get_service_client()
    now_iso_ = _now_iso()

    try:
        resp = (
            db.table("transcript_asr_jobs")
            .select("id, result_path")
            .lt("expires_at", now_iso_)
            .execute()
        )
        rows: list[dict] = resp.data or []
        if not rows:
            return

        for row in rows:
            path = row.get("result_path")
            if path:
                for p in (path, sidecar_json_path(path)):
                    try:
                        if os.path.isfile(p):
                            os.remove(p)
                    except Exception:
                        pass

        db.table("transcript_asr_jobs").delete().in_("id", [r["id"] for r in rows]).execute()
        logger.info("expire_transcript_asr_jobs_task: deleted expired jobs", extra={"count": len(rows)})
    except Exception as exc:
        logger.error("expire_transcript_asr_jobs_task: failed", extra={"error": str(exc)}, exc_info=True)
