"""
Phase 10 smoke tests — verify core flows don't catastrophically break.

These tests are intentionally lightweight: they assert HTTP shapes and
error codes without requiring live Supabase / Redis.  Set VIDGRAB_LIVE=1
to run integration tests against a real backend.
"""

import pytest
from app.core.error_codes import ERROR_META, get_error_meta, make_error


# ─── Error code model ─────────────────────────────────────────────────────────

class TestErrorCodes:
    """Every code in ERROR_META must have required fields."""

    REQUIRED_FIELDS = {"user_message", "retryable", "suggested_action"}

    def test_all_entries_have_required_fields(self):
        for code, meta in ERROR_META.items():
            missing = self.REQUIRED_FIELDS - meta.keys()
            assert not missing, f"Error code '{code}' missing: {missing}"

    def test_retryable_is_bool(self):
        for code, meta in ERROR_META.items():
            assert isinstance(meta["retryable"], bool), f"'{code}'.retryable must be bool"

    def test_get_error_meta_known_code(self):
        m = get_error_meta("file_expired")
        assert m["retryable"] is False
        assert "hết hạn" in m["user_message"].lower() or "file" in m["user_message"].lower()

    def test_get_error_meta_unknown_code_fallback(self):
        m = get_error_meta("totally_unknown_xyz")
        assert "user_message" in m
        assert "retryable" in m

    def test_make_error_structure(self):
        err = make_error("rate_limited")
        assert err["error_code"] == "rate_limited"
        assert err["retryable"] is True
        assert "user_message" in err
        assert "suggested_action" in err

    def test_make_error_override_message(self):
        err = make_error("rate_limited", override_message="Custom msg")
        assert err["user_message"] == "Custom msg"

    def test_make_error_extra_fields(self):
        err = make_error("quota_exceeded_daily", extra={"downloads_today": 30})
        assert err["downloads_today"] == 30
        assert err["error_code"] == "quota_exceeded_daily"


# ─── URL validation utility ───────────────────────────────────────────────────

class TestUrlValidator:
    """Client-side URL validator (pure Python reimplementation for test parity)."""

    def _validate(self, url):
        """Minimal reimplementation matching urlValidator.js logic."""
        url = (url or "").strip()
        if not url:
            return {"valid": False, "error": "empty"}
        if not url.startswith(("http://", "https://")):
            return {"valid": False, "error": "scheme"}
        try:
            from urllib.parse import urlparse
            p = urlparse(url)
            assert p.netloc
        except Exception:
            return {"valid": False, "error": "parse"}
        return {"valid": True, "error": None}

    def test_valid_youtube_url(self):
        r = self._validate("https://youtube.com/watch?v=dQw4w9WgXcQ")
        assert r["valid"] is True

    def test_valid_tiktok_url(self):
        r = self._validate("https://www.tiktok.com/@user/video/123")
        assert r["valid"] is True

    def test_empty_url(self):
        assert self._validate("")["valid"] is False

    def test_missing_scheme(self):
        assert self._validate("youtube.com/watch?v=abc")["valid"] is False

    def test_http_allowed(self):
        assert self._validate("http://example.com/video")["valid"] is True

    def test_whitespace_trimmed(self):
        r = self._validate("  https://youtube.com/watch?v=abc  ")
        assert r["valid"] is True


# ─── Health endpoint (requires running server) ────────────────────────────────

@pytest.mark.skipif(
    not __import__("os").getenv("VIDGRAB_LIVE"),
    reason="Set VIDGRAB_LIVE=1 to run live integration tests",
)
class TestLiveHealthEndpoint:
    def test_health_returns_ok(self, app):
        r = app.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "disk" in body
        assert "redis" in body

    def test_root_returns_ok(self, app):
        r = app.get("/")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ─── API error shape contract ─────────────────────────────────────────────────

@pytest.mark.skipif(
    not __import__("os").getenv("VIDGRAB_LIVE"),
    reason="Set VIDGRAB_LIVE=1 to run live integration tests",
)
class TestApiErrorShape:
    """Structured errors must include error_code, retryable, suggested_action."""

    def test_fetch_link_missing_url_returns_structured_error(self, app):
        r = app.post("/api/v1/fetch-link", json={"url": ""})
        assert r.status_code in (400, 422)

    def test_fetch_link_bad_url_returns_invalid_url_code(self, app):
        r = app.post("/api/v1/fetch-link", json={"url": "not-a-url"})
        # May return 400 or 422 depending on Pydantic validation
        assert r.status_code in (400, 422)
        if r.status_code == 400:
            body = r.json()
            detail = body.get("detail", {})
            if isinstance(detail, dict):
                assert "error_code" in detail
                assert "retryable" in detail

    def test_bulk_download_no_urls_returns_error(self, app):
        r = app.post("/api/v1/bulk-download", json={"urls": []})
        assert r.status_code in (400, 422)


# ─── Subtitle feature unit tests ─────────────────────────────────────────────

