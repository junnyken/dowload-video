"""
Split over-long ASR segments into subtitle-sized cues.

2026-10-05 live selftest (gemini-3.5-flash, 60 s of VTV1 news): the prompt
asked for "at most about 7 seconds" yet the model returned 3 segments of
12-18 s and ~60 words each — a block of text covering half the frame for
16 seconds. The prompt alone is not a guarantee, so every result passes
through here.

A segment longer than max_sec or max_chars is cut at sentence ends first;
a sentence still too long is cut into parts of similar length between
words, preferring a cut after , ; : near the even point. Providers give no
word timings, so the segment's time span is shared out in proportion to
each piece's character count: the first cue still starts and the last
still ends exactly where the provider said; the cut points in between are
an estimate.
"""
from __future__ import annotations

import math
import re

from app.services.asr.types import Segment

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def _cut_balanced(words: list[str], k: int) -> list[str]:
    """Cut words into k parts of similar length. A cut snaps to a word ending
    in , ; : when one lies within a quarter part of the even cut point, so
    a short clause is not left alone on screen."""
    ends, pos = [], 0
    for i, w in enumerate(words):
        pos += len(w) + (1 if i else 0)
        ends.append(pos)
    total = ends[-1]
    window = total / k / 4
    cuts: list[int] = []
    for j in range(1, k):
        target = total * j / k
        cand = range(cuts[-1] + 1 if cuts else 0, len(words) - 1)
        if not cand:
            break

        def score(i: int) -> float:
            d = abs(ends[i] - target)
            return d - window if words[i][-1] in ",;:" and d <= window else d

        cuts.append(min(cand, key=score))
    parts, prev = [], 0
    for c in cuts:
        parts.append(" ".join(words[prev:c + 1]))
        prev = c + 1
    parts.append(" ".join(words[prev:]))
    return parts


def _split_sentence(sentence: str, size: float, max_chars: int) -> list[str]:
    """Fewest balanced parts of about `size` characters, none over max_chars
    (a single word longer than max_chars is kept whole)."""
    words = sentence.split()
    k = max(1, math.ceil(len(sentence) / size))
    while k < len(words):
        parts = _cut_balanced(words, k)
        if all(len(p) <= max_chars or " " not in p for p in parts):
            return parts
        k += 1
    return _cut_balanced(words, k) if len(words) > 1 else [sentence]


def _pieces(text: str, size: float, max_chars: int) -> list[str]:
    out: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        if not sentence:
            continue
        if len(sentence) <= size:
            out.append(sentence)
        else:
            out.extend(_split_sentence(sentence, size, max_chars))
    return out


def _pack(pieces: list[str], limit: float) -> list[str]:
    groups: list[str] = []
    cur = ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > limit:
            groups.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}" if cur else p
    if cur:
        groups.append(cur)
    return groups


def split_long_segments(segments: list[Segment], *, max_sec: float, max_chars: int) -> list[Segment]:
    out: list[Segment] = []
    for seg in segments:
        text = " ".join(seg.text.split())
        dur = seg.end - seg.start
        if not text or dur <= 0 or (dur <= max_sec and len(text) <= max_chars):
            out.append(seg)
            continue
        # Enough cues to satisfy both limits, with text spread evenly between them.
        n = max(math.ceil(len(text) / max_chars), math.ceil(dur / max_sec))
        size = min(max_chars, max(len(text) / n * 1.15, 1))
        groups = _pack(_pieces(text, size, max_chars), min(max_chars, size * 1.15))
        if len(groups) <= 1:
            out.append(seg)
            continue
        total = sum(len(g) for g in groups)
        start = seg.start
        for i, g in enumerate(groups):
            end = seg.end if i == len(groups) - 1 else round(start + dur * len(g) / total, 3)
            out.append(Segment(start=start, end=end, text=g, speaker=seg.speaker))
            start = end
    return out
