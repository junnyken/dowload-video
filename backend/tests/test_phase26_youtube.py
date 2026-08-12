"""
Phase 26-A — YouTube Proxy-Safe Container Discovery Tests
==========================================================
Covers the 8 test groups from the PR26-A spec:
  1. URL classification (playlist / channel / single / shorts)
  2. Proxy health probe (no_proxy / unhealthy / healthy / cache / force)
  3. Capability matrix (3 env states × 3 fields)
  4. discover-container preflight (5 scenarios)
  5. expand proxy re-check (3 scenarios)
  6. queue policy block (3 scenarios)
  7. YouTubeExpander unit (4 scenarios)
  8. resolve-input endpoint (5 scenarios)

All network calls are mocked — no real HTTP traffic.

Mocking notes:
  - async functions imported *inside* function bodies: patch the source module
    e.g. "app.services.youtube_proxy_health.get_youtube_proxy_health_async"
  - functions imported at module level in container.py: patch the binding
    e.g. "app.api.container.get_url_for_container_id"
  - use AsyncMock for any function that is called with `await`
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch


def _make_health(state, error=""):
    from app.services.youtube_proxy_health import ProxyHealth
    return ProxyHealth(
        state=state,
        proxy_configured=(state != "no_proxy"),
        checked_at=time.time(),
        error=error,
    )


def _classify_result(platform, source_type, nid="test"):
    from app.core.source_classifier import ClassifyResult
    return ClassifyResult(
        platform=platform, source_type=source_type,
        normalized_id=nid, confidence=1.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 1. URL Classification
# ══════════════════════════════════════════════════════════════════════════════

class TestYouTubeURLClassification:
    def _classify(self, url):
        from app.core.source_classifier import classify
        return classify(url)

    def test_playlist_url(self):
        r = self._classify("https://www.youtube.com/playlist?list=PLtest123")
        assert r.platform == "youtube"
        assert r.source_type == "playlist"
        assert r.normalized_id == "PLtest123"

    def test_channel_at_handle(self):
        r = self._classify("https://www.youtube.com/@MrBeast")
        assert r.platform == "youtube"
        assert r.source_type == "channel"
        assert r.normalized_id == "MrBeast"

    def test_channel_slash_c(self):
        r = self._classify("https://www.youtube.com/c/SomeChannel")
        assert r.platform == "youtube"
        assert r.source_type == "channel"

    def test_single_video(self):
        r = self._classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert r.platform == "youtube"
        assert r.source_type == "single_video"
        assert r.normalized_id == "dQw4w9WgXcQ"

    def test_shorts_classified_as_single_video(self):
        r = self._classify("https://www.youtube.com/shorts/abcdefghijk")
        assert r.platform == "youtube"
        assert r.source_type == "single_video"

    def test_channel_id_style(self):
        r = self._classify("https://www.youtube.com/channel/UCxxxxxx")
        assert r.platform == "youtube"
        assert r.source_type == "channel"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Proxy Health Probe
# ══════════════════════════════════════════════════════════════════════════════

class TestYouTubeProxyHealth:
    def test_no_proxy_env_returns_no_proxy(self):
        import os
        os.environ.pop("YTDL_PROXY", None)
        os.environ.pop("RESIDENTIAL_PROXY_URL", None)
        from app.services.youtube_proxy_health import get_youtube_proxy_health
        health = get_youtube_proxy_health(force=True)
        assert health.state == "no_proxy"
        assert not health.proxy_configured
        assert not health.is_usable()

    def test_unhealthy_when_probe_fails(self):
        with patch.dict("os.environ", {"YTDL_PROXY": "http://user:pass@bad-host:8080"}):
            with patch("app.services.youtube_proxy_health._from_cache", return_value=None):
                with patch("app.services.youtube_proxy_health._to_cache"):
                    with patch("app.services.youtube_proxy_health._run_probe",
                               return_value=_make_health("unhealthy", "Connection refused")):
                        from app.services.youtube_proxy_health import get_youtube_proxy_health
                        health = get_youtube_proxy_health(force=True)
                        assert health.state == "unhealthy"
                        assert not health.is_usable()

    def test_healthy_when_probe_succeeds(self):
        with patch.dict("os.environ", {"YTDL_PROXY": "http://user:pass@proxy:8080"}):
            with patch("app.services.youtube_proxy_health._from_cache", return_value=None):
                with patch("app.services.youtube_proxy_health._to_cache"):
                    with patch("app.services.youtube_proxy_health._run_probe",
                               return_value=_make_health("healthy")):
                        from app.services.youtube_proxy_health import get_youtube_proxy_health
                        health = get_youtube_proxy_health(force=True)
                        assert health.state == "healthy"
                        assert health.is_usable()

    def test_cache_hit_skips_probe(self):
        cached = _make_health("healthy")
        with patch.dict("os.environ", {"YTDL_PROXY": "http://proxy:8080"}):
            with patch("app.services.youtube_proxy_health._from_cache", return_value=cached):
                with patch("app.services.youtube_proxy_health._run_probe") as mock_probe:
                    from app.services.youtube_proxy_health import get_youtube_proxy_health
                    result = get_youtube_proxy_health()
                    mock_probe.assert_not_called()
                    assert result.state == "healthy"

    def test_force_bypasses_cache(self):
        cached = _make_health("healthy")
        new_result = _make_health("unhealthy", "timeout")
        with patch.dict("os.environ", {"YTDL_PROXY": "http://proxy:8080"}):
            with patch("app.services.youtube_proxy_health._from_cache", return_value=cached):
                with patch("app.services.youtube_proxy_health._run_probe", return_value=new_result):
                    with patch("app.services.youtube_proxy_health._to_cache"):
                        from app.services.youtube_proxy_health import get_youtube_proxy_health
                        result = get_youtube_proxy_health(force=True)
                        assert result.state == "unhealthy"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Capability Matrix
# ══════════════════════════════════════════════════════════════════════════════

class TestYouTubeCapabilityMatrix:
    def test_no_proxy_cannot_discover(self):
        import os
        os.environ.pop("YTDL_PROXY", None)
        os.environ.pop("RESIDENTIAL_PROXY_URL", None)
        from app.api.container import _runtime_capability
        cap = _runtime_capability("youtube")
        assert cap["can_discover_container"] is False
        assert cap["proxy_configured"] is False

    def test_proxy_configured_can_discover(self):
        with patch.dict("os.environ", {"YTDL_PROXY": "http://proxy:8080"}):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("youtube")
            assert cap["can_discover_container"] is True
            assert cap["proxy_configured"] is True

    def test_single_download_always_false(self):
        with patch.dict("os.environ", {"YTDL_PROXY": "http://proxy:8080"}):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("youtube")
            assert cap["can_single_download"] is False

    def test_queue_always_false_pr26a(self):
        with patch.dict("os.environ", {"YTDL_PROXY": "http://proxy:8080"}):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("youtube")
            assert cap["can_queue_container_items"] is False
            assert cap["can_queue"] is False

    def test_requires_proxy_always_true(self):
        import os
        os.environ.pop("YTDL_PROXY", None)
        from app.api.container import _runtime_capability
        cap = _runtime_capability("youtube")
        assert cap["requires_proxy"] is True

    def test_availability_reason_set(self):
        import os
        os.environ.pop("YTDL_PROXY", None)
        from app.api.container import _runtime_capability
        cap = _runtime_capability("youtube")
        assert "proxy" in cap["availability_reason"].lower()


# ══════════════════════════════════════════════════════════════════════════════
# 4. discover-container YouTube Preflight
# ══════════════════════════════════════════════════════════════════════════════

class TestDiscoverContainerYouTubePreflight:
    """
    Tests the preflight gate in POST /discover-container for YouTube URLs.

    get_youtube_proxy_health_async is imported INSIDE discover_container's body,
    so we patch the source module, not app.api.container.
    """

    def _norm_mock(self, url):
        m = MagicMock()
        m.canonical_url = url
        return m

    def test_single_video_always_503(self):
        import asyncio
        from fastapi import HTTPException
        import pytest

        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "single_video")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://youtube.com/watch?v=x")):
                from app.api.container import discover_container, DiscoverRequest
                req = DiscoverRequest(url="https://youtube.com/watch?v=dQw4w9WgXcQ")
                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(discover_container(req))
                assert exc_info.value.status_code == 503
                assert exc_info.value.detail["code"] == "youtube_single_temporarily_disabled"

    def test_no_proxy_returns_proxy_required(self):
        import asyncio
        from fastapi import HTTPException
        import pytest

        health_mock = AsyncMock(return_value=_make_health("no_proxy"))
        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "channel")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://youtube.com/@TestChan")):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    from app.api.container import discover_container, DiscoverRequest
                    req = DiscoverRequest(url="https://youtube.com/@TestChan")
                    with pytest.raises(HTTPException) as exc_info:
                        asyncio.run(discover_container(req))
                    assert exc_info.value.status_code == 503
                    assert exc_info.value.detail["code"] == "youtube_proxy_required"

    def test_unhealthy_proxy_returns_proxy_unhealthy(self):
        import asyncio
        from fastapi import HTTPException
        import pytest

        health_mock = AsyncMock(return_value=_make_health("unhealthy", "timeout"))
        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "channel")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://youtube.com/@TestChan")):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    from app.api.container import discover_container, DiscoverRequest
                    req = DiscoverRequest(url="https://youtube.com/@TestChan")
                    with pytest.raises(HTTPException) as exc_info:
                        asyncio.run(discover_container(req))
                    assert exc_info.value.status_code == 503
                    assert exc_info.value.detail["code"] == "youtube_proxy_unhealthy"

    def test_healthy_proxy_channel_passes_preflight(self):
        """Healthy proxy + channel must pass preflight and reach cache-check stage."""
        import asyncio

        health_mock = AsyncMock(return_value=_make_health("healthy"))
        task_mock = MagicMock()
        task_mock.apply_async = MagicMock()

        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "channel", "TestChan")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://youtube.com/@TestChan")):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    with patch("app.api.container.get_cached_container", return_value=None):
                        with patch("app.api.container.get_active_job_for_url", return_value=None):
                            with patch("app.api.container.acquire_discovery_lock"):
                                with patch("app.api.container.cache_container"):
                                    with patch("app.api.container.save_job"):
                                        with patch("app.api.container.save_job_meta"):
                                            with patch("app.api.container.set_active_job_for_url"):
                                                with patch("app.api.container.track_container_metric"):
                                                    with patch("app.tasks.container_tasks.discover_container_task",
                                                               task_mock):
                                                        from app.api.container import discover_container, DiscoverRequest
                                                        req = DiscoverRequest(url="https://youtube.com/@TestChan")
                                                        result = asyncio.run(discover_container(req))
                                                        assert result["platform"] == "youtube"
                                                        assert result["status"] == "queued"

    def test_healthy_proxy_playlist_passes_preflight(self):
        """Healthy proxy + playlist source_type must also pass."""
        import asyncio

        health_mock = AsyncMock(return_value=_make_health("healthy"))
        task_mock = MagicMock()
        task_mock.apply_async = MagicMock()

        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "playlist", "PL123")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://youtube.com/playlist?list=PL123")):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    with patch("app.api.container.get_cached_container", return_value=None):
                        with patch("app.api.container.get_active_job_for_url", return_value=None):
                            with patch("app.api.container.acquire_discovery_lock"):
                                with patch("app.api.container.cache_container"):
                                    with patch("app.api.container.save_job"):
                                        with patch("app.api.container.save_job_meta"):
                                            with patch("app.api.container.set_active_job_for_url"):
                                                with patch("app.api.container.track_container_metric"):
                                                    with patch("app.tasks.container_tasks.discover_container_task",
                                                               task_mock):
                                                        from app.api.container import discover_container, DiscoverRequest
                                                        req = DiscoverRequest(url="https://youtube.com/playlist?list=PL123")
                                                        result = asyncio.run(discover_container(req))
                                                        assert result["container_type"] == "playlist"


# ══════════════════════════════════════════════════════════════════════════════
# 5. Expand Proxy Re-check
# ══════════════════════════════════════════════════════════════════════════════

class TestYouTubeExpandGating:
    """
    expand_section must re-check proxy health for YouTube containers.

    _get_container_or_404 uses get_url_for_container_id / get_cached_container
    which are imported at module level → patch as app.api.container.*
    """

    def _make_meta(self):
        from app.services.container_discovery import ContainerMeta, ContainerSection
        return ContainerMeta(
            container_id="cid123",
            platform="youtube",
            container_type="channel",
            url="https://youtube.com/@Test",
            title="Test Channel",
            status="ready",
            sections=[
                ContainerSection(
                    key="videos", label="Videos",
                    item_count=10, items_loaded=False,
                )
            ],
        )

    def test_expand_blocked_no_proxy(self):
        import asyncio
        from fastapi import HTTPException
        import pytest

        health_mock = AsyncMock(return_value=_make_health("no_proxy"))
        meta = self._make_meta()
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://youtube.com/@Test"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    from app.api.container import expand_section, ExpandRequest
                    req = ExpandRequest(section="videos")
                    with pytest.raises(HTTPException) as exc_info:
                        asyncio.run(expand_section("cid123", req))
                    assert exc_info.value.status_code == 503
                    assert exc_info.value.detail["code"] == "youtube_proxy_required"

    def test_expand_blocked_unhealthy_proxy(self):
        import asyncio
        from fastapi import HTTPException
        import pytest

        health_mock = AsyncMock(return_value=_make_health("unhealthy", "probe failed"))
        meta = self._make_meta()
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://youtube.com/@Test"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    from app.api.container import expand_section, ExpandRequest
                    req = ExpandRequest(section="videos")
                    with pytest.raises(HTTPException) as exc_info:
                        asyncio.run(expand_section("cid123", req))
                    assert exc_info.value.status_code == 503
                    assert exc_info.value.detail["code"] == "youtube_proxy_unhealthy"

    def test_expand_proceeds_healthy_proxy(self):
        """Healthy proxy must pass gate and reach the expander."""
        import asyncio

        health_mock = AsyncMock(return_value=_make_health("healthy"))
        meta = self._make_meta()
        fake_expander = MagicMock()
        fake_expander.expand_section = MagicMock(return_value=[])

        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://youtube.com/@Test"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    # get_expander is imported inside expand_section body → patch source module
                    with patch("app.services.container_expanders.get_expander",
                               return_value=fake_expander):
                        with patch("app.api.container.track_container_metric"):
                            with patch("app.api.container.cache_container"):
                                from app.api.container import expand_section, ExpandRequest
                                req = ExpandRequest(section="videos")
                                asyncio.run(expand_section("cid123", req))
                                fake_expander.expand_section.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 6. Queue Policy Block
# ══════════════════════════════════════════════════════════════════════════════

class TestYouTubeQueueBlocking:
    """POST /container/{id}/queue must reject YouTube containers."""

    def _make_meta(self, status="ready"):
        from app.services.container_discovery import ContainerMeta, ContainerSection, ContainerItem
        return ContainerMeta(
            container_id="ytcid",
            platform="youtube",
            container_type="channel",
            url="https://youtube.com/@Test",
            title="Test",
            status=status,
            sections=[
                ContainerSection(
                    key="videos", label="Videos",
                    item_count=5, items_loaded=True,
                    items=[
                        ContainerItem(
                            id=f"yt:vid{i}",
                            url=f"https://yt.com/watch?v=vid{i}",
                            title=f"Video {i}",
                            author="", thumbnail="",
                            duration_ms=0, media_type="video",
                        )
                        for i in range(5)
                    ],
                )
            ],
        )

    def test_queue_returns_503_for_youtube(self):
        import asyncio
        from fastapi import HTTPException
        import pytest

        meta = self._make_meta()
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://youtube.com/@Test"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                from app.api.container import queue_container, QueueRequest
                req = QueueRequest(item_ids=["yt:vid0"], queue_mode="selected")
                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(queue_container("ytcid", req, MagicMock()))
                assert exc_info.value.status_code == 503
                assert exc_info.value.detail["code"] == "youtube_container_queue_not_available"

    def test_no_phantom_batch_created(self):
        """When queue is blocked, _create_bulk_jobs must never be called."""
        import asyncio
        from fastapi import HTTPException
        import pytest

        meta = self._make_meta()
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://youtube.com/@Test"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.api.container._create_bulk_jobs") as mock_jobs:
                    from app.api.container import queue_container, QueueRequest
                    req = QueueRequest(item_ids=["yt:vid0"], queue_mode="selected")
                    with pytest.raises(HTTPException):
                        asyncio.run(queue_container("ytcid", req, MagicMock()))
                    mock_jobs.assert_not_called()

    def test_queue_not_retryable(self):
        """Error detail must say retryable=False so client doesn't retry."""
        import asyncio
        from fastapi import HTTPException
        import pytest

        meta = self._make_meta()
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://youtube.com/@Test"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                from app.api.container import queue_container, QueueRequest
                req = QueueRequest(item_ids=["yt:vid0"], queue_mode="selected")
                with pytest.raises(HTTPException) as exc_info:
                    asyncio.run(queue_container("ytcid", req, MagicMock()))
                assert exc_info.value.detail.get("retryable") is False


