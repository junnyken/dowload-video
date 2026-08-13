"""
Media Analyzer
==============
FFmpeg-based heuristic analysis for smart trim/clip/GIF suggestions.

All analysis runs via ffmpeg/ffprobe subprocess calls.
No GPU required. Fallbacks return empty suggestions, never crash.

Analyzer pipeline:
  1. probe_media()        — duration, streams, basic metadata
  2. detect_silence()     — silence periods (for dead-intro/outro detection)
  3. detect_scenes()      — scene change timestamps
  4. detect_audio_peaks() — RMS audio energy peaks
  5. compute_motion_score() — approximate motion level per segment
  6. build_analysis()     — orchestrate all detectors, merge into result dict

All functions:
  - accept a local file path
  - return typed dicts or empty fallback on error
  - log errors with get_logger(__name__)
  - have a MAX_ANALYSIS_DURATION guard (skip analysis on files > this limit)
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import time
from typing import Any

from app.core.structured_log import get_logger

logger = get_logger(__name__)

# ─────────────────────────────────────────────
# Constants (all env-overridable)
# ─────────────────────────────────────────────
MAX_ANALYSIS_DURATION: int = int(os.getenv("AI_MAX_ANALYSIS_SECONDS", "3600"))  # 1 h
SCENE_CHANGE_THRESHOLD: float = float(os.getenv("AI_SCENE_THRESHOLD", "0.35"))
SILENCE_THRESHOLD_DB: str = os.getenv("AI_SILENCE_DB", "-35dB")
SILENCE_MIN_DURATION: float = float(os.getenv("AI_SILENCE_MIN_S", "0.8"))
ANALYSIS_TIMEOUT: int = int(os.getenv("AI_ANALYSIS_TIMEOUT_S", "120"))

# Noise floor / ceiling used for audio normalisation
_AUDIO_FLOOR_DB: float = -60.0
_AUDIO_CEIL_DB: float = 0.0

# How many seconds of the start of a file we scan for silence / scenes
_SILENCE_SCAN_LIMIT: float = 300.0
_SCENE_SCAN_LIMIT: float = 1200.0

# Fingerprint reads this many bytes from the start and end of the file
_FINGERPRINT_CHUNK: int = 65536  # 64 KB


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _run(cmd: list[str], timeout: int = ANALYSIS_TIMEOUT) -> tuple[int, str, str]:
    """Run a subprocess, return (returncode, stdout, stderr). Never raises."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return result.returncode, result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg_timeout", extra={"cmd": cmd[0], "timeout": timeout})
        return -1, "", "timeout"
    except FileNotFoundError:
        logger.error("ffmpeg_not_found", extra={"cmd": cmd[0]})
        return -1, "", "not_found"
    except Exception as exc:
        logger.error("ffmpeg_unexpected_error", extra={"error": str(exc)})
        return -1, "", str(exc)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ─────────────────────────────────────────────
# 1. probe_media
# ─────────────────────────────────────────────

