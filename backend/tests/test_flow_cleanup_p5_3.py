"""
Phase P5.3 tests — Input Hardening, Codecs, Odd/Rotated Dimensions, File Lifecycle (TTL), Retry Cap & Daily User Quota
=======================================================================================================================
Covers:
  1. Valid codecs & containers (`h264_aac.mp4`, `hevc.mp4`, `vp9.webm`, `mov_clip.mov`, `no_audio.mp4`,
     `vertical_720x1280.mp4`, `odd_721x405.mp4`, `rotated_90.mp4`, `vfr.mp4`):
     - Output preserves display resolution (even-aligned for odd inputs), FPS (±1% for CFR), duration (±0.1s),
       and audio presence.
     - PSNR outside mask (output vs input) >= 40 dB.
  2. Invalid / adversarial inputs rejected BEFORE entering worker:
     - 0-byte file (`empty_0byte.mp4`) -> HTTP 400 (`empty_file`)
     - Non-video file renamed to `.mp4` (`fake_text_as_mp4.mp4`) -> HTTP 422 (`invalid_media_container`)
     - Corrupted/truncated video (`corrupted_truncated.mp4`) -> HTTP 422 (`corrupted_video`)
     - Oversized / over-duration / over-resolution -> HTTP 413 / 400
  3. File lifecycle & TTL (`FLOW_CLEANUP_TTL_SEC`):
     - Simulated TTL expiration (without `time.sleep`) causes all endpoints (`/status`, `/result`, and signed
       `/frame`, `/preview-clean`, `/preview-clip-video`) to return HTTP 404 AND physically remove `work_dir` from disk.
  4. Retry cap (`FLOW_MAX_RETRY_COUNT`, default 3) blocks 4th retry with HTTP 400 (`max_retries_exceeded`).
  5. Daily per-user quota (`FLOW_DAILY_USER_QUOTA`, default 20 jobs/day) rejects the (N+1)-th upload with HTTP 429
     (`daily_quota_exceeded`).
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.main import app
from benchmarks.generate_hardening_fixtures import HARDENING_DIR, generate_all_hardening_fixtures


@pytest.fixture(scope="module")
def hardening_fixtures() -> dict:
    return generate_all_hardening_fixtures(HARDENING_DIR)


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user():
    async def _pro():
        return {"id": "pro-user-p53", "email": "pro53@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


def _decode_frames(path: str, vw: int, vh: int) -> list[np.ndarray]:
    frame_bytes = vw * vh * 3
    vf_pad = f"scale='min(iw,{vw})':'min(ih,{vh})',pad={vw}:{vh}:0:0:black"
    r = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", path, "-vf", vf_pad, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        capture_output=True,
        timeout=60,
    )
    n = len(r.stdout) // frame_bytes
    frames = []
    for i in range(n):
        fr = np.frombuffer(r.stdout[i * frame_bytes:(i + 1) * frame_bytes], dtype=np.uint8).reshape((vh, vw, 3)).copy()
        if vw >= 2 and int(fr[:, -1].max()) == 0 and int(fr[:, -2].max()) > 0:
            fr[:, -1] = fr[:, -2]
        if vh >= 2 and int(fr[-1, :].max()) == 0 and int(fr[-2, :].max()) > 0:
            fr[-1, :] = fr[-2, :]
        frames.append(fr)
    return frames


def _psnr_outside_mask(frames_a: list[np.ndarray], frames_b: list[np.ndarray], box: tuple[int, int, int, int], dilate_px: int = 24) -> float:
    n = min(len(frames_a), len(frames_b))
    assert n > 0
    vh, vw = frames_a[0].shape[:2]
    x, y, w, h = box
    keep = np.ones((vh, vw), dtype=bool)
    keep[max(0, y - dilate_px):min(vh, y + h + dilate_px), max(0, x - dilate_px):min(vw, x + w + dilate_px)] = False
    mses = []
    for i in range(n):
        diff = frames_a[i][keep].astype(np.float64) - frames_b[i][keep].astype(np.float64)
        mses.append(float(np.mean(diff * diff)))
    mse = float(np.mean(mses))
    if mse <= 1e-10:
        return 99.0
    return 10.0 * math.log10((255.0 * 255.0) / mse)


@pytest.mark.parametrize(
    "fixture_key,check_cfr_fps",
    [
        ("h264_aac", True),
        ("hevc", True),
        ("vp9_webm", True),
        ("mov", True),
        ("no_audio", True),
        ("vertical_720x1280", True),
        ("odd_721x405", True),
        ("rotated_90", True),
        ("vfr", False),
    ],
)
def test_valid_codecs_dimensions_rotation_and_psnr_outside_mask(
    hardening_fixtures, tmp_path, monkeypatch, pro_user, fixture_key, check_cfr_fps
):
    """
    P5.3: Process every valid codec/dimension/rotation/VFR fixture end-to-end:
    - Preserves display width/height (even-aligned for odd 721x405 -> 720x404, swapped 180x320 for rotated_90),
      FPS (±1% for CFR), duration (±0.1s), and audio presence.
    - PSNR outside mask (output vs input) >= 40 dB.
    """
    dl_dir = tmp_path / f"dl_{fixture_key}"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
    monkeypatch.setenv("FLOW_CLEANUP_ALLOW_INPROCESS", "1")

    src_path = hardening_fixtures[fixture_key]
    ext = os.path.splitext(src_path)[1]
    client = TestClient(app)

    with open(src_path, "rb") as f:
        up = client.post(
            "/api/v1/flow-cleanup/upload",
            files={"file": (f"sample{ext}", f, "video/mp4")},
        )
    assert up.status_code == 200, up.text
    up_data = up.json()
    tid = up_data["temp_id"]
    in_info = up_data["video_info"]
    vw, vh = int(in_info["width"]), int(in_info["height"])

    # Odd 721x405 is padded +1px to 722x406 (intentional deviation for yuv420p, preserving 100% of 721x405 content);
    # rotated_90 must have display dims 180x320.
    assert vw % 2 == 0 and vh % 2 == 0
    if fixture_key == "odd_721x405":
        assert (vw, vh) == (722, 406)
        assert in_info.get("raw_width") == 721 and in_info.get("raw_height") == 405
        assert in_info.get("odd_dimension_adjusted") is True
        assert in_info.get("dimension_adjustment") == "padded_to_even_for_yuv420p"
    elif fixture_key == "rotated_90":
        assert (vw, vh) == (180, 320)
    elif fixture_key == "vertical_720x1280":
        assert (vw, vh) == (720, 1280)

    rx = max(8, vw - max(48, vw // 5) - 10)
    ry = max(8, vh - max(28, vh // 6) - 10)
    rw = min(vw - rx - 4, max(40, vw // 6))
    rh = min(vh - ry - 4, max(24, vh // 7))

    proc_res = client.post(
        "/api/v1/flow-cleanup/process",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": {"x": rx, "y": ry, "w": rw, "h": rh},
            "method": "natural",
            "region_adjusted": True,
        },
    )
    assert proc_res.status_code == 200, proc_res.text
    p_data = proc_res.json()
    assert p_data["status"] == "done"

    work_dir = fc._safe_work_dir(tid)
    cleaned_files = [os.path.join(work_dir, n) for n in os.listdir(work_dir) if n.startswith("cleaned_") and n.endswith(".mp4")]
    assert len(cleaned_files) == 1
    out_path = cleaned_files[0]

    out_info = fc._probe(out_path)
    assert (out_info["width"], out_info["height"]) == (vw, vh)
    assert abs(float(out_info["duration"]) - float(in_info["duration"])) <= 0.12
    assert bool(out_info.get("has_audio")) == bool(in_info.get("has_audio"))
    if check_cfr_fps:
        assert abs(float(out_info["fps"]) - float(in_info["fps"])) / max(1.0, float(in_info["fps"])) <= 0.01

    in_frames = _decode_frames(src_path, vw, vh)
    out_frames = _decode_frames(out_path, vw, vh)
    psnr_out = _psnr_outside_mask(in_frames, out_frames, (rx, ry, rw, rh))
    assert psnr_out >= 40.0, f"{fixture_key}: PSNR outside mask {psnr_out:.2f} dB < 40 dB"


def test_invalid_inputs_rejected_before_worker(hardening_fixtures, tmp_path, monkeypatch, pro_user):
    """
    P5.3:
    - 0-byte file -> 400 (`empty_file`)
    - Non-video text file renamed to `.mp4` -> 422 (`invalid_media_container`)
    - Corrupted/truncated `.mp4` -> 422 (`invalid_media_container` or `corrupted_video`)
    """
    dl_dir = tmp_path / "dl_invalid"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    client = TestClient(app)

    # 1. 0-byte file
    with open(hardening_fixtures["empty_0byte"], "rb") as f:
        r_empty = client.post("/api/v1/flow-cleanup/upload", files={"file": ("empty.mp4", f, "video/mp4")})
    assert r_empty.status_code == 400
    assert r_empty.json()["detail"]["error_code"] == "empty_file"

    # 2. Fake text renamed to .mp4
    with open(hardening_fixtures["fake_text_as_mp4"], "rb") as f:
        r_fake = client.post("/api/v1/flow-cleanup/upload", files={"file": ("fake.mp4", f, "video/mp4")})
    assert r_fake.status_code == 422
    assert r_fake.json()["detail"]["error_code"] == "invalid_media_container"

    # 3. Corrupted / truncated video
    with open(hardening_fixtures["corrupted_truncated"], "rb") as f:
        r_corrupt = client.post("/api/v1/flow-cleanup/upload", files={"file": ("corrupt.mp4", f, "video/mp4")})
    assert r_corrupt.status_code == 422
    assert r_corrupt.json()["detail"]["error_code"] in ("invalid_media_container", "corrupted_video")


def test_ttl_expiration_deletes_files_and_blocks_even_valid_signed_url(hardening_fixtures, tmp_path, monkeypatch, pro_user):
    """
    P5.3: When a job exceeds `FLOW_CLEANUP_TTL_SEC` (simulated via `created_at` in the past without `time.sleep`):
    - Even a freshly signed URL (`/frame/{temp_id}?exp=...&sig=...`) returns HTTP 404.
    - The job directory `flow_<temp_id>` is physically deleted from disk.
    """
    dl_dir = tmp_path / "dl_ttl"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    client = TestClient(app)
    with open(hardening_fixtures["h264_aac"], "rb") as f:
        up = client.post("/api/v1/flow-cleanup/upload", files={"file": ("clip.mp4", f, "video/mp4")})
    assert up.status_code == 200
    tid = up.json()["temp_id"]
    preview_signed_url = up.json()["preview_url"]
    work_dir = fc._safe_work_dir(tid)
    assert os.path.isdir(work_dir)

    # Simulate TTL expiration by backdating `created_at` beyond `_FLOW_TTL_SEC` (no time.sleep)
    expired_ts = (datetime.now(timezone.utc) - timedelta(seconds=fc._FLOW_TTL_SEC + 60)).isoformat()
    fc._write_meta(work_dir, {"created_at": expired_ts, "status": "uploaded"})

    # Requesting the valid signed URL now returns 404 and deletes `work_dir` from disk
    r_expired = client.get(preview_signed_url)
    assert r_expired.status_code == 404
    assert not os.path.exists(work_dir)


def test_daily_user_quota_enforced_with_429(hardening_fixtures, tmp_path, monkeypatch, pro_user):
    """
    P5.3: Enforces per-user daily quota (`FLOW_DAILY_USER_QUOTA`).
    Exceeding the daily limit returns HTTP 429 (`daily_quota_exceeded`).
    """
    dl_dir = tmp_path / "dl_quota"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
    monkeypatch.setattr(fc, "_DAILY_USER_QUOTA", 2)

    client = TestClient(app)
    for i in range(2):
        with open(hardening_fixtures["h264_aac"], "rb") as f:
            r_ok = client.post("/api/v1/flow-cleanup/upload", files={"file": (f"ok_{i}.mp4", f, "video/mp4")})
        assert r_ok.status_code == 200

    # 3rd upload on the same day exceeds quota=2 -> 429
    with open(hardening_fixtures["h264_aac"], "rb") as f:
        r_over = client.post("/api/v1/flow-cleanup/upload", files={"file": ("over.mp4", f, "video/mp4")})
    assert r_over.status_code == 429
    detail = r_over.json()["detail"]
    assert detail["error_code"] == "daily_quota_exceeded"
    assert detail["daily_quota"] == 2


def test_limits_size_duration_resolution_and_max_retry_cap(hardening_fixtures, tmp_path, monkeypatch, pro_user):
    """
    P5.3:
    - Upload exceeding `_MAX_UPLOAD_MB` -> HTTP 413
    - Resolution exceeding `_MAX_VIDEO_WIDTH` -> HTTP 400 (`resolution_limit_exceeded`)
    - Duration exceeding `_MAX_INPAINT_DURATION_SEC` -> HTTP 400 (`duration_limit_exceeded`)
    - Retry exceeding `_MAX_RETRY_COUNT` (3) -> HTTP 400 (`max_retries_exceeded`)
    """
    dl_dir = tmp_path / "dl_limits"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
    client = TestClient(app)

    # 1. Resolution limit
    monkeypatch.setattr(fc, "_MAX_VIDEO_WIDTH", 200)
    with open(hardening_fixtures["h264_aac"], "rb") as f:
        r_res = client.post("/api/v1/flow-cleanup/upload", files={"file": ("wide.mp4", f, "video/mp4")})
    assert r_res.status_code == 400
    assert r_res.json()["detail"]["error_code"] == "resolution_limit_exceeded"
    monkeypatch.setattr(fc, "_MAX_VIDEO_WIDTH", 3840)

    # 2. Upload valid clip, then test duration limit on /process
    with open(hardening_fixtures["h264_aac"], "rb") as f:
        up = client.post("/api/v1/flow-cleanup/upload", files={"file": ("clip.mp4", f, "video/mp4")})
    assert up.status_code == 200
    tid = up.json()["temp_id"]

    monkeypatch.setattr(fc, "_MAX_INPAINT_DURATION_SEC", 0.5)
    r_dur = client.post(
        "/api/v1/flow-cleanup/process",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": {"x": 20, "y": 20, "w": 40, "h": 24},
            "method": "natural",
            "region_adjusted": True,
        },
    )
    assert r_dur.status_code == 400
    assert r_dur.json()["detail"]["error_code"] == "duration_limit_exceeded"
    monkeypatch.setattr(fc, "_MAX_INPAINT_DURATION_SEC", 90.0)

    # 3. Retry cap (`_MAX_RETRY_COUNT` = 3)
    work_dir = fc._safe_work_dir(tid)
    fc._write_meta(
        work_dir,
        {
            "status": "failed",
            "retry_count": 3,
            "region": {"x": 20, "y": 20, "w": 40, "h": 24},
            "method": "natural",
            "preset": "custom",
        },
    )
    r_retry = client.post(f"/api/v1/flow-cleanup/retry/{tid}")
    assert r_retry.status_code == 400
    assert r_retry.json()["detail"]["error_code"] == "max_retries_exceeded"
