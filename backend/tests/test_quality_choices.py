"""
Task #6134 — quality choices match the video. Holds: a request for a
resolution the video lacks is reported (never swapped silently) · a
Cobalt-served file reports its real size/codec and the source's own format
list becomes the choices (it used to be empty, so the page showed fixed
"HD / 4K" buttons) · YouTube keeps its real list, every row downloaded by the
server (no proxy-signed URL leaks to the client).
"""
from __future__ import annotations

import inspect
import shutil
import subprocess

import pytest

from app.api.routes import quality_note
from app.services import downloader


def test_quality_note():
    fm = [{"type": "video", "height": 720}, {"type": "video", "height": 540}, {"type": "audio", "height": 0}]
    n = quality_note("video_1080", 720, fm)
    assert n["requested"] == 1080 and n["delivered"] == 720 and n["best_available"] == 720
    assert "1080p" in n["message"] and "720p" in n["message"]
    assert quality_note("video_1080", 1080, fm) is None          # delivered what was asked
    assert quality_note("video_720", 640, fm) is None            # within 80 %: same class
    assert quality_note("video", 360, fm) is None                # default: no specific ask
    assert quality_note("mp3_320", 0, fm) is None
    assert quality_note("video_1080", 0, fm) is None             # unknown height: say nothing


@pytest.fixture
def vid(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    p = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=72x128:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(p)], check=True)
    return p


def test_cobalt_file_reports_its_real_stream_and_keeps_source_formats(vid):
    meta = {"title": "T", "formats": [{"format_id": "dash-1", "vcodec": "vp09", "acodec": "none",
                                       "width": 1080, "height": 1920, "ext": "mp4", "url": "https://cdn/1"}]}
    out = downloader.enrich_cobalt_info({"title": "instagram_x", "filepath": str(vid)}, meta, "instagram")
    assert (out["width"], out["height"], out["vcodec"]) == (72, 128, "avc1")
    assert downloader._stream_short_side(out) == 72
    assert out["_meta_formats"] == meta["formats"] and "formats" not in out
    fi = downloader._extract_available_formats({"formats": out["_meta_formats"], "duration": 5})
    assert [v["height"] for v in fi["video_formats"]] == [1080]
    assert fi["video_formats"][0]["universal"] is False


def test_the_meta_formats_feed_the_choices():
    src = inspect.getsource(downloader._extract_video_info_impl)
    assert '"formats": info["_meta_formats"]' in src


def test_youtube_keeps_its_real_list_without_urls():
    from app.api import routes
    src = inspect.getsource(routes)
    assert 'info["available_formats"] = []' not in src
    assert '{**f, "url": "", "requires_merge": True}' in src


# ── the fields must actually reach the client through /fetch-link ───────────
from tests.test_fetch_link_uuid_scope import route  # noqa: E402,F401


def test_fetch_link_returns_the_new_fields(app, route, monkeypatch):
    async def _ok(url, *a, **k):
        return {"title": "t", "direct_mp4_url": "", "original_url": url,
                "downloaded_height": 720, "downloaded_vcodec": "vp09", "downloaded_universal": False,
                "available_formats": [{"type": "video", "height": 720, "universal": False},
                                      {"type": "video", "height": 540, "universal": False}]}
    monkeypatch.setattr(route, "extract_video_info", _ok)
    monkeypatch.setattr(route, "check_platform_quota", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(route, "increment_usage", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.core.metering.record_download", _noop)
    r = app.post("/api/v1/fetch-link", json={"url": "https://www.instagram.com/p/abc/", "quality": "video_1080"})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["downloaded_vcodec"] == "vp09" and body["downloaded_universal"] is False
    assert body["quality_note"]["requested"] == 1080 and body["quality_note"]["delivered"] == 720
