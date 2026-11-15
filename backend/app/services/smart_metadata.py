"""
Smart Metadata & Export Cleanup
================================
Cleans filenames, suggests tags, and enhances MP3 tags
using deterministic rules + available metadata signals.

No hallucination: fields with low confidence are left empty.
"""
from __future__ import annotations

import html
import re
from typing import Any

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

FILENAME_JUNK_PATTERNS: list[str] = [
    r"\s*[-|•]\s*(Official\s+)?(Music\s+)?(Video|Audio|Lyrics?|MV|Live|Performance)\s*$",
    r"\s*\(?(?:HD|HQ|4K|1080p|720p|480p)\)?\s*$",
    r"\[\s*(?:Official|Music|Lyric|Full)\s*\w*\s*\]",
    r"\(\s*(?:Official\s+)?(?:Video|Audio|Audio Only|Music Video|Lyric|HD|HQ|4K|720p|1080p|480p)\s*\)",
    r"\[\s*(?:Server\s*[^\]]*|VIP|FHD|Full\s*HD|Vietsub|Thuyết\s*Minh|Lồng\s*Tiếng)\s*\]",
    r"【\s*(?:高清|超清|蓝光|中字|生肉|熟肉|完整版)\s*】",
]

_SHARE_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"复制打开抖音.*$"),
    re.compile(r"长按复制此条消息.*$"),
    re.compile(r"https?://\S+"),
    re.compile(r"^\s*\d+(?:\.\d+)?\s+[A-Za-z0-9@._-]{2,12}\s+\d{2}/\d{2}\s+[A-Za-z]{2,5}:/\s*"),
)
_HASHTAG_CHAIN_RE = re.compile(r"(?:\s+#[\w\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff-]+){2,}\s*$")
_TRAILING_VIRAL_TAG_RE = re.compile(
    r"(?:\s+#(?:fyp|foryou|foryoupage|xuhuong|viral|trending|tiktok|douyin|reels|shorts)\b)+\s*$",
    re.IGNORECASE,
)
_SINGLE_HASHTAG_MARK_RE = re.compile(r"#([\w\u00C0-\u024F\u1E00-\u1EFF\u4e00-\u9fff-]+)")
_SERIES_EP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(.+?)(?:\s*[-–—:|•]\s*|\s+)"
        r"(?:(?:s|ss)\s*(\d{1,2})\s*(?:[-–—:|•\s]*)(?:ep?\.?)\s*(\d{1,4}))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(.+?)(?:\s*[-–—:|•]\s*|\s+)"
        r"(?:season\s*(\d{1,2})\s*[-–—:|•\s]*(?:episode|ep\.?)\s*(\d{1,4}))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(.+?)(?:\s*[-–—:|•]\s*|\s+)"
        r"((?:(?:phần|phan|mùa|mua|season)\s*\d+\s*[-–—:|•]?\s*)?(?:tập|tap|episode|ep\.?)\s*\d+(?:\.\d+)?(?:\s*(?:end|cuối|cuoi|final))?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^(.+?)(?:\s*[-–—:|•]\s*|\s+)(ova\s*\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"^(.+?)(?:\s*[-–—:|•]\s*|\s+)(第\s*\d+(?:\.\d+)?\s*[話话集])"),
    re.compile(r"^(.+?)\s*\[(\d{1,3})\s*/\s*\d{1,3}\](?:\s|$)"),
)

_FEAT_PATTERN = re.compile(
    r"\s*(ft\.?|feat\.?)\s+", re.IGNORECASE
)
_UNSAFE_CHARS = re.compile(r'[/\\:*?"<>|]')
_MULTI_DASHES = re.compile(r"-{2,}")
_MULTI_SPACES = re.compile(r" {2,}")
_TRAILING_JUNK = re.compile(r"[-.\s]+$")
_LEADING_JUNK = re.compile(r"^[-.\s]+")

_STOP_WORDS_EN = frozenset(
    ["a", "an", "the", "of", "in", "at", "to", "and", "or", "but", "for", "with", "on", "by"]
)
_STOP_WORDS_VI = frozenset(
    ["và", "của", "với", "là", "trong", "cho", "các", "một", "những", "này", "đó", "khi", "được", "từ"]
)
_STOP_WORDS = _STOP_WORDS_EN | _STOP_WORDS_VI