def probe_media(path: str) -> dict[str, Any]:
    """
    Run ffprobe on *path* and return a summary dict.

    Returns:
        {
            duration_s: float,
            width: int | None,
            height: int | None,
            fps: float | None,
            has_audio: bool,
            has_video: bool,
            bitrate: int | None,   # bits/s
            format_name: str,
            nb_streams: int,
        }

    On any error returns a safe fallback with ``error="probe_failed"``.
    """
    _fallback: dict[str, Any] = {
        "duration_s": 0.0,
        "width": None,
        "height": None,
        "fps": None,
        "has_audio": False,
        "has_video": False,
        "bitrate": None,
        "format_name": "unknown",
        "nb_streams": 0,
        "error": "probe_failed",
    }

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    rc, stdout, stderr = _run(cmd)
    if rc != 0 or not stdout.strip():
        logger.warning("probe_failed", extra={"path": path, "stderr": stderr[:200]})
        return _fallback

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        logger.warning("probe_json_error", extra={"path": path, "error": str(exc)})
        return _fallback

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    duration_s = float(fmt.get("duration", 0) or 0)
    bitrate = int(fmt.get("bit_rate", 0) or 0) or None
    format_name = fmt.get("format_name", "unknown")

    has_audio = False
    has_video = False
    width: int | None = None
    height: int | None = None
    fps: float | None = None

    for stream in streams:
        codec_type = stream.get("codec_type", "")
        if codec_type == "video":
            has_video = True
            width = stream.get("width") or width
            height = stream.get("height") or height
            # fps may be expressed as "30000/1001"
            r_frame_rate = stream.get("r_frame_rate", "")
            if r_frame_rate and "/" in r_frame_rate:
                try:
                    num, den = r_frame_rate.split("/")
                    fps_val = float(num) / float(den) if float(den) else None
                    if fps_val and fps_val > 0:
                        fps = round(fps_val, 3)
                except (ValueError, ZeroDivisionError):
                    pass
            # Fall back to avg_frame_rate
            if fps is None:
                avg = stream.get("avg_frame_rate", "")
                if avg and "/" in avg:
                    try:
                        num, den = avg.split("/")
                        fps_val = float(num) / float(den) if float(den) else None
                        if fps_val and fps_val > 0:
                            fps = round(fps_val, 3)
                    except (ValueError, ZeroDivisionError):
                        pass
        elif codec_type == "audio":
            has_audio = True

    return {
        "duration_s": duration_s,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": has_audio,
        "has_video": has_video,
        "bitrate": bitrate,
        "format_name": format_name,
        "nb_streams": len(streams),
    }


# ─────────────────────────────────────────────
# 2. detect_silence
# ─────────────────────────────────────────────

def detect_silence(path: str, duration_s: float) -> list[dict[str, float]]:
    """
    Detect silence periods in the audio track using FFmpeg's silencedetect filter.

    Only analyses the first ``_SILENCE_SCAN_LIMIT`` seconds to keep it fast.

    Returns:
        [ {start: float, end: float, duration: float}, ... ]

    On any error returns ``[]``.
    """
    if duration_s <= 0:
        return []

    scan_end = min(duration_s, _SILENCE_SCAN_LIMIT)
    af_filter = f"silencedetect=n={SILENCE_THRESHOLD_DB}:d={SILENCE_MIN_DURATION}"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i", path,
        "-t", str(scan_end),
        "-af", af_filter,
        "-f", "null",
        "-",
    ]
    rc, _stdout, stderr = _run(cmd)

    # silencedetect writes to stderr regardless of rc
    combined = stderr

    silence_starts: list[float] = []
    silence_ends: list[float] = []

    for line in combined.splitlines():
        m_start = re.search(r"silence_start:\s*([\d.]+)", line)
        if m_start:
            try:
                silence_starts.append(float(m_start.group(1)))
            except ValueError:
                pass

        m_end = re.search(r"silence_end:\s*([\d.]+)", line)
        if m_end:
            try:
                silence_ends.append(float(m_end.group(1)))
            except ValueError:
                pass

    periods: list[dict[str, float]] = []
    for start, end in zip(silence_starts, silence_ends):
        dur = round(end - start, 3)
        if dur >= SILENCE_MIN_DURATION:
            periods.append({"start": round(start, 3), "end": round(end, 3), "duration": dur})

    # Handle trailing silence (file ended during silence)
    if len(silence_starts) > len(silence_ends):
        start = silence_starts[-1]
        end = scan_end
        dur = round(end - start, 3)
        if dur >= SILENCE_MIN_DURATION:
            periods.append({"start": round(start, 3), "end": round(end, 3), "duration": dur})

    return periods


# ─────────────────────────────────────────────
# 3. detect_scenes
# ─────────────────────────────────────────────

