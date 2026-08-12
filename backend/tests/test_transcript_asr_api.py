"""Tests for the transcript-asr endpoints (create/list/download/translate/delete)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app as fastapi_app

client = TestClient(fastapi_app, raise_server_exceptions=False)


def _with_identity(user_id):
    from app.api.transcript_translate import resolve_identity as _real_resolve
    fastapi_app.dependency_overrides[_real_resolve] = lambda: user_id
    return _real_resolve


def _mock_ffprobe(duration_sec: float):
    result = MagicMock()
    result.returncode = 0
    result.stdout = str(duration_sec)
    result.stderr = ""
    return result


def test_create_job_rejects_video_over_duration_cap(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    key = _with_identity("user-1")
    try:
        with patch("app.api.processing._guard_local_path", return_value=str(video)), \
             patch("subprocess.run", return_value=_mock_ffprobe(46 * 60)):  # 46 min > 45 min cap
            resp = client.post(
                "/api/v1/transcript-asr/jobs",
                json={"video_local_path": str(video), "video_title": "Long video"},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 422
    assert "phút" in resp.json()["detail"]


def test_create_job_succeeds_and_reserves_quota(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = True
    db.table.return_value.insert.return_value.execute.return_value.data = [{"id": "asr-job-1"}]

    key = _with_identity("user-1")
    try:
        with patch("app.api.processing._guard_local_path", return_value=str(video)), \
             patch("subprocess.run", return_value=_mock_ffprobe(120)), \
             patch("app.api.transcript_asr._get_db", return_value=db), \
             patch("app.api.transcript_asr._get_transcribe_task", return_value=None):
            resp = client.post(
                "/api/v1/transcript-asr/jobs",
                json={"video_local_path": str(video), "video_title": "Short clip"},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "asr-job-1"
    assert body["status"] == "queued"

    # Reserved by duration in MINUTES, not raw seconds.
    rpc_call = db.rpc.call_args
    assert rpc_call[0][0] == "reserve_transcript_asr_usage"
    assert rpc_call[0][1]["p_minutes"] == 2.0  # 120s = 2min


def test_create_job_rejects_quota_exceeded(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")

    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = False  # quota exhausted

    key = _with_identity("user-1")
    try:
        with patch("app.api.processing._guard_local_path", return_value=str(video)), \
             patch("subprocess.run", return_value=_mock_ffprobe(60)), \
             patch("app.api.transcript_asr._get_db", return_value=db):
            resp = client.post(
                "/api/v1/transcript-asr/jobs",
                json={"video_local_path": str(video)},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 422
    assert "hạn mức" in resp.json()["detail"]


def test_download_rejects_not_done():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "asr-job-1", "user_id": "user-1", "status": "transcribing", "result_path": None,
    }

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_asr._get_db", return_value=db):
            resp = client.get("/api/v1/transcript-asr/jobs/asr-job-1/download")
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 404


def test_translate_chains_into_transcript_translation_jobs(tmp_path):
    srt_path = tmp_path / "asr_result.srt"
    srt_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")

    asr_db = MagicMock()
    asr_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "asr-job-1", "user_id": "user-1", "status": "done",
        "result_path": str(srt_path), "video_title": "My Video",
    }
    asr_db.rpc.return_value.execute.return_value.data = True  # translation quota reserved

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_asr._get_db", return_value=asr_db), \
             patch("app.api.transcript_translate._get_translate_task", return_value=None):
            resp = client.post(
                "/api/v1/transcript-asr/jobs/asr-job-1/translate",
                json={"target_lang": "vi"},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["target_lang"] == "vi"
    assert body["cue_count"] == 1
    assert body["filename"] == "My Video.srt"

    # A real transcript_translation_jobs insert happened with the right shape.
    insert_call = asr_db.table.return_value.insert.call_args
    assert insert_call[0][0]["source_format"] == "srt"
    assert insert_call[0][0]["target_lang"] == "vi"
    assert insert_call[0][0]["user_id"] == "user-1"


def test_translate_rejects_invalid_target_lang():
    key = _with_identity("user-1")
    try:
        resp = client.post(
            "/api/v1/transcript-asr/jobs/asr-job-1/translate",
            json={"target_lang": "not-a-real-lang"},
        )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 422


def test_delete_removes_result_file_and_row(tmp_path):
    result_path = tmp_path / "result.srt"
    result_path.write_text("x", encoding="utf-8")

    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "asr-job-1", "user_id": "user-1", "result_path": str(result_path),
    }

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_asr._get_db", return_value=db):
            resp = client.delete("/api/v1/transcript-asr/jobs/asr-job-1")
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
    assert not result_path.exists()
    db.table.return_value.delete.assert_called_once()
