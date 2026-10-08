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
    assert n["message"] == "Video này không có bản 1080p. Đã tải bản 720p."
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


def test_quality_note_tells_an_account_limit_from_a_missing_resolution():
    fm = [{"type": "video", "height": 2160}, {"type": "video", "height": 720}]
    g = quality_note("video_2160", 720, fm, {"height": 720, "reason": "signin"})
    assert g["limit"] == 720 and g["limit_reason"] == "signin" and "Đăng nhập" in g["message"]
    assert "không có bản" not in g["message"]                     # the video HAS 2160p
    u = quality_note("video_2160", 1080, fm, {"height": 1080, "reason": "plan"})
    assert u["message"].startswith("Gói hiện tại tải tối đa 1080p")
    # asked below the cap, still missing → the video lacks it
    n = quality_note("video_720", 480, [{"type": "video", "height": 480}], {"height": 1080, "reason": "plan"})
    assert n["message"].startswith("Video này không có bản 720p")


def test_rows_above_the_cap_are_locked_not_hidden():
    from app.api.routes import lock_rows_above_cap
    fm = [{"type": "video", "height": 2160}, {"type": "video", "height": 1080},
          {"type": "video", "height": 720}, {"type": "audio", "bitrate": 128}]
    out = lock_rows_above_cap(fm, {"height": 720, "reason": "signin"})
    assert [f.get("locked") for f in out] == ["signin", "signin", None, None]
    assert lock_rows_above_cap(fm, {"height": 0, "reason": None}) == fm


def test_the_route_wires_the_cap_through():
    import inspect
    from app.api import routes
    src = inspect.getsource(routes)
    assert 'lock_rows_above_cap(info.get("available_formats") or [], _height_cap)' in src
    assert '"max_allowed_height": _height_cap["height"]' in src
    assert 'quality_note(payload.quality, info.get("downloaded_height") or 0,' in src


def test_free_max_height_is_configurable(monkeypatch):
    import importlib
    from app.core import quotas
    monkeypatch.setenv("FREE_MAX_HEIGHT", "4320")
    q = importlib.reload(quotas)
    try:
        monkeypatch.setattr(q, "_get_tier", lambda uid: "free")
        assert q.check_quality_permission("u", "video_2160", height=2160)["allowed"] is True
    finally:
        monkeypatch.delenv("FREE_MAX_HEIGHT")
        q = importlib.reload(quotas)
    monkeypatch.setattr(q, "_get_tier", lambda uid: "free")
    assert q.check_quality_permission("u", "video_2160", height=2160)["allowed"] is False   # default unchanged


def test_lock_reflects_the_account_cap_not_the_request():
    import inspect
    from app.api import routes
    src = inspect.getsource(routes)
    i, j = src.index('_height_cap = {"height": _cap'), src.index("_cap = min(_cap, _gate_h)")
    assert i < j, "the account cap must be recorded before the per-request gate narrows _cap"


def _stub(route, monkeypatch, seen):
    async def _ok(url, quality="video", *a, **k):
        seen["quality"] = quality
        return {"title": "t", "direct_mp4_url": "", "original_url": url, "downloaded_height": 1080,
                "available_formats": [{"type": "video", "height": 2160, "universal": False},
                                      {"type": "video", "height": 1440, "universal": False},
                                      {"type": "video", "height": 1080, "universal": True}]}
    monkeypatch.setattr(route, "extract_video_info", _ok)
    monkeypatch.setattr(route, "check_platform_quota", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(route, "increment_usage", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.core.metering.record_download", _noop)


@pytest.mark.parametrize("asked", ["video_2160", "video_4k", "4k"])
def test_a_guest_gets_1080_and_the_2k_4k_rows_say_sign_in(app, route, monkeypatch, asked):
    seen = {}
    _stub(route, monkeypatch, seen)
    r = app.post("/api/v1/fetch-link", json={"url": "https://vimeo.com/123", "quality": asked})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert seen["quality"] == "video_1080"
    assert body["max_allowed_height"] == 1080 and body["height_cap_reason"] == "signin"
    assert {f["height"]: f.get("locked") for f in body["available_formats"]} == {2160: "signin", 1440: "signin", 1080: None}
    if asked == "video_2160":
        assert "Đăng nhập" in body["quality_note"]["message"]


def test_a_signed_in_user_gets_2k_4k(app, route, monkeypatch):
    from app.core.auth_middleware import get_optional_user
    import app.main as main_mod
    seen = {}
    _stub(route, monkeypatch, seen)
    main_mod.app.dependency_overrides[get_optional_user] = lambda: {"id": "u-1", "tier": "free"}
    try:
        r = app.post("/api/v1/fetch-link", json={"url": "https://vimeo.com/123", "quality": "video_2160"})
    finally:
        main_mod.app.dependency_overrides.pop(get_optional_user, None)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert seen["quality"] == "video_2160"
    assert body["max_allowed_height"] == 0
    assert all(not f.get("locked") for f in body["available_formats"])



def test_a_video_below_the_limit_is_not_blamed_on_the_account():
    fm = [{"type": "video", "height": 720}, {"type": "video", "height": 360}]
    n = quality_note("video_2160", 720, fm, {"height": 1080, "reason": "signin"})
    assert n["message"] == "Video này không có bản 2160p. Đã tải bản 720p."
    assert "limit" not in n
