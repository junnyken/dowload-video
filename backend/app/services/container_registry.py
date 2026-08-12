"""
Container Registry — Phase 25 PR1
===================================
Single source of truth for platform capability declarations.

Usage:
    from app.services.container_registry import get_capability, get_platform_matrix

    cap = get_capability("spotify", "artist")
    # → CapabilityDescriptor(support_level=partial, ...)

Design:
  - Registry is a static dict of (platform_slug, source_type_slug) → CapabilityDescriptor.
  - PR1 defines the shape; PR2 populates deep scrape logic.
  - get_capability() always returns a descriptor (falls back to _UNKNOWN_CAP).
  - No network calls, no yt-dlp, no Redis.
"""

from __future__ import annotations

from app.schemas.container import (
    ActionType,
    CapabilityDescriptor,
    ContainerWarning,
    EntryFlow,
    RequirementFlags,
    SupportLevel,
    WarningCode,
    WarningSeverity,
)


# ── Shorthand helpers ─────────────────────────────────────────────────────────

def _w(code: WarningCode, msg: str, sev: WarningSeverity = WarningSeverity.warning) -> ContainerWarning:
    return ContainerWarning(code=code, message=msg, severity=sev)


def _req(**kw) -> RequirementFlags:
    return RequirementFlags(**kw)


def _cap(
    platform: str,
    source_type: str,
    support: SupportLevel,
    actions: list[ActionType],
    requirements: RequirementFlags,
    warnings: list[ContainerWarning],
    flow: EntryFlow,
    endpoint: str = "/api/v1/fetch-link",
    notes: str = "",
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        platform=platform,
        source_type=source_type,
        support_level=support,
        supported_actions=actions,
        requirements=requirements,
        warnings=warnings,
        best_entry_flow=flow,
        recommended_endpoint=endpoint,
        notes=notes,
    )


_PUB = _req(public_only=True)
_COOKIE = _req(cookie_required=True, login_required=True)
_PROXY  = _req(proxy_required=True, public_only=True)
_COOKIE_PUB = _req(cookie_required=True)

_CONTAINER_ENDPOINT = "/api/v1/discover-container"
_BULK_ENDPOINT      = "/api/v1/bulk-download"
_FETCH_ENDPOINT     = "/api/v1/fetch-link"
_SPOTIFY_ENDPOINT   = "/api/v1/fetch-spotify"
_THREADS_ENDPOINT   = "/api/v1/fetch-threads"


# ═══════════════════════════════════════════════════════════════════
# REGISTRY
# ═══════════════════════════════════════════════════════════════════
# Key: (platform_slug, source_type_slug)
# Order inside each platform: most important / most used first.

_REGISTRY: dict[tuple[str, str], CapabilityDescriptor] = {}


def _reg(cap: CapabilityDescriptor) -> None:
    _REGISTRY[(cap.platform, cap.source_type)] = cap


# ── YouTube ──────────────────────────────────────────────────────────────────

_reg(_cap("youtube", "single_video", SupportLevel.proxy_required,
    [ActionType.queue_video, ActionType.queue_audio, ActionType.queue_subtitles],
    _PROXY,
    [_w(WarningCode.proxy_required, "Yêu cầu proxy trả phí — tốc độ chậm hơn các nền tảng khác.")],
    EntryFlow.single_download,
))

_reg(_cap("youtube", "playlist", SupportLevel.proxy_required,
    [ActionType.queue_video, ActionType.queue_audio],
    _PROXY,
    [_w(WarningCode.proxy_required, "Yêu cầu proxy — tải playlist lớn có thể chậm.")],
    EntryFlow.batch_queue, _BULK_ENDPOINT,
))

_reg(_cap("youtube", "channel", SupportLevel.proxy_required,
    [ActionType.queue_video],
    _PROXY,
    [_w(WarningCode.proxy_required, "Yêu cầu proxy — chỉ tải được channel công khai.")],
    EntryFlow.batch_queue, _BULK_ENDPOINT,
))


# ── TikTok ────────────────────────────────────────────────────────────────────

_reg(_cap("tiktok", "single_video", SupportLevel.full,
    [ActionType.queue_video, ActionType.queue_audio, ActionType.queue_no_watermark],
    _PUB, [],
    EntryFlow.single_download,
))

_reg(_cap("tiktok", "profile", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_video, ActionType.queue_audio,
     ActionType.queue_no_watermark, ActionType.queue_zip, ActionType.export_csv],
    _PUB,
    [_w(WarningCode.public_only, "Chỉ scrape được profile công khai.", WarningSeverity.info)],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
    notes="Calls scrape_channel_entries_sync → TikTokExpander.",
))


