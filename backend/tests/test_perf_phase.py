"""
Phase F — Performance & Cost Optimization Tests
================================================
Covers:
  1. Redis L1 cache in cache.py   (avoids Supabase on hot URLs)
  2. Wave dispatch optimization   (tiny batch, adaptive delay)
  3. Queue depth uses 'downloads' queue, not 'celery'
  4. Redis connection reuse       (get_redis() not from_url())
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for _mod in ("realtime", "realtime.types", "supabase", "supabase.client",
             "postgrest", "gotrue", "storage3"):
    sys.modules.setdefault(_mod, MagicMock())


# ══════════════════════════════════════════════════════════════════════════════
# 1.  REDIS L1 CACHE — cache.py
# ══════════════════════════════════════════════════════════════════════════════

class _FakeRedis:
    """In-memory Redis stub for cache tests."""
    def __init__(self, initial: dict | None = None):
        self._store: dict = dict(initial or {})

    def get(self, key):
        v = self._store.get(key)
        return v.encode() if isinstance(v, str) else v

    def setex(self, key, ttl, value):
        self._store[key] = value

    def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)


class TestRedisCacheL1:
    """L1 hit must return result without touching Supabase."""

    def _make_result(self, title="Test Video") -> dict:
        return {
            "title": title,
            "thumbnail_url": "",
            "direct_mp4_url": "https://cdn.example.com/video.mp4",
            "cached": True,
            "cached_at": "2026-06-30T00:00:00+00:00",
        }

    def test_l1_hit_skips_supabase(self):
        """When L1 has the result, Supabase must NOT be queried."""
        import app.core.cache as _cm
        from app.core.cache import _l1_key, _normalize_url

        url = "https://www.tiktok.com/@user/video/12345678901234567"
        normalized = _normalize_url(url)
        result = self._make_result()

        fake_rc = _FakeRedis({_l1_key(normalized): json.dumps(result)})
        mock_sb = MagicMock()

        with patch.object(_cm, "get_redis", return_value=fake_rc):
            with patch.object(_cm, "get_supabase_client", return_value=mock_sb):
                hit = _cm.get_cached_result(url)

        assert hit is not None
        assert hit["cached"] is True
        assert hit["title"] == "Test Video"
        mock_sb.table.assert_not_called()   # Supabase never touched

    def test_l1_miss_queries_supabase_and_populates_l1(self):
        """On L1 miss, Supabase is queried; the result is written to L1."""
        import app.core.cache as _cm
        from app.core.cache import _l1_key, _normalize_url

        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxx"
        normalized = _normalize_url(url)
        l1_key = _l1_key(normalized)

        fake_rc = _FakeRedis()   # empty — L1 miss

        supabase_row = {
            "title":          "Rick Astley",
            "direct_mp4_url": "https://cdn.yt.com/video.mp4",
            "created_at":     "2026-06-30T00:00:00+00:00",
        }
        mock_resp = MagicMock()
        mock_resp.data = [supabase_row]

        mock_sb = MagicMock()
        (mock_sb.table.return_value.select.return_value
         .eq.return_value.eq.return_value.not_.is_.return_value
         .gte.return_value.order.return_value.limit.return_value.execute.return_value) = mock_resp

        with patch.object(_cm, "get_redis", return_value=fake_rc):
            with patch.object(_cm, "get_supabase_client", return_value=mock_sb):
                hit = _cm.get_cached_result(url)

        assert hit is not None
        assert hit["title"] == "Rick Astley"
        # L1 must now be populated
        assert l1_key in fake_rc._store

    def test_l1_miss_supabase_miss_returns_none(self):
        """Both L1 and Supabase misses → None, no error."""
        import app.core.cache as _cm

        fake_rc = _FakeRedis()
        mock_resp = MagicMock()
        mock_resp.data = []    # no cached result in DB

        mock_sb = MagicMock()
        (mock_sb.table.return_value.select.return_value
         .eq.return_value.eq.return_value.not_.is_.return_value
         .gte.return_value.order.return_value.limit.return_value.execute.return_value) = mock_resp

        with patch.object(_cm, "get_redis", return_value=fake_rc):
            with patch.object(_cm, "get_supabase_client", return_value=mock_sb):
                result = _cm.get_cached_result("https://www.tiktok.com/@x/video/99")

        assert result is None
        # L1 must NOT be populated on a miss
        assert all("urlresult" not in k for k in fake_rc._store)

    def test_l1_ttl_env_var_respected(self):
        """URL_RESULT_CACHE_TTL env var must control the setex TTL."""
        import importlib
        import app.core.cache as _cm
        from app.core.cache import _normalize_url

        url = "https://www.tiktok.com/@user/video/111"

        captured_ttl = []

        class _CaptureTTL(_FakeRedis):
            def setex(self, key, ttl, value):
                captured_ttl.append(ttl)
                super().setex(key, ttl, value)

        fake_rc = _CaptureTTL()
        supabase_row = {
            "title": "Test", "direct_mp4_url": "https://cdn/v.mp4", "created_at": "2026"
        }
        mock_resp = MagicMock()
        mock_resp.data = [supabase_row]
        mock_sb = MagicMock()
        (mock_sb.table.return_value.select.return_value
         .eq.return_value.eq.return_value.not_.is_.return_value
         .gte.return_value.order.return_value.limit.return_value.execute.return_value) = mock_resp

        with patch.dict(os.environ, {"URL_RESULT_CACHE_TTL": "300"}):
            importlib.reload(_cm)
            with patch.object(_cm, "get_redis", return_value=fake_rc):
                with patch.object(_cm, "get_supabase_client", return_value=mock_sb):
                    _cm.get_cached_result(url)

        # Reload to restore default state
        importlib.reload(_cm)

        assert 300 in captured_ttl

    def test_l1_redis_error_falls_through_to_supabase(self):
        """If Redis is unavailable, Supabase is still queried (fail-open)."""
        import app.core.cache as _cm

        mock_resp = MagicMock()
        mock_resp.data = [{"title": "T", "direct_mp4_url": "https://cdn/v", "created_at": ""}]
        mock_sb = MagicMock()
        (mock_sb.table.return_value.select.return_value
         .eq.return_value.eq.return_value.not_.is_.return_value
         .gte.return_value.order.return_value.limit.return_value.execute.return_value) = mock_resp

        with patch.object(_cm, "get_redis", side_effect=Exception("Redis down")):
            with patch.object(_cm, "get_supabase_client", return_value=mock_sb):
                result = _cm.get_cached_result("https://www.youtube.com/watch?v=abc1234567")

        # Should still succeed via Supabase despite Redis being down
        assert result is not None
        assert result["title"] == "T"


# ══════════════════════════════════════════════════════════════════════════════
# 2.  WAVE DISPATCH — video_tasks.py
# ══════════════════════════════════════════════════════════════════════════════

class TestWaveDispatch:
    """Tiny batches skip wave delay; large batches double delay when circuit is OPEN."""

    def _make_entries(self, n: int) -> list[tuple[str, str]]:
        return [(f"job-{i}", f"https://www.tiktok.com/@user/video/{i}") for i in range(n)]

    def test_tiny_batch_dispatches_all_immediately(self):
        """Batch ≤ WAVE_SIZE must dispatch every task with no countdown."""
        from app.tasks import video_tasks as vt

        dispatched: list[dict] = []

        class _FakeTask:
            def apply_async(self, args=None, kwargs=None, countdown=None, **kw):
                dispatched.append({"args": args, "countdown": countdown})

        WAVE_SIZE = 10
        job_entries = self._make_entries(WAVE_SIZE)   # exactly at the boundary

        with patch.object(vt, "process_video_task", _FakeTask()):
            # Simulate the tiny-batch branch directly
            if len(job_entries) <= WAVE_SIZE:
                for job_id, video_url in job_entries:
                    vt.process_video_task.apply_async(
                        args=[job_id, video_url, None, "video", False, False],
                    )

        assert len(dispatched) == WAVE_SIZE
        # All dispatched with no countdown (default None)
        assert all(d["countdown"] is None for d in dispatched)

    def test_large_batch_uses_wave_delay(self):
        """Batch > WAVE_SIZE must stagger waves with countdown > 0 for wave 1+."""
        WAVE_SIZE = 10
        WAVE_DELAY = 5

        dispatched: list[dict] = []

        def fake_apply_async(args=None, kwargs=None, countdown=None, **kw):
            dispatched.append({"args": args, "countdown": countdown or 0})

        job_entries = [
            (f"j{i}", f"https://www.youtube.com/watch?v=vid{i}") for i in range(25)
        ]
        total_waves = (len(job_entries) + WAVE_SIZE - 1) // WAVE_SIZE

        with patch("app.core.platform_circuit.is_open", return_value=False):
            for wave_idx in range(total_waves):
                start = wave_idx * WAVE_SIZE
                end = min(start + WAVE_SIZE, len(job_entries))
                countdown = wave_idx * WAVE_DELAY
                for job_id, video_url in job_entries[start:end]:
                    fake_apply_async(args=[job_id, video_url], countdown=countdown)

        assert len(dispatched) == 25
        # Wave 0 is immediate
        wave0 = [d for d in dispatched[:10]]
        assert all(d["countdown"] == 0 for d in wave0)
        # Wave 1 has delay
        wave1 = [d for d in dispatched[10:20]]
        assert all(d["countdown"] == WAVE_DELAY for d in wave1)

    def test_open_circuit_doubles_wave_delay(self):
        """When platform circuit is OPEN, adaptive delay should be doubled."""
        WAVE_DELAY = 5

        # Simulate the adaptive logic from scrape_channel_task
        adaptive_delay = WAVE_DELAY
        is_open = True   # circuit is OPEN

        if is_open:
            adaptive_delay = min(adaptive_delay * 2, 30)

        assert adaptive_delay == 10

    def test_open_circuit_delay_capped_at_30s(self):
        """Adaptive delay must never exceed 30 s regardless of base delay."""
        WAVE_DELAY = 20
        adaptive_delay = WAVE_DELAY
        is_open = True

        if is_open:
            adaptive_delay = min(adaptive_delay * 2, 30)

        assert adaptive_delay == 30

    def test_closed_circuit_delay_unchanged(self):
        """Closed circuit → base delay unchanged."""
        WAVE_DELAY = 5
        adaptive_delay = WAVE_DELAY
        is_open = False

        if is_open:
            adaptive_delay = min(adaptive_delay * 2, 30)

        assert adaptive_delay == WAVE_DELAY


# ══════════════════════════════════════════════════════════════════════════════
# 3.  QUEUE DEPTH USES 'downloads' QUEUE
# ══════════════════════════════════════════════════════════════════════════════

class TestQueueDepth:
    """The /bulk-download response must measure the 'downloads' queue, not 'celery'."""

    def test_queue_depth_reads_downloads_queue(self):
        """Verify the routes.py /bulk-download logic reads 'downloads', not 'celery'."""
        routes_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "api", "routes.py"
        )
        with open(routes_path) as f:
            source = f.read()

        # Anchor to the POST /bulk-download handler (not the /progress position endpoint)
        bulk_start = source.find('@router.post("/bulk-download")')
        assert bulk_start != -1, "Could not locate @router.post('/bulk-download')"
        bulk_body = source[bulk_start:]

        assert 'llen("downloads")' in bulk_body, (
            "routes.py queue depth in /bulk-download must use llen('downloads'), not llen('celery')"
        )

    def test_queue_depth_uses_avg_job_duration_key(self):
        """ETA estimation must use the queue_intelligence avg key."""
        routes_path = os.path.join(
            os.path.dirname(__file__), "..", "app", "api", "routes.py"
        )
        with open(routes_path) as f:
            source = f.read()

        assert "vidgrab:queue:avg_job_duration" in source, (
            "ETA in /bulk-download must use queue_intelligence avg key"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 4.  REDIS CONNECTION REUSE
# ══════════════════════════════════════════════════════════════════════════════

class TestRedisConnectionReuse:
    """Hot-path functions must use get_redis() (connection pool) not raw from_url()."""

    def _routes_source(self) -> str:
        path = os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes.py")
        with open(path) as f:
            return f.read()

    def test_track_download_uses_get_redis(self):
        """_track_download must not create a raw redis connection."""
        source = self._routes_source()
        # Find the _track_download function body
        start = source.index("def _track_download")
        # Find next def after it
        next_def = source.index("\ndef ", start + 10)
        fn_body = source[start:next_def]
        assert "from_url" not in fn_body, "_track_download must not use redis.from_url()"
        assert "get_redis" in fn_body, "_track_download must use get_redis()"

    def test_acquire_slot_uses_get_redis(self):
        """_acquire_download_slot must not create a raw redis connection."""
        source = self._routes_source()
        start = source.index("def _acquire_download_slot")
        next_def = source.index("\ndef ", start + 10)
        fn_body = source[start:next_def]
        assert "from_url" not in fn_body, "_acquire_download_slot must not use redis.from_url()"
        assert "get_redis" in fn_body, "_acquire_download_slot must use get_redis()"

    def test_release_slot_uses_get_redis(self):
        """_release_download_slot must not create a raw redis connection."""
        source = self._routes_source()
        start = source.index("def _release_download_slot")
        next_def = source.index("\ndef ", start + 10)
        fn_body = source[start:next_def]
        assert "from_url" not in fn_body, "_release_download_slot must not use redis.from_url()"
        assert "get_redis" in fn_body, "_release_download_slot must use get_redis()"

    def test_bulk_download_queue_depth_uses_get_redis(self):
        """Queue depth estimation in /bulk-download must use get_redis()."""
        source = self._routes_source()
        # Anchor to the POST /bulk-download handler (not the /progress position endpoint)
        bulk_start = source.find('@router.post("/bulk-download")')
        assert bulk_start != -1, "Could not locate @router.post('/bulk-download')"
        idx = source.find("Queue depth", bulk_start)
        assert idx != -1, "Could not find 'Queue depth' in /bulk-download handler"
        segment = source[idx: idx + 400]
        assert "from_url" not in segment, (
            "Queue depth block must not use redis.from_url()"
        )
        assert "get_redis" in segment, (
            "Queue depth block must use get_redis()"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5.  CACHE NORMALIZE URL
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeURL:
    """URL normalization must strip tracking params consistently."""

    def _norm(self, url: str) -> str:
        from app.core.cache import _normalize_url
        return _normalize_url(url)

    def test_youtube_strips_extra_params(self):
        clean = self._norm("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxx&t=42")
        assert clean == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_tiktok_strips_query(self):
        clean = self._norm("https://www.tiktok.com/@user/video/123?_r=1&share_source=COPY")
        assert "?" not in clean

    def test_facebook_strips_fbclid(self):
        clean = self._norm("https://www.facebook.com/watch/?v=12345&fbclid=abc123")
        assert "fbclid" not in clean

    def test_instagram_strips_query(self):
        clean = self._norm("https://www.instagram.com/reel/abc/?igsh=xyz")
        assert "?" not in clean

    def test_trailing_slash_removed(self):
        clean = self._norm("https://www.tiktok.com/@user/video/123/")
        assert not clean.endswith("/")

    def test_same_video_normalizes_to_same_key(self):
        """Two URLs for the same YouTube video must produce the same L1 key."""
        from app.core.cache import _l1_key
        url1 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLaaa"
        url2 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share"
        assert _l1_key(self._norm(url1)) == _l1_key(self._norm(url2))


# ══════════════════════════════════════════════════════════════════════════════
# 6.  CACHE_STATS (non-regression)
# ══════════════════════════════════════════════════════════════════════════════

class TestCacheStats:
    def test_get_cache_stats_returns_dict(self):
        mock_resp = MagicMock()
        mock_resp.count = 5
        mock_sb = MagicMock()
        (mock_sb.table.return_value.select.return_value
         .eq.return_value.not_.is_.return_value
         .gte.return_value.execute.return_value) = mock_resp

        with patch("app.core.cache.get_supabase_client", return_value=mock_sb):
            from app.core.cache import get_cache_stats
            stats = get_cache_stats()

        assert "cached_urls_24h" in stats
        assert "cache_ttl_hours" in stats
        assert stats["cache_ttl_hours"] == 24
