"""
ASR (Automatic Speech Recognition) Service — compatibility shim
=================================================================
Phase 32A moved the providers to app.services.asr (Gemini + Whisper behind
an AsrProvider protocol). This module keeps the original Whisper-only entry
point, transcribe_audio(), for any caller that still imports it.
"""
from __future__ import annotations

from app.services.asr.types import ProviderUnavailable
from app.services.asr.whisper import MAX_AUDIO_FILE_BYTES  # noqa: F401 — re-exported
from app.services.subtitle_format import Cue, seconds_to_srt_timestamp

# Raised when no ASR provider is configured — callers must treat this as a
# clean failure, never fall back to fabricating cues.
AsrUnavailableError = ProviderUnavailable


def segments_to_cues(segments, *, with_speaker: bool = False) -> list[Cue]:
    cues = []
    for i, seg in enumerate(segments):
        text = seg.text.strip()
        if with_speaker and getattr(seg, "speaker", None):
            text = f"[{seg.speaker}] {text}"
        cues.append(Cue(
            index=i + 1,
            start=seconds_to_srt_timestamp(seg.start),
            end=seconds_to_srt_timestamp(seg.end),
            text=text,
        ))
    return cues


def transcribe_audio(audio_path: str) -> dict:
    """Whisper transcription. Returns {"language": str, "cues": list[Cue]}.
    Raises AsrUnavailableError if OPENAI_API_KEY isn't configured."""
    from app.services.asr.whisper import WhisperProvider  # noqa: PLC0415

    result = WhisperProvider().transcribe(audio_path)
    return {"language": result.language, "cues": segments_to_cues(result.segments)}
