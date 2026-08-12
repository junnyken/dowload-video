"""
Smart Summary & Chapter Suggestion
====================================
Generates chapter markers and summaries from available signals.

Tier 1 (always): heuristic from scene changes + audio peaks + description
Tier 3 (optional): LLM summarization when GEMINI_API_KEY or OPENAI_API_KEY is set

Without transcript: only generates chapter markers from heuristic signals.
With description/subtitle: adds summary and suggested clip titles.
"""
from __future__ import annotations

import re
from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class ChapterSuggestion(TypedDict):
    index: int
    start: float
    title: str
    confidence: float
    source: str  # "heuristic"|"description_parse"|"llm"


# ---------------------------------------------------------------------------
# Description-based chapter parsing
# ---------------------------------------------------------------------------

_TS_PATTERN = re.compile(
    r"^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$",
    re.MULTILINE,
)


def _ts_to_seconds(ts: str) -> float:
    """Convert HH:MM:SS or MM:SS timestamp string to seconds."""
    parts = ts.split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0.0


def _parse_description_chapters(
    description: str,
    duration_s: float,
) -> list[ChapterSuggestion]:
    """
    Parse YouTube-style chapter timestamps from a video description.

    Format: lines like "0:00 Intro" or "1:23:45 Title Here"
    Returns ChapterSuggestion list sorted by start time.
    """
    chapters: list[ChapterSuggestion] = []
    for i, m in enumerate(_TS_PATTERN.finditer(description)):
        ts_str, chapter_title = m.group(1), m.group(2).strip()
        start = _ts_to_seconds(ts_str)
        if start >= duration_s > 0:
            continue
        chapters.append(
            ChapterSuggestion(
                index=i,
                start=start,
                title=chapter_title,
                confidence=0.95,
                source="description_parse",
            )
        )

    # Sort and re-index
    chapters.sort(key=lambda c: c["start"])
    for idx, ch in enumerate(chapters):
        ch["index"] = idx

    return chapters


# ---------------------------------------------------------------------------
# Heuristic chapter generation
# ---------------------------------------------------------------------------

_CLUSTER_GAP_S = 30.0  # scenes within 30 s → same chapter


def _heuristic_chapters(
    analysis: dict,
    max_chapters: int = 8,
) -> list[ChapterSuggestion]:
    """
    Generate heuristic chapters from scene change data in the analysis dict.

    Clusters nearby scene changes and assigns generic Vietnamese titles.
    Always includes 00:00 as "Giới thiệu".
    """
    scene_changes: list[float] = []

    # Accept different key conventions from the media analyzer
    for key in ("scene_changes", "scenes", "scene_timestamps"):
        raw = analysis.get(key)
        if raw:
            for s in raw:
                t = s if isinstance(s, (int, float)) else s.get("time", s.get("start", None))
                if t is not None:
                    scene_changes.append(float(t))
            break

    # Sort and cluster
    scene_changes = sorted(set(scene_changes))

    clusters: list[float] = []
    prev: float | None = None
    for t in scene_changes:
        if prev is None or t - prev >= _CLUSTER_GAP_S:
            clusters.append(t)
        prev = t

    # Always start with 0:00
    if not clusters or clusters[0] > 0:
        clusters.insert(0, 0.0)

    # Cap at max_chapters
    clusters = clusters[:max_chapters]

    vi_titles = [
        "Giới thiệu",
        "Phần 1", "Phần 2", "Phần 3", "Phần 4",
        "Phần 5", "Phần 6", "Phần 7", "Phần 8",
    ]

    chapters: list[ChapterSuggestion] = []
    for idx, start in enumerate(clusters):
        title = vi_titles[idx] if idx < len(vi_titles) else f"Phần {idx}"
        chapters.append(
            ChapterSuggestion(
                index=idx,
                start=start,
                title=title,
                confidence=0.35,
                source="heuristic",
            )
        )

    return chapters


# ---------------------------------------------------------------------------
# Optional LLM summarization
# ---------------------------------------------------------------------------