# ══════════════════════════════════════════════════════════════════════════════
# 7. YouTubeExpander Unit
# ══════════════════════════════════════════════════════════════════════════════

class TestYouTubeExpanderUnit:
    def _expander(self):
        from app.services.container_expanders.youtube_expander import YouTubeExpander
        return YouTubeExpander()

    def test_no_proxy_returns_c020(self):
        exp = self._expander()
        with patch("app.services.youtube_proxy_health.get_youtube_proxy_health",
                   return_value=_make_health("no_proxy")):
            result = exp.discover("https://youtube.com/@Test", max_items=50)
            assert result.status == "failed"
            assert result.error_code == "C020"

    def test_unhealthy_proxy_returns_c021(self):
        exp = self._expander()
        with patch("app.services.youtube_proxy_health.get_youtube_proxy_health",
                   return_value=_make_health("unhealthy", "Connection refused")):
            result = exp.discover("https://youtube.com/@Test", max_items=50)
            assert result.status == "failed"
            assert result.error_code == "C021"
            assert "proxy" in result.error_message.lower()

    def test_proxy_error_during_scrape_maps_to_c021(self):
        """Scrape exception with proxy keywords must surface C021."""
        exp = self._expander()
        with patch("app.services.youtube_proxy_health.get_youtube_proxy_health",
                   return_value=_make_health("healthy")):
            with patch.object(exp, "_get_proxy", return_value="http://proxy:8080"):
                with patch.object(exp, "_scrape_flat",
                                  side_effect=Exception("407 proxy authentication required")):
                    result = exp.discover("https://youtube.com/@Test", max_items=50)
                    assert result.status == "failed"
                    assert result.error_code == "C021"

    def test_healthy_proxy_successful_scrape_returns_ready(self):
        exp = self._expander()
        fake_entries = [
            {
                "id": f"vid{i}", "title": f"Video {i}",
                "url": f"https://yt.com/watch?v=vid{i}",
                "duration": 120, "view_count": 1000,
                "upload_date": "20240101", "uploader": "Chan",
            }
            for i in range(5)
        ]
        with patch("app.services.youtube_proxy_health.get_youtube_proxy_health",
                   return_value=_make_health("healthy")):
            with patch.object(exp, "_get_proxy", return_value="http://proxy:8080"):
                with patch.object(exp, "_scrape_flat",
                                  return_value=(fake_entries, "Test Channel")):
                    result = exp.discover("https://youtube.com/@Test", max_items=50)
                    assert result.status == "ready"
                    assert result.item_count == 5
                    assert len(result.sections) == 1
                    assert result.sections[0].key == "videos"


