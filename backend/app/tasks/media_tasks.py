"""
Media (FFmpeg) tasks
====================
celery_app.py has routed `watermark_task` and `merge_audio_video_task` to the
`media` queue since the queue was introduced, but neither function existed, so
nothing was ever produced for that queue. The ffmpeg work stayed in the request
handlers, running inside the API process: an encode there competes with uvicorn
for CPU, and nothing bounded how many ran at once (the endpoints carry only a
5/minute per-IP rate limit, which several callers clear simultaneously).

Running them here instead puts encodes behind the worker's concurrency setting,
so a burst queues rather than all landing on the CPU at the same moment, and the
API process goes back to only waiting on I/O.

The tasks deliberately take an already-built argv rather than the request body.
Validation, path safety (`_safe_path`), duration limits and the response shape
all stay in the endpoints, unchanged — this moves *where ffmpeg runs*, and
nothing else.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any, Dict, List

from app.core.celery_app import celery_app

log = logging.getLogger(__name__)

# Wait budget for the HTTP request sitting on the result: the task's own ffmpeg
# timeout plus a grace period for time spent queued behind another encode.
QUEUE_GRACE_SEC = 30


def _run_ffmpeg(cmd: List[str], timeout: int) -> Dict[str, Any]:
    """Run ffmpeg and return the parts of the outcome a caller can act on."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "returncode": proc.returncode,
            # Matches what the endpoints previously surfaced to the client.
            "stderr": (proc.stderr or "")[-500:],
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stderr": f"FFmpeg exceeded its {timeout}s time limit.",
        }


@celery_app.task(name="watermark_task")
def watermark_task(cmd: List[str], timeout: int = 300) -> Dict[str, Any]:
    return _run_ffmpeg(cmd, timeout)


@celery_app.task(name="merge_audio_video_task")
def merge_audio_video_task(cmd: List[str], timeout: int = 300) -> Dict[str, Any]:
    return _run_ffmpeg(cmd, timeout)


def run_on_worker(task, cmd: List[str], timeout: int = 300) -> Dict[str, Any]:
    """Run an ffmpeg command on the media worker and wait for its result.

    Falls back to running in-process if the broker cannot be reached. Losing
    the worker should degrade CPU isolation, not take a working feature
    offline — and the `-threads` cap from ffmpeg_budget still applies to the
    command either way, so the fallback is bounded too.
    """
    try:
        async_result = task.delay(cmd, timeout)
    except Exception as exc:  # broker unreachable / not configured
        log.warning(
            "[media] could not dispatch ffmpeg to the worker, running in-process: %s",
            exc,
        )
        return _run_ffmpeg(cmd, timeout)

    try:
        return async_result.get(timeout=timeout + QUEUE_GRACE_SEC)
    except Exception as exc:
        log.warning("[media] ffmpeg task did not return in time: %s", exc)
        return {
            "returncode": -1,
            "stderr": "Máy chủ đang bận xử lý video khác. Vui lòng thử lại sau.",
        }
