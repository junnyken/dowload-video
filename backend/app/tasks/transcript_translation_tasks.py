"""
Transcript Translation Tasks — Celery
=======================================
Celery tasks for async .srt/.vtt transcript translation.
Runs on the 'analysis' queue (shared with app.tasks.analysis_tasks — no
worker in docker-compose.yml consumes a standalone 'translation' queue).

Task: translate_transcript_task(job_id)
  - Loads transcript_translation_jobs row
  - Parses input_path (format from source_format)
  - Detects source language, translates cues in bounded chunks
  - Writes translated output to result_path
  - Updates status/progress across the whole flow so polling clients see
    live progress, not just a final jump to done/failed.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.database import get_service_client
from app.core.structured_log import get_logger

logger = get_logger(__name__)

# Failures worth retrying from the T3-B checkpoint rather than treated as
# permanent: SoftTimeLimitExceeded (job legitimately needs more than one run
# to finish) and TranslationAlignmentError (a single chunk's malformed LLM
# response — often succeeds on a fresh attempt). Bounded so a job that keeps
# failing the same way every time doesn't requeue forever.
_MAX_TRANSIENT_RETRIES = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_path_for(input_path: str, source_format: str) -> str:
    """Derive the translated output path as a sibling file next to input_path."""
    directory = os.path.dirname(input_path)
    return os.path.join(directory, f"translated.{source_format}")


def _progress_file_for(input_path: str) -> str:
    """
    On-disk checkpoint for chunk-level resume — one JSON line appended per
    completed chunk: {"chunk_index": int, "translations": [str, ...]}.
    An append-only file (not a DB column) so each chunk's checkpoint write is
    O(1) rather than rewriting an ever-growing JSONB blob on every chunk.
    """
    directory = os.path.dirname(input_path)
    return os.path.join(directory, "progress.ndjson")


def _append_chunk_progress(progress_path: str, chunk_index: int, translations: list[str]) -> None:
    """Best-effort — a failed checkpoint write must not fail the chunk that
    already translated successfully; it just means a future resume can't
    skip this chunk (falls back to the cache, which still avoids re-billing
    it in the common case)."""
    try:
        with open(progress_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"chunk_index": chunk_index, "translations": translations}) + "\n")
    except Exception as exc:
        logger.warning(
            "transcript_translation: failed to write resume checkpoint",
            extra={"progress_path": progress_path, "chunk_index": chunk_index, "error": str(exc)},
        )


def _load_resume_state(progress_path: str, resume_chunk_index: int) -> list[str] | None:
    """
    Reconstruct translated_texts for chunks [0, resume_chunk_index) from the
    checkpoint file. Returns None (caller must restart from chunk 0) if the
    file is missing, unreadable, or doesn't cover every expected chunk —
    never raises, never returns a partial/misordered result silently.
    """
    if resume_chunk_index <= 0:
        return []
    try:
        by_index: dict[int, list[str]] = {}
        with open(progress_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                by_index[entry["chunk_index"]] = entry["translations"]

        translated_texts: list[str] = []
        for i in range(resume_chunk_index):
            if i not in by_index:
                logger.warning(
                    "transcript_translation: resume checkpoint missing chunk %d, restarting from 0",
                    i,
                )
                return None
            translated_texts.extend(by_index[i])
        return translated_texts
    except Exception as exc:
        logger.warning(
            "transcript_translation: failed to read resume checkpoint, restarting from 0",
            extra={"progress_path": progress_path, "error": str(exc)},
        )
        return None


# ---------------------------------------------------------------------------
# Main translation task
# ---------------------------------------------------------------------------

@celery_app.task(
    name="translate_transcript_task",
    queue="analysis",
    # A max-size job (_MAX_CUES_PER_JOB=4000, chunk_size=35) is ~115 chunks,
    # up to 2 LLM calls each with the strict retry = up to 230 sequential
    # calls. The previous 280s/340s limits (copied from analyze_media_task,
    # which does far fewer LLM calls per job) meant any large job was
    # virtually guaranteed to hit SoftTimeLimitExceeded and fail — wasting
    # every LLM call already paid for, with no partial result saved.
    soft_time_limit=1500,
    time_limit=1680,
    acks_late=True,
)
def translate_transcript_task(job_id: str) -> dict:
    """
    Translate a transcript_translation_jobs row's uploaded .srt/.vtt file.

    Steps:
      1. Load job row from Supabase
      2. Read + parse input_path (format from source_format)
      3. status='detecting' -> detect_source_language -> persist source_lang
      4. status='translating' -> translate_cues chunk-by-chunk, updating
         translated_cue_count/progress_pct after each chunk
      5. Serialize translated cues back to the same format -> result_path
      6. status='done'

    On any exception (including TranslationAlignmentError): status='failed',
    error_message set, then re-raise.
    """
    db = get_service_client()

    # ------------------------------------------------------------------
    # 1. Load job
    # ------------------------------------------------------------------
    try:
        resp = (
            db.table("transcript_translation_jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        job: dict = resp.data
    except Exception as exc:
        logger.error(
            "translate_transcript_task: failed to load job",
            extra={"job_id": job_id, "error": str(exc)},
        )
        raise

    if not job:
        logger.error("translate_transcript_task: job not found", extra={"job_id": job_id})
        return {"status": "not_found", "job_id": job_id}

    # Idempotency: acks_late + task_reject_on_worker_lost means this task can
    # be redelivered if a worker dies mid-run.
    #   - Already 'done' before redelivery lands → skip entirely (below).
    #   - Died mid-translation → resumes from the on-disk checkpoint
    #     (resume_chunk_index + progress.ndjson, see _load_resume_state)
    #     instead of restarting from chunk 0. Even chunks NOT covered by the
    #     checkpoint (e.g. checkpoint file lost) still avoid most re-billing
    #     via the cross-job translation cache (per-cue, checked below) —
    #     the checkpoint just makes the common case free of even the cache
    #     DB round-trips and gives accurate progress immediately.
    if job.get("status") == "done":
        logger.info(
            "translate_transcript_task: job already done, skipping redelivered run",
            extra={"job_id": job_id},
        )
        return {"status": "already_done", "job_id": job_id}

    try:
        # ------------------------------------------------------------------
        # 2. Read + parse input file
        # ------------------------------------------------------------------
        from app.services.subtitle_format import parse_srt, parse_vtt, serialize_srt, serialize_vtt

        input_path: str = job["input_path"]
        source_format: str = job["source_format"]
        target_lang: str = job["target_lang"]

        with open(input_path, "r", encoding="utf-8") as f:
            content = f.read()

        if source_format == "vtt":
            cues = parse_vtt(content)
        else:
            cues = parse_srt(content)

        cue_count = len(cues)

        from app.services.transcript_translation_service import (
            TranslationAlignmentError,
            detect_source_language,
            translate_cues,
        )

        # ------------------------------------------------------------------
        # 3. Detect source language — skipped on resume if a prior (possibly
        #    interrupted) run of this job already detected it.
        # ------------------------------------------------------------------
        source_lang = job.get("source_lang")
        if source_lang:
            db.table("transcript_translation_jobs").update(
                {"status": "translating", "cue_count": cue_count, "updated_at": _now_iso()}
            ).eq("id", job_id).execute()
        else:
            db.table("transcript_translation_jobs").update(
                {"status": "detecting", "cue_count": cue_count, "updated_at": _now_iso()}
            ).eq("id", job_id).execute()
            source_lang = detect_source_language(cues)
            db.table("transcript_translation_jobs").update(
                {"status": "translating", "source_lang": source_lang, "updated_at": _now_iso()}
            ).eq("id", job_id).execute()

        # ------------------------------------------------------------------
        # 4. Translate in bounded chunks, reporting progress after each.
        #
        # Cost control: within each chunk, cue text is deduped by
        # sha256(text, target_lang) both against the cross-job/cross-user
        # cache table AND against duplicate cues in the SAME chunk (e.g.
        # repeated "[Music]" filler lines) — only genuinely new text is ever
        # sent to the LLM. See app.services.transcript_translation_cache.
        # ------------------------------------------------------------------
        from app.services.transcript_translation_cache import (
            compute_hash,
            lookup_many,
            store_many,
        )

        chunk_size = 35

        # Resume from checkpoint if this is a redelivered run of a job that
        # got partway through translating before a worker died. Falls back
        # to starting over (translated_texts=[], resume_chunk_index=0) if
        # the checkpoint is missing/unreadable/incomplete — never trusts a
        # partial reconstruction.
        progress_path = _progress_file_for(input_path)
        resume_chunk_index = int(job.get("resume_chunk_index") or 0)
        translated_texts = _load_resume_state(progress_path, resume_chunk_index)
        if translated_texts is None:
            resume_chunk_index = 0
            translated_texts = []
        elif resume_chunk_index > 0:
            logger.info(
                "translate_transcript_task: resuming from checkpoint",
                extra={"job_id": job_id, "resume_chunk_index": resume_chunk_index},
            )

        for chunk_start in range(resume_chunk_index * chunk_size, cue_count, chunk_size):
            chunk_index = chunk_start // chunk_size
            chunk = cues[chunk_start:chunk_start + chunk_size]

            hashes = [compute_hash(c.text, target_lang) for c in chunk]
            cached = lookup_many(db, list(set(hashes)))

            first_index_of_hash: dict[str, int] = {}
            for i, h in enumerate(hashes):
                if h not in cached and h not in first_index_of_hash:
                    first_index_of_hash[h] = i
            uncached_hashes_ordered = list(first_index_of_hash.keys())
            uncached_cues = [chunk[first_index_of_hash[h]] for h in uncached_hashes_ordered]

            if uncached_cues:
                fresh = translate_cues(
                    uncached_cues, source_lang=source_lang, target_lang=target_lang, chunk_size=chunk_size
                )
                fresh_by_hash = dict(zip(uncached_hashes_ordered, fresh))
                store_many(
                    db,
                    [(h, target_lang, fresh_by_hash[h]) for h in uncached_hashes_ordered],
                )
            else:
                fresh_by_hash = {}

            resolved = {**cached, **fresh_by_hash}
            chunk_translations = [resolved[h] for h in hashes]
            translated_texts.extend(chunk_translations)

            # Checkpoint this chunk to disk BEFORE reporting progress, so a
            # crash right after the progress update still has the chunk's
            # translations recoverable on resume.
            _append_chunk_progress(progress_path, chunk_index, chunk_translations)

            translated_cue_count = len(translated_texts)
            progress_pct = (
                round(translated_cue_count / cue_count * 100) if cue_count else 100
            )
            db.table("transcript_translation_jobs").update(
                {
                    "translated_cue_count": translated_cue_count,
                    "progress_pct": progress_pct,
                    "resume_chunk_index": chunk_index + 1,
                    "updated_at": _now_iso(),
                }
            ).eq("id", job_id).execute()

        if len(translated_texts) != cue_count:
            # Defensive — translate_cues/TranslationAlignmentError should already
            # guarantee this, but never mark a job done with mismatched counts.
            raise TranslationAlignmentError(
                f"translated_cue_count ({len(translated_texts)}) != cue_count ({cue_count})"
            )

        # ------------------------------------------------------------------
        # 5. Serialize translated cues -> result_path
        # ------------------------------------------------------------------
        translated_cues = [
            type(cue)(index=cue.index, start=cue.start, end=cue.end, text=text)
            for cue, text in zip(cues, translated_texts)
        ]

        if source_format == "vtt":
            output_content = serialize_vtt(translated_cues)
        else:
            output_content = serialize_srt(translated_cues)

        result_path = _result_path_for(input_path, source_format)
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(output_content)

        # ------------------------------------------------------------------
        # 5b. Reading-speed QA — informational only, never blocks the job.
        # ------------------------------------------------------------------
        from app.services.subtitle_qa import check_reading_speed

        reading_speed_warnings = check_reading_speed(translated_cues)

        # ------------------------------------------------------------------
        # 6. Mark done
        # ------------------------------------------------------------------
        db.table("transcript_translation_jobs").update(
            {
                "status": "done",
                "result_path": result_path,
                "translated_cue_count": len(translated_texts),
                "progress_pct": 100,
                "reading_speed_warning_count": len(reading_speed_warnings),
                "updated_at": _now_iso(),
            }
        ).eq("id", job_id).execute()

        logger.info(
            "translate_transcript_task: completed",
            extra={"job_id": job_id, "cue_count": cue_count},
        )
        return {"status": "done", "job_id": job_id, "cue_count": cue_count}

    except Exception as exc:
        # SoftTimeLimitExceeded / TranslationAlignmentError are worth retrying
        # from the checkpoint (T3-B) rather than failed-and-cleaned-up: a
        # SoftTimeLimitExceeded on a legitimately large job will hit the SAME
        # limit again if just restarted from scratch, but resuming from
        # resume_chunk_index means each successive run covers new ground
        # instead of re-doing (and re-timing-out on) the same prefix. A
        # TranslationAlignmentError is usually just one chunk's malformed
        # response — often fine on a fresh attempt. Bounded by
        # timeout_retry_count so a job that keeps failing the same way every
        # time doesn't requeue forever.
        is_transient = isinstance(exc, (SoftTimeLimitExceeded, TranslationAlignmentError))
        retry_count = int(job.get("timeout_retry_count") or 0)

        if is_transient and retry_count < _MAX_TRANSIENT_RETRIES:
            new_retry_count = retry_count + 1
            logger.warning(
                "translate_transcript_task: transient failure, requeueing from checkpoint",
                extra={"job_id": job_id, "error": str(exc), "retry_count": new_retry_count},
            )
            try:
                db.table("transcript_translation_jobs").update(
                    {
                        "status": "queued",
                        "timeout_retry_count": new_retry_count,
                        "error_message": (
                            f"Tạm dừng ({exc.__class__.__name__}), tự động thử lại "
                            f"({new_retry_count}/{_MAX_TRANSIENT_RETRIES})..."
                        ),
                        "updated_at": _now_iso(),
                    }
                ).eq("id", job_id).execute()
                # NOT cleaning up job_dir here — resume_chunk_index/progress.ndjson
                # must survive so the requeued run can pick up where this one left off.
                translate_transcript_task.apply_async(args=[job_id])
                return {"status": "requeued", "job_id": job_id, "retry_count": new_retry_count}
            except Exception as requeue_exc:
                logger.error(
                    "translate_transcript_task: failed to requeue after transient failure, "
                    "falling through to hard failure",
                    extra={"job_id": job_id, "error": str(requeue_exc)},
                )
                # fall through to the hard-failure path below

        logger.error(
            "translate_transcript_task: unhandled exception",
            extra={"job_id": job_id, "error": str(exc)},
            exc_info=True,
        )
        try:
            db.table("transcript_translation_jobs").update(
                {
                    "status": "failed",
                    "error_message": str(exc)[:2000],
                    "updated_at": _now_iso(),
                }
            ).eq("id", job_id).execute()
        except Exception:
            pass

        # Clean up on-disk input/partial-result files now rather than only
        # via the hourly expiry sweep — a burst of failing jobs (bad input,
        # repeated timeouts) shouldn't be able to fill disk for up to an hour.
        # Only reached once transient retries are exhausted (or genuinely
        # non-retryable), so the checkpoint is no longer needed.
        try:
            input_path = job.get("input_path")
            if input_path:
                job_dir = os.path.dirname(input_path)
                if job_dir and os.path.isdir(job_dir):
                    shutil.rmtree(job_dir, ignore_errors=True)
        except Exception:
            pass

        raise


# ---------------------------------------------------------------------------
# Expiry beat task
# ---------------------------------------------------------------------------

@celery_app.task(
    name="expire_transcript_translation_jobs_task",
    queue="analysis",
    ignore_result=True,
)
def expire_transcript_translation_jobs_task() -> None:
    """
    Delete expired transcript_translation_jobs rows and their input/result files.

    Finds jobs WHERE expires_at < NOW(). Removes files on disk first
    (ignoring errors on missing files), then deletes the DB rows.
    """
    db = get_service_client()
    now_iso = _now_iso()

    try:
        resp = (
            db.table("transcript_translation_jobs")
            .select("id, input_path, result_path")
            .lt("expires_at", now_iso)
            .execute()
        )
        rows: list[dict] = resp.data or []
        if not rows:
            logger.info("expire_transcript_translation_jobs_task: no expired jobs found")
            return

        job_ids = [r["id"] for r in rows]

        for row in rows:
            for path_key in ("input_path", "result_path"):
                path = row.get(path_key)
                if not path:
                    continue
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except Exception as exc:
                    logger.warning(
                        "expire_transcript_translation_jobs_task: failed to remove file",
                        extra={"path": path, "error": str(exc)},
                    )
            # Best-effort: remove the now-possibly-empty job directory
            input_path = row.get("input_path") or ""
            if input_path:
                try:
                    job_dir = os.path.dirname(input_path)
                    if os.path.isdir(job_dir) and not os.listdir(job_dir):
                        os.rmdir(job_dir)
                except Exception:
                    pass

        db.table("transcript_translation_jobs").delete().in_("id", job_ids).execute()

        logger.info(
            "expire_transcript_translation_jobs_task: deleted expired jobs",
            extra={"count": len(job_ids)},
        )

    except Exception as exc:
        logger.error(
            "expire_transcript_translation_jobs_task: failed",
            extra={"error": str(exc)},
            exc_info=True,
        )