# ══════════════════════════════════════════════════════════════════════════════
# 8. resolve-input endpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveInput:
    """
    get_youtube_proxy_health_async is imported inside resolve_input's body →
    patch the source module.
    """

    def _norm_mock(self, url):
        m = MagicMock()
        m.canonical_url = url
        return m

    def test_single_video_cannot_discover(self):
        import asyncio

        # No proxy call expected for single_video
        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "single_video")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://yt.com/watch?v=x")):
                from app.api.container import resolve_input, ResolveInputRequest
                req = ResolveInputRequest(url="https://yt.com/watch?v=dQw")
                result = asyncio.run(resolve_input(req))
                assert result["can_discover"] is False
                assert result["message"] == "youtube_single_temporarily_disabled"

    def test_channel_no_proxy_cannot_discover(self):
        import asyncio

        health_mock = AsyncMock(return_value=_make_health("no_proxy"))
        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "channel", "TestChan")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://yt.com/@TestChan")):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    from app.api.container import resolve_input, ResolveInputRequest
                    req = ResolveInputRequest(url="https://yt.com/@TestChan")
                    result = asyncio.run(resolve_input(req))
                    assert result["can_discover"] is False
                    assert result["message"] == "youtube_proxy_required"

    def test_channel_unhealthy_proxy_cannot_discover(self):
        import asyncio

        health_mock = AsyncMock(return_value=_make_health("unhealthy", "timeout"))
        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "channel")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://yt.com/@Test")):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    from app.api.container import resolve_input, ResolveInputRequest
                    req = ResolveInputRequest(url="https://yt.com/@Test")
                    result = asyncio.run(resolve_input(req))
                    assert result["can_discover"] is False
                    assert result["message"] == "youtube_proxy_unhealthy"

    def test_channel_healthy_proxy_can_discover(self):
        import asyncio

        health_mock = AsyncMock(return_value=_make_health("healthy"))
        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("youtube", "channel", "MrBeast")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://yt.com/@MrBeast")):
                with patch("app.services.youtube_proxy_health.get_youtube_proxy_health_async",
                           health_mock):
                    from app.api.container import resolve_input, ResolveInputRequest
                    req = ResolveInputRequest(url="https://yt.com/@MrBeast")
                    result = asyncio.run(resolve_input(req))
                    assert result["can_discover"] is True
                    assert result["message"] == ""
                    assert result["platform"] == "youtube"
                    assert result["source_type"] == "channel"

    def test_non_youtube_no_proxy_gating(self):
        """Non-YouTube platforms must not trigger YouTube-specific gates."""
        import asyncio

        with patch("app.core.source_classifier.classify",
                   return_value=_classify_result("tiktok", "profile", "user1")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=self._norm_mock("https://tiktok.com/@user1")):
                from app.api.container import resolve_input, ResolveInputRequest
                req = ResolveInputRequest(url="https://tiktok.com/@user1")
                result = asyncio.run(resolve_input(req))
                assert result["platform"] == "tiktok"
                # No YouTube-specific gate applied
                assert result["message"] != "youtube_proxy_required"
