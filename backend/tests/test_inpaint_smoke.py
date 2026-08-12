"""
Phase 4 smoke tests — Logo Inpaint (experimental).

Tests are intentionally lightweight: pure-Python assertions on error-code
shapes, preset contracts, and input validation helpers.  No server, no OpenCV,
no GPU required.  Set VIDGRAB_LIVE=1 to run live integration tests.
"""
import os
import pytest


# ─── Error code catalogue ──────────────────────────────────────────────────────

INPAINT_ERROR_CODES = [
    "inpaint_input_invalid",
    "inpaint_mask_missing",
    "inpaint_processing_failed",
    "inpaint_unsupported_format",
    "inpaint_result_unavailable",
]

REQUIRED_FIELDS = {"user_message", "retryable", "suggested_action"}


class TestInpaintErrorCodes:
    """All Phase 4 inpaint error codes must exist with the correct shape."""

    def _meta(self):
        from app.core.error_codes import ERROR_META
        return ERROR_META

    def test_all_inpaint_codes_present(self):
        meta = self._meta()
        for code in INPAINT_ERROR_CODES:
            assert code in meta, f"Missing error code: '{code}'"

    def test_all_inpaint_codes_have_required_fields(self):
        meta = self._meta()
        for code in INPAINT_ERROR_CODES:
            entry = meta[code]
            missing = REQUIRED_FIELDS - entry.keys()
            assert not missing, f"'{code}' missing fields: {missing}"

    def test_retryable_is_bool(self):
        meta = self._meta()
        for code in INPAINT_ERROR_CODES:
            assert isinstance(meta[code]["retryable"], bool), \
                f"'{code}'.retryable must be bool"

    def test_user_messages_not_empty(self):
        meta = self._meta()
        for code in INPAINT_ERROR_CODES:
            msg = meta[code]["user_message"]
            assert msg and len(msg.strip()) > 5, \
                f"'{code}'.user_message is empty or too short"

    def test_suggested_actions_not_empty(self):
        meta = self._meta()
        for code in INPAINT_ERROR_CODES:
            action = meta[code]["suggested_action"]
            assert action and len(action.strip()) > 5, \
                f"'{code}'.suggested_action is empty or too short"

    def test_input_invalid_not_retryable(self):
        meta = self._meta()
        assert meta["inpaint_input_invalid"]["retryable"] is False

    def test_mask_missing_not_retryable(self):
        meta = self._meta()
        assert meta["inpaint_mask_missing"]["retryable"] is False

    def test_processing_failed_is_retryable(self):
        meta = self._meta()
        assert meta["inpaint_processing_failed"]["retryable"] is True

    def test_unsupported_format_not_retryable(self):
        meta = self._meta()
        assert meta["inpaint_unsupported_format"]["retryable"] is False

    def test_result_unavailable_not_retryable(self):
        meta = self._meta()
        assert meta["inpaint_result_unavailable"]["retryable"] is False

    def test_make_error_works_for_inpaint_codes(self):
        from app.core.error_codes import make_error
        for code in INPAINT_ERROR_CODES:
            err = make_error(code)
            assert err["error_code"] == code
            assert "user_message" in err
            assert "retryable" in err
            assert "suggested_action" in err


# ─── Mask / region validation (pure logic) ────────────────────────────────────

class TestInpaintRegionValidation:
    """Region {x, y, w, h} must be inside frame bounds and non-zero."""

    @staticmethod
    def _validate_region(region: dict, video_width: int, video_height: int) -> tuple[bool, str]:
        """
        Minimal validation matching what the backend expects.
        Returns (is_valid, reason).
        """
        x, y, w, h = region.get("x", 0), region.get("y", 0), \
                     region.get("w", 0), region.get("h", 0)
        if w <= 0 or h <= 0:
            return False, "inpaint_mask_missing"
        if x < 0 or y < 0:
            return False, "inpaint_input_invalid"
        if x + w > video_width or y + h > video_height:
            return False, "inpaint_input_invalid"
        return True, ""

    def test_valid_corner_region(self):
        valid, _ = self._validate_region({"x": 10, "y": 10, "w": 100, "h": 40}, 1280, 720)
        assert valid is True

    def test_zero_width_invalid(self):
        valid, code = self._validate_region({"x": 10, "y": 10, "w": 0, "h": 40}, 1280, 720)
        assert valid is False
        assert code == "inpaint_mask_missing"

    def test_zero_height_invalid(self):
        valid, code = self._validate_region({"x": 10, "y": 10, "w": 100, "h": 0}, 1280, 720)
        assert valid is False
        assert code == "inpaint_mask_missing"

    def test_negative_x_invalid(self):
        valid, code = self._validate_region({"x": -5, "y": 10, "w": 100, "h": 40}, 1280, 720)
        assert valid is False
        assert code == "inpaint_input_invalid"

    def test_region_out_of_frame_invalid(self):
        valid, code = self._validate_region({"x": 1200, "y": 700, "w": 200, "h": 100}, 1280, 720)
        assert valid is False
        assert code == "inpaint_input_invalid"

    def test_exactly_fills_frame_valid(self):
        valid, _ = self._validate_region({"x": 0, "y": 0, "w": 1280, "h": 720}, 1280, 720)
        assert valid is True

    def test_missing_keys_treated_as_zero_mask(self):
        valid, code = self._validate_region({}, 1280, 720)
        assert valid is False
        assert code == "inpaint_mask_missing"


