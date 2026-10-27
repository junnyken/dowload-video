"""Audio qualities from one source stream get their own file (27/10, live find)."""
from app.services import downloader as d

URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_each_audio_quality_has_its_own_name():
    names = {q: d._outtmpl_for(URL, tag=q) for q in ("mp3_128", "mp3_320", "mp3_320_norm", "audio_opus")}
    assert len(set(names.values())) == 4
    assert names["mp3_320_norm"].endswith("_%(format_id)s_mp3_320_norm.%(ext)s")


def test_video_name_is_unchanged():
    assert d._outtmpl_for(URL).endswith("_%(format_id)s.%(ext)s")
    assert d._outtmpl_for(URL, tag="") == d._outtmpl_for(URL)


def test_tag_cannot_carry_path_parts():
    assert "/.." not in d._outtmpl_for(URL, tag="../../etc").split("downloads", 1)[-1]
    assert d._outtmpl_for(URL, tag="../x").endswith("_%(format_id)s_x.%(ext)s")


def test_cookie_isolation_keeps_the_tag(tmp_path):
    jar = tmp_path / "c.txt"
    jar.write_text("# cookies")
    opts = {"outtmpl": d._outtmpl_for(URL, tag="mp3_128")}
    d._isolate_outtmpl(opts, URL, str(jar))
    assert opts["outtmpl"].endswith("_%(format_id)s_mp3_128.%(ext)s")
    assert opts["outtmpl"] != d._outtmpl_for(URL, tag="mp3_128")      # cookie-specific token
    plain = {"outtmpl": d._outtmpl_for(URL)}
    d._isolate_outtmpl(plain, URL, str(jar))
    assert plain["outtmpl"].endswith("_%(format_id)s.%(ext)s")


def test_download_options_tag_audio_only():
    opts = {q: d._get_base_opts(URL, phase="download", quality=q)["outtmpl"]
            for q in ("mp3_128", "mp3_320_norm", "audio_best", "video_1080")}
    assert opts["mp3_128"].endswith("_mp3_128.%(ext)s")
    assert opts["mp3_320_norm"].endswith("_mp3_320_norm.%(ext)s")
    assert opts["audio_best"].endswith("_audio_best.%(ext)s")
    assert opts["video_1080"].endswith("_%(format_id)s.%(ext)s")
