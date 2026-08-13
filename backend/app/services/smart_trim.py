"""
Smart Trim Suggestions
======================
Analyzes media signals to suggest optimal trim start/end points.

Modes:
  remove_dead_intro   — skip leading silence/low-motion content
  remove_dead_outro   — skip trailing silence/low-motion content
  auto_focus_segment  — find highest-activity window fitting duration
  short_clip_15s      — best 15-second window
  short_clip_30s      — best 30-second window
  loopable_segment    — find segment suitable for smooth GIF loop

All functions return TrimSuggestion dict.
"""

from __future__ import annotations

import statistics
from typing import Any

from app.core.structured_log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

TrimSuggestion = dict  # see shape below:
# {
#     "mode": str,
#     "suggested_start": float,
#     "suggested_end": float,
#     "duration": float,
#     "confidence": float,
#     "reasons": list[str],
#     "signals_used": list[str],
#     "fallback_used": bool,
# }

_ALL_MODES = [
    "remove_dead_intro",
    "remove_dead_outro",
    "auto_focus_segment",
    "short_clip_15s",
    "short_clip_30s",
    "loopable_segment",
]

_MIN_INTRO_SKIP = 0.5   # seconds — ignore tiny intro corrections
_MIN_OUTRO_CUT  = 2.0   # seconds — ignore tiny outro cuts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_duration(analysis: dict) -> float:
    """Extract video duration from probe data."""
    probe = analysis.get("probe") or {}
    return float(probe.get("duration_s", 0) or 0)


def _motion_avg_in_range(motion: list, start: float, end: float) -> float:
    """Average motion score for frames within [start, end]."""
    if not motion:
        return 0.0
    scores = [
        float(m.get("score", 0) or 0)
        for m in motion
        if start <= float(m.get("ts", 0) or 0) <= end
    ]
    return statistics.mean(scores) if scores else 0.0


def _audio_avg_in_range(audio_peaks: list, start: float, end: float) -> float:
    """Average audio level for peaks within [start, end]."""
    if not audio_peaks:
        return 0.0
    levels = [
        float(p.get("level", 0) or 0)
        for p in audio_peaks
        if start <= float(p.get("ts", 0) or 0) <= end
    ]
    return statistics.mean(levels) if levels else 0.0


def _scene_count_in_range(scenes: list, start: float, end: float) -> int:
    """Count scene changes within [start, end]."""
    return sum(
        1 for s in scenes
        if start <= float(s.get("ts", 0) or 0) <= end
    )


# ---------------------------------------------------------------------------
# Private detection functions
# ---------------------------------------------------------------------------

def _find_dead_intro(
    silence: list,
    scenes: list,
    motion: list,
    duration_s: float,
) -> float:
    """
    Find how far into the video the dead intro extends.

    Returns suggested_start in seconds (0.0 = no dead intro detected).
    """
    suggested_start = 0.0

    # Rule 1: leading silence that starts at 0 and ends before 30 s
    for seg in sorted(silence, key=lambda s: float(s.get("start", 0) or 0)):
        seg_start = float(seg.get("start", 0) or 0)
        seg_end   = float(seg.get("end", 0)   or 0)
        if seg_start <= 0.1 and seg_end < 30.0:
            candidate = seg_end + 0.3
            if candidate >= _MIN_INTRO_SKIP:
                suggested_start = max(suggested_start, candidate)
            break  # only look at the first silence block

    # Rule 2: first scene change before 15 s with very low motion before it
    sorted_scenes = sorted(scenes, key=lambda s: float(s.get("ts", 0) or 0))
    if sorted_scenes:
        first_scene_ts = float(sorted_scenes[0].get("ts", 0) or 0)
        if first_scene_ts < 15.0:
            pre_motion = _motion_avg_in_range(motion, 0.0, first_scene_ts)
            if pre_motion < 0.2:
                candidate = first_scene_ts
                if candidate >= _MIN_INTRO_SKIP:
                    suggested_start = max(suggested_start, candidate)

    return suggested_start


