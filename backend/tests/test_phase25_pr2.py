"""
Phase 25 PR2 Tests — Container Discovery Framework
====================================================
Tests for the discovery job lifecycle, Redis cache contract,
Celery task structure, and expander interface.

Run with:
    cd backend && source venv/bin/activate
    python -m pytest tests/test_phase25_pr2.py -q

These tests use minimal mocking — they test schemas, cache helpers,
and expander interface without real Redis/Celery/network calls.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════
# A — Discovery Job Schema Tests
# ═══════════════════════════════════════════════════════════════════

class TestDiscoveryJobSchemas:

    def test_discovery_job_status_is_str_enum(self):
        from app.schemas.container_discovery import DiscoveryJobStatus
        assert DiscoveryJobStatus.queued == "queued"
        assert DiscoveryJobStatus.success == "success"
        assert DiscoveryJobStatus.failed == "failed"
        assert DiscoveryJobStatus.expired == "expired"
        assert DiscoveryJobStatus.partial == "partial"

    def test_discovery_stage_is_str_enum(self):
        from app.schemas.container_discovery import DiscoveryStage
        assert DiscoveryStage.normalize_input == "normalize_input"
        assert DiscoveryStage.finalize == "finalize"
        assert DiscoveryStage.expand_section == "expand_section"

    def test_stage_progress_map_has_all_stages(self):
        from app.schemas.container_discovery import DiscoveryStage, STAGE_PROGRESS
        for stage in DiscoveryStage:
            assert stage in STAGE_PROGRESS, f"Stage {stage} missing from STAGE_PROGRESS"
        # Progress must be 0-100
        for stage, pct in STAGE_PROGRESS.items():
            assert 0 <= pct <= 100, f"{stage}: progress {pct} out of range"

    def test_discovery_error_model(self):
        from app.schemas.container_discovery import DiscoveryError
        err = DiscoveryError(code="C001", message="Test error", retryable=True)
        assert err.code == "C001"
        assert err.retryable is True
        assert err.model_dump()["code"] == "C001"

    def test_discovery_stats_defaults(self):
        from app.schemas.container_discovery import DiscoveryStats
        stats = DiscoveryStats()
        assert stats.sections_ready == 0
        assert stats.items_estimated == 0
        assert stats.expandables == 0

    def test_discovery_job_snapshot_roundtrip(self):
        from app.schemas.container_discovery import (
            DiscoveryJobSnapshot, DiscoveryJobStatus, DiscoveryStage,
        )
        now = time.time()
        snap = DiscoveryJobSnapshot(
            job_id="job_abc123",
            container_id="ctr_xyz456",
            platform="spotify",
            source_type="artist",
            status=DiscoveryJobStatus.discovering,
            stage=DiscoveryStage.fetch_preview,
            progress_pct=60,
            message="Đang dựng danh sách…",
            created_at=now,
            updated_at=now,
            expires_at=now + 1800,
        )
        # JSON round-trip
        j = snap.model_dump_json()
        parsed = DiscoveryJobSnapshot.model_validate_json(j)
        assert parsed.job_id == "job_abc123"
        assert parsed.status == DiscoveryJobStatus.discovering
        assert parsed.progress_pct == 60

    def test_snapshot_progress_bounded(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus, DiscoveryStage
        import pytest
        with pytest.raises(Exception):
            DiscoveryJobSnapshot(
                job_id="j", container_id="c", platform="p", source_type="s",
                status=DiscoveryJobStatus.queued, stage=DiscoveryStage.normalize_input,
                progress_pct=150,   # invalid
                created_at=0.0, updated_at=0.0, expires_at=0.0,
            )

    def test_discover_container_request_effective_url(self):
        from app.schemas.container_discovery import DiscoverContainerRequest
        req1 = DiscoverContainerRequest(url="https://example.com/abc")
        assert req1.effective_url() == "https://example.com/abc"

        req2 = DiscoverContainerRequest(raw_input="https://example.com/def")
        assert req2.effective_url() == "https://example.com/def"

        req3 = DiscoverContainerRequest(resolved_item={"canonical_url": "https://x.com/g"})
        assert req3.effective_url() == "https://x.com/g"

        req4 = DiscoverContainerRequest()
        assert req4.effective_url() is None

    def test_expand_container_request_effective(self):
        from app.schemas.container_discovery import ExpandContainerRequest

        req = ExpandContainerRequest(section_id="albums", ref_id="sp_album:abc")
        assert req.effective_section() == "albums"
        assert req.effective_ref() == "sp_album:abc"

        # Legacy compat fields
        req_old = ExpandContainerRequest(section="top_tracks", child_id="")
        assert req_old.effective_section() == "top_tracks"

    def test_discover_container_response_model(self):
        from app.schemas.container_discovery import DiscoverContainerResponse, DiscoveryJobStatus
        resp = DiscoverContainerResponse(
            job_id="job_abc",
            container_id="ctr_xyz",
            status=DiscoveryJobStatus.queued,
            cached=False,
            platform="spotify",
            container_type="artist",
            eta_seconds=15,
        )
        assert resp.eta_seconds == 15
        assert resp.status == "queued"


# ═══════════════════════════════════════════════════════════════════
# B — Container Cache Tests (mocked Redis)
# ═══════════════════════════════════════════════════════════════════

class TestContainerCache:
    """Tests for container_cache.py Redis key helpers + lock logic."""

    def test_key_builders_format(self):
        from app.services.container_cache import (
            job_key, data_key, summary_key, sections_key,
            expand_key, lock_key, url_job_key, url_hash,
        )
        jk = job_key("job_abc123")
        assert jk == "container:job:job_abc123"

        dk = data_key("ctr_xyz")
        assert dk == "container:data:ctr_xyz"

        h = url_hash("https://open.spotify.com/artist/abc")
        assert len(h) == 24
        assert all(c in "0123456789abcdef" for c in h)

        sk = summary_key("spotify", "https://open.spotify.com/artist/abc")
        assert sk.startswith("container:summary:spotify:")

        ek = expand_key("ctr_xyz", "albums:abc123", "page2")
        assert ek.startswith("container:expand:ctr_xyz:")

        lk = lock_key("https://open.spotify.com/artist/abc")
        assert lk.startswith("container:lock:")

        ujk = url_job_key("https://example.com")
        assert ujk.startswith("container:url_job:")

    def test_key_same_url_same_hash(self):
        from app.services.container_cache import url_hash
        h1 = url_hash("https://open.spotify.com/artist/abc")
        h2 = url_hash("https://open.spotify.com/artist/abc")
        assert h1 == h2

    def test_key_different_url_different_hash(self):
        from app.services.container_cache import url_hash
        h1 = url_hash("https://open.spotify.com/artist/abc")
        h2 = url_hash("https://open.spotify.com/artist/xyz")
        assert h1 != h2

    def test_expand_key_safe_chars(self):
        from app.services.container_cache import expand_key
        key = expand_key("ctr_abc", "sp_album:abc/123:extra", "cursor?=1&2")
        # Should not contain characters that break Redis keys
        assert ":" not in key.replace("container:expand:ctr_abc:", "", 1).split(":")[0]

    @patch("app.services.container_cache.get_redis")
    def test_save_and_get_job(self, mock_get_redis):
        from app.schemas.container_discovery import (
            DiscoveryJobSnapshot, DiscoveryJobStatus, DiscoveryStage,
        )
        from app.services.container_cache import save_job, get_job

        store: dict = {}
        redis_mock = MagicMock()
        redis_mock.setex.side_effect = lambda k, ttl, v: store.update({k: v})
        redis_mock.get.side_effect = lambda k: store.get(k)
        mock_get_redis.return_value = redis_mock

        now = time.time()
        snap = DiscoveryJobSnapshot(
            job_id="job_test01",
            container_id="ctr_test01",
            platform="tiktok",
            source_type="profile",
            status=DiscoveryJobStatus.discovering,
            stage=DiscoveryStage.fetch_preview,
            progress_pct=60,
            message="test",
            created_at=now,
            updated_at=now,
            expires_at=now + 1800,
        )
        save_job(snap)

        loaded = get_job("job_test01")
        assert loaded is not None
        assert loaded.job_id == "job_test01"
        assert loaded.platform == "tiktok"
        assert loaded.progress_pct == 60

    @patch("app.services.container_cache.get_redis")
    def test_get_job_missing_returns_none(self, mock_get_redis):
        from app.services.container_cache import get_job
        redis_mock = MagicMock()
        redis_mock.get.return_value = None
        mock_get_redis.return_value = redis_mock
        assert get_job("nonexistent_job") is None

    @patch("app.services.container_cache.get_redis")
    def test_patch_job_updates_fields(self, mock_get_redis):
        from app.schemas.container_discovery import (
            DiscoveryJobSnapshot, DiscoveryJobStatus, DiscoveryStage,
        )
        from app.services.container_cache import save_job, patch_job

        store: dict = {}
        redis_mock = MagicMock()
        redis_mock.setex.side_effect = lambda k, ttl, v: store.update({k: v})
        redis_mock.get.side_effect = lambda k: store.get(k)
        mock_get_redis.return_value = redis_mock

        now = time.time()
        snap = DiscoveryJobSnapshot(
            job_id="job_patch",
            container_id="ctr_patch",
            platform="spotify",
            source_type="album",
            status=DiscoveryJobStatus.resolving,
            stage=DiscoveryStage.resolve_capability,
            progress_pct=10,
            message="initial",
            created_at=now, updated_at=now, expires_at=now + 1800,
        )
        save_job(snap)

        updated = patch_job("job_patch", status=DiscoveryJobStatus.success, progress_pct=100)
        assert updated is not None
        assert updated.status == DiscoveryJobStatus.success
        assert updated.progress_pct == 100

    @patch("app.services.container_cache.get_redis")
    def test_acquire_lock_nx_semantics(self, mock_get_redis):
        from app.services.container_cache import acquire_discovery_lock

        redis_mock = MagicMock()
        # First call: lock acquired (returns truthy)
        redis_mock.set.side_effect = [True, None]
        mock_get_redis.return_value = redis_mock

        url = "https://open.spotify.com/artist/abc"
        assert acquire_discovery_lock(url, "job_1") is True
        assert acquire_discovery_lock(url, "job_2") is False

    @patch("app.services.container_cache.get_redis")
    def test_cache_graceful_on_redis_error(self, mock_get_redis):
        from app.services.container_cache import get_job
        # Redis throws — should return None, not raise
        mock_get_redis.side_effect = Exception("Redis down")
        result = get_job("any_job")
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# C — Expander Interface Tests
# ═══════════════════════════════════════════════════════════════════

class TestExpanderInterface:
    """Tests that all expanders satisfy the BaseExpander interface."""

    def test_base_expander_has_expand_section(self):
        from app.services.container_expanders import BaseExpander
        assert hasattr(BaseExpander, "expand_section")
        # Calling without override should raise NotImplementedError
        expander = BaseExpander()
        with pytest.raises(NotImplementedError):
            expander.expand_section("https://example.com", "section")

    def test_all_registered_expanders_have_expand_section(self):
        """Every expander returned by get_expander() must implement expand_section."""
        from app.services.container_expanders import _get_map
        expander_map = _get_map()
        for platform, expander in expander_map.items():
            assert hasattr(expander, "expand_section"), \
                f"{platform} expander missing expand_section()"

    def test_all_registered_expanders_have_platform(self):
        from app.services.container_expanders import _get_map
        expander_map = _get_map()
        for platform, expander in expander_map.items():
            assert expander.platform, f"{platform}: expander.platform is empty"

    def test_registry_has_pr2_platforms(self):
        from app.services.container_expanders import _get_map
        expander_map = _get_map()
        pr2_platforms = {"instagram", "twitter", "reddit", "youtube"}
        for p in pr2_platforms:
            assert p in expander_map, f"Missing PR2 expander: {p}"

    def test_get_expander_returns_none_for_unknown(self):
        from app.services.container_expanders import get_expander
        assert get_expander("nonexistent_platform") is None

    def test_instagram_expander_no_cookie_returns_failed(self):
        from app.services.container_expanders.instagram_expander import InstagramExpander
        expander = InstagramExpander()
        with patch.object(expander, "_has_cookies", return_value=False):
            result = expander.discover("https://www.instagram.com/username/")
        assert result.status == "failed"
        assert result.error_code == "C010"
        assert "cookie" in result.error_message.lower()

    def test_twitter_expander_no_cookie_returns_failed(self):
        from app.services.container_expanders.twitter_expander import TwitterExpander
        expander = TwitterExpander()
        with patch.object(expander, "_has_cookies", return_value=False):
            result = expander.discover("https://x.com/username")
        assert result.status == "failed"
        assert result.error_code == "C010"

    def test_youtube_expander_no_proxy_returns_failed(self):
        from app.services.container_expanders.youtube_expander import YouTubeExpander
        expander = YouTubeExpander()
        with patch.object(expander, "_get_proxy", return_value=None):
            result = expander.discover("https://www.youtube.com/@channel")
        assert result.status == "failed"
        assert result.error_code == "C020"
        assert "proxy" in result.error_message.lower()

    def test_soundcloud_expander_has_expand_section(self):
        from app.services.container_expanders.soundcloud_expander import SoundCloudExpander
        expander = SoundCloudExpander()
        assert hasattr(expander, "expand_section")
        # expand_section exists and calls _scrape_flat
        with patch.object(expander, "_scrape_flat", return_value=[]) as mock_scrape:
            result = expander.expand_section("https://soundcloud.com/artist", "uploads")
            mock_scrape.assert_called_once_with("https://soundcloud.com/artist", 500)
            assert result == []

    def test_reddit_expander_extracts_subreddit_name(self):
        from app.services.container_expanders.reddit_expander import RedditExpander
        expander = RedditExpander()
        name = expander._extract_name("https://reddit.com/r/programming/", "subreddit")
        assert name == "r/programming"

    def test_reddit_expander_has_expand_section(self):
        from app.services.container_expanders.reddit_expander import RedditExpander
        expander = RedditExpander()
        with patch.object(expander, "_scrape_flat", return_value=([], "")) as mock_sc:
            result = expander.expand_section("https://reddit.com/r/test/", "videos")
            mock_sc.assert_called_once()
            assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════════
# D — Celery Task Structure Tests
# ═══════════════════════════════════════════════════════════════════

class TestContainerTasksStructure:
    """Tests that container_tasks.py has the right task signatures."""

    def test_discover_task_is_registered(self):
        from app.tasks.container_tasks import discover_container_task
        assert callable(discover_container_task)
        # Celery task should have .apply_async
        assert hasattr(discover_container_task, "apply_async")

    def test_expand_task_is_registered(self):
        from app.tasks.container_tasks import expand_container_section_task
        assert callable(expand_container_section_task)
        assert hasattr(expand_container_section_task, "apply_async")

    def test_discover_task_name(self):
        from app.tasks.container_tasks import discover_container_task
        assert discover_container_task.name == "app.tasks.container_tasks.discover_container_task"

    def test_expand_task_name(self):
        from app.tasks.container_tasks import expand_container_section_task
        assert expand_container_section_task.name == "app.tasks.container_tasks.expand_container_section_task"

    def test_legacy_task_still_exists(self):
        """Legacy discover_container_task in video_tasks.py must not be removed."""
        from app.tasks.video_tasks import discover_container_task as legacy_task
        assert callable(legacy_task)


# ═══════════════════════════════════════════════════════════════════
# E — API Endpoint Tests (minimal FastAPI test client)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="session")
def container_client():
    """Mini FastAPI app with only the container router — avoids Supabase import."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.container import router
    mini = FastAPI()
    mini.include_router(router, prefix="/api/v1")
    return TestClient(mini)


