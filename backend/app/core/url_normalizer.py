"""
URL Normalizer — Phase 24 Universal Capture
=============================================
Idempotent URL normalization pipeline. Runs BEFORE classification.

Pipeline per URL:
  1. Whitespace / protocol cleanup
  2. Host alias mapping  (m.youtube.com → youtube.com, youtu.be → youtube.com/watch?v=)
  3. Tracking param strip
  4. Platform-specific path normalization
  5. Short-link flag (does NOT expand — caller should use link_resolver for that)

Idempotent: normalize(normalize(x)) == normalize(x)
Non-destructive: never changes meaningful path/query params.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# ── Tracking params to strip (universal) ─────────────────────────────────────
_TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "igshid", "igsh", "ref", "ref_src", "ref_url",
    "feature", "si", "_branch_match_id", "s", "from", "app", "checksum",
    "share_app_id", "share_link_id", "_r", "_d", "ttwid", "unique_id",
    "enter_from", "lang", "is_copy_url", "is_from_webapp",
})

# ── Host aliases: non-canonical → canonical host ─────────────────────────────
_HOST_ALIASES: dict[str, str] = {
    # YouTube
    "m.youtube.com":      "youtube.com",
    "music.youtube.com":  "music.youtube.com",  # keep — separate service
    # TikTok
    "m.tiktok.com":       "www.tiktok.com",
    # Facebook
    "m.facebook.com":     "www.facebook.com",
    "l.facebook.com":     "www.facebook.com",
    "fb.com":             "www.facebook.com",
    # Instagram
    "www.instagram.com":  "instagram.com",
    # Twitter/X
    "mobile.twitter.com": "twitter.com",
    # Threads
    "threads.com":        "threads.net",
    # Reddit
    "old.reddit.com":     "www.reddit.com",
    "new.reddit.com":     "www.reddit.com",
    "sh.reddit.com":      "www.reddit.com",
}

# ── Short-link domains that need async expansion (flag only) ──────────────────
_SHORT_LINK_DOMAINS: frozenset[str] = frozenset({
    "v.douyin.com", "vt.tiktok.com", "vm.tiktok.com",
    "t.co", "bit.ly", "tinyurl.com", "ow.ly", "buff.ly",
    "youtu.be", "redd.it", "instagr.am", "b23.tv", "xhslink.com",
    "fb.watch",
})

# Kuaishou short-link hosts — counted as short links only while
# KUAISHOU_ENABLED is on (see normalize()).
_KUAISHOU_SHORT_HOSTS: frozenset[str] = frozenset({"v.kuaishou.com", "v.kuaishou.cn"})

# ── youtu.be shortlink rewrite → full YouTube URL ────────────────────────────
_YOUTU_BE_RE = re.compile(r"(?:https?://)?youtu\.be/([A-Za-z0-9_-]{11})(.*)", re.I)

# ── instagr.am shortlink rewrite ─────────────────────────────────────────────
_INSTAGR_AM_RE = re.compile(r"(?:https?://)?instagr\.am/(.*)", re.I)


# ── Share-text → URL extraction ──────────────────────────────────────────────
# Platform share sheets paste prose around the link ("8.94 :1pm anD:/ ...
# https://v.douyin.com/x/ 复制此链接，打开Dou音搜索…"). API clients (extension,
# desktop app, bulk) send that verbatim, so the server reduces it to the URL.
# A URL ends at whitespace or at CJK / full-width punctuation.
_FULLWIDTH_STOP = (
    "\u3000-\u303f"      # CJK symbols & punctuation (、。〈〉《》「」『』【】 …)
    "\uff00-\uffef"      # full-width forms (，！？：；（）＂ …)
    "\u2018\u2019\u201c\u201d\u2026"  # curly quotes, ellipsis
)
_SHARE_URL_RE = re.compile(
    r"https?://[^\s<>\"'`" + _FULLWIDTH_STOP + r"]+", re.I,
)
_NEEDS_EXTRACT_RE = re.compile(r"[\s" + _FULLWIDTH_STOP + r"]")
_TRAILING_PUNCT = ".,;:!?)]}>*~|"


def extract_share_url(text):
    """
    Reduce pasted share text to a single http(s) URL.

    * Input without whitespace or full-width punctuation (already a single
      token — including scheme-less ones the normalizer handles) is returned
      byte-identical.
    * Otherwise the FIRST http(s) URL in the text is returned, with trailing
      punctuation stripped; None when the text contains no http(s) URL.
    * Non-str / empty input -> None.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if not _NEEDS_EXTRACT_RE.search(stripped):
        return text
    m = _SHARE_URL_RE.search(stripped)
    if not m:
        return None
    url = m.group(0).rstrip(_TRAILING_PUNCT)
    return url if re.match(r"https?://.", url, re.I) else None


