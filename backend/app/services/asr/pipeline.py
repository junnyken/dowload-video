"""
Chunked transcription with cost accounting (shared by the Celery task and
the admin selftest).

Billing rules:
  * Every provider call is counted as billed the moment it is attempted
    (tracker.paid_started / tracker.actual_cost), whether or not it succeeds.
  * A chunk is retried only on ProviderTransientError, at most
    ASR_CHUNK_MAX_RETRIES times, and only if the retry still fits under the
    daily ceiling — the retry's cost is added to the ledger before the call.
  * The kill switch is checked before every provider call.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from app.services.asr import budget, config
from app.services.asr.chunking import cut_chunk, merge_chunk_results, plan_chunks
from app.services.asr.types import BudgetError, ProviderTransientError, TranscriptResult


@dataclass
class CostTracker:
    spend_day: str | None = None
    actual_cost: float = 0.0
    extra_reserved: float = 0.0   # retry costs already pushed to the ledger
    paid_calls: int = 0
    chunks: int = 0
    timings_ms: list = field(default_factory=list)

    @property
    def paid_started(self) -> bool:
        return self.paid_calls > 0


def _ensure_not_paused() -> None:
    try:
        paused = budget.killswitch_on()
    except Exception:  # noqa: BLE001 — Redis blip mid-job: the job was already budgeted
        paused = False
    if paused:
        raise BudgetError(code="asr_paused")


_sleep = time.sleep  # patched in tests


def transcribe_chunked(
    provider,
    audio_path: str,
    duration_sec: float,
    *,
    work_dir: str,
    tracker: CostTracker,
    language: str | None = None,
    diarize: bool = False,
    progress_cb=None,
) -> TranscriptResult:
    plan = plan_chunks(duration_sec, config.chunk_sec())
    tracker.chunks = len(plan)
    max_retries = config.chunk_max_retries()
    parts: list[tuple[float, TranscriptResult]] = []

    for idx, (offset, length) in enumerate(plan):
        if len(plan) == 1:
            chunk_path = audio_path
        else:
            chunk_path = os.path.join(work_dir, f"asr_chunk_{idx:03d}.mp3")
            cut_chunk(audio_path, offset, length, chunk_path)
        try:
            attempt = 0
            while True:
                _ensure_not_paused()
                cost = provider.estimate_cost_usd(length, diarize)
                if attempt > 0:
                    if tracker.spend_day is None or budget.would_exceed(tracker.spend_day, cost):
                        raise BudgetError(code="spend_ceiling_reached")
                    budget.add_spend(tracker.spend_day, cost)
                    tracker.extra_reserved += cost
                tracker.paid_calls += 1
                tracker.actual_cost += cost
                t0 = time.monotonic()
                try:
                    res = provider.transcribe(chunk_path, language=language, diarize=diarize, duration_sec=length)
                    tracker.timings_ms.append(int((time.monotonic() - t0) * 1000))
                    break
                except ProviderTransientError:
                    attempt += 1
                    if attempt > max_retries:
                        raise
                    _sleep(min(30, 5 * attempt))
        finally:
            if chunk_path != audio_path:
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass
        res.duration_sec = length
        parts.append((offset, res))
        if progress_cb:
            progress_cb(idx + 1, len(plan))

    return merge_chunk_results(parts)
