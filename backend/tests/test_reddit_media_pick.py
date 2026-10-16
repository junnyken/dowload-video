"""
Reddit downloads must hand the user a playable file.

v.redd.it serves picture and sound as separate HLS/DASH renditions. The old
extractor fell back to the LAST format's URL, which for v.redd.it is a
video-only HLS variant playlist: the browser saved a ~400-byte text file named
.mp4 (measured on prod 2026-10-16, "That small heart attack."). Now a direct
URL is used only when it is a real file; otherwise the master playlist is
muxed server-side by ffmpeg.
"""
from unittest import mock

import pytest

from app.services import downloader
from app.services.reddit_extractor import _pick_reddit_media

VID = "zv89llsvexdz"
MASTER = f"https://v.redd.it/{VID}/HLSPlaylist.m3u8"

# The shape yt-dlp returns for a v.redd.it post: HLS variants (video-only
# picture + separate audio group), DASH video-only mp4 and DASH audio.
VREDDIT_FORMATS = [
    {"url": f"https://v.redd.it/{VID}/HLS_AUDIO_160_K_v4.m3u8",
     "manifest_url": MASTER, "vcodec": "none", "acodec": "mp4a.40.2"},
    {"url": f"https://v.redd.it/{VID}/DASH_480.mp4",
     "vcodec": "avc1.4d001f", "acodec": "none", "height": 480},
    {"url": f"https://v.redd.it/{VID}/DASH_AUDIO_128.mp4",
     "vcodec": "none", "acodec": "mp4a.40.2"},
    {"url": f"https://v.redd.it/{VID}/HLS_1_5_M_v4.m3u8",
     "manifest_url": MASTER, "vcodec": "avc1.4d001f", "acodec": "none", "height": 640},
]


def test_vreddit_never_returns_a_playlist_or_silent_video_as_the_file():
    direct, master = _pick_reddit_media({"formats": VREDDIT_FORMATS})
    # Before the fix this was .../HLS_1_5_M_v4.m3u8 (the last format).
    assert direct == ""
    assert master == MASTER


def test_vreddit_master_found_from_plain_format_urls_without_manifest_url():
    fmts = [{k: v for k, v in f.items() if k != "manifest_url"} for f in VREDDIT_FORMATS]
    assert _pick_reddit_media({"formats": fmts}) == ("", MASTER)


def test_external_embed_single_url_is_used_directly():
    info = {"url": "https://i.imgur.com/abc.mp4", "formats": VREDDIT_FORMATS}
    assert _pick_reddit_media(info) == ("https://i.imgur.com/abc.mp4", "")


def test_top_level_playlist_url_is_not_treated_as_a_file():
    info = {"url": f"https://v.redd.it/{VID}/HLS_1_5_M_v4.m3u8", "formats": VREDDIT_FORMATS}
    assert _pick_reddit_media(info) == ("", MASTER)


def test_progressive_format_with_sound_is_used_directly():
    fmts = VREDDIT_FORMATS + [
        {"url": "https://cdn.example/low.mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 360},
        {"url": "https://cdn.example/high.mp4", "vcodec": "avc1", "acodec": "mp4a", "height": 720},
    ]
    assert _pick_reddit_media({"formats": fmts}) == ("https://cdn.example/high.mp4", "")


def test_nothing_usable_returns_empty():
    assert _pick_reddit_media({"formats": [{"url": "https://x.example/a.m3u8"}]}) == ("", "")


# ── server-side mux ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "https://evil.example/HLSPlaylist.m3u8",
    "file:///etc/passwd",
    f"https://v.redd.it/{VID}/HLS_1_5_M_v4.m3u8",
    f"https://v.redd.it/{VID}/HLSPlaylist.m3u8?x=http://169.254.169.254/",
    f"http://v.redd.it/{VID}/HLSPlaylist.m3u8",
])
def test_mux_refuses_anything_but_a_vreddit_master_playlist(bad):
    with mock.patch("subprocess.run") as run:
        assert downloader._reddit_mux_hls(bad, "video") is None
    run.assert_not_called()


def _fake_ffmpeg(cmd, **kwargs):
    with open(cmd[-1], "wb") as f:
        f.write(b"\x00" * 2048)


@pytest.mark.parametrize("quality,ext,expect", [
    ("video", ".mp4", ["-c", "copy"]),
    ("mp3_320", ".mp3", ["-vn", "-acodec", "libmp3lame"]),
])
def test_mux_builds_the_right_output(tmp_path, monkeypatch, quality, ext, expect):
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", str(tmp_path))
    with mock.patch("subprocess.run", side_effect=_fake_ffmpeg) as run:
        out = downloader._reddit_mux_hls(MASTER, quality)
    assert out and out.endswith(ext) and out.startswith(str(tmp_path))
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("-i") + 1] == MASTER
    assert "https,tls,tcp,crypto" in cmd
    joined = " ".join(cmd)
    assert " ".join(expect) in joined


def test_mux_failure_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", str(tmp_path))
    with mock.patch("subprocess.run", side_effect=RuntimeError("boom")):
        assert downloader._reddit_mux_hls(MASTER, "video") is None
    assert list(tmp_path.iterdir()) == []


# ── wiring in the downloader's Reddit branch ─────────────────────────────

def _extract(url, quality="video"):
    return {
        "platform": "Reddit", "title": "That small heart attack.", "duration": 12,
        "formats": [], "direct_url": "", "hls_master_url": MASTER,
        "error_code": None, "error_message": None,
    }


def test_reddit_branch_returns_the_muxed_local_file(tmp_path):
    local = tmp_path / "reddit_x.mp4"
    local.write_bytes(b"\x00" * 4096)
    with mock.patch("app.services.reddit_extractor.extract_reddit_single", _extract), \
         mock.patch.object(downloader, "_reddit_mux_hls", return_value=str(local)) as mux:
        res = downloader._extract_video_info_impl(
            "https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/")
    mux.assert_called_once_with(MASTER, "video")
    assert res["local_file_path"] == str(local)
    assert not res["direct_mp4_url"]
    assert "direct_url" not in res


def test_reddit_branch_does_not_hand_out_a_playlist_when_mux_fails():
    with mock.patch("app.services.reddit_extractor.extract_reddit_single", _extract), \
         mock.patch.object(downloader, "_reddit_mux_hls", return_value=None), \
         mock.patch("app.services.cobalt_service.is_cobalt_available", return_value=False):
        with pytest.raises(ValueError, match="Reddit"):
            downloader._extract_video_info_impl(
                "https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/")
