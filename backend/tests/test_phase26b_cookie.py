"""
Phase 26-B — Cookie-gated Platform Hardening Tests
====================================================
Covers:
  1. cookie_manager module (has_cookies_for / get_cookie_file)
  2. cookie_capability evaluator (6 states × 3 platforms)
  3. discover_container cookie preflight (Instagram / Twitter)
  4. expand_section cookie re-check
  5. queue_container cookie gate + Reddit partial response
  6. URL classification helpers (via source_classifier)
  7. _runtime_capability updated to use pool state

Mocking rules:
  - Patch _platform_pool_status at source module for pool introspection
  - Patch get_cookie_from_pool for cookie_manager tests
  - All routes in backend/app/api/container.py
  - Use asyncio.run() (not get_event_loop) for Python 3.12 compat
  - Use AsyncMock for async functions
"""
from __future__ import annotations

import time
import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _pool(total=1, healthy=1, in_cooldown=0, soft_blocked=0, hard_blocked=0,
          min_days_left=30):
    return {
        "total":        total,
        "healthy":      healthy,
        "in_cooldown":  in_cooldown,
        "soft_blocked": soft_blocked,
        "hard_blocked": hard_blocked,
        "min_days_left": min_days_left,
    }


def _classify(platform, source_type, nid="test"):
    from app.core.source_classifier import ClassifyResult
    return ClassifyResult(platform=platform, source_type=source_type,
                          normalized_id=nid, confidence=1.0)


def _norm(url):
    m = MagicMock()
    m.canonical_url = url
    return m


# ══════════════════════════════════════════════════════════════════════════════
# 1. cookie_manager
# ══════════════════════════════════════════════════════════════════════════════

class TestCookieManager:
    def test_has_cookies_for_pool_nonempty(self):
        # cookie_manager.py does `from app.core.cookie_pool import
        # get_cookie_from_pool`, binding its own local reference — patching
        # cookie_pool's copy (as this test did) never reaches the name
        # cookie_manager.has_cookies_for() actually calls, so the real
        # function ran and hit a real Redis at localhost:6379. Patch where
        # it's used, matching every other test in this class.
        with patch("app.core.cookie_manager.get_cookie_from_pool", return_value="abc123"):
            from app.core.cookie_manager import has_cookies_for
            assert has_cookies_for("instagram") is True

    def test_has_cookies_for_pool_empty_no_env(self):
        import os
        os.environ.pop("INSTAGRAM_COOKIES_B64", None)
        # Patch the binding in cookie_manager (imported at module level)
        with patch("app.core.cookie_manager.get_cookie_from_pool", return_value=None):
            from app.core.cookie_manager import has_cookies_for
            assert has_cookies_for("instagram") is False

    def test_has_cookies_for_env_fallback(self):
        b64 = base64.b64encode(b"# Netscape HTTP Cookie File\n").decode()
        with patch.dict("os.environ", {"INSTAGRAM_COOKIES_B64": b64}):
            with patch("app.core.cookie_manager.get_cookie_from_pool", return_value=None):
                from app.core.cookie_manager import has_cookies_for
                assert has_cookies_for("instagram") is True

    def test_get_cookie_file_returns_path(self, tmp_path):
        b64 = base64.b64encode(b"# Netscape HTTP Cookie File\ntest\n").decode()
        with patch("app.core.cookie_manager.get_cookie_from_pool", return_value=b64):
            from app.core.cookie_manager import get_cookie_file
            path = get_cookie_file("instagram")
            assert path is not None
            import os
            assert os.path.exists(path)
            os.unlink(path)

    def test_get_cookie_file_none_when_no_cookie(self):
        import os
        os.environ.pop("TWITTER_COOKIES_B64", None)
        with patch("app.core.cookie_manager.get_cookie_from_pool", return_value=None):
            from app.core.cookie_manager import get_cookie_file
            assert get_cookie_file("twitter") is None


# ══════════════════════════════════════════════════════════════════════════════
# 2. cookie_capability evaluator
# ══════════════════════════════════════════════════════════════════════════════

