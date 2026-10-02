"""
/process/burn-subtitle goes through the same subtitle path as /process/subtitle
(shared yt-dlp builder, YouTube gate, classified errors, bounded time, temp
cleanup), and no processing response hands the client a server filesystem
path: links are /download-local?file=<basename>.

yt-dlp, ffmpeg and every network-touching helper are mocked.
Run: pytest tests/test_processing_burn_and_paths.py -v
"""
import asyncio
import json
import os
import subprocess
import sys
import threading

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-anon-key")

YT = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
PROXY = "http://user:pw@resi.example:8000"
COOKIES = "/tmp/fake_youtube_cookies.txt"
BOT = "ERROR: [youtube] jNQXAC9IVRw: Sign in to confirm you're not a bot"
VTT = """WEBVTT

00:00:01.200 --> 00:00:03.360
All right, so here we are
"""


@pytest.fixture
def proc(monkeypatch, tmp_path):
    from app.api import processing
    from app.core import youtube_gate
    import app.core.proxy_pool as pp
    from app.services import downloader

    monkeypatch.setattr(processing, "_DOWNLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(processing, "_assert_safe_url", lambda u: None)
    monkeypatch.setattr(processing, "_schedule_cleanup", lambda p: None)
    monkeypatch.setattr(youtube_gate, "is_youtube_enabled", lambda: True)
    monkeypatch.setattr(youtube_gate, "circuit_open", lambda: False)
    monkeypatch.setattr(youtube_gate, "over_daily_limit", lambda: False)
    monkeypatch.setattr(pp, "get_proxy_from_pool", lambda platform: PROXY)
    monkeypatch.setattr(downloader, "get_proxy_config_for_phase",
                        lambda u, phase="metadata": None)
    monkeypatch.setattr(downloader, "_get_youtube_cookies_file", lambda: COOKIES)
    monkeypatch.delenv("PROXY_POOL_YT_STATIC", raising=False)
    monkeypatch.delenv("BGUTIL_POT_URL", raising=False)
    return processing


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "abc123def456_18.mp4"
    p.write_bytes(b"\x00" * 64)
    return p


def _ydl(calls, script):
    """One script entry per attempt: Exception to raise, None = no track,
    or (lang, ext, body) to write."""
    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts
            calls.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            step = script[len(calls) - 1]
            if isinstance(step, BaseException):
                raise step
            if step is None:
                return {"id": "x", "requested_subtitles": None}
            lang, ext, body = step
            with open(f"{self.opts['outtmpl']}.{lang}.{ext}", "w", encoding="utf-8") as f:
                f.write(body)
            return {"id": "x", "requested_subtitles": {lang: {"ext": ext}}}
    return FakeYDL


def _dl_error(msg):
    import yt_dlp
    return yt_dlp.utils.DownloadError(msg)


class FakeFFmpeg:
    """Stands in for _run_burn_ffmpeg. Records the thread it ran on."""

    def __init__(self, result=True, raise_=None, write_partial=True):
        self.calls = []
        self.result = result
        self.raise_ = raise_
        self.write_partial = write_partial

    def __call__(self, video_path, sub_path, output_path, timeout):
        self.calls.append({
            "video": video_path, "sub": sub_path, "out": output_path,
            "timeout": timeout, "sub_text": open(sub_path, encoding="utf-8").read(),
            "thread": threading.current_thread(),
        })
        if self.write_partial:
            with open(output_path, "wb") as f:
                f.write(b"burned")
        if self.raise_:
            raise self.raise_
        return self.result


def _burn(proc, video_path, url=YT, **kw):
    kw.setdefault("language", "en")
    payload = proc.BurnSubtitleRequest(video_path=str(video_path), source_url=url, **kw)
    return asyncio.run(proc.burn_subtitle.__wrapped__(payload, None))


def _subtitle(proc, url=YT, **kw):
    kw.setdefault("language", "en")
    payload = proc.SubtitleRequest(source_url=url, **kw)
    return asyncio.run(proc.download_subtitle.__wrapped__(payload, None))


def _temp_dirs(tmp_path):
    return [n for n in os.listdir(tmp_path) if n.startswith((".subtmp_", ".bsubtmp_"))]


def _assert_no_server_path(resp: dict, root):
    blob = json.dumps(resp)
    assert "/app/" not in blob
    assert str(root) not in blob
    assert "filepath=" not in blob
    assert "output_path" not in resp


# ── burn-subtitle: shared builder, gate, errors, bounds, cleanup ────────

class TestBurnSubtitle:
    def test_bot_block_retries_with_proxy_cookies_then_burns_off_loop(
            self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL",
                            _ydl(calls, [_dl_error(BOT), ("en", "vtt", VTT)]))
        ff = FakeFFmpeg()
        monkeypatch.setattr(proc, "_run_burn_ffmpeg", ff)

        r = _burn(proc, video)

        assert r["success"] is True
        assert len(calls) == 2
        assert "proxy" not in calls[0]
        assert calls[1]["proxy"] == PROXY and calls[1]["cookiefile"] == COOKIES
        assert calls[1]["skip_download"] is True
        # vtt converted to srt before ffmpeg, under a fixed name
        c = ff.calls[0]
        assert os.path.basename(c["sub"]) == "burn.srt"
        assert "00:00:01,200 --> 00:00:03,360" in c["sub_text"]
        assert c["video"] == os.path.realpath(video)
        assert c["timeout"] == proc._BURN_FFMPEG_TIMEOUT_SEC
        assert c["thread"] is not threading.main_thread()
        # output is a top-level file; temp dir gone
        assert os.path.isfile(os.path.join(tmp_path, r["file_id"]))
        assert r["file_id"].startswith("burned_") and r["file_id"].endswith(".mp4")
        assert _temp_dirs(tmp_path) == []
        _assert_no_server_path(r, tmp_path)

    def test_video_path_accepts_file_id(self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl([], [("en", "vtt", VTT)]))
        ff = FakeFFmpeg()
        monkeypatch.setattr(proc, "_run_burn_ffmpeg", ff)
        r = _burn(proc, video.name)
        assert r["success"] is True
        assert ff.calls[0]["video"] == os.path.realpath(video)

    def test_youtube_gate_off_is_503_without_ytdlp_or_ffmpeg(
            self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        from app.core import youtube_gate
        calls = []
        monkeypatch.setattr(youtube_gate, "is_youtube_enabled", lambda: False)
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, []))
        ff = FakeFFmpeg()
        monkeypatch.setattr(proc, "_run_burn_ffmpeg", ff)
        with pytest.raises(HTTPException) as ei:
            _burn(proc, video)
        assert ei.value.status_code == 503
        d = ei.value.detail
        assert d["error_code"] == "youtube_disabled" and d["error"] == "youtube_disabled"
        assert "YouTube" in d["message"]
        assert calls == [] and ff.calls == []
        assert _temp_dirs(tmp_path) == []

    def test_removed_video_is_404_classified(self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        calls = []
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl(calls, [
            _dl_error("ERROR: [youtube] x: This video has been removed by the uploader")]))
        ff = FakeFFmpeg()
        monkeypatch.setattr(proc, "_run_burn_ffmpeg", ff)
        with pytest.raises(ExtractionHTTPException) as ei:
            _burn(proc, video)
        assert ei.value.status_code == 404
        assert ei.value.error_code == "video_unavailable"
        assert len(calls) == 1 and ff.calls == []
        assert _temp_dirs(tmp_path) == []

    def test_blocked_everywhere_is_503(self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl([], [_dl_error(BOT), _dl_error(BOT)]))
        with pytest.raises(ExtractionHTTPException) as ei:
            _burn(proc, video)
        assert ei.value.status_code == 503
        assert ei.value.error_code == "temporary_blocked"
        assert _temp_dirs(tmp_path) == []

    def test_subtitle_timeout_is_504(self, proc, monkeypatch, tmp_path, video):
        import time
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        monkeypatch.setattr(proc, "_SUBTITLE_ATTEMPT_TIMEOUT_SEC", 0.2)

        class Slow(_ydl([], [None, None])):
            def extract_info(self, url, download=True):
                time.sleep(1)
                return {}
        monkeypatch.setattr(yt_dlp, "YoutubeDL", Slow)
        t0 = time.monotonic()
        with pytest.raises(ExtractionHTTPException) as ei:
            _burn(proc, video)
        assert ei.value.status_code == 504
        assert time.monotonic() - t0 < 1.5
        assert _temp_dirs(tmp_path) == []

    def test_no_track_is_404_no_subtitles(self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl([], [None]))
        ff = FakeFFmpeg()
        monkeypatch.setattr(proc, "_run_burn_ffmpeg", ff)
        r = _burn(proc, video, language="fr")
        assert r.status_code == 404
        body = json.loads(r.body)
        assert body["error"] == "no_subtitles"
        assert ff.calls == []
        assert _temp_dirs(tmp_path) == []

    def test_ffmpeg_timeout_is_504_and_partial_output_removed(
            self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        from app.core.extraction_errors import ExtractionHTTPException
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl([], [("en", "vtt", VTT)]))
        ff = FakeFFmpeg(raise_=subprocess.TimeoutExpired("ffmpeg", 300))
        monkeypatch.setattr(proc, "_run_burn_ffmpeg", ff)
        with pytest.raises(ExtractionHTTPException) as ei:
            _burn(proc, video)
        assert ei.value.status_code == 504
        assert sorted(os.listdir(tmp_path)) == [video.name]

    def test_ffmpeg_failure_is_500_and_partial_output_removed(
            self, proc, monkeypatch, tmp_path, video):
        import yt_dlp
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl([], [("en", "vtt", VTT)]))
        monkeypatch.setattr(proc, "_run_burn_ffmpeg", FakeFFmpeg(result=False))
        with pytest.raises(HTTPException) as ei:
            _burn(proc, video)
        assert ei.value.status_code == 500
        assert sorted(os.listdir(tmp_path)) == [video.name]

    def test_ffmpeg_command_is_thread_capped_and_time_bounded(self, proc, monkeypatch, tmp_path):
        seen = {}

        class R:
            returncode = 0
            stderr = b""

        def fake_run(cmd, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            open(cmd[-1], "wb").close()
            return R()
        monkeypatch.setattr(proc.subprocess, "run", fake_run)
        out = str(tmp_path / "o.mp4")
        assert proc._run_burn_ffmpeg("/v.mp4", "/s.srt", out, 7) is True
        from app.core.ffmpeg_budget import thread_args
        targs = thread_args()
        if targs:
            i = seen["cmd"].index("-threads")
            assert seen["cmd"][i:i + 2] == targs
        assert seen["kw"]["timeout"] == 7

    @pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "..", ""])
    def test_video_path_traversal_rejected(self, proc, bad):
        with pytest.raises(HTTPException) as ei:
            _burn(proc, bad)
        assert ei.value.status_code in (400, 404)

    def test_sibling_prefix_dir_rejected(self, proc, tmp_path):
        sib = str(tmp_path) + "_evil"
        os.makedirs(sib, exist_ok=True)
        p = os.path.join(sib, "x.mp4")
        open(p, "wb").close()
        with pytest.raises(HTTPException) as ei:
            _burn(proc, p)
        assert ei.value.status_code == 400


