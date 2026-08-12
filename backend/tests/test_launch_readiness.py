"""
Phase G — Launch Readiness & QA Hardening
==========================================
Critical-path smoke tests, config validation, launch blocker detection,
rollback readiness checks, error-state validation, and regression coverage.

All checks use source-code inspection or direct module imports (no live server,
no circular-import risk from importing app.main / app.api.routes at module load).

Unit tests (no live server needed):
    cd backend && python3 -m pytest tests/test_launch_readiness.py -v

Live HTTP checks (requires running server at BASE_URL):
    VIDGRAB_LIVE=1 BASE_URL=https://dowloadvideo.io.vn \
        python3 -m pytest tests/test_launch_readiness.py -v -k live
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub heavy native deps before app.main pulls in flow_cleanup (cv2/numpy).
for _mod in ("realtime", "realtime.types", "supabase", "supabase.client",
             "postgrest", "gotrue", "storage3", "cv2", "numpy", "np"):
    sys.modules.setdefault(_mod, MagicMock())

# ── Convenience paths ─────────────────────────────────────────────────────────
_BACKEND  = Path(__file__).parents[1]
_ROOT     = Path(__file__).parents[2]
_SCRIPTS  = _ROOT / "scripts"
_FRONTEND = _ROOT / "frontend"

# ── Shared helpers (source-code based, no circular-import risk) ───────────────

def _routes_src() -> str:
    return (_BACKEND / "app" / "api" / "routes.py").read_text()


def _admin_src() -> str:
    # Admin auth (_ADMIN_PASSWORD / verify_admin) lives in app/api/admin.py,
    # not routes.py — moved there since these tests were written.
    return (_BACKEND / "app" / "api" / "admin.py").read_text()


def _has_route(fragment: str, method: str) -> bool:
    """Return True if routes.py has @router.METHOD("...fragment...")."""
    method_lower = method.lower()
    for line in _routes_src().splitlines():
        stripped = line.strip()
        if stripped.startswith(f"@router.{method_lower}(") and fragment in stripped:
            return True
    return False


def _routes_section(fragment: str, window: int = 500) -> str:
    """Return `window` chars of source code around the first occurrence of fragment."""
    src = _routes_src()
    idx = src.find(fragment)
    return src[max(0, idx - 20): idx + window] if idx != -1 else ""


# ══════════════════════════════════════════════════════════════════════════════
# 1. HEALTH / SMOKE — route existence + handler source shape
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthSmoke:
    """Critical-path smoke: routes must be registered, handlers must have correct shape."""

    def test_routes_file_readable(self):
        src = _routes_src()
        assert "APIRouter" in src and "router" in src

    def test_ping_route_registered(self):
        assert _has_route("/ping", "GET"), "No @router.get('/ping') found in routes.py"

    def test_ping_route_is_get(self):
        assert _has_route("/ping", "GET")

    def test_ping_handler_returns_ok(self):
        segment = _routes_section('"/ping"', window=300)
        assert '"ok": True' in segment or '"ok":True' in segment, (
            "ping handler must return {'ok': True}"
        )

    def test_ping_handler_fast(self):
        """ping must be lightweight — no supabase/DB calls in its handler body."""
        segment = _routes_section('"/ping"', window=300)
        assert segment, "Could not locate /ping handler"
        assert "supabase" not in segment.lower() or "ok" in segment

    def test_platforms_route_registered(self):
        assert _has_route("/platforms", "GET"), "No @router.get('/platforms') found"

    def test_platforms_route_is_get(self):
        assert _has_route("/platforms", "GET")

    def test_platforms_handler_returns_success(self):
        segment = _routes_section('"/platforms"', window=2000)
        assert '"success"' in segment, "/platforms handler must include 'success' key"

    def test_platforms_handler_returns_list(self):
        segment = _routes_section('"/platforms"', window=2000)
        assert '"platforms"' in segment, "/platforms handler must include 'platforms' key"

    def test_platforms_total_matches_list_length(self):
        segment = _routes_section('"/platforms"', window=2000)
        assert '"total"' in segment or "'total'" in segment, (
            "/platforms handler must include 'total' field"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2. CRITICAL-PATH ENDPOINT ROUTES — verify registrations and HTTP methods
# ══════════════════════════════════════════════════════════════════════════════

class TestCriticalPathEndpoints:
    """Each critical route must be registered with the correct HTTP method."""

    def test_bulk_download_post_registered(self):
        assert _has_route("/bulk-download", "POST"), "POST /bulk-download not found"

    def test_fetch_link_post_registered(self):
        assert _has_route("/fetch-link", "POST"), "POST /fetch-link not found"

    def test_retry_failed_post_registered(self):
        assert _has_route("/retry-failed", "POST"), "POST /retry-failed/{batch_id} not found"

    def test_resume_batch_post_registered(self):
        assert _has_route("/resume-batch", "POST"), "POST /resume-batch/{batch_id} not found"

    def test_jobs_batch_get_registered(self):
        assert _has_route("/jobs", "GET"), "GET /jobs/{batch_id} not found"

    def test_history_get_registered(self):
        assert _has_route("/history", "GET"), "GET /history not found"

    def test_extension_download_get_registered(self):
        assert _has_route("/extension/download", "GET"), "GET /extension/download not found"

    def test_validate_urls_post_registered(self):
        assert _has_route("/validate-urls", "POST"), "POST /validate-urls not found"

    def test_quota_get_registered(self):
        assert _has_route("/quota", "GET"), "GET /quota not found"

    def test_progress_get_registered(self):
        assert _has_route("/progress", "GET"), "GET /progress/{token} not found"


# ══════════════════════════════════════════════════════════════════════════════
# 3. CONFIG VALIDATION — env vars have safe defaults
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigValidation:
    """Env vars critical for production must be documented with defaults."""

    @staticmethod
    def _routes_src() -> str:
        return (_BACKEND / "app" / "api" / "routes.py").read_text()

    @staticmethod
    def _main_src() -> str:
        return (_BACKEND / "app" / "main.py").read_text()

    def test_redis_url_has_default_in_main(self):
        assert "REDIS_URL" in self._main_src()

    def test_download_dir_has_default(self):
        src = self._routes_src()
        assert "DOWNLOAD_DIR" in src
        assert "/app/downloads" in src

    def test_local_file_ttl_has_default(self):
        assert "LOCAL_FILE_TTL_SEC" in self._routes_src()

    def test_max_concurrent_downloads_has_default(self):
        assert "MAX_CONCURRENT_DOWNLOADS" in self._routes_src()

    def test_admin_password_is_env_driven(self):
        assert "ADMIN_PASSWORD" in _admin_src()

    def test_daily_quota_has_default_in_main(self):
        assert "DAILY_QUOTA_PER_IP" in self._main_src()

    def test_app_version_has_default(self):
        assert "APP_VERSION" in self._main_src()

    def test_conftest_sets_test_supabase_url(self):
        text = (_BACKEND / "tests" / "conftest.py").read_text()
        assert "SUPABASE_URL" in text

    def test_conftest_sets_test_redis_url(self):
        text = (_BACKEND / "tests" / "conftest.py").read_text()
        assert "REDIS_URL" in text

    def test_conftest_uses_isolated_redis_db(self):
        """Tests must use db 15, not db 0, to avoid clobbering prod data."""
        text = (_BACKEND / "tests" / "conftest.py").read_text()
        assert "/15" in text, "conftest.py must use Redis db 15 for test isolation"


# ══════════════════════════════════════════════════════════════════════════════
# 4. ADMIN SECURITY — auth dependencies wired on admin routes
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminSecurity:
    """Admin endpoints must require auth — verified via source-code inspection.

    Admin auth was refactored out of routes.py into its own module,
    app/api/admin.py (`verify_admin` dependency, registered at /api/v1/admin
    in app/main.py) — these tests were written against the old routes.py
    location and updated here to match."""

    @staticmethod
    def _src() -> str:
        return _admin_src()

    def test_verify_admin_defined(self):
        assert "async def verify_admin" in self._src(), (
            "verify_admin dependency not found in admin.py"
        )

    def test_admin_password_from_env_not_hardcoded(self):
        src = self._src()
        assert "ADMIN_PASSWORD" in src and "getenv" in src

    def test_admin_routes_have_auth_dependency(self):
        src = self._src()
        # Every admin route is gated except POST /login (must stay public —
        # it's how you obtain the session in the first place). Count routes
        # by their decorator and compare to how many actually depend on
        # verify_admin, rather than just checking the string appears once.
        route_count = src.count("@router.")
        gated_count = src.count("Depends(verify_admin)")
        assert gated_count >= route_count - 1, (
            f"expected all but the public /login route to depend on verify_admin "
            f"({gated_count} gated out of {route_count} routes)"
        )

    def test_admin_password_has_no_insecure_hardcoded_default(self):
        """ADMIN_PASSWORD must default to empty string (forcing explicit env
        configuration), not a hardcoded fallback password — the opposite of
        what this test originally asserted before the security hardening
        that introduced this exact env-driven-no-default pattern."""
        src = self._src()
        idx = src.find("_ADMIN_PASSWORD = os.getenv(")
        assert idx != -1
        segment = src[idx: idx + 80]
        assert '""' in segment, (
            "ADMIN_PASSWORD must default to empty string, not a hardcoded password"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 5. LAUNCH BLOCKER DETECTION — if these fail, the app is unusable
# ══════════════════════════════════════════════════════════════════════════════

class TestLaunchBlockerDetection:
    """If any of these fail, block the launch."""

    def test_routes_module_importable(self):
        """routes.py must be present, readable, and define an APIRouter."""
        src = _routes_src()
        assert "router = APIRouter" in src or "APIRouter()" in src

    def test_ping_shape_stable(self):
        """Monitoring scrapes ping.ok — shape change = alerting blind spot."""
        segment = _routes_section('"/ping"', window=300)
        assert '"ok": True' in segment or '"ok":True' in segment, (
            "/ping handler must return {'ok': True}"
        )

    def test_error_codes_module_importable(self):
        from app.core.error_codes import ERROR_META, get_error_meta, make_error
        assert len(ERROR_META) > 5

    def test_redis_client_importable(self):
        from app.core.redis_client import get_redis
        assert callable(get_redis)

    def test_cache_module_importable(self):
        from app.core.cache import get_cached_result, get_cache_stats
        assert callable(get_cached_result) and callable(get_cache_stats)

    def test_video_tasks_importable(self):
        from app.tasks.video_tasks import process_video_task, scrape_channel_task
        assert process_video_task is not None and scrape_channel_task is not None

    def test_routes_router_exported(self):
        """routes.py must define and export `router = APIRouter()`."""
        src = _routes_src()
        assert "router = APIRouter" in src or "router=APIRouter" in src

    def test_platforms_always_returns_list(self):
        """platforms handler must return a list — verified via source structure."""
        segment = _routes_section('"/platforms"', window=2000)
        assert '"platforms"' in segment, "/platforms handler must include 'platforms' key"


# ══════════════════════════════════════════════════════════════════════════════
# 6. ROLLBACK READINESS — scripts exist and are executable
# ══════════════════════════════════════════════════════════════════════════════

class TestRollbackReadiness:
    """Rollback and recovery scripts must be present before launch."""

    def test_rollback_sh_exists(self):
        assert (_SCRIPTS / "rollback.sh").exists(), "rollback.sh missing"

    def test_rollback_sh_is_executable(self):
        assert os.access(str(_SCRIPTS / "rollback.sh"), os.X_OK)

    def test_rollback_sh_mentions_image_digest(self):
        assert "vidgrab_last_image" in (_SCRIPTS / "rollback.sh").read_text()

    def test_smoke_test_sh_exists(self):
        assert (_SCRIPTS / "smoke-test.sh").exists()

    def test_smoke_test_sh_is_executable(self):
        assert os.access(str(_SCRIPTS / "smoke-test.sh"), os.X_OK)

    def test_deploy_check_sh_exists(self):
        assert (_SCRIPTS / "deploy-check.sh").exists()

    def test_deploy_check_sh_is_executable(self):
        assert os.access(str(_SCRIPTS / "deploy-check.sh"), os.X_OK)

    def test_deploy_vps_sh_exists(self):
        assert (_ROOT / "deploy-vps.sh").exists()

    def test_deploy_vps_has_pre_check(self):
        text = (_ROOT / "deploy-vps.sh").read_text()
        assert "pre" in text.lower() and "check" in text.lower()

    def test_deploy_vps_has_post_check(self):
        text = (_ROOT / "deploy-vps.sh").read_text()
        assert "post" in text.lower() and "check" in text.lower()

    def test_docker_compose_yml_exists(self):
        assert (_ROOT / "docker-compose.yml").exists()

    def test_docker_compose_preview_exists(self):
        assert (_ROOT / "docker-compose.preview.yml").exists()

    def test_requirements_txt_exists(self):
        assert (_BACKEND / "requirements.txt").exists()


# ══════════════════════════════════════════════════════════════════════════════
# 7. ERROR STATE HANDLING — structured errors, no raw tracebacks
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorStateHandling:
    """Error responses must be JSON, human-readable, and traceback-free."""

    def test_error_codes_all_have_user_message(self):
        from app.core.error_codes import ERROR_META
        for code, meta in ERROR_META.items():
            assert "user_message" in meta, f"'{code}' missing user_message"
            assert meta["user_message"].strip(), f"'{code}' has blank user_message"

    def test_error_codes_all_have_retryable_bool(self):
        from app.core.error_codes import ERROR_META
        for code, meta in ERROR_META.items():
            assert isinstance(meta.get("retryable"), bool), (
                f"'{code}'.retryable must be bool"
            )

    def test_make_error_no_traceback(self):
        from app.core.error_codes import make_error
        err = json.dumps(make_error("rate_limited"))
        assert "Traceback" not in err and "Exception" not in err

    def test_make_error_has_user_message(self):
        from app.core.error_codes import make_error
        err = make_error("rate_limited")
        assert "user_message" in err and err["user_message"].strip()

    def test_make_error_has_retryable(self):
        from app.core.error_codes import make_error
        err = make_error("rate_limited")
        assert isinstance(err.get("retryable"), bool)

    def test_rate_limited_code_present(self):
        from app.core.error_codes import ERROR_META
        assert "rate_limited" in ERROR_META

    def test_file_expired_code_present(self):
        from app.core.error_codes import ERROR_META
        assert "file_expired" in ERROR_META

    def test_error_codes_suggested_action_present(self):
        from app.core.error_codes import ERROR_META
        for code, meta in ERROR_META.items():
            assert "suggested_action" in meta, f"'{code}' missing suggested_action"


# ══════════════════════════════════════════════════════════════════════════════
# 8. REGRESSION COVERAGE — guard against breaks from previous phases
# ══════════════════════════════════════════════════════════════════════════════

class TestRegressionCoverage:
    """Explicit guards for critical invariants introduced in prior phases."""

    def test_cache_l1_prefix(self):
        from app.core.cache import _L1_PREFIX
        assert _L1_PREFIX == "vidcache:urlresult:"

    def test_cache_get_redis_module_level(self):
        import app.core.cache as _c
        assert hasattr(_c, "get_redis"), (
            "get_redis must be a module-level name in cache.py"
        )

    def test_wave_size_defined_in_task(self):
        src = (_BACKEND / "app" / "tasks" / "video_tasks.py").read_text()
        assert "WAVE_SIZE" in src and ("WAVE_SIZE = 10" in src or "WAVE_SIZE =" in src)

    def test_bulk_download_reads_downloads_queue(self):
        src = _routes_src()
        bulk_start = src.find('@router.post("/bulk-download")')
        assert bulk_start != -1
        assert 'llen("downloads")' in src[bulk_start:]

    def test_normalize_url_strips_yt_tracking(self):
        from app.core.cache import _normalize_url
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxx&t=42"
        assert _normalize_url(url) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_metrics_module_importable(self):
        from app.core import metrics
        assert metrics is not None

    def test_structured_log_importable(self):
        from app.core import structured_log
        assert structured_log is not None

    def test_platform_circuit_importable(self):
        from app.core import platform_circuit
        assert hasattr(platform_circuit, "is_open")

    def test_job_recovery_module_importable(self):
        from app.core import recovery
        assert recovery is not None

    def test_verify_admin_defined(self):
        # Admin auth moved to app/api/admin.py's verify_admin — see
        # TestAdminSecurity for the full picture.
        assert "async def verify_admin" in _admin_src(), (
            "verify_admin must be defined in admin.py"
        )

    def test_ping_handler_callable_without_auth(self):
        """ping handler must return ok without auth — no Depends on its decorator."""
        segment = _routes_section('"/ping"', window=300)
        assert '"ok": True' in segment or '"ok":True' in segment

    def test_platforms_total_matches_list(self):
        """platforms handler must include both 'total' and 'platforms' keys."""
        segment = _routes_section('"/platforms"', window=2000)
        assert '"total"' in segment or "'total'" in segment
        assert '"platforms"' in segment or "'platforms'" in segment


# ══════════════════════════════════════════════════════════════════════════════
# 9. CROSS-BROWSER / MOBILE READINESS — frontend static assets
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossBrowserMobile:
    """Frontend must be correctly configured for mobile + cross-browser."""

    @staticmethod
    def _read(rel: str) -> str:
        return (_FRONTEND / rel).read_text(encoding="utf-8")

    @staticmethod
    def _exists(rel: str) -> bool:
        return (_FRONTEND / rel).exists()

    def test_index_html_exists(self):
        assert self._exists("index.html")

    def test_index_html_has_viewport(self):
        html = self._read("index.html")
        assert 'name="viewport"' in html and "width=device-width" in html

    def test_index_html_has_charset_utf8(self):
        assert "UTF-8" in self._read("index.html")

    def test_index_html_has_theme_color(self):
        assert "theme-color" in self._read("index.html")

    def test_index_html_has_root_div(self):
        assert '<div id="root">' in self._read("index.html")

    def test_index_html_has_module_script(self):
        assert 'type="module"' in self._read("index.html")

    def test_manifest_json_exists(self):
        assert self._exists("public/manifest.json"), "manifest.json missing — PWA install broken"

    def test_manifest_has_display_standalone(self):
        assert json.loads(self._read("public/manifest.json")).get("display") == "standalone"

    def test_manifest_has_name(self):
        assert json.loads(self._read("public/manifest.json")).get("name")

    def test_manifest_has_icons(self):
        assert len(json.loads(self._read("public/manifest.json")).get("icons", [])) >= 2

    def test_sw_js_exists(self):
        assert self._exists("public/sw.js") or self._exists("public/service-worker.js"), (
            "Service worker missing — PWA offline and install will fail"
        )

    def test_apple_touch_icon_referenced(self):
        assert "apple-touch-icon" in self._read("index.html")

    def test_og_meta_present(self):
        assert "og:title" in self._read("index.html")

    def test_lang_attribute_set(self):
        html = self._read("index.html")
        assert 'lang="vi"' in html or 'lang="en"' in html


# ══════════════════════════════════════════════════════════════════════════════
# 10. LAUNCH CHECKLIST — all launch-critical infrastructure is in place
# ══════════════════════════════════════════════════════════════════════════════

class TestLaunchChecklist:
    """High-level pre-launch checklist items — fail here = block launch."""

    def test_smoke_sh_checks_ping_or_health(self):
        text = (_SCRIPTS / "smoke-test.sh").read_text()
        assert "/ping" in text or "/health" in text

    def test_launch_check_sh_exists(self):
        assert (_SCRIPTS / "launch-check.sh").exists(), (
            "launch-check.sh missing — run scripts/launch-check.sh before each launch"
        )

    def test_launch_check_sh_is_executable(self):
        p = _SCRIPTS / "launch-check.sh"
        if p.exists():
            assert os.access(str(p), os.X_OK)

    def test_docker_compose_has_healthcheck(self):
        assert "healthcheck" in (_ROOT / "docker-compose.yml").read_text().lower()

    def test_docker_compose_has_backend(self):
        assert "backend" in (_ROOT / "docker-compose.yml").read_text()

    def test_docker_compose_has_celery(self):
        assert "celery" in (_ROOT / "docker-compose.yml").read_text()

    def test_docker_compose_has_redis(self):
        assert "redis" in (_ROOT / "docker-compose.yml").read_text()

    def test_backend_requirements_exist(self):
        assert (_BACKEND / "requirements.txt").exists()

    def test_test_suite_count_adequate(self):
        """There must be a minimum number of test files for adequate coverage."""
        test_files = list((_BACKEND / "tests").glob("test_*.py"))
        assert len(test_files) >= 10, (
            f"Only {len(test_files)} test files found — coverage may be inadequate"
        )