class TestCookieCapabilityEvaluator:
    def setup_method(self):
        # Clear in-process cache before each test
        from app.services import cookie_capability
        cookie_capability._cache.clear()

    def _eval(self, platform, pool_info, env=None):
        env = env or {}
        with patch("app.services.cookie_capability._platform_pool_status",
                   return_value=pool_info):
            with patch.dict("os.environ", env, clear=False):
                from app.services.cookie_capability import evaluate
                return evaluate(platform)

    def test_instagram_healthy_pool(self):
        cap = self._eval("instagram", _pool(total=2, healthy=2))
        assert cap["can_discover_container"] is True
        assert cap["cookie_state"] == "cookie_healthy"
        assert cap["availability_reason"] is None

    def test_instagram_empty_pool_no_env(self):
        import os
        os.environ.pop("INSTAGRAM_COOKIES_B64", None)
        cap = self._eval("instagram", _pool(total=0))
        assert cap["can_discover_container"] is False
        assert cap["cookie_state"] == "cookie_unavailable"
        assert cap["availability_reason"] == "cookie_unavailable"

    def test_instagram_empty_pool_with_env(self):
        b64 = base64.b64encode(b"cookie").decode()
        cap = self._eval("instagram", _pool(total=0), env={"INSTAGRAM_COOKIES_B64": b64})
        assert cap["can_discover_container"] is True
        assert cap["cookie_state"] == "cookie_healthy"

    def test_instagram_all_hard_blocked(self):
        cap = self._eval("instagram", _pool(total=2, healthy=0, in_cooldown=0,
                                             hard_blocked=2))
        assert cap["can_discover_container"] is False
        assert cap["cookie_state"] == "cookie_blocked"

    def test_instagram_all_expired(self):
        import os
        os.environ.pop("INSTAGRAM_COOKIES_B64", None)
        cap = self._eval("instagram", _pool(total=1, healthy=1, min_days_left=0))
        assert cap["can_discover_container"] is False
        assert cap["cookie_state"] == "cookie_expired"

    def test_twitter_healthy_pool(self):
        cap = self._eval("twitter", _pool(total=1, healthy=1))
        assert cap["can_discover_container"] is True
        assert cap["requires_cookie"] is True

    def test_twitter_cooldown_still_healthy(self):
        # Cookies in cooldown are still usable (cooldown ≠ blocked)
        cap = self._eval("twitter", _pool(total=2, healthy=0, in_cooldown=2))
        assert cap["can_discover_container"] is True
        assert cap["cookie_state"] == "cookie_healthy"

    def test_reddit_always_partial(self):
        # Reddit doesn't check pool — always cookie_partial
        cap = self._eval("reddit", {})
        assert cap["can_discover_container"] is True
        assert cap["cookie_state"] == "cookie_partial"
        assert cap["requires_cookie"] is False

    def test_cache_hit_avoids_pool_check(self):
        from app.services import cookie_capability
        from app.services.cookie_capability import evaluate
        # Prime cache
        cookie_capability._cache["instagram"] = (time.time(), {"can_discover_container": True,
                                                                "cookie_state": "cookie_healthy"})
        with patch("app.services.cookie_capability._platform_pool_status") as mock_pool:
            result = evaluate("instagram")
            mock_pool.assert_not_called()
        assert result["can_discover_container"] is True

    def test_cache_expires(self):
        from app.services import cookie_capability
        from app.services.cookie_capability import evaluate
        # Force cache miss by setting old timestamp
        cookie_capability._cache["instagram"] = (0.0, {"can_discover_container": False,
                                                        "cookie_state": "cookie_unavailable"})
        with patch("app.services.cookie_capability._platform_pool_status",
                   return_value=_pool(total=1, healthy=1)):
            import os
            os.environ.pop("INSTAGRAM_COOKIES_B64", None)
            result = evaluate("instagram")
        assert result["can_discover_container"] is True  # fresh eval


# ══════════════════════════════════════════════════════════════════════════════
# 3. discover_container cookie preflight
# ══════════════════════════════════════════════════════════════════════════════