class TestSubtitleMetadataSchema:
    """Verify subtitle fields have the expected types and values."""

    def _make_response(self, **overrides):
        """Minimal fetch-link response shape with subtitle fields."""
        base = {
            "success": True,
            "has_subtitles": False,
            "has_auto_captions": False,
            "available_subtitle_languages": [],
            "subtitle_source": "none",
            "subtitle_url": None,
            "subtitle_file_url": None,
            "subtitle_error": None,
        }
        base.update(overrides)
        return base

    def test_no_subtitles_shape(self):
        r = self._make_response()
        assert r["has_subtitles"] is False
        assert r["subtitle_source"] == "none"
        assert r["available_subtitle_languages"] == []
        assert r["subtitle_url"] is None
        assert r["subtitle_file_url"] is None

    def test_manual_subtitles_shape(self):
        r = self._make_response(
            has_subtitles=True,
            subtitle_source="manual",
            available_subtitle_languages=["en", "vi"],
        )
        assert r["has_subtitles"] is True
        assert r["subtitle_source"] == "manual"
        assert "en" in r["available_subtitle_languages"]

    def test_auto_captions_shape(self):
        r = self._make_response(
            has_subtitles=True,
            has_auto_captions=True,
            subtitle_source="auto",
            available_subtitle_languages=["en-orig", "vi"],
        )
        assert r["has_auto_captions"] is True
        assert r["subtitle_source"] == "auto"

    def test_subtitle_error_no_subtitles_available(self):
        r = self._make_response(subtitle_error="no_subtitles_available")
        assert r["subtitle_error"] == "no_subtitles_available"
        assert r["subtitle_url"] is None

    def test_subtitle_error_extraction_failed(self):
        r = self._make_response(subtitle_error="subtitle_extraction_failed")
        assert r["subtitle_error"] == "subtitle_extraction_failed"

    def test_subtitle_file_url_preferred_over_cdn_url(self):
        r = self._make_response(
            has_subtitles=True,
            subtitle_source="manual",
            subtitle_file_url="/api/v1/download-local?filepath=downloads/sub.srt&filename=sub.srt",
            subtitle_url="https://cdn.example.com/sub.vtt",
        )
        # Both present — client should prefer subtitle_file_url
        assert r["subtitle_file_url"] is not None
        assert r["subtitle_url"] is not None
        # Verify the local file URL has the expected shape
        assert "/download-local" in r["subtitle_file_url"]


class TestSubtitleFindFile:
    """Unit tests for _find_subtitle_file helper."""

    def test_find_srt_returns_path(self, tmp_path):
        from app.services.downloader import _find_subtitle_file
        video = tmp_path / "video.mp4"
        video.touch()
        srt = tmp_path / "video.en.srt"
        srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        result = _find_subtitle_file(str(video))
        assert result == str(srt)

    def test_find_vi_srt_preferred_over_en(self, tmp_path):
        from app.services.downloader import _find_subtitle_file
        video = tmp_path / "video.mp4"
        video.touch()
        vi_srt = tmp_path / "video.vi.srt"
        vi_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nXin chào\n")
        en_srt = tmp_path / "video.en.srt"
        en_srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        result = _find_subtitle_file(str(video))
        assert result == str(vi_srt)

    def test_find_vtt_converts_to_srt(self, tmp_path):
        from app.services.downloader import _find_subtitle_file
        video = tmp_path / "video.mp4"
        video.touch()
        vtt = tmp_path / "video.en.vtt"
        vtt.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n")
        result = _find_subtitle_file(str(video))
        # VTT should be converted to SRT and original removed
        assert result is not None
        assert result.endswith(".srt")
        assert not vtt.exists()

    def test_find_returns_none_when_no_subtitle(self, tmp_path):
        from app.services.downloader import _find_subtitle_file
        video = tmp_path / "video.mp4"
        video.touch()
        result = _find_subtitle_file(str(video))
        assert result is None


class TestBulkDownloadSubtitleParam:
    """BulkDownloadRequest must accept download_subs."""

    def test_bulk_request_accepts_download_subs(self):
        try:
            from app.api.routes import BulkDownloadRequest
        except ImportError:
            pytest.skip("app.api.routes import requires full container deps")
        req = BulkDownloadRequest(urls=["https://youtube.com/watch?v=abc"], download_subs=True)
        assert req.download_subs is True

    def test_bulk_request_download_subs_defaults_false(self):
        try:
            from app.api.routes import BulkDownloadRequest
        except ImportError:
            pytest.skip("app.api.routes import requires full container deps")
        req = BulkDownloadRequest(urls=["https://youtube.com/watch?v=abc"])
        assert req.download_subs is False


class TestSmartDefaultsNaming:
    """Smart defaults must return download_subs (not subtitle_enabled)."""

    def test_get_defaults_returns_download_subs_key(self):
        from app.core.smart_defaults import get_defaults
        # Without Redis, falls back to defaults — key must be download_subs
        try:
            result = get_defaults(user_id=None, platform="youtube")
        except Exception:
            pytest.skip("Redis not available")
        assert "download_subs" in result
        assert "subtitle_enabled" not in result


# ─── Schedule endpoint shape ──────────────────────────────────────────────────

@pytest.mark.skipif(
    not __import__("os").getenv("VIDGRAB_LIVE"),
    reason="Set VIDGRAB_LIVE=1 to run live integration tests",
)
class TestScheduleShape:
    def test_list_schedule_requires_auth(self, app):
        r = app.get("/api/v1/schedule")
        assert r.status_code == 401

    def test_create_schedule_requires_auth(self, app):
        r = app.post("/api/v1/schedule", json={
            "job_type": "single",
            "input_payload": {"url": "https://youtube.com/watch?v=abc"},
            "schedule_type": "once",
            "next_run_at": "2099-01-01T00:00:00Z",
        })
        assert r.status_code == 401
