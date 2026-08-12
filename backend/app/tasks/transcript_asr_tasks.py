"""
Transcript ASR Tasks — Celery
================================
Auto-generate a subtitle file from a downloaded video's audio via
app.services.asr_service (OpenAI Whisper). Runs on the 'analysis' queue —
same reasoning as transcript_translation_tasks.py: no worker in
docker-compose.yml consumes a standalone queue for this feature, and this
is the same class of work (bounded, external-API-bound, moderate
concurrency).

Task: transcribe_video_task(job_id)
  - Loads transcript_asr_jobs row
  - Extracts a compressed mono audio track via FFmpeg (bounded duration,
    sized to stay under Whisper's 25MB upload limit)
  - Transcribes via asr_service.transcribe_audio
  - Writes an SRT to result_path
  - Bounded self-requeue on SoftTimeLimitExceeded, mirroring T3-C in
    transcript_translation_tasks.py (preserves partial progress-free state,
    just re-runs since ASR isn't chunked/resumable the way translation is —
    there's nothing to check-point mid-transcription).
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

from celery.exceptions import SoftTimeLimitExceeded

from app.core.celery_app import celery_app
from app.core.database import get_service_client
from app.core.structured_log import get_logger

logger = get_logger(__name__)

_MAX_TRANSIENT_RETRIES = 3

# Whisper's hard upload limit is 25MB. 48kbps mono opus/mp3 keeps a 45-minute
# extract comfortably under that (45*60*48000/8 ≈ 16.2MB) with real margin
# for encoding overhead — chosen together with _MAX_DURATION_SEC below.
_AUDIO_BITRATE_KBPS = 48
_MAX_DURATION_SEC = 45 * 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _probe_duration_sec(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, timeout=30, text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed: {result.stderr[-300:]}")
    return float(result.stdout.strip())


def _extract_audio(video_path: str, audio_path: str) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-ac", "1", "-acodec", "libmp3lame", "-b:a", f"{_AUDIO_BITRATE_KBPS}k",
         audio_path],
        capture_output=True, timeout=300,
    )
    if result.returncode != 0 or not os.path.isfile(audio_path):
        raise ValueError(f"FFmpeg audio extraction failed: {result.stderr.decode(errors='replace')[-300:]}")

    # The bitrate/duration cap above keeps us under Whisper's 25MB upload
    # limit "by construction" with margin — but VBR/encoding overhead on an
    # unusual input could still blow past it. Check the real output size so
    # that case fails clean here instead of as an opaque Whisper API error.
    from app.services.asr_service import MAX_AUDIO_FILE_BYTES

    audio_bytes = os.path.getsize(audio_path)
    if audio_bytes > MAX_AUDIO_FILE_BYTES:
        raise ValueError(
            f"File âm thanh sau khi tách ({audio_bytes / 1024 / 1024:.1f}MB) vượt giới hạn "
            f"{MAX_AUDIO_FILE_BYTES / 1024 / 1024:.0f}MB của Whisper API."
        )


@celery_app.task(
    name="transcribe_video_task",
    queue="analysis",
    soft_time_limit=300,
    time_limit=360,
    acks_late=True,
)
def transcribe_video_task(job_id: str) -> dict:
    db = get_service_client()

    try:
        resp = (
            db.table("transcript_asr_jobs")
            .select("*")
            .eq("id", job_id)
            .single()
            .execute()
        )
        job: dict = resp.data
    except Exception as exc:
        logger.error("transcribe_video_task: failed to load job", extra={"job_id": job_id, "error": str(exc)})
        raise

    if not job:
        logger.error("transcribe_video_task: job not found", extra={"job_id": job_id})
        return {"status": "not_found", "job_id": job_id}

    if job.get("status") == "done":
        return {"status": "already_done", "job_id": job_id}

    video_path = job["video_local_path"]
    # video_local_path points into the SHARED downloads dir, not a
    # per-job dir like transcript_translation_jobs' input files — never
    # delete it (it's someone's downloaded video, owned by a different
    # feature's cleanup lifecycle). Only our own generated audio/result
    # files get cleaned up here.
    downloads_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "downloads",
    )
    audio_path = os.path.join(downloads_dir, f"asr_audio_{job_id}.mp3")

    try:
        if not os.path.isfile(video_path):
            raise FileNotFoundError(
                "Video đã hết hạn hoặc bị xoá khỏi server. Vui lòng tải lại video."
            )

        # ------------------------------------------------------------------
        # 1. Probe + bound duration
        # ------------------------------------------------------------------
        duration_sec = _probe_duration_sec(video_path)
        if duration_sec > _MAX_DURATION_SEC:
            raise ValueError(
                f"Video dài {duration_sec/60:.1f} phút, vượt giới hạn {_MAX_DURATION_SEC//60} phút cho tạo phụ đề tự động."
            )

        db.table("transcript_asr_jobs").update(
            {"status": "extracting_audio", "duration_sec": duration_sec, "progress_pct": 20, "updated_at": _now_iso()}
        ).eq("id", job_id).execute()

        # ------------------------------------------------------------------
        # 2. Extract audio
        # ------------------------------------------------------------------
        os.makedirs(os.path.dirname(audio_path), exist_ok=True)
        _extract_audio(video_path, audio_path)

        # ------------------------------------------------------------------
        # 3. Transcribe
        # ------------------------------------------------------------------
        db.table("transcript_asr_jobs").update(
            {"status": "transcribing", "progress_pct": 50, "updated_at": _now_iso()}
        ).eq("id", job_id).execute()

        from app.services.asr_service import transcribe_audio

        asr_result = transcribe_audio(audio_path)
        cues = asr_result["cues"]

        if not cues:
            raise ValueError("Không nhận diện được lời thoại nào trong video (có thể video không có tiếng nói).")

        # ------------------------------------------------------------------
        # 4. Serialize -> result_path
        # ------------------------------------------------------------------
        from app.services.subtitle_format import serialize_srt

        result_path = os.path.join(os.path.dirname(audio_path), f"asr_result_{job_id}.srt")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(serialize_srt(cues))

        db.table("transcript_asr_jobs").update(
            {
                "status": "done",
                "result_path": result_path,
                "detected_language": asr_result["language"],
                "progress_pct": 100,
                "updated_at": _now_iso(),
            }
        ).eq("id", job_id).execute()

        logger.info("transcribe_video_task: completed", extra={"job_id": job_id, "cue_count": len(cues)})
        return {"status": "done", "job_id": job_id, "cue_count": len(cues)}

    except Exception as exc:
        # Same bounded-self-requeue pattern as T3-C (translate task) —
        # SoftTimeLimitExceeded is worth one more attempt (transient: a slow
        # OpenAI response, not a structural problem with this job).
        is_transient = isinstance(exc, SoftTimeLimitExceeded)
        retry_count = int(job.get("timeout_retry_count") or 0)

        if is_transient and retry_count < _MAX_TRANSIENT_RETRIES:
            new_retry_count = retry_count + 1
            logger.warning(
                "transcribe_video_task: transient failure, requeueing",
                extra={"job_id": job_id, "retry_count": new_retry_count},
            )
            try:
                db.table("transcript_asr_jobs").update(
                    {
                        "status": "queued",
                        "timeout_retry_count": new_retry_count,
                        "error_message": f"Tạm dừng (timeout), tự động thử lại ({new_retry_count}/{_MAX_TRANSIENT_RETRIES})...",
                        "updated_at": _now_iso(),
                    }
                ).eq("id", job_id).execute()
                transcribe_video_task.apply_async(args=[job_id])
                return {"status": "requeued", "job_id": job_id, "retry_count": new_retry_count}
            except Exception as requeue_exc:
                logger.error("transcribe_video_task: requeue failed, hard-failing", extra={"error": str(requeue_exc)})

        logger.error("transcribe_video_task: unhandled exception", extra={"job_id": job_id, "error": str(exc)}, exc_info=True)
        try:
            db.table("transcript_asr_jobs").update(
                {"status": "failed", "error_message": str(exc)[:2000], "updated_at": _now_iso()}
            ).eq("id", job_id).execute()
        except Exception:
            pass
        raise

    finally:
        # Our own generated audio scratch file — always safe to remove,
        # regardless of success/failure (never the source video).
        try:
            if os.path.isfile(audio_path):
                os.remove(audio_path)
        except Exception:
            pass


@celery_app.task(name="expire_transcript_asr_jobs_task", queue="analysis", ignore_result=True)
def expire_transcript_asr_jobs_task() -> None:
    """Delete expired transcript_asr_jobs rows and their result files (never
    the source video — that belongs to the download feature's own lifecycle)."""
    db = get_service_client()
    now_iso = _now_iso()

    try:
        resp = (
            db.table("transcript_asr_jobs")
            .select("id, result_path")
            .lt("expires_at", now_iso)
            .execute()
        )
        rows: list[dict] = resp.data or []
        if not rows:
            return

        for row in rows:
            path = row.get("result_path")
            if path:
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except Exception:
                    pass

        db.table("transcript_asr_jobs").delete().in_("id", [r["id"] for r in rows]).execute()
        logger.info("expire_transcript_asr_jobs_task: deleted expired jobs", extra={"count": len(rows)})
    except Exception as exc:
        logger.error("expire_transcript_asr_jobs_task: failed", extra={"error": str(exc)}, exc_info=True)
