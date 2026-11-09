"""
Phase P4 tests for Flow/Veo Visible-Logo Cleanup UI backend support
===================================================================
Covers:
  1. `GET /api/v1/flow-cleanup/frame/{temp_id}?frame_idx=...` keyframe JPEG extraction & caching.
  2. `POST /api/v1/flow-cleanup/preview-clip` & `GET /api/v1/flow-cleanup/preview-clip-video/{temp_id}` 3–5s video clip preview (static + tracked `frame_boxes`).
  3. Owner verification (`owner_user_id`) and Pro tier access control on `/preview-clip`.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.core.local_download import new_token
from app.main import app


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user():
    async def _pro():
        return {"id": "pro-user-p4", "email": "pro4@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def other_pro_user():
    async def _other():
        return {"id": "other-pro-p4", "email": "other4@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _other
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def free_user():
    async def _free():
        return {"id": "free-user-p4", "email": "free4@test.local", "tier": "free"}

    app.dependency_overrides[fc.get_optional_user] = _free
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def sample_session_p4(tmp_path, monkeypatch):
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    temp_id = new_token()
    work_dir = fc._safe_work_dir(temp_id)
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
    rng = np.random.default_rng(123)
    for idx in range(n_frames):
        frame = rng.integers(30, 180, size=(vh, vw, 3), dtype=np.uint8)
        frame[130:160, 240:300] = (230, 230, 230)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait(timeout=30)

    fc._extract_frame(input_path, os.path.join(work_dir, "preview.jpg"))
    fc._write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": "pro-user-p4",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "video_info": {"width": vw, "height": vh, "duration": 2.0, "fps": fps},
        "file_size_mb": 0.05,
    })
    return {"temp_id": temp_id, "work_dir": work_dir, "input_path": input_path}


def test_frame_endpoint_extracts_specific_keyframe(sample_session_p4):
    client = TestClient(app)
    tid = sample_session_p4["temp_id"]
    work_dir = sample_session_p4["work_dir"]

    # Default frame 0
    r0 = client.get(f"/api/v1/flow-cleanup/frame/{tid}")
    assert r0.status_code == 200
    assert r0.headers["content-type"].startswith("image/jpeg")

    # Keyframe 10
    r10 = client.get(f"/api/v1/flow-cleanup/frame/{tid}?frame_idx=10")
    assert r10.status_code == 200
    assert r10.headers["content-type"].startswith("image/jpeg")
    assert os.path.isfile(os.path.join(work_dir, "frame_10.jpg"))


def test_preview_clip_renders_and_serves_short_video(sample_session_p4, pro_user):
    client = TestClient(app)
    tid = sample_session_p4["temp_id"]

    res = client.post(
        "/api/v1/flow-cleanup/preview-clip",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": {"x": 240, "y": 130, "w": 60, "h": 30},
            "method": "natural",
            "expand_padding": 4,
            "clip_seconds": 1.5,
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["success"] is True
    assert "preview-clip-video" in data["preview_clip_url"]

    # Fetch the rendered clip
    clip_res = client.get(f"/api/v1/flow-cleanup/preview-clip-video/{tid}")
    assert clip_res.status_code == 200
    assert clip_res.headers["content-type"].startswith("video/mp4")
    assert len(clip_res.content) > 500


def test_preview_clip_access_control(sample_session_p4, free_user):
    client = TestClient(app)
    tid = sample_session_p4["temp_id"]

    # Free user gets 402
    res_free = client.post(
        "/api/v1/flow-cleanup/preview-clip",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": {"x": 240, "y": 130, "w": 60, "h": 30},
            "method": "crop",
        },
    )
    assert res_free.status_code == 402


def test_preview_clip_blocks_other_pro_user(sample_session_p4, other_pro_user):
    client = TestClient(app)
    tid = sample_session_p4["temp_id"]

    res_other = client.post(
        "/api/v1/flow-cleanup/preview-clip",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": {"x": 240, "y": 130, "w": 60, "h": 30},
            "method": "crop",
        },
    )
    assert res_other.status_code == 404