# ── no server paths in responses ────────────────────────────────────────

class TestNoServerPaths:
    def test_subtitle_response_has_no_server_path(self, proc, monkeypatch, tmp_path):
        import yt_dlp
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl([], [("en", "vtt", VTT)]))
        r = _subtitle(proc)
        _assert_no_server_path(r, tmp_path)
        assert r["download_url"].startswith("/api/v1/download-local?file=sub_")
        assert os.path.isfile(os.path.join(tmp_path, r["file_id"]))

    def test_package_zip_response_has_no_server_path(self, proc, tmp_path, video):
        payload = proc.PackageZipRequest(paths=[video.name], titles=["clip"])
        r = asyncio.run(proc.package_zip.__wrapped__(payload, None))
        _assert_no_server_path(r, tmp_path)
        assert r["file_id"].startswith("pkg_")

    def test_frame_thumb_response_has_no_server_path(self, proc, monkeypatch, tmp_path, video):
        class R:
            returncode = 0
            stderr = b""

        def fake_run(cmd, **kw):
            open(cmd[-1], "wb").close()
            return R()
        monkeypatch.setattr(proc.subprocess, "run", fake_run)
        payload = proc.FrameThumbRequest(local_path=str(video), timestamp=1)
        r = asyncio.run(proc.frame_thumb.__wrapped__(payload, None))
        _assert_no_server_path(r, tmp_path)

    def test_success_response_shape(self, proc, tmp_path):
        p = tmp_path / "audio_deadbeef.mp3"
        p.write_bytes(b"x")
        r = proc._success_response(str(p), "My Song.mp3")
        _assert_no_server_path(r, tmp_path)
        assert r["file_id"] == "audio_deadbeef.mp3"
        assert r["download_url"] == (
            "/api/v1/download-local?file=audio_deadbeef.mp3&filename=My%20Song.mp3")

    def test_batch_rename_link_helper_hides_dir(self):
        from app.core.local_download import download_url
        u = download_url("/app/downloads/renamed_abcd1234_ff00aa.zip", "x.zip")
        assert "/app/" not in u and "file=renamed_abcd1234_ff00aa.zip" in u


