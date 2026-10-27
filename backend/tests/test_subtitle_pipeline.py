"""Quality #7 (27/10/2026): downloaded captions become a valid SRT (YouTube
auto-caption repeats removed) and soft subtitles keep an .mp4 an .mp4."""
import subprocess

import pytest

from app.services.subtitle_format import parse_srt, timestamp_to_seconds, vtt_to_clean_srt

AUTO = """WEBVTT
Kind: captions
Language: vi

00:00:00.000 --> 00:00:02.000 align:start position:0%
xin<00:00:00.500><c> chào</c><00:00:01.000><c> các</c><00:00:01.500><c> bạn</c>

00:00:02.000 --> 00:00:02.010 align:start position:0%
xin chào các bạn
 

00:00:02.010 --> 00:00:04.000 align:start position:0%
xin chào các bạn
hôm<00:00:02.500><c> nay</c><00:00:03.000><c> Đường</c><00:00:03.500><c> về</c>

00:00:04.000 --> 00:00:04.010 align:start position:0%
hôm nay Đường về
 
"""


def test_auto_captions_lose_their_repeats():
    srt = vtt_to_clean_srt(AUTO)
    cues = parse_srt(srt)
    assert [c.text for c in cues] == ["xin chào các bạn", "hôm nay Đường về"]
    assert [c.start for c in cues] == ["00:00:00,000", "00:00:02,010"]
    starts = [timestamp_to_seconds(c.start) for c in cues]
    assert starts == sorted(starts) and all(timestamp_to_seconds(c.end) > timestamp_to_seconds(c.start) for c in cues)


def test_human_captions_unchanged_short_timestamps():
    vtt = "WEBVTT\n\n01:02.500 --> 01:04.000\n<i>Một</i> câu\n\n01:04.000 --> 01:06.000\nCâu hai\n"
    cues = parse_srt(vtt_to_clean_srt(vtt))
    assert [(c.start, c.text) for c in cues] == [("00:01:02,500", "Một câu"), ("00:01:04,000", "Câu hai")]


@pytest.mark.parametrize("bad", ["", "WEBVTT\n\nNOTE only\n", "not a subtitle"])
def test_nothing_usable_gives_none(bad):
    assert vtt_to_clean_srt(bad) is None


def test_downloader_writes_no_file_for_a_broken_vtt(tmp_path):
    from app.services.downloader import _convert_vtt_to_srt
    v, s = tmp_path / "a.vi.vtt", tmp_path / "a.vi.srt"
    v.write_text("garbage", encoding="utf-8")
    _convert_vtt_to_srt(str(v), str(s))
    assert not s.exists()


def test_soft_subtitles_keep_mp4(monkeypatch, tmp_path):
    import app.api.routes as r
    assert r._soft_sub_output("/x/v.mp4") == "/x/v_subbed.mp4"
    assert r._soft_sub_output("/x/v.webm") == "/x/v_subbed.mkv"
    seen = []
    monkeypatch.setattr(r._subprocess, "run",
                        lambda cmd, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 1, b"", b""))
    r._mux_subtitle("/x/v.mp4", "/x/v.vi.srt", "/x/v_subbed.mp4")
    cmd = seen[0]
    assert cmd[cmd.index("-c:s") + 1] == "mov_text" and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "language=vie" in cmd and "+faststart" in cmd
    r._mux_subtitle("/x/v.webm", "/x/v.en.srt", "/x/v_subbed.mkv")
    assert seen[1][seen[1].index("-c:s") + 1] == "srt" and "language=eng" in seen[1]
