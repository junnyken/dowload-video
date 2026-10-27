"""Quality #3 (27/10/2026): what a downloaded file really is, measured with
ffprobe — not what yt-dlp said it would merge (the 4K request that delivered
1080p). parse_media_json never raises and never claims an unmeasured value."""
import json
import os
import shutil
import subprocess

import pytest

from app.services.format_probe import parse_media_json, vcodec_tag


def probe(*streams, fmt=None):
    return json.dumps({"streams": list(streams), "format": fmt or {}})


H264 = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
        "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001", "color_transfer": "bt709"}
VP9_60 = {"codec_type": "video", "codec_name": "vp9", "width": 3840, "height": 2160,
          "avg_frame_rate": "60/1", "r_frame_rate": "60/1"}
HEVC_HDR = {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160,
            "avg_frame_rate": "24/1", "color_transfer": "smpte2084"}
AAC = {"codec_type": "audio", "codec_name": "aac", "bit_rate": "128000", "sample_rate": "44100", "channels": 2}
OPUS = {"codec_type": "audio", "codec_name": "opus", "sample_rate": "48000", "channels": 2}
COVER = {"codec_type": "video", "codec_name": "mjpeg", "width": 600, "height": 600,
         "disposition": {"attached_pic": 1}}


def test_h264_sdr_with_size_based_bitrate():
    m = parse_media_json(probe(H264, AAC, fmt={"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "10.0"}),
                         size_bytes=10_000_000)
    assert m == {"measured": True, "container": "mov", "duration": 10.0, "size_bytes": 10_000_000,
                 "bitrate_kbps": 8000, "width": 1920, "height": 1080, "short_side": 1080, "vcodec": "h264",
                 "fps": 29.97, "hdr": False, "color_transfer": "bt709", "acodec": "aac",
                 "audio_bitrate_kbps": 128, "sample_rate": 44100, "channels": 2}


def test_vp9_4k_60fps():
    m = parse_media_json(probe(VP9_60, OPUS, fmt={"duration": "2", "bit_rate": "12000000"}))
    assert (m["short_side"], m["vcodec"], m["fps"], m["bitrate_kbps"], m["hdr"]) == (2160, "vp9", 60.0, 12000, False)


def test_hdr_by_transfer_or_mastering_display():
    assert parse_media_json(probe(HEVC_HDR))["hdr"] is True
    md = dict(H264, color_transfer=None, side_data_list=[{"side_data_type": "Mastering display metadata"}])
    assert parse_media_json(probe(md))["hdr"] is True


def test_portrait_short_side():
    m = parse_media_json(probe(dict(H264, width=1080, height=1920)))
    assert m["short_side"] == 1080 and m["height"] == 1920


def test_audio_only_and_cover_art_is_not_video():
    m = parse_media_json(probe(COVER, AAC, fmt={"duration": "180"}))
    assert m["measured"] is True and m["vcodec"] is None and m["width"] is None and m["acodec"] == "aac"


@pytest.mark.parametrize("raw", ["", "not json", json.dumps({"streams": []}), None])
def test_unreadable_claims_nothing(raw):
    assert parse_media_json(raw) == {"measured": False}


def test_bad_frame_rate_is_unknown():
    assert parse_media_json(probe(dict(H264, avg_frame_rate="0/0", r_frame_rate="0/0")))["fps"] is None


def test_vcodec_tag():
    assert [vcodec_tag(c) for c in ("h264", "vp9", "av1", "hevc", "mpeg4", None)] == ["avc1", "vp09", "av01", "hvc1", "mpeg4", ""]


def test_downloader_overrides_reported_fields_with_measured(monkeypatch, tmp_path):
    """The block in download_video: measured short side / codec replace yt-dlp's claim."""
    import app.services.downloader as d
    f = tmp_path / "x.mp4"
    f.write_bytes(b"\0" * 100)
    monkeypatch.setattr(d, "probe_media_file", lambda p: parse_media_json(probe(H264)))
    media = d.probe_media_file(str(f))
    assert media["short_side"] == 1080 and vcodec_tag(media["vcodec"]) == "avc1"


FFPROBE = shutil.which("ffprobe")
FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(not (FFPROBE and FFMPEG), reason="needs ffmpeg + ffprobe")
@pytest.mark.parametrize("args,want", [
    (["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p"],
     {"vcodec": "h264", "short_side": 180, "fps": 30.0, "hdr": False}),
    (["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60", "-t", "1", "-c:v", "libvpx-vp9", "-b:v", "200k"],
     {"vcodec": "vp9", "short_side": 180, "fps": 60.0, "hdr": False}),
    (["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30", "-t", "1", "-c:v", "libvpx-vp9", "-b:v", "200k",
      "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"],
     {"vcodec": "vp9", "hdr": True}),
    (["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", "-b:a", "128k"],
     {"vcodec": None, "acodec": "aac"}),
])
def test_real_files(tmp_path, args, want):
    from app.services.downloader import probe_media_file
    ext = ".webm" if "libvpx-vp9" in args else ".mp4" if "libx264" in args else ".m4a"
    out = tmp_path / f"f{ext}"
    subprocess.run([FFMPEG, "-v", "error", "-y", *args, str(out)], check=True, timeout=60)
    m = probe_media_file(str(out))
    assert m["measured"] is True
    assert {k: m[k] for k in want} == want
    assert m["size_bytes"] == os.path.getsize(out)


@pytest.mark.skipif(not FFPROBE, reason="needs ffprobe")
def test_broken_file_is_not_measured(tmp_path):
    from app.services.downloader import probe_media_file
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video")
    assert probe_media_file(str(bad)) == {"measured": False}
