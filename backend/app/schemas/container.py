"""
Container Schemas — Phase 25 PR1: Data Contracts
=================================================
Single source of truth for all container-workflow types shared across:
  - Backend API responses
  - Frontend TypeScript types (mirrored in frontend/src/types/container.ts)
  - Extension shared contract (mirrored in extension/src/shared/containerContract.ts)

Design rules:
  - All enums are str-based → serialize cleanly to JSON without extra conversion.
  - Pydantic v2 throughout.
  - No circular imports: this module imports only stdlib + pydantic.
  - Do NOT import from extractor, tasks, or redis here.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════
# ENUMS — stable string tokens shared across stack
# ═══════════════════════════════════════════════════════════════════

class Platform(str, Enum):
    tiktok       = "tiktok"
    douyin       = "douyin"
    facebook     = "facebook"
    threads      = "threads"
    pinterest    = "pinterest"
    soundcloud   = "soundcloud"
    spotify      = "spotify"
    instagram    = "instagram"
    twitter      = "twitter"
    reddit       = "reddit"
    youtube      = "youtube"
    bilibili     = "bilibili"
    xiaohongshu  = "xiaohongshu"
    lemon8       = "lemon8"
    snapchat     = "snapchat"
    vk           = "vk"
    twitch       = "twitch"
    rumble       = "rumble"
    odysee       = "odysee"
    dailymotion  = "dailymotion"
    podcast_rss  = "podcast_rss"
    generic      = "generic"
    unknown      = "unknown"


class SourceType(str, Enum):
    single_video   = "single_video"
    single_audio   = "single_audio"
    single_post    = "single_post"
    playlist       = "playlist"
    album          = "album"
    artist         = "artist"
    profile        = "profile"
    channel        = "channel"
    board          = "board"
    feed           = "feed"
    subreddit      = "subreddit"
    thread         = "thread"
    media_tab      = "media_tab"
    carousel       = "carousel"
    podcast_feed   = "podcast_feed"
    batch_mixed    = "batch_mixed"
    unknown        = "unknown"


# Source types that represent collections (containers) vs single items
CONTAINER_SOURCE_TYPES: frozenset[SourceType] = frozenset({
    SourceType.playlist,
    SourceType.album,
    SourceType.artist,
    SourceType.profile,
    SourceType.channel,
    SourceType.board,
    SourceType.feed,
    SourceType.subreddit,
    SourceType.media_tab,
    SourceType.podcast_feed,
})

def is_container_source_type(source_type: str) -> bool:
    """Return True if source_type represents a collection (container) vs a single item."""
    return source_type in {st.value for st in CONTAINER_SOURCE_TYPES}


SINGLE_ITEM_SOURCE_TYPES: frozenset[SourceType] = frozenset({
    SourceType.single_video,
    SourceType.single_audio,
    SourceType.single_post,
    SourceType.thread,
    SourceType.carousel,
})


class SupportLevel(str, Enum):
    full                 = "full"
    partial              = "partial"
    cookie_required      = "cookie_required"
    proxy_required       = "proxy_required"
    experimental         = "experimental"
    temporarily_disabled = "temporarily_disabled"
    unsupported          = "unsupported"


class ActionType(str, Enum):
    fetch_metadata      = "fetch_metadata"
    preview_items       = "preview_items"
    expand_children     = "expand_children"
    queue_download      = "queue_download"
    queue_audio         = "queue_audio"
    queue_video         = "queue_video"
    queue_no_watermark  = "queue_no_watermark"
    queue_subtitles     = "queue_subtitles"
    queue_zip           = "queue_zip"
    export_csv          = "export_csv"
    save_cloud          = "save_cloud"
    open_single_download  = "open_single_download"
    open_bulk_discovery   = "open_bulk_discovery"


class WarningCode(str, Enum):
    cookie_required       = "cookie_required"
    login_required        = "login_required"
    public_only           = "public_only"
    proxy_recommended     = "proxy_recommended"
    proxy_required        = "proxy_required"
    temporarily_disabled  = "temporarily_disabled"
    extraction_unstable   = "extraction_unstable"
    partial_support_only  = "partial_support_only"
    batch_not_supported   = "batch_not_supported"
    channel_not_supported = "channel_not_supported"
    audio_only            = "audio_only"
    video_only            = "video_only"


class EntryFlow(str, Enum):
    """Surface flow the frontend/extension should use for this capability."""
    single_download     = "single_download"   # → /fetch-link
    container_preview   = "container_preview" # → /discover-container → UI panel
    playlist_preview    = "playlist_preview"  # → /fetch-spotify (expand in UI)
    album_preview       = "album_preview"
    artist_preview      = "artist_preview"
    profile_scrape      = "profile_scrape"    # → /bulk-download with channel_mode
    batch_queue         = "batch_queue"       # → /bulk-download, no preview
    unsupported         = "unsupported"


class WarningSeverity(str, Enum):
    info    = "info"
    warning = "warning"
    error   = "error"


# ═══════════════════════════════════════════════════════════════════
# SHARED COMPONENT MODELS
# ═══════════════════════════════════════════════════════════════════

class RequirementFlags(BaseModel):
    """Boolean flags that describe what's needed to access this source."""
    cookie_required:    bool = False
    login_required:     bool = False
    public_only:        bool = True
    proxy_required:     bool = False
    proxy_recommended:  bool = False