@dataclass
class NormalizeResult:
    canonical_url: str
    original_url:  str
    host:          str
    is_short_link: bool = False
    transformations: list[str] = field(default_factory=list)


def normalize(raw: str) -> NormalizeResult:
    """
    Normalize a single URL.  Never raises — on any parse failure returns
    the original URL with is_short_link=False.
    """
    original = raw.strip()
    url = original
    transformations: list[str] = []

    # 1. Whitespace / protocol
    url = url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        if url.startswith("//"):
            url = "https:" + url
        else:
            url = "https://" + url
        transformations.append("added_https")

    # 2. youtu.be → full YouTube watch URL
    m = _YOUTU_BE_RE.match(url)
    if m:
        vid_id = m.group(1)
        extra  = m.group(2) or ""
        url = f"https://www.youtube.com/watch?v={vid_id}"
        transformations.append("expanded_youtu_be")
        _ = extra  # preserve any fragment/time params if needed

    # 3. instagr.am → instagram.com
    m2 = _INSTAGR_AM_RE.match(url)
    if m2:
        url = f"https://www.instagram.com/{m2.group(1)}"
        transformations.append("expanded_instagr_am")

    try:
        parsed = urlparse(url)
    except Exception:
        return NormalizeResult(
            canonical_url=original, original_url=original,
            host="", is_short_link=False,
        )

    host = parsed.netloc.lower().lstrip("www.") if parsed.netloc else ""
    canonical_host = parsed.netloc.lower()

    # 4. Host alias mapping
    if canonical_host in _HOST_ALIASES:
        new_host = _HOST_ALIASES[canonical_host]
        canonical_host = new_host
        transformations.append(f"host_alias:{parsed.netloc.lower()}→{new_host}")

    # 5. Tracking param strip
    if parsed.query:
        qs = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
        if len(cleaned) != len(qs):
            transformations.append("stripped_tracking_params")
        new_query = urlencode(cleaned, doseq=True)
    else:
        new_query = ""

    # 6. Force https for known platforms
    scheme = "https" if parsed.scheme == "http" else parsed.scheme

    canonical = urlunparse((
        scheme,
        canonical_host,
        parsed.path.rstrip("/") or "/",
        parsed.params,
        new_query,
        "",   # drop fragment
    ))

    # 7. Short-link flag
    bare_host = canonical_host.lstrip("www.")
    is_short = bare_host in _SHORT_LINK_DOMAINS or canonical_host in _SHORT_LINK_DOMAINS
    if not is_short and canonical_host in _KUAISHOU_SHORT_HOSTS:
        # Flag-gated: with KUAISHOU_ENABLED off these stay ordinary URLs.
        from app.services.kuaishou_extractor import kuaishou_enabled
        is_short = kuaishou_enabled()

    return NormalizeResult(
        canonical_url=canonical,
        original_url=original,
        host=bare_host,
        is_short_link=is_short,
        transformations=transformations,
    )


def normalize_many(raws: list[str]) -> list[NormalizeResult]:
    """Normalize a list of raw URLs, dedup by canonical_url."""
    seen: set[str] = set()
    results: list[NormalizeResult] = []
    for raw in raws:
        r = normalize(raw)
        if r.canonical_url not in seen:
            seen.add(r.canonical_url)
            results.append(r)
    return results
