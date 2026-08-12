"""
ASR (Automatic Speech Recognition) Service
=============================================
Transcribes an audio file into timed cues via OpenAI's Whisper API — the
engine choice for this feature (v1: OpenAI only, no local-model fallback;
see T5 mini-spec discussion). Produces app.services.subtitle_format.Cue
objects so the result flows into the exact same parse/serialize/translate
infrastructure already built for the translate-an-existing-file feature.
"""
from __future__ import annotations

import os

from app.services.subtitle_format import Cue, seconds_to_srt_timestamp

# Whisper API hard limit is 25MB per file — audio extraction (asr_tasks.py)
# encodes at a bitrate/duration combination designed to stay under this.
MAX_AUDIO_FILE_BYTES = 25 * 1024 * 1024


class AsrUnavailableError(Exception):
    """Raised when no ASR provider is configured/reachable — callers must
    treat this as a clean failure, never fall back to fabricating cues."""


def transcribe_audio(audio_path: str) -> dict:
    """
    Transcribe audio_path via OpenAI Whisper. Returns
    {"language": str, "cues": list[Cue]}. Raises AsrUnavailableError if
    OPENAI_API_KEY isn't configured, or the underlying openai package call
    failure otherwise (never silently returns an empty/fabricated result).
    """
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise AsrUnavailableError(
            "OPENAI_API_KEY chưa được cấu hình — không thể tạo phụ đề tự động."
        )

    import openai  # noqa: PLC0415 — lazy import, mirrors llm_client.py's pattern

    client = openai.OpenAI(api_key=api_key)
    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = getattr(response, "segments", None) or []
    non_blank = [seg for seg in segments if seg.text and seg.text.strip()]
    # Renumber sequentially over the surviving (non-blank) segments — unlike
    # the translate-an-existing-file feature, these cues are brand new, not
    # references to anything a caller already has by index, so gaps left by
    # a dropped blank segment (e.g. Whisper emitting an empty pause segment)
    # would just be non-standard SRT, not a meaningful preserved identity.
    cues = [
        Cue(
            index=i + 1,
            start=seconds_to_srt_timestamp(seg.start),
            end=seconds_to_srt_timestamp(seg.end),
            text=seg.text.strip(),
        )
        for i, seg in enumerate(non_blank)
    ]

    return {
        "language": getattr(response, "language", None) or "unknown",
        "cues": cues,
    }