class ContainerWarning(BaseModel):
    """A structured warning attached to a capability or container summary."""
    code:     WarningCode
    message:  str
    severity: WarningSeverity = WarningSeverity.warning


class CapabilityDescriptor(BaseModel):
    """
    Full capability description for a (platform, source_type) pair.
    Returned by the registry and embedded in resolve-input responses.
    """
    platform:          str
    source_type:       str
    support_level:     SupportLevel
    supported_actions: list[ActionType]         = Field(default_factory=list)
    requirements:      RequirementFlags         = Field(default_factory=RequirementFlags)
    warnings:          list[ContainerWarning]   = Field(default_factory=list)
    best_entry_flow:   EntryFlow                = EntryFlow.single_download
    recommended_endpoint: str                  = "/api/v1/fetch-link"
    notes:             str                      = ""

    # Legacy flat fields kept for backward compat with Phase 24 frontend
    @property
    def actions(self) -> list[str]:
        return [a.value for a in self.supported_actions]

    @property
    def best_flow(self) -> str:
        return self.best_entry_flow.value

    @property
    def api_endpoint(self) -> str:
        return self.recommended_endpoint


# ═══════════════════════════════════════════════════════════════════
# CONTAINER SUMMARY — root object for a discovered collection
# ═══════════════════════════════════════════════════════════════════

class ContainerSummary(BaseModel):
    """
    Summary of a discovered container source.
    Returned by /discover-container immediately; sections filled lazily.
    """
    container_id:        str
    platform:            str
    source_type:         str
    canonical_url:       str
    title:               str               = ""
    subtitle:            str               = ""
    thumbnail_url:       str               = ""
    item_count_estimate: int               = 0
    avatar_url:          str               = ""
    support:             Optional[CapabilityDescriptor] = None
    status:              str               = "discovering"   # discovering | ready | partial | failed
    error_message:       str               = ""
    warning:             str               = ""
    cached:              bool              = False


# ═══════════════════════════════════════════════════════════════════
# LEAF ITEM — one selectable media item inside a container
# ═══════════════════════════════════════════════════════════════════

class MediaKind(str, Enum):
    video = "video"
    audio = "audio"
    image = "image"
    mixed = "mixed"


class LeafMediaItem(BaseModel):
    """One selectable item inside a container (track, video, pin, post…)."""
    item_id:           str
    platform:          str
    source_type:       str
    url:               str
    title:             str              = ""
    author:            str              = ""
    thumbnail_url:     str              = ""
    duration_sec:      Optional[int]    = None
    published_at:      Optional[str]    = None    # ISO-8601
    media_kind:        MediaKind        = MediaKind.video
    selectable:        bool             = True
    supported_actions: list[ActionType] = Field(default_factory=list)
    views:             int              = 0
    extras:            dict[str, Any]   = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# SECTION + CHILD REF — structure inside a container
# ═══════════════════════════════════════════════════════════════════

class SectionType(str, Enum):
    flat_list       = "flat_list"        # simple ordered list of items
    group           = "group"            # grouped by some dimension (e.g. year)
    collection_ref  = "collection_ref"   # lazy child collections (e.g. albums list)


class ChildCollectionRef(BaseModel):
    """Reference to a nested collection (e.g. one album under 'Albums' section)."""
    ref_id:              str
    label:               str
    source_type:         str
    thumbnail_url:       str         = ""
    item_count_estimate: int         = 0
    expandable:          bool        = True
    expand_token:        str         = ""   # opaque cursor/id for expand call


