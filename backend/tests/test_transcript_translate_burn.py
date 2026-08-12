"""
Tests for POST /transcript-translate/jobs/{job_id}/burn-into-video —
verifies ownership/status/path checks and that the FFmpeg burn helper and
path-traversal guard from app.api.processing are reused (not duplicated).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app as fastapi_app

client = TestClient(fastapi_app, raise_server_exceptions=False)

_URL = "/api/v1/transcript-translate/jobs/job-1/burn-into-video"


def _mock_db(job_row):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = job_row
    return db


def test_burn_succeeds_and_reuses_processing_helpers(tmp_path):
    subtitle_path = tmp_path / "translated.srt"
    subtitle_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")

    job_row = {
        "id": "job-1", "user_id": "user-1", "status": "done",
        "result_path": str(subtitle_path),
    }

    # Override the identity dependency the app actually wired up (must be
    # captured BEFORE any patch() on the same module, or dependency_overrides
    # ends up keyed by a mock object FastAPI's route was never bound to).
    from app.api.transcript_translate import resolve_identity as _real_resolve
    fastapi_app.dependency_overrides[_real_resolve] = lambda: "user-1"

    with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)), \
         patch("app.api.processing._guard_local_path", return_value="/downloads/video.mp4") as mock_guard, \
         patch("app.api.processing._burn_subtitle", return_value=True) as mock_burn, \
         patch("app.api.processing._schedule_cleanup") as mock_cleanup, \
         patch("app.api.processing._safe_download_dir", return_value=str(tmp_path)):
        try:
            resp = client.post(_URL, json={"video_local_path": "/downloads/video.mp4"})
        finally:
            fastapi_app.dependency_overrides.pop(_real_resolve, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert "download_url" in body

    mock_guard.assert_called_once_with("/downloads/video.mp4")
    mock_burn.assert_called_once()
    burn_args = mock_burn.call_args[0]
    assert burn_args[0] == "/downloads/video.mp4"   # video_path (guarded)
    assert burn_args[1] == str(subtitle_path)         # subtitle_path (job's result_path)
    mock_cleanup.assert_called_once()


def test_burn_rejects_job_not_owned_by_caller():
    job_row = {"id": "job-1", "user_id": "someone-else", "status": "done", "result_path": "/x.srt"}

    from app.api.transcript_translate import resolve_identity as _real_resolve
    fastapi_app.dependency_overrides[_real_resolve] = lambda: "user-1"
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)):
            resp = client.post(_URL, json={"video_local_path": "/downloads/video.mp4"})
    finally:
        fastapi_app.dependency_overrides.pop(_real_resolve, None)

    assert resp.status_code == 404


def test_burn_rejects_job_not_done():
    job_row = {"id": "job-1", "user_id": "user-1", "status": "translating", "result_path": None}

    from app.api.transcript_translate import resolve_identity as _real_resolve
    fastapi_app.dependency_overrides[_real_resolve] = lambda: "user-1"
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)):
            resp = client.post(_URL, json={"video_local_path": "/downloads/video.mp4"})
    finally:
        fastapi_app.dependency_overrides.pop(_real_resolve, None)

    assert resp.status_code == 404
    assert "chưa hoàn tất" in resp.json()["detail"]


def test_burn_propagates_video_path_guard_rejection(tmp_path):
    subtitle_path = tmp_path / "translated.srt"
    subtitle_path.write_text("x", encoding="utf-8")
    job_row = {"id": "job-1", "user_id": "user-1", "status": "done", "result_path": str(subtitle_path)}

    from app.api.transcript_translate import resolve_identity as _real_resolve
    fastapi_app.dependency_overrides[_real_resolve] = lambda: "user-1"
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)), \
             patch(
                 "app.api.processing._guard_local_path",
                 side_effect=HTTPException(status_code=400, detail="Invalid local_path: path traversal not allowed"),
             ):
            resp = client.post(_URL, json={"video_local_path": "../../etc/passwd"})
    finally:
        fastapi_app.dependency_overrides.pop(_real_resolve, None)

    assert resp.status_code == 400
    assert "path traversal" in resp.json()["detail"]