def detect_scenes(path: str, duration_s: float) -> list[float]:
    """
    Detect scene-change timestamps using FFmpeg's ``select`` + ``showinfo`` filter.

    Only analyses the first ``_SCENE_SCAN_LIMIT`` seconds.

    Returns:
        [timestamp_float, ...] — timestamps (seconds) where scene cuts occur.

    On any error returns ``[]``.
    """
    if duration_s <= 0:
        return []

    scan_end = min(duration_s, _SCENE_SCAN_LIMIT)
    vf = f"select=gt(scene\\,{SCENE_CHANGE_THRESHOLD}),showinfo"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i", path,
        "-t", str(scan_end),
        "-vf", vf,
        "-f", "null",
        "-",
    ]
    _rc, _stdout, stderr = _run(cmd)

    timestamps: list[float] = []
    # showinfo emits: [Parsed_showinfo_1 @ ...] n:   0 pts:    0 pts_time:0
    for line in stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            try:
                ts = float(m.group(1))
                timestamps.append(round(ts, 3))
            except ValueError:
                pass

    return sorted(set(timestamps))


# ─────────────────────────────────────────────
# 4. detect_audio_peaks
# ─────────────────────────────────────────────

def detect_audio_peaks(
    path: str,
    duration_s: float,
    segment_size: float = 5.0,
) -> list[dict[str, Any]]:
    """
    Compute per-segment RMS audio energy using FFmpeg ``astats``.

    Each segment is ``segment_size`` seconds long (last may be shorter).

    Returns:
        [
            {
                start: float,
                end: float,
                rms_db: float,
                normalized_level: float,  # 0.0 – 1.0
            },
            ...
        ]

    On any error returns ``[]``.
    """
    if duration_s <= 0 or segment_size <= 0:
        return []

    # asetnsamples resets the stats window every N samples; we approximate
    # segment_size seconds using 44100 Hz as reference sample rate.
    n_samples = int(44100 * segment_size)
    af = f"asetnsamples=n={n_samples},astats=metadata=1:reset=1"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i", path,
        "-af", af,
        "-f", "null",
        "-",
    ]
    _rc, _stdout, stderr = _run(cmd)

    rms_values: list[float] = []
    for line in stderr.splitlines():
        m = re.search(r"lavfi\.astats\.RMS_level=(-?[\d.]+|nan|inf|-inf)", line, re.IGNORECASE)
        if m:
            raw = m.group(1).lower()
            if raw in ("nan", "inf", "-inf"):
                rms_values.append(_AUDIO_FLOOR_DB)
            else:
                try:
                    rms_values.append(float(raw))
                except ValueError:
                    rms_values.append(_AUDIO_FLOOR_DB)

    if not rms_values:
        return []

    db_range = _AUDIO_CEIL_DB - _AUDIO_FLOOR_DB  # 60.0

    segments: list[dict[str, Any]] = []
    for idx, rms_db in enumerate(rms_values):
        start = idx * segment_size
        end = min(start + segment_size, duration_s)
        clamped = _clamp(rms_db, _AUDIO_FLOOR_DB, _AUDIO_CEIL_DB)
        normalized = round((clamped - _AUDIO_FLOOR_DB) / db_range, 4)
        segments.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "rms_db": round(rms_db, 2),
                "normalized_level": normalized,
            }
        )

    return segments


# ─────────────────────────────────────────────
# 5. compute_motion_score
# ─────────────────────────────────────────────

