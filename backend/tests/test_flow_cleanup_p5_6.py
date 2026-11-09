"""
Phase P5.6 Regression Tests — Post-Review Hardening Fixes
=========================================================
Mục 3 (P5.6.3): Dynamic effective max duration per resolution/FPS & probe fields.
"""
import os
import sys
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.api import flow_cleanup


class TestP563EffectiveMaxDuration:
    """Verify _compute_effective_max_duration and _validate_job_limits across profiles."""

    @pytest.mark.parametrize(
        "vw,vh,fps,method,expected_max_sec",
        [
            (640, 360, 24.0, "natural", 60),
            (640, 360, 30.0, "natural", 60),
            (854, 480, 30.0, "natural", 54),
            (720, 1280, 24.0, "natural", 30),
            (720, 1280, 30.0, "natural", 24),
            (720, 1280, 60.0, "natural", 12),
            (1080, 1920, 24.0, "natural", 13),
            (1080, 1920, 30.0, "natural", 10),
            (1080, 1920, 60.0, "natural", 5),
            (1080, 1920, 30.0, "crop", 600),
            (1080, 1920, 30.0, "blur", 600),
        ],
    )
    def test_compute_effective_max_duration_profiles(
        self, vw, vh, fps, method, expected_max_sec
    ):
        actual = flow_cleanup._compute_effective_max_duration(vw, vh, fps, method)
        assert actual == expected_max_sec

    def test_validate_job_limits_reports_exact_max_duration_for_1080p30(self, tmp_path):
        dummy_video = tmp_path / "dummy.mp4"
        dummy_video.write_bytes(b"\x00" * 1024)

        # 1080x1920 @ 30fps allows at most 10s for natural inpaint; 15s must be rejected
        info_ok = {"width": 1080, "height": 1920, "fps": 30.0, "duration": 10.0}
        flow_cleanup._validate_job_limits(str(dummy_video), info_ok, method="natural")

        info_too_long = {"width": 1080, "height": 1920, "fps": 30.0, "duration": 15.0}
        with pytest.raises(HTTPException) as exc_info:
            flow_cleanup._validate_job_limits(str(dummy_video), info_too_long, method="natural")
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert detail["error_code"] == "duration_limit_exceeded"
        assert detail["max_duration_sec"] == 10
        assert "10s" in detail["user_message"]

    def test_validate_job_limits_reports_exact_max_duration_for_720p30(self, tmp_path):
        dummy_video = tmp_path / "dummy.mp4"
        dummy_video.write_bytes(b"\x00" * 1024)

        # 720x1280 @ 30fps allows at most 24s for natural inpaint; 30s must be rejected
        info_ok = {"width": 720, "height": 1280, "fps": 30.0, "duration": 24.0}
        flow_cleanup._validate_job_limits(str(dummy_video), info_ok, method="natural")

        info_too_long = {"width": 720, "height": 1280, "fps": 30.0, "duration": 30.0}
        with pytest.raises(HTTPException) as exc_info:
            flow_cleanup._validate_job_limits(str(dummy_video), info_too_long, method="natural")
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert detail["error_code"] == "duration_limit_exceeded"
        assert detail["max_duration_sec"] == 24
        assert "24s" in detail["user_message"]