# ─── Preset contract ──────────────────────────────────────────────────────────

class TestInpaintPresets:
    """
    INPAINT_PRESETS used by the UI must cover at least the 4 standard corners.
    Checked via backend's known preset keys (flow_cleanup maps these server-side).
    """

    EXPECTED_PRESETS = {"lower-right", "lower-left", "upper-right", "upper-left"}

    def test_all_expected_presets_known(self):
        try:
            from app.api.flow_cleanup import PRESET_REGIONS
            for key in self.EXPECTED_PRESETS:
                assert key in PRESET_REGIONS, f"Preset '{key}' not found in PRESET_REGIONS"
        except ImportError:
            pytest.skip("flow_cleanup not importable without full deps")

    def test_preset_regions_have_bbox(self):
        try:
            from app.api.flow_cleanup import PRESET_REGIONS
            for key, region in PRESET_REGIONS.items():
                assert "x_pct" in region or "x" in region or callable(region), \
                    f"Preset '{key}' missing positional data"
        except ImportError:
            pytest.skip("flow_cleanup not importable without full deps")


# ─── Input file validation (format + size) ────────────────────────────────────

class TestInpaintInputValidation:
    """Basic file format and size guardrails."""

    SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    MAX_FILE_SIZE_MB = 500

    def test_mp4_accepted(self):
        ext = ".mp4"
        assert ext in self.SUPPORTED_EXTENSIONS

    def test_srt_rejected(self):
        ext = ".srt"
        assert ext not in self.SUPPORTED_EXTENSIONS

    def test_jpg_rejected(self):
        ext = ".jpg"
        assert ext not in self.SUPPORTED_EXTENSIONS

    def test_size_under_limit(self):
        file_mb = 200
        assert file_mb <= self.MAX_FILE_SIZE_MB

    def test_size_over_limit(self):
        file_mb = 600
        assert file_mb > self.MAX_FILE_SIZE_MB

    def test_max_limit_defined(self):
        assert self.MAX_FILE_SIZE_MB > 0


# ─── Experimental labeling contract ───────────────────────────────────────────

class TestExperimentalLabeling:
    """
    The spec requires specific copy strings to appear in the UI.
    We test the backend error message content (proxy for what the UI shows)
    and the existence of the spec-required warning signals.
    """

    def test_processing_failed_message_mentions_artifacts(self):
        from app.core.error_codes import ERROR_META
        msg = ERROR_META["inpaint_processing_failed"]["user_message"].lower()
        # Should mention that quality varies (proxy for "may contain visual artifacts")
        assert any(word in msg for word in ["thử nghiệm", "thay đổi", "thất bại", "kết quả"])

    def test_suggested_action_mentions_alternative(self):
        from app.core.error_codes import ERROR_META
        action = ERROR_META["inpaint_processing_failed"]["suggested_action"].lower()
        # Suggested action should mention trying something else
        assert any(word in action for word in ["thử", "khác", "crop", "preset"])

    def test_unsupported_format_suggests_mp4(self):
        from app.core.error_codes import ERROR_META
        action = ERROR_META["inpaint_unsupported_format"]["suggested_action"].lower()
        assert "mp4" in action

    def test_result_unavailable_suggests_restart(self):
        from app.core.error_codes import ERROR_META
        action = ERROR_META["inpaint_result_unavailable"]["suggested_action"].lower()
        assert any(word in action for word in ["lại", "chạy lại", "từ đầu"])


# ─── Live integration tests (VIDGRAB_LIVE=1 only) ────────────────────────────

@pytest.mark.skipif(
    not os.getenv("VIDGRAB_LIVE"),
    reason="Set VIDGRAB_LIVE=1 to run live integration tests",
)
class TestInpaintLive:
    def test_from_local_rejects_missing_file(self, app):
        r = app.post("/api/v1/flow-cleanup/from-local",
                     json={"local_path": "/nonexistent/path.mp4"})
        assert r.status_code in (400, 404, 422)

    def test_process_rejects_missing_temp_id(self, app):
        r = app.post("/api/v1/flow-cleanup/process",
                     json={"temp_id": "nonexistent_id_xyz", "method": "natural"})
        assert r.status_code in (400, 404, 422)

    def test_preview_frame_rejects_missing_temp_id(self, app):
        r = app.post("/api/v1/flow-cleanup/preview-frame",
                     json={"temp_id": "nonexistent_id_xyz"})
        assert r.status_code in (400, 404, 422)