def compute_motion_score(
    path: str,
    duration_s: float,
    sample_interval: float = 5.0,
) -> list[dict[str, Any]]:
    """
    Estimate motion level per segment by extracting tiny (64×36) greyscale
    frames at ``sample_interval`` intervals and computing the standard
    deviation of raw pixel values across consecutive frames.

    A higher standard deviation between successive frames indicates more motion.

    Returns:
        [
            {start: float, end: float, motion_score: float},  # score 0-1
            ...
        ]

    Falls back to uniform 0.5 scores if the frame extraction fails.
    """
    if duration_s <= 0 or sample_interval <= 0:
        return _uniform_motion(duration_s, sample_interval)

    fps_str = f"1/{sample_interval}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-i", path,
        "-vf", f"fps={fps_str},scale=64:36",
        "-f", "image2pipe",
        "-pix_fmt", "gray",
        "-vcodec", "rawvideo",
        "-",
    ]
    rc, stdout_bytes_str, stderr = _run_bytes(cmd)

    frame_bytes = 64 * 36  # width * height * 1 (gray)

    if rc != 0 or not stdout_bytes_str:
        logger.debug(
            "motion_score_fallback",
            extra={"path": path, "reason": stderr[:100] if stderr else "no_output"},
        )
        return _uniform_motion(duration_s, sample_interval)

    # Parse raw bytes into frames
    raw = stdout_bytes_str
    n_frames = len(raw) // frame_bytes
    if n_frames == 0:
        return _uniform_motion(duration_s, sample_interval)

    frames: list[list[int]] = []
    for i in range(n_frames):
        chunk = raw[i * frame_bytes: (i + 1) * frame_bytes]
        frames.append(list(chunk))

    # Compute per-frame motion scores via successive-frame difference std dev
    scores: list[float] = []
    for i, frame in enumerate(frames):
        if i == 0:
            scores.append(0.0)
            continue
        prev = frames[i - 1]
        diff = [abs(a - b) for a, b in zip(frame, prev)]
        mean_diff = sum(diff) / len(diff)
        # normalise to 0-1: max plausible mean diff is ~128 (half of 0-255)
        scores.append(_clamp(mean_diff / 128.0, 0.0, 1.0))

    segments: list[dict[str, Any]] = []
    for idx, score in enumerate(scores):
        start = idx * sample_interval
        end = min(start + sample_interval, duration_s)
        segments.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "motion_score": round(score, 4),
            }
        )

    return segments


def _uniform_motion(
    duration_s: float,
    sample_interval: float,
) -> list[dict[str, Any]]:
    """Return uniform 0.5 motion scores for the whole file."""
    segments: list[dict[str, Any]] = []
    t = 0.0
    while t < duration_s:
        end = min(t + sample_interval, duration_s)
        segments.append({"start": round(t, 3), "end": round(end, 3), "motion_score": 0.5})
        t = end
    return segments


def _run_bytes(cmd: list[str], timeout: int = ANALYSIS_TIMEOUT) -> tuple[int, bytes, str]:
    """
    Like ``_run`` but returns raw bytes for stdout (needed for rawvideo pipe).
    Never raises.
    """
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        logger.warning("motion_score_timeout", extra={"cmd": cmd[0], "timeout": timeout})
        return -1, b"", "timeout"
    except FileNotFoundError:
        logger.error("ffmpeg_not_found", extra={"cmd": cmd[0]})
        return -1, b"", "not_found"
    except Exception as exc:
        logger.error("motion_score_error", extra={"error": str(exc)})
        return -1, b"", str(exc)


# ─────────────────────────────────────────────
# 6. build_analysis  (orchestrator)
# ─────────────────────────────────────────────

