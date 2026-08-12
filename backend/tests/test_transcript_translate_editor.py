"""
Tests for GET/PUT /transcript-translate/jobs/{job_id}/cues — the
review/edit-before-download endpoints. Verifies edits only touch `text`
(never index/start/end), unknown indices are ignored not errored, and
reading_speed_warning_count is recomputed after a save.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app as fastapi_app

client = TestClient(fastapi_app, raise_server_exceptions=False)

_SRT = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\nWorld\n"
)


def _mock_db(job_row):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = job_row
    return db


def _with_identity(user_id):
    from app.api.transcript_translate import resolve_identity as _real_resolve
    fastapi_app.dependency_overrides[_real_resolve] = lambda: user_id
    return _real_resolve


def test_get_cues_returns_parsed_content(tmp_path):
    result_path = tmp_path / "translated.srt"
    result_path.write_text(_SRT, encoding="utf-8")
    job_row = {"id": "job-1", "user_id": "user-1", "status": "done",
               "result_path": str(result_path), "source_format": "srt"}

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)):
            resp = client.get("/api/v1/transcript-translate/jobs/job-1/cues")
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 200, resp.text
    cues = resp.json()["cues"]
    assert len(cues) == 2
    assert cues[0] == {"index": 1, "start": "00:00:01,000", "end": "00:00:02,000", "text": "Hello"}
    assert cues[1]["text"] == "World"


def test_put_cues_edits_text_only_and_preserves_timing(tmp_path):
    result_path = tmp_path / "translated.srt"
    result_path.write_text(_SRT, encoding="utf-8")
    job_row = {"id": "job-1", "user_id": "user-1", "status": "done",
               "result_path": str(result_path), "source_format": "srt"}

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)):
            resp = client.put(
                "/api/v1/transcript-translate/jobs/job-1/cues",
                json={"cues": [{"index": 1, "text": "Xin chào"}]},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    saved = result_path.read_text(encoding="utf-8")
    assert "Xin chào" in saved
    assert "World" in saved  # cue 2 untouched
    assert "00:00:01,000 --> 00:00:02,000" in saved  # timing preserved byte-for-byte


def test_put_cues_ignores_unknown_index(tmp_path):
    result_path = tmp_path / "translated.srt"
    result_path.write_text(_SRT, encoding="utf-8")
    job_row = {"id": "job-1", "user_id": "user-1", "status": "done",
               "result_path": str(result_path), "source_format": "srt"}

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)):
            resp = client.put(
                "/api/v1/transcript-translate/jobs/job-1/cues",
                json={"cues": [{"index": 999, "text": "should be ignored"}]},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 200, resp.text
    saved = result_path.read_text(encoding="utf-8")
    assert "should be ignored" not in saved
    assert "Hello" in saved and "World" in saved


def test_put_cues_recomputes_reading_speed_warning_count(tmp_path):
    result_path = tmp_path / "translated.srt"
    result_path.write_text(_SRT, encoding="utf-8")
    job_row = {"id": "job-1", "user_id": "user-1", "status": "done",
               "result_path": str(result_path), "source_format": "srt"}
    db = _mock_db(job_row)

    # Cue 1 spans exactly 1 second; a 60-char edit is way over the 20 CPS cap.
    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_translate._get_db", return_value=db):
            resp = client.put(
                "/api/v1/transcript-translate/jobs/job-1/cues",
                json={"cues": [{"index": 1, "text": "x" * 60}]},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 200, resp.text
    assert resp.json()["reading_speed_warning_count"] == 1
    db.table.return_value.update.assert_called_once()
    assert db.table.return_value.update.call_args[0][0]["reading_speed_warning_count"] == 1


def test_put_cues_rejects_oversized_edit_text(tmp_path):
    result_path = tmp_path / "translated.srt"
    result_path.write_text(_SRT, encoding="utf-8")
    job_row = {"id": "job-1", "user_id": "user-1", "status": "done",
               "result_path": str(result_path), "source_format": "srt"}

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)):
            resp = client.put(
                "/api/v1/transcript-translate/jobs/job-1/cues",
                json={"cues": [{"index": 1, "text": "x" * 2001}]},
            )
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 422
    # Rejected before ever touching disk — original content untouched.
    assert result_path.read_text(encoding="utf-8") == _SRT


def test_get_cues_rejects_job_not_owned():
    job_row = {"id": "job-1", "user_id": "someone-else", "status": "done", "result_path": "/x.srt"}

    key = _with_identity("user-1")
    try:
        with patch("app.api.transcript_translate._get_db", return_value=_mock_db(job_row)):
            resp = client.get("/api/v1/transcript-translate/jobs/job-1/cues")
    finally:
        fastapi_app.dependency_overrides.pop(key, None)

    assert resp.status_code == 404
