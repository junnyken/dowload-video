"""
Tests for VidGrab Telegram Bot — Phase 3

Run from telegram-bot/ directory:
    pytest tests/test_bot.py -v
"""
import os
import sys
from unittest.mock import MagicMock, patch

# Provide required env vars before importing bot
os.environ.setdefault("TELEGRAM_DIST_BOT_TOKEN", "test_token_123")
os.environ.setdefault("VIDGRAB_BACKEND_URL", "http://localhost:8000")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot  # noqa: E402


# ─── URL helpers ─────────────────────────────────────────────────────────────

class TestExtractUrl:
    def test_simple_https(self):
        assert bot.extract_url("check https://youtube.com/watch?v=abc") == "https://youtube.com/watch?v=abc"

    def test_strips_trailing_period(self):
        assert bot.extract_url("see https://tiktok.com/video/123.") == "https://tiktok.com/video/123"

    def test_strips_trailing_paren(self):
        assert bot.extract_url("(https://youtube.com/abc)") == "https://youtube.com/abc"

    def test_http_also_works(self):
        assert bot.extract_url("http://youtube.com/watch?v=x") is not None

    def test_none_on_plain_text(self):
        assert bot.extract_url("hello world") is None

    def test_none_on_empty(self):
        assert bot.extract_url("") is None

    def test_none_on_none(self):
        assert bot.extract_url(None) is None


class TestDetectPlatform:
    def test_youtube_long(self):
        name, supported, unsup = bot.detect_platform("https://youtube.com/watch?v=abc")
        assert name == "YouTube" and supported is True and unsup is False

    def test_youtu_be_short(self):
        name, supported, _ = bot.detect_platform("https://youtu.be/abc123")
        assert name == "YouTube" and supported is True

    def test_tiktok(self):
        name, supported, _ = bot.detect_platform("https://www.tiktok.com/@user/video/123")
        assert name == "TikTok" and supported is True

    def test_douyin(self):
        name, supported, _ = bot.detect_platform("https://douyin.com/video/abc")
        assert name == "Douyin" and supported is True

    def test_threads(self):
        name, supported, _ = bot.detect_platform("https://threads.net/@user/post/abc")
        assert name == "Threads" and supported is True

    def test_instagram_known_unsupported(self):
        _, supported, unsup = bot.detect_platform("https://instagram.com/reel/abc")
        assert supported is False and unsup is True

    def test_facebook_known_unsupported(self):
        _, supported, unsup = bot.detect_platform("https://facebook.com/video/123")
        assert supported is False and unsup is True

    def test_twitter_known_unsupported(self):
        _, supported, unsup = bot.detect_platform("https://twitter.com/status/123")
        assert supported is False and unsup is True

    def test_unknown_returns_empty(self):
        name, supported, unsup = bot.detect_platform("https://example.com/video")
        assert name == "" and supported is False and unsup is False


class TestIsSafeUrl:
    def test_valid_youtube(self):
        assert bot.is_safe_url("https://youtube.com/watch?v=abc") is True

    def test_valid_tiktok(self):
        assert bot.is_safe_url("https://tiktok.com/@u/video/1") is True

    def test_localhost_rejected(self):
        assert bot.is_safe_url("http://localhost:8000/api") is False

    def test_private_192_rejected(self):
        assert bot.is_safe_url("http://192.168.1.1/admin") is False

    def test_private_10_rejected(self):
        assert bot.is_safe_url("http://10.0.0.1/") is False

    def test_internal_backend_rejected(self):
        assert bot.is_safe_url("http://backend:8000/api") is False

    def test_redis_rejected(self):
        assert bot.is_safe_url("http://redis:6379") is False

    def test_non_http_scheme_rejected(self):
        assert bot.is_safe_url("ftp://example.com/file") is False

    def test_empty_rejected(self):
        assert bot.is_safe_url("") is False


class TestCacheKey:
    def test_deterministic(self):
        assert bot.cache_key("https://youtube.com/abc") == bot.cache_key("https://youtube.com/abc")

    def test_different_urls_differ(self):
        assert bot.cache_key("https://youtube.com/a") != bot.cache_key("https://youtube.com/b")

    def test_length_12(self):
        assert len(bot.cache_key("https://any.url")) == 12


# ─── Keyboard helpers ────────────────────────────────────────────────────────