# ---------------------------------------------------------------------------
# Filename cleaning
# ---------------------------------------------------------------------------

def extract_series_episode(title: str) -> dict[str, str]:
    """Extracts normalized series name and episode tag when title clearly belongs to a series."""
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    if len(cleaned) < 5:
        return {"series_name": "", "episode_tag": ""}

    for idx, pat in enumerate(_SERIES_EP_PATTERNS):
        m = pat.match(cleaned)
        if not m:
            continue
        series = re.sub(r"[\s\-–—:|•(\[{/]+$", "", m.group(1)).strip()
        series = _UNSAFE_CHARS.sub(" ", series)
        series = _MULTI_SPACES.sub(" ", series).strip()
        if len(series) < 2 or len(series) > 90 or series.isdigit():
            continue
        if idx in (0, 1):
            season_num = int(m.group(2))
            ep_num = int(m.group(3))
            ep_tag = f"S{season_num:02d}E{ep_num:02d}"
        elif idx == 3:
            num = re.sub(r"^ova\s*", "", m.group(2), flags=re.IGNORECASE).strip()
            ep_tag = f"OVA{num.zfill(2)}" if num.isdigit() else m.group(2).upper()
        elif idx == 5:
            ep_tag = f"E{m.group(2).zfill(2)}"
        else:
            ep_tag = _MULTI_SPACES.sub(" ", m.group(2)).strip()
        return {"series_name": series, "episode_tag": ep_tag}

    return {"series_name": "", "episode_tag": ""}


def clean_filename(
    raw: str,
    template: str = "{title}",
    metadata: dict | None = None,
) -> dict:
    """
    Clean a raw filename/title string and render it using the given template.

    Returns:
        {
            "filename": str,
            "title_cleaned": str,
            "series_name": str,
            "episode_tag": str,
            "confidence": float,
            "changes_made": list[str],
        }
    """
    if metadata is None:
        metadata = {}

    changes: list[str] = []
    title = raw

    # Step 1: Decode HTML entities
    decoded = html.unescape(title)
    if decoded != title:
        changes.append("html_entities_decoded")
        title = decoded

    # Step 1b: Remove social share boilerplate / copy prompts / hashtags
    for share_re in _SHARE_NOISE_PATTERNS:
        updated = share_re.sub("", title).strip()
        if updated and updated != title:
            changes.append("share_boilerplate_removed")
            title = updated

    no_chain = _HASHTAG_CHAIN_RE.sub("", title).strip()
    if no_chain and no_chain != title:
        changes.append("hashtag_chain_removed")
        title = no_chain

    no_viral = _TRAILING_VIRAL_TAG_RE.sub("", title).strip()
    if no_viral and no_viral != title:
        changes.append("viral_hashtags_removed")
        title = no_viral

    unhashed = _SINGLE_HASHTAG_MARK_RE.sub(r"\1", title).strip()
    if unhashed and unhashed != title:
        changes.append("hashtag_symbols_stripped")
        title = unhashed

    # Step 2: Strip leading/trailing whitespace + normalize internal spaces
    stripped = title.strip()
    if stripped != title:
        changes.append("whitespace_stripped")
    title = stripped
    normalized = _MULTI_SPACES.sub(" ", title)
    if normalized != title:
        changes.append("internal_spaces_normalized")
    title = normalized

    # Step 3: Remove tracking junk patterns
    for pattern in FILENAME_JUNK_PATTERNS:
        new_title = re.sub(pattern, "", title, flags=re.IGNORECASE | re.MULTILINE).strip()
        if new_title != title:
            changes.append(f"junk_pattern_removed:{pattern[:30]}")
            title = new_title

    # Step 3b: Normalize feat. syntax but keep the feat artist
    feat_normalized = _FEAT_PATTERN.sub(" ft. ", title)
    if feat_normalized != title:
        changes.append("feat_syntax_normalized")
        title = feat_normalized

    # Step 4: Remove parenthetical junk at end (catch-all for anything not caught above)
    paren_junk = re.sub(
        r"\s*\([^()]{0,40}\)\s*$",
        lambda m: "" if re.search(
            r"official|audio|video|hd|hq|4k|\d{3,4}p|lyric|live",
            m.group(0),
            re.IGNORECASE,
        ) else m.group(0),
        title,
    ).strip()
    if paren_junk != title:
        changes.append("trailing_parenthetical_removed")
        title = paren_junk

    # Step 5: Normalize special chars for filename safety
    safe = _UNSAFE_CHARS.sub("-", title)
    if safe != title:
        changes.append("unsafe_chars_replaced")
    title = safe

    # Step 6: Collapse multiple dashes/spaces
    collapsed = _MULTI_DASHES.sub("-", _MULTI_SPACES.sub(" ", title))
    if collapsed != title:
        changes.append("dashes_spaces_collapsed")
    title = collapsed

    # Step 7: Strip trailing dashes and dots
    title = _TRAILING_JUNK.sub("", title)
    title = _LEADING_JUNK.sub("", title)

    title_cleaned = title.strip()

    # --- Template rendering ---
    uploader: str = str(metadata.get("uploader", metadata.get("creator", ""))).strip()
    platform: str = str(metadata.get("platform", "")).strip().lower()
    upload_date: str = str(metadata.get("upload_date", "")).strip()
    date_part = upload_date[:8] if len(upload_date) >= 8 else ""

    filename = (
        template
        .replace("{title}", title_cleaned)
        .replace("{creator}", uploader or "unknown")
        .replace("{platform}", platform or "unknown")
        .replace("{date}", date_part or "00000000")
    )
    # Final safety pass on filename
    filename = _UNSAFE_CHARS.sub("-", filename).strip()
    filename = _MULTI_DASHES.sub("-", filename)
    filename = _TRAILING_JUNK.sub("", filename)

    confidence: float
    if len(changes) == 0:
        confidence = 0.95
    elif len(changes) <= 2:
        confidence = 0.85
    else:
        confidence = 0.70

    series_info = extract_series_episode(title_cleaned)

    return {
        "filename": filename,
        "title_cleaned": title_cleaned,
        "series_name": series_info["series_name"],
        "episode_tag": series_info["episode_tag"],
        "confidence": confidence,
        "changes_made": changes,
    }