class TestDiscoverEndpointShape:

    def test_discover_rejects_non_container_url(self, container_client):
        """Single video URL should be rejected with 400."""
        # normalize/classify are imported inside the function body → patch at source
        with patch("app.core.url_normalizer.normalize") as mock_norm, \
             patch("app.core.source_classifier.classify") as mock_clf:
            mock_norm.return_value = MagicMock(canonical_url="https://www.tiktok.com/@user/video/123")
            mock_clf.return_value = MagicMock(source_type="single_video", platform="tiktok")
            resp = container_client.post("/api/v1/discover-container", json={"url": "https://www.tiktok.com/@user/video/123"})
        assert resp.status_code == 400

    def test_discover_returns_job_id(self, container_client):
        """Valid container URL should return job_id in response."""
        with patch("app.core.url_normalizer.normalize") as mock_norm, \
             patch("app.core.source_classifier.classify") as mock_clf, \
             patch("app.api.container.get_cached_container", return_value=None), \
             patch("app.api.container.get_active_job_for_url", return_value=None), \
             patch("app.api.container.acquire_discovery_lock", return_value=True), \
             patch("app.api.container.cache_container"), \
             patch("app.api.container.save_job"), \
             patch("app.api.container.set_active_job_for_url"), \
             patch("app.api.container.track_container_metric"), \
             patch("app.tasks.container_tasks.discover_container_task") as mock_task:
            mock_norm.return_value = MagicMock(canonical_url="https://www.tiktok.com/@user")
            mock_clf.return_value = MagicMock(source_type="profile", platform="tiktok")
            mock_task.apply_async = MagicMock()
            resp = container_client.post("/api/v1/discover-container", json={"url": "https://www.tiktok.com/@user"})
        assert resp.status_code == 200
        data = resp.json()
        assert "job_id" in data
        assert "container_id" in data
        assert "poll_url" in data

    def test_poll_job_missing_returns_expired(self, container_client):
        """GET /discover-container/{job_id} with unknown job_id returns expired."""
        # Also mock get_job_meta (the PR3 recovery-path fallback) — with only
        # get_job mocked, this test passes in isolation but can flake in a
        # full suite run: an unmocked get_job_meta() can return a truthy dict
        # left over from other in-memory job-meta state without a "url" key,
        # and the endpoint does meta_info["url"] unguarded, turning this into
        # an unhandled 500 instead of the expected clean {"status": "expired"}.
        with patch("app.api.container.get_job", return_value=None), \
             patch("app.api.container.get_job_meta", return_value=None):
            resp = container_client.get("/api/v1/discover-container/nonexistent_job_id")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "expired"
        assert data["job_id"] == "nonexistent_job_id"

    def test_poll_job_returns_snapshot(self, container_client):
        """GET /discover-container/{job_id} returns DiscoveryJobSnapshot fields."""
        from app.schemas.container_discovery import (
            DiscoveryJobSnapshot, DiscoveryJobStatus, DiscoveryStage,
        )
        now = time.time()
        mock_snap = DiscoveryJobSnapshot(
            job_id="job_abc",
            container_id="ctr_xyz",
            platform="spotify",
            source_type="artist",
            status=DiscoveryJobStatus.discovering,
            stage=DiscoveryStage.fetch_preview,
            progress_pct=60,
            message="Đang khám phá…",
            created_at=now, updated_at=now, expires_at=now + 1800,
        )
        with patch("app.api.container.get_job", return_value=mock_snap):
            resp = container_client.get("/api/v1/discover-container/job_abc")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "discovering"
        assert data["progress_pct"] == 60
        assert data["platform"] == "spotify"
        assert "message" in data
        assert "stats" in data


