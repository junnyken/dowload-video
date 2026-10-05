"""Formats with no `height` (MangoTV) must still be selectable.

Offline: synthetic format lists through the real yt-dlp selector engine.
"""
import pytest
from yt_dlp import YoutubeDL

from app.services.downloader import _get_base_opts

URL = "https://www.mgtv.com/b/609457/20644104.html"
QUALITIES = ["video", "video_360", "video_480", "video_720", "video_1080", "video_1440"]


def _sel(q):
    return _get_base_opts(URL, quality=q)["format"]


def _fmt(fid, height, vcodec="avc1.4d401f", acodec="mp4a.40.2", ext="mp4"):
    return {
        "format_id": fid, "url": f"http://x/{fid}", "ext": ext, "protocol": "https",
        "vcodec": vcodec, "acodec": acodec, "height": height,
        "width": None if height is None else height * 16 // 9,
    }


def _pick(sel, formats):
    ydl = YoutubeDL({"quiet": True, "format": sel})
    info = {"id": "x", "title": "t", "extractor": "t", "formats": formats}
    try:
        r = ydl.process_ie_result(info, download=False)
    except Exception:
        return None
    return [f["format_id"] for f in (r.get("requested_formats") or [r])]


def _no_height_list():  # MangoTV-like: single progressive formats, height unknown
    return [_fmt("lo", None), _fmt("hi", None)]


@pytest.mark.parametrize("q", QUALITIES)
def test_unknown_height_is_selected(q):
    assert _pick(_sel(q), _no_height_list()) is not None


@pytest.mark.parametrize("q", QUALITIES)
def test_every_selector_ends_with_unknown_height_fallback(q):
    last = _sel(q).split("/")[-1]
    assert "<=?" in last or last == "best"


def test_unfixed_selector_fails_on_unknown_height():
    # documents the original bug
    assert _pick("bestvideo[height<=1080]+bestaudio/best[height<=1080]", _no_height_list()) is None


def test_known_heights_under_cap_unchanged():
    fm = [_fmt("a", 360), _fmt("b", 720), _fmt("c", 1080)]
    assert _pick(_sel("video_1080"), fm) == ["c"]
    assert _pick(_sel("video_720"), fm) == ["b"]
    assert _pick(_sel("video_360"), fm) == ["a"]
    assert _pick(_sel("video"), fm) == ["c"]


def test_some_above_cap_picks_best_under_cap():
    fm = [_fmt("a", 360), _fmt("b", 720), _fmt("c", 2160)]
    assert _pick(_sel("video_1080"), fm) == ["b"]
    assert _pick(_sel("video"), fm) == ["b"]


@pytest.mark.parametrize("q", ["video_360", "video_480", "video_720", "video_1080", "video"])
def test_all_above_cap_still_rejected_for_progressive(q):
    cap = {"video_360": 360, "video_480": 480, "video_720": 720, "video_1080": 1080, "video": 1080}[q]
    fm = [_fmt("big", cap * 2, acodec="mp4a.40.2")]
    assert _pick(_sel(q), fm) is None


def test_360_cap_rejects_lone_1080p():
    assert _pick(_sel("video_360"), [_fmt("only1080", 1080)]) is None


def test_mixed_known_and_unknown_prefers_known_under_cap():
    fm = [_fmt("u", None), _fmt("k", 720)]
    assert _pick(_sel("video_1080"), fm) == ["k"]


def test_mixed_unknown_and_known_above_cap_uses_unknown():
    fm = [_fmt("u", None), _fmt("big", 1080)]
    assert _pick(_sel("video_360"), fm) == ["u"]