class TestDiscoverContainerCookiePreflight:
    def setup_method(self):
        from app.services import cookie_capability
        cookie_capability._cache.clear()

    def test_instagram_blocked_when_no_cookie(self):
        from fastapi import HTTPException
        no_cookie_cap = {
            "can_discover_container": False,
            "cookie_state": "cookie_unavailable",
            "availability_reason": "cookie_unavailable",
            "message": "Chưa cấu hình cookie.",
        }
        with patch("app.core.source_classifier.classify",
                   return_value=_classify("instagram", "profile")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=_norm("https://instagram.com/testuser")):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=no_cookie_cap):
                    from app.api.container import discover_container, DiscoverRequest
                    with pytest.raises(HTTPException) as exc:
                        asyncio.run(discover_container(
                            DiscoverRequest(url="https://instagram.com/testuser")
                        ))
                    assert exc.value.status_code == 503
                    assert "cookie_unavailable" in exc.value.detail["code"]

    def test_instagram_blocked_when_expired(self):
        from fastapi import HTTPException
        expired_cap = {
            "can_discover_container": False,
            "cookie_state": "cookie_expired",
            "availability_reason": "cookie_expired",
            "message": "Cookie đã hết hạn.",
        }
        with patch("app.core.source_classifier.classify",
                   return_value=_classify("instagram", "profile")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=_norm("https://instagram.com/testuser")):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=expired_cap):
                    from app.api.container import discover_container, DiscoverRequest
                    with pytest.raises(HTTPException) as exc:
                        asyncio.run(discover_container(
                            DiscoverRequest(url="https://instagram.com/testuser")
                        ))
                    assert exc.value.status_code == 503
                    assert exc.value.detail["availability_reason"] == "cookie_expired"

    def test_twitter_blocked_when_hard_blocked(self):
        from fastapi import HTTPException
        blocked_cap = {
            "can_discover_container": False,
            "cookie_state": "cookie_blocked",
            "availability_reason": "cookie_blocked",
            "message": "Cookie bị khoá.",
        }
        with patch("app.core.source_classifier.classify",
                   return_value=_classify("twitter", "profile")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=_norm("https://x.com/testuser")):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=blocked_cap):
                    from app.api.container import discover_container, DiscoverRequest
                    with pytest.raises(HTTPException) as exc:
                        asyncio.run(discover_container(
                            DiscoverRequest(url="https://x.com/testuser")
                        ))
                    assert exc.value.status_code == 503

    def test_instagram_proceeds_when_cookie_healthy(self):
        healthy_cap = {
            "can_discover_container": True,
            "cookie_state": "cookie_healthy",
            "availability_reason": None,
            "message": None,
        }
        task_mock = MagicMock()
        task_mock.apply_async = MagicMock()
        with patch("app.core.source_classifier.classify",
                   return_value=_classify("instagram", "profile", "testuser")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=_norm("https://instagram.com/testuser")):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=healthy_cap):
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
                                                        result = asyncio.run(discover_container(
                                                            DiscoverRequest(url="https://instagram.com/testuser")
                                                        ))
                                                        assert result["platform"] == "instagram"
                                                        assert result["status"] == "queued"

    def test_reddit_no_preflight_needed(self):
        """Reddit public subreddits must pass preflight without cookie check."""
        task_mock = MagicMock()
        task_mock.apply_async = MagicMock()
        with patch("app.core.source_classifier.classify",
                   return_value=_classify("reddit", "subreddit", "videos")):
            with patch("app.core.url_normalizer.normalize",
                       return_value=_norm("https://reddit.com/r/videos")):
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
                                                    result = asyncio.run(discover_container(
                                                        DiscoverRequest(url="https://reddit.com/r/videos")
                                                    ))
                                                    assert result["platform"] == "reddit"


# ══════════════════════════════════════════════════════════════════════════════
# 4. expand_section cookie re-check
# ══════════════════════════════════════════════════════════════════════════════

class TestExpandCookieRecheck:
    def setup_method(self):
        from app.services import cookie_capability
        cookie_capability._cache.clear()

    def _make_meta(self, platform):
        from app.services.container_discovery import ContainerMeta, ContainerSection
        return ContainerMeta(
            container_id="cid_test",
            platform=platform,
            container_type="profile",
            url=f"https://{platform}.com/testuser",
            title="Test",
            status="ready",
            sections=[ContainerSection(key="posts", label="Posts",
                                       item_count=5, items_loaded=False)],
        )

    def test_instagram_expand_blocked_when_cookie_expires(self):
        from fastapi import HTTPException
        expired_cap = {
            "can_expand": False,
            "cookie_state": "cookie_expired",
            "availability_reason": "cookie_expired",
            "message": "Cookie đã hết hạn.",
        }
        meta = self._make_meta("instagram")
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://instagram.com/testuser"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=expired_cap):
                    from app.api.container import expand_section, ExpandRequest
                    with pytest.raises(HTTPException) as exc:
                        asyncio.run(expand_section("cid_test",
                                                   ExpandRequest(section="posts")))
                    assert exc.value.status_code == 503
                    assert exc.value.detail["availability_reason"] == "cookie_expired"

    def test_twitter_expand_blocked_when_blocked(self):
        from fastapi import HTTPException
        blocked_cap = {
            "can_expand": False,
            "cookie_state": "cookie_blocked",
            "availability_reason": "cookie_blocked",
            "message": "Cookie bị khoá.",
        }
        meta = self._make_meta("twitter")
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://x.com/testuser"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=blocked_cap):
                    from app.api.container import expand_section, ExpandRequest
                    with pytest.raises(HTTPException) as exc:
                        asyncio.run(expand_section("cid_test",
                                                   ExpandRequest(section="posts")))
                    assert exc.value.status_code == 503

    def test_instagram_expand_proceeds_healthy(self):
        healthy_cap = {
            "can_expand": True,
            "cookie_state": "cookie_healthy",
            "availability_reason": None,
            "message": None,
        }
        meta = self._make_meta("instagram")
        fake_expander = MagicMock()
        fake_expander.expand_section = MagicMock(return_value=[])
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://instagram.com/testuser"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=healthy_cap):
                    with patch("app.services.container_expanders.get_expander",
                               return_value=fake_expander):
                        with patch("app.api.container.track_container_metric"):
                            with patch("app.api.container.cache_container"):
                                from app.api.container import expand_section, ExpandRequest
                                asyncio.run(expand_section("cid_test",
                                                           ExpandRequest(section="posts")))
                                fake_expander.expand_section.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 5. queue_container cookie gate + Reddit partial
