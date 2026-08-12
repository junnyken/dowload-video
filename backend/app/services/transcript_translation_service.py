"""
Transcript Translation Service
================================
Detects the source language of a subtitle file and translates its cues to a
target language via the shared LLM helper (app.services.llm_client.call_llm),
preserving meaning/context (not literal word-by-word) while leaving cue
timing untouched.

All LLM usage here is bounded:
  - detect_source_language: a single call over only the first ~15 cues.
  - translate_cues: chunked (default 35 cues/chunk), never the whole
    transcript in one call — guards against unbounded token cost / timeout.
"""
from __future__ import annotations

import re

from app.services.llm_client import call_llm
from app.services.subtitle_format import Cue

_DETECT_CUE_SAMPLE = 15
_LINE_RE = re.compile(r"^\s*(\d+)\s*:\s*(.*)$")


def detect_source_language(cues: list[Cue]) -> str:
    """
    Detect the source language of a set of cues using a single bounded
    call_llm() over the first ~15 cues' text concatenated.

    Returns a language name (e.g. "Vietnamese"). Falls back to "Unknown"
    if call_llm returns None — callers must treat "Unknown" as a soft-fail,
    not crash.
    """
    sample_text = "\n".join(c.text for c in cues[:_DETECT_CUE_SAMPLE] if c.text.strip())
    if not sample_text.strip():
        return "Unknown"

    prompt = (
        "Identify the language of the following subtitle text. "
        "Reply with ONLY the language name in English (e.g. 'Vietnamese', "
        "'English', 'Japanese'), nothing else.\n\n"
        f"{sample_text[:2000]}"
    )

    result = call_llm(prompt, max_output_tokens=32)
    print(f"[DIAG-lang] call_llm raw result: {result!r}")
    if not result:
        return "Unknown"

    # Keep just the first line/word-ish answer, strip punctuation/quotes.
    cleaned = result.strip().splitlines()[0].strip().strip(".\"'")
    return cleaned if cleaned else "Unknown"


class TranslationAlignmentError(Exception):
    """Raised when a chunk's translated line count doesn't match its cue count."""


def _flatten(text: str) -> str:
    """Collapse a cue's internal line breaks to spaces before it goes into a
    prompt — keeps every prompt/response line strictly 1 cue = 1 line, so a
    multi-line cue's continuation can never be misread as a new "N: " entry
    (see _parse_chunk_response)."""
    return " ".join(text.split())


def _build_chunk_prompt(chunk: list[Cue], source_lang: str, target_lang: str, strict: bool = False) -> str:
    numbered_lines = "\n".join(f"{i + 1}: {_flatten(cue.text)}" for i, cue in enumerate(chunk))

    instructions = (
        f"Translate the following {len(chunk)} numbered subtitle lines from "
        f"{source_lang} to {target_lang}.\n"
        "Rules:\n"
        "- Preserve the meaning, context, and tone of the source — this is NOT "
        "a literal word-by-word translation. Make it read naturally in the "
        "target language.\n"
        "- Keep the SAME NUMBER of lines as the input — one translation per "
        "input line, in the same order.\n"
        "- Do NOT merge, split, skip, or reorder lines.\n"
        "- Each translation MUST be a SINGLE line with no embedded line "
        "breaks, even if the source line looks like it could be split.\n"
        "- Reply in EXACTLY this format, one line per translation, nothing else:\n"
        "N: <translated text>\n"
    )
    if strict:
        instructions += (
            "\nSTRICT MODE: your previous reply did not match the required "
            f"line count. You MUST output exactly {len(chunk)} lines, each "
            "starting with its number and a colon, and NOTHING else "
            "(no preamble, no explanation, no extra blank lines).\n"
        )

    return f"{instructions}\nInput:\n{numbered_lines}"


def _parse_chunk_response(response: str, expected_count: int) -> list[str] | None:
    """
    Parse "N: <text>" lines from an LLM response into an ordered list of
    translations indexed 1..expected_count. Returns None if parsing fails,
    the count doesn't match, or the same index appears twice.

    Deliberately does NOT treat non-matching lines as a "continuation" of the
    previous entry — every prompt line is flattened to single-line text (see
    _build_chunk_prompt/_flatten) specifically so every response line is
    unambiguous. A prior version merged trailing non-numbered lines into the
    previous cue's translation, which meant a cue's translated text that
    itself happened to start with "<number>: " (e.g. dialogue reading
    "12: Attack now") could be misread as a new numbered entry — silently
    dropping the real preceding cue's text while `len(parsed) == expected_count`
    still held, so no error was ever raised. Any duplicate index now fails
    the chunk outright (triggering the strict retry) instead of silently
    keeping one occurrence and discarding the other.
    """
    parsed: dict[int, str] = {}

    for raw_line in response.splitlines():
        match = _LINE_RE.match(raw_line)
        if not match:
            continue  # stray blank/preamble line — ignored, not merged
        idx = int(match.group(1))
        text = match.group(2).strip()
        if idx in parsed:
            return None  # ambiguous — same index twice, don't guess which is right
        parsed[idx] = text

    if len(parsed) != expected_count:
        return None
    if set(parsed.keys()) != set(range(1, expected_count + 1)):
        return None

    return [parsed[i] for i in range(1, expected_count + 1)]


def translate_cues(
    cues: list[Cue],
    source_lang: str,
    target_lang: str,
    chunk_size: int = 35,
) -> list[str]:
    """
    Translate cue text in bounded chunks, preserving order and meaning
    (not literal word-by-word). Returns translated text strings in the
    same order as the input `cues`.

    Raises TranslationAlignmentError if a chunk's response can't be aligned
    1:1 with its input cues even after one strict retry — this must never
    silently produce a partial/misaligned result.
    """
    translations: list[str] = []

    for chunk_start in range(0, len(cues), chunk_size):
        chunk = cues[chunk_start:chunk_start + chunk_size]

        parsed: list[str] | None = None

        for attempt in range(2):  # 1 normal attempt + 1 strict retry
            strict = attempt == 1
            prompt = _build_chunk_prompt(chunk, source_lang, target_lang, strict=strict)
            response = call_llm(prompt, max_output_tokens=4096)
            if response:
                parsed = _parse_chunk_response(response, len(chunk))
            if parsed is not None:
                break

        if parsed is None:
            first_cue_index = chunk[0].index
            last_cue_index = chunk[-1].index
            raise TranslationAlignmentError(
                f"Translation alignment failed for cue index range "
                f"{first_cue_index}-{last_cue_index} "
                f"(expected {len(chunk)} lines back from the LLM)."
            )

        translations.extend(parsed)

    return translations
