"""
Transcript Translation Cache
==============================
Dedups identical (source cue text, target_lang) pairs across ALL jobs and
users so the LLM is never asked to translate the same content twice — the
single biggest lever against LLM cost at scale (repeated filler lines like
"[Music]"/"..." within one file, or the same popular video's subtitles
uploaded by many different users).

Not a correctness feature — a cache miss/lookup failure always falls back to
translating via the LLM, never silently skips or corrupts output.
"""
from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

_SEP = "\x1f"  # unit separator — text itself may contain any character incl. ':'


def compute_hash(text: str, target_lang: str) -> str:
    normalized = text.strip()
    payload = f"{normalized}{_SEP}{target_lang}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def lookup_many(db, hashes: list[str]) -> dict[str, str]:
    """
    Batch cache lookup. Returns {content_hash: translated_text} for hits only.
    Never raises — a lookup failure just means everything falls through to
    the LLM (cache is a pure optimization, not a dependency).
    """
    if not hashes:
        return {}
    try:
        resp = (
            db.table("transcript_translation_cache")
            .select("content_hash, translated_text")
            .in_("content_hash", hashes)
            .execute()
        )
        rows = resp.data or []
        hits = {r["content_hash"]: r["translated_text"] for r in rows}
        for h in hits:
            try:
                db.rpc("bump_transcript_cache_hit", {"p_hash": h}).execute()
            except Exception:
                pass  # best-effort telemetry, never blocks translation
        return hits
    except Exception as exc:
        logger.warning("transcript_translation_cache lookup failed: %s", exc)
        return {}


def store_many(db, entries: list[tuple[str, str, str]]) -> None:
    """
    Batch upsert freshly-translated (content_hash, target_lang, translated_text)
    triples into the cache. Best-effort — a store failure must never fail the
    translation job, it just means the next request re-translates that text.
    """
    if not entries:
        return
    rows = [
        {"content_hash": h, "target_lang": lang, "translated_text": text}
        for h, lang, text in entries
    ]
    try:
        db.table("transcript_translation_cache").upsert(
            rows, on_conflict="content_hash"
        ).execute()
    except Exception as exc:
        logger.warning("transcript_translation_cache store failed: %s", exc)
