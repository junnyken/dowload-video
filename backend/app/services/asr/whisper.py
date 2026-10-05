"""OpenAI Whisper provider — the original T5 implementation, behind AsrProvider."""
from __future__ import annotations

import os

from app.services.asr import config
from app.services.asr.types import (
    ProviderError,
    ProviderTransientError,
    ProviderUnavailable,
    Segment,
    TranscriptResult,
)

# Whisper API hard limit is 25MB per file.
MAX_AUDIO_FILE_BYTES = 25 * 1024 * 1024

_TRANSIENT_NAMES = {"RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError"}


class WhisperProvider:
    name = "whisper"
    model = "whisper-1"

    def __init__(self) -> None:
        self._api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise ProviderUnavailable(
                "OPENAI_API_KEY chưa được cấu hình — không thể tạo phụ đề tự động."
            )

    def estimate_cost_usd(self, duration_sec: float, diarize: bool = False) -> float:
        return round(max(0.0, duration_sec) / 60.0 * config.price_per_min(self.name), 6)

    def transcribe(self, audio_path, *, language=None, diarize=False, duration_sec=None) -> TranscriptResult:
        import openai  # noqa: PLC0415 — lazy import, mirrors llm_client.py

        size = os.path.getsize(audio_path)
        if size > MAX_AUDIO_FILE_BYTES:
            raise ProviderError(
                f"File âm thanh ({size / 1024 / 1024:.1f}MB) vượt giới hạn 25MB của Whisper API."
            )

        client = openai.OpenAI(api_key=self._api_key)
        kwargs = {
            "model": self.model,
            "response_format": "verbose_json",
            "timestamp_granularities": ["segment"],
        }
        if language:
            kwargs["language"] = language
        try:
            with open(audio_path, "rb") as f:
                response = client.audio.transcriptions.create(file=f, **kwargs)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ in _TRANSIENT_NAMES:
                raise ProviderTransientError(f"Whisper: {type(exc).__name__}") from exc
            if type(exc).__name__ in ("AuthenticationError", "PermissionDeniedError"):
                raise ProviderUnavailable(f"Whisper: {type(exc).__name__}") from exc
            raise ProviderError(f"Whisper: {type(exc).__name__}: {str(exc)[:300]}") from exc

        segments = []
        for seg in getattr(response, "segments", None) or []:
            text = (seg.text or "").strip()
            if not text:
                continue
            segments.append(Segment(start=float(seg.start), end=float(seg.end), text=text))

        try:
            dur = float(duration_sec if duration_sec is not None else getattr(response, "duration", 0) or 0)
        except (TypeError, ValueError):
            dur = 0.0
        return TranscriptResult(
            segments=segments,
            language=getattr(response, "language", None) or "unknown",
            duration_sec=dur,
        )