class TestFormatLabel:
    def test_video_height(self):
        assert "720" in bot._format_label({"height": 720})

    def test_audio_type(self):
        label = bot._format_label({"type": "audio", "quality": "mp3_320"})
        assert "MP3" in label and "320" in label

    def test_audio_ext(self):
        assert "MP3" in bot._format_label({"ext": "mp3"})

    def test_label_fallback(self):
        assert "Original" in bot._format_label({"label": "Original"})


class TestBuildKeyboard:
    def test_format_buttons_plus_controls(self):
        kb = bot.build_format_keyboard("ck", [{"height": 720}, {"height": 480}])
        rows = kb["inline_keyboard"]
        # 2 format rows + refresh/cancel row + extension/help row = 4
        assert len(rows) == 4

    def test_dl_callback_prefix(self):
        kb = bot.build_format_keyboard("ck123", [{"height": 720}])
        btn = kb["inline_keyboard"][0][0]
        assert btn["callback_data"].startswith("dl:ck123:")

    def test_empty_formats_still_has_control_rows(self):
        kb = bot.build_format_keyboard("ck", [])
        assert len(kb["inline_keyboard"]) == 2

    def test_retry_keyboard_has_retry_button(self):
        kb = bot.build_retry_keyboard("ck99")
        btns = [b for row in kb["inline_keyboard"] for b in row]
        assert any("refresh:ck99" in b["callback_data"] for b in btns)


# ─── Rate limiting ────────────────────────────────────────────────────────────

class TestRateLimiting:
    def _mock_redis(self, incr_value: int) -> MagicMock:
        r = MagicMock()
        r.incr.return_value = incr_value
        return r

    def test_allows_first_request(self):
        with patch.object(bot, "_get_redis", return_value=self._mock_redis(1)):
            assert bot.check_rate_limit(user_id=1) is True

    def test_allows_at_exact_limit(self):
        with patch.object(bot, "_get_redis", return_value=self._mock_redis(bot.RATE_LIMIT)):
            assert bot.check_rate_limit(user_id=1) is True

    def test_blocks_over_limit(self):
        with patch.object(bot, "_get_redis", return_value=self._mock_redis(bot.RATE_LIMIT + 1)):
            assert bot.check_rate_limit(user_id=1) is False

    def test_fail_open_on_redis_down(self):
        with patch.object(bot, "_get_redis", return_value=None):
            assert bot.check_rate_limit(user_id=1) is True

    def test_chat_rate_limit_blocks_over(self):
        with patch.object(bot, "_get_redis", return_value=self._mock_redis(bot.RATE_LIMIT_CHAT + 1)):
            assert bot.check_chat_rate_limit(chat_id=100) is False


# ─── Job tracking ────────────────────────────────────────────────────────────

class TestJobTracking:
    def _make_redis(self):
        store = {}
        r = MagicMock()
        r.set.side_effect = lambda k, v, ex=None: store.update({k: v})
        r.get.side_effect = lambda k: store.get(k)
        r.delete.side_effect = lambda k: store.pop(k, None)
        return r, store

    def test_set_and_get(self):
        r, _ = self._make_redis()
        with patch.object(bot, "_get_redis", return_value=r):
            bot.job_set(user_id=42, ck="abc", status="processing")
            job = bot.job_get(user_id=42)
        assert job is not None
        assert job["ck"] == "abc" and job["status"] == "processing"

    def test_cancel_marks_status(self):
        r, _ = self._make_redis()
        with patch.object(bot, "_get_redis", return_value=r):
            bot.job_set(user_id=42, ck="abc", status="processing")
            bot.job_cancel(user_id=42)
            assert bot.job_is_cancelled(user_id=42, ck="abc") is True

    def test_cancel_wrong_ck_not_cancelled(self):
        r, _ = self._make_redis()
        with patch.object(bot, "_get_redis", return_value=r):
            bot.job_set(user_id=42, ck="abc", status="processing")
            bot.job_cancel(user_id=42)
            assert bot.job_is_cancelled(user_id=42, ck="DIFFERENT") is False

    def test_clear_removes_job(self):
        r, store = self._make_redis()
        with patch.object(bot, "_get_redis", return_value=r):
            bot.job_set(user_id=42, ck="abc")
            bot.job_clear(user_id=42)
            assert bot.job_get(user_id=42) is None

    def test_not_cancelled_when_no_job(self):
        with patch.object(bot, "_get_redis", return_value=None):
            assert bot.job_is_cancelled(user_id=99, ck="xyz") is False


