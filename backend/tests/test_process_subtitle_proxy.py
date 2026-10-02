"""
/process/subtitle goes through the same yt-dlp option builder as video
extraction (proxy pool, cookies, YouTube client + PO token), honours the
YouTube gate, classifies failures, bounds time/size and cleans its temp dir.

yt-dlp and every network-touching helper are mocked.
Run: pytest backend/tests/test_process_subtitle_proxy.py -v
"""
import asyncio
import json
import os
import sys
import time

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-anon-key")

YT = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
PROXY = "http://user:pw@resi.example:8000"
COOKIES = "/tmp/fake_youtube_cookies.txt"

VTT = """WEBVTT

00:00:01.200 --> 00:00:03.360
All right, so here we are

00:00:05.318 --> 00:00:07.974
really long trunks
"""

BOT = "ERROR: [youtube] jNQXAC9IVRw: Sign in to confirm you're not a bot"


@pytest.fixture
def proc(monkeypatch, tmp_path):
    from app.api import processing
    from app.core import youtube_gate
    import app.core.proxy_pool as pp
    from app.services import downloader

    monkeypatch.setattr(processing, "_DOWNLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(processing, "_assert_safe_url", lambda u: None)
    monkeypatch.setattr(processing, "_schedule_cleanup", lambda p: None)
    # Gate open by default (no Redis).
    monkeypatch.setattr(youtube_gate, "is_youtube_enabled", lambda: True)
    monkeypatch.setattr(youtube_gate, "circuit_open", lambda: False)
    monkeypatch.setattr(youtube_gate, "over_daily_limit", lambda: False)
    # The pool hands out a residential proxy; the cookie pool a cookie file.
    monkeypatch.setattr(pp, "get_proxy_from_pool", lambda platform: PROXY)
    monkeypatch.setattr(downloader, "get_proxy_config_for_phase",
                        lambda u, phase="metadata": None)
    monkeypatch.setattr(downloader, "_get_youtube_cookies_file", lambda: COOKIES)
    monkeypatch.delenv("PROXY_POOL_YT_STATIC", raising=False)
    monkeypatch.delenv("BGUTIL_POT_URL", raising=False)
    return processing


def _ydl(calls, script):
    """FakeYoutubeDL. `script` is a list, one entry per attempt: an Exception
    to raise, or (lang, ext, body) to write, or None for "no track"."""
    import yt_dlp  # noqa: F401

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            calls.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            assert download is True
            step = script[len(calls) - 1]
            if isinstance(step, BaseException):
                raise step
            if callable(step):
                return step(self.opts)
            if step is None:
                return {"id": "x", "requested_subtitles": None}
            lang, ext, body = step
            with open(f"{self.opts['outtmpl']}.{lang}.{ext}", "w", encoding="utf-8") as f:
                f.write(body)
            return {"id": "x", "requested_subtitles": {lang: {"ext": ext}}}
    return FakeYDL


def _call(proc, url=YT, **kw):
    kw.setdefault("language", "en")
    payload = proc.SubtitleRequest(source_url=url, **kw)
    return asyncio.run(proc.download_subtitle.__wrapped__(payload, None))


def _dl_error(msg):
    import yt_dlp
    return yt_dlp.utils.DownloadError(msg)


def _leftover_tmp(tmp_path):
    return [n for n in os.listdir(tmp_path) if n.startswith(".subtmp_")]


# ── shared builder ──────────────────────────────────────────────────────

class TestSharedBuilder:
    def test_youtube_attempts_direct_then_proxy_cookies_pot(self, proc):
        from app.services.downloader import build_subtitle_attempts
        attempts = build_subtitle_attempts(YT, ["en"], "srt/vtt/best", "/x/sub")
        assert [a[0] for a in attempts] == ["direct", "full"]
        direct, full = attempts[0][1], attempts[1][1]

        # direct: free route, like Phase A Layer 1 — no proxy, no cookies
        assert "proxy" not in direct and "cookiefile" not in direct
        assert "youtubepot-bgutilscript" in direct["extractor_args"]

        # full: exactly what the video builder attaches
        assert full["proxy"] == PROXY
        assert full["cookiefile"] == COOKIES
        assert full["extractor_args"]["youtube"]["player_client"] == ["android_vr", "web_safari"]
        assert "youtubepot-bgutilscript" in full["extractor_args"]
        assert full["js_runtimes"] == {"node": {}}

        for o in (direct, full):
            assert o["skip_download"] is True
            assert o["writesubtitles"] and o["writeautomaticsub"]
            assert o["subtitleslangs"] == ["en"]
            assert o["subtitlesformat"] == "srt/vtt/best"
            assert o["outtmpl"] == "/x/sub"
            assert o["ignoreerrors"] is False
            assert o["ignore_no_formats_error"] is True
            assert o["logger"] is not None
            for k in ("format", "postprocessors", "merge_output_format"):
                assert k not in o

    def test_video_builder_unchanged(self, proc):
        from app.services.downloader import _get_base_opts
        o = _get_base_opts(YT, phase="metadata", quality="video")
        assert o["proxy"] == PROXY and o["cookiefile"] == COOKIES
        assert "format" in o and "skip_download" not in o

    def test_no_proxy_no_cookies_means_single_attempt(self, proc, monkeypatch):
        import app.core.proxy_pool as pp
        from app.services.downloader import build_subtitle_attempts
        monkeypatch.setattr(pp, "get_proxy_from_pool", lambda platform: None)
        attempts = build_subtitle_attempts("https://vimeo.com/1", ["en"], "srt", "/x/sub")
        assert [a[0] for a in attempts] == ["direct"]


# ── endpoint ────────────────────────────────────────────────────────────

class TestEndpointUsesBuilder:
    def test_bot_block_on_direct_retries_with_proxy_and_cookies(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL",
                            _ydl(calls, [_dl_error(BOT), ("en", "vtt", VTT)]))
        r = _call(proc)
        assert r["success"] is True
        assert len(calls) == 2
        assert "proxy" not in calls[0]
        assert calls[1]["proxy"] == PROXY and calls[1]["cookiefile"] == COOKIES
        assert r["output_path"].endswith(".en.srt")
        out = open(r["output_path"], encoding="utf-8").read()
        assert "00:00:01,200 --> 00:00:03,360" in out and "WEBVTT" not in out
        assert _leftover_tmp(tmp_path) == []

    def test_direct_success_spends_no_proxy(self, proc, monkeypatch):
        import yt_dlp
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [("en", "vtt", VTT)]))
        assert _call(proc)["success"] is True
        assert len(calls) == 1 and "proxy" not in calls[0]


