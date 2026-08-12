"""
Phase 25 PR3 — Hardening Tests
================================
Covers:
  - Discovery snapshot recovery (snapshot gone, container data exists)
  - Expired when both snapshot and data are gone
  - Stale lock cleanup
  - Queue request idempotency (same request → same batch_id)
  - Dedupe summary structure (accepted_count / dropped_count / reasons)
  - Partial status renders without error
  - Refresh invalidation
  - Capability truthfulness (env on / off)
  - Maintenance task structure
  - New schema fields (terminal_reason, recovered_from_cache, processing_started_at)

All Redis calls are fully mocked — no real Redis needed.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# A.  Schema — new fields
# ─────────────────────────────────────────────────────────────────────────────

class TestPR3SchemaFields:
    def test_terminal_reason_optional(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        snap = DiscoveryJobSnapshot(
            job_id="j1", container_id="c1", platform="tiktok", source_type="profile",
            status=DiscoveryJobStatus.failed,
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        assert snap.terminal_reason is None

    def test_terminal_reason_set(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        snap = DiscoveryJobSnapshot(
            job_id="j1", container_id="c1", platform="instagram", source_type="profile",
            status=DiscoveryJobStatus.failed,
            terminal_reason="cookie_required",
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        assert snap.terminal_reason == "cookie_required"

    def test_recovered_from_cache_default_false(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        snap = DiscoveryJobSnapshot(
            job_id="j1", container_id="c1", platform="spotify", source_type="album",
            status=DiscoveryJobStatus.success,
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        assert snap.recovered_from_cache is False

    def test_processing_started_at_default_zero(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        snap = DiscoveryJobSnapshot(
            job_id="j1", container_id="c1", platform="soundcloud", source_type="artist",
            status=DiscoveryJobStatus.queued,
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        assert snap.processing_started_at == 0.0

    def test_roundtrip_new_fields(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        snap = DiscoveryJobSnapshot(
            job_id="j2", container_id="c2", platform="tiktok", source_type="profile",
            status=DiscoveryJobStatus.partial,
            terminal_reason="timeout",
            recovered_from_cache=False,
            processing_started_at=12345.0,
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        restored = DiscoveryJobSnapshot.model_validate_json(snap.model_dump_json())
        assert restored.terminal_reason == "timeout"
        assert restored.processing_started_at == 12345.0


# ─────────────────────────────────────────────────────────────────────────────
# B.  Cache — new helpers
# ─────────────────────────────────────────────────────────────────────────────

class _MockRedis:
    """In-memory Redis stub for cache tests."""
    def __init__(self):
        self._store: dict = {}
    def get(self, key):
        v = self._store.get(key)
        return v.encode() if isinstance(v, str) else v
    def set(self, key, value, ex=None, nx=False):
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True
    def setex(self, key, ttl, value):
        self._store[key] = value
        return True
    def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
    def keys(self, pattern="*"):
        import fnmatch
        return [k.encode() for k in self._store if fnmatch.fnmatch(k, pattern)]


def _make_mock_redis():
    r = _MockRedis()
    m = MagicMock()
    m.return_value = r
    return m, r


class TestCacheRecoveryHelpers:
    def test_save_and_get_job_meta(self):
        mock_factory, store = _make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import save_job_meta, get_job_meta
            save_job_meta("job_abc", "ctr_123", "https://tiktok.com/@user")
            result = get_job_meta("job_abc")
        assert result is not None
        assert result["container_id"] == "ctr_123"
        assert result["url"] == "https://tiktok.com/@user"

    def test_get_job_meta_returns_none_when_missing(self):
        mock_factory, _ = _make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import get_job_meta
            result = get_job_meta("nonexistent_job")
        assert result is None

    def test_check_and_set_queue_dedup(self):
        mock_factory, store = _make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import check_queue_dedup, set_queue_dedup
            req_hash = "abc123xyz"
            assert check_queue_dedup(req_hash) is None
            set_queue_dedup(req_hash, "batch-uuid-1")
            result = check_queue_dedup(req_hash)
        assert result == "batch-uuid-1"

    def test_cleanup_stale_lock_removes_done_job(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        mock_factory, store = _make_mock_redis()

        snap = DiscoveryJobSnapshot(
            job_id="job_done", container_id="c1", platform="spotify", source_type="album",
            status=DiscoveryJobStatus.success,
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        snap_json = snap.model_dump_json()

        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import (
                cleanup_stale_lock, lock_key, url_hash, job_key,
            )
            url = "https://open.spotify.com/album/abc"
            store._store[lock_key(url)] = "job_done"
            store._store[job_key("job_done")] = snap_json

            cleaned = cleanup_stale_lock(url)

        assert cleaned is True
        assert lock_key(url) not in store._store

    def test_cleanup_stale_lock_keeps_active_job(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        mock_factory, store = _make_mock_redis()

        snap = DiscoveryJobSnapshot(
            job_id="job_active", container_id="c1", platform="spotify", source_type="album",
            status=DiscoveryJobStatus.discovering,
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )

        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import cleanup_stale_lock, lock_key, job_key
            url = "https://open.spotify.com/album/abc"
            store._store[lock_key(url)] = "job_active"
            store._store[job_key("job_active")] = snap.model_dump_json()

            cleaned = cleanup_stale_lock(url)

        assert cleaned is False
        assert lock_key(url) in store._store

    def test_cleanup_stale_lock_missing_snapshot(self):
        """Lock present but snapshot expired → treat as stale."""
        mock_factory, store = _make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import cleanup_stale_lock, lock_key
            url = "https://open.spotify.com/album/abc"
            store._store[lock_key(url)] = "job_gone"
            # No snapshot in store
            cleaned = cleanup_stale_lock(url)
        assert cleaned is True

    def test_invalidate_all_for_container_removes_expand_keys(self):
        mock_factory, store = _make_mock_redis()
        container_id = "ctr_aabbcc"
        # Seed some expand keys
        store._store[f"container:expand:{container_id}:section1:0"] = "[]"
        store._store[f"container:expand:{container_id}:section2:0"] = "[]"
        store._store["container:expand:other_ctr:section1:0"] = "[]"

        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import invalidate_all_for_container
            invalidate_all_for_container("https://example.com", "spotify", container_id)

        # Only our container's expand keys should be gone
        assert f"container:expand:{container_id}:section1:0" not in store._store
        assert f"container:expand:{container_id}:section2:0" not in store._store
        # Other containers untouched
        assert "container:expand:other_ctr:section1:0" in store._store

    def test_save_and_get_batch_source_meta(self):
        mock_factory, store = _make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import save_batch_source_meta, get_batch_source_meta
            save_batch_source_meta("batch-1", "ctr_001", "tiktok", ["id1", "id2", "id3"], "videos")
            result = get_batch_source_meta("batch-1")
        assert result is not None
        assert result["source_container_id"] == "ctr_001"
        assert result["source_platform"] == "tiktok"
        assert result["source_section_ref"] == "videos"
        assert result["item_count"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# C.  API — recovery endpoint logic
# ─────────────────────────────────────────────────────────────────────────────

def _make_mini_app():
    """Create a minimal FastAPI app with only the container router."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    from app.api.container import router
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


