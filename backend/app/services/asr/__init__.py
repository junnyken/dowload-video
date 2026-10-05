"""
ASR provider layer (Phase 32A).

    provider = get_provider()            # ASR_PROVIDER, default "gemini"
    result = provider.transcribe(path, language=None, diarize=False, duration_sec=...)

There is NO silent fallback between providers: if the selected provider has
no key, get_provider() raises ProviderUnavailable and the job fails cleanly.
"""
from __future__ import annotations

from app.services.asr import config
from app.services.asr.types import (  # noqa: F401 — re-exported
    ERROR_MESSAGES_VI,
    AsrError,
    AsrProvider,
    BudgetError,
    NoSpeechError,
    ProviderBadOutput,
    ProviderError,
    ProviderTransientError,
    ProviderUnavailable,
    Segment,
    TranscriptResult,
)

PROVIDERS = ("gemini", "whisper")


def get_provider(name: str | None = None):
    name = (name or config.provider_name()).strip().lower()
    if name == "gemini":
        from app.services.asr.gemini import GeminiProvider  # noqa: PLC0415
        return GeminiProvider()
    if name == "whisper":
        from app.services.asr.whisper import WhisperProvider  # noqa: PLC0415
        return WhisperProvider()
    raise ProviderUnavailable(f"ASR_PROVIDER không hợp lệ: {name!r} (chỉ hỗ trợ {', '.join(PROVIDERS)}).")


def estimate_cost_usd(provider_name: str, duration_sec: float) -> float:
    """Cost estimate without constructing a provider (no key needed)."""
    return round(max(0.0, duration_sec) / 60.0 * config.price_per_min(provider_name), 6)
