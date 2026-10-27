"""Quality #5 (27/10/2026): MP3 128/192/256/320, the source's own audio without
re-encoding, a real Opus/Vorbis, loudness normalisation for MP3. The web's OGG
button used to send mp3_128 and audio_webm is not an audio codec yt-dlp knows."""
import pytest

from app.services.downloader import _get_base_opts, audio_postprocessing, audio_quality_valid
from tests.test_fetch_link_uuid_scope import route  # noqa: E402,F401

YT = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


@pytest.mark.parametrize("q,codec,kbps", [
    ("mp3_128", "mp3", "128"), ("mp3_192", "mp3", "192"), ("mp3_256", "mp3", "256"), ("mp3_320", "mp3", "320"),
    ("mp3", "mp3", "128"),                                   # old shorthand keeps its meaning
    ("audio_best", "best", "192"), ("audio_m4a", "m4a", "192"),
    ("audio_opus", "opus", "192"), ("audio_vorbis", "vorbis", "192"),
    ("audio_webm", "opus", "192"),                           # old name: webm audio is Opus
])
def test_postprocessor_table(q, codec, kbps):
    pps, args = audio_postprocessing(q)
    assert pps == [{"key": "FFmpegExtractAudio", "preferredcodec": codec, "preferredquality": kbps}]
    assert args == {}
    assert _get_base_opts(YT, phase="download", quality=q)["postprocessors"] == pps


def test_normalised_mp3_gets_loudnorm():
    opts = _get_base_opts(YT, phase="download", quality="mp3_320_norm")
    assert opts["postprocessors"][0]["preferredquality"] == "320"
    assert opts["postprocessor_args"] == {"extractaudio": ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]}


def test_video_has_no_audio_postprocessing():
    assert audio_postprocessing("video_1080") is None


@pytest.mark.parametrize("q,ok", [
    ("mp3_320", True), ("mp3_192_norm", True), ("audio_best", True), ("audio_webm", True), ("video_1080", True),
    ("audio_exe", False), ("audio_", False), ("audio_best_norm", False), ("audio_m4a_norm", False),
])
def test_validation(q, ok):
    assert audio_quality_valid(q) is ok


def _fetch(app, route, monkeypatch, body):
    seen = {}

    async def _ok(url, quality="video", *a, **k):
        seen["quality"] = quality
        return {"title": "t", "direct_mp4_url": "", "original_url": url}
    monkeypatch.setattr(route, "extract_video_info", _ok)
    monkeypatch.setattr(route, "check_platform_quota", lambda *a, **k: {"allowed": True})
    monkeypatch.setattr(route, "increment_usage", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.core.metering.record_download", _noop)
    return app.post("/api/v1/fetch-link", json={"url": "https://vimeo.com/123", **body}), seen


def test_route_rejects_unknown_audio(app, route, monkeypatch):
    r, seen = _fetch(app, route, monkeypatch, {"quality": "audio_exe"})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_quality" and not seen


def test_route_normalise_only_mp3(app, route, monkeypatch):
    r, seen = _fetch(app, route, monkeypatch, {"quality": "audio_best", "normalize_audio": True})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "normalize_mp3_only"


def test_route_passes_normalised_mp3(app, route, monkeypatch):
    # the stub returns no audio file, so the route then answers "processing_failed" —
    # what matters here is the quality the downloader was asked for
    r, seen = _fetch(app, route, monkeypatch, {"quality": "mp3_192", "normalize_audio": True})
    assert seen["quality"] == "mp3_192_norm"