def _find_dead_outro(
    silence: list,
    motion: list,
    duration_s: float,
) -> float:
    """
    Find the last point of real activity (everything after = dead outro).

    Returns suggested_end in seconds (duration_s = no dead outro detected).
    """
    if duration_s <= 0:
        return duration_s

    outro_threshold = duration_s * 0.70  # examine last 30 % of video

    # Find last silence block that starts in the final 30 %
    last_silence_end: float | None = None
    for seg in sorted(silence, key=lambda s: float(s.get("start", 0) or 0), reverse=True):
        seg_start = float(seg.get("start", 0) or 0)
        seg_end   = float(seg.get("end",   0) or 0)
        if seg_start >= outro_threshold:
            last_silence_end = seg_start  # activity ended where silence began
            break

    # Find last high-motion frame in final 30 %
    high_motion_ts: float | None = None
    for m in sorted(motion, key=lambda m: float(m.get("ts", 0) or 0), reverse=True):
        ts    = float(m.get("ts", 0)    or 0)
        score = float(m.get("score", 0) or 0)
        if ts >= outro_threshold and score >= 0.2:
            high_motion_ts = ts
            break

    # Take the later of the two signals
    candidates = [t for t in (last_silence_end, high_motion_ts) if t is not None]
    if not candidates:
        return duration_s

    suggested_end = max(candidates)

    # Enforce minimum outro cut
    if duration_s - suggested_end < _MIN_OUTRO_CUT:
        return duration_s

    return suggested_end


def _score_window(
    start: float,
    end: float,
    audio_peaks: list,
    motion: list,
    scenes: list,
) -> float:
    """
    Compute activity score for a time window [start, end].

    Score = audio_level_avg * 0.4 + motion_avg * 0.4 + scene_density * 0.2
    """
    window = end - start
    if window <= 0:
        return 0.0

    audio_avg  = _audio_avg_in_range(audio_peaks, start, end)
    motion_avg = _motion_avg_in_range(motion, start, end)

    scene_count   = _scene_count_in_range(scenes, start, end)
    scene_density = scene_count / window  # changes per second

    # Normalise scene_density to ~0-1 (cap at 1 change/s)
    scene_norm = min(scene_density, 1.0)

    return audio_avg * 0.4 + motion_avg * 0.4 + scene_norm * 0.2


# ---------------------------------------------------------------------------
# Loopable segment helper
# ---------------------------------------------------------------------------

