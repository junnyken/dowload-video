"""
Pytest fixtures for VidGrab backend smoke tests.
Run with: cd backend && python -m pytest tests/ -v
"""

import os
import pytest

# Point tests at a throwaway test database / mocked Supabase
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-anon-key")
os.environ.setdefault("REDIS_URL",    "redis://localhost:6379/15")  # db 15 = isolated
os.environ.setdefault("ENV",          "test")

# ── Load the real app.main FIRST, for the whole test session ──────────────────
# conftest.py is guaranteed by pytest to be imported before any test module is
# collected, so this runs before anything else touches sys.modules.
#
# Several test files used to work around two problems by stubbing sys.modules
# with MagicMocks at their own module level (no teardown):
#   1. A pydantic-version conflict that supposedly crashed `supabase`/
#      `realtime` imports — verified NOT reproducible in this venv anymore
#      (`import supabase`, `from realtime.types import ...` both succeed
#      cleanly), so this workaround is no longer needed at all.
#   2. `app.api.routes` does `from app.main import limiter` at module level,
#      and `app.main` does `from app.api.routes import router`, i.e. a real
#      circular import if `app.api.routes` is imported directly before
#      `app.main` has fully finished loading. Verified: importing app.main
#      FIRST (fully) sidesteps this entirely — app.api.routes (or anything
#      else) can then be imported directly afterward with no error, because
#      by the time it does `from app.main import limiter`, app.main is
#      already fully initialized in sys.modules, not mid-import.
#
# Since every test file was independently stubbing to route around these,
# each with different guard logic (some unconditional) and none with
# teardown, whichever file's stub happened to be the last one touched during
# pytest's full collect-everything-before-running-anything phase silently
# became "the" app.main/app.core.database for the entire session — causing
# unrelated false failures/false passes depending on collection order. This
# single real import replaces the need for any of that: the real modules are
# what's in sys.modules from here on, consistently, for every test file.
import app.main  # noqa: E402,F401

# Same reasoning as app.main above — app.tasks.video_tasks and
# app.core.notifications were also independently stubbed as bare MagicMocks
# by individual test files (test_job_recovery.py, test_observability.py,
# test_admin_security.py), with no teardown, causing the same
# whichever-file-collects-last-wins false failures in OTHER files (e.g.
# test_phase11_resilience.py's real _register_pending_task/_compute_file_
# expires calls silently no-op'd against a MagicMock module instead of
# actually running). Both import cleanly for real in this venv.
import app.tasks.video_tasks  # noqa: E402,F401
import app.core.notifications  # noqa: E402,F401


@pytest.fixture(scope="session")
def app():
    """Create FastAPI test client (no real Supabase/Redis required for unit tests)."""
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture
def auth_headers():
    """Stub Bearer token for authenticated endpoints (unit tests)."""
    return {"Authorization": "Bearer test-token"}
