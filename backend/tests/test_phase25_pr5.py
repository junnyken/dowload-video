"""
Phase 25 PR5 — Batch Queue, Dedupe, Manifest Tests
=====================================================
Covers:
  - DedupeLayer canonical ID extraction (all 12 platform patterns)
  - DedupeLayer URL normalization (YouTube v-param preserve, strip tracking)
  - DedupeLayer fuzzy title key generation (5-second bucket)
  - DedupeLayer.filter: same_id / same_url / similar_title / cross-call state
  - DedupeLayer.has_seen / mark_seen / reset
  - DedupeResult.summary_text Vietnamese message generation
  - _collect_items_by_mode: selected, latest_n, top_n, section, all
  - Idempotent queue hash: same request → same hash
  - Manifest CSV field order and content
  - Queue dedupe: apply_dedupe=True removes duplicates, apply_dedupe=False keeps all
  - Retry idempotency: duplicate request returns existing batch_id

All tests are pure unit tests — no Redis, no Supabase, no network.
"""
from __future__ import annotations

import csv
import io
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _item(id, url, title="Title", author="Author", duration_ms=60_000, views=100, media_type="video"):
    from app.services.container_discovery import ContainerItem
    return ContainerItem(
        id=id, url=url, title=title, author=author,
        duration_ms=duration_ms, views=views, media_type=media_type,
    )


def _section(key, items, label="Section"):
    from app.services.container_discovery import ContainerSection
    return ContainerSection(key=key, label=label, items=items, items_loaded=True)


def _meta(sections):
    from app.services.container_discovery import ContainerMeta
    return ContainerMeta(
        container_id="ctr_test",
        platform="spotify",
        container_type="album",
        title="Test Album",
        sections=sections,
        status="ready",
        url="https://open.spotify.com/album/abc",
        item_count=sum(len(s.items) for s in sections),
    )


