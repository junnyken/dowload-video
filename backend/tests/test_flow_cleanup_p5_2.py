"""
Phase P5.2 tests — Asynchronous Queueing for `/track` & `/preview-clip`, Deduplication, Cancel, Timeout & User Concurrency
===========================================================================================================================
Covers:
  1. `POST /api/v1/flow-cleanup/track/{temp_id}` and `POST /api/v1/flow-cleanup/preview-clip` dispatch onto
     the Celery `media` queue (`track_watermark_task`, `preview_clip_task`) and return HTTP 202 with `task_id` and `status_url` in < 1s.
  2. Calling `/track`, `/preview-clip`, or `/process` twice while the job is `queued` or `running` deduplicates
     and does NOT spawn a second Celery task.
  3. Cancelling a job mid-execution (`POST /cancel/{job_id}`) stops processing and cleans up partial files
     (`preview_clip_src.mp4`, `preview_clip_clean.mp4`, `cleaned_*.mp4`, `.noaudio.mp4`, `.tmp.*`).
  4. Worker timeout (`<= 120s` for `/track`, `<= 90s` for `/preview-clip`) transitions job status to `failed`
     with `error_code == "processing_timeout"`.
  5. Per-user concurrency limit (`FLOW_MAX_USER_CONCURRENT_JOBS=1`): launching a second heavy job for another
     `temp_id` while one is already `queued` or `running` returns HTTP 429 (`user_concurrent_job_limit`).
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.core.local_download import new_token
from app.main import app
from app.tasks import media_tasks


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user():
    async def _pro():
        return {"id": "pro-user-p52", "email": "pro52@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


def _make_job_session(dl_dir: str, owner_id: str = "pro-user-p52") -> dict:
    temp_id = new_token()
    work_dir = os.path.join(dl_dir, f"flow_{temp_id}")
    os.makedirs(work_dir, exist_ok=True)
    input_path = os.path.join(work_dir, "input.mp4")

    vw, vh, fps, n_frames = 320, 180, 10.0, 20
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}", "-r", str(fps), "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", input_path,
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rng = np.random.default_rng(77)
    for _ in range(n_frames):
        frame = rng.integers(30, 170, size=(vh, vw, 3), dtype=np.uint8)
        frame[130:160, 240:300] = (235, 235, 235)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait(timeout=30)

    fc._extract_frame(input_path, os.path.join(work_dir, "preview.jpg"))
    fc._write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": owner_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "video_info": {"width": vw, "height": vh, "duration": 2.0, "fps": fps, "nb_frames": n_frames},
        "file_size_mb": 0.05,
    })
    return {"temp_id": temp_id, "work_dir": work_dir, "input_path": input_path}


def test_track_and_preview_clip_return_202_under_1s_and_deduplicate(tmp_path, monkeypatch, pro_user):
    """
    P5.2:
    - `/track` and `/preview-clip` dispatch to Celery `media` queue and return HTTP 202 in < 1s.
    - Calling the same endpoint again while `queued` or `running` deduplicates and does not spawn a second task.
    """
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    sess1 = _make_job_session(str(dl_dir))
    tid1 = sess1["temp_id"]

    mock_track_apply = MagicMock(return_value=MagicMock(id="celery-track-001"))
    monkeypatch.setattr(media_tasks.track_watermark_task, "apply_async", mock_track_apply)

    client = TestClient(app)
    t0 = time.perf_counter()
    r1 = client.post(
        f"/api/v1/flow-cleanup/track/{tid1}",
        json={"region": {"x": 240, "y": 130, "w": 60, "h": 30}},
    )
    elapsed1 = time.perf_counter() - t0
    assert r1.status_code == 202, r1.text
    assert elapsed1 < 1.0
    d1 = r1.json()
    assert d1["task_id"] == "celery-track-001"
    assert d1["status"] == "queued"
    assert d1["deduplicated"] is False
    assert d1["status_url"] == f"/api/v1/flow-cleanup/status/{tid1}"
    assert mock_track_apply.call_count == 1
    assert mock_track_apply.call_args.kwargs.get("queue") == "media"

    # Second call while still queued -> deduplicated (no second task spawned)
    r2 = client.post(
        f"/api/v1/flow-cleanup/track/{tid1}",
        json={"region": {"x": 240, "y": 130, "w": 60, "h": 30}},
    )
    assert r2.status_code == 202
    assert r2.json()["deduplicated"] is True
    assert mock_track_apply.call_count == 1

    # Cancel first job so user concurrency slot is freed, then test /preview-clip 202 + deduplication
    client.post(f"/api/v1/flow-cleanup/cancel/{tid1}")

    mock_clip_apply = MagicMock(return_value=MagicMock(id="celery-clip-001"))
    monkeypatch.setattr(media_tasks.preview_clip_task, "apply_async", mock_clip_apply)

    t1 = time.perf_counter()
    rc1 = client.post(
        "/api/v1/flow-cleanup/preview-clip",
        json={
            "temp_id": tid1,
            "preset": "custom",
            "region": {"x": 240, "y": 130, "w": 60, "h": 30},
            "method": "natural",
            "clip_seconds": 2.0,
        },
    )
    elapsed_clip = time.perf_counter() - t1
    assert rc1.status_code == 202, rc1.text
    assert elapsed_clip < 1.0
    dc1 = rc1.json()
    assert dc1["task_id"] == "celery-clip-001"
    assert dc1["deduplicated"] is False
    assert mock_clip_apply.call_count == 1
    assert mock_clip_apply.call_args.kwargs.get("queue") == "media"

    # Duplicate call to /preview-clip while queued -> does not spawn a second task
    rc2 = client.post(
        "/api/v1/flow-cleanup/preview-clip",
        json={
            "temp_id": tid1,
            "preset": "custom",
            "region": {"x": 240, "y": 130, "w": 60, "h": 30},
            "method": "natural",
            "clip_seconds": 2.0,
        },
    )
    assert rc2.status_code == 202
    assert rc2.json()["deduplicated"] is True
    assert mock_clip_apply.call_count == 1


def test_user_concurrent_job_limit_blocks_second_active_job(tmp_path, monkeypatch, pro_user):
    """
    P5.2: Each user may run at most 1 heavy job (`process`/`track`/`preview-clip`) at a time.
    Starting another job on a second `temp_id` while the first is `queued` or `running` returns HTTP 429.
    """
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    sess1 = _make_job_session(str(dl_dir))
    sess2 = _make_job_session(str(dl_dir))

    mock_track_apply = MagicMock(return_value=MagicMock(id="celery-track-a"))
    monkeypatch.setattr(media_tasks.track_watermark_task, "apply_async", mock_track_apply)

    client = TestClient(app)
    r1 = client.post(
        f"/api/v1/flow-cleanup/track/{sess1['temp_id']}",
        json={"region": {"x": 240, "y": 130, "w": 60, "h": 30}},
    )
    assert r1.status_code == 202

    # Attempt to start a job on sess2 while sess1 is still queued -> 429
    r_blocked = client.post(
        f"/api/v1/flow-cleanup/track/{sess2['temp_id']}",
        json={"region": {"x": 240, "y": 130, "w": 60, "h": 30}},
    )
    assert r_blocked.status_code == 429
    detail = r_blocked.json()["detail"]
    assert detail["error_code"] == "user_concurrent_job_limit"
    assert detail["active_job_id"] == sess1["temp_id"]


def test_cancel_mid_execution_cleans_up_partial_files(tmp_path, monkeypatch, pro_user):
    """
    P5.2: Cancelling a job mid-execution stops the worker and cleans up all partial files
    (`preview_clip_src.mp4`, `preview_clip_clean.mp4`, `cleaned_*.mp4`, `.noaudio.mp4`, `.tmp.*`).
    """
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    sess = _make_job_session(str(dl_dir))
    tid = sess["temp_id"]
    wdir = sess["work_dir"]

    # Create simulated partial files in work_dir
    partials = [
        os.path.join(wdir, "preview_clip_src.mp4"),
        os.path.join(wdir, "preview_clip_clean.mp4"),
        os.path.join(wdir, "cleaned_partial123.mp4"),
        os.path.join(wdir, "cleaned_partial123.mp4.noaudio.mp4"),
        os.path.join(wdir, "metadata.json.tmp.999"),
    ]
    for p in partials:
        with open(p, "wb") as f:
            f.write(b"partial-bytes")

    fc._write_meta(wdir, {"status": "running", "active_task_kind": "preview_clip", "progress": 45})

    client = TestClient(app)
    rc = client.post(f"/api/v1/flow-cleanup/cancel/{tid}")
    assert rc.status_code == 200
    assert rc.json()["status"] == "cancelled"

    for p in partials:
        assert not os.path.exists(p), f"Partial file not removed on cancel: {p}"


def test_track_and_preview_clip_timeout_transitions_to_failed(tmp_path, monkeypatch, pro_user):
    """
    P5.2: When `/track` or `/preview-clip` exceeds its timeout (`_TRACK_TIMEOUT_SEC` / `_PREVIEW_CLIP_TIMEOUT_SEC`),
    the job transitions to `status == 'failed'` with `error_code == 'processing_timeout'`.
    """
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    sess = _make_job_session(str(dl_dir))
    tid = sess["temp_id"]

    # Set an immediate timeout threshold so tracking times out deterministically without sleeping
    monkeypatch.setattr(fc, "_TRACK_TIMEOUT_SEC", 0.0001)

    with pytest.raises(TimeoutError):
        fc._execute_flow_track_job(
            tid,
            {"init_box": [240, 130, 60, 30], "keyframes": None, "min_confidence": 0.65},
        )

    client = TestClient(app)
    st = client.get(f"/api/v1/flow-cleanup/status/{tid}")
    assert st.status_code == 200
    data = st.json()
    assert data["status"] == "failed"
    assert data["error_code"] == "processing_timeout"
