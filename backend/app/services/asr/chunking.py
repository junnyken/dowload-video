"""
Audio extraction, chunk planning/cutting and merging (D4).

Chunks are cut with explicit -ss/-t and re-encoded, so each chunk's offset in
the original timeline is exactly the planned offset — merge just adds it.
"""
from __future__ import annotations

import math
import os
import subprocess
from collections import Counter

from app.services.asr.types import AsrError, Segment, TranscriptResult

AUDIO_BITRATE_KBPS = 48
# A trailing chunk shorter than this is folded into the previous one.
_MIN_TAIL_SEC = 30.0


def probe_duration_sec(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, timeout=30, text=True,
    )
    if result.returncode != 0:
        raise AsrError(f"ffprobe failed: {result.stderr[-300:]}", code="audio_extract_failed")
    return float(result.stdout.strip())


def extract_audio(video_path: str, audio_path: str, *, max_sec: float | None = None, timeout: int = 600) -> None:
    cmd = ["ffmpeg", "-y", "-i", video_path]
    if max_sec:
        cmd += ["-t", f"{max_sec:.3f}"]
    cmd += ["-vn", "-ac", "1", "-acodec", "libmp3lame", "-b:a", f"{AUDIO_BITRATE_KBPS}k", audio_path]
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0 or not os.path.isfile(audio_path):
        raise AsrError(
            f"FFmpeg audio extraction failed: {result.stderr.decode(errors='replace')[-300:]}",
            code="audio_extract_failed",
        )


def plan_chunks(duration_sec: float, chunk_sec: float) -> list[tuple[float, float]]:
    """[(offset, length)] covering [0, duration_sec]."""
    if duration_sec <= 0:
        return []
    if duration_sec <= chunk_sec:
        return [(0.0, float(duration_sec))]
    n = math.ceil(duration_sec / chunk_sec)
    plan = [(i * chunk_sec, min(chunk_sec, duration_sec - i * chunk_sec)) for i in range(n)]
    if len(plan) > 1 and plan[-1][1] < _MIN_TAIL_SEC:
        tail = plan.pop()
        off, length = plan[-1]
        plan[-1] = (off, length + tail[1])
    return [(float(o), float(l)) for o, l in plan]


def cut_chunk(audio_path: str, offset: float, length: float, out_path: str, timeout: int = 300) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", f"{offset:.3f}", "-t", f"{length:.3f}", "-i", audio_path,
         "-ac", "1", "-acodec", "libmp3lame", "-b:a", f"{AUDIO_BITRATE_KBPS}k", out_path],
        capture_output=True, timeout=timeout,
    )
    if result.returncode != 0 or not os.path.isfile(out_path):
        raise AsrError(
            f"FFmpeg chunk cut failed: {result.stderr.decode(errors='replace')[-300:]}",
            code="audio_extract_failed",
        )


def merge_chunk_results(parts: list[tuple[float, TranscriptResult]]) -> TranscriptResult:
    """Shift each chunk's segments by its offset and concatenate. A segment
    that starts before the previous one ended (a word straddling the cut) is
    trimmed to start at the previous end; if nothing is left it is dropped."""
    merged: list[Segment] = []
    langs: Counter = Counter()
    total = 0.0
    for offset, res in sorted(parts, key=lambda p: p[0]):
        total = max(total, offset + (res.duration_sec or 0.0))
        if res.language and res.language != "unknown":
            langs[res.language] += len(res.segments) or 1
        for seg in res.segments:
            start, end = round(seg.start + offset, 3), round(seg.end + offset, 3)
            if merged and start < merged[-1].end:
                start = merged[-1].end
            if end <= start:
                continue
            merged.append(Segment(start=start, end=end, text=seg.text, speaker=seg.speaker))
    language = langs.most_common(1)[0][0] if langs else "unknown"
    return TranscriptResult(segments=merged, language=language, duration_sec=total)