# ---------------------------------------------------------------------------
# Tag suggestion
# ---------------------------------------------------------------------------

def suggest_tags(
    platform: str,
    title: str,
    description: str = "",
    uploader: str = "",
    language: str = "",
) -> dict:
    """
    Suggest tags from deterministic rules + available metadata signals.

    Returns:
        {
            "tags": list[str],
            "confidence": float,
            "signals_used": list[str],
        }
    """
    tags: list[str] = []
    signals: list[str] = []

    # Always include platform tag
    platform_clean = platform.strip().lower()
    if platform_clean:
        tags.append(platform_clean)
        signals.append("platform")

    # Uploader as tag
    if uploader:
        uploader_tag = re.sub(r"[^a-zA-Z0-9À-ɏḀ-ỿ\s_-]", "", uploader)
        uploader_tag = uploader_tag.strip()[:30].strip()
        if uploader_tag:
            tags.append(uploader_tag)
            signals.append("uploader")

    # Language detection
    detected_lang = language.strip().lower() if language else ""
    if not detected_lang:
        vi_chars = re.search(r"[àáảãạăắặẳẵâầấậẩẫèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
                             title + description, re.IGNORECASE)
        if vi_chars:
            detected_lang = "vi"
        elif re.search(r"\b(the|and|with|this|that|you|are|was|for)\b", title + description, re.IGNORECASE):
            detected_lang = "en"

    if detected_lang:
        tags.append(f"language:{detected_lang}")
        signals.append("language_detected")

    # Content type tags
    combined = (title + " " + description).lower()

    if re.search(r"\b(feat|ft\.?|official|mv|lyrics?|music video|audio)\b", combined):
        tags.extend(["music", "audio"])
        signals.append("music_content")

    if re.search(r"\b(tutorial|how to|how-to|hướng dẫn|step by step)\b", combined):
        tags.extend(["tutorial", "educational"])
        signals.append("tutorial_content")

    if re.search(r"\b(podcast|interview|phỏng vấn|conversation|episode)\b", combined):
        tags.extend(["podcast", "interview"])
        signals.append("podcast_content")

    if re.search(r"\b(game|gameplay|gaming|playthru|playthrough|walkthrough|gamer)\b", combined):
        tags.append("gaming")
        signals.append("gaming_content")

    if re.search(r"\b(news|tin tức|breaking|headline|report|phóng sự)\b", combined):
        tags.append("news")
        signals.append("news_content")

    # Keyword extraction from title
    words = re.findall(r"[a-zA-ZÀ-ɏḀ-ỿ]{4,}", title)
    keywords: list[str] = []
    for w in words:
        w_lower = w.lower()
        if w_lower not in _STOP_WORDS and w_lower not in tags:
            keywords.append(w_lower)
        if len(keywords) >= 5:
            break
    if keywords:
        tags.extend(keywords)
        signals.append("title_keywords")

    # Deduplicate while preserving order
    seen: set[str] = set()
    tags_deduped: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            tags_deduped.append(t)

    n_signals = len(signals)
    if n_signals >= 3:
        confidence = 0.8
    elif n_signals >= 1:
        confidence = 0.5
    else:
        confidence = 0.2

    return {
        "tags": tags_deduped,
        "confidence": confidence,
        "signals_used": signals,
    }