# ── GET /download-local ─────────────────────────────────────────────────

@pytest.fixture
def client_root(app, monkeypatch, tmp_path):
    from app.api import routes
    monkeypatch.setattr(routes, "_LOCAL_DOWNLOADS_DIR", str(tmp_path))
    return app


class TestDownloadLocal:
    def test_file_id_download_works_end_to_end(self, proc, client_root, monkeypatch, tmp_path):
        import yt_dlp
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _ydl([], [("en", "vtt", VTT)]))
        r = _subtitle(proc, filename="clip")
        got = client_root.get(r["download_url"])
        assert got.status_code == 200
        assert "00:00:01,200 --> 00:00:03,360" in got.text
        assert 'filename="clip.srt"' in got.headers["content-disposition"]

    @pytest.mark.parametrize("bad", [
        "../etc/passwd", "..", ".", "a/b", "/etc/passwd", "..%2fetc",
        ".subtmp_abc", "x\\..\\y", "",
    ])
    def test_traversal_and_hidden_rejected(self, client_root, tmp_path, bad):
        os.makedirs(tmp_path / ".subtmp_abc", exist_ok=True)
        r = client_root.get("/api/v1/download-local", params={"file": bad, "filename": "x"})
        assert r.status_code == 400

    def test_symlink_out_of_root_rejected(self, client_root, tmp_path):
        outside = tmp_path.parent / f"{tmp_path.name}_secret.txt"
        outside.write_text("secret")
        os.symlink(outside, tmp_path / "link.txt")
        r = client_root.get("/api/v1/download-local", params={"file": "link.txt", "filename": "x"})
        assert r.status_code == 400
        assert "secret" not in r.text

    def test_missing_and_directory_are_404(self, client_root, tmp_path):
        os.makedirs(tmp_path / "somedir", exist_ok=True)
        for name in ("nope.mp4", "somedir"):
            r = client_root.get("/api/v1/download-local", params={"file": name, "filename": "x"})
            assert r.status_code == 404

    def test_neither_param_is_422(self, client_root):
        r = client_root.get("/api/v1/download-local", params={"filename": "x"})
        assert r.status_code == 422

    def test_legacy_filepath_links_still_work(self, app):
        # Links issued before this change carry the absolute path; they stay
        # valid until the file expires (and fetch-link still builds them).
        from app.api import routes
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(routes.__file__))))
        root = os.path.join(base, "downloads")
        os.makedirs(root, exist_ok=True)
        p = os.path.join(root, "legacy_test_9f8e7d6c.srt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("legacy")
        try:
            r = app.get("/api/v1/download-local", params={"filepath": p, "filename": "a.srt"})
            assert r.status_code == 200 and r.text == "legacy"
            r = app.get("/api/v1/download-local",
                        params={"filepath": "../../../../etc/passwd", "filename": "x"})
            assert r.status_code == 403
        finally:
            os.remove(p)
