"""
SRT / WebVTT subtitle parser + serializer
===========================================
Minimal, dependency-free parser for standard SRT and WebVTT cue files.

Only the `text` field of a Cue is expected to change after translation —
`index`/`start`/`end` are always preserved byte-for-byte from the source.

SRT format:
    1
    00:00:01,000 --> 00:00:04,000
    Hello world

    2
    00:00:05,000 --> 00:00:07,500
    Line one
    Line two

WebVTT format:
    WEBVTT

    1
    00:00:01.000 --> 00:00:04.000
    Hello world

    00:00:05.000 --> 00:00:07.500
    Cue without an explicit identifier
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Cue:
    index: int
    start: str  # raw timestamp string, preserved exactly as-is
    end: str
    text: str   # may be multi-line, joined with \n


_SRT_TIME_ARROW_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
    r"(.*)$"
)

_VTT_TIME_ARROW_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2}:\d{2}\.\d{1,3}|\d{2}:\d{2}\.\d{1,3})\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}\.\d{1,3}|\d{2}:\d{2}\.\d{1,3})"
    r"(.*)$"
)


def timestamp_to_seconds(ts: str) -> float:
    """
    Parse an SRT (`HH:MM:SS,mmm`) or WebVTT (`HH:MM:SS.mmm` / `MM:SS.mmm`)
    timestamp into seconds. Raises ValueError on a malformed string — callers
    doing QA/reading-speed math on a Cue's raw start/end should treat that as
    "can't compute, skip this cue" rather than guessing.
    """
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours, (minutes, seconds) = "0", parts
    else:
        raise ValueError(f"Unrecognized timestamp: {ts!r}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def seconds_to_srt_timestamp(seconds: float) -> str:
    """Inverse of timestamp_to_seconds, SRT flavor (`HH:MM:SS,mmm`) — used
    to turn ASR's float-second segment boundaries into Cue.start/end."""
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _normalize_newlines(content: str) -> str:
    # Strip a leading UTF-8 BOM (U+FEFF) — common in .srt files exported by
    # Windows editors/tools. Left in place, it prefixes the first line (e.g.
    # "﻿1"), which fails the digit/timestamp checks below and silently
    # drops the entire first cue.
    if content.startswith("﻿"):
        content = content[1:]
    return content.replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------------------
# SRT
# ---------------------------------------------------------------------------

def _parse_srt_impl(content: str) -> tuple[list[Cue], int]:
    """Real SRT parsing logic. Returns (cues, skipped_block_count) — the
    latter counts blocks that looked like they should be a cue (non-blank,
    survived the WEBVTT-header/NOTE-style carve-outs that don't apply to SRT)
    but failed to match a valid timestamp line, so they were silently
    dropped. See parse_srt/parse_srt_with_skip_count below."""
    content = _normalize_newlines(content).strip("\n")
    if not content.strip():
        return [], 0

    blocks = re.split(r"\n\s*\n", content)
    cues: list[Cue] = []
    skipped = 0
    fallback_index = 0

    for block in blocks:
        lines = [ln for ln in block.split("\n")]
        # Strip trailing empty lines
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue

        idx = 0
        line_ptr = 0

        # First line may be a numeric index
        first = lines[0].strip()
        match = _SRT_TIME_ARROW_RE.match(first)
        if match is None and first.isdigit():
            idx = int(first)
            line_ptr = 1
        else:
            fallback_index += 1
            idx = fallback_index

        if line_ptr >= len(lines):
            skipped += 1
            continue

        time_line = lines[line_ptr].strip()
        match = _SRT_TIME_ARROW_RE.match(time_line)
        if match is None:
            # Not a valid cue block — skip
            skipped += 1
            continue
        start, end = match.group(1), match.group(2)
        line_ptr += 1

        text_lines = lines[line_ptr:]
        text = "\n".join(text_lines)

        cues.append(Cue(index=idx, start=start, end=end, text=text))
        fallback_index = idx

    return cues, skipped


