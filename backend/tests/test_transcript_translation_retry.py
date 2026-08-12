"""
Tests for the T3-C bounded self-requeue-on-transient-failure behavior in
translate_transcript_task: SoftTimeLimitExceeded / TranslationAlignmentError
must requeue from the checkpoint (preserving job_dir files) instead of being
treated as a hard failure that deletes them — until retries are exhausted.
"""
from __future__ import annotations

import sys

# See test_transcript_translation_resume.py — test_phase13_subtitle.py
# permanently stubs sys.modules['app.core.database'] with no teardown, and
# test_observability.py does the same (via sys.modules.setdefault, so it
# only "wins" if collected first) for 'app.core.celery_app'. This file needs
# BOTH real (the real celery_app is required for the @celery_app.task(...)
# decorator to actually wrap our function instead of a MagicMock that
# discards its body — otherwise translate_transcript_task(job_id) returns a
# MagicMock instead of running any of our logic). Confirmed both import
# cleanly for real in this venv (app.main imports fine all session).
sys.modules.pop("app.core.database", None)
sys.modules.pop("app.core.celery_app", None)
sys.modules.pop("app.tasks.transcript_translation_tasks", None)

import os
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.tasks.transcript_translation_tasks import translate_transcript_task


def _make_job(job_id="job-1", **overrides):
    job = {
        "id": job_id,
        "status": "queued",
        "source_lang": None,
        "target_lang": "vi",
        "input_path": None,  # filled in by fixture
        "source_format": "srt",
        "resume_chunk_index": 0,
        "timeout_retry_count": 0,
    }
    job.update(overrides)
    return job


def _make_db(job_row):
    """A MagicMock Supabase-like client where every .table(...) call chains
    to the same mock, configured so loading the job returns `job_row` and
    every other terminal call (.execute()) succeeds with empty/no-op data."""
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = job_row
    # Cache lookup path (transcript_translation_cache.select(...).in_(...).execute())
    db.table.return_value.select.return_value.in_.return_value.execute.return_value.data = []
    return db


@pytest.fixture
def srt_file(tmp_path):
    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    input_path = job_dir / "input.srt"
    input_path.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nWorld\n",
        encoding="utf-8",
    )
    return str(input_path)


def test_soft_time_limit_requeues_and_preserves_job_dir(srt_file):
    job_row = _make_job(input_path=srt_file, timeout_retry_count=0)
    db = _make_db(job_row)

    with patch("app.tasks.transcript_translation_tasks.get_service_client", return_value=db), \
         patch("app.services.transcript_translation_service.detect_source_language", return_value="English"), \
         patch("app.services.transcript_translation_service.translate_cues", side_effect=SoftTimeLimitExceeded()), \
         patch("app.services.transcript_translation_cache.lookup_many", return_value={}), \
         patch("app.services.transcript_translation_cache.store_many"), \
         patch("app.tasks.transcript_translation_tasks.translate_transcript_task.apply_async") as mock_requeue:

        result = translate_transcript_task(job_row["id"])

    assert result["status"] == "requeued"
    assert result["retry_count"] == 1
    mock_requeue.assert_called_once_with(args=[job_row["id"]])

    # The whole point of T3-C: job_dir (and its checkpoint file, if any) must
    # survive a transient failure so the requeued run can resume.
    assert os.path.isdir(os.path.dirname(srt_file))
    assert os.path.isfile(srt_file)

    # Status set back to queued (not failed) with retry count incremented.
    update_calls = db.table.return_value.update.call_args_list
    assert any(
        call.args[0].get("status") == "queued" and call.args[0].get("timeout_retry_count") == 1
        for call in update_calls
    )


def test_transient_failure_hard_fails_after_max_retries(srt_file):
    job_row = _make_job(input_path=srt_file, timeout_retry_count=3)  # already at _MAX_TRANSIENT_RETRIES
    db = _make_db(job_row)

    with patch("app.tasks.transcript_translation_tasks.get_service_client", return_value=db), \
         patch("app.services.transcript_translation_service.detect_source_language", return_value="English"), \
         patch("app.services.transcript_translation_service.translate_cues", side_effect=SoftTimeLimitExceeded()), \
         patch("app.services.transcript_translation_cache.lookup_many", return_value={}), \
         patch("app.services.transcript_translation_cache.store_many"), \
         patch("app.tasks.transcript_translation_tasks.translate_transcript_task.apply_async") as mock_requeue:

        with pytest.raises(SoftTimeLimitExceeded):
            translate_transcript_task(job_row["id"])

    mock_requeue.assert_not_called()

    # Retries exhausted — this IS a hard failure, so cleanup should have run.
    assert not os.path.isdir(os.path.dirname(srt_file))

    update_calls = db.table.return_value.update.call_args_list
    assert any(call.args[0].get("status") == "failed" for call in update_calls)
