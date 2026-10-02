"""
TikWM returns only stream URLs and sizes — never width/height. The format list
used to hardcode 1080 / 720 / 540 and "HD" / "SD", so the UI showed
"H.264 (Chất lượng gốc) · SD" next to "H.265 (Dung lượng nhỏ) · HD" for numbers
nobody measured. Unknown must stay unknown: height 0, no SD claim, no bitrate.

The codec names in those labels were unverified too, so the labels now say
only what TikWM tells us (HD stream or not, logo or not); measured resolution
and codec come later from POST /formats/probe.
"""
from app.services import downloader


async def _fake_tikwm(url, quality):
    return {
        "title": "t",
        "thumbnail_url": "",
        "direct_mp4_url": "https://cdn.example/play.mp4",
        "hdplay_url": "https://cdn.example/hd.mp4",
        "play_url": "https://cdn.example/play.mp4",
        "wmplay_url": "https://cdn.example/wm.mp4",
        "audio_url": "https://cdn.example/a.mp3",
        "hd_size_mb": 2.96,
        "size_mb": 3.01,
        "duration": 26,
    }


def test_tikwm_formats_carry_no_guessed_dimensions(monkeypatch):
    from app.services import format_probe
    monkeypatch.setattr(downloader, "_try_tikwm", _fake_tikwm)
    monkeypatch.setattr(format_probe, "register_issued_urls", lambda urls: None)
    info = downloader._extract_video_info_impl(
        "https://www.tiktok.com/@tiktok/video/6584647400055377158"
    )
    assert info["provider"] == "tikwm"
    videos = [f for f in info["available_formats"] if f["type"] == "video"]
    audios = [f for f in info["available_formats"] if f["type"] == "audio"]
    assert len(videos) == 3 and len(audios) == 1

    assert all(f["height"] == 0 for f in videos)
    # No resolution claim at all: "HD" is in the hdplay label (TikWM's name),
    # the measured resolution arrives later from /formats/probe.
    assert [f["resolution"] for f in videos] == ["", "", ""]
    assert [f["label"] for f in videos] == ["HD không logo", "Thường không logo", "Có logo"]
    # Labels must not assert a codec nobody measured.
    assert not any("H.26" in f["label"] for f in videos)
    assert audios[0]["bitrate"] is None


def test_tikwm_video_urls_are_registered_for_probe(monkeypatch):
    from app.services import format_probe
    monkeypatch.setattr(downloader, "_try_tikwm", _fake_tikwm)
    seen = []
    monkeypatch.setattr(format_probe, "register_issued_urls", lambda urls: seen.extend(urls))
    downloader._extract_video_info_impl(
        "https://www.tiktok.com/@tiktok/video/6584647400055377158"
    )
    # Video streams only — the MP3 is never probed.
    assert seen == [
        "https://cdn.example/hd.mp4",
        "https://cdn.example/play.mp4",
        "https://cdn.example/wm.mp4",
    ]
