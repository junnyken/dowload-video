"""
PR1 Contract Tests — Phase 25
==============================
Covers:
  A. Schema validation + enum serialization
  B. Source classifier (resolve-input parser)
  C. Container registry (capability lookups)
  D. Batch mode / multi-URL handling
  E. End-to-end resolve endpoint (FastAPI TestClient)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.container import (
    CONTAINER_SOURCE_TYPES,
    ActionType,
    CapabilityDescriptor,
    ContainerWarning,
    EntryFlow,
    LeafMediaItem,
    MediaKind,
    Platform,
    PreviewSection,
    RequirementFlags,
    SourceType,
    SupportLevel,
    WarningCode,
    WarningSeverity,
    is_container_source_type,
)
from app.core.source_classifier import classify
from app.services.container_registry import (
    get_capability,
    get_full_matrix,
    get_platform_source_types,
)


# ═══════════════════════════════════════════════════════════════════
# A. TAXONOMY / SCHEMA
# ═══════════════════════════════════════════════════════════════════

class TestEnumSerialization:
    def test_support_level_values(self):
        assert SupportLevel.full.value == "full"
        assert SupportLevel.cookie_required.value == "cookie_required"
        assert SupportLevel.temporarily_disabled.value == "temporarily_disabled"

    def test_platform_values(self):
        assert Platform.tiktok.value == "tiktok"
        assert Platform.spotify.value == "spotify"
        assert Platform.unknown.value == "unknown"

    def test_source_type_values(self):
        assert SourceType.single_video.value == "single_video"
        assert SourceType.podcast_feed.value == "podcast_feed"

    def test_action_type_values(self):
        assert ActionType.queue_no_watermark.value == "queue_no_watermark"
        assert ActionType.export_csv.value == "export_csv"

    def test_entry_flow_values(self):
        assert EntryFlow.container_preview.value == "container_preview"
        assert EntryFlow.unsupported.value == "unsupported"

    def test_warning_code_values(self):
        assert WarningCode.cookie_required.value == "cookie_required"
        assert WarningCode.extraction_unstable.value == "extraction_unstable"

    def test_enums_are_str(self):
        # Must be serializable as plain strings (no extra __value__ nesting)
        assert isinstance(SupportLevel.full, str)
        assert SupportLevel.full == "full"


class TestSchemaValidation:
    def test_requirement_flags_defaults(self):
        req = RequirementFlags()
        assert req.cookie_required is False
        assert req.public_only is True

    def test_container_warning_model(self):
        w = ContainerWarning(
            code=WarningCode.proxy_required,
            message="test",
            severity=WarningSeverity.error,
        )
        assert w.code == "proxy_required"
        d = w.model_dump()
        assert d["code"] == "proxy_required"
        assert d["severity"] == "error"

    def test_capability_descriptor_defaults(self):
        cap = CapabilityDescriptor(
            platform="tiktok",
            source_type="single_video",
            support_level=SupportLevel.full,
        )
        assert cap.supported_actions == []
        assert cap.requirements.public_only is True
        assert cap.best_entry_flow == EntryFlow.single_download

    def test_capability_descriptor_json_round_trip(self):
        cap = CapabilityDescriptor(
            platform="spotify",
            source_type="artist",
            support_level=SupportLevel.experimental,
            supported_actions=[ActionType.preview_items, ActionType.queue_audio],
            best_entry_flow=EntryFlow.artist_preview,
        )
        data = cap.model_dump()
        assert data["support_level"] == "experimental"
        assert "preview_items" in data["supported_actions"]
        # Reconstruct from dict
        cap2 = CapabilityDescriptor(**data)
        assert cap2.support_level == SupportLevel.experimental

    def test_leaf_media_item_defaults(self):
        item = LeafMediaItem(
            item_id="t1", platform="tiktok", source_type="single_video", url="https://x"
        )
        assert item.media_kind == MediaKind.video
        assert item.selectable is True
        assert item.views == 0

    def test_preview_section_empty(self):
        sec = PreviewSection(section_id="top_tracks", label="Top tracks")
        assert sec.items_loaded is False
        assert sec.items == []
        assert sec.child_refs == []

    def test_container_source_types_set(self):
        assert SourceType.playlist in CONTAINER_SOURCE_TYPES
        assert SourceType.single_video not in CONTAINER_SOURCE_TYPES
        assert SourceType.profile in CONTAINER_SOURCE_TYPES

    def test_is_container_source_type_helper(self):
        assert is_container_source_type("playlist") is True
        assert is_container_source_type("single_video") is False
        assert is_container_source_type("garbage") is False

    def test_invalid_support_level_raises(self):
        with pytest.raises(ValidationError):
            CapabilityDescriptor(
                platform="x", source_type="y", support_level="not_a_level"
            )


# ═══════════════════════════════════════════════════════════════════
# B. SOURCE CLASSIFIER
# ═══════════════════════════════════════════════════════════════════

class TestSourceClassifier:
    # ── Spotify ──────────────────────────────────────────────────
    def test_spotify_track(self):
        r = classify("https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT")
        assert r.platform == "spotify"
        assert r.source_type == "single_audio"
        assert r.normalized_id == "4cOdK2wGLETKBW3PvgPWqT"

    def test_spotify_playlist(self):
        r = classify("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
        assert r.platform == "spotify"
        assert r.source_type == "playlist"

    def test_spotify_album(self):
        r = classify("https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3")
        assert r.platform == "spotify"
        assert r.source_type == "album"

    def test_spotify_artist(self):
        r = classify("https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02")
        assert r.platform == "spotify"
        assert r.source_type == "artist"

    # ── SoundCloud ───────────────────────────────────────────────
    def test_soundcloud_track(self):
        r = classify("https://soundcloud.com/artistname/trackname")
        assert r.platform == "soundcloud"
        assert r.source_type == "single_audio"

    def test_soundcloud_playlist(self):
        r = classify("https://soundcloud.com/artistname/sets/myplaylist")
        assert r.platform == "soundcloud"
        assert r.source_type == "playlist"

    def test_soundcloud_artist(self):
        r = classify("https://soundcloud.com/artistname")
        assert r.platform == "soundcloud"
        assert r.source_type == "artist"

    # ── TikTok ───────────────────────────────────────────────────
    def test_tiktok_single_video(self):
        r = classify("https://www.tiktok.com/@username/video/1234567890123456789")
        assert r.platform == "tiktok"
        assert r.source_type == "single_video"

    def test_tiktok_profile(self):
        r = classify("https://www.tiktok.com/@username")
        assert r.platform == "tiktok"
        assert r.source_type == "profile"

    def test_tiktok_short_link(self):
        r = classify("https://vm.tiktok.com/ABCDE12345/")
        assert r.platform == "tiktok"
        assert r.source_type == "single_video"

    # ── Douyin ───────────────────────────────────────────────────
    def test_douyin_user(self):
        r = classify("https://www.douyin.com/user/MS4wLjABAAAA123abc")
        assert r.platform == "douyin"
        assert r.source_type == "profile"

    def test_douyin_video(self):
        r = classify("https://www.douyin.com/video/7123456789012345678")
        assert r.platform == "douyin"
        assert r.source_type == "single_video"

    # ── Threads ──────────────────────────────────────────────────
    def test_threads_post(self):
        r = classify("https://www.threads.net/p/CuXyZ123abc")
        assert r.platform == "threads"
        assert r.source_type == "single_post"

    def test_threads_profile(self):
        r = classify("https://www.threads.net/@username")
        assert r.platform == "threads"
        assert r.source_type == "profile"

    # ── Pinterest ─────────────────────────────────────────────────
    def test_pinterest_board(self):
        r = classify("https://www.pinterest.com/username/board-name/")
        assert r.platform == "pinterest"
        assert r.source_type == "board"

    def test_pinterest_pin(self):
        r = classify("https://www.pinterest.com/pin/123456789012345678/")
        assert r.platform == "pinterest"
        assert r.source_type == "single_post"

    # ── Instagram ─────────────────────────────────────────────────
    def test_instagram_profile(self):
        r = classify("https://www.instagram.com/username/")
        assert r.platform == "instagram"
        assert r.source_type == "profile"

    def test_instagram_reel(self):
        r = classify("https://www.instagram.com/reel/CuABC123def/")
        assert r.platform == "instagram"
        assert r.source_type == "single_video"

    # ── Twitter / X ───────────────────────────────────────────────
    def test_twitter_post(self):
        r = classify("https://twitter.com/username/status/1234567890123456789")
        assert r.platform == "twitter"
        assert r.source_type == "single_post"

    def test_x_com_post(self):
        r = classify("https://x.com/username/status/9876543210123456789")
        assert r.platform == "twitter"
        assert r.source_type == "single_post"

    def test_twitter_profile(self):
        r = classify("https://twitter.com/username")
        assert r.platform == "twitter"
        assert r.source_type == "profile"

    # ── Reddit ────────────────────────────────────────────────────
    def test_reddit_subreddit(self):
        r = classify("https://www.reddit.com/r/videos/")
        assert r.platform == "reddit"
        assert r.source_type == "subreddit"

    def test_reddit_post(self):
        r = classify("https://www.reddit.com/r/videos/comments/abc123/my_post/")
        assert r.platform == "reddit"
        assert r.source_type == "single_post"

    # ── YouTube ───────────────────────────────────────────────────
    def test_youtube_video(self):
        r = classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert r.platform == "youtube"
        assert r.source_type == "single_video"

    def test_youtube_playlist(self):
        r = classify("https://www.youtube.com/playlist?list=PLrEnWoR732-BHrPp_Pm8_VleD68f9s14-")
        assert r.platform == "youtube"
        assert r.source_type == "playlist"

    def test_youtube_channel_at(self):
        r = classify("https://www.youtube.com/@MrBeast")
        assert r.platform == "youtube"
        assert r.source_type == "channel"

    def test_youtube_channel_c(self):
        r = classify("https://www.youtube.com/c/mkbhd")
        assert r.platform == "youtube"
        assert r.source_type == "channel"

    # ── Unknown / garbage ────────────────────────────────────────
    def test_unknown_url(self):
        r = classify("https://example.com/totally/unknown")
        assert r.platform == "unknown"
        assert r.source_type == "unknown"
        assert r.normalized_id is None

    def test_raw_text_no_url(self):
        r = classify("just some random text without a URL")
        assert r.platform == "unknown"
        assert r.source_type == "unknown"

    def test_empty_string(self):
        r = classify("")
        assert r.platform == "unknown"


# ═══════════════════════════════════════════════════════════════════
# C. CONTAINER REGISTRY
# ═══════════════════════════════════════════════════════════════════

class TestContainerRegistry:
    def test_tiktok_single_video_full(self):
        cap = get_capability("tiktok", "single_video")
        assert cap.support_level == SupportLevel.full
        assert ActionType.queue_no_watermark in cap.supported_actions

    def test_tiktok_profile_container(self):
        cap = get_capability("tiktok", "profile")
        assert cap.support_level == SupportLevel.full
        assert cap.best_entry_flow == EntryFlow.container_preview
        assert cap.recommended_endpoint == "/api/v1/discover-container"

    def test_spotify_track_full(self):
        cap = get_capability("spotify", "single_audio")
        assert cap.support_level == SupportLevel.full
        assert cap.best_entry_flow == EntryFlow.single_download

    def test_spotify_playlist_full(self):
        cap = get_capability("spotify", "playlist")
        assert cap.support_level == SupportLevel.full
        assert cap.best_entry_flow == EntryFlow.playlist_preview

    def test_spotify_album_full(self):
        cap = get_capability("spotify", "album")
        assert cap.support_level == SupportLevel.full
        assert cap.best_entry_flow == EntryFlow.album_preview

    def test_spotify_artist_experimental(self):
        cap = get_capability("spotify", "artist")
        assert cap.support_level == SupportLevel.experimental
        assert cap.best_entry_flow == EntryFlow.artist_preview

    def test_instagram_profile_cookie_required(self):
        cap = get_capability("instagram", "profile")
        assert cap.support_level == SupportLevel.cookie_required
        assert cap.requirements.cookie_required is True
        assert cap.requirements.login_required is True

    def test_twitter_profile_cookie_required(self):
        cap = get_capability("twitter", "profile")
        assert cap.support_level == SupportLevel.cookie_required

    def test_youtube_single_proxy_required(self):
        cap = get_capability("youtube", "single_video")
        assert cap.support_level == SupportLevel.proxy_required
        assert cap.requirements.proxy_required is True

    def test_soundcloud_artist_partial(self):
        cap = get_capability("soundcloud", "artist")
        assert cap.support_level == SupportLevel.partial

    def test_facebook_channel_partial(self):
        cap = get_capability("facebook", "channel")
        assert cap.support_level == SupportLevel.partial

    def test_unknown_platform_fallback(self):
        cap = get_capability("unknown", "unknown")
        assert cap.support_level in (SupportLevel.partial, SupportLevel.unsupported)

    def test_unknown_source_type_fallback(self):
        cap = get_capability("tiktok", "nonexistent_source")
        # Falls back gracefully — never raises
        assert isinstance(cap, CapabilityDescriptor)

    def test_get_platform_source_types_returns_list(self):
        caps = get_platform_source_types("spotify")
        assert len(caps) >= 4  # track, playlist, album, artist
        platforms = {c.platform for c in caps}
        assert platforms == {"spotify"}

    def test_platform_source_types_sorted_by_level(self):
        caps = get_platform_source_types("spotify")
        # full entries should come before experimental
        levels = [c.support_level.value for c in caps]
        assert levels.index("full") < levels.index("experimental")

    def test_full_matrix_not_empty(self):
        matrix = get_full_matrix()
        assert "tiktok" in matrix
        assert "spotify" in matrix
        assert len(matrix) >= 10

    def test_no_temporarily_disabled_in_current_registry(self):
        """PR1: no platform should be temporarily_disabled (YouTube is proxy_required, not disabled)."""
        matrix = get_full_matrix()
        for platform, caps in matrix.items():
            for cap in caps:
                assert cap.support_level != SupportLevel.temporarily_disabled, (
                    f"{platform}/{cap.source_type} is temporarily_disabled — "
                    "update registry or this assertion"
                )


# ── Minimal test client (avoids full app startup / Supabase realtime) ─────────

@pytest.fixture(scope="session")
def resolve_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.resolve_input import router
    mini = FastAPI()
    mini.include_router(router, prefix="/api/v1")
    return TestClient(mini)


# ═══════════════════════════════════════════════════════════════════
# D. BATCH MODE / MULTI-URL
# ═══════════════════════════════════════════════════════════════════

class TestBatchMode:
    """Test multi-URL resolve via lightweight FastAPI client."""

    def test_single_url_not_batch(self, resolve_client):
        r = resolve_client.post("/api/v1/resolve-input", json={"raw_input": "https://www.tiktok.com/@username"})
        assert r.status_code == 200
        data = r.json()
        assert data["batch_mode"] is False
        assert data["total"] == 1

    def test_multi_url_batch_mode(self, resolve_client):
        payload = {
            "raw_input": (
                "https://www.tiktok.com/@username\n"
                "https://open.spotify.com/playlist/abc123\n"
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            )
        }
        r = resolve_client.post("/api/v1/resolve-input", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["batch_mode"] is True
        assert data["total"] == 3
        assert len(data["items"]) == 3
        assert len(data["normalized_inputs"]) == 3

    def test_raw_inputs_list_takes_precedence(self, resolve_client):
        payload = {
            "raw_input": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "raw_inputs": [
                "https://www.tiktok.com/@user",
                "https://open.spotify.com/album/abc",
            ],
        }
        r = resolve_client.post("/api/v1/resolve-input", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2

    def test_blank_lines_stripped(self, resolve_client):
        payload = {"raw_input": "\n\nhttps://www.tiktok.com/@user\n\n\n"}
        r = resolve_client.post("/api/v1/resolve-input", json=payload)
        data = r.json()
        assert data["total"] == 1

    def test_comma_separated_urls(self, resolve_client):
        payload = {
            "raw_input": "https://www.tiktok.com/@a,https://www.tiktok.com/@b"
        }
        r = resolve_client.post("/api/v1/resolve-input", json=payload)
        data = r.json()
        assert data["total"] == 2

    def test_counters_correct(self, resolve_client):
        payload = {
            "raw_input": (
                "https://www.tiktok.com/@user\n"
                "https://www.instagram.com/user/\n"  # cookie_required
                "https://unknown.example.com/thing"   # partial fallback
            )
        }
        r = resolve_client.post("/api/v1/resolve-input", json=payload)
        data = r.json()
        assert data["cookie_required"] >= 1
        assert data["total"] == 3


# ═══════════════════════════════════════════════════════════════════
# E. ENDPOINT RESPONSE SHAPE
# ═══════════════════════════════════════════════════════════════════

class TestResolveEndpointShape:
    def test_spotify_artist_response_shape(self, resolve_client):
        r = resolve_client.post("/api/v1/resolve-input", json={
            "raw_input": "https://open.spotify.com/artist/06HL4z0CvFAxyc27GXpf02"
        })
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["platform"] == "spotify"
        assert item["source_type"] == "artist"

        cap = item["capability"]
        assert cap["support_level"] == "experimental"
        assert cap["best_entry_flow"] == "artist_preview"
        assert "preview_items" in cap["supported_actions"]

        routing = item["routing"]
        assert routing["is_container"] is True
        assert routing["surface_flow"] == "artist_preview"

    def test_tiktok_profile_routes_to_container(self, resolve_client):
        r = resolve_client.post("/api/v1/resolve-input", json={
            "raw_input": "https://www.tiktok.com/@user"
        })
        item = r.json()["items"][0]
        assert item["routing"]["is_container"] is True
        assert item["routing"]["recommended_endpoint"] == "/api/v1/discover-container"

    def test_tiktok_video_routes_to_single(self, resolve_client):
        r = resolve_client.post("/api/v1/resolve-input", json={
            "raw_input": "https://www.tiktok.com/@user/video/1234567890123456789"
        })
        item = r.json()["items"][0]
        assert item["routing"]["is_container"] is False
        assert item["routing"]["surface_flow"] == "single_download"

    def test_unknown_url_returns_gracefully(self, resolve_client):
        r = resolve_client.post("/api/v1/resolve-input", json={
            "raw_input": "https://totallyunknown.example.com/path"
        })
        assert r.status_code == 200
        item = r.json()["items"][0]
        assert item["platform"] == "unknown"

    def test_flat_compat_fields_present(self, resolve_client):
        r = resolve_client.post("/api/v1/resolve-input", json={
            "raw_input": "https://www.tiktok.com/@user"
        })
        item = r.json()["items"][0]
        assert "platform_label" in item
        assert "platform_emoji" in item
        assert "source_type_label" in item
        assert "support_level" in item
        assert "support_level_label" in item

    def test_platforms_capabilities_endpoint(self, resolve_client):
        r = resolve_client.get("/api/v1/platforms/capabilities")
        assert r.status_code == 200
        data = r.json()
        assert "platforms" in data
        assert data["total"] >= 10
        platforms = {p["platform"] for p in data["platforms"]}
        assert "tiktok" in platforms
        assert "spotify" in platforms