# ══════════════════════════════════════════════════════════════════════════════

class TestQueueCookieGate:
    def setup_method(self):
        from app.services import cookie_capability
        cookie_capability._cache.clear()

    def _make_meta(self, platform):
        from app.services.container_discovery import (
            ContainerMeta, ContainerSection, ContainerItem,
        )
        return ContainerMeta(
            container_id="cid_q",
            platform=platform,
            container_type="profile",
            url=f"https://{platform}.com/testuser",
            title="Test",
            status="ready",
            sections=[
                ContainerSection(
                    key="posts", label="Posts",
                    item_count=3, items_loaded=True,
                    items=[
                        ContainerItem(
                            id=f"{platform}:item{i}",
                            url=f"https://{platform}.com/p/{i}",
                            title=f"Post {i}", author="test",
                            thumbnail="", duration_ms=0, media_type="video",
                        )
                        for i in range(3)
                    ],
                )
            ],
        )

    def test_instagram_queue_blocked_no_cookie(self):
        from fastapi import HTTPException
        no_cookie_cap = {
            "can_queue_container_items": False,
            "cookie_state": "cookie_unavailable",
            "availability_reason": "cookie_unavailable",
            "message": "Chưa cấu hình cookie.",
        }
        meta = self._make_meta("instagram")
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://instagram.com/testuser"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=no_cookie_cap):
                    from app.api.container import queue_container, QueueRequest
                    with pytest.raises(HTTPException) as exc:
                        asyncio.run(queue_container(
                            "cid_q",
                            QueueRequest(item_ids=["instagram:item0"], queue_mode="selected"),
                            MagicMock(),
                        ))
                    assert exc.value.status_code == 503
                    assert "cookie_unavailable" in exc.value.detail["code"]

    def test_instagram_queue_not_create_jobs_when_blocked(self):
        """_create_bulk_jobs must NOT be called when cookie gate rejects."""
        from fastapi import HTTPException
        no_cookie_cap = {
            "can_queue_container_items": False,
            "cookie_state": "cookie_unavailable",
            "availability_reason": "cookie_unavailable",
            "message": "Chưa cấu hình cookie.",
        }
        meta = self._make_meta("instagram")
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://instagram.com/testuser"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=no_cookie_cap):
                    with patch("app.api.container._create_bulk_jobs") as mock_jobs:
                        from app.api.container import queue_container, QueueRequest
                        with pytest.raises(HTTPException):
                            asyncio.run(queue_container(
                                "cid_q",
                                QueueRequest(item_ids=["instagram:item0"], queue_mode="selected"),
                                MagicMock(),
                            ))
                        mock_jobs.assert_not_called()

    def test_reddit_queue_returns_partial_accepted(self):
        """Reddit queue succeeds and annotates partial_accepted=True."""
        healthy_cap = {
            "can_queue_container_items": True,
            "cookie_state": "cookie_partial",
            "availability_reason": "cookie_partial",
            "message": None,
        }
        meta = self._make_meta("reddit")
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://reddit.com/r/videos"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.api.container.check_queue_dedup", return_value=None):
                    with patch("app.api.container._create_bulk_jobs",
                               new=AsyncMock(return_value=3)):
                        with patch("app.api.container.save_batch_source_meta"):
                            with patch("app.api.container.set_queue_dedup"):
                                with patch("app.api.container.track_container_metric"):
                                    from app.api.container import queue_container, QueueRequest
                                    result = asyncio.run(queue_container(
                                        "cid_q",
                                        QueueRequest(queue_mode="all"),
                                        MagicMock(),
                                    ))
                                    assert result["partial_accepted"] is True
                                    assert result["accepted_count"] == 3
                                    assert result["rejected_count"] == 0
                                    assert result["rejected_reason"] == "reddit_login_required"

    def test_cookie_blocked_retryable_flag(self):
        """cookie_blocked should have retryable=True (auto-unblocks after TTL)."""
        from fastapi import HTTPException
        blocked_cap = {
            "can_queue_container_items": False,
            "cookie_state": "cookie_blocked",
            "availability_reason": "cookie_blocked",
            "message": "Cookie bị khoá.",
        }
        meta = self._make_meta("twitter")
        with patch("app.api.container.get_url_for_container_id",
                   return_value="https://x.com/testuser"):
            with patch("app.api.container.get_cached_container", return_value=meta):
                with patch("app.services.cookie_capability.evaluate",
                           return_value=blocked_cap):
                    from app.api.container import queue_container, QueueRequest
                    with pytest.raises(HTTPException) as exc:
                        asyncio.run(queue_container(
                            "cid_q",
                            QueueRequest(item_ids=["twitter:item0"], queue_mode="selected"),
                            MagicMock(),
                        ))
                    assert exc.value.detail.get("retryable") is True