def _optional_llm_summary(text: str, max_words: int = 150) -> dict:
    """
    Try Gemini, then OpenAI for a Vietnamese summary.

    Returns {"summary": str, "llm_used": bool}
    """
    from app.services.llm_client import call_llm

    prompt = (
        f"Tóm tắt nội dung video sau trong {max_words} từ tiếng Việt. "
        f"Chỉ dùng thông tin có trong text, không bịa thêm:\n\n{text[:3000]}"
    )

    summary = call_llm(prompt, max_output_tokens=512)
    if summary:
        return {"summary": summary, "llm_used": True}

    return {"summary": "", "llm_used": False}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def suggest_summary(analysis: dict, metadata: dict | None = None) -> dict:
    """
    Generate chapter suggestions and an optional summary for a video.

    Args:
        analysis: output from media_analyzer.build_analysis()
        metadata: optional yt-dlp-style metadata dict with keys:
                  title, description, uploader, duration_s, chapters

    Returns:
        {
            "summary_short": str,
            "key_moments": list[str],
            "chapter_suggestions": list[ChapterSuggestion],
            "clip_titles": list[str],
            "llm_used": bool,
            "fallback_used": bool,
            "confidence": float,
            "warnings": list[str],
        }
    """
    if metadata is None:
        metadata = {}

    warnings: list[str] = []
    summary_short = ""
    llm_used = False
    fallback_used = False
    chapter_suggestions: list[ChapterSuggestion] = []
    confidence = 0.0

    description: str = metadata.get("description", "") or ""
    duration_s: float = float(metadata.get("duration_s", 0) or metadata.get("duration", 0) or 0)
    pre_parsed_chapters: list[Any] = metadata.get("chapters", []) or []

    # --- Choose chapter source ---
    if pre_parsed_chapters:
        # YouTube pre-parsed chapters
        for idx, ch in enumerate(pre_parsed_chapters):
            start = float(ch.get("start_time", ch.get("start", 0)) or 0)
            title = str(ch.get("title", f"Chapter {idx + 1}")).strip()
            chapter_suggestions.append(
                ChapterSuggestion(
                    index=idx,
                    start=start,
                    title=title,
                    confidence=0.9,
                    source="description_parse",
                )
            )
        confidence = 0.9

    elif description and _TS_PATTERN.search(description):
        chapter_suggestions = _parse_description_chapters(description, duration_s)
        if chapter_suggestions:
            confidence = 0.9
        else:
            warnings.append("description_timestamp_parse_returned_empty")

    if not chapter_suggestions:
        # Fall back to heuristic
        chapter_suggestions = _heuristic_chapters(analysis)
        fallback_used = True
        confidence = 0.35

    # --- Summary ---
    if len(description) > 200:
        llm_result = _optional_llm_summary(description)
        summary_short = llm_result["summary"]
        llm_used = llm_result["llm_used"]
        if not summary_short:
            warnings.append("llm_summary_failed_or_unavailable")
    else:
        if description:
            summary_short = description[:300].strip()
        warnings.append("description_too_short_for_llm_summary")

    # --- key_moments (human-readable) ---
    key_moments: list[str] = []
    for ch in chapter_suggestions:
        total_s = int(ch["start"])
        h, rem = divmod(total_s, 3600)
        m, s = divmod(rem, 60)
        if h:
            ts_str = f"{h}:{m:02d}:{s:02d}"
        else:
            ts_str = f"{m}:{s:02d}"
        key_moments.append(f"{ts_str} - {ch['title']}")

    # --- clip_titles ---
    clip_titles: list[str] = [ch["title"] for ch in chapter_suggestions]

    return {
        "summary_short": summary_short,
        "key_moments": key_moments,
        "chapter_suggestions": chapter_suggestions,
        "clip_titles": clip_titles,
        "llm_used": llm_used,
        "fallback_used": fallback_used,
        "confidence": confidence,
        "warnings": warnings,
    }