# ---------------------------------------------------------------------------
# MP3 tag enhancement
# ---------------------------------------------------------------------------

def enhance_mp3_tags(metadata: dict) -> dict:
    """
    Derive enhanced MP3 ID3 tags from yt-dlp-style metadata.

    Returns:
        {
            "artist": str,
            "title": str,
            "album": str,
            "year": str,
            "genre": str,
            "comment": str,
            "cover_art_url": str,
            "confidence": float,
        }
    """
    raw_title: str = metadata.get("title", "") or ""
    uploader: str = (
        metadata.get("uploader")
        or metadata.get("channel")
        or metadata.get("artist")
        or ""
    )
    platform: str = (metadata.get("platform") or metadata.get("extractor_key") or "").lower()
    upload_date: str = metadata.get("upload_date", "") or ""

    # Title
    title_result = clean_filename(raw_title, template="{title}", metadata=metadata)
    title_clean = title_result["title_cleaned"] or raw_title

    # Artist
    artist: str = uploader.strip() if uploader else ""

    # Album
    album: str = ""
    if platform in ("spotify",):
        album = metadata.get("album", "") or ""
    elif platform in ("soundcloud",):
        album = metadata.get("album", "") or (f"{uploader} - Singles" if uploader else "")
    else:
        album = uploader.strip() if uploader else ""

    # Year
    year: str = ""
    if len(upload_date) >= 4 and upload_date[:4].isdigit():
        year = upload_date[:4]
    elif metadata.get("release_year"):
        year = str(metadata["release_year"])

    # Genre — derive from tag suggestions
    genre: str = ""
    tag_result = suggest_tags(
        platform=platform,
        title=raw_title,
        description=metadata.get("description", "") or "",
        uploader=uploader,
    )
    content_type_tags = {"music", "audio", "tutorial", "educational", "podcast", "gaming", "news"}
    for t in tag_result["tags"]:
        if t in content_type_tags:
            genre = t.capitalize()
            break

    # Comment
    comment = f"Downloaded from {platform} by VidGrab" if platform else "Downloaded by VidGrab"

    # Cover art URL (thumbnail)
    thumbnail = metadata.get("thumbnail") or ""
    if isinstance(thumbnail, list):
        # yt-dlp returns list of thumbnails
        thumbnail = thumbnail[0].get("url", "") if thumbnail else ""
    cover_art_url: str = thumbnail if isinstance(thumbnail, str) else ""

    # Confidence
    filled = sum(bool(x) for x in [artist, title_clean, album, year, genre, cover_art_url])
    if filled >= 5:
        confidence = 0.9
    elif filled >= 2:
        confidence = 0.6
    else:
        confidence = 0.3

    return {
        "artist": artist,
        "title": title_clean,
        "album": album,
        "year": year,
        "genre": genre,
        "comment": comment,
        "cover_art_url": cover_art_url,
        "confidence": confidence,
    }