# ─── Command dispatch ────────────────────────────────────────────────────────

class TestCommandDispatch:
    def test_start_dispatches(self):
        with patch.object(bot, "cmd_start") as m:
            bot.handle_command(chat_id=1, user_id=2, text="/start")
            m.assert_called_once_with(1)

    def test_help_dispatches(self):
        with patch.object(bot, "cmd_help") as m:
            bot.handle_command(chat_id=1, user_id=2, text="/help")
            m.assert_called_once_with(1)

    def test_extension_dispatches(self):
        with patch.object(bot, "cmd_extension") as m:
            bot.handle_command(chat_id=1, user_id=2, text="/extension")
            m.assert_called_once_with(1)

    def test_status_dispatches(self):
        with patch.object(bot, "cmd_status") as m:
            bot.handle_command(chat_id=1, user_id=2, text="/status")
            m.assert_called_once_with(1)

    def test_cancel_dispatches(self):
        with patch.object(bot, "cmd_cancel") as m:
            bot.handle_command(chat_id=1, user_id=2, text="/cancel")
            m.assert_called_once_with(1, 2)

    def test_download_passes_args(self):
        with patch.object(bot, "cmd_download") as m:
            bot.handle_command(chat_id=1, user_id=2,
                               text="/download https://youtube.com/watch?v=abc")
            m.assert_called_once_with(1, 2, "https://youtube.com/watch?v=abc")

    def test_botname_suffix_stripped(self):
        with patch.object(bot, "cmd_start") as m:
            bot.handle_command(chat_id=1, user_id=2, text="/start@vidgrab_bot")
            m.assert_called_once_with(1)

    def test_unknown_command_sends_help_hint(self):
        with patch.object(bot, "send_message") as m:
            bot.handle_command(chat_id=1, user_id=2, text="/unknown_xyz")
            text = m.call_args[0][1]
            assert "/help" in text


# ─── URL intake security ─────────────────────────────────────────────────────

class TestHandleUrlSecurity:
    def test_ssrf_url_rejected(self):
        with patch.object(bot, "send_message") as m:
            bot.handle_url(chat_id=1, user_id=2, url="http://localhost/api")
        assert "không hợp lệ" in m.call_args[0][1]

    def test_unsupported_platform_rejected(self):
        with patch.object(bot, "send_message") as m:
            bot.handle_url(chat_id=1, user_id=2, url="https://instagram.com/reel/abc")
        assert "chưa hỗ trợ" in m.call_args[0][1]

    def test_user_rate_limit_blocks(self):
        with patch.object(bot, "check_chat_rate_limit", return_value=True), \
             patch.object(bot, "check_rate_limit", return_value=False), \
             patch.object(bot, "send_message") as m:
            bot.handle_url(chat_id=1, user_id=2, url="https://youtube.com/watch?v=abc")
        assert "hết" in m.call_args[0][1]

    def test_chat_rate_limit_blocks(self):
        with patch.object(bot, "check_chat_rate_limit", return_value=False), \
             patch.object(bot, "send_message") as m:
            bot.handle_url(chat_id=1, user_id=2, url="https://youtube.com/watch?v=abc")
        assert "giới hạn" in m.call_args[0][1]

    def test_existing_active_job_blocks(self):
        with patch.object(bot, "check_chat_rate_limit", return_value=True), \
             patch.object(bot, "check_rate_limit", return_value=True), \
             patch.object(bot, "job_get", return_value={"ck": "x", "status": "processing"}), \
             patch.object(bot, "send_message") as m:
            bot.handle_url(chat_id=1, user_id=2, url="https://youtube.com/watch?v=abc")
        assert "/cancel" in m.call_args[0][1]


# ─── /status command ─────────────────────────────────────────────────────────

