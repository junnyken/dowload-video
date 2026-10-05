"""
ASR configuration — every value is read from the environment AT CALL TIME
(never captured at import), so a test or an env change is not defeated by a
module-level snapshot.

Defaults keep today's behaviour where it existed (45-min per-job cap and
TRANSCRIPT_ASR_DAILY_MINUTES_LIMIT=120 stay in app.api.transcript_asr) and
default everything new to the safe side: the feature is OFF.

Prices are UNVERIFIED placeholders used only for the spend ceiling; set the
real per-minute price via env once confirmed against the provider's bill.
"""
from __future__ import annotations

import os


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "") or default))
    except ValueError:
        return default


def asr_enabled() -> bool:
    """Master flag. Default OFF: POST /transcript-asr/jobs answers asr_disabled."""
    return _bool("ASR_ENABLED", False)


def provider_name() -> str:
    return (os.environ.get("ASR_PROVIDER") or "gemini").strip().lower()


def gemini_model() -> str:
    from app.services.llm_client import _GEMINI_MODEL  # same model the rest of the app uses
    return (os.environ.get("ASR_GEMINI_MODEL") or _GEMINI_MODEL).strip()


# UNVERIFIED defaults (USD per audio minute). Whisper's public list price is
# $0.006/min; the Gemini figure is a rough audio-token estimate for a Flash
# model and MUST be checked against the real bill.
_DEFAULT_PRICE_PER_MIN = {"gemini": 0.003, "whisper": 0.006}


def price_per_min(provider: str) -> float:
    return _float(f"ASR_PRICE_PER_MIN_{provider.upper()}", _DEFAULT_PRICE_PER_MIN.get(provider, 0.01))


def spend_ceiling_usd() -> float:
    return _float("ASR_SPEND_CEILING_USD", 3.0)


def global_daily_minutes_cap() -> float:
    return _float("ASR_GLOBAL_DAILY_MINUTES_CAP", 600.0)


def chunk_sec() -> int:
    return max(60, _int("ASR_CHUNK_SEC", 600))


def chunk_max_retries() -> int:
    """Extra attempts per chunk on a clearly transient provider error. Each
    attempt is counted as billed."""
    return max(0, _int("ASR_CHUNK_MAX_RETRIES", 1))


# Celery's Redis visibility_timeout is 1800s; the hard limit must stay below
# it or a still-running task gets redelivered (and billed twice).
_VISIBILITY_TIMEOUT_SEC = 1800


def task_hard_limit_sec() -> int:
    return min(_int("ASR_TASK_HARD_LIMIT_SEC", 1620), _VISIBILITY_TIMEOUT_SEC - 60)


def task_soft_limit_sec() -> int:
    return min(_int("ASR_TASK_SOFT_LIMIT_SEC", 1500), task_hard_limit_sec() - 30)


def stuck_after_sec() -> int:
    """A non-terminal job not updated for this long cannot still be running
    (the hard limit killed it) — the sweeper fails and refunds it."""
    return task_hard_limit_sec() + 300


def diarization_enabled() -> bool:
    return _bool("ASR_DIARIZATION_ENABLED", False)


def selftest_max_sec() -> int:
    return max(5, min(_int("ASR_SELFTEST_MAX_SEC", 60), 60))


def gemini_max_output_tokens() -> int:
    return _int("ASR_GEMINI_MAX_OUTPUT_TOKENS", 32768)


def gemini_inline_max_bytes() -> int:
    # Inline request limit is ~20MB; a 10-min 48kbps chunk is ~3.6MB.
    return _int("ASR_GEMINI_INLINE_MAX_BYTES", 15 * 1024 * 1024)


def gemini_timeout_sec() -> int:
    return _int("ASR_GEMINI_TIMEOUT_SEC", 600)


def cue_max_sec() -> float:
    """Longest a subtitle cue may stay on screen; longer segments are split."""
    return max(2.0, _float("ASR_CUE_MAX_SEC", 7.0))


def cue_max_chars() -> int:
    """Two subtitle lines of ~42 characters."""
    return max(20, _int("ASR_CUE_MAX_CHARS", 84))


def gemini_prompt_style() -> str:
    """"sentence" (default) or "cue" — see gemini._SEGMENT_RULES."""
    return (os.environ.get("ASR_GEMINI_PROMPT_STYLE") or "sentence").strip().lower()
