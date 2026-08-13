"""
Smart Clip / Highlight Detection
=================================
Detects 3–5 highlight segments from a longer video using heuristic signals.
No ML required — uses audio peaks, scene density, motion levels.

Output: list of ClipSuggestion dicts.
"""

from __future__ import annotations

import statistics
from typing import Any

from app.core.structured_log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

CLIP_DETECT_MIN_VIDEO_DURATION: float = 60.0  # seconds

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ClipSuggestion = dict  # see shape below:
# {
#     "index": int,
#     "start": float,
#     "end": float,
#     "duration": float,
#     "confidence": float,
#     "label": str,
#     "reason": str,
#     "motion_score": float,
#     "audio_level": float,
#     "thumbnail_ts": float,
#     "signals_used": list[str],
# }

# Vietnamese reason labels
_REASON_MAP: dict[str, str] = {
    "high_activity":    "Đoạn có nhiều chuyển động và âm thanh cao",
    "speech_burst":     "Đoạn có giọng nói/âm thanh nổi bật",
    "scene_peak":       "Đoạn có nhiều cảnh chuyển đổi",
    "high_audio":       "Đoạn có âm thanh nổi bật",
    "general_highlight": "Đoạn nổi bật được phát hiện tự động",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_duration(analysis: dict) -> float:
    probe = analysis.get("probe") or {}
    return float(probe.get("duration_s", 0) or 0)


def _motion_avg_in_range(motion: list, start: float, end: float) -> float:
    scores = [
        float(m.get("score", 0) or 0)
        for m in motion
        if start <= float(m.get("ts", 0) or 0) <= end
    ]
    return statistics.mean(scores) if scores else 0.0


def _audio_avg_in_range(audio_peaks: list, start: float, end: float) -> float:
    levels = [
        float(p.get("level", 0) or 0)
        for p in audio_peaks
        if start <= float(p.get("ts", 0) or 0) <= end
    ]
    return statistics.mean(levels) if levels else 0.0


def _scene_count_in_range(scenes: list, start: float, end: float) -> int:
    return sum(
        1 for s in scenes
        if start <= float(s.get("ts", 0) or 0) <= end
    )


def _signals_list(analysis: dict) -> list[str]:
    used = []
    if analysis.get("silence"):
        used.append("silence")
    if analysis.get("scenes"):
        used.append("scenes")
    if analysis.get("audio_peaks"):
        used.append("audio_peaks")
    if analysis.get("motion"):
        used.append("motion")
    return used


# ---------------------------------------------------------------------------
# Segment scoring
# ---------------------------------------------------------------------------

def _score_segments(
    analysis: dict,
    segment_duration: float = 15.0,
) -> list[dict]:
    """
    Divide video into overlapping segments and score each one.

    Step is 5 s; segments overlap.

    Returns a list of dicts sorted by combined_score descending:
        {start, end, audio_level, motion_score, scene_count, combined_score}
    """
    duration_s  = _get_duration(analysis)
    audio_peaks = analysis.get("audio_peaks") or []
    motion      = analysis.get("motion")      or []
    scenes      = analysis.get("scenes")      or []

    if duration_s <= 0:
        return []

    step = 5.0
    segments: list[dict] = []

    t = 0.0
    while t + segment_duration <= duration_s:
        end = t + segment_duration

        audio_level  = _audio_avg_in_range(audio_peaks, t, end)
        motion_score = _motion_avg_in_range(motion, t, end)
        scene_count  = _scene_count_in_range(scenes, t, end)

        segments.append({
            "start":        t,
            "end":          end,
            "audio_level":  audio_level,
            "motion_score": motion_score,
            "scene_count":  scene_count,
            "combined_score": 0.0,   # filled after normalisation
        })
        t += step

    if not segments:
        return []

    # Normalise each signal to [0, 1] across all segments
    max_audio  = max(s["audio_level"]  for s in segments) or 1.0
    max_motion = max(s["motion_score"] for s in segments) or 1.0
    max_scene  = max(s["scene_count"]  for s in segments) or 1.0

    for s in segments:
        audio_norm  = s["audio_level"]  / max_audio
        motion_norm = s["motion_score"] / max_motion
        scene_norm  = s["scene_count"]  / max_scene

        s["combined_score"] = (
            0.35 * audio_norm
            + 0.40 * motion_norm
            + 0.25 * scene_norm
        )

    return sorted(segments, key=lambda s: s["combined_score"], reverse=True)


# ---------------------------------------------------------------------------
# Overlap merging
# ---------------------------------------------------------------------------

def _merge_overlapping(segments: list, min_gap: float = 5.0) -> list:
    """
    Merge overlapping or too-close segments into one.

    ``segments`` must be sorted by start time.
    """
    if not segments:
        return []

    merged = [dict(segments[0])]

    for seg in segments[1:]:
        last = merged[-1]
        if seg["start"] < last["end"] + min_gap:
            # Extend the previous segment if the new one goes further
            if seg["end"] > last["end"]:
                last["end"]          = seg["end"]
                last["motion_score"] = max(last.get("motion_score", 0), seg.get("motion_score", 0))
                last["audio_level"]  = max(last.get("audio_level",  0), seg.get("audio_level",  0))
                last["scene_count"]  = last.get("scene_count", 0) + seg.get("scene_count", 0)
                last["combined_score"] = max(
                    last.get("combined_score", 0), seg.get("combined_score", 0)
                )
        else:
            merged.append(dict(seg))

    return merged


# ---------------------------------------------------------------------------
# Label assignment
# ---------------------------------------------------------------------------

def _assign_labels(segment: dict, analysis: dict) -> str:
    """Return a label string based on dominant signal in segment."""
    audio_level  = segment.get("audio_level",  0.0)
    motion_score = segment.get("motion_score", 0.0)
    scene_count  = segment.get("scene_count",  0)

    duration = segment.get("end", 0) - segment.get("start", 0)
    scene_density = scene_count / duration if duration > 0 else 0.0

    if audio_level > 0.7:
        return "speech_burst" if motion_score < 0.5 else "high_activity"
    if motion_score > 0.7:
        return "high_activity"
    if scene_density > 0.5:
        return "scene_peak"

    return "general_highlight"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_highlights(
    analysis: dict,
    max_clips: int = 5,
    min_duration: float = 8.0,
    max_duration: float = 30.0,
    min_video_length: float = 60.0,
) -> list[ClipSuggestion]:
    """
    Detect highlight segments from a video using heuristic signals.

    Parameters
    ----------
    analysis:
        Output of ``build_analysis()``.
    max_clips:
        Maximum number of highlights to return.
    min_duration:
        Minimum clip length in seconds.
    max_duration:
        Maximum clip length in seconds.
    min_video_length:
        Skip videos shorter than this (seconds).
    """
    duration_s   = _get_duration(analysis)
    signals_used = _signals_list(analysis)

    if duration_s < min_video_length:
        logger.debug(
            "detect_highlights.skip",
            extra={"reason": "video_too_short", "duration_s": duration_s, "min_video_length": min_video_length},
        )
        return []

    # Use segment_duration tuned to the requested clip length
    seg_dur = max(min_duration, min(max_duration, 15.0))
    scored  = _score_segments(analysis, segment_duration=seg_dur)

    if not scored:
        return []

    # Greedily pick top-scoring non-overlapping segments
    selected: list[dict] = []
    used_ranges: list[tuple[float, float]] = []

    for seg in scored:
        if len(selected) >= max_clips:
            break

        # Check overlap with already-selected segments
        overlap = any(
            seg["start"] < end + 5.0 and seg["end"] > start - 5.0
            for start, end in used_ranges
        )
        if overlap:
            continue

        # Clip the segment to [min_duration, max_duration]
        seg_len = seg["end"] - seg["start"]
        if seg_len < min_duration:
            continue
        if seg_len > max_duration:
            seg = dict(seg)
            seg["end"] = seg["start"] + max_duration

        selected.append(seg)
        used_ranges.append((seg["start"], seg["end"]))

    if not selected:
        return []

    # Sort by timestamp for logical playback order
    selected.sort(key=lambda s: s["start"])

    # Determine confidence based on available signals
    num_sig = len(signals_used)
    if num_sig >= 3:
        base_confidence = 0.80
    elif num_sig == 2:
        base_confidence = 0.65
    elif num_sig == 1:
        base_confidence = 0.45
    else:
        base_confidence = 0.25

    clips: list[ClipSuggestion] = []
    for idx, seg in enumerate(selected, start=1):
        label  = _assign_labels(seg, analysis)
        reason = _REASON_MAP.get(label, _REASON_MAP["general_highlight"])

        start = seg["start"]
        end   = seg["end"]

        # Confidence is scaled by the segment's combined score
        confidence = min(
            0.95,
            base_confidence * (0.7 + 0.3 * seg.get("combined_score", 0.5)),
        )

        clips.append({
            "index":        idx,
            "start":        round(start, 3),
            "end":          round(end, 3),
            "duration":     round(end - start, 3),
            "confidence":   round(confidence, 3),
            "label":        label,
            "reason":       reason,
            "motion_score": round(float(seg.get("motion_score", 0)), 3),
            "audio_level":  round(float(seg.get("audio_level",  0)), 3),
            "thumbnail_ts": round((start + end) / 2, 3),
            "signals_used": signals_used,
        })

    return clips