def _find_loopable_segment(
    motion: list,
    scenes: list,
    duration_s: float,
    min_dur: float = 3.0,
    max_dur: float = 6.0,
) -> tuple[float, float, float]:
    """
    Find a window suitable for a smooth GIF loop.

    Returns (start, end, confidence).
    """
    best_start    = 0.0
    best_end      = min(min_dur, duration_s)
    best_score    = -1.0
    best_conf     = 0.2

    step  = 0.5
    dur   = (min_dur + max_dur) / 2  # preferred ~4.5 s window

    t = 0.0
    while t + dur <= duration_s:
        win_end = t + dur

        # Check for scene cuts inside this window (bad for loops)
        has_cut = _scene_count_in_range(scenes, t, win_end) > 0

        if has_cut:
            t += step
            continue

        # Motion consistency: low variance = smooth loop
        scores = [
            float(m.get("score", 0) or 0)
            for m in motion
            if t <= float(m.get("ts", 0) or 0) <= win_end
        ]
        if not scores:
            t += step
            continue

        avg_motion = statistics.mean(scores)
        if avg_motion < 0.1:
            t += step
            continue  # too static

        variance   = statistics.pstdev(scores) if len(scores) > 1 else 0.5
        consistency = max(0.0, 1.0 - variance)  # 1 = perfectly smooth
        loop_score  = consistency * 0.6 + avg_motion * 0.4

        if loop_score > best_score:
            best_score = loop_score
            best_start = t
            best_end   = win_end

            # Confidence based on how many motion samples
            if len(scores) >= 5:
                best_conf = min(0.85, 0.5 + loop_score * 0.4)
            else:
                best_conf = min(0.6, 0.3 + loop_score * 0.3)

        t += step

    return best_start, best_end, best_conf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_trim(
    analysis: dict,
    mode: str = "auto_focus_segment",
    target_duration: float | None = None,
) -> TrimSuggestion:
    """
    Return a TrimSuggestion dict for the requested mode.

    Parameters
    ----------
    analysis:
        Output of ``build_analysis()`` — may have partial/missing signals.
    mode:
        One of ``_ALL_MODES``.
    target_duration:
        Desired clip length in seconds (used by ``auto_focus_segment``).
    """
    probe       = analysis.get("probe")       or {}
    silence     = analysis.get("silence")     or []
    scenes      = analysis.get("scenes")      or []
    audio_peaks = analysis.get("audio_peaks") or []
    motion      = analysis.get("motion")      or []
    warnings    = analysis.get("warnings")    or []
    fallback    = bool(analysis.get("fallback_used", False))

    duration_s = float(probe.get("duration_s", 0) or 0)

    signals_used: list[str] = []
    if silence:
        signals_used.append("silence")
    if scenes:
        signals_used.append("scenes")
    if audio_peaks:
        signals_used.append("audio_peaks")
    if motion:
        signals_used.append("motion")

    # ── short_clip_* delegate to auto_focus_segment ─────────────────────
    if mode == "short_clip_15s":
        return suggest_trim(analysis, "auto_focus_segment", target_duration=15.0)
    if mode == "short_clip_30s":
        return suggest_trim(analysis, "auto_focus_segment", target_duration=30.0)

    # ── remove_dead_intro ───────────────────────────────────────────────
    if mode == "remove_dead_intro":
        intro_end  = _find_dead_intro(silence, scenes, motion, duration_s)
        reasons    = []
        confidence = 0.2

        if intro_end > 0:
            reasons.append("silence_intro" if silence else "low_motion_intro")
            if len(signals_used) >= 2:
                confidence = 0.80
            else:
                confidence = 0.50

        return {
            "mode":            mode,
            "suggested_start": round(intro_end, 3),
            "suggested_end":   round(duration_s, 3),
            "duration":        round(duration_s - intro_end, 3),
            "confidence":      round(confidence, 3),
            "reasons":         reasons or ["no_dead_intro_detected"],
            "signals_used":    signals_used,
            "fallback_used":   fallback,
        }

    # ── remove_dead_outro ───────────────────────────────────────────────
    if mode == "remove_dead_outro":
        outro_start = _find_dead_outro(silence, motion, duration_s)
        reasons     = []
        confidence  = 0.2

        if outro_start < duration_s:
            reasons.append("silence_outro" if silence else "low_motion_outro")
            if len(signals_used) >= 2:
                confidence = 0.80
            else:
                confidence = 0.50

        return {
            "mode":            mode,
            "suggested_start": 0.0,
            "suggested_end":   round(outro_start, 3),
            "duration":        round(outro_start, 3),
            "confidence":      round(confidence, 3),
            "reasons":         reasons or ["no_dead_outro_detected"],
            "signals_used":    signals_used,
            "fallback_used":   fallback,
        }

    # ── loopable_segment ────────────────────────────────────────────────
    if mode == "loopable_segment":
        loop_start, loop_end, confidence = _find_loopable_segment(
            motion, scenes, duration_s
        )
        reasons = ["low_motion_variance", "no_scene_cut_in_window"]

        return {
            "mode":            mode,
            "suggested_start": round(loop_start, 3),
            "suggested_end":   round(loop_end, 3),
            "duration":        round(loop_end - loop_start, 3),
            "confidence":      round(confidence, 3),
            "reasons":         reasons,
            "signals_used":    signals_used,
            "fallback_used":   fallback,
        }

    # ── auto_focus_segment (default) ────────────────────────────────────
    # Determine the search range (strip intro/outro first)
    range_start = _find_dead_intro(silence, scenes, motion, duration_s)
    range_end   = _find_dead_outro(silence, motion, duration_s)

    if range_end <= range_start:
        range_start = 0.0
        range_end   = duration_s

    reasons    : list[str] = []
    confidence : float     = 0.2
    fallback_used          = fallback

    if target_duration and target_duration > 0:
        # Slide a window of target_duration across [range_start, range_end]
        step = max(0.5, target_duration / 20)
        best_start = range_start
        best_end   = min(range_start + target_duration, range_end)
        best_score = -1.0

        t = range_start
        while t + target_duration <= range_end:
            score = _score_window(t, t + target_duration, audio_peaks, motion, scenes)
            if score > best_score:
                best_score = score
                best_start = t
                best_end   = t + target_duration
            t += step

        reasons.append("highest_activity_window")
        num_sig = len(signals_used)
        if best_score >= 0.5 and num_sig >= 2:
            confidence = 0.85
        elif best_score >= 0.3 or num_sig >= 2:
            confidence = 0.65
        elif num_sig == 1:
            confidence = 0.45
        else:
            confidence     = 0.2
            fallback_used  = True

        return {
            "mode":            mode,
            "suggested_start": round(best_start, 3),
            "suggested_end":   round(best_end,   3),
            "duration":        round(best_end - best_start, 3),
            "confidence":      round(confidence, 3),
            "reasons":         reasons,
            "signals_used":    signals_used,
            "fallback_used":   fallback_used,
        }

    # No target_duration: just use [intro_end, outro_start] window
    reasons = ["intro_outro_trimmed"]
    num_sig = len(signals_used)
    if num_sig >= 2:
        confidence = 0.75
    elif num_sig == 1:
        confidence = 0.50
    else:
        confidence    = 0.2
        fallback_used = True

    return {
        "mode":            mode,
        "suggested_start": round(range_start, 3),
        "suggested_end":   round(range_end,   3),
        "duration":        round(range_end - range_start, 3),
        "confidence":      round(confidence, 3),
        "reasons":         reasons,
        "signals_used":    signals_used,
        "fallback_used":   fallback_used,
    }


