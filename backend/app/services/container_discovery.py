"""
Container Discovery — Phase 25
================================
Core data models + Redis cache helpers for container-source workflows.

A "container source" is any URL that resolves to a collection of media items
(profile, channel, playlist, album, artist, board, feed, subreddit, …).

Design:
  - All Redis ops are SYNCHRONOUS (mirrors existing get_redis() pattern).
  - ContainerMeta is the root object stored in Redis (JSON-serialised).
  - Sections are lazy: items_loaded=False until POST /expand is called.
  - TTL varies per container_type (hot social data < stable music data).
  - Cache key is sha256(canonical_url) → consistent across calls.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.core.redis_client import get_redis

# ── TTL policy ────────────────────────────────────────────────────────────────
# Shorter for frequently-updated sources, longer for stable library content.
CONTAINER_TTL: dict[str, int] = {
    "profile":    1800,   # 30 min — creator pages update often
    "channel":    1800,
    "media_tab":  3600,   # 1 h
    "playlist":   7200,   # 2 h — curator-defined, fairly stable
    "board":      3600,   # 1 h — pins added irregularly
    "feed":       1800,   # 30 min — new episodes drop regularly
    "subreddit":  900,    # 15 min — fast-moving community
    "album":      86400,  # 24 h — tracklist never changes
    "artist":     14400,  # 4 h — discography rarely changes
}
_DEFAULT_TTL = 1800

# Section items are more stable than root metadata → 2× TTL
_SECTION_TTL_MULTIPLIER = 2.0


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class ContainerItem:
    """One leaf media item inside a container (track, video, pin, post…)."""
    id: str                        # canonical source ID (e.g. "sp:4Rv…" / "tt:123")
    url: str                       # downloadable URL passed to /bulk-download
    title: str
    author: str = ""
    thumbnail: str = ""
    duration_ms: int = 0
    media_type: str = "video"      # video | audio | image | mixed
    views: int = 0
    published_at: str = ""         # ISO-8601 or YYYY-MM-DD string
    extras: dict = field(default_factory=dict)   # platform-specific fields


@dataclass
class ContainerChild:
    """A child collection inside a section (e.g. one album under 'Albums')."""
    id: str
    label: str
    item_count: int = 0
    thumbnail: str = ""
    extras: dict = field(default_factory=dict)


@dataclass
class ContainerSection:
    """
    One named group of items inside a container.

    items_loaded=False means items are NOT fetched yet (lazy expand).
    items_loaded=True  means items list is populated.
    """
    key: str                           # machine key: "top_tracks", "albums", …
    label: str                         # display label (Vietnamese ok)
    item_count: int = 0                # estimated or exact count
    items_loaded: bool = False
    items: list = field(default_factory=list)       # list[ContainerItem]
    children: list = field(default_factory=list)    # list[ContainerChild]
    has_more: bool = False
    next_cursor: str = ""              # opaque cursor for incremental load


@dataclass
class ContainerMeta:
    """Root object for a discovered container source."""
    container_id: str
    platform: str
    container_type: str        # profile | channel | playlist | album | artist | board | feed | subreddit | media_tab
    url: str                   # canonical URL used as cache key
    title: str
    description: str = ""
    avatar: str = ""
    item_count: int = 0        # total items in container (estimate ok)
    sections: list = field(default_factory=list)   # list[ContainerSection]
    status: str = "pending"    # pending | discovering | partial | ready | failed
    error_code: str = ""
    error_message: str = ""
    discovered_at: float = 0.0
    support_level: str = "full"   # full | partial | cookie_required | proxy_required | temporarily_disabled
    warning: str = ""


# ── ID helpers ────────────────────────────────────────────────────────────────

def make_container_id() -> str:
    return "ctr_" + uuid.uuid4().hex[:12]


def container_cache_key(url: str) -> str:
    h = hashlib.sha256(url.encode()).hexdigest()[:24]
    return f"vidgrab:container:{h}"


def container_id_map_key(container_id: str) -> str:
    return f"vidgrab:ctr_id:{container_id}"


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _to_dict(meta: ContainerMeta) -> dict:
    return asdict(meta)


def _from_dict(data: dict) -> ContainerMeta:
    sections_raw = data.pop("sections", [])
    meta = ContainerMeta(**{k: v for k, v in data.items()
                            if k in ContainerMeta.__dataclass_fields__})
    for s in sections_raw:
        items_raw = s.pop("items", [])
        children_raw = s.pop("children", [])
        section = ContainerSection(**{k: v for k, v in s.items()
                                      if k in ContainerSection.__dataclass_fields__})
        section.items = [ContainerItem(**i) for i in items_raw]
        section.children = [ContainerChild(**c) for c in children_raw]
        meta.sections.append(section)
    return meta


# ── Redis cache (synchronous) ─────────────────────────────────────────────────

def get_cached_container(url: str) -> Optional[ContainerMeta]:
    """Return cached ContainerMeta or None."""
    try:
        rc = get_redis()
        raw = rc.get(container_cache_key(url))
        if not raw:
            return None
        data = json.loads(raw)
        return _from_dict(data)
    except Exception:
        return None


def cache_container(meta: ContainerMeta) -> None:
    """Store ContainerMeta in Redis. TTL depends on container_type."""
    try:
        rc = get_redis()
        ttl = CONTAINER_TTL.get(meta.container_type, _DEFAULT_TTL)
        rc.setex(container_cache_key(meta.url), ttl, json.dumps(_to_dict(meta)))
        # Also store container_id → url mapping (for poll endpoint)
        rc.setex(container_id_map_key(meta.container_id), ttl + 3600,
                 meta.url)
    except Exception:
        pass


def update_container_fields(url: str, **kwargs) -> None:
    """Patch specific fields of a cached ContainerMeta in-place."""
    meta = get_cached_container(url)
    if meta is None:
        return
    for k, v in kwargs.items():
        if hasattr(meta, k):
            setattr(meta, k, v)
    cache_container(meta)


def get_url_for_container_id(container_id: str) -> Optional[str]:
    """Resolve container_id → canonical url (needed by poll endpoint)."""
    try:
        rc = get_redis()
        return rc.get(container_id_map_key(container_id))
    except Exception:
        return None


def invalidate_container(url: str) -> None:
    """Delete cached container (forces re-discover on next request)."""
    try:
        rc = get_redis()
        rc.delete(container_cache_key(url))
    except Exception:
        pass


# ── Admin metrics helpers ─────────────────────────────────────────────────────

def track_container_metric(platform: str, metric: str, value: int = 1) -> None:
    """
    Increment a container-level Redis counter.
    metric examples: "discover_ok", "discover_fail", "expand_ok", "queued"
    """
    try:
        import datetime
        rc = get_redis()
        today = datetime.date.today().isoformat()
        rc.hincrby(f"vidgrab:container:{today}", f"{platform}:{metric}", value)
        rc.expire(f"vidgrab:container:{today}", 8 * 86400)
    except Exception:
        pass
