"""
Subtitle Reading-Speed QA
===========================
After translation, flag cues whose translated text is too long to
comfortably read in the time the cue is on screen — a translation can be
100% linguistically correct and still be a bad subtitle if nobody can
finish reading it before it disappears.

Industry subtitle guidelines commonly cap reading speed around 17-20
characters/second and ~42 characters/line for adult content (Netflix,
BBC, and most streaming style guides land in this range). This is
informational only — never blocks a translation, just flags it so the
user knows which lines might want manual shortening.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.subtitle_format import Cue, timestamp_to_seconds

# Cues flattened to a single line during translation (see
# transcript_translation_service._flatten), so "chars per line" is just the
# total character count — there's only ever one line.
_MAX_CPS = 20.0
_MAX_CHARS = 42


@dataclass
class ReadingSpeedWarning:
    cue_index: int
    char_count: int
    duration_sec: float
    cps: float
    reason: str  # "too_fast" | "too_long" | "both"


def check_reading_speed(cues: list[Cue]) -> list[ReadingSpeedWarning]:
    """
    Returns one ReadingSpeedWarning per cue that exceeds the CPS or
    character-count threshold. Cues with an unparseable/zero-length
    duration are skipped (can't compute a meaningful rate), not flagged —
    a QA false-positive from a malformed timestamp would just be noise.
    """
    warnings: list[ReadingSpeedWarning] = []

    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue

        try:
            start_sec = timestamp_to_seconds(cue.start)
            end_sec = timestamp_to_seconds(cue.end)
        except ValueError:
            continue

        duration = end_sec - start_sec
        if duration <= 0:
            continue

        char_count = len(text)
        cps = char_count / duration

        too_fast = cps > _MAX_CPS
        too_long = char_count > _MAX_CHARS
        if not (too_fast or too_long):
            continue

        reason = "both" if (too_fast and too_long) else ("too_fast" if too_fast else "too_long")
        warnings.append(ReadingSpeedWarning(
            cue_index=cue.index,
            char_count=char_count,
            duration_sec=round(duration, 2),
            cps=round(cps, 1),
            reason=reason,
        ))

    return warnings