# ── Douyin ────────────────────────────────────────────────────────────────────

_reg(_cap("douyin", "single_video", SupportLevel.full,
    [ActionType.queue_video, ActionType.queue_audio, ActionType.queue_no_watermark],
    _PUB, [],
    EntryFlow.single_download,
))

_reg(_cap("douyin", "profile", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_video, ActionType.queue_no_watermark,
     ActionType.queue_zip, ActionType.export_csv],
    _PUB, [],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
    notes="Routes to TikTokExpander (reuses TikTok scraper).",
))


# ── Facebook ─────────────────────────────────────────────────────────────────

_reg(_cap("facebook", "single_video", SupportLevel.full,
    [ActionType.queue_video, ActionType.queue_audio],
    _PUB,
    [_w(WarningCode.public_only, "Video riêng tư cần đăng nhập — mặc định chỉ hỗ trợ video công khai.", WarningSeverity.info)],
    EntryFlow.single_download,
))

_reg(_cap("facebook", "channel", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_video, ActionType.export_csv],
    _PUB,
    [_w(WarningCode.public_only, "Chỉ tải được video công khai từ tab video của trang/nhóm."),
     _w(WarningCode.partial_support_only, "Tối đa ~50 video/lần. Một số định dạng không hỗ trợ.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))

_reg(_cap("facebook", "profile", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_video],
    _PUB,
    [_w(WarningCode.partial_support_only, "Chuyển hướng sang tab video tự động.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))


# ── Threads ───────────────────────────────────────────────────────────────────

_reg(_cap("threads", "single_post", SupportLevel.full,
    [ActionType.queue_video, ActionType.queue_audio, ActionType.open_single_download],
    _PUB,
    [_w(WarningCode.public_only, "Chỉ hỗ trợ bài đăng công khai.", WarningSeverity.info)],
    EntryFlow.single_download, _THREADS_ENDPOINT,
))

_reg(_cap("threads", "profile", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_video, ActionType.export_csv],
    _PUB,
    [_w(WarningCode.public_only, "Chỉ hỗ trợ profile công khai."),
     _w(WarningCode.extraction_unstable, "Anti-bot có thể giới hạn số lượng bài lấy được.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
    notes="Uses scrape_threads_profile_sync → ThreadsExpander.",
))


# ── Pinterest ─────────────────────────────────────────────────────────────────

_reg(_cap("pinterest", "single_post", SupportLevel.full,
    [ActionType.queue_video, ActionType.open_single_download],
    _PUB, [],
    EntryFlow.single_download,
))

_reg(_cap("pinterest", "board", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_video, ActionType.queue_zip, ActionType.export_csv],
    _PUB, [],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
    notes="PinterestExpander: video + image sections, yt-dlp extract_flat.",
))

_reg(_cap("pinterest", "profile", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_video],
    _PUB,
    [_w(WarningCode.partial_support_only, "Chỉ hỗ trợ board/profile công khai.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))


# ── SoundCloud ────────────────────────────────────────────────────────────────

_reg(_cap("soundcloud", "single_audio", SupportLevel.full,
    [ActionType.queue_audio, ActionType.open_single_download],
    _PUB, [],
    EntryFlow.single_download,
))

_reg(_cap("soundcloud", "playlist", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_audio, ActionType.queue_zip, ActionType.export_csv],
    _PUB, [],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
    notes="SoundCloudExpander uses yt-dlp extract_flat.",
))

_reg(_cap("soundcloud", "artist", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_audio, ActionType.export_csv],
    _PUB,
    [_w(WarningCode.partial_support_only, "Chỉ lấy được danh sách uploads công khai.", WarningSeverity.info)],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
    notes="SoundCloudExpander: single flat section 'uploads'.",
))

_reg(_cap("soundcloud", "media_tab", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_audio],
    _PUB,
    [_w(WarningCode.partial_support_only, "Chỉ hỗ trợ tab likes/reposts công khai.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))


# ── Spotify ──────────────────────────────────────────────────────────────────

_reg(_cap("spotify", "single_audio", SupportLevel.full,
    [ActionType.queue_audio, ActionType.open_single_download],
    _PUB,
    [_w(WarningCode.public_only,
        "Tải qua YouTube match — chất lượng phụ thuộc độ phổ biến bài nhạc.", WarningSeverity.info)],
    EntryFlow.single_download,
    notes="Routes to /fetch-spotify → ytsearch → yt-dlp.",
))

_reg(_cap("spotify", "playlist", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_audio, ActionType.queue_zip, ActionType.export_csv],
    _PUB,
    [_w(WarningCode.public_only, "Mỗi track tải qua YouTube match.", WarningSeverity.info)],
    EntryFlow.playlist_preview, _SPOTIFY_ENDPOINT,
    notes="Uses get_playlist_tracks_async; each track → ytsearch download.",
))

_reg(_cap("spotify", "album", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_audio, ActionType.queue_zip, ActionType.export_csv],
    _PUB,
    [_w(WarningCode.public_only, "Mỗi track tải qua YouTube match.", WarningSeverity.info)],
    EntryFlow.album_preview, _SPOTIFY_ENDPOINT,
    notes="Uses get_album_tracks_async.",
))

_reg(_cap("spotify", "artist", SupportLevel.experimental,
    [ActionType.preview_items, ActionType.expand_children, ActionType.queue_audio],
    _PUB,
    [_w(WarningCode.partial_support_only,
        "Spotify artist đang ở mức thử nghiệm — chỉ lấy top tracks + danh sách album.",
        WarningSeverity.warning)],
    EntryFlow.artist_preview, _CONTAINER_ENDPOINT,
    notes="SpotifyExpander: top_tracks eager, albums/singles lazy. Audio via ytsearch.",
))


# ── Instagram ─────────────────────────────────────────────────────────────────

_reg(_cap("instagram", "single_video", SupportLevel.cookie_required,
    [ActionType.queue_video, ActionType.queue_audio],
    _COOKIE,
    [_w(WarningCode.cookie_required, "Cần cookie Instagram (admin cấu hình) để tải nội dung riêng tư.")],
    EntryFlow.single_download,
))

_reg(_cap("instagram", "single_post", SupportLevel.cookie_required,
    [ActionType.queue_video, ActionType.open_single_download],
    _COOKIE,
    [_w(WarningCode.cookie_required, "Cần cookie Instagram để tải nội dung riêng tư.")],
    EntryFlow.single_download,
))

_reg(_cap("instagram", "profile", SupportLevel.cookie_required,
    [ActionType.preview_items, ActionType.queue_video],
    _COOKIE,
    [_w(WarningCode.cookie_required, "Cần cookie — chỉ scrape được profile đã đăng nhập hoặc công khai.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))

_reg(_cap("instagram", "media_tab", SupportLevel.cookie_required,
    [ActionType.preview_items, ActionType.queue_video],
    _COOKIE,
    [_w(WarningCode.cookie_required, "Cần cookie để truy cập tab media.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))


# ── Twitter / X ──────────────────────────────────────────────────────────────

_reg(_cap("twitter", "single_post", SupportLevel.cookie_required,
    [ActionType.queue_video],
    _COOKIE,
    [_w(WarningCode.cookie_required, "Cần cookie Twitter/X để tải tweet có giới hạn độ tuổi.")],
    EntryFlow.single_download,
))

_reg(_cap("twitter", "profile", SupportLevel.cookie_required,
    [ActionType.preview_items, ActionType.queue_video, ActionType.export_csv],
    _COOKIE,
    [_w(WarningCode.cookie_required, "Cần cookie — media tab thường bị rate-limit."),
     _w(WarningCode.extraction_unstable, "Rate-limit chặt — tối đa 100 video/lần.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))


# ── Reddit ────────────────────────────────────────────────────────────────────

_reg(_cap("reddit", "single_post", SupportLevel.full,
    [ActionType.queue_video, ActionType.queue_audio],
    _PUB,
    [_w(WarningCode.public_only, "Video Reddit cần merge audio — xử lý tự động.", WarningSeverity.info)],
    EntryFlow.single_download,
))

_reg(_cap("reddit", "subreddit", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_video],
    _PUB,
    [_w(WarningCode.partial_support_only, "Chỉ tải được video từ post công khai của subreddit."),
     _w(WarningCode.extraction_unstable, "Reddit API giới hạn 50 post/lần.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))

_reg(_cap("reddit", "profile", SupportLevel.partial,
    [ActionType.preview_items, ActionType.queue_video],
    _PUB,
    [_w(WarningCode.partial_support_only, "Chỉ tải được video từ post công khai của user.")],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))


# ── Bilibili ──────────────────────────────────────────────────────────────────

_reg(_cap("bilibili", "single_video", SupportLevel.full,
    [ActionType.queue_video, ActionType.queue_audio, ActionType.queue_subtitles],
    _req(cookie_required=False),
    [_w(WarningCode.cookie_required, "Nội dung VIP cần cookie Bilibili.", WarningSeverity.info)],
    EntryFlow.single_download,
))

_reg(_cap("bilibili", "profile", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_video, ActionType.export_csv],
    _PUB, [],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))

_reg(_cap("bilibili", "playlist", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_video, ActionType.queue_zip],
    _PUB, [],
    EntryFlow.batch_queue, _BULK_ENDPOINT,
))


# ── Xiaohongshu ──────────────────────────────────────────────────────────────

_reg(_cap("xiaohongshu", "single_post", SupportLevel.cookie_required,
    [ActionType.queue_video, ActionType.open_single_download],
    _COOKIE,
    [_w(WarningCode.cookie_required, "Cần cookie XHS. Một số nội dung yêu cầu CN proxy.")],
    EntryFlow.single_download,
))


# ── Lemon8 ───────────────────────────────────────────────────────────────────

_reg(_cap("lemon8", "single_post", SupportLevel.partial,
    [ActionType.queue_video, ActionType.open_single_download],
    _PUB,
    [_w(WarningCode.extraction_unstable, "Lemon8 đang ở mức thử nghiệm.")],
    EntryFlow.single_download,
))


# ── Podcast RSS ───────────────────────────────────────────────────────────────

_reg(_cap("podcast_rss", "podcast_feed", SupportLevel.full,
    [ActionType.preview_items, ActionType.queue_audio, ActionType.queue_zip, ActionType.export_csv],
    _PUB, [],
    EntryFlow.container_preview, _CONTAINER_ENDPOINT,
))


# Fallback for any (platform, source_type) not in registry
_UNKNOWN_CAP = CapabilityDescriptor(
    platform="unknown",
    source_type="unknown",
    support_level=SupportLevel.unsupported,
    supported_actions=[],
    requirements=RequirementFlags(),
    warnings=[ContainerWarning(
        code=WarningCode.partial_support_only,
        message="Nền tảng hoặc loại nguồn này chưa được hỗ trợ.",
        severity=WarningSeverity.warning,
    )],
    best_entry_flow=EntryFlow.unsupported,
    recommended_endpoint="/api/v1/fetch-link",
)

_PARTIAL_FALLBACK = CapabilityDescriptor(
    platform="generic",
    source_type="single_video",
    support_level=SupportLevel.partial,
    supported_actions=[ActionType.queue_video, ActionType.queue_audio],
    requirements=RequirementFlags(public_only=True),
    warnings=[ContainerWarning(
        code=WarningCode.partial_support_only,
        message="Nền tảng hoặc loại nguồn này có thể chưa được tối ưu.",
        severity=WarningSeverity.warning,
    )],
    best_entry_flow=EntryFlow.single_download,
    recommended_endpoint="/api/v1/fetch-link",
)


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════

def get_capability(platform: str, source_type: str) -> CapabilityDescriptor:
    """
    Look up capability for (platform, source_type).

    Returns:
      - Exact match if known.
      - Partial-fallback for generic/unknown but valid URL.
      - UNKNOWN_CAP for truly unsupported cases.
    """
    cap = _REGISTRY.get((platform, source_type))
    if cap:
        # Return a copy with the actual platform/source_type filled in
        return cap

    if platform in ("unknown", "generic") or source_type == "unknown":
        fallback = _PARTIAL_FALLBACK.model_copy(
            update={"platform": platform, "source_type": source_type}
        )
        return fallback

    # Known platform, unknown source type → return partial fallback with correct labels
    fallback = _PARTIAL_FALLBACK.model_copy(
        update={"platform": platform, "source_type": source_type}
    )
    return fallback


def get_platform_source_types(platform: str) -> list[CapabilityDescriptor]:
    """All registered source types for a platform, sorted by support level."""
    result = [v for (p, _), v in _REGISTRY.items() if p == platform]
    _LEVEL_ORDER = {
        SupportLevel.full: 0,
        SupportLevel.partial: 1,
        SupportLevel.experimental: 2,
        SupportLevel.cookie_required: 3,
        SupportLevel.proxy_required: 4,
        SupportLevel.temporarily_disabled: 5,
        SupportLevel.unsupported: 6,
    }
    return sorted(result, key=lambda c: _LEVEL_ORDER.get(c.support_level, 9))


def get_all_platforms() -> list[str]:
    """Unique platform slugs in the registry."""
    seen: set[str] = set()
    result = []
    for (platform, _) in _REGISTRY:
        if platform not in seen:
            seen.add(platform)
            result.append(platform)
    return sorted(result)


def get_full_matrix() -> dict[str, list[CapabilityDescriptor]]:
    """Full registry grouped by platform. Used by /platforms/capabilities."""
    matrix: dict[str, list[CapabilityDescriptor]] = {}
    for (platform, _), cap in _REGISTRY.items():
        matrix.setdefault(platform, []).append(cap)
    return matrix