def suggest_all_trim_modes(
    analysis: dict,
    duration_limit_s: float = 3600.0,
) -> list[TrimSuggestion]:
    """
    Return suggestions for all 6 trim modes.

    Skips modes that are not applicable:
      - short_clip_* on video shorter than 20 s
      - loopable_segment on video shorter than 6 s

    Parameters
    ----------
    analysis:
        Output of ``build_analysis()``.
    duration_limit_s:
        Upper bound — skip if video is longer than this (avoids huge sliding
        windows for e.g. 24-hour streams).
    """
    probe      = analysis.get("probe") or {}
    duration_s = float(probe.get("duration_s", 0) or 0)

    results: list[TrimSuggestion] = []

    for mode in _ALL_MODES:
        # Skip short-clip modes on very short videos
        if mode in ("short_clip_15s", "short_clip_30s") and duration_s < 20.0:
            logger.debug(
                "suggest_all_trim_modes.skip",
                extra={"mode": mode, "reason": "video_too_short", "duration_s": duration_s},
            )
            continue

        # Skip loopable on very short videos
        if mode == "loopable_segment" and duration_s < 6.0:
            logger.debug(
                "suggest_all_trim_modes.skip",
                extra={"mode": mode, "reason": "video_too_short_for_loop", "duration_s": duration_s},
            )
            continue

        # Skip 30 s clip on videos shorter than 30 s
        if mode == "short_clip_30s" and duration_s < 30.0:
            continue

        try:
            suggestion = suggest_trim(analysis, mode=mode)
            results.append(suggestion)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "suggest_all_trim_modes.error",
                extra={"mode": mode, "error": str(exc)},
            )

    return results
