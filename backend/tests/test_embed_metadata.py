"""Quality #6 (27/10/2026): title / artist / date / link / chapters / cover are
written into the file after the download (one ffmpeg stream copy). Never fails
the download: an ffmpeg error leaves the file exactly as it was."""
import os
import shutil
import subprocess

import pytest

import app.services.downloader as d

INFO = {"title": "Đường về nhà đẹp", "uploader": "Ca sĩ Đ", "upload_date": "20261027",
        "webpage_url": "https://www.youtube.com/watch?v=x", "thumbnail": "https://i.ytimg.com/vi/x/hq.jpg",
        "chapters": [{"start_time": 0, "end_time": 1.5, "title": "Mở đầu"},
                     {"start_time": 1.5, "end_time": 3, "title": "Phần #2"}]}


def test_ffmetadata_escapes_and_orders_chapters():
    txt = d._ffmetadata({"title": "a=b;c#d", "artist": "", "date": "2026-10-27"}, INFO["chapters"])
    assert txt.startswith(";FFMETADATA1\ntitle=a\\=b\;c\\#d\ndate=2026-10-27\n")
    assert "artist=" not in txt
    assert "START=1500\nEND=3000\ntitle=Phần \\#2" in txt


class _Run:
    def __init__(self, rc=0, write=True):
        self.rc, self.write, self.cmds = rc, write, []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        if cmd[0] == "ffmpeg" and self.write:
            with open(cmd[-1], "wb") as fh:
                fh.write(b"\1" * 2000)
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "0\n", "")
        return subprocess.CompletedProcess(cmd, self.rc, b"", b"")


def _file(tmp_path, ext):
    p = tmp_path / f"v{ext}"
    p.write_bytes(b"\0" * 2000)
    return str(p)


def test_mp4_gets_tags_chapters_and_cover(tmp_path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(d, "_fetch_cover", lambda url: b"\xff\xd8jpeg")
    p = _file(tmp_path, ".mp4")
    assert d.embed_media_metadata(p, INFO) == {"title": True, "artist": True, "cover": True, "chapters": 2}
    cmd = next(c for c in run.cmds if c[0] == "ffmpeg")
    assert cmd[cmd.index("-map_metadata") + 1] == "1" and cmd[cmd.index("-map_chapters") + 1] == "1"
    assert "attached_pic" in cmd and "mjpeg" in cmd and "-c" in cmd
    assert open(p, "rb").read() == b"\1" * 2000            # replaced by the tagged file


def test_mp3_is_id3v23_and_has_no_chapters(tmp_path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(d, "_fetch_cover", lambda url: b"img")
    out = d.embed_media_metadata(_file(tmp_path, ".mp3"), INFO)
    cmd = next(c for c in run.cmds if c[0] == "ffmpeg")
    assert out["chapters"] == 0 and cmd[cmd.index("-id3v2_version") + 1] == "3"
    assert cmd[cmd.index("-map") + 1] == "0:a"


def test_opus_gets_tags_but_no_cover(tmp_path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    fetched = []
    monkeypatch.setattr(d, "_fetch_cover", lambda url: fetched.append(url) or b"img")
    out = d.embed_media_metadata(_file(tmp_path, ".opus"), INFO)
    assert out["cover"] is False and fetched == []


def test_ffmpeg_failure_keeps_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _Run(rc=1))
    monkeypatch.setattr(d, "_fetch_cover", lambda url: None)
    p = _file(tmp_path, ".mp4")
    assert d.embed_media_metadata(p, INFO) == {}
    assert open(p, "rb").read() == b"\0" * 2000


def test_truncated_output_is_refused(tmp_path, monkeypatch):
    class Short(_Run):
        def __call__(self, cmd, **kw):
            r = super().__call__(cmd, **kw)
            if cmd[0] == "ffmpeg":
                open(cmd[-1], "wb").write(b"x")
            return r
    monkeypatch.setattr(subprocess, "run", Short())
    monkeypatch.setattr(d, "_fetch_cover", lambda url: None)
    p = _file(tmp_path, ".mp4")
    assert d.embed_media_metadata(p, INFO) == {} and os.path.getsize(p) == 2000


def test_switch_off(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBED_METADATA", "0")
    run = _Run()
    monkeypatch.setattr(subprocess, "run", run)
    assert d.embed_media_metadata(_file(tmp_path, ".mp4"), INFO) == {} and run.cmds == []


def test_cover_fetch_is_ssrf_guarded(monkeypatch):
    import app.core.ssrf_guard as g

    def boom(url, **k):
        raise ValueError("blocked")
    monkeypatch.setattr(g, "assert_safe_url", boom)
    assert d._fetch_cover("http://169.254.169.254/latest") is None


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_real_mp4_tags_and_chapters(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "_fetch_cover", lambda url: None)
    p = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10", "-t", "3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
    assert d.embed_media_metadata(str(p), INFO)["chapters"] == 2
    import json
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_chapters", "-of", "json", str(p)],
                                  capture_output=True, text=True).stdout)
    assert j["format"]["tags"]["title"] == "Đường về nhà đẹp" and len(j["chapters"]) == 2
