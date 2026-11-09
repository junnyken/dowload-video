"""
Phase P5.1 Security & Authorization Tests for Flow/Veo Visible-Logo Cleanup
===========================================================================
Covers all mandatory P5.1 requirements:
  a) GET endpoints (`/frame/{temp_id}`, `/preview-clean/{temp_id}`, `/preview-clip-video/{temp_id}`)
     require HMAC-SHA256 signed URLs (`exp <= 10m`, `hmac.compare_digest`).
     Missing, expired, >10m, or tampered signatures/params return 404.
  b) `frame_idx < 0` or `frame_idx >= total_frames` returns HTTP 400;
     cached keyframes do not re-invoke FFmpeg; LRU cap keeps `<= 50` keyframe files per job.
  c) `POST /no-logo` requires Pro (`401` unauth, `402` Free) and owner check (`404` other user).
  d) `_verify_job_owner` fails closed (`404` when `owner_user_id` is missing/empty);
     both `/upload` and `/from-local` write `owner_user_id` and return signed `preview_url`.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import time
from datetime import datetime, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.core.local_download import new_token, safe_file_id
from app.main import app


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user():
    async def _pro():
        return {"id": "pro-owner-p51", "email": "owner@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def other_pro_user():
    async def _other():
        return {"id": "pro-intruder-p51", "email": "intruder@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _other
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def free_user():
    async def _free():
        return {"id": "free-user-p51", "email": "free@test.local", "tier": "free"}

    app.dependency_overrides[fc.get_optional_user] = _free
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def unauth_user():
    async def _none():
        return None

    app.dependency_overrides[fc.get_optional_user] = _none
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


def _encode_tiny_mp4(path: str, vw: int = 320, vh: int = 180, fps: float = 10.0, n_frames: int = 60) -> None:
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}", "-r", str(fps), "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "22", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", path,
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rng = np.random.default_rng(7)
    for _ in range(n_frames):
        frame = rng.integers(40, 180, size=(vh, vw, 3), dtype=np.uint8)
        frame[130:160, 240:300] = (235, 235, 235)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait(timeout=30)


@pytest.fixture
def session_p51(tmp_path, monkeypatch):
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
    monkeypatch.setenv("FLOW_URL_SIGNING_SECRET", "test-secret-p51-hmac-key")

    temp_id = new_token()
    work_dir = fc._safe_work_dir(temp_id)
    os.makedirs(work_dir, exist_ok=True)
    input_path = os.path.join(work_dir, "input.mp4")
    _encode_tiny_mp4(input_path, vw=320, vh=180, fps=10.0, n_frames=60)

    fc._extract_frame(input_path, os.path.join(work_dir, "preview.jpg"))
    with open(os.path.join(work_dir, "preview_clean.jpg"), "wb") as f:
        with open(os.path.join(work_dir, "preview.jpg"), "rb") as src:
            f.write(src.read())
    with open(os.path.join(work_dir, "preview_clip_clean.mp4"), "wb") as f:
        with open(input_path, "rb") as src:
            f.write(src.read())

    fc._write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": "pro-owner-p51",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "video_info": {"width": 320, "height": 180, "duration": 6.0, "fps": 10.0},
        "file_size_mb": 0.05,
    })
    return {"temp_id": temp_id, "work_dir": work_dir, "input_path": input_path, "dl_dir": str(dl_dir)}


def test_unsigned_get_endpoints_return_404(session_p51):
    """P5.1a: Calling GET /frame, /preview-clean, or /preview-clip-video without signature returns 404."""
    client = TestClient(app)
    tid = session_p51["temp_id"]

    assert client.get(f"/api/v1/flow-cleanup/frame/{tid}").status_code == 404
    assert client.get(f"/api/v1/flow-cleanup/preview-clean/{tid}").status_code == 404
    assert client.get(f"/api/v1/flow-cleanup/preview-clip-video/{tid}").status_code == 404


def test_signed_url_expired_or_tampered_returns_404(session_p51):
    """P5.1a: Expired, >10m TTL, tampered sig/frame_idx/owner all return 404; valid returns 200."""
    client = TestClient(app)
    tid = session_p51["temp_id"]
    owner = "pro-owner-p51"

    # 1. Valid signed URLs succeed
    u_frame = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", owner)
    u_clean = fc._build_signed_url(f"/api/v1/flow-cleanup/preview-clean/{tid}", tid, "preview_clean", owner)
    u_clip = fc._build_signed_url(f"/api/v1/flow-cleanup/preview-clip-video/{tid}", tid, "preview_clip", owner)
    assert client.get(u_frame).status_code == 200
    assert client.get(u_clean).status_code == 200
    assert client.get(u_clip).status_code == 200

    # 2. Expired signature (exp in the past) -> 404
    exp_past = int(time.time()) - 10
    sig_past = fc._sign_resource_token(tid, "frame", owner, exp_past)
    assert client.get(f"/api/v1/flow-cleanup/frame/{tid}?exp={exp_past}&sig={sig_past}").status_code == 404

    # 3. Expiration > 10 minutes in the future -> 404
    exp_too_far = int(time.time()) + 1200
    sig_too_far = fc._sign_resource_token(tid, "frame", owner, exp_too_far)
    assert client.get(f"/api/v1/flow-cleanup/frame/{tid}?exp={exp_too_far}&sig={sig_too_far}").status_code == 404

    # 4. Tampered signature -> 404
    tampered = u_frame[:-4] + ("0000" if not u_frame.endswith("0000") else "ffff")
    assert client.get(tampered).status_code == 404

    # 5. Tampered frame_idx parameter using frame 0's signature -> 404
    assert client.get(u_frame + "&frame_idx=5").status_code == 404

    # 6. Signed for a different user -> 404
    u_wrong_owner = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", "other-user")
    assert client.get(u_wrong_owner).status_code == 404


def test_frame_idx_bounds_negative_and_overflow_return_400(session_p51, pro_user):
    """P5.1b: frame_idx = -1 or 10^9 must be rejected with HTTP 400."""
    client = TestClient(app)
    tid = session_p51["temp_id"]
    owner = "pro-owner-p51"

    # Direct GET /frame with valid signature for -1 and 10^9 -> 400
    u_neg = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", owner, frame_idx=-1)
    u_huge = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", owner, frame_idx=10**9)
    assert client.get(u_neg).status_code == 400
    assert client.get(u_huge).status_code == 400

    # GET /frame-url endpoint also rejects -1 and 10^9 with 400
    assert client.get(f"/api/v1/flow-cleanup/frame-url/{tid}?frame_idx=-1").status_code == 400
    assert client.get(f"/api/v1/flow-cleanup/frame-url/{tid}?frame_idx=1000000000").status_code == 400


def test_keyframe_cache_no_reextract_and_lru_cap_50(session_p51, monkeypatch):
    """P5.1b: Cached keyframe does not call FFmpeg again, and >50 keyframes keeps cache <= 50 files."""
    client = TestClient(app)
    tid = session_p51["temp_id"]
    work_dir = session_p51["work_dir"]
    owner = "pro-owner-p51"

    calls = []
    orig_extract = fc._extract_frame

    def _spy_extract(input_path, frame_path, seek_sec=0.0):
        calls.append(frame_path)
        # Write a fast 64-byte dummy JPEG so we don't spawn 55 ffmpeg processes in the LRU test
        with open(frame_path, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9")
        return True

    monkeypatch.setattr(fc, "_extract_frame", _spy_extract)

    # First call for frame_idx=5 extracts once; second call serves from cache without calling _extract_frame
    u5 = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", owner, frame_idx=5)
    assert client.get(u5).status_code == 200
    assert len(calls) == 1
    assert client.get(u5).status_code == 200
    assert len(calls) == 1, "Cached keyframe must NOT invoke _extract_frame again"

    # Request 55 distinct keyframes (1..55 out of 60 total frames)
    for idx in range(1, 56):
        u_idx = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", owner, frame_idx=idx)
        r = client.get(u_idx)
        assert r.status_code == 200

    cached_files = [f for f in os.listdir(work_dir) if re.match(r"^frame_\d+\.jpg$", f)]
    assert len(cached_files) <= 50, f"Expected <= 50 cached keyframes, found {len(cached_files)}"


def test_no_logo_auth_and_owner_enforcement(session_p51):
    """P5.1c: POST /no-logo requires Pro (401 unauth, 402 Free) and job ownership (404 other Pro user)."""
    client = TestClient(app)
    tid = session_p51["temp_id"]

    # 1. Unauthenticated -> 401
    async def _none():
        return None
    app.dependency_overrides[fc.get_optional_user] = _none
    try:
        r_unauth = client.post("/api/v1/flow-cleanup/no-logo", json={"temp_id": tid})
        assert r_unauth.status_code == 401
    finally:
        app.dependency_overrides.pop(fc.get_optional_user, None)

    # 2. Free user -> 402
    async def _free():
        return {"id": "pro-owner-p51", "tier": "free"}
    app.dependency_overrides[fc.get_optional_user] = _free
    try:
        r_free = client.post("/api/v1/flow-cleanup/no-logo", json={"temp_id": tid})
        assert r_free.status_code == 402
    finally:
        app.dependency_overrides.pop(fc.get_optional_user, None)

    # 3. Other Pro user -> 404
    async def _other():
        return {"id": "other-pro-user", "tier": "pro"}
    app.dependency_overrides[fc.get_optional_user] = _other
    try:
        r_other = client.post("/api/v1/flow-cleanup/no-logo", json={"temp_id": tid})
        assert r_other.status_code == 404
    finally:
        app.dependency_overrides.pop(fc.get_optional_user, None)

    # 4. Owner Pro user -> 200
    async def _owner():
        return {"id": "pro-owner-p51", "tier": "pro"}
    app.dependency_overrides[fc.get_optional_user] = _owner
    try:
        r_ok = client.post("/api/v1/flow-cleanup/no-logo", json={"temp_id": tid})
        assert r_ok.status_code == 200
        assert r_ok.json()["recorded"] is True
    finally:
        app.dependency_overrides.pop(fc.get_optional_user, None)


def test_verify_job_owner_fails_closed_when_owner_missing(session_p51, pro_user):
    """P5.1d: A job without owner_user_id in metadata.json must be rejected (404), never allowed through."""
    client = TestClient(app)
    tid = session_p51["temp_id"]
    work_dir = session_p51["work_dir"]

    # Remove owner_user_id from metadata
    fc._write_meta(work_dir, {"owner_user_id": None})

    assert client.get(f"/api/v1/flow-cleanup/status/{tid}").status_code == 404
    assert client.post(f"/api/v1/flow-cleanup/cancel/{tid}").status_code == 404
    assert client.post("/api/v1/flow-cleanup/no-logo", json={"temp_id": tid}).status_code == 404
    assert client.post(
        "/api/v1/flow-cleanup/process",
        json={"temp_id": tid, "preset": "lower-right", "method": "crop"},
    ).status_code == 404


def test_upload_and_from_local_both_persist_owner_and_return_signed_url(session_p51, pro_user):
    """P5.1d: Every job creation path (/upload and /from-local) records owner_user_id and returns a signed preview_url."""
    client = TestClient(app)
    src_mp4 = session_p51["input_path"]
    dl_dir = session_p51["dl_dir"]

    # 1. POST /upload
    with open(src_mp4, "rb") as f:
        up_res = client.post(
            "/api/v1/flow-cleanup/upload",
            files={"file": ("clip.mp4", io.BytesIO(f.read()), "video/mp4")},
        )
    assert up_res.status_code == 200, up_res.text
    up_data = up_res.json()
    up_meta = fc._read_meta(fc._safe_work_dir(up_data["temp_id"]))
    assert up_meta.get("owner_user_id") == "pro-owner-p51"
    assert "sig=" in up_data["preview_url"] and "exp=" in up_data["preview_url"]
    assert client.get(up_data["preview_url"]).status_code == 200

    # 2. POST /from-local
    local_copy = os.path.join(dl_dir, "downloaded_video.mp4")
    with open(src_mp4, "rb") as src, open(local_copy, "wb") as dst:
        dst.write(src.read())

    fl_res = client.post(
        "/api/v1/flow-cleanup/from-local",
        json={"local_path": safe_file_id(local_copy), "title": "Local Clip"},
    )
    assert fl_res.status_code == 200, fl_res.text
    fl_data = fl_res.json()
    fl_meta = fc._read_meta(fc._safe_work_dir(fl_data["temp_id"]))
    assert fl_meta.get("owner_user_id") == "pro-owner-p51"
    assert "sig=" in fl_data["preview_url"] and "exp=" in fl_data["preview_url"]
    assert client.get(fl_data["preview_url"]).status_code == 200