class PreviewSection(BaseModel):
    """
    One named group inside a container preview.
    items is populated when items_loaded=True; child_refs when section_type=collection_ref.
    """
    section_id:    str
    label:         str
    section_type:  SectionType             = SectionType.flat_list
    item_count:    int                     = 0
    expandable:    bool                    = False
    items_loaded:  bool                    = False
    items:         list[LeafMediaItem]     = Field(default_factory=list)
    child_refs:    list[ChildCollectionRef]= Field(default_factory=list)
    has_more:      bool                    = False
    next_cursor:   str                     = ""


# ═══════════════════════════════════════════════════════════════════
# RESOLVE INPUT — request/response for /resolve-input
# ═══════════════════════════════════════════════════════════════════

class RoutingInfo(BaseModel):
    """Tells the caller where to send the user next."""
    surface_flow:         EntryFlow    = EntryFlow.single_download
    recommended_endpoint: str         = "/api/v1/fetch-link"
    is_container:         bool        = False
    is_batch:             bool        = False


class ResolveInputItem(BaseModel):
    """
    Resolution result for a single input string.
    Embeds the full CapabilityDescriptor so callers don't need a second request.
    """
    raw_input:         str
    normalized_input:  str
    canonical_url:     str
    is_short_link:     bool                     = False
    platform:          str
    source_type:       str
    normalized_id:     Optional[str]            = None
    capability:        CapabilityDescriptor
    routing:           RoutingInfo
    transformations:   list[str]               = Field(default_factory=list)

    # Flat convenience fields (keep Phase 24 compat)
    platform_label:      str  = ""
    platform_emoji:      str  = "🌐"
    source_type_label:   str  = ""
    support_level:       str  = ""
    support_level_label: str  = ""


class ResolveInputResult(BaseModel):
    """
    Full resolve-input response, possibly for multiple inputs.
    """
    batch_mode:        bool
    normalized_inputs: list[str]
    items:             list[ResolveInputItem]
    total:             int
    supported:         int
    cookie_required:   int
    unsupported:       int
    context:           str                   = "web"


# ═══════════════════════════════════════════════════════════════════
# DISPLAY HELPERS (used by resolve-input endpoint)
# ═══════════════════════════════════════════════════════════════════

SOURCE_TYPE_LABELS: dict[str, str] = {
    SourceType.single_video:  "Video đơn",
    SourceType.single_audio:  "Âm thanh",
    SourceType.single_post:   "Bài đăng",
    SourceType.playlist:      "Playlist",
    SourceType.album:         "Album",
    SourceType.artist:        "Nghệ sĩ",
    SourceType.channel:       "Kênh",
    SourceType.profile:       "Trang cá nhân",
    SourceType.board:         "Board",
    SourceType.subreddit:     "Cộng đồng",
    SourceType.podcast_feed:  "Podcast",
    SourceType.feed:          "Feed",
    SourceType.media_tab:     "Tab media",
    SourceType.carousel:      "Carousel",
    SourceType.thread:        "Thread",
    SourceType.unknown:       "Không xác định",
}

SUPPORT_LEVEL_LABELS: dict[str, str] = {
    SupportLevel.full:                 "Hỗ trợ đầy đủ",
    SupportLevel.partial:              "Hỗ trợ một phần",
    SupportLevel.cookie_required:      "Cần cookie",
    SupportLevel.proxy_required:       "Cần proxy",
    SupportLevel.experimental:         "Thử nghiệm",
    SupportLevel.temporarily_disabled: "Tạm dừng",
    SupportLevel.unsupported:          "Chưa hỗ trợ",
}

PLATFORM_EMOJIS: dict[str, str] = {
    Platform.tiktok:      "🎵",
    Platform.douyin:      "🎵",
    Platform.youtube:     "▶️",
    Platform.instagram:   "📸",
    Platform.facebook:    "📘",
    Platform.threads:     "🧵",
    Platform.pinterest:   "📌",
    Platform.soundcloud:  "🎧",
    Platform.spotify:     "🎵",
    Platform.reddit:      "🤖",
    Platform.twitter:     "🐦",
    Platform.bilibili:    "📺",
    Platform.xiaohongshu: "🌸",
    Platform.lemon8:      "🍋",
    Platform.snapchat:    "👻",
    Platform.vk:          "💙",
    Platform.twitch:      "🎮",
    Platform.rumble:      "📹",
    Platform.odysee:      "🌊",
    Platform.dailymotion: "📽️",
    Platform.podcast_rss: "🎙️",
    Platform.unknown:     "🌐",
    Platform.generic:     "🌐",
}
