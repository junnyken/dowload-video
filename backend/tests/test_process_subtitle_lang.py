"""
/process/subtitle: pick a concrete language and always hand back an .srt.
Run: pytest backend/tests/test_process_subtitle_lang.py -v
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-anon-key")

VTT = """WEBVTT
Kind: captions
Language: ja

00:00:01.000 --> 00:00:03.500 align:start position:0%
こんにちは

00:00:04.000 --> 00:00:06.000
さようなら
"""


@pytest.fixture
def proc(monkeypatch, tmp_path):
    from app.api import processing
    monkeypatch.setattr(processing, "_DOWNLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(processing, "_assert_safe_url", lambda u: None)
    monkeypatch.setattr(processing, "_schedule_cleanup", lambda p: None)
    # No network: no proxy from the pool / env, no cookie pool.
    import app.core.proxy_pool as pp
    from app.services import downloader
    monkeypatch.setattr(pp, "get_proxy_from_pool", lambda platform: None)
    monkeypatch.setattr(downloader, "get_proxy_config_for_phase", lambda u, phase="metadata": None)
    return processing


def _fake_ydl(captured, ext, body):
    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            with open(f"{self.opts['outtmpl']}.ja.{ext}", "w", encoding="utf-8") as f:
                f.write(body)
            return {"id": "x", "requested_subtitles": {"ja": {"ext": ext}}}
    return FakeYDL


def _call(proc, **kw):
    payload = proc.SubtitleRequest(source_url="https://example.com/v", **kw)
    return asyncio.run(proc.download_subtitle.__wrapped__(payload, None))


class TestLangCodes:
    def test_concrete_code_is_used_as_is(self, proc):
        assert proc._subtitle_langs_for("ja") == ["ja"]
        assert proc._subtitle_langs_for("zh-Hans") == ["zh-Hans"]
        assert proc._subtitle_langs_for("en-orig") == ["en-orig"]

    def test_presets_unchanged(self, proc):
        assert proc._subtitle_langs_for("vi") == ["vi", "vi-VN", "vi-VIE"]
        assert proc._subtitle_langs_for("auto")[0] == "vi"
        assert proc._subtitle_langs_for("all")  # falls back to default list

    @pytest.mark.parametrize("bad", [".*", "en|ja", "a" * 40, "en;rm", "../x", ""])
    def test_regex_or_junk_falls_back_to_auto(self, proc, bad):
        assert proc._subtitle_langs_for(bad) == proc._subtitle_langs_for("auto")


class TestVttToSrt:
    def test_converts_and_drops_cue_settings(self, proc, tmp_path):
        v, s = tmp_path / "a.vtt", tmp_path / "a.srt"
        v.write_text(VTT, encoding="utf-8")
        assert proc._vtt_file_to_srt(str(v), str(s)) is True
        out = s.read_text(encoding="utf-8")
        assert "00:00:01,000 --> 00:00:03,500" in out
        assert "align:" not in out and "WEBVTT" not in out

    def test_empty_vtt_returns_false(self, proc, tmp_path):
        v = tmp_path / "e.vtt"
        v.write_text("WEBVTT\n\n", encoding="utf-8")
        assert proc._vtt_file_to_srt(str(v), str(tmp_path / "e.srt")) is False


class TestEndpoint:
    def test_srt_only_source_is_returned_as_is(self, proc, monkeypatch):
        import yt_dlp
        cap = {}
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _fake_ydl(cap, "srt", "1\n00:00:01,000 --> 00:00:02,000\nhi\n\n"))
        r = _call(proc, language="ja")
        assert r["success"] is True
        assert cap["subtitleslangs"] == ["ja"]
        assert r["output_path"].endswith(".srt")

    def test_vtt_only_source_is_converted_to_srt(self, proc, monkeypatch):
        import yt_dlp
        cap = {}
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _fake_ydl(cap, "vtt", VTT))
        r = _call(proc, language="ja", filename="clip")
        assert r["success"] is True
        assert cap["subtitlesformat"] == "srt/vtt/best"
        assert r["output_path"].endswith(".srt")
        assert "00:00:04,000 --> 00:00:06,000" in open(r["output_path"], encoding="utf-8").read()
        assert "clip.srt" in r["download_url"]
        # the intermediate .vtt is gone
        assert not [f for f in os.listdir(os.path.dirname(r["output_path"])) if f.endswith(".vtt")]

    def test_no_track_means_no_subtitles(self, proc, monkeypatch):
        import json
        import yt_dlp

        class Empty:
            def __init__(self, o): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def extract_info(self, u, download=True):
                return {"id": "x", "requested_subtitles": None}
        monkeypatch.setattr(yt_dlp, "YoutubeDL", Empty)
        r = _call(proc, language="ja")
        assert r.status_code == 404
        body = json.loads(r.body)
        assert body["success"] is False and body["error"] == "no_subtitles"
        assert body["error_code"] == "subtitle_language_unavailable"
        assert "'ja'" in body["message"]
