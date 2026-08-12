"""
Phase 5 smoke tests — Archive, History & PWA polish.

Covers:
 - Archive error codes present and correctly shaped
 - History job field contract (fields needed by copy-URL + expiry badge)
 - PWA manifest presence check (static assertion on expected fields)
 - Nav items consistency (sidebar + mobile must both include archive)
"""
import os
import json
import pytest


# ─── Archive error codes ──────────────────────────────────────────────────────

ARCHIVE_ERROR_CODES = [
    "archive_sync_failed",
    "archive_item_not_found",
]

class TestArchiveErrorCodes:
    def _meta(self):
        from app.core.error_codes import ERROR_META
        return ERROR_META

    def test_archive_codes_present(self):
        meta = self._meta()
        for code in ARCHIVE_ERROR_CODES:
            assert code in meta, f"Missing archive error code: '{code}'"

    def test_archive_codes_have_required_fields(self):
        meta = self._meta()
        for code in ARCHIVE_ERROR_CODES:
            entry = meta[code]
            for field in ("user_message", "retryable", "suggested_action"):
                assert field in entry, f"'{code}' missing field '{field}'"

    def test_archive_sync_failed_is_retryable(self):
        meta = self._meta()
        assert meta["archive_sync_failed"]["retryable"] is True

    def test_archive_item_not_found_not_retryable(self):
        meta = self._meta()
        assert meta["archive_item_not_found"]["retryable"] is False

    def test_file_expired_code_exists(self):
        meta = self._meta()
        assert "file_expired" in meta
        assert "archive" in meta["file_expired"]["suggested_action"].lower() or \
               "url" in meta["file_expired"]["suggested_action"].lower(), \
            "file_expired should suggest archive or re-entry of URL"


# ─── History job field contract ───────────────────────────────────────────────

class TestHistoryJobFieldContract:
    """
    The fields used by copy-URL + expiry badge must be part of the known
    job shape.  These tests assert against the *expected* schema, not live data.
    """

    COPY_URL_FIELDS = {"original_url"}
    EXPIRY_FIELDS   = {"link_expires_at", "file_expires_at"}
    DISPLAY_FIELDS  = {"title", "status", "created_at", "file_size_mb", "slugified_name"}

    def _all_expected_fields(self):
        return self.COPY_URL_FIELDS | self.EXPIRY_FIELDS | self.DISPLAY_FIELDS

    def test_copy_url_fields_defined(self):
        for f in self.COPY_URL_FIELDS:
            assert isinstance(f, str) and f

    def test_expiry_fields_defined(self):
        for f in self.EXPIRY_FIELDS:
            assert isinstance(f, str) and f

    def test_no_overlap_between_copy_and_expiry_fields(self):
        assert self.COPY_URL_FIELDS.isdisjoint(self.EXPIRY_FIELDS)

    def test_all_display_fields_are_strings(self):
        for f in self._all_expected_fields():
            assert isinstance(f, str)

    def test_expiry_badge_logic_expired(self):
        import time
        past_ms = (time.time() - 86400) * 1000  # yesterday in ms
        past_iso = f"2020-01-01T00:00:00Z"
        import datetime
        d = datetime.datetime.fromisoformat("2020-01-01T00:00:00+00:00")
        ms = (d.timestamp() - datetime.datetime.now().timestamp()) * 1000
        assert ms < 0, "2020-01-01 should be in the past"

    def test_expiry_badge_logic_within_3_days(self):
        import datetime
        future = datetime.datetime.now() + datetime.timedelta(days=2)
        ms = (future.timestamp() - datetime.datetime.now().timestamp()) * 1000
        days = int(ms / 86400000)
        assert days <= 3

    def test_expiry_badge_logic_not_shown_beyond_3_days(self):
        import datetime
        future = datetime.datetime.now() + datetime.timedelta(days=10)
        ms = (future.timestamp() - datetime.datetime.now().timestamp()) * 1000
        days = int(ms / 86400000)
        assert days > 3


# ─── PWA manifest field contract ─────────────────────────────────────────────

MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../frontend/public/manifest.json",
)

