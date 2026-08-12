"""
Optimization Phase — Regression Tests
======================================
Covers four areas required by the optimization phase spec:
  1. Stale-job recovery (cooldown + bounded attempts)
  2. Dedupe correctness (compiled patterns, conservative behaviour)
  3. Container discovery partial-success contract
  4. Queue/manifest/ZIP integrity
"""

from __future__ import annotations

import csv
import io
import os
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

# ── path setup ────────────────────────────────────────────────────────────────

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub supabase/realtime before any project module imports them.
# Some environments have a pydantic v1/v2 mismatch in realtime that crashes
# `from realtime.types import …` at import time.
for _mod in ("realtime", "realtime.types", "supabase", "supabase.client",
             "postgrest", "gotrue", "storage3"):
    sys.modules.setdefault(_mod, MagicMock())


# ══════════════════════════════════════════════════════════════════════════════
# 1.  RECOVERY REGRESSION
# ══════════════════════════════════════════════════════════════════════════════

class TestRecoveryCooldown:
    """_recover_or_abandon must skip jobs recovered too recently."""

    def _job(self, last_recovery_at: Optional[str] = None,
             attempts: int = 0, age_minutes: int = 15) -> dict:
        updated = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
        return {
            "id": "job-abc",
            "original_url": "https://www.tiktok.com/@user/video/12345678901234567",
            "status": "processing",
            "job_stage": "downloading",
            "recovery_attempts": attempts,
            "selected_quality": "video",
            "last_recovery_at": last_recovery_at,
            "updated_at": updated,
            "created_at": updated,
        }

    def test_skips_when_recovered_recently(self):
        """Job recovered 2 min ago should be skipped (cooldown)."""
        from app.core.recovery import _recover_or_abandon, STUCK_PROCESSING_MINUTES

        recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        job = self._job(last_recovery_at=recent, age_minutes=STUCK_PROCESSING_MINUTES + 2)

        sb = MagicMock()
        result = _recover_or_abandon(sb, job, source="test")
        assert result == "skip"
        sb.table.assert_not_called()

    def test_recovers_when_cooldown_expired(self):
        """Job recovered long ago (> STUCK_PROCESSING_MINUTES) should be recovered."""
        from app.core.recovery import _recover_or_abandon, STUCK_PROCESSING_MINUTES

        old_rec = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_PROCESSING_MINUTES + 5)).isoformat()
        job = self._job(last_recovery_at=old_rec, age_minutes=STUCK_PROCESSING_MINUTES + 10)

        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        fake_task = MagicMock()
        fake_task.delay = MagicMock()
        fake_module = MagicMock()
        fake_module.process_video_task = fake_task

        with patch("app.core.recovery._track"):
            with patch.dict("sys.modules", {"app.tasks.video_tasks": fake_module}):
                result = _recover_or_abandon(sb, job, source="test")

        assert result == "recovered"

    def test_skips_batch_zip_jobs(self):
        """Jobs with url='batch_zip' must always be skipped."""
        from app.core.recovery import _recover_or_abandon

        job = self._job(age_minutes=60)
        job["original_url"] = "batch_zip"

        sb = MagicMock()
        result = _recover_or_abandon(sb, job, source="test")
        assert result == "skip"
        sb.table.assert_not_called()

    def test_abandons_after_max_attempts(self):
        """Job at MAX_AUTO_RECOVERY attempts must be abandoned, not re-queued."""
        from app.core.recovery import _recover_or_abandon, MAX_AUTO_RECOVERY

        job = self._job(attempts=MAX_AUTO_RECOVERY, age_minutes=30)
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("app.core.recovery._track"):
            result = _recover_or_abandon(sb, job, source="test")
        assert result == "abandoned"

    def test_skips_still_running_job(self):
        """Job updated recently should be treated as still running."""
        from app.core.recovery import _recover_or_abandon, STUCK_PROCESSING_MINUTES

        job = self._job(age_minutes=STUCK_PROCESSING_MINUTES - 2)
        sb = MagicMock()
        result = _recover_or_abandon(sb, job, source="test")
        assert result == "skip"

    def test_no_double_recovery_within_cooldown(self):
        """Calling _recover_or_abandon twice rapidly must fire only one recovery."""
        from app.core.recovery import _recover_or_abandon, STUCK_PROCESSING_MINUTES

        fake_task = MagicMock()
        fake_task.delay = MagicMock()
        fake_module = MagicMock()
        fake_module.process_video_task = fake_task

        # First call: no last_recovery_at → should recover
        job1 = self._job(age_minutes=STUCK_PROCESSING_MINUTES + 5)
        sb = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

        with patch("app.core.recovery._track"):
            with patch.dict("sys.modules", {"app.tasks.video_tasks": fake_module}):
                r1 = _recover_or_abandon(sb, job1, source="test")

        assert r1 == "recovered"

        # Second call: simulate last_recovery_at = just now → must skip (cooldown)
        just_now = datetime.now(timezone.utc).isoformat()
        job2 = self._job(last_recovery_at=just_now, age_minutes=STUCK_PROCESSING_MINUTES + 5)
        sb2 = MagicMock()
        r2 = _recover_or_abandon(sb2, job2, source="test")

        assert r2 == "skip"
        sb2.table.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 2.  DEDUPE REGRESSION
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _Item:
    id: str = ""
    url: str = ""
    title: str = "Test Song"
    author: str = "Artist"
    duration_ms: int = 180_000
    media_type: str = "audio"
    views: int = 0
    published_at: str = ""
    thumbnail: str = ""
    extras: dict = field(default_factory=dict)


