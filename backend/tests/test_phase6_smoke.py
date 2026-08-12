"""
Phase 6 smoke tests — Public API, Exports, Notifications, Observability, Error Standardization.

Covers:
 - Phase 6 error codes present and correctly shaped
 - APP_VERSION exposed in main module
 - /health endpoint shape (with app_version)
 - /api/v1/api-info endpoint shape
 - /api/v1/error-codes endpoint contract
 - History endpoint offset + platform params
 - Export handler presence in HistoryContent
 - Notification utility contract (JS source check)
 - ApiDocsPage presence
 - SettingsContent upgrade (retention + notification + API key sections)
"""
import os

# ─── Phase 6 error codes ─────────────────────────────────────────────────────

PHASE6_ERROR_CODES = [
    "provider_unavailable",
    "queue_busy",
    "validation_failed",
    "job_expired",
    "export_failed",
    "notification_permission_denied",
]

class TestPhase6ErrorCodes:
    def _meta(self):
        from app.core.error_codes import ERROR_META
        return ERROR_META

    def test_phase6_codes_present(self):
        meta = self._meta()
        for code in PHASE6_ERROR_CODES:
            assert code in meta, f"Missing Phase 6 error code: '{code}'"

    def test_phase6_codes_shape(self):
        meta = self._meta()
        for code in PHASE6_ERROR_CODES:
            entry = meta[code]
            for field in ("user_message", "retryable", "suggested_action"):
                assert field in entry, f"'{code}' missing '{field}'"
            assert isinstance(entry["retryable"], bool)

    def test_provider_unavailable_retryable(self):
        meta = self._meta()
        assert meta["provider_unavailable"]["retryable"] is True

    def test_queue_busy_retryable(self):
        meta = self._meta()
        assert meta["queue_busy"]["retryable"] is True

    def test_validation_failed_not_retryable(self):
        meta = self._meta()
        assert meta["validation_failed"]["retryable"] is False

    def test_job_expired_not_retryable(self):
        meta = self._meta()
        assert meta["job_expired"]["retryable"] is False

    def test_export_failed_retryable(self):
        meta = self._meta()
        assert meta["export_failed"]["retryable"] is True

    def test_all_error_codes_have_vi_message(self):
        """Every error message should be non-empty (Vietnamese copy preferred)."""
        from app.core.error_codes import ERROR_META
        for code, meta in ERROR_META.items():
            msg = meta.get("user_message", "")
            assert msg and len(msg.strip()) > 5, f"'{code}' has empty user_message"

    def test_make_error_for_phase6_codes(self):
        from app.core.error_codes import make_error
        for code in PHASE6_ERROR_CODES:
            err = make_error(code)
            assert err["error_code"] == code
            assert "retryable" in err
            assert "user_message" in err


MAIN_PY_PATH = os.path.join(os.path.dirname(__file__), "../app/main.py")

# ─── APP_VERSION in main module ──────────────────────────────────────────────

class TestAppVersion:
    def _src(self):
        with open(MAIN_PY_PATH) as f:
            return f.read()

    def test_app_version_constant_defined(self):
        assert "APP_VERSION" in self._src()

    def test_app_version_has_default_value(self):
        src = self._src()
        assert '"1.' in src or "'1." in src, "APP_VERSION should default to a 1.x.y string"

    def test_app_version_uses_env_var(self):
        assert 'os.getenv("APP_VERSION"' in self._src()


# ─── /health endpoint shape ──────────────────────────────────────────────────

class TestHealthEndpointShape:
    """Verify /health source includes app_version field (Phase 6 addition)."""

    def _src(self):
        with open(MAIN_PY_PATH) as f:
            return f.read()

    def test_health_handler_includes_app_version(self):
        assert '"app_version"' in self._src() or "'app_version'" in self._src()

    def test_health_handler_includes_ytdlp_version(self):
        assert "ytdlp_version" in self._src()

    def test_health_endpoint_defined(self):
        assert '@app.get("/health"' in self._src()


# ─── /api/v1/api-info endpoint contract ──────────────────────────────────────

class TestApiInfoEndpoint:
    """Check api_info handler source contains required fields (no import needed)."""

    def _src(self):
        with open(MAIN_PY_PATH) as f:
            return f.read()

    def test_api_info_endpoint_defined(self):
        assert '/api/v1/api-info' in self._src()

    def test_api_info_has_api_version_field(self):
        assert '"api_version"' in self._src()

    def test_api_info_has_app_version_field(self):
        assert '"app_version": APP_VERSION' in self._src()

    def test_api_info_has_auth_field(self):
        assert '"auth"' in self._src()

    def test_api_info_has_rate_limits_field(self):
        assert '"rate_limits"' in self._src()

    def test_api_info_has_public_endpoints_field(self):
        assert '"public_endpoints"' in self._src()

    def test_api_info_has_error_format_field(self):
        assert '"error_format"' in self._src()

    def test_api_info_no_secrets_leaked(self):
        src = self._src()
        # Find the api_info function body
        start = src.find("async def api_info")
        if start == -1:
            start = src.find("def api_info")
        # Find the next top-level def/class after api_info to bound the check
        end = src.find("\n@app.", start + 1)
        func_body = src[start:end] if end != -1 else src[start:]
        for secret in ("ADMIN_PASSWORD", "REDIS_URL", "SUPABASE", "cookie_pool"):
            assert secret not in func_body, f"api_info leaks: {secret}"


