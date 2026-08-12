"""
Container Dedupe Layer — Phase 25
===================================
Multi-layer deduplication for container source items.

Layers (in order, first match wins):
  1. Canonical platform ID   (most reliable, zero false positives)
  2. Normalized URL          (same resource, different params)
  3. Title + author + duration bucket  (fuzzy, cross-platform)

Cross-container deduplication:
  - Spotify: track appearing in both album and compilation
  - TikTok: pinned post also appearing in recent feed
  - SoundCloud: repost visible in artist profile and original profile

Usage:
    deduper = DedupeLayer()
    result = deduper.filter(items)   # -> DedupeResult
    print(result.summary_text)       # user-facing explanation
    queue(result.kept)               # only unique items
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

# Avoid circular import: ContainerItem is a plain dataclass, safe to import
from app.services.container_discovery import ContainerItem


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class DedupeResult:
    kept: list[ContainerItem]
    removed: list[dict]        # {"item": ContainerItem, "reason": str}
    removed_count: int

    @property
    def summary_text(self) -> str:
        if not self.removed_count:
            return ""
        buckets: dict[str, int] = {}
        for r in self.removed:
            key = r["reason"].split(":")[0]
            buckets[key] = buckets.get(key, 0) + 1
        parts = []
        if buckets.get("same_id"):
            parts.append(f"{buckets['same_id']} bài cùng ID nguồn")
        if buckets.get("same_url"):
            parts.append(f"{buckets['same_url']} bài cùng URL")
        if buckets.get("similar_title"):
            parts.append(f"{buckets['similar_title']} bài tiêu đề/thời lượng gần giống")
        return f"Đã loại {self.removed_count} trùng: " + ", ".join(parts) + "."

    @property
    def reasons_list(self) -> list[str]:
        return [r["reason"] for r in self.removed]


# ── Platform ID extraction patterns (precompiled for speed) ──────────────────

# Compiled once at module load — avoids Python re._cache lookup overhead on
# every item in large batches.
_ID_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"open\.spotify\.com/track/([A-Za-z0-9]+)", re.IGNORECASE), "sp"),
    (re.compile(r"spotify:track:([A-Za-z0-9]+)", re.IGNORECASE), "sp"),
    (re.compile(r"soundcloud\.com/[^/]+/([^/?#]+)(?:\?|#|$)", re.IGNORECASE), "sc"),
    (re.compile(r"(?:tiktok\.com|douyin\.com).+?/video/(\d+)", re.IGNORECASE), "tt"),
    (re.compile(r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})", re.IGNORECASE), "yt"),
    (re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})", re.IGNORECASE), "yt"),
    (re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE), "ig"),
    (re.compile(r"threads\.net/@[^/]+/post/([A-Za-z0-9_-]+)", re.IGNORECASE), "th"),
    (re.compile(r"pinterest\.[^/]+/pin/(\d+)", re.IGNORECASE), "pin"),
    (re.compile(r"reddit\.com/r/[^/]+/comments/([A-Za-z0-9]+)", re.IGNORECASE), "rd"),
    (re.compile(r"bilibili\.com/video/(BV[A-Za-z0-9]+)", re.IGNORECASE), "bl"),
    (re.compile(r"twitter\.com/[^/]+/status/(\d+)", re.IGNORECASE), "tw"),
    (re.compile(r"x\.com/[^/]+/status/(\d+)", re.IGNORECASE), "tw"),
    (re.compile(r"facebook\.com/(?:watch/?\?v=|video/|reel/)(\d+)", re.IGNORECASE), "fb"),
]

# Keep the original string list for backward-compat imports (tests etc.)
_ID_PATTERNS: list[tuple[str, str]] = [(p.pattern, pfx) for p, pfx in _ID_COMPILED]

# Precompiled non-word-non-space remover for _title_key
_NONWORD_RE: re.Pattern = re.compile(r"[^\w\s]")


# ── DedupeLayer ───────────────────────────────────────────────────────────────

class DedupeLayer:
    """
    Stateful deduplicator.  Create one instance per container discovery session
    and pass items across sections/branches so cross-branch dedupe works.

    thread-unsafe — single-threaded Celery task use only.
    """

    def __init__(self):
        self._seen_ids: set[str] = set()
        self._seen_urls: set[str] = set()
        self._seen_title_keys: dict[str, str] = {}   # key → first item title

    # ── private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _canonical_id(item: ContainerItem) -> Optional[str]:
        """Extract a stable platform-scoped ID from the URL."""
        # Also check item.id directly (may already be pre-computed)
        if item.id and ":" in item.id and len(item.id) > 4:
            return item.id  # e.g. "sp:4RvWPy…"

        url = item.url or ""
        for pattern, prefix in _ID_COMPILED:
            m = pattern.search(url)
            if m:
                return f"{prefix}:{m.group(1)}"
        return None

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Strip tracking params, trailing slash, lowercase."""
        try:
            p = urlparse(url)
            # Keep path only (no query/fragment = canonical form for most platforms)
            # Exception: YouTube needs the `v` param
            if "youtube.com" in (p.netloc or ""):
                from urllib.parse import parse_qs, urlencode
                qs = parse_qs(p.query)
                v = qs.get("v", [""])[0]
                clean_q = f"v={v}" if v else ""
                return (p.scheme + "://" + p.netloc + p.path + ("?" + clean_q if clean_q else "")).rstrip("/").lower()
            return (p.scheme + "://" + p.netloc + p.path).rstrip("/").lower()
        except Exception:
            return url.lower().rstrip("/")

    @staticmethod
    def _title_key(item: ContainerItem) -> str:
        """Fuzzy key: lowercase-alphanum title prefix + author + 5-second duration bucket."""
        title = _NONWORD_RE.sub("", (item.title or "").lower()).strip()
        author = _NONWORD_RE.sub("", (item.author or "").lower()).strip()
        dur_bucket = item.duration_ms // 5000  # 5-second buckets
        return f"{author[:20]}:{title[:40]}:{dur_bucket}"

    # ── public interface ──────────────────────────────────────────────────────

    def filter(self, items: list[ContainerItem]) -> DedupeResult:
        """
        Return DedupeResult(kept, removed, removed_count).
        Mutates internal state so subsequent calls continue cross-branch dedupe.
        """
        kept: list[ContainerItem] = []
        removed: list[dict] = []

        for item in items:
            cid = self._canonical_id(item)
            nurl = self._normalize_url(item.url)
            tkey = self._title_key(item)

            reason: Optional[str] = None

            if cid and cid in self._seen_ids:
                reason = f"same_id:{cid}"
            elif nurl in self._seen_urls:
                reason = "same_url"
            elif tkey in self._seen_title_keys:
                reason = f"similar_title:{self._seen_title_keys[tkey][:30]}"

            if reason:
                removed.append({"item": item, "reason": reason})
            else:
                kept.append(item)
                if cid:
                    self._seen_ids.add(cid)
                self._seen_urls.add(nurl)
                self._seen_title_keys[tkey] = item.title

        return DedupeResult(kept=kept, removed=removed, removed_count=len(removed))

    def has_seen(self, item: ContainerItem) -> bool:
        """Quick check without modifying state."""
        cid = self._canonical_id(item)
        nurl = self._normalize_url(item.url)
        tkey = self._title_key(item)
        return (
            (bool(cid) and cid in self._seen_ids)
            or nurl in self._seen_urls
            or tkey in self._seen_title_keys
        )

    def mark_seen(self, item: ContainerItem) -> None:
        """Mark item as seen without filtering."""
        cid = self._canonical_id(item)
        nurl = self._normalize_url(item.url)
        tkey = self._title_key(item)
        if cid:
            self._seen_ids.add(cid)
        self._seen_urls.add(nurl)
        self._seen_title_keys[tkey] = item.title

    def reset(self) -> None:
        self._seen_ids.clear()
        self._seen_urls.clear()
        self._seen_title_keys.clear()
