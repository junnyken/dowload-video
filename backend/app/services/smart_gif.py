"""
Smart GIF Suggestion
====================
Suggests 2–4 short segments (2–6 seconds) optimal for GIF creation.

Criteria:
  - Clear motion (not static)
  - Smooth loop potential (consistent motion, no abrupt cuts)
  - Not too dark (visual stability)
  - Short duration (2–6s ideal)
  - Avoids silence-only segments

Output: list of GifSuggestion dicts.
"""

from __future__ import annotations

import statistics
from typing import Any

from app.core.structured_log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

GIF_MAX_DURATION: float = 6.0    # seconds — hard ceiling for GIF windows
GIF_MIN_MOTION: float   = 0.15   # motion score threshold — below = too static

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

GifSuggestion = dict  # see shape below:
# {
#     "index": int,
#     "start": float,
#     "end": float,
#     "duration": float,
#     "loop_score": float,
#     "motion_score": float,
#     "stability_score": float,
#     "combined_score": float,
#     "has_scene_cut": bool,
#     "thumbnail_ts": float,
#     "recommended_size": str,
# }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_duration(analysis: dict) -> float:
    probe = analysis.get("probe") or {}
    return float(probe.get("duration_s", 0) or 0)


def _get_height(analysis: dict) -> int:
    probe = analysis.get("probe") or {}
    return int(probe.get("height", 0) or 0)


def _motion_scores_in_range(motion: list, start: float, end: float) -> list[float]:
    return [
        float(m.get("score", 0) or 0)
        for m in motion
        if start <= float(m.get("ts", 0) or 0) <= end
    ]


def _audio_avg_in_range(audio_peaks: list, start: float, end: float) -> float:
    levels = [
        float(p.get("level", 0) or 0)
        for p in audio_peaks
        if start <= float(p.get("ts", 0) or 0) <= end
    ]
    return statistics.mean(levels) if levels else 0.0


def _has_scene_cut_in_range(scenes: list, start: float, end: float) -> bool:
    return any(
        start <= float(s.get("ts", 0) or 0) <= end
        for s in scenes
    )


def _is_silence_window(audio_peaks: list, start: float, end: float, threshold: float = 0.05) -> bool:
    """Return True if the window is effectively silent."""
    avg = _audio_avg_in_range(audio_peaks, start, end)
    return avg < threshold


# ---------------------------------------------------------------------------
# Window scorer
# ---------------------------------------------------------------------------