# ═══════════════════════════════════════════════════════════════════
# F — Backward Compatibility Tests
# ═══════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """Ensure PR2 additions don't break PR1 contracts."""

    def test_container_py_schemas_import_unchanged(self):
        """PR1 schema imports must still work."""
        from app.schemas.container import (
            Platform, SourceType, SupportLevel, CapabilityDescriptor,
            ResolveInputItem, ResolveInputResult, is_container_source_type,
        )
        assert is_container_source_type("playlist") is True
        assert is_container_source_type("single_video") is False

    def test_container_registry_unchanged(self):
        """PR1 registry lookups must still work."""
        from app.services.container_registry import get_capability
        cap = get_capability("spotify", "playlist")
        assert cap is not None
        assert cap.platform == "spotify"

    def test_container_discovery_unchanged(self):
        """PR1 ContainerMeta + cache ops must still work."""
        from app.services.container_discovery import (
            ContainerMeta, make_container_id, container_cache_key,
        )
        cid = make_container_id()
        assert cid.startswith("ctr_")
        meta = ContainerMeta(
            container_id=cid, platform="spotify",
            container_type="playlist", url="https://open.spotify.com/playlist/abc",
            title="Test Playlist",
        )
        assert meta.status == "pending"

    def test_container_dedupe_unchanged(self):
        """PR1 DedupeLayer must still work."""
        from app.services.container_dedupe import DedupeLayer
        from app.services.container_discovery import ContainerItem
        layer = DedupeLayer()
        items = [
            ContainerItem(id="sp:abc", url="https://open.spotify.com/track/abc", title="Song 1", author="Artist"),
            ContainerItem(id="sp:abc", url="https://open.spotify.com/track/abc", title="Song 1", author="Artist"),
        ]
        result = layer.filter(items)
        assert len(result.kept) == 1
        assert result.removed_count == 1

    def test_pr2_schemas_dont_conflict_with_pr1(self):
        """PR2 discovery schemas and PR1 container schemas can both be imported."""
        from app.schemas.container import Platform, SourceType
        from app.schemas.container_discovery import DiscoveryJobStatus, DiscoveryStage
        # No conflict, both enum families coexist
        assert Platform.spotify == "spotify"
        assert DiscoveryJobStatus.success == "success"

    def test_container_cache_key_namespace_separate(self):
        """PR2 container:* keys don't clash with PR1 vidgrab:container:* keys."""
        from app.services.container_cache import job_key, lock_key
        from app.services.container_discovery import container_cache_key
        pr2_key = job_key("job_abc")
        pr1_key = container_cache_key("https://example.com")
        assert not pr2_key.startswith("vidgrab:")
        assert pr1_key.startswith("vidgrab:")
        assert pr2_key.startswith("container:")