# ─── /api/v1/error-codes endpoint contract ───────────────────────────────────

class TestErrorCodesEndpoint:
    def _src(self):
        with open(MAIN_PY_PATH) as f:
            return f.read()

    def test_error_codes_endpoint_defined(self):
        assert '/api/v1/error-codes' in self._src()

    def test_error_codes_returns_from_error_meta(self):
        assert "ERROR_META" in self._src()

    def test_error_codes_returns_safe_fields_only(self):
        # Check source only returns 3 safe fields
        src = self._src()
        start = src.find("def list_error_codes")
        end = src.find("\n@app.", start + 1)
        body = src[start:end] if end != -1 else src[start:]
        assert "user_message" in body
        assert "retryable" in body
        assert "suggested_action" in body


# ─── History endpoint params ─────────────────────────────────────────────────

HISTORY_ROUTE_PATH = os.path.join(os.path.dirname(__file__), "../../backend/app/api/routes.py")

class TestHistoryEndpointParams:
    def _src(self):
        with open(HISTORY_ROUTE_PATH) as f:
            return f.read()

    def test_history_supports_offset(self):
        assert "offset" in self._src()

    def test_history_supports_platform_filter(self):
        assert "platform" in self._src()

    def test_history_supports_status_filter(self):
        src = self._src()
        assert "status" in src and 'eq("status"' in src or ".eq" in src


# ─── Frontend: HistoryContent export + platform filter ───────────────────────

HISTORY_CONTENT_PATH = os.path.join(
    os.path.dirname(__file__), "../../frontend/src/components/HistoryContent.jsx"
)

class TestHistoryContentPhase6:
    def _src(self):
        with open(HISTORY_CONTENT_PATH) as f:
            return f.read()

    def test_export_handler_exists(self):
        assert "handleExport" in self._src()

    def test_export_supports_csv(self):
        assert "csv" in self._src()

    def test_export_supports_json(self):
        assert "json" in self._src()

    def test_platform_filter_state_exists(self):
        assert "platformFilter" in self._src()

    def test_filedown_icon_imported(self):
        assert "FileDown" in self._src()

    def test_user_history_export_endpoint_called(self):
        assert "user/history/export" in self._src()


# ─── Frontend: notification utility ─────────────────────────────────────────

NOTIF_PATH = os.path.join(
    os.path.dirname(__file__), "../../frontend/src/utils/notifications.js"
)

class TestNotificationsUtil:
    def _src(self):
        with open(NOTIF_PATH) as f:
            return f.read()

    def test_file_exists(self):
        assert os.path.exists(NOTIF_PATH)

    def test_request_permission_exported(self):
        assert "requestPermission" in self._src()

    def test_notify_function_exported(self):
        assert "export function notify" in self._src()

    def test_notify_download_done_exported(self):
        assert "notifyDownloadDone" in self._src()

    def test_notify_download_failed_exported(self):
        assert "notifyDownloadFailed" in self._src()

    def test_is_supported_exported(self):
        assert "isSupported" in self._src()

    def test_get_permission_exported(self):
        assert "getPermission" in self._src()


# ─── Frontend: ApiDocsPage ───────────────────────────────────────────────────

API_DOCS_PATH = os.path.join(
    os.path.dirname(__file__), "../../frontend/src/pages/ApiDocsPage.jsx"
)

class TestApiDocsPage:
    def _src(self):
        with open(API_DOCS_PATH) as f:
            return f.read()

    def test_file_exists(self):
        assert os.path.exists(API_DOCS_PATH)

    def test_fetch_link_documented(self):
        assert "fetch-link" in self._src()

    def test_error_format_documented(self):
        assert "error_code" in self._src()

    def test_auth_documented(self):
        assert "Bearer" in self._src()

    def test_rate_limits_documented(self):
        assert "rate" in self._src().lower()

    def test_retry_guidance_documented(self):
        assert "retry" in self._src().lower()


# ─── Frontend: SettingsContent Phase 6 upgrade ───────────────────────────────

SETTINGS_PATH = os.path.join(
    os.path.dirname(__file__), "../../frontend/src/components/SettingsContent.jsx"
)

class TestSettingsContentPhase6:
    def _src(self):
        with open(SETTINGS_PATH) as f:
            return f.read()

    def test_notification_toggle_present(self):
        assert "notifPerm" in self._src() or "notification" in self._src().lower()

    def test_retention_rules_present(self):
        assert "RETENTION_RULES" in self._src()

    def test_app_version_displayed(self):
        assert "appVersion" in self._src() or "app_version" in self._src()

    def test_api_key_section_present(self):
        assert "api-key" in self._src() or "apiKey" in self._src()

    def test_health_endpoint_called(self):
        assert "/health" in self._src()

    def test_notification_utility_imported(self):
        assert "notifications" in self._src()

    def test_api_docs_link_present(self):
        assert "api-docs" in self._src()


# ─── App.jsx route for api-docs ───────────────────────────────────────────────

APP_PATH = os.path.join(os.path.dirname(__file__), "../../frontend/src/App.jsx")

class TestAppApiDocsRoute:
    def _src(self):
        with open(APP_PATH) as f:
            return f.read()

    def test_api_docs_in_path_map(self):
        assert "'/api-docs'" in self._src() or '"/api-docs"' in self._src()

    def test_api_docs_page_imported(self):
        assert "ApiDocsPage" in self._src()

    def test_api_docs_view_rendered(self):
        assert "view === 'api-docs'" in self._src()