def _gif_window_score(
    start: float,
    end: float,
    motion: list,
    audio_peaks: list,
    scenes: list,
) -> dict:
    """
    Compute GIF-quality scores for a time window [start, end].

    Returns a dict with:
        motion_score, stability_score, loop_score, has_scene_cut, combined_score
    """
    scores = _motion_scores_in_range(motion, start, end)

    motion_score: float = statistics.mean(scores) if scores else 0.0

    # Stability: low variance in motion = smooth loop
    if len(scores) > 1:
        max_motion = max(scores) or 1.0
        std        = statistics.pstdev(scores)
        stability_score = max(0.0, 1.0 - std / max_motion)
    elif len(scores) == 1:
        stability_score = 0.5  # can't measure variance with 1 point
    else:
        stability_score = 0.0

    has_scene_cut = _has_scene_cut_in_range(scenes, start, end)

    # Scene-cut penalty is binary: 0.4 deduction if there's a cut
    scene_cut_penalty = 0.4 if has_scene_cut else 0.0

    loop_score = max(
        0.0,
        0.5 * stability_score + 0.3 * motion_score - scene_cut_penalty,
    )

    combined_score = (
        0.40 * loop_score
        + 0.35 * motion_score
        + 0.25 * stability_score
    )

    return {
        "motion_score":    round(motion_score,    3),
        "stability_score": round(stability_score, 3),
        "loop_score":      round(loop_score,      3),
        "has_scene_cut":   has_scene_cut,
        "combined_score":  round(combined_score,  3),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_gif_segments(
    analysis: dict,
    count: int = 3,
    min_duration: float = 2.0,
    max_duration: float = GIF_MAX_DURATION,
    preferred_duration: float = 3.5,
) -> list[GifSuggestion]:
    """
    Suggest up to ``count`` short segments suitable for GIF creation.

    Parameters
    ----------
    analysis:
        Output of ``build_analysis()``.
    count:
        Maximum number of GIF suggestions.
    min_duration:
        Minimum window length in seconds.
    max_duration:
        Maximum window length in seconds (hard ceiling = GIF_MAX_DURATION).
    preferred_duration:
        Sliding window size (seconds).
    """
    duration_s  = _get_duration(analysis)
    motion      = analysis.get("motion")      or []
    audio_peaks = analysis.get("audio_peaks") or []
    scenes      = analysis.get("scenes")      or []
    height      = _get_height(analysis)
    fallback    = bool(analysis.get("fallback_used", False))

    # Clamp durations
    max_duration      = min(max_duration, GIF_MAX_DURATION)
    preferred_duration = max(min_duration, min(preferred_duration, max_duration))

    # Determine recommended output size
    recommended_size = "720p" if height >= 720 else "480p"

    if duration_s < min_duration:
        logger.debug(
            "suggest_gif_segments.skip",
            extra={"reason": "video_too_short", "duration_s": duration_s},
        )
        return []

    # ── Fallback: no motion data — use audio peaks to find active sections ──
    if not motion:
        logger.debug("suggest_gif_segments.fallback", extra={"reason": "no_motion_data"})

        # Build candidate windows centred on audio peaks
        candidates: list[dict] = []
        sorted_peaks = sorted(audio_peaks, key=lambda p: float(p.get("level", 0) or 0), reverse=True)

        for peak in sorted_peaks:
            ts  = float(peak.get("ts", 0) or 0)
            half = preferred_duration / 2
            start = max(0.0, ts - half)
            end   = min(duration_s, start + preferred_duration)
            if end - start < min_duration:
                continue

            scores_dict = _gif_window_score(start, end, motion, audio_peaks, scenes)
            # Score at low confidence since we have no motion
            scores_dict["combined_score"] = scores_dict["combined_score"] * 0.4

            candidates.append({"start": start, "end": end, **scores_dict})

        # Deduplicate / pick non-overlapping
        selected = _pick_non_overlapping(candidates, count, min_gap=5.0)

        return _build_suggestions(selected, recommended_size, count)

    # ── Main path: slide preferred_duration window across video ─────────────
    step = 1.0
    candidates: list[dict] = []

    t = 0.0
    while t + preferred_duration <= duration_s:
        end = t + preferred_duration

        # Filter: exclude static windows
        scores_dict = _gif_window_score(t, end, motion, audio_peaks, scenes)
        if scores_dict["motion_score"] < GIF_MIN_MOTION:
            t += step
            continue

        # Filter: exclude silence-only windows
        if _is_silence_window(audio_peaks, t, end):
            t += step
            continue

        candidates.append({"start": t, "end": end, **scores_dict})
        t += step

    if not candidates:
        logger.debug("suggest_gif_segments.no_candidates")
        return []

    # Sort by combined_score descending, then pick non-overlapping
    candidates.sort(key=lambda c: c["combined_score"], reverse=True)
    selected = _pick_non_overlapping(candidates, count, min_gap=5.0)

    # Sort by timestamp
    selected.sort(key=lambda s: s["start"])

    return _build_suggestions(selected, recommended_size, count)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _pick_non_overlapping(
    candidates: list[dict],
    max_count: int,
    min_gap: float = 5.0,
) -> list[dict]:
    """
    Greedy selection of non-overlapping candidates.

    ``candidates`` should already be sorted by score descending.
    """
    selected: list[dict] = []
    used: list[tuple[float, float]] = []

    for cand in candidates:
        if len(selected) >= max_count:
            break

        s, e = cand["start"], cand["end"]
        overlap = any(
            s < end + min_gap and e > start - min_gap
            for start, end in used
        )
        if overlap:
            continue

        selected.append(cand)
        used.append((s, e))

    return selected


def _build_suggestions(
    selected: list[dict],
    recommended_size: str,
    count: int,
) -> list[GifSuggestion]:
    """Convert raw scored windows to GifSuggestion dicts."""
    results: list[GifSuggestion] = []

    for idx, seg in enumerate(selected[:count], start=1):
        start = seg["start"]
        end   = seg["end"]

        results.append({
            "index":            idx,
            "start":            round(start, 3),
            "end":              round(end, 3),
            "duration":         round(end - start, 3),
            "loop_score":       seg.get("loop_score",      0.0),
            "motion_score":     seg.get("motion_score",    0.0),
            "stability_score":  seg.get("stability_score", 0.0),
            "combined_score":   seg.get("combined_score",  0.0),
            "has_scene_cut":    bool(seg.get("has_scene_cut", False)),
            "thumbnail_ts":     round((start + end) / 2, 3),
            "recommended_size": recommended_size,
        })

    return results