# ══════════════════════════════════════════════════════════════════════════════
# 6. URL classification for cookie platforms
# ══════════════════════════════════════════════════════════════════════════════

class TestCookiePlatformURLClassification:
    def _classify(self, url):
        from app.core.source_classifier import classify
        return classify(url)

    def test_instagram_profile(self):
        r = self._classify("https://www.instagram.com/natgeo/")
        assert r.platform == "instagram"
        assert r.source_type in ("profile", "channel")

    def test_instagram_reel(self):
        r = self._classify("https://www.instagram.com/reel/ABC123/")
        assert r.platform == "instagram"

    def test_instagram_post(self):
        r = self._classify("https://www.instagram.com/p/ABC123/")
        assert r.platform == "instagram"

    def test_twitter_profile(self):
        r = self._classify("https://twitter.com/natgeo")
        assert r.platform == "twitter"

    def test_x_com_profile(self):
        r = self._classify("https://x.com/natgeo")
        assert r.platform == "twitter"

    def test_reddit_subreddit(self):
        r = self._classify("https://www.reddit.com/r/videos/")
        assert r.platform == "reddit"
        assert r.source_type in ("subreddit", "channel", "profile")

    def test_reddit_user(self):
        r = self._classify("https://www.reddit.com/user/someone/")
        assert r.platform == "reddit"


# ══════════════════════════════════════════════════════════════════════════════
# 7. _runtime_capability updated to use pool state
# ══════════════════════════════════════════════════════════════════════════════

class TestRuntimeCapabilityCookiePlatforms:
    def setup_method(self):
        from app.services import cookie_capability
        cookie_capability._cache.clear()

    def test_instagram_runtime_reflects_pool_state(self):
        healthy_cap = {
            "can_discover_container": True,
            "can_expand": True,
            "can_queue_container_items": True,
            "can_single_download": True,
            "availability_reason": None,
            "cookie_state": "cookie_healthy",
        }
        with patch("app.services.cookie_capability.evaluate", return_value=healthy_cap):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("instagram")
            assert cap["can_discover_container"] is True
            assert cap["cookie_state"] == "cookie_healthy"

    def test_twitter_runtime_reflects_no_cookie(self):
        no_cookie_cap = {
            "can_discover_container": False,
            "can_expand": False,
            "can_queue_container_items": False,
            "can_single_download": False,
            "availability_reason": "cookie_unavailable",
            "cookie_state": "cookie_unavailable",
        }
        with patch("app.services.cookie_capability.evaluate", return_value=no_cookie_cap):
            from app.api.container import _runtime_capability
            cap = _runtime_capability("twitter")
            assert cap["can_discover_container"] is False
            assert cap["availability_reason"] == "cookie_unavailable"

    def test_youtube_runtime_unchanged(self):
        """YouTube capability must NOT be touched by 26-B changes."""
        from app.api.container import _runtime_capability
        import os
        os.environ.pop("YTDL_PROXY", None)
        cap = _runtime_capability("youtube")
        assert cap["can_single_download"] is False
        assert cap["requires_proxy"] is True