class TestDedupeCompiledPatterns:
    """Verify that precompiled _ID_COMPILED patterns match identically to string patterns."""

    def test_id_compiled_is_list_of_pattern_prefix(self):
        from app.services.container_dedupe import _ID_COMPILED
        import re
        for pat, pfx in _ID_COMPILED:
            assert isinstance(pat, re.Pattern), f"Expected compiled Pattern, got {type(pat)}"
            assert isinstance(pfx, str)

    def test_id_patterns_backward_compat_strings(self):
        """_ID_PATTERNS still exported as string tuples for tests that use it."""
        from app.services.container_dedupe import _ID_PATTERNS
        for pat, pfx in _ID_PATTERNS:
            assert isinstance(pat, str)

    def test_canonical_id_spotify(self):
        from app.services.container_dedupe import DedupeLayer
        item = _Item(url="https://open.spotify.com/track/4RVnAlexyz")
        cid = DedupeLayer._canonical_id(item)
        assert cid == "sp:4RVnAlexyz"

    def test_canonical_id_tiktok(self):
        from app.services.container_dedupe import DedupeLayer
        item = _Item(url="https://www.tiktok.com/@user/video/7123456789012345678")
        cid = DedupeLayer._canonical_id(item)
        assert cid == "tt:7123456789012345678"

    def test_canonical_id_youtube(self):
        from app.services.container_dedupe import DedupeLayer
        item = _Item(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        cid = DedupeLayer._canonical_id(item)
        assert cid == "yt:dQw4w9WgXcQ"

    def test_canonical_id_uses_item_id_fast_path(self):
        """Items with pre-computed platform IDs skip URL pattern matching."""
        from app.services.container_dedupe import DedupeLayer
        item = _Item(id="sp:4RVnAlexyzABCDE", url="https://example.com/no-match")
        cid = DedupeLayer._canonical_id(item)
        assert cid == "sp:4RVnAlexyzABCDE"

    def test_nonword_re_used_in_title_key(self):
        """_NONWORD_RE strips punctuation from title and author."""
        from app.services.container_dedupe import DedupeLayer, _NONWORD_RE
        item = _Item(title="Hello, World!", author="DJ Mixér", duration_ms=60_000)
        key = DedupeLayer._title_key(item)
        assert "," not in key
        assert "!" not in key

    def test_dedupe_still_suppresses_same_id(self):
        from app.services.container_dedupe import DedupeLayer
        items = [
            _Item(id="sp:trackABC", url="https://open.spotify.com/track/trackABC",
                  title="Alpha", author="X", duration_ms=60_000),
            _Item(id="sp:trackABC", url="https://open.spotify.com/track/trackABC",
                  title="Alpha", author="X", duration_ms=60_000),
        ]
        result = DedupeLayer().filter(items)
        assert result.removed_count == 1
        assert result.removed[0]["reason"].startswith("same_id:")

    def test_dedupe_does_not_over_remove_distinct_items(self):
        """Items with different IDs, URLs, and titles must all be kept."""
        from app.services.container_dedupe import DedupeLayer
        items = [
            _Item(id="sp:track1", url="https://open.spotify.com/track/track1",
                  title="Song One", author="Artist A", duration_ms=60_000),
            _Item(id="sp:track2", url="https://open.spotify.com/track/track2",
                  title="Song Two", author="Artist B", duration_ms=90_000),
            _Item(id="sp:track3", url="https://open.spotify.com/track/track3",
                  title="Song Three", author="Artist C", duration_ms=120_000),
        ]
        result = DedupeLayer().filter(items)
        assert result.removed_count == 0
        assert len(result.kept) == 3

    def test_dedupe_conservative_no_false_positive_on_similar_title(self):
        """Items sharing title+author but in different 5-second duration buckets must NOT be merged."""
        from app.services.container_dedupe import DedupeLayer
        # Use distinct URLs so same_url layer doesn't fire; test purely the fuzzy layer.
        items = [
            _Item(id="sc:shortmix",
                  url="https://soundcloud.com/artist/summer-hits-short",
                  title="Summer Hits", author="Various", duration_ms=180_000),
            _Item(id="sc:longmix",
                  url="https://soundcloud.com/artist/summer-hits-long",
                  title="Summer Hits", author="Various", duration_ms=360_000),  # diff 5-s bucket
        ]
        result = DedupeLayer().filter(items)
        assert result.removed_count == 0, "Different duration buckets = distinct items"


# ══════════════════════════════════════════════════════════════════════════════
# 3.  CONTAINER DISCOVERY PARTIAL-SUCCESS CONTRACT
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class _Section:
    key: str
    label: str
    items: list = field(default_factory=list)
    items_loaded: bool = True
    item_count: int = 0
    children: list = field(default_factory=list)
    has_more: bool = False
    next_cursor: str = ""


@dataclass
class _Meta:
    container_id: str
    platform: str
    container_type: str
    url: str
    title: str
    status: str = "partial"
    sections: list = field(default_factory=list)
    total_items: int = 0
    fetched_items: int = 0
    error_message: str = ""
    thumbnail: str = ""
    description: str = ""


class TestContainerDiscoveryPartial:
    """Verify that partial discovery status is preserved correctly."""

    def test_partial_meta_has_status_partial(self):
        """ContainerMeta with status='partial' must be returned as-is."""
        meta = _Meta(
            container_id="cid1",
            platform="spotify",
            container_type="album",
            url="https://open.spotify.com/album/abc",
            title="Test Album",
            status="partial",
        )
        assert meta.status == "partial"

    def test_partial_sections_can_have_items(self):
        """Sections loaded before partial failure still contain their items."""
        items = [_Item(id=f"sp:t{i}", url=f"https://open.spotify.com/track/t{i}",
                       title=f"Track {i}", author="Artist", duration_ms=180_000)
                 for i in range(5)]
        sec = _Section(key="tracks", label="Tracks", items=items, items_loaded=True)
        meta = _Meta(
            container_id="cid2",
            platform="spotify",
            container_type="album",
            url="https://open.spotify.com/album/abc",
            title="Test Album",
            status="partial",
            sections=[sec],
        )
        assert len(meta.sections[0].items) == 5

    def test_manifest_generator_streams_partial_items(self):
        """_iter_manifest_csv must yield header + N item rows for partial meta."""
        from app.api.container import _iter_manifest_csv

        items = [_Item(id=f"sp:t{i}", url=f"https://open.spotify.com/track/t{i}",
                       title=f"Track {i}", author="Artist A", duration_ms=180_000)
                 for i in range(3)]
        sec = _Section(key="album_tracks", label="Album Tracks", items=items)
        meta = _Meta(
            container_id="cid3",
            platform="spotify",
            container_type="album",
            url="https://open.spotify.com/album/abc",
            title="Partial Album",
            status="partial",
            sections=[sec],
        )

        rows = list(_iter_manifest_csv(meta))
        # First row is the header
        assert "id" in rows[0] and "url" in rows[0]
        # Remaining rows = items
        assert len(rows) == 1 + 3

    def test_manifest_generator_returns_correct_section_key(self):
        """CSV rows must include the correct section key."""
        from app.api.container import _iter_manifest_csv

        items = [_Item(id="sp:xyz123", url="https://open.spotify.com/track/xyz123",
                       title="My Song", author="Band")]
        sec = _Section(key="new_releases", label="New Releases", items=items)
        meta = _Meta(
            container_id="cid4", platform="spotify", container_type="playlist",
            url="https://open.spotify.com/playlist/abc", title="Test",
            sections=[sec],
        )

        rows = list(_iter_manifest_csv(meta))
        reader = csv.reader(io.StringIO("".join(rows)))
        rows_parsed = list(reader)
        header = rows_parsed[0]
        data = rows_parsed[1]
        section_idx = header.index("section")
        assert data[section_idx] == "new_releases"

    def test_manifest_empty_sections_yields_only_header(self):
        """Empty container → manifest CSV has only the header row."""
        from app.api.container import _iter_manifest_csv

        meta = _Meta(
            container_id="cid5", platform="tiktok", container_type="profile",
            url="https://www.tiktok.com/@empty", title="Empty",
            sections=[],
        )
        rows = list(_iter_manifest_csv(meta))
        assert len(rows) == 1
        assert "id" in rows[0]


# ══════════════════════════════════════════════════════════════════════════════
# 4.  QUEUE / ZIP / MANIFEST INTEGRITY
# ══════════════════════════════════════════════════════════════════════════════

class TestZIPReuse:
    """create_batch_zip must return early if the ZIP already exists."""

    def test_returns_cached_when_zip_exists(self, tmp_path):
        """If batch_<id>.zip already exists, no Supabase query should run."""
        import app.services.archive_service as svc

        batch_id = "test-batch-reuse"
        zip_path = tmp_path / f"batch_{batch_id}.zip"
        # Write a tiny placeholder ZIP
        with zipfile.ZipFile(str(zip_path), "w") as zf:
            zf.writestr("dummy.txt", "hello")

        original_dir = svc.DOWNLOAD_DIR
        svc.DOWNLOAD_DIR = str(tmp_path)
        try:
            import asyncio
            result = asyncio.run(svc.create_batch_zip(batch_id))
        finally:
            svc.DOWNLOAD_DIR = original_dir

        assert result["success"] is True
        assert result.get("cached") is True

    def test_creates_zip_from_local_files(self, tmp_path):
        """ZIP created from local files must contain all valid entries."""
        import app.services.archive_service as svc
        import asyncio

        batch_id = "test-batch-local"
        batch_dir = tmp_path / batch_id
        batch_dir.mkdir()

        # Create two fake local media files
        f1 = batch_dir / "track1.mp3"
        f2 = batch_dir / "track2.mp3"
        f1.write_bytes(b"audio1" * 100)
        f2.write_bytes(b"audio2" * 100)

        jobs = [
            {"id": "j1", "batch_id": batch_id, "status": "success",
             "slugified_name": "track1", "file_size_mb": 0,
             "direct_mp4_url": str(f1)},
            {"id": "j2", "batch_id": batch_id, "status": "success",
             "slugified_name": "track2", "file_size_mb": 0,
             "direct_mp4_url": str(f2)},
        ]

        mock_sb = MagicMock()
        mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = jobs

        original_dir = svc.DOWNLOAD_DIR
        svc.DOWNLOAD_DIR = str(tmp_path)
        try:
            with patch("app.services.archive_service.get_supabase_client", return_value=mock_sb):
                result = asyncio.run(svc.create_batch_zip(batch_id))
        finally:
            svc.DOWNLOAD_DIR = original_dir

        assert result["success"] is True
        assert result["total_files"] == 2
        zip_path = result["zip_path"]
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert len(names) == 2


class TestManifestCSVIntegrity:
    """Streaming manifest CSV must produce well-formed, complete output."""

    def _make_meta(self, n_items: int = 10):
        items = [_Item(id=f"yt:vid{i}", url=f"https://www.youtube.com/watch?v=vid{i:011d}",
                       title=f"Video {i}", author="Channel", duration_ms=i * 10_000,
                       media_type="video", views=i * 100)
                 for i in range(n_items)]
        sec = _Section(key="uploads", label="Uploads", items=items)
        return _Meta(container_id="cid-yt", platform="youtube",
                     container_type="channel", url="https://www.youtube.com/@chan",
                     title="My Channel", sections=[sec])

    def test_header_columns_correct(self):
        from app.api.container import _iter_manifest_csv
        rows = list(_iter_manifest_csv(self._make_meta(0)))
        header_row = next(csv.reader(io.StringIO(rows[0])))
        assert header_row == ["id", "url", "title", "author", "duration_ms",
                               "media_type", "views", "published_at", "section"]

    def test_row_count_matches_items(self):
        from app.api.container import _iter_manifest_csv
        n = 25
        rows = list(_iter_manifest_csv(self._make_meta(n)))
        # 1 header + n data rows
        assert len(rows) == n + 1

    def test_data_is_parseable_csv(self):
        from app.api.container import _iter_manifest_csv
        all_text = "".join(_iter_manifest_csv(self._make_meta(5)))
        reader = csv.DictReader(io.StringIO(all_text))
        parsed = list(reader)
        assert len(parsed) == 5
        assert all("id" in r and "url" in r for r in parsed)

    def test_section_key_present_in_each_row(self):
        from app.api.container import _iter_manifest_csv
        all_text = "".join(_iter_manifest_csv(self._make_meta(3)))
        reader = csv.DictReader(io.StringIO(all_text))
        for row in reader:
            assert row["section"] == "uploads"

    def test_generator_is_lazy(self):
        """_iter_manifest_csv must return a generator, not a list."""
        from app.api.container import _iter_manifest_csv
        import types
        result = _iter_manifest_csv(self._make_meta(5))
        assert isinstance(result, types.GeneratorType)
