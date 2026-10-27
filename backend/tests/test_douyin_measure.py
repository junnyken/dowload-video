"""Douyin server copies get tags + measured media like every other file (27/10, live find)."""
from app.services import downloader as d


def _patch(monkeypatch, media, tags=None):
    seen = {}
    monkeypatch.setattr(d, "embed_media_metadata", lambda p, info: seen.update(info=info) or (tags or {}))
    monkeypatch.setattr(d, "probe_media_file", lambda p: dict(media))
    return seen


def test_measured_douyin_file_fills_the_result(tmp_path, monkeypatch):
    f = tmp_path / "douyin_x.mp4"
    f.write_bytes(b"\0" * 2048)
    seen = _patch(monkeypatch, {"measured": True, "vcodec": "h264", "short_side": 1080},
                  {"title": True, "artist": False, "cover": True, "chapters": 0})
    r = {"title": "今天吃玉米粑粑", "thumbnail_url": "https://p3/c.jpg",
         "original_url": "https://www.douyin.com/video/7700461185568827002"}
    d._douyin_measure(r, str(f), "https://www.douyin.com/video/7700461185568827002")
    assert seen["info"] == {"title": "今天吃玉米粑粑", "uploader": None,
                            "webpage_url": "https://www.douyin.com/video/7700461185568827002",
                            "thumbnail": "https://p3/c.jpg"}
    assert r["downloaded_media"]["tags"]["cover"] is True
    assert (r["downloaded_height"], r["downloaded_vcodec"], r["downloaded_universal"]) == (1080, "avc1", True)


def test_h265_file_is_not_universal(tmp_path, monkeypatch):
    f = tmp_path / "douyin_y.mp4"
    f.write_bytes(b"\0")
    _patch(monkeypatch, {"measured": True, "vcodec": "hevc", "short_side": 1080})
    r = {"title": "t"}
    d._douyin_measure(r, str(f), "https://www.douyin.com/video/1")
    assert r["downloaded_universal"] is False and "tags" not in r["downloaded_media"]


def test_unmeasured_file_leaves_height_fields_alone(tmp_path, monkeypatch):
    f = tmp_path / "douyin_z.mp4"
    f.write_bytes(b"\0")
    _patch(monkeypatch, {"measured": False})
    r = {"title": "t"}
    d._douyin_measure(r, str(f), "https://www.douyin.com/video/1")
    assert r["downloaded_media"] == {"measured": False} and "downloaded_height" not in r


def test_douyin_download_path_calls_it():
    import inspect
    src = inspect.getsource(d._extract_video_info_impl)
    assert "_douyin_measure(result, local_path, douyin_input)" in src
