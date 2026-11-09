"""
Phase P3 tests for Per-Frame Watermark Tracking (`watermark_track.py`, `/track/{temp_id}`, & dynamic `flow_inpaint.py`)
=======================================================================================================================
Covers:
  1. Static, drifting (`moving_drift`), and jumping (`moving_jump2`, `moving_jump3`) watermark tracking:
     - Median IoU >= 0.85
     - Max drift <= 3.0% of frame diagonal
  2. Scene cut detection (`scene_cut_01..03`).
  3. Lost-track / disappearing watermark detection (`needs_review=True`, `auto_erasable=False`, `review_keyframes`)
     and segment-scoped keyframe correction (`retrack_with_keyframes`).
  4. `build_frame_mask` / `build_frame_masks` and `flow_inpaint.process_video_natural` with `frame_boxes` & `frame_masks`
     (while keeping backward compatibility with fixed `(x, y, w, h)`).
  5. API endpoint `/api/v1/flow-cleanup/track/{temp_id}` (Pro check, ownership check, keyframe retracking)
     and `/api/v1/flow-cleanup/process` guard blocking auto-erasure when `tracking_needs_review=True`.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.api import flow_inpaint as fi
from app.core.local_download import new_token
from app.main import app
from app.services.watermark_track import (
    build_frame_mask,
    build_frame_masks,
    evaluate_tracking_against_gt,
    retrack_with_keyframes,
    track_watermark_in_video,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
BENCH_INPUT_DIR = ROOT_DIR / "benchmarks" / "watermark-input"
BENCH_REF_DIR = ROOT_DIR / "benchmarks" / "watermark-reference"
BENCH_LABELS_DIR = ROOT_DIR / "benchmarks" / "labels"


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user():
    async def _pro():
        return {"id": "pro-user-p3", "email": "pro3@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def free_user():
    async def _free():
        return {"id": "free-user-p3", "email": "free3@test.local", "tier": "free"}

    app.dependency_overrides[fc.get_optional_user] = _free
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


class TestWatermarkTrackerService:

    @pytest.mark.parametrize(
        "stem",
        [
            "static_corner_01",
            "moving_watermark_01",
            "moving_watermark_02",
            "moving_watermark_03",
        ],
    )
    def test_tracks_static_jumping_and_drifting_watermarks_above_target(self, stem):
        in_path = BENCH_INPUT_DIR / f"{stem}.mp4"
        lbl_path = BENCH_LABELS_DIR / f"{stem}.json"
        if not (in_path.exists() and lbl_path.exists()):
            pytest.skip("Benchmark video not generated")

        lbl = json.loads(lbl_path.read_text(encoding="utf-8"))
        gt_boxes = lbl["regions"][0]["boxes"]
        box0 = gt_boxes["0"]

        tr = track_watermark_in_video(str(in_path), init_box=box0)
        ev = evaluate_tracking_against_gt(tr, gt_boxes, lbl["width"], lbl["height"])

        assert tr["status"] == "tracked"
        assert tr["needs_review"] is False
        assert tr["auto_erasable"] is True
        assert ev["median_iou"] >= 0.85, f"{stem} median_iou={ev['median_iou']} < 0.85"
        assert ev["max_drift_pct"] <= 3.0, f"{stem} max_drift_pct={ev['max_drift_pct']}% > 3.0%"

    def test_detects_exact_scene_cuts(self):
        expected = {
            "scene_cut_01": [75],
            "scene_cut_02": [50, 100],
            "scene_cut_03": [75],
        }
        for stem, gt_cuts in expected.items():
            in_path = BENCH_INPUT_DIR / f"{stem}.mp4"
            lbl_path = BENCH_LABELS_DIR / f"{stem}.json"
            if not (in_path.exists() and lbl_path.exists()):
                pytest.skip("Benchmark video not generated")
            lbl = json.loads(lbl_path.read_text(encoding="utf-8"))
            gt_boxes = lbl["regions"][0]["boxes"]
            tr = track_watermark_in_video(str(in_path), init_box=gt_boxes["0"])
            ev = evaluate_tracking_against_gt(tr, gt_boxes, lbl["width"], lbl["height"])
            assert tr["scene_cuts"] == gt_cuts, f"{stem}: expected cuts {gt_cuts}, got {tr['scene_cuts']}"
            assert ev["median_iou"] >= 0.85
            assert ev["max_drift_pct"] <= 3.0

    def test_detects_lost_track_and_recomputes_segment_on_keyframe(self, tmp_path):
        import sys
        sys.path.insert(0, str(ROOT_DIR / "benchmarks"))
        from run_benchmark import _draw_watermark_patch, _make_base_canvas, encode_frames_lossless

        vw, vh = 640, 360
        canvas = _make_base_canvas(vw, vh, 0)
        frames = []
        for i in range(60):
            ox, oy = int(i * 1.5), 40
            fr = canvas[oy:oy + vh, ox:ox + vw].copy()
            if i < 30:
                fr = _draw_watermark_patch(fr, [480, 288, 136, 48], "LOST TEST", alpha=0.88)
            frames.append(fr)

        mp4_path = tmp_path / "lost_watermark.mp4"
        encode_frames_lossless(frames, mp4_path, fps=30.0)

        tr = track_watermark_in_video(str(mp4_path), init_box=[480, 288, 136, 48])
        assert tr["needs_review"] is True
        assert tr["auto_erasable"] is False
        assert len(tr["lost_frames"]) >= 25
        assert 30 in tr["review_keyframes"]

        # Supply keyframe at frame 30 -> only segment [30, 60) is recomputed, frames [0, 30) untouched
        updated = retrack_with_keyframes(
            str(mp4_path),
            tr,
            keyframes=[{"frame_idx": 30, "x": 480, "y": 288, "w": 136, "h": 48}],
        )
        assert updated["recomputed_ranges"] == [(30, 60)]
        assert updated["frames"][30]["status"] == "keyframe"
        assert updated["frames"][30]["confidence"] == 1.0

    def test_build_frame_masks_and_dynamic_inpaint_engine(self, tmp_path):
        in_path = BENCH_INPUT_DIR / "moving_watermark_01.mp4"
        ref_path = BENCH_REF_DIR / "moving_watermark_01.mp4"
        lbl_path = BENCH_LABELS_DIR / "moving_watermark_01.json"
        if not (in_path.exists() and ref_path.exists() and lbl_path.exists()):
            pytest.skip("Benchmark video not generated")

        lbl = json.loads(lbl_path.read_text(encoding="utf-8"))
        gt_boxes = lbl["regions"][0]["boxes"]
        box0 = gt_boxes["0"]

        # Verify mask helpers
        m0 = build_frame_mask(box0, 640, 360, dilate_px=12)
        assert m0.shape == (360, 640) and m0.dtype == np.uint8
        assert int((m0 > 0).sum()) > box0[2] * box0[3]

        ms = build_frame_masks([gt_boxes["0"], gt_boxes["80"]], 640, 360, dilate_px=8)
        assert len(ms) == 2

        # Run process_video_natural with tracked frame_boxes
        tr = track_watermark_in_video(str(in_path), init_box=box0)
        out_path = tmp_path / "cleaned_moving.mp4"
        stats = fi.process_video_natural(
            str(in_path),
            str(out_path),
            box0[0], box0[1], box0[2], box0[3],
            640, 360,
            fps=30.0,
            duration=5.0,
            frame_boxes=tr["boxes_by_frame"],
        )
        assert stats["dynamic_mask"] is True
        assert stats["total_frames"] == 150
        assert stats["temporal_frames"] > 100
        assert out_path.exists() and out_path.stat().st_size > 0


class TestTrackEndpointAndProcessGuard:

    def test_track_endpoint_permissions_and_execution(self, pro_user, tmp_path, monkeypatch):
        src_video = BENCH_INPUT_DIR / "moving_watermark_01.mp4"
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
            "owner_user_id": "pro-user-p3",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "uploaded",
        })

        client = TestClient(app)
        r = client.post(
            f"/api/v1/flow-cleanup/track/{temp_id}",
            json={"region": {"x": 480, "y": 288, "w": 136, "h": 48}},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["status"] == "tracked"
        assert data["needs_review"] is False
        assert data["boxes_by_frame"]["0"] == [480, 288, 136, 48]
        assert data["boxes_by_frame"]["80"] == [24, 24, 136, 48]

        # Another Pro user -> 404
        async def _other_pro():
            return {"id": "other-pro", "email": "other@test.local", "tier": "pro"}

        app.dependency_overrides[fc.get_optional_user] = _other_pro
        assert client.post(f"/api/v1/flow-cleanup/track/{temp_id}").status_code == 404

        # Free user -> 402
        async def _free():
            return {"id": "free-u", "email": "free@test.local", "tier": "free"}

        app.dependency_overrides[fc.get_optional_user] = _free
        assert client.post(f"/api/v1/flow-cleanup/track/{temp_id}").status_code == 402

    def test_process_blocks_auto_erase_when_tracking_needs_review(self, pro_user, tmp_path, monkeypatch):
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
            "owner_user_id": "pro-user-p3",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "uploaded",
            "tracking_status": "needs_review",
            "tracking_needs_review": True,
            "tracking_review_keyframes": [45],
        })

        client = TestClient(app)
        r_blocked = client.post(
            "/api/v1/flow-cleanup/process",
            json={
                "temp_id": temp_id,
                "preset": "custom",
                "region": {"x": 480, "y": 290, "w": 136, "h": 48},
                "method": "crop",
                "region_adjusted": False,
            },
        )
        assert r_blocked.status_code == 400
        detail = r_blocked.json()["detail"]
        assert detail["error_code"] == "tracking_needs_review"
        assert detail["needs_review"] is True
        assert detail["review_keyframes"] == [45]