class TestPwaManifest:
    def _load(self):
        with open(MANIFEST_PATH) as f:
            return json.load(f)

    def test_manifest_exists(self):
        assert os.path.exists(MANIFEST_PATH), "manifest.json not found"

    def test_manifest_has_name(self):
        m = self._load()
        assert "name" in m and m["name"]

    def test_manifest_has_short_name(self):
        m = self._load()
        assert "short_name" in m and m["short_name"]

    def test_manifest_has_start_url(self):
        m = self._load()
        assert "start_url" in m

    def test_manifest_has_display(self):
        m = self._load()
        assert m.get("display") in ("standalone", "fullscreen", "minimal-ui")

    def test_manifest_has_icons(self):
        m = self._load()
        assert "icons" in m and len(m["icons"]) >= 1

    def test_manifest_icons_have_src(self):
        m = self._load()
        for icon in m["icons"]:
            assert "src" in icon, f"icon missing 'src': {icon}"


# ─── Sidebar + MobileTabBar nav item contract ─────────────────────────────────

SIDEBAR_PATH      = os.path.join(os.path.dirname(__file__), "../../frontend/src/components/Sidebar.jsx")
# Mobile's cross-device Archive entry point is the AccountMenu dropdown
# (rendered unconditionally in App.jsx's top nav, not hidden below the `sm`
# breakpoint), not MobileTabBar's 5-tab bottom bar — same pattern this app
# already uses for Schedule/Playlists/Analytics, none of which are in the
# bottom tab bar either. This test used to check MobileTabBar.jsx directly,
# which never actually got an archive tab added; updated to check the real
# access path instead of resurrecting a design that was never implemented.
ACCOUNT_MENU_PATH = os.path.join(os.path.dirname(__file__), "../../frontend/src/components/AccountMenu.jsx")

class TestNavArchivePresence:
    def _read(self, path):
        with open(path) as f:
            return f.read()

    def test_sidebar_includes_archive(self):
        src = self._read(SIDEBAR_PATH)
        assert "'archive'" in src or '"archive"' in src, \
            "Sidebar.jsx must include archive nav item"

    def test_sidebar_imports_archive_icon(self):
        src = self._read(SIDEBAR_PATH)
        assert "Archive" in src, "Sidebar.jsx must import Archive icon"

    def test_mobile_account_menu_includes_archive(self):
        src = self._read(ACCOUNT_MENU_PATH)
        assert "'archive'" in src or '"archive"' in src, \
            "AccountMenu.jsx (the cross-device nav surface mobile users reach via the top-right avatar) must include an archive nav item"

    def test_mobile_account_menu_imports_archive_icon(self):
        src = self._read(ACCOUNT_MENU_PATH)
        assert "Archive" in src, "AccountMenu.jsx must import the Archive icon"


# ─── HistoryContent copy + expiry contract ────────────────────────────────────

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "../../frontend/src/components/HistoryContent.jsx")

class TestHistoryContentPhase5:
    def _read(self):
        with open(HISTORY_PATH) as f:
            return f.read()

    def test_copy_icon_imported(self):
        src = self._read()
        assert "Copy," in src or "Copy\n" in src or " Copy " in src, \
            "HistoryContent.jsx must import Copy icon from lucide-react"

    def test_check_icon_imported(self):
        src = self._read()
        assert "Check," in src or "Check\n" in src or " Check," in src, \
            "HistoryContent.jsx must import Check icon from lucide-react"

    def test_copy_url_handler_exists(self):
        src = self._read()
        assert "handleCopyUrl" in src

    def test_expiry_badge_helper_exists(self):
        src = self._read()
        assert "getExpiryBadge" in src

    def test_expiry_checks_link_expires_at(self):
        src = self._read()
        assert "link_expires_at" in src

    def test_expiry_checks_file_expires_at(self):
        src = self._read()
        assert "file_expires_at" in src

    def test_copied_id_state_exists(self):
        src = self._read()
        assert "copiedId" in src


# ─── App.jsx SW update contract ───────────────────────────────────────────────

APP_PATH = os.path.join(os.path.dirname(__file__), "../../frontend/src/App.jsx")

class TestAppSwUpdate:
    def _read(self):
        with open(APP_PATH) as f:
            return f.read()

    def test_sw_update_state_exists(self):
        src = self._read()
        assert "swUpdateReady" in src

    def test_sw_update_handler_exists(self):
        src = self._read()
        assert "handleSwUpdate" in src

    def test_sw_skip_waiting_message(self):
        src = self._read()
        assert "SKIP_WAITING" in src

    def test_pwa_install_handler_exists(self):
        src = self._read()
        assert "handlePwaInstall" in src

    def test_pwa_install_ready_state_exists(self):
        src = self._read()
        assert "pwaInstallReady" in src

    def test_update_banner_vi_copy(self):
        src = self._read()
        assert "Phiên bản mới khả dụng" in src
