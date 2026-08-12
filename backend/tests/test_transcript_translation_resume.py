import os
import sys

# test_phase13_subtitle.py permanently stubs sys.modules['app.core.database']
# (and a few other transitive deps) at collection time, with no teardown, to
# work around a pydantic/supabase-realtime conflict on the system Python. In
# this repo's actual backend/venv that conflict doesn't reproduce (app.main
# imports cleanly), so the stub is unnecessary noise here — but pytest
# collects both files into one process, and this file is the first to need
# the REAL app.core.database.get_service_client. Drop the stub so the
# following imports pull in the real module instead of the collection-order-
# dependent leftover.
sys.modules.pop("app.core.database", None)
sys.modules.pop("app.tasks.transcript_translation_tasks", None)

from app.tasks.transcript_translation_tasks import (
    _append_chunk_progress,
    _load_resume_state,
    _progress_file_for,
)


def test_progress_file_path_is_sibling_of_input():
    assert _progress_file_for("/tmp/x/job1/input.srt") == "/tmp/x/job1/progress.ndjson"


def test_load_resume_state_zero_returns_empty_list(tmp_path):
    progress_path = str(tmp_path / "progress.ndjson")
    assert _load_resume_state(progress_path, 0) == []


def test_load_resume_state_missing_file_returns_none(tmp_path):
    progress_path = str(tmp_path / "does-not-exist.ndjson")
    assert _load_resume_state(progress_path, 2) is None


def test_append_then_load_reconstructs_in_order(tmp_path):
    progress_path = str(tmp_path / "progress.ndjson")
    _append_chunk_progress(progress_path, 0, ["a1", "a2"])
    _append_chunk_progress(progress_path, 1, ["b1", "b2", "b3"])

    result = _load_resume_state(progress_path, 2)
    assert result == ["a1", "a2", "b1", "b2", "b3"]


def test_load_resume_state_missing_middle_chunk_returns_none(tmp_path):
    progress_path = str(tmp_path / "progress.ndjson")
    _append_chunk_progress(progress_path, 0, ["a1"])
    # chunk_index 1 never written (crash happened mid-chunk-1)
    _append_chunk_progress(progress_path, 2, ["c1"])

    # Caller expects chunks 0,1,2 to be resumable (resume_chunk_index=3) but
    # chunk 1 is missing — must not silently reconstruct a wrong/short list.
    assert _load_resume_state(progress_path, 3) is None


def test_load_resume_state_only_needs_chunks_up_to_resume_index(tmp_path):
    progress_path = str(tmp_path / "progress.ndjson")
    _append_chunk_progress(progress_path, 0, ["a1"])
    _append_chunk_progress(progress_path, 1, ["b1"])

    # Only resuming through chunk 0 — chunk 1 being present is irrelevant/ignored.
    assert _load_resume_state(progress_path, 1) == ["a1"]


def test_append_chunk_progress_survives_missing_directory(tmp_path):
    # Directory doesn't exist — must not raise (best-effort per docstring).
    progress_path = str(tmp_path / "nonexistent-dir" / "progress.ndjson")
    _append_chunk_progress(progress_path, 0, ["a1"])  # should not raise
    assert not os.path.exists(progress_path)
