"""Quality #4 (owner 27/10/2026): Douyin's sharpest no-watermark stream, H.264
first (H.265 only for "4K" and only when strictly sharper)."""
import pytest

from app.services.douyin_extractor import _slim_aweme, pick_douyin_stream


def br(h, w, rate, h265=False, url=None, gear=""):
    return {"bit_rate": rate, "is_h265": 1 if h265 else 0, "gear_name": gear,
            "play_addr": {"height": h, "width": w, "url_list": [url or f"https://v/{h}x{w}{'-265' if h265 else ''}"]}}


VIDEO = {"play_addr": {"url_list": ["https://v/playwm/default"]},
         "bit_rate": [br(1280, 720, 900), br(1920, 1080, 2000), br(1920, 1080, 1500, h265=True),
                      br(3840, 2160, 6000, h265=True)]}


def test_default_is_sharpest_h264():
    s = pick_douyin_stream(VIDEO, "video")
    assert s["url"] == "https://v/1920x1080" and s["short_side"] == 1080 and not s["is_h265"]


def test_4k_takes_h265_only_when_sharper():
    assert pick_douyin_stream(VIDEO, "video_4k")["url"] == "https://v/3840x2160-265"
    same = {"bit_rate": [br(1920, 1080, 2000), br(1920, 1080, 2500, h265=True)]}
    assert pick_douyin_stream(same, "video_4k")["url"] == "https://v/1920x1080"


def test_requested_height_is_a_ceiling():
    assert pick_douyin_stream(VIDEO, "video_720")["url"] == "https://v/1280x720"
    tall_only = {"bit_rate": [br(1920, 1080, 2000), br(2560, 1440, 4000)]}
    assert pick_douyin_stream(tall_only, "video_480")["url"] == "https://v/1920x1080"   # smallest above


def test_gear_name_marks_h265():
    v = {"bit_rate": [br(1920, 1080, 3000, gear="adapt_bytevc1_1080"), br(1280, 720, 800)]}
    assert pick_douyin_stream(v)["url"] == "https://v/1280x720"


def test_never_a_watermarked_url():
    v = {"bit_rate": [br(1920, 1080, 2000, url="https://v/playwm/x")]}
    assert pick_douyin_stream(v)["url"] == "https://v/play/x"
    assert pick_douyin_stream({"play_addr": {"url_list": ["https://v/playwm/d"]}})["url"] == "https://v/play/d"


def test_falls_back_to_play_addr_h264_then_play_addr():
    v = {"play_addr_h264": {"url_list": ["https://v/h264"]}, "play_addr": {"url_list": ["https://v/any"]}}
    assert pick_douyin_stream(v)["from"] == "play_addr_h264"
    assert pick_douyin_stream({"play_addr": {"url_list": ["https://v/any"]}})["url"] == "https://v/any"
    assert pick_douyin_stream({})["url"] == ""


def test_slim_cache_keeps_what_the_picker_needs():
    slim = _slim_aweme({"video": VIDEO})
    assert pick_douyin_stream(slim["video"], "video")["url"] == "https://v/1920x1080"
    assert pick_douyin_stream(slim["video"], "video_4k")["url"] == "https://v/3840x2160-265"
