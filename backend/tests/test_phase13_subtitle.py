"""
Phase 13 — Subtitle end-to-end tests
Run: pytest backend/tests/test_phase13_subtitle.py -v
"""
import os
import sys

# ── Path setup ─────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# ── No stubbing needed ──────────────────────────────────────────────────────
# This file used to stub supabase/realtime/postgrest/gotrue/storage3 (working
# around a "pydantic version conflict" — verified NOT reproducible in this
# venv: `import supabase` and `from realtime.types import ...` both succeed
# cleanly) and unconditionally overwrite app.main/app.core.database (to dodge
# the real routes.py<->main.py circular import) with bare MagicMock modules,
# with no restoration afterward. That silently broke any OTHER test file
# collected later in the same pytest session that needed the real app.main
# (confirmed: direct cause of test_phase11_resilience.py's
# TestHealthEndpointShape failure — `from app.main import health_check`
# raised ImportError against this stub).
#
# tests/conftest.py now imports the real app.main (and, transitively,
# app.core.database/supabase/realtime) before any test file is collected —
# which also sidesteps the circular import this used to work around, since
# app.main is fully initialized before app.api.routes is ever imported
# directly. Nothing here needs stubbing anymore.

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-anon-key")


# ── Unit: subtitle helpers ─────────────────────────────────────────────────
class TestSubtitleLangMap:
    """_SUBTITLE_LANG_MAP covers all expected modes."""

    def _import_helpers(self):
        from app.api.routes import _SUBTITLE_LANG_MAP, _subtitle_langs_for, _resolve_download_subs
        return _SUBTITLE_LANG_MAP, _subtitle_langs_for, _resolve_download_subs

    def test_map_vi_codes(self):
        m, *_ = self._import_helpers()
        assert 'vi' in m['vi']
        assert 'vi-VN' in m['vi']

    def test_map_en_codes(self):
        m, *_ = self._import_helpers()
        assert 'en' in m['en']
        assert 'en-US' in m['en']

    def test_map_auto_has_vi_and_en(self):
        m, *_ = self._import_helpers()
        auto = m['auto']
        assert any('vi' in c for c in auto)
        assert any('en' in c for c in auto)

    def test_map_all_is_none(self):
        m, *_ = self._import_helpers()
        assert m['all'] is None

    def test_subtitle_langs_for_vi(self):
        _, fn, _ = self._import_helpers()
        result = fn('vi')
        assert isinstance(result, list)
        assert 'vi' in result

    def test_subtitle_langs_for_all_falls_back_to_default(self):
        # _SUBTITLE_LANG_MAP['all'] is None → function returns the auto default list
        _, fn, _ = self._import_helpers()
        result = fn('all')
        assert isinstance(result, list)
        assert len(result) > 0

    def test_subtitle_langs_for_unknown_defaults_to_auto(self):
        _, fn, _ = self._import_helpers()
        result = fn('xx-unknown')
        auto_langs = ['vi', 'vi-VN', 'vi-VIE', 'en', 'en-US']
        assert result == auto_langs


class TestResolveDownloadSubs:
    """_resolve_download_subs maintains backward compatibility."""

    def _fn(self):
        from app.api.routes import _resolve_download_subs
        return _resolve_download_subs

    def test_mode_off_no_legacy_returns_false(self):
        # mode='off', no legacy flag → don't download subs
        fn = self._fn()
        assert fn('off', False) is False

    def test_mode_off_with_legacy_true_returns_true(self):
        # Legacy clients: download_subs=True, no explicit mode → download subs
        fn = self._fn()
        assert fn('off', True) is True

    def test_mode_file_explicit_returns_true(self):
        fn = self._fn()
        assert fn('file', False) is True

    def test_mode_burned_explicit_returns_true(self):
        fn = self._fn()
        assert fn('burned', False) is True

    def test_mode_soft_explicit_returns_true(self):
        fn = self._fn()
        assert fn('soft', False) is True

    def test_subtitle_only_mode_returns_false(self):
        # subtitle_only = download subs only, no video — so download_subs flag is False
        fn = self._fn()
        assert fn('subtitle_only', False) is False

    def test_explicit_mode_wins_over_legacy(self):
        # When both are set, mode takes effect (burned → True)
        fn = self._fn()
        assert fn('burned', True) is True