class TestGate:
    def test_youtube_disabled_is_503_and_never_calls_ytdlp(self, proc, monkeypatch):
        import yt_dlp
        from app.core import youtube_gate
        calls = []
        monkeypatch.setattr(youtube_gate, "is_youtube_enabled", lambda: False)
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, []))
        with pytest.raises(HTTPException) as ei:
            _call(proc)
        assert ei.value.status_code == 503
        assert ei.value.detail["error_code"] == "youtube_disabled"
        assert "YouTube" in ei.value.detail["message"]
        assert calls == []

    def test_circuit_open_is_503(self, proc, monkeypatch):
        from app.core import youtube_gate
        monkeypatch.setattr(youtube_gate, "circuit_open", lambda: True)
        monkeypatch.setattr(youtube_gate, "circuit_cooldown_remaining", lambda: 120)
        with pytest.raises(HTTPException) as ei:
            _call(proc)
        assert ei.value.status_code == 503
        assert ei.value.detail["error_code"] == "youtube_circuit_open"
        assert ei.value.detail["cooldown_sec"] == 120

    def test_gate_not_applied_to_other_platforms(self, proc, monkeypatch):
        import yt_dlp
        from app.core import youtube_gate
        calls = []
        monkeypatch.setattr(youtube_gate, "is_youtube_enabled", lambda: False)
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [("en", "srt", "1\n00:00:01,000 --> 00:00:02,000\nhi\n")]))
        assert _call(proc, url="https://www.tiktok.com/@a/video/1")["success"] is True


class TestErrors:
    def test_language_not_available_is_404(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [None]))
        r = _call(proc, language="fr")
        assert r.status_code == 404
        body = json.loads(r.body)
        assert body["error"] == "no_subtitles"
        assert body["error_code"] == "subtitle_language_unavailable"
        assert len(calls) == 1  # no paid retry for a missing track
        assert _leftover_tmp(tmp_path) == []

    def test_private_video_is_4xx_after_cookie_retry(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        calls = []
        err = "ERROR: [youtube] x: Private video. Sign in if you've been granted access"
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [_dl_error(err), _dl_error(err)]))
        with pytest.raises(ExtractionHTTPException) as ei:
            _call(proc)
        assert ei.value.status_code == 422
        assert ei.value.error_code == "private_or_login_required"
        assert ei.value.detail.startswith("Không tải được phụ đề")
        assert len(calls) == 2
        assert _leftover_tmp(tmp_path) == []

    def test_removed_video_is_404_without_retry(self, proc, monkeypatch):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [
            _dl_error("ERROR: [youtube] x: This video has been removed by the uploader")]))
        with pytest.raises(ExtractionHTTPException) as ei:
            _call(proc)
        assert ei.value.status_code == 404
        assert ei.value.error_code == "video_unavailable"
        assert len(calls) == 1

    def test_blocked_on_every_route_is_503(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [_dl_error(BOT), _dl_error(BOT)]))
        with pytest.raises(ExtractionHTTPException) as ei:
            _call(proc)
        assert ei.value.status_code == 503
        assert ei.value.error_code == "temporary_blocked"
        assert _leftover_tmp(tmp_path) == []

    def test_timeout_is_504(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        monkeypatch.setattr(proc, "_SUBTITLE_ATTEMPT_TIMEOUT_SEC", 0.2)

        def slow(opts):
            time.sleep(1)
            return {"id": "x", "requested_subtitles": None}
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [slow, slow]))
        t0 = time.monotonic()
        with pytest.raises(ExtractionHTTPException) as ei:
            _call(proc)
        assert ei.value.status_code == 504
        assert ei.value.error_code == "extraction_timeout"
        assert time.monotonic() - t0 < 1.5  # both attempts bounded
        assert _leftover_tmp(tmp_path) == []

    def test_oversized_subtitle_is_413_and_cleaned(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        monkeypatch.setattr(proc, "_SUBTITLE_MAX_BYTES", 100)
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [("en", "vtt", VTT * 10)]))
        with pytest.raises(ExtractionHTTPException) as ei:
            _call(proc)
        assert ei.value.status_code == 413
        assert os.listdir(tmp_path) == []


class TestTxtAndCleanup:
    def test_txt_output_and_only_final_file_remains(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [("en", "vtt", VTT)]))
        r = _call(proc, format="txt")
        assert r["output_path"].endswith(".txt")
        text = open(r["output_path"], encoding="utf-8").read()
        assert "really long trunks" in text and "-->" not in text
        assert os.listdir(tmp_path) == [os.path.basename(r["output_path"])]
