"""
Phase 7 smoke tests — Stability, Hardening, and Release-Ready Polish.

All tests avoid importing app.main or app.api.routes directly (host Pydantic version
mismatch). Structural checks use source-file inspection.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── helpers ──────────────────────────────────────────────────────────────────

def _src(rel):
    return open(os.path.join(BASE, rel), encoding="utf-8").read()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Canonical state model: state_model.py
# ═══════════════════════════════════════════════════════════════════════════════

sys.path.insert(0, BASE)
from app.core.state_model import (
    VALID_JOB_STATES, TERMINAL_STATES, RETRYABLE_STATES, ACTIVE_STATES,
    STATE_TRANSITIONS, MAX_MANUAL_RETRIES, MAX_AUTO_RETRIES,
    RETRY_BACKOFF_SECONDS, is_valid_transition, should_allow_retry,
    STATE_UI, get_ui,
)


class TestStateModelBasics:
    def test_valid_states_count(self):
        assert len(VALID_JOB_STATES) == 13  # added stale, retrying, partial in stability phase

    def test_required_states_present(self):
        for s in ["pending", "queued", "processing", "success", "failed",
                  "cancelled", "expired", "metadata_only", "archived", "abandoned"]:
            assert s in VALID_JOB_STATES

    def test_terminal_states_subset(self):
        assert TERMINAL_STATES.issubset(VALID_JOB_STATES)

    def test_retryable_states_subset(self):
        assert RETRYABLE_STATES.issubset(VALID_JOB_STATES)

    def test_active_states_subset(self):
        assert ACTIVE_STATES.issubset(VALID_JOB_STATES)

    def test_disjoint_terminal_active(self):
        assert TERMINAL_STATES.isdisjoint(ACTIVE_STATES)

    def test_transitions_cover_all_states(self):
        assert set(STATE_TRANSITIONS.keys()) == VALID_JOB_STATES

    def test_transitions_targets_are_valid(self):
        for src, targets in STATE_TRANSITIONS.items():
            for t in targets:
                assert t in VALID_JOB_STATES, f"Invalid target {t!r} from {src!r}"


class TestStateTransitions:
    def test_pending_to_queued(self):
        assert is_valid_transition("pending", "queued")

    def test_pending_to_processing(self):
        assert is_valid_transition("pending", "processing")

    def test_processing_to_success(self):
        assert is_valid_transition("processing", "success")

    def test_processing_to_failed(self):
        assert is_valid_transition("processing", "failed")

    def test_failed_to_pending_allowed(self):
        assert is_valid_transition("failed", "pending")

    def test_success_to_archived(self):
        assert is_valid_transition("success", "archived")

    def test_success_to_expired(self):
        assert is_valid_transition("success", "expired")

    def test_expired_to_metadata_only(self):
        assert is_valid_transition("expired", "metadata_only")

    def test_cancelled_has_no_outgoing(self):
        assert not STATE_TRANSITIONS["cancelled"]

    def test_metadata_only_has_no_outgoing(self):
        assert not STATE_TRANSITIONS["metadata_only"]

    def test_invalid_backwards_transition(self):
        assert not is_valid_transition("success", "pending")

    def test_invalid_direct_pending_to_archived(self):
        assert not is_valid_transition("pending", "archived")

    def test_unknown_state_returns_false(self):
        assert not is_valid_transition("nonexistent", "pending")


class TestRetryPolicy:
    def test_max_manual_retries_is_5(self):
        assert MAX_MANUAL_RETRIES == 5

    def test_max_auto_retries_is_3(self):
        assert MAX_AUTO_RETRIES == 3

    def test_backoff_has_5_entries(self):
        assert len(RETRY_BACKOFF_SECONDS) == 5

    def test_backoff_starts_at_zero(self):
        assert RETRY_BACKOFF_SECONDS[0] == 0

    def test_backoff_is_increasing(self):
        for i in range(len(RETRY_BACKOFF_SECONDS) - 1):
            assert RETRY_BACKOFF_SECONDS[i] <= RETRY_BACKOFF_SECONDS[i + 1]

    def test_should_allow_retry_failed(self):
        allowed, reason = should_allow_retry({"status": "failed", "recovery_attempts": 0})
        assert allowed
        assert reason == ""

    def test_should_allow_retry_abandoned(self):
        allowed, _ = should_allow_retry({"status": "abandoned", "recovery_attempts": 2})
        assert allowed

    def test_should_deny_retry_success(self):
        allowed, reason = should_allow_retry({"status": "success", "recovery_attempts": 0})
        assert not allowed
        assert "not retryable" in reason

    def test_should_deny_retry_max_reached(self):
        allowed, reason = should_allow_retry({"status": "failed", "recovery_attempts": 5})
        assert not allowed
        assert "Max retries" in reason

    def test_should_deny_retry_over_max(self):
        allowed, _ = should_allow_retry({"status": "failed", "recovery_attempts": 99})
        assert not allowed

    def test_missing_attempts_defaults_to_zero(self):
        allowed, _ = should_allow_retry({"status": "failed"})
        assert allowed


class TestStateUI:
    def test_all_states_have_ui(self):
        for s in VALID_JOB_STATES:
            assert s in STATE_UI, f"Missing STATE_UI entry for {s!r}"

    def test_ui_entries_have_required_keys(self):
        for s, meta in STATE_UI.items():
            assert "label" in meta, f"Missing 'label' for {s!r}"
            assert "color" in meta, f"Missing 'color' for {s!r}"
            assert "icon" in meta,  f"Missing 'icon' for {s!r}"

    def test_get_ui_known_state(self):
        ui = get_ui("success")
        assert ui["label"] == "Thành công"
        assert ui["color"] == "success"

    def test_get_ui_unknown_state_fallback(self):
        ui = get_ui("garbage_state")
        assert "label" in ui
        assert "color" in ui

    def test_get_ui_empty_string_fallback(self):
        ui = get_ui("")
        assert "label" in ui

    def test_success_label_vietnamese(self):
        assert "Thành công" in STATE_UI["success"]["label"]

    def test_failed_label_vietnamese(self):
        assert "Thất bại" in STATE_UI["failed"]["label"]

    def test_processing_icon_is_loader(self):
        assert "Loader" in STATE_UI["processing"]["icon"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Correlation ID middleware — source inspection
# ═══════════════════════════════════════════════════════════════════════════════

class TestCorrelationIDMiddleware:
    def _main_src(self):
        return _src("app/main.py")

    def test_uuid_imported(self):
        assert "import uuid" in self._main_src()

    def test_middleware_class_defined(self):
        assert "class CorrelationIDMiddleware" in self._main_src()

    def test_base_http_middleware_parent(self):
        assert "BaseHTTPMiddleware" in self._main_src()

    def test_x_request_id_header_read(self):
        src = self._main_src()
        assert "X-Request-ID" in src

    def test_x_request_id_set_on_response(self):
        src = self._main_src()
        # Both reading and writing
        assert src.count("X-Request-ID") >= 2

    def test_uuid_hex_generated(self):
        assert "uuid.uuid4().hex" in self._main_src()

    def test_middleware_registered(self):
        src = self._main_src()
        assert "add_middleware(CorrelationIDMiddleware)" in src

    def test_request_state_request_id_set(self):
        assert "request.state.request_id" in self._main_src()

    def test_correlation_registered_before_security(self):
        src = self._main_src()
        corr_pos = src.find("add_middleware(CorrelationIDMiddleware)")
        sec_pos  = src.find("add_middleware(SecurityHeadersMiddleware)")
        assert corr_pos < sec_pos, "CorrelationIDMiddleware must be registered before SecurityHeadersMiddleware"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Retry endpoint hardening — source inspection
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetryEndpointHardening:
    def _routes_src(self):
        return _src("app/api/routes.py")

    def test_retry_endpoint_exists(self):
        assert "retry-failed" in self._routes_src()

    def test_retryable_states_imported(self):
        src = self._routes_src()
        assert "RETRYABLE_STATES" in src

    def test_max_manual_retries_imported(self):
        assert "MAX_MANUAL_RETRIES" in self._routes_src()

    def test_recovery_attempts_incremented(self):
        src = self._routes_src()
        assert "recovery_attempts" in src

    def test_retry_skipped_count_returned(self):
        src = self._routes_src()
        assert "skipped" in src

    def test_state_model_import_present(self):
        src = self._routes_src()
        assert "state_model" in src


# ═══════════════════════════════════════════════════════════════════════════════
# 4. ErrorBoundary component — source inspection
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorBoundaryComponent:
    def _src(self):
        return _src("../frontend/src/components/ErrorBoundary.jsx")

    def test_file_exists(self):
        path = os.path.join(BASE, "../frontend/src/components/ErrorBoundary.jsx")
        assert os.path.exists(path)

    def test_class_component(self):
        assert "class ErrorBoundary extends Component" in self._src()

    def test_get_derived_state_from_error(self):
        assert "getDerivedStateFromError" in self._src()

    def test_component_did_catch(self):
        assert "componentDidCatch" in self._src()

    def test_handle_reset_method(self):
        assert "handleReset" in self._src()

    def test_handle_reload_method(self):
        assert "handleReload" in self._src()

    def test_window_location_reload(self):
        assert "window.location.reload" in self._src()

    def test_network_error_detection(self):
        src = self._src()
        assert "fetch" in src.lower() or "network" in src.lower()

    def test_alert_triangle_icon(self):
        assert "AlertTriangle" in self._src()

    def test_default_export(self):
        assert "export default class ErrorBoundary" in self._src()

    def test_telegram_support_link(self):
        assert "t.me" in self._src() or "telegram" in self._src().lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. ErrorBoundary wired in App.jsx — source inspection
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorBoundaryWiring:
    def _app_src(self):
        return _src("../frontend/src/App.jsx")

    def test_error_boundary_imported(self):
        assert "import ErrorBoundary" in self._app_src()

    def test_error_boundary_wraps_main(self):
        src = self._app_src()
        assert "<ErrorBoundary>" in src

    def test_error_boundary_closed(self):
        src = self._app_src()
        assert "</ErrorBoundary>" in src


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Extended status badges in HistoryContent.jsx — source inspection
# ═══════════════════════════════════════════════════════════════════════════════

class TestExtendedStatusBadges:
    def _src(self):
        return _src("../frontend/src/components/HistoryContent.jsx")

    def test_cancelled_badge(self):
        assert "Đã hủy" in self._src()

    def test_expired_badge(self):
        assert "Hết hạn" in self._src()

    def test_metadata_only_badge(self):
        assert "Chỉ metadata" in self._src()

    def test_archived_badge(self):
        assert "Đã lưu trữ" in self._src()

    def test_abandoned_badge(self):
        assert "Đã từ bỏ" in self._src()

    def test_queued_badge(self):
        assert "Trong hàng đợi" in self._src()

    def test_pending_handled(self):
        src = self._src()
        assert "Chờ xử lý" in src or "pending" in src

    def test_all_states_in_switch(self):
        src = self._src()
        for state in ["success", "failed", "processing", "cancelled",
                      "expired", "metadata_only", "archived", "abandoned", "queued"]:
            assert f"case '{state}'" in src, f"Missing case for state {state!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Telegram bot version display — source inspection
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelegramVersionDisplay:
    def _bot_src(self):
        return _src("../telegram-bot/bot.py")

    def test_status_command_exists(self):
        src = self._bot_src()
        assert "cmd_status" in src or "status" in src

    def test_app_version_read_from_health(self):
        src = self._bot_src()
        assert "app_version" in src

    def test_version_line_in_status(self):
        src = self._bot_src()
        assert "Phiên bản" in src or "app_version" in src

    def test_health_endpoint_queried(self):
        src = self._bot_src()
        assert "/health" in src


# ═══════════════════════════════════════════════════════════════════════════════
# 8. APP_VERSION exposed on /health — source inspection
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppVersionHealth:
    def _main_src(self):
        return _src("app/main.py")

    def test_app_version_constant_defined(self):
        assert "APP_VERSION" in self._main_src()

    def test_app_version_env_sourced(self):
        assert 'os.getenv("APP_VERSION"' in self._main_src()

    def test_app_version_in_health_response(self):
        src = self._main_src()
        assert "app_version" in src


# ═══════════════════════════════════════════════════════════════════════════════
# 9. State model consistency: UI covers all valid states
# ═══════════════════════════════════════════════════════════════════════════════

class TestStateModelConsistency:
    def test_every_valid_state_in_state_ui(self):
        for s in VALID_JOB_STATES:
            assert s in STATE_UI, f"STATE_UI missing {s!r}"

    def test_every_ui_state_is_valid(self):
        for s in STATE_UI:
            assert s in VALID_JOB_STATES, f"STATE_UI has unknown state {s!r}"

    def test_transitions_are_symmetric_with_valid_states(self):
        assert set(STATE_TRANSITIONS.keys()) == VALID_JOB_STATES

    def test_terminal_states_have_empty_or_minimal_transitions(self):
        # Terminal states: cancelled, metadata_only are fully terminal (no outbound transitions)
        assert not STATE_TRANSITIONS["cancelled"]
        assert not STATE_TRANSITIONS["metadata_only"]

    def test_active_states_not_terminal(self):
        for s in ACTIVE_STATES:
            assert s not in TERMINAL_STATES

    def test_failed_not_in_terminal(self):
        # 'failed' is retryable and must NOT be terminal (auto-recovery still possible)
        # 'abandoned' is a special case: terminal (no auto-progression) but manually retryable
        assert "failed" not in TERMINAL_STATES
        assert "abandoned" in RETRYABLE_STATES  # manual retry still allowed
