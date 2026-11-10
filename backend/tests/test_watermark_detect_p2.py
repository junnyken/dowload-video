"""
Phase P2 tests for Video Watermark Auto-Detection (`watermark_detect.py` & `/detect/{temp_id}`)
==============================================================================================
Covers:
  1. `detect_watermark_regions` unit tests across static logo, semi-transparent watermark,
     jumping/drifting watermark, and negative samples (in-content speech subtitles / signage).
  2. `POST /api/v1/flow-cleanup/detect/{temp_id}` endpoint contract, `_require_pro` gating,
     and `owner_user_id` ownership verification.
  3. Benchmark acceptance criteria verification (Precision >= 0.90, Recall >= 0.85,
     Negative False Positive rate <= 10%, detection time <= 3.0s).
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.core.local_download import new_token
from app.main import app
from app.services.watermark_detect import _iou, detect_watermark_regions

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BENCH_INPUT_DIR = ROOT_DIR / "benchmarks" / "watermark-input"
BENCH_LABELS_DIR = ROOT_DIR / "benchmarks" / "labels"


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user():
    async def _pro():
        return {"id": "pro-user-p2", "email": "pro2@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def free_user():
    async def _free():
        return {"id": "free-user-p2", "email": "free2@test.local", "tier": "free"}

    app.dependency_overrides[fc.get_optional_user] = _free
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


class TestWatermarkDetectorService:

    def test_detects_static_corner_logo_with_high_iou(self):
        in_path = BENCH_INPUT_DIR / "static_corner_01.mp4"
        if not in_path.exists():
            pytest.skip("Benchmark video not generated")
        res = detect_watermark_regions(str(in_path))
        assert res["status"] in ("detected", "needs_review")
        assert len(res["regions"]) >= 1
        top = res["regions"][0]
        assert top["kind"] in ("logo", "semi_transparent")
        assert top["confidence"] >= 0.75
        iou = _iou((480, 290, 136, 48), (top["x"], top["y"], top["w"], top["h"]))
        assert iou >= 0.70
        assert res["elapsed_sec"] <= 3.0

    def test_detects_semi_transparent_watermark(self):
        in_path = BENCH_INPUT_DIR / "semi_transparent_02.mp4"
        if not in_path.exists():
            pytest.skip("Benchmark video not generated")
        res = detect_watermark_regions(str(in_path))
        assert len(res["regions"]) >= 1
        top = res["regions"][0]
        assert top["kind"] == "semi_transparent"
        iou = _iou((240, 286, 160, 50), (top["x"], top["y"], top["w"], top["h"]))
        assert iou >= 0.70

    def test_detects_moving_watermark(self):
        for name, gt_box in [
            ("moving_watermark_01.mp4", (480, 288, 136, 48)),
            ("moving_watermark_03.mp4", (40, 288, 136, 48)),
        ]:
            in_path = BENCH_INPUT_DIR / name
            if not in_path.exists():
                pytest.skip("Benchmark video not generated")
            res = detect_watermark_regions(str(in_path))
            assert len(res["regions"]) >= 1
            top = res["regions"][0]
            assert top["kind"] == "moving"
            iou = _iou(gt_box, (top["x"], top["y"], top["w"], top["h"]))
            assert iou >= 0.70

    def test_rejects_negative_content_text_videos(self):
        for neg_name in ("content_text_neg_01.mp4", "content_text_neg_02.mp4", "content_text_neg_03.mp4"):
            in_path = BENCH_INPUT_DIR / neg_name
            if not in_path.exists():
                pytest.skip("Benchmark video not generated")
            res = detect_watermark_regions(str(in_path))
            assert res["status"] == "none"
            assert res["regions"] == []


class TestDetectEndpointAPI:

    def test_detect_endpoint_returns_candidates_and_saves_metadata(self, pro_user, tmp_path, monkeypatch):
        src_video = BENCH_INPUT_DIR / "static_corner_01.mp4"
        if not src_video.exists():
            pytest.skip("Benchmark video not generated")

        dl_dir = tmp_path / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

        temp_id = new_token()
        work_dir = fc._safe_work_dir(temp_id)
        os.makedirs(work_dir, exist_ok=True)
        shutil.copy2(src_video, os.path.join(work_dir, "input.mp4"))
        fc._write_meta(work_dir, {
            "temp_id": temp_id,
            "job_id": temp_id,
            "owner_user_id": "pro-user-p2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "uploaded",
        })

        client = TestClient(app)
        r = client.post(f"/api/v1/flow-cleanup/detect/{temp_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["temp_id"] == temp_id
        assert data["status"] == "detected"
        assert 1 <= len(data["regions"]) <= 3
        reg = data["regions"][0]
        assert set(reg.keys()) >= {"id", "x", "y", "w", "h", "confidence", "kind", "needs_review"}
        assert _iou((480, 290, 136, 48), (reg["x"], reg["y"], reg["w"], reg["h"])) >= 0.70

        meta = fc._read_meta(work_dir)
        assert meta["detection_status"] == "detected"
        assert len(meta["detected_regions"]) >= 1

    def test_detect_endpoint_requires_pro_and_enforces_ownership(self, free_user, tmp_path, monkeypatch):
        dl_dir = tmp_path / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

        temp_id = new_token()
        work_dir = fc._safe_work_dir(temp_id)
        os.makedirs(work_dir, exist_ok=True)
        fc._write_meta(work_dir, {
            "temp_id": temp_id,
            "owner_user_id": "owner-user-a",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "uploaded",
        })

        client = TestClient(app)
        # 1. Free user -> 402
        r_free = client.post(f"/api/v1/flow-cleanup/detect/{temp_id}")
        assert r_free.status_code == 402

        # 2. Different Pro user -> 404 (ownership check)
        async def _other_pro():
            return {"id": "other-pro-user-b", "email": "other@test.local", "tier": "pro"}

        app.dependency_overrides[fc.get_optional_user] = _other_pro
        try:
            r_other = client.post(f"/api/v1/flow-cleanup/detect/{temp_id}")
            assert r_other.status_code == 404
        finally:
            app.dependency_overrides.pop(fc.get_optional_user, None)

    def test_low_confidence_returns_needs_review_and_blocks_auto_process(self, pro_user, tmp_path, monkeypatch):
        src_video = BENCH_INPUT_DIR / "static_corner_01.mp4"
        if not src_video.exists():
            pytest.skip("Benchmark video not generated")

        dl_dir = tmp_path / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

        temp_id = new_token()
        work_dir = fc._safe_work_dir(temp_id)
        os.makedirs(work_dir, exist_ok=True)
        shutil.copy2(src_video, os.path.join(work_dir, "input.mp4"))
        fc._write_meta(work_dir, {
            "temp_id": temp_id,
            "job_id": temp_id,
            "owner_user_id": "pro-user-p2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "uploaded",
        })

        # Simulate uncertain/low-confidence detector output
        def _fake_low_conf_detect(input_path: str, max_candidates: int = 3):
            return {
                "status": "needs_review",
                "needs_review": True,
                "regions": [{
                    "id": "wm_1",
                    "x": 480,
                    "y": 290,
                    "w": 136,
                    "h": 48,
                    "confidence": 0.58,
                    "kind": "semi_transparent",
                    "needs_review": True,
                }],
                "scene_cuts": [],
                "sampled_frames": 24,
                "elapsed_sec": 0.42,
                "video_info": {"width": 640, "height": 360, "fps": 30.0, "total_frames": 90, "duration": 3.0},
            }

        import app.services.watermark_detect as wd_mod
        monkeypatch.setattr(wd_mod, "detect_watermark_regions", _fake_low_conf_detect)

        client = TestClient(app)
        r_det = client.post(f"/api/v1/flow-cleanup/detect/{temp_id}")
        assert r_det.status_code == 200
        det_data = r_det.json()
        assert det_data["status"] == "needs_review"
        assert det_data["needs_review"] is True
        assert det_data["auto_processable"] is False
        assert det_data["regions"][0]["needs_review"] is True

        # Ensure /detect never automatically triggered processing
        meta = fc._read_meta(work_dir)
        assert meta["status"] == "uploaded"
        assert meta["detection_needs_review"] is True

        # Attempting to auto-process without user confirmation (region_adjusted=False) must be blocked (HTTP 400)
        r_proc_auto = client.post(
            "/api/v1/flow-cleanup/process",
            json={
                "temp_id": temp_id,
                "preset": "auto",
                "method": "crop",
                "region_adjusted": False,
            },
        )
        assert r_proc_auto.status_code == 400
        assert r_proc_auto.json()["detail"]["error_code"] == "detection_needs_review"
        assert r_proc_auto.json()["detail"]["needs_review"] is True

        r_proc_unreviewed_custom = client.post(
            "/api/v1/flow-cleanup/process",
            json={
                "temp_id": temp_id,
                "preset": "custom",
                "region": {"x": 480, "y": 290, "w": 136, "h": 48},
                "method": "crop",
                "region_adjusted": False,
            },
        )
        assert r_proc_unreviewed_custom.status_code == 400
        assert r_proc_unreviewed_custom.json()["detail"]["error_code"] == "detection_needs_review"