def parse_srt(content: str) -> list[Cue]:
    """
    Parse standard SRT content into a list of Cue.

    Standard structure per block, separated by a blank line:
        <index>
        <start> --> <end>
        <text line 1>
        <text line 2...>

    Malformed blocks are silently dropped — use parse_srt_with_skip_count if
    the caller needs to know whether that happened.
    """
    cues, _skipped = _parse_srt_impl(content)
    return cues


def parse_srt_with_skip_count(content: str) -> tuple[list[Cue], int]:
    """Same as parse_srt, but also returns how many blocks looked like a cue
    and failed to parse (dropped) — surface this to the user rather than
    letting cue_count silently under-represent the source file."""
    return _parse_srt_impl(content)


def serialize_srt(cues: list[Cue]) -> str:
    """Serialize a list of Cue back into standard SRT text."""
    parts: list[str] = []
    for cue in cues:
        parts.append(f"{cue.index}\n{cue.start} --> {cue.end}\n{cue.text}")
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# WebVTT
# ---------------------------------------------------------------------------

def _parse_vtt_impl(content: str) -> tuple[list[Cue], int]:
    """Real WebVTT parsing logic. Returns (cues, skipped_block_count) —
    NOTE/STYLE/REGION blocks are intentional non-cue content and NOT counted
    as skipped; only blocks that looked like they should be a cue but failed
    to match a valid timestamp line count. See parse_vtt/
    parse_vtt_with_skip_count below."""
    content = _normalize_newlines(content).strip("\n")
    if not content.strip():
        return [], 0

    blocks = re.split(r"\n\s*\n", content)
    cues: list[Cue] = []
    skipped = 0
    fallback_index = 0

    for block_i, block in enumerate(blocks):
        lines = [ln for ln in block.split("\n")]
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue

        # First block: strip the WEBVTT header line (and any metadata after it)
        if block_i == 0 and lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
            while lines and not lines[0].strip():
                lines = lines[1:]
            if not lines:
                continue

        # Skip NOTE / STYLE / REGION blocks entirely — intentional, not a parse failure.
        first_stripped = lines[0].strip()
        if first_stripped.upper().startswith(("NOTE", "STYLE", "REGION")):
            continue

        line_ptr = 0
        identifier = ""

        match = _VTT_TIME_ARROW_RE.match(lines[0])
        if match is None:
            # First line is a cue identifier
            identifier = lines[0].strip()
            line_ptr = 1
            if line_ptr >= len(lines):
                skipped += 1
                continue
            match = _VTT_TIME_ARROW_RE.match(lines[line_ptr])
            if match is None:
                # Not a valid cue block — skip
                skipped += 1
                continue

        start, end = match.group(1), match.group(2)
        line_ptr += 1

        text_lines = lines[line_ptr:]
        text = "\n".join(text_lines)

        fallback_index += 1
        idx = int(identifier) if identifier.isdigit() else fallback_index

        cues.append(Cue(index=idx, start=start, end=end, text=text))

    return cues, skipped


def parse_vtt(content: str) -> list[Cue]:
    """
    Parse standard WebVTT content into a list of Cue.

    Handles the WEBVTT header, optional NOTE/STYLE blocks (skipped), optional
    cue identifiers, and cue-settings trailing a timestamp line (ignored).

    Malformed blocks are silently dropped — use parse_vtt_with_skip_count if
    the caller needs to know whether that happened.
    """
    cues, _skipped = _parse_vtt_impl(content)
    return cues


def parse_vtt_with_skip_count(content: str) -> tuple[list[Cue], int]:
    """Same as parse_vtt, but also returns how many blocks looked like a cue
    and failed to parse (dropped) — surface this to the user rather than
    letting cue_count silently under-represent the source file."""
    return _parse_vtt_impl(content)


def serialize_vtt(cues: list[Cue]) -> str:
    """Serialize a list of Cue back into standard WebVTT text."""
    parts: list[str] = ["WEBVTT"]
    for cue in cues:
        parts.append(f"{cue.index}\n{cue.start} --> {cue.end}\n{cue.text}")
    return "\n\n".join(parts) + "\n"