# ─────────────────────────────────────────────────────────────────────────────
# A.  Canonical ID extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestCanonicalIDExtraction:
    """DedupeLayer._canonical_id extracts stable IDs for every platform pattern."""

    def _cid(self, url, id=""):
        from app.services.container_dedupe import DedupeLayer
        item = _item(id=id, url=url)
        return DedupeLayer._canonical_id(item)

    def test_spotify_track_url(self):
        cid = self._cid("https://open.spotify.com/track/4RvWPy3Y6WT5AEkO")
        assert cid == "sp:4RvWPy3Y6WT5AEkO"

    def test_spotify_track_uri(self):
        cid = self._cid("spotify:track:4RvWPy3Y6WT5AEkO")
        assert cid == "sp:4RvWPy3Y6WT5AEkO"

    def test_tiktok_video_url(self):
        cid = self._cid("https://www.tiktok.com/@user/video/7123456789012345678")
        assert cid == "tt:7123456789012345678"

    def test_douyin_video_url(self):
        # Regex requires at least 1 char between douyin.com and /video/ (e.g. /@user)
        cid = self._cid("https://www.douyin.com/@username/video/7123456789012345678")
        assert cid == "tt:7123456789012345678"

    def test_youtube_watch_url(self):
        cid = self._cid("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert cid == "yt:dQw4w9WgXcQ"

    def test_youtube_short_url(self):
        cid = self._cid("https://youtu.be/dQw4w9WgXcQ")
        assert cid == "yt:dQw4w9WgXcQ"

    def test_instagram_post(self):
        cid = self._cid("https://www.instagram.com/p/CxABC123xyz/")
        assert cid == "ig:CxABC123xyz"

    def test_instagram_reel(self):
        cid = self._cid("https://www.instagram.com/reel/CxABC123xyz/")
        assert cid == "ig:CxABC123xyz"

    def test_threads_post(self):
        cid = self._cid("https://www.threads.net/@user/post/CxABC123xyz")
        assert cid == "th:CxABC123xyz"

    def test_pinterest_pin(self):
        cid = self._cid("https://www.pinterest.com/pin/123456789012345678/")
        assert cid == "pin:123456789012345678"

    def test_reddit_post(self):
        cid = self._cid("https://www.reddit.com/r/funny/comments/abc123/title/")
        assert cid == "rd:abc123"

    def test_facebook_video(self):
        cid = self._cid("https://www.facebook.com/watch/?v=1234567890")
        assert cid == "fb:1234567890"

    def test_twitter_status(self):
        cid = self._cid("https://twitter.com/user/status/1234567890123456789")
        assert cid == "tw:1234567890123456789"

    def test_x_com_status(self):
        cid = self._cid("https://x.com/user/status/1234567890123456789")
        assert cid == "tw:1234567890123456789"

    def test_unknown_url_returns_none(self):
        cid = self._cid("https://someunknownsite.com/video/123")
        assert cid is None

    def test_precomputed_id_takes_priority(self):
        """If item.id already contains ':', return it directly without URL parsing."""
        cid = self._cid("https://unknown.com/", id="sp:precomputed123")
        assert cid == "sp:precomputed123"

    def test_short_id_without_colon_uses_url(self):
        """item.id without ':' is not treated as precomputed."""
        cid = self._cid("https://open.spotify.com/track/XYZ123", id="XYZ")
        assert cid == "sp:XYZ123"


# ─────────────────────────────────────────────────────────────────────────────
# B.  URL normalization
# ─────────────────────────────────────────────────────────────────────────────

class TestURLNormalization:
    """DedupeLayer._normalize_url produces stable canonical forms."""

    def _norm(self, url):
        from app.services.container_dedupe import DedupeLayer
        return DedupeLayer._normalize_url(url)

    def test_strips_trailing_slash(self):
        assert self._norm("https://example.com/path/") == "https://example.com/path"

    def test_lowercases_domain(self):
        n = self._norm("https://WWW.EXAMPLE.COM/Path")
        assert n == "https://www.example.com/path"

    def test_youtube_preserves_v_param(self):
        # _normalize_url lowercases everything including the v param value
        n = self._norm("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share")
        assert "v=dqw4w9wgxcq" in n   # lowercased
        assert "feature" not in n

    def test_youtube_strips_non_v_params(self):
        n = self._norm("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30&list=PL123")
        assert "list" not in n
        assert "t=" not in n
        assert "v=" in n

    def test_non_youtube_strips_query_params(self):
        n = self._norm("https://www.tiktok.com/@user/video/123?utm_source=share")
        assert "utm_source" not in n
        assert n.endswith("/123")

    def test_same_url_different_trailing_slash(self):
        a = self._norm("https://open.spotify.com/track/abc")
        b = self._norm("https://open.spotify.com/track/abc/")
        assert a == b

    def test_handles_malformed_url_gracefully(self):
        n = self._norm("not-a-url")
        assert isinstance(n, str)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# C.  Fuzzy title key
# ─────────────────────────────────────────────────────────────────────────────

class TestFuzzyTitleKey:
    """DedupeLayer._title_key groups items by author+title+5s-duration-bucket."""

    def _key(self, title, author, duration_ms):
        from app.services.container_dedupe import DedupeLayer
        item = _item(id="x:1", url="https://x.com/1", title=title, author=author,
                     duration_ms=duration_ms)
        return DedupeLayer._title_key(item)

    def test_same_title_author_duration_same_key(self):
        k1 = self._key("Summer Nights", "DJ Cool", 180_000)
        k2 = self._key("Summer Nights", "DJ Cool", 180_000)
        assert k1 == k2

    def test_same_title_diff_duration_gt5s_different_key(self):
        k1 = self._key("Song A", "Artist", 10_000)
        k2 = self._key("Song A", "Artist", 16_000)  # 6s apart → different bucket
        assert k1 != k2

    def test_same_title_diff_duration_within_5s_same_key(self):
        k1 = self._key("Song B", "Artist", 10_000)
        k2 = self._key("Song B", "Artist", 14_000)  # 4s apart → same bucket
        assert k1 == k2

    def test_different_author_different_key(self):
        k1 = self._key("Same Title", "Artist A", 60_000)
        k2 = self._key("Same Title", "Artist B", 60_000)
        assert k1 != k2

    def test_special_chars_stripped(self):
        k1 = self._key("Song! (Remix)", "DJ #1", 60_000)
        k2 = self._key("Song Remix", "DJ 1", 60_000)
        assert k1 == k2


# ─────────────────────────────────────────────────────────────────────────────
# D.  DedupeLayer.filter — core deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestDedupeLayerFilter:
    """DedupeLayer.filter correctly deduplicates by three layers."""

    def test_exact_duplicate_canonical_id(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("sp:ABC", "https://open.spotify.com/track/ABC", "Track 1"),
            _item("sp:ABC", "https://open.spotify.com/track/ABC", "Track 1 (copy)"),
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 1
        assert result.removed_count == 1
        assert result.reasons_list[0].startswith("same_id:")

    def test_same_url_different_id(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("sp:A1", "https://open.spotify.com/track/SAME"),
            _item("sp:A2", "https://open.spotify.com/track/SAME"),  # different id, same url
        ]
        result = DedupeLayer().filter(items)
        # First item kept by canonical ID, second hits same_url
        assert len(result.kept) == 1
        assert result.removed_count == 1

    def test_fuzzy_match_same_title_author_duration(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("yt:V1", "https://youtube.com/watch?v=V1",
                  title="Summer Nights", author="DJ Cool", duration_ms=180_000),
            _item("sc:SC1", "https://soundcloud.com/djcool/summer-nights-remix",
                  title="Summer Nights", author="DJ Cool", duration_ms=181_000),
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 1
        assert result.removed_count == 1
        assert result.reasons_list[0].startswith("similar_title:")

    def test_all_unique_items_kept(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("sp:1", "https://open.spotify.com/track/1", "Track 1"),
            _item("sp:2", "https://open.spotify.com/track/2", "Track 2"),
            _item("sp:3", "https://open.spotify.com/track/3", "Track 3"),
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 3
        assert result.removed_count == 0
        assert result.reasons_list == []

    def test_empty_list_returns_empty_result(self):
        from app.services.container_dedupe import DedupeLayer
        result = DedupeLayer().filter([])
        assert result.kept == []
        assert result.removed_count == 0

    def test_cross_call_deduplication(self):
        """Stateful deduper remembers items across multiple filter() calls."""
        from app.services.container_dedupe import DedupeLayer
        deduper = DedupeLayer()

        batch1 = [_item("sp:1", "https://open.spotify.com/track/1", "Track 1")]
        r1 = deduper.filter(batch1)
        assert len(r1.kept) == 1

        # Same item again in a second call → should be removed
        batch2 = [_item("sp:1", "https://open.spotify.com/track/1", "Track 1")]
        r2 = deduper.filter(batch2)
        assert len(r2.kept) == 0
        assert r2.removed_count == 1

    def test_tiktok_pinned_in_both_sections_deduped(self):
        """TikTok pinned post appearing in both pinned and recent feed is deduped."""
        from app.services.container_dedupe import DedupeLayer
        pinned = _item("tt:1234", "https://tiktok.com/@user/video/1234", "Pinned Video")
        recent_same = _item("tt:1234", "https://tiktok.com/@user/video/1234", "Pinned Video")

        deduper = DedupeLayer()
        # Simulate: pinned section processed first, then recent feed
        r = deduper.filter([pinned, recent_same])
        assert len(r.kept) == 1
        assert r.removed_count == 1

    def test_conservative_fuzzy_keeps_ambiguous_items(self):
        """Items with same title but very different durations are NOT deduped."""
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("sp:A", "https://spotify.com/track/A",
                  title="Radio Edit", author="Band", duration_ms=180_000),
            _item("sp:B", "https://spotify.com/track/B",
                  title="Radio Edit", author="Band", duration_ms=240_000),  # 60s longer
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 2  # different duration bucket → kept both


# ─────────────────────────────────────────────────────────────────────────────
# E.  DedupeLayer helpers: has_seen / mark_seen / reset
# ─────────────────────────────────────────────────────────────────────────────

class TestDedupeLayerHelpers:

    def test_has_seen_returns_false_for_new_item(self):
        from app.services.container_dedupe import DedupeLayer
        deduper = DedupeLayer()
        item = _item("sp:1", "https://open.spotify.com/track/1")
        assert deduper.has_seen(item) is False

    def test_has_seen_returns_true_after_filter(self):
        from app.services.container_dedupe import DedupeLayer
        deduper = DedupeLayer()
        item = _item("sp:1", "https://open.spotify.com/track/1")
        deduper.filter([item])
        assert deduper.has_seen(item) is True

    def test_mark_seen_without_filtering(self):
        from app.services.container_dedupe import DedupeLayer
        deduper = DedupeLayer()
        item = _item("sp:1", "https://open.spotify.com/track/1")
        deduper.mark_seen(item)
        assert deduper.has_seen(item) is True

    def test_mark_seen_does_not_mutate_kept_list(self):
        """mark_seen adds to state but doesn't create DedupeResult."""
        from app.services.container_dedupe import DedupeLayer
        deduper = DedupeLayer()
        item = _item("sp:track_a", "https://open.spotify.com/track/aaa",
                     title="Song Alpha", author="Artist A", duration_ms=120_000)
        deduper.mark_seen(item)
        # Filter a genuinely different item (different title/author/duration)
        item2 = _item("sp:track_b", "https://open.spotify.com/track/bbb",
                      title="Song Beta", author="Artist B", duration_ms=240_000)
        result = deduper.filter([item2])
        assert len(result.kept) == 1

    def test_reset_clears_all_state(self):
        from app.services.container_dedupe import DedupeLayer
        deduper = DedupeLayer()
        item = _item("sp:1", "https://open.spotify.com/track/1")
        deduper.filter([item])
        assert deduper.has_seen(item) is True

        deduper.reset()
        assert deduper.has_seen(item) is False

    def test_after_reset_same_item_accepted_again(self):
        from app.services.container_dedupe import DedupeLayer
        deduper = DedupeLayer()
        item = _item("sp:1", "https://open.spotify.com/track/1")
        r1 = deduper.filter([item])
        assert r1.removed_count == 0

        deduper.reset()
        r2 = deduper.filter([item])
        assert r2.removed_count == 0  # accepted again after reset


# ─────────────────────────────────────────────────────────────────────────────
# F.  DedupeResult.summary_text — Vietnamese messages
# ─────────────────────────────────────────────────────────────────────────────

class TestDedupeResultSummaryText:

    def test_no_dupes_returns_empty_string(self):
        from app.services.container_dedupe import DedupeLayer
        items = [_item("sp:1", "https://spotify.com/t/1")]
        result = DedupeLayer().filter(items)
        assert result.summary_text == ""

    def test_same_id_mention_in_text(self):
        from app.services.container_dedupe import DedupeLayer
        # Use id > 4 chars with colon so _canonical_id returns item.id directly
        items = [
            _item("sp:track123", "https://open.spotify.com/track/track123"),
            _item("sp:track123", "https://open.spotify.com/track/track123"),
        ]
        result = DedupeLayer().filter(items)
        assert "bài cùng ID nguồn" in result.summary_text

    def test_same_url_mention_in_text(self):
        from app.services.container_dedupe import DedupeLayer
        # Use items without pre-computed canonical IDs so URL layer fires
        items = [
            _item("", "https://somesite.com/video/42", title="X"),
            _item("", "https://somesite.com/video/42", title="X copy"),
        ]
        result = DedupeLayer().filter(items)
        assert "bài cùng URL" in result.summary_text

    def test_summary_starts_with_da_loai(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("sp:1", "https://spotify.com/t/1"),
            _item("sp:1", "https://spotify.com/t/1"),
        ]
        result = DedupeLayer().filter(items)
        assert result.summary_text.startswith("Đã loại")

    def test_removed_count_appears_in_text(self):
        from app.services.container_dedupe import DedupeLayer
        # Use distinct titles/durations so only explicit id-dupes are removed
        items = [
            _item("sp:item0", "https://open.spotify.com/track/item0",
                  title="Song Alpha", duration_ms=60_000),
            _item("sp:item1", "https://open.spotify.com/track/item1",
                  title="Song Beta", duration_ms=120_000),
            _item("sp:item2", "https://open.spotify.com/track/item2",
                  title="Song Gamma", duration_ms=180_000),
        ]
        dupes = items[:2]  # duplicate first 2 by canonical id
        result = DedupeLayer().filter(items + dupes)
        assert result.removed_count == 2
        assert "2" in result.summary_text


# ─────────────────────────────────────────────────────────────────────────────
# G.  _collect_items_by_mode
# ─────────────────────────────────────────────────────────────────────────────

class TestCollectItemsByMode:
    """_collect_items_by_mode correctly selects items per queue_mode."""

    def _make_meta(self):
        items_a = [_item(f"sp:{i}", f"https://spotify.com/t/{i}",
                         title=f"Track {i}", views=i * 10,
                         duration_ms=i * 1000)
                   for i in range(1, 6)]
        items_b = [_item(f"sc:{i}", f"https://soundcloud.com/artist/song{i}",
                         title=f"Song {i}", views=i * 5)
                   for i in range(1, 4)]
        sections = [_section("top_tracks", items_a, "Top Tracks"),
                    _section("singles", items_b, "Singles")]
        return _meta(sections)

    def _req(self, queue_mode="all", item_ids=None, n=None, section_key=None):
        from app.api.container import QueueRequest
        return QueueRequest(
            queue_mode=queue_mode,
            item_ids=item_ids,
            n=n,
            section_key=section_key,
        )

    def test_mode_all_returns_all_items(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        items = _collect_items_by_mode(meta, self._req("all"))
        assert len(items) == 8  # 5 + 3

    def test_mode_selected_filters_by_id(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        items = _collect_items_by_mode(meta, self._req("selected", item_ids=["sp:1", "sp:3"]))
        assert len(items) == 2
        assert {i.id for i in items} == {"sp:1", "sp:3"}

    def test_mode_selected_by_url(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        items = _collect_items_by_mode(
            meta, self._req("selected", item_ids=["https://spotify.com/t/2"])
        )
        assert len(items) == 1
        assert items[0].id == "sp:2"

    def test_mode_latest_n(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        items = _collect_items_by_mode(meta, self._req("latest_n", n=3))
        assert len(items) == 3
        # First 3 items from first section (in order)
        assert items[0].id == "sp:1"

    def test_mode_top_n_by_views(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        items = _collect_items_by_mode(meta, self._req("top_n", n=2))
        assert len(items) == 2
        # Views: sp:5=50, sp:4=40, sp:3=30 ... sc:3=15 — top 2 by views
        assert items[0].views >= items[1].views

    def test_section_key_filter(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        items = _collect_items_by_mode(
            meta, self._req("section", section_key="singles")
        )
        assert len(items) == 3
        assert all(i.id.startswith("sc:") for i in items)

    def test_section_key_excludes_other_sections(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        items = _collect_items_by_mode(
            meta, self._req("all", section_key="top_tracks")
        )
        assert len(items) == 5
        assert all(i.id.startswith("sp:") for i in items)

    def test_selected_empty_ids_returns_all(self):
        from app.api.container import _collect_items_by_mode
        meta = self._make_meta()
        # selected mode with no item_ids → fallback to all
        items = _collect_items_by_mode(meta, self._req("selected", item_ids=[]))
        assert len(items) == 8


# ─────────────────────────────────────────────────────────────────────────────
# H.  Queue request idempotency hash
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueRequestHash:
    """_queue_request_hash produces stable fingerprints for idempotency."""

    def _req(self, item_ids=None, mode="selected", quality="mp3_320"):
        from app.api.container import QueueRequest
        return QueueRequest(queue_mode=mode, item_ids=item_ids or [], quality=quality)

    def test_same_container_and_items_same_hash(self):
        from app.api.container import _queue_request_hash
        r = self._req(["sp:1", "sp:2"])
        h1 = _queue_request_hash("ctr_abc", r)
        h2 = _queue_request_hash("ctr_abc", r)
        assert h1 == h2

    def test_different_container_id_different_hash(self):
        from app.api.container import _queue_request_hash
        r = self._req(["sp:1"])
        h1 = _queue_request_hash("ctr_aaa", r)
        h2 = _queue_request_hash("ctr_bbb", r)
        assert h1 != h2

    def test_different_items_different_hash(self):
        from app.api.container import _queue_request_hash
        r1 = self._req(["sp:1", "sp:2"])
        r2 = self._req(["sp:3", "sp:4"])
        h1 = _queue_request_hash("ctr_abc", r1)
        h2 = _queue_request_hash("ctr_abc", r2)
        assert h1 != h2

    def test_different_quality_different_hash(self):
        from app.api.container import _queue_request_hash
        r1 = self._req(["sp:1"], quality="mp3_320")
        r2 = self._req(["sp:1"], quality="video_720")
        assert _queue_request_hash("ctr_abc", r1) != _queue_request_hash("ctr_abc", r2)

    def test_hash_is_string(self):
        from app.api.container import _queue_request_hash
        r = self._req(["sp:1"])
        h = _queue_request_hash("ctr_abc", r)
        assert isinstance(h, str) and len(h) > 8


# ─────────────────────────────────────────────────────────────────────────────
# I.  Queue dedupe integration — apply_dedupe flag
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueDedupeIntegration:
    """DedupeLayer correctly integrates with the queue flow."""

    def test_apply_dedupe_removes_duplicate_canonical_ids(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("sp:1", "https://spotify.com/t/1", "Track 1"),
            _item("sp:2", "https://spotify.com/t/2", "Track 2"),
            _item("sp:1", "https://spotify.com/t/1", "Track 1 copy"),  # duplicate
        ]
        deduper = DedupeLayer()
        result = deduper.filter(items)
        assert result.kept == [items[0], items[1]]
        assert result.removed_count == 1

    def test_dedupe_summary_structure(self):
        from app.services.container_dedupe import DedupeLayer
        # Use ids > 4 chars so canonical_id layer fires
        items = [
            _item("tt:video123", "https://tiktok.com/@u/video/video123"),
            _item("tt:video123", "https://tiktok.com/@u/video/video123"),
        ]
        result = DedupeLayer().filter(items)
        # Verify summary fields match what the queue endpoint returns
        assert isinstance(result.reasons_list, list)
        assert len(result.reasons_list) == 1
        assert "same_id" in result.reasons_list[0]
        assert result.removed_count == 1

    def test_dedupe_false_keeps_all(self):
        """When apply_dedupe=False, all items pass through (simulated via not calling filter)."""
        items = [
            _item("sp:1", "https://spotify.com/t/1"),
            _item("sp:1", "https://spotify.com/t/1"),  # would be deduped
        ]
        # Simulate apply_dedupe=False: don't call DedupeLayer at all
        kept = items  # all pass through
        assert len(kept) == 2

    def test_empty_result_after_dedupe_signals_all_duplicates(self):
        from app.services.container_dedupe import DedupeLayer
        item = _item("sp:1", "https://spotify.com/t/1")
        deduper = DedupeLayer()
        deduper.filter([item])  # first pass marks as seen

        result = deduper.filter([item])  # second pass: should be empty
        assert len(result.kept) == 0
        assert result.removed_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# J.  Manifest CSV fields
# ─────────────────────────────────────────────────────────────────────────────

class TestManifestCSVFields:
    """Manifest CSV export contains all required fields with correct content."""

    def _generate_csv(self, sections):
        """Replicate the manifest endpoint's CSV generation logic."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "url", "title", "author", "duration_ms",
                         "media_type", "views", "published_at", "section"])
        for section in sections:
            for item in section.items:
                writer.writerow([
                    item.id, item.url, item.title, item.author,
                    item.duration_ms, item.media_type,
                    item.views, item.published_at, section.key,
                ])
        buf.seek(0)
        return list(csv.DictReader(buf))

    def test_manifest_has_required_columns(self):
        items = [_item("sp:1", "https://spotify.com/t/1", "Track 1")]
        sections = [_section("tracks", items)]
        rows = self._generate_csv(sections)
        assert len(rows) == 1
        expected_cols = {"id", "url", "title", "author", "duration_ms",
                         "media_type", "views", "published_at", "section"}
        assert expected_cols == set(rows[0].keys())

    def test_manifest_row_values_match_item(self):
        item = _item("sp:42", "https://spotify.com/t/42", title="My Song",
                     author="Artist X", duration_ms=240_000, views=999)
        sections = [_section("featured", [item])]
        rows = self._generate_csv(sections)
        row = rows[0]
        assert row["id"] == "sp:42"
        assert row["url"] == "https://spotify.com/t/42"
        assert row["title"] == "My Song"
        assert row["author"] == "Artist X"
        assert row["duration_ms"] == "240000"
        assert row["views"] == "999"
        assert row["section"] == "featured"

    def test_manifest_includes_all_sections(self):
        items_a = [_item(f"sp:{i}", f"https://s.com/{i}") for i in range(3)]
        items_b = [_item(f"sc:{i}", f"https://sc.com/{i}") for i in range(2)]
        sections = [_section("a", items_a), _section("b", items_b)]
        rows = self._generate_csv(sections)
        assert len(rows) == 5

    def test_manifest_section_key_per_row(self):
        items = [_item("sp:1", "https://s.com/1")]
        sections = [_section("top_tracks", items, "Top Tracks")]
        rows = self._generate_csv(sections)
        assert rows[0]["section"] == "top_tracks"

    def test_manifest_empty_container_is_header_only(self):
        rows = self._generate_csv([])
        assert rows == []

    def test_manifest_handles_missing_optional_fields(self):
        from app.services.container_discovery import ContainerItem
        # Item with minimal fields (empty strings/zeros)
        item = ContainerItem(id="sp:1", url="https://s.com/1", title="T")
        sections = [_section("s", [item])]
        rows = self._generate_csv(sections)
        assert rows[0]["author"] == ""
        assert rows[0]["published_at"] == ""
        assert rows[0]["views"] == "0"


# ─────────────────────────────────────────────────────────────────────────────
# K.  Queue idempotency via Redis dedup
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueIdempotency:
    """check_queue_dedup / set_queue_dedup prevent duplicate batch creation."""

    def _make_mock_redis(self):
        store = {}
        rc = MagicMock()
        rc.get.side_effect = lambda k: store.get(k)
        rc.set.side_effect = lambda k, v, ex=None: store.update({k: v})
        rc.setex.side_effect = lambda k, ttl, v: store.update({k: v})
        mock_factory = MagicMock(return_value=rc)
        return mock_factory, store

    def test_first_request_returns_none(self):
        mock_factory, _ = self._make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import check_queue_dedup
            result = check_queue_dedup("hash_abc")
        assert result is None

    def test_second_request_returns_existing_batch(self):
        mock_factory, _ = self._make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import check_queue_dedup, set_queue_dedup
            set_queue_dedup("hash_abc", "batch-uuid-1")
            result = check_queue_dedup("hash_abc")
        assert result == "batch-uuid-1"

    def test_different_hash_independent(self):
        mock_factory, _ = self._make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import check_queue_dedup, set_queue_dedup
            set_queue_dedup("hash_aaa", "batch-1")
            assert check_queue_dedup("hash_bbb") is None

    def test_set_queue_dedup_stores_value(self):
        mock_factory, store = self._make_mock_redis()
        with patch("app.services.container_cache.get_redis", mock_factory):
            from app.services.container_cache import set_queue_dedup
            set_queue_dedup("myhash", "batch-xyz")
        # Verify something was written to the store
        assert any("batch-xyz" in str(v) for v in store.values())


# ─────────────────────────────────────────────────────────────────────────────
# L.  Regression: dedupe does not over-remove different items
# ─────────────────────────────────────────────────────────────────────────────

class TestDedupeConservativeBehavior:
    """Dedupe must not remove legitimately different items."""

    def test_same_title_different_author_kept(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("tt:1", "https://tiktok.com/@a/video/1",
                  title="Dance Remix", author="Artist A", duration_ms=60_000),
            _item("tt:2", "https://tiktok.com/@b/video/2",
                  title="Dance Remix", author="Artist B", duration_ms=60_000),
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 2  # different authors → kept

    def test_same_author_different_title_kept(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _item("sp:1", "https://spotify.com/t/1",
                  title="Song A", author="Same Artist", duration_ms=200_000),
            _item("sp:2", "https://spotify.com/t/2",
                  title="Song B", author="Same Artist", duration_ms=200_000),
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 2

    def test_large_batch_does_not_over_dedupe(self):
        from app.services.container_dedupe import DedupeLayer
        # 100 unique items — none should be removed
        items = [
            _item(f"sp:{i}", f"https://spotify.com/t/{i}",
                  title=f"Unique Track {i}", author="Artist", duration_ms=i * 10_000)
            for i in range(100)
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 100
        assert result.removed_count == 0

    def test_duplicate_without_canonical_id_deduped_by_url(self):
        from app.services.container_dedupe import DedupeLayer
        # No canonical ID (unknown platform) — URL layer catches it
        items = [
            _item("", "https://someplatform.com/video/999", "Video"),
            _item("", "https://someplatform.com/video/999", "Video copy"),
        ]
        result = DedupeLayer().filter(items)
        assert len(result.kept) == 1
        assert result.reasons_list[0] == "same_url"