class TestDiscoveryJobRecovery:
    def test_recovery_when_snapshot_missing_but_container_data_exists(self):
        from app.services.container_discovery import ContainerMeta

        cached_meta = ContainerMeta(
            container_id="ctr_recover",
            platform="tiktok",
            container_type="profile",
            url="https://tiktok.com/@user",
            title="Test User",
            status="ready",
        )

        with (
            patch("app.api.container.get_job", return_value=None),
            patch("app.api.container.get_job_meta", return_value={"container_id": "ctr_recover", "url": "https://tiktok.com/@user"}),
            patch("app.api.container.get_cached_container", return_value=cached_meta),
        ):
            client = _make_mini_app()
            res = client.get("/api/v1/discover-container/job_gone")

        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("success", "partial")
        assert data["recovered_from_cache"] is True
        assert data["container_id"] == "ctr_recover"
        assert data["from_cache"] is True

    def test_partial_recovery_when_container_status_partial(self):
        from app.services.container_discovery import ContainerMeta

        cached_meta = ContainerMeta(
            container_id="ctr_par",
            platform="spotify",
            container_type="artist",
            url="https://open.spotify.com/artist/abc",
            title="Artist X",
            status="partial",
        )

        with (
            patch("app.api.container.get_job", return_value=None),
            patch("app.api.container.get_job_meta", return_value={"container_id": "ctr_par", "url": "https://open.spotify.com/artist/abc"}),
            patch("app.api.container.get_cached_container", return_value=cached_meta),
        ):
            client = _make_mini_app()
            res = client.get("/api/v1/discover-container/job_partial")

        data = res.json()
        assert data["status"] == "partial"
        assert data["partial"] is True
        assert data["recovered_from_cache"] is True

    def test_expired_when_both_snapshot_and_data_gone(self):
        with (
            patch("app.api.container.get_job", return_value=None),
            patch("app.api.container.get_job_meta", return_value=None),
        ):
            client = _make_mini_app()
            res = client.get("/api/v1/discover-container/job_ghost")

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "expired"
        assert data["recovered_from_cache"] is False
        assert data["error"]["retryable"] is True
        assert data["terminal_reason"] == "expired"

    def test_expired_when_job_meta_exists_but_no_container_data(self):
        with (
            patch("app.api.container.get_job", return_value=None),
            patch("app.api.container.get_job_meta", return_value={"container_id": "ctr_old", "url": "https://tiktok.com/@old"}),
            patch("app.api.container.get_cached_container", return_value=None),
        ):
            client = _make_mini_app()
            res = client.get("/api/v1/discover-container/job_stale_meta")

        data = res.json()
        assert data["status"] == "expired"
        assert data["recovered_from_cache"] is False

    def test_live_snapshot_returned_normally(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        snap = DiscoveryJobSnapshot(
            job_id="job_live",
            container_id="ctr_live",
            platform="soundcloud",
            source_type="artist",
            status=DiscoveryJobStatus.success,
            progress_pct=100,
            created_at=0.0, updated_at=0.0, expires_at=99999.0,
        )

        with patch("app.api.container.get_job", return_value=snap):
            client = _make_mini_app()
            res = client.get("/api/v1/discover-container/job_live")

        data = res.json()
        assert data["status"] == "success"
        assert data["job_id"] == "job_live"
        # recovered_from_cache should be False (not a recovered job)
        assert data.get("recovered_from_cache", False) is False


# ─────────────────────────────────────────────────────────────────────────────
# D.  Queue idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueIdempotency:
    def test_queue_request_hash_stable(self):
        """Same request always produces same hash."""
        from app.api.container import _queue_request_hash, QueueRequest
        req = QueueRequest(item_ids=["id3", "id1", "id2"], queue_mode="selected", quality="mp3_320")
        h1 = _queue_request_hash("ctr_abc", req)
        h2 = _queue_request_hash("ctr_abc", req)
        assert h1 == h2

    def test_queue_request_hash_order_independent(self):
        """item_ids order should not affect hash."""
        from app.api.container import _queue_request_hash, QueueRequest
        req_a = QueueRequest(item_ids=["a", "b", "c"], queue_mode="selected", quality="mp3_320")
        req_b = QueueRequest(item_ids=["c", "a", "b"], queue_mode="selected", quality="mp3_320")
        assert _queue_request_hash("ctr_1", req_a) == _queue_request_hash("ctr_1", req_b)

    def test_queue_request_hash_differs_by_container(self):
        from app.api.container import _queue_request_hash, QueueRequest
        req = QueueRequest(item_ids=["id1"], queue_mode="selected", quality="mp3_320")
        h1 = _queue_request_hash("ctr_aaa", req)
        h2 = _queue_request_hash("ctr_bbb", req)
        assert h1 != h2

    def test_queue_request_hash_differs_by_quality(self):
        from app.api.container import _queue_request_hash, QueueRequest
        req_mp3 = QueueRequest(item_ids=["id1"], queue_mode="selected", quality="mp3_320")
        req_vid = QueueRequest(item_ids=["id1"], queue_mode="selected", quality="video_720")
        assert _queue_request_hash("ctr_x", req_mp3) != _queue_request_hash("ctr_x", req_vid)


# ─────────────────────────────────────────────────────────────────────────────
# E.  Dedupe summary structure
# ─────────────────────────────────────────────────────────────────────────────

class TestDedupeSummaryStructure:
    def _make_items(self, n, platform_prefix="sp"):
        from app.services.container_discovery import ContainerItem
        return [
            ContainerItem(id=f"{platform_prefix}:{i}", url=f"https://example.com/{i}",
                          title=f"Track {i}", author="Artist")
            for i in range(n)
        ]

    def test_dedupe_result_has_correct_counts(self):
        from app.services.container_dedupe import DedupeLayer
        items = self._make_items(5)
        dupes = items[:2]  # duplicate first 2
        all_items = items + dupes

        layer = DedupeLayer()
        result = layer.filter(all_items)
        assert len(result.kept) == 5
        assert result.removed_count == 2

    def test_dedupe_result_has_reasons_list(self):
        from app.services.container_dedupe import DedupeLayer
        from app.services.container_discovery import ContainerItem
        item = ContainerItem(id="sp:1", url="https://example.com/1", title="T", author="A")
        dupe = ContainerItem(id="sp:1", url="https://example.com/1", title="T", author="A")

        layer = DedupeLayer()
        result = layer.filter([item, dupe])
        assert isinstance(result.reasons_list, list)
        assert len(result.reasons_list) > 0

    def test_no_dedupe_when_all_unique(self):
        from app.services.container_dedupe import DedupeLayer
        items = self._make_items(3)
        layer = DedupeLayer()
        result = layer.filter(items)
        assert len(result.kept) == 3
        assert result.removed_count == 0
        assert result.reasons_list == []


# ─────────────────────────────────────────────────────────────────────────────
# F.  Capability truthfulness
# ─────────────────────────────────────────────────────────────────────────────

class TestCapabilityTruthfulness:
    def test_youtube_proxy_required_no_env(self):
        """YouTube should have can_preview=False when YTDL_PROXY not set.

        Updated for the Phase 26-A truthfulness-matrix redesign
        (app/api/container.py:_runtime_capability): YouTube's branch no
        longer has a `gating_reason` key at all — it uses
        `availability_reason` instead, with values
        "youtube_proxy_not_configured" / "proxy_configured_check_health"."""
        import os
        env = {k: v for k, v in os.environ.items() if k not in ("YTDL_PROXY", "RESIDENTIAL_PROXY_URL")}
        with patch.dict(os.environ, env, clear=True):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("youtube")
        assert cap["support_level"] == "proxy_required"
        assert cap["can_preview"] is False
        assert cap["can_queue"] is False
        assert cap["availability_reason"] == "youtube_proxy_not_configured"

    def test_youtube_proxy_required_with_env(self):
        """With YTDL_PROXY configured, container/channel preview opens up —
        but can_queue stays False regardless (Phase 26-A: "Queue is disabled
        until Oracle-IP block is resolved", a real infra constraint, not
        proxy-conditional — the original test's `can_queue is True` asserted
        a behavior this module explicitly documents as intentionally not
        the case)."""
        import os
        with patch.dict(os.environ, {"YTDL_PROXY": "http://proxy.example.com:8080"}):
            from importlib import reload
            import app.api.container as m
            cap = m._runtime_capability("youtube")
        assert cap["can_preview"] is True
        assert cap["can_queue"] is False
        assert cap["availability_reason"] == "proxy_configured_check_health"

    def test_instagram_cookie_required_no_env(self):
        import os
        env = {k: v for k, v in os.environ.items() if k != "INSTAGRAM_COOKIES_B64"}
        with patch.dict(os.environ, env, clear=True):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("instagram")
        assert cap["support_level"] == "cookie_required"
        assert cap["can_preview"] is False

    def test_instagram_cookie_required_with_env(self):
        """Phase 26-B: cookie-gated platforms now check live Redis
        cookie-pool state via app.services.cookie_capability.evaluate(),
        not just INSTAGRAM_COOKIES_B64's presence — mock the evaluator
        directly rather than relying on an env var the current code path
        may only use as one fallback signal among several."""
        with patch(
            "app.services.cookie_capability.evaluate",
            return_value={
                "can_discover_container": True, "can_expand": True,
                "can_queue_container_items": True, "can_single_download": True,
                "requires_cookie": True, "availability_reason": None,
                "cookie_state": "cookie_healthy",
            },
        ):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("instagram")
        assert cap["can_preview"] is True

    def test_tiktok_full_support(self):
        from app.api.container import _runtime_capability
        cap = _runtime_capability("tiktok")
        assert cap["support_level"] == "full"
        assert cap["can_preview"] is True
        assert cap["can_queue"] is True
        assert cap["gating_reason"] is None

    def test_spotify_full_support(self):
        from app.api.container import _runtime_capability
        cap = _runtime_capability("spotify")
        assert cap["support_level"] == "full"
        assert cap["can_queue"] is True


# ─────────────────────────────────────────────────────────────────────────────
# G.  Partial status compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestPartialStatusCompatibility:
    def test_partial_snapshot_is_terminal(self):
        """Frontend must stop polling on partial status."""
        from app.schemas.container_discovery import DiscoveryJobStatus
        TERMINAL = {"success", "failed", "expired", "partial"}
        assert DiscoveryJobStatus.partial in TERMINAL or DiscoveryJobStatus.partial.value in TERMINAL

    def test_partial_snapshot_has_warning(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        snap = DiscoveryJobSnapshot(
            job_id="j", container_id="c", platform="tiktok", source_type="profile",
            status=DiscoveryJobStatus.partial,
            partial=True,
            terminal_reason="timeout",
            warnings=["container_discovery_timeout"],
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        assert snap.partial is True
        assert "container_discovery_timeout" in snap.warnings
        assert snap.terminal_reason == "timeout"

    def test_partial_sections_accessible(self):
        from app.schemas.container_discovery import DiscoveryJobSnapshot, DiscoveryJobStatus
        sections = [{"key": "videos", "label": "Videos", "items": [{"id": "v1"}]}]
        snap = DiscoveryJobSnapshot(
            job_id="j", container_id="c", platform="facebook", source_type="media_tab",
            status=DiscoveryJobStatus.partial,
            sections=sections,
            created_at=0.0, updated_at=0.0, expires_at=0.0,
        )
        assert len(snap.sections) == 1


# ─────────────────────────────────────────────────────────────────────────────
# H.  Maintenance task structure
# ─────────────────────────────────────────────────────────────────────────────

class TestMaintenanceTask:
    def test_task_registered(self):
        from app.core.celery_app import celery_app
        from app.tasks.container_tasks import cleanup_stale_container_jobs
        assert cleanup_stale_container_jobs.name == "app.tasks.container_tasks.cleanup_stale_container_jobs"

    def test_task_runs_without_error_when_no_locks(self):
        """Maintenance should return clean stats when Redis has no container locks."""
        mock_redis = MagicMock()
        mock_redis.scan.return_value = (0, [])

        # get_redis is imported inside the function from app.core.redis_client
        with patch("app.core.redis_client.get_redis", return_value=mock_redis):
            from app.tasks.container_tasks import cleanup_stale_container_jobs
            result = cleanup_stale_container_jobs()

        assert result["checked"] == 0
        assert result["cleaned_locks"] == 0

    def test_maintenance_task_queue_is_light(self):
        from app.tasks.container_tasks import cleanup_stale_container_jobs
        # The task should run on the "light" queue, not "bulk" or "downloads"
        assert cleanup_stale_container_jobs.queue == "light"


# ─────────────────────────────────────────────────────────────────────────────
# I.  Refresh invalidation
# ─────────────────────────────────────────────────────────────────────────────

class TestRefreshInvalidation:
    def test_invalidate_all_removes_expand_cache(self):
        mock_factory, store = _make_mock_redis()
        container_id = "ctr_refresh"
        store._store[f"container:expand:{container_id}:section1:0"] = "[]"
        store._store[f"container:expand:{container_id}:section2:0"] = "[]"
        store._store[f"container:sections:{container_id}"] = "[]"

        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import invalidate_all_for_container
            invalidate_all_for_container("https://tiktok.com/@user", "tiktok", container_id)

        assert f"container:sections:{container_id}" not in store._store
        assert f"container:expand:{container_id}:section1:0" not in store._store

    def test_invalidate_all_does_not_touch_job_keys(self):
        mock_factory, store = _make_mock_redis()
        container_id = "ctr_r2"
        job_key = "container:job:job_running"
        lock_key = "container:lock:abc123"
        store._store[job_key] = "{}"
        store._store[lock_key] = "job_running"
        store._store[f"container:sections:{container_id}"] = "[]"

        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import invalidate_all_for_container
            invalidate_all_for_container("https://tiktok.com/@user", "tiktok", container_id)

        # Lock and job keys must NOT be touched by refresh invalidation
        assert job_key in store._store
        assert lock_key in store._store