# ── Unit: FFmpeg command builders ──────────────────────────────────────────
class TestBurnSubtitleCommand:
    """_burn_subtitle builds a correct FFmpeg command string."""

    def _fn(self):
        from app.api.routes import _burn_subtitle
        return _burn_subtitle

    def test_returns_bool(self, tmp_path):
        fn = self._fn()
        out = str(tmp_path / 'out.mp4')
        import unittest.mock as mock
        with mock.patch('app.api.routes._subprocess') as mock_sub, \
             mock.patch('os.path.exists', return_value=True):
            mock_sub.run.return_value = mock.MagicMock(returncode=0)
            result = fn('/v.mp4', '/s.srt', out)
            assert mock_sub.run.called
            assert result is True

    def test_uses_subtitles_filter(self, tmp_path):
        fn = self._fn()
        import unittest.mock as mock
        with mock.patch('app.api.routes._subprocess') as mock_sub, \
             mock.patch('os.path.exists', return_value=True):
            mock_sub.run.return_value = mock.MagicMock(returncode=0)
            fn('/v.mp4', '/s.srt', '/o.mp4')
            cmd = mock_sub.run.call_args[0][0]
            full_cmd = ' '.join(cmd)
            assert 'subtitles' in full_cmd and '-vf' in full_cmd


class TestMuxSubtitleCommand:
    """_mux_subtitle builds a correct FFmpeg MKV mux command."""

    def _fn(self):
        from app.api.routes import _mux_subtitle
        return _mux_subtitle

    def test_returns_bool(self, tmp_path):
        fn = self._fn()
        import unittest.mock as mock
        with mock.patch('app.api.routes._subprocess') as mock_sub, \
             mock.patch('os.path.exists', return_value=True):
            mock_sub.run.return_value = mock.MagicMock(returncode=0)
            result = fn('/v.mp4', '/s.srt', '/o.mkv')
            assert result is True
            cmd = mock_sub.run.call_args[0][0]
            assert '/o.mkv' in cmd

    def test_copies_video_audio_streams(self, tmp_path):
        fn = self._fn()
        import unittest.mock as mock
        with mock.patch('app.api.routes._subprocess') as mock_sub, \
             mock.patch('os.path.exists', return_value=True):
            mock_sub.run.return_value = mock.MagicMock(returncode=0)
            fn('/v.mp4', '/s.srt', '/o.mkv')
            cmd = ' '.join(mock_sub.run.call_args[0][0])
            assert '-c:v copy' in cmd and '-c:a copy' in cmd


# ── Integration: request models ────────────────────────────────────────────
class TestFetchLinkRequestModel:
    """FetchLinkRequest accepts new subtitle fields."""

    def _model(self):
        from app.api.routes import FetchLinkRequest
        return FetchLinkRequest

    def test_default_subtitle_mode_is_off(self):
        m = self._model()
        req = m(url='https://youtube.com/watch?v=test')
        assert req.subtitle_mode == 'off'

    def test_default_subtitle_language_is_auto(self):
        m = self._model()
        req = m(url='https://youtube.com/watch?v=test')
        assert req.subtitle_language == 'auto'

    def test_accepts_burned_mode(self):
        m = self._model()
        req = m(url='https://youtube.com/watch?v=test', subtitle_mode='burned')
        assert req.subtitle_mode == 'burned'

    def test_accepts_soft_mode(self):
        m = self._model()
        req = m(url='https://youtube.com/watch?v=test', subtitle_mode='soft')
        assert req.subtitle_mode == 'soft'

    def test_accepts_language_vi(self):
        m = self._model()
        req = m(url='https://youtube.com/watch?v=test', subtitle_mode='file', subtitle_language='vi')
        assert req.subtitle_language == 'vi'


class TestBulkDownloadRequestModel:
    """BulkDownloadRequest also carries subtitle fields."""

    def _model(self):
        from app.api.routes import BulkDownloadRequest
        return BulkDownloadRequest

    def test_default_subtitle_mode_is_off(self):
        m = self._model()
        req = m(urls=['https://youtube.com/watch?v=test'])
        assert req.subtitle_mode == 'off'

    def test_subtitle_mode_propagates(self):
        m = self._model()
        req = m(urls=['https://youtube.com/watch?v=test'], subtitle_mode='file', subtitle_language='en')
        assert req.subtitle_mode == 'file'
        assert req.subtitle_language == 'en'


# ── Smoke: admin analytics endpoints exist ─────────────────────────────────
class TestAdminAnalyticsEndpointsExist:
    """Verify the 3 Phase-13 admin endpoints are registered."""

    def test_platform_analytics_endpoint(self):
        from app.api.admin import router
        routes = [r.path for r in router.routes]
        assert '/platform-analytics' in routes

    def test_user_analytics_deep_endpoint(self):
        from app.api.admin import router
        routes = [r.path for r in router.routes]
        assert '/user-analytics-deep' in routes

    def test_revenue_signals_endpoint(self):
        from app.api.admin import router
        routes = [r.path for r in router.routes]
        assert '/revenue-signals' in routes