def build_analysis(path: str) -> dict[str, Any]:
    """
    Orchestrate all FFmpeg-based detectors and return a unified result dict.

    Short-circuits early if ``probe_media`` fails or the file exceeds
    ``MAX_ANALYSIS_DURATION``.

    Returns:
        {
            "probe":        {duration_s, has_audio, has_video, width, height, fps, ...},
            "silence":      [...],
            "scenes":       [...],
            "audio_peaks":  [...],
            "motion":       [...],
            "signals_used": ["silence", "scenes", "audio_peaks", "motion"],
            "warnings":     [...],
            "fallback_used": bool,
            "processing_time_ms": int,
        }

    If probing fails entirely:
        {
            "error":         "probe_failed",
            "fallback_used": True,
            "signals_used":  [],
            "warnings":      [],
            "processing_time_ms": int,
        }
    """
    t0 = time.monotonic()
    warnings: list[str] = []
    signals_used: list[str] = []
    fallback_used = False

    # ── Probe ──────────────────────────────────────────────────────────────
    probe = probe_media(path)
    if probe.get("error") == "probe_failed":
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.warning("analysis_probe_failed", extra={"path": path})
        return {
            "error": "probe_failed",
            "fallback_used": True,
            "signals_used": [],
            "warnings": ["probe_failed: could not read media metadata"],
            "processing_time_ms": elapsed_ms,
        }

    duration_s: float = probe.get("duration_s", 0.0) or 0.0

    if duration_s > MAX_ANALYSIS_DURATION:
        warnings.append(
            f"media_too_long: duration {duration_s:.0f}s exceeds limit "
            f"{MAX_ANALYSIS_DURATION}s — deep signals skipped"
        )
        fallback_used = True
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return {
            "probe": probe,
            "silence": [],
            "scenes": [],
            "audio_peaks": [],
            "motion": [],
            "signals_used": [],
            "warnings": warnings,
            "fallback_used": True,
            "processing_time_ms": elapsed_ms,
        }

    has_audio: bool = probe.get("has_audio", False)
    has_video: bool = probe.get("has_video", False)

    # ── Silence ────────────────────────────────────────────────────────────
    silence: list[dict[str, float]] = []
    if has_audio:
        try:
            silence = detect_silence(path, duration_s)
            signals_used.append("silence")
        except Exception as exc:
            warnings.append(f"silence_detector_error: {exc}")
            fallback_used = True
    else:
        warnings.append("no_audio_track: silence detection skipped")

    # ── Scenes ─────────────────────────────────────────────────────────────
    scenes: list[float] = []
    if has_video:
        try:
            scenes = detect_scenes(path, duration_s)
            signals_used.append("scenes")
        except Exception as exc:
            warnings.append(f"scene_detector_error: {exc}")
            fallback_used = True
    else:
        warnings.append("no_video_track: scene detection skipped")

    # ── Audio peaks ────────────────────────────────────────────────────────
    audio_peaks: list[dict[str, Any]] = []
    if has_audio:
        try:
            audio_peaks = detect_audio_peaks(path, duration_s)
            if audio_peaks:
                signals_used.append("audio_peaks")
        except Exception as exc:
            warnings.append(f"audio_peaks_error: {exc}")
            fallback_used = True

    # ── Motion ─────────────────────────────────────────────────────────────
    motion: list[dict[str, Any]] = []
    if has_video:
        try:
            motion = compute_motion_score(path, duration_s)
            # Only count as a real signal if we got non-uniform results
            unique_scores = {seg["motion_score"] for seg in motion}
            if len(unique_scores) > 1:
                signals_used.append("motion")
            else:
                warnings.append("motion_score_uniform: fallback values used")
                fallback_used = True
        except Exception as exc:
            warnings.append(f"motion_score_error: {exc}")
            fallback_used = True
    else:
        warnings.append("no_video_track: motion scoring skipped")

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "analysis_complete",
        extra={
            "path": path,
            "duration_s": duration_s,
            "signals": signals_used,
            "warnings_count": len(warnings),
            "processing_time_ms": elapsed_ms,
        },
    )

    return {
        "probe": probe,
        "silence": silence,
        "scenes": scenes,
        "audio_peaks": audio_peaks,
        "motion": motion,
        "signals_used": signals_used,
        "warnings": warnings,
        "fallback_used": fallback_used,
        "processing_time_ms": elapsed_ms,
    }


# ─────────────────────────────────────────────
# 7. compute_media_fingerprint
# ─────────────────────────────────────────────

def compute_media_fingerprint(path: str) -> str:
    """
    Compute a fast SHA-256 fingerprint of a media file.

    Reads the first 64 KB + last 64 KB of the file plus the file size.
    This avoids hashing the entire file while still producing a unique-enough
    fingerprint for cache lookups.

    Returns the hex digest string, or an empty string on error.
    """
    try:
        file_size = os.path.getsize(path)
    except OSError as exc:
        logger.warning("fingerprint_stat_error", extra={"path": path, "error": str(exc)})
        return ""

    h = hashlib.sha256()
    h.update(str(file_size).encode())

    try:
        with open(path, "rb") as fh:
            # Head chunk
            head = fh.read(_FINGERPRINT_CHUNK)
            h.update(head)

            # Tail chunk (only if file is large enough to have a distinct tail)
            if file_size > _FINGERPRINT_CHUNK * 2:
                fh.seek(-_FINGERPRINT_CHUNK, 2)
                tail = fh.read(_FINGERPRINT_CHUNK)
                h.update(tail)
    except OSError as exc:
        logger.warning("fingerprint_read_error", extra={"path": path, "error": str(exc)})
        return ""

    return h.hexdigest()