class TestStatusCommand:
    def _call(self, health: dict):
        with patch.object(bot, "call_health", return_value=health), \
             patch.object(bot, "send_message") as m, \
             patch.object(bot, "send_chat_action"):
            bot.cmd_status(chat_id=1)
        return m.call_args[0][1]

    def test_healthy_message(self):
        text = self._call({"ok": True, "status": "ok", "redis": "ok", "yt_dlp": "2024.1"})
        assert "Hoạt động" in text

    def test_offline_message(self):
        text = self._call({"ok": False, "status": "offline"})
        assert "không phản hồi" in text

    def test_degraded_message(self):
        text = self._call({"ok": False, "status": "degraded"})
        assert "không ổn" in text

    def test_maintenance_message(self):
        text = self._call({"ok": False, "status": "maintenance"})
        assert "bảo trì" in text


# ─── /extension command ──────────────────────────────────────────────────────

class TestExtensionCommand:
    def test_sends_file_when_zip_exists(self, tmp_path):
        zip_file = tmp_path / "VidGrab.zip"
        zip_file.write_bytes(b"PK fake")
        with patch.object(bot, "ZIP_PATH", str(zip_file)), \
             patch.object(bot, "send_message") as m_send, \
             patch.object(bot, "send_chat_action"), \
             patch("requests.post") as m_post:
            m_post.return_value.ok = True
            bot.cmd_extension(chat_id=1)
        # send_message called with EXTENSION_TEXT
        assert m_send.called
        assert "Extension" in m_send.call_args[0][1]

    def test_graceful_when_zip_missing(self):
        with patch.object(bot, "ZIP_PATH", "/nonexistent/path.zip"), \
             patch.object(bot, "send_message") as m, \
             patch.object(bot, "send_chat_action"):
            bot.cmd_extension(chat_id=1)
        # Should mention fallback URL, not crash
        assert m.called
        assert bot.WEB_URL in m.call_args[0][1]


# ─── /cancel command ─────────────────────────────────────────────────────────

class TestCancelCommand:
    def test_cancels_active_job(self):
        with patch.object(bot, "job_get", return_value={"ck": "abc", "status": "processing"}), \
             patch.object(bot, "job_cancel") as m_cancel, \
             patch.object(bot, "send_message") as m_send:
            bot.cmd_cancel(chat_id=1, user_id=2)
        m_cancel.assert_called_once_with(2)
        assert "Đã huỷ" in m_send.call_args[0][1]

    def test_no_op_when_no_active_job(self):
        with patch.object(bot, "job_get", return_value=None), \
             patch.object(bot, "send_message") as m:
            bot.cmd_cancel(chat_id=1, user_id=2)
        assert "Không có" in m.call_args[0][1]

    def test_no_op_when_job_not_processing(self):
        with patch.object(bot, "job_get", return_value={"ck": "abc", "status": "cancelled"}), \
             patch.object(bot, "send_message") as m:
            bot.cmd_cancel(chat_id=1, user_id=2)
        assert "Không có" in m.call_args[0][1]


# ─── /download shortcut ──────────────────────────────────────────────────────

class TestDownloadCommand:
    def test_valid_url_calls_handle_url(self):
        with patch.object(bot, "handle_url") as m:
            bot.cmd_download(chat_id=1, user_id=2, args="https://youtube.com/watch?v=abc")
        m.assert_called_once_with(1, 2, "https://youtube.com/watch?v=abc")

    def test_no_url_sends_usage_hint(self):
        with patch.object(bot, "send_message") as m:
            bot.cmd_download(chat_id=1, user_id=2, args="just text no url")
        assert "/download" in m.call_args[0][1]


# ─── Copy / secret sanity ────────────────────────────────────────────────────

class TestCopyConstants:
    def test_welcome_no_token(self):
        assert "test_token" not in bot.WELCOME_TEXT.lower()

    def test_help_no_internal_host_urls(self):
        # Check that internal host:port patterns don't appear, not just the word
        for pattern in ("redis://", "backend:8000", "celery:", "localhost:", ":6379"):
            assert pattern not in bot.HELP_TEXT

    def test_extension_text_has_version(self):
        assert bot.EXTENSION_VERSION in bot.EXTENSION_TEXT

    def test_help_mentions_all_commands(self):
        for cmd in ("/download", "/extension", "/status", "/cancel", "/help"):
            assert cmd in bot.HELP_TEXT

    def test_rate_limit_is_positive(self):
        assert bot.RATE_LIMIT > 0
        assert bot.RATE_LIMIT_CHAT >= bot.RATE_LIMIT
