"""Quality #1 (27/10/2026): asking for 1440p / 2160p / 4K on YouTube must give
that resolution. The H.264-first chain settled on 1080p for all of them, since
YouTube has no H.264 above 1080p. Offline: a YouTube-shaped format list through
the real yt-dlp selector with the same format_sort the downloader uses."""
import pytest
from yt_dlp import YoutubeDL

from app.services.downloader import _get_base_opts, _high_res_prefix

YT = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def f(fid, w, h, vcodec, ext, acodec="none", tbr=1000):
    return {"format_id": fid, "url": f"http://x/{fid}", "ext": ext, "protocol": "https",
            "vcodec": vcodec, "acodec": acodec, "width": w, "height": h, "tbr": tbr}


AUDIO = [f("140", None, None, "none", "m4a", "mp4a.40.2", 128), f("251", None, None, "none", "webm", "opus", 160)]
LANDSCAPE = AUDIO + [
    f("137", 1920, 1080, "avc1.640028", "mp4", tbr=4000),
    f("248", 1920, 1080, "vp9", "webm", tbr=3000),
    f("271", 2560, 1440, "vp9", "webm", tbr=9000),
    f("313", 3840, 2160, "vp9", "webm", tbr=18000),
    f("401", 3840, 2160, "av01.0.12M.08", "mp4", tbr=16000),
]
PORTRAIT = AUDIO + [
    f("137", 1080, 1920, "avc1.640028", "mp4", tbr=4000),
    f("313", 2160, 3840, "vp9", "webm", tbr=18000),
    f("401", 2160, 3840, "av01.0.12M.08", "mp4", tbr=16000),
]
H264_ONLY = AUDIO + [f("136", 1280, 720, "avc1.4d401f", "mp4"), f("137", 1920, 1080, "avc1.640028", "mp4")]
AV1_ONLY_4K = AUDIO + [f("137", 1920, 1080, "avc1.640028", "mp4"), f("401", 3840, 2160, "av01.0.12M.08", "mp4")]


def pick(quality, formats):
    opts = _get_base_opts(YT, quality=quality)
    ydl = YoutubeDL({"quiet": True, "format": opts["format"], "format_sort": opts.get("format_sort") or []})
    r = ydl.process_ie_result({"id": "x", "title": "t", "extractor": "t", "formats": formats}, download=False)
    return [x["format_id"] for x in (r.get("requested_formats") or [r])]


@pytest.mark.parametrize("q,want", [
    ("video_1440", ["271", "140"]),
    ("video_2160", ["313", "140"]),     # VP9 before AV1 at the same height
    ("video_4k", ["313", "140"]),
])
def test_high_resolutions_are_delivered(q, want):
    assert pick(q, LANDSCAPE) == want


@pytest.mark.parametrize("q", ["video", "video_1080"])
def test_1080_and_hd_stay_h264(q):
    assert pick(q, LANDSCAPE) == ["137", "140"]


def test_portrait_short_side():
    assert pick("video_2160", PORTRAIT) == ["313", "140"]
    assert pick("video_1080", PORTRAIT) == ["137", "140"]


def test_only_h264_falls_back_to_the_old_chain():
    assert pick("video_2160", H264_ONLY) == ["137", "140"]
    assert pick("video_4k", H264_ONLY) == ["137", "140"]


def test_av1_when_it_is_the_only_4k():
    assert pick("video_2160", AV1_ONLY_4K) == ["401", "140"]


def test_no_prefix_at_or_below_1080():
    for n in (360, 720, 1080):
        assert _high_res_prefix(n) == ""
    assert "width>1080" not in _get_base_opts(YT, quality="video_1080")["format"]
    assert "width>1080" not in _get_base_opts(YT, quality="video")["format"]


@pytest.mark.parametrize("q,want", [("video_1440", True), ("video_2160", True), ("video_1080", False),
                                    ("video_720", False), ("video", False), ("video_4k", False), ("mp3_320", False)])
def test_cobalt_high_res_needs_vp9(q, want):
    from app.services.downloader import _cobalt_high_res
    assert _cobalt_high_res(q) is want


@pytest.mark.parametrize("uid,tier,env,want", [
    (None, None, {}, 720),
    ("u", "free", {}, 1080),
    ("u", "free", {"FREE_MAX_HEIGHT": "4320"}, 1080),   # production's Free cap for other platforms does not open YouTube 4K
    ("u", "pro", {}, 2160),
    ("u", "team", {}, 2160),
    ("u", "enterprise", {}, 2160),
    ("u", "pro", {"YT_MAX_HEIGHT_PRO": "1440"}, 1440),
    ("u", "free", {"YT_MAX_HEIGHT_USER": "720"}, 720),
    # production 28/10: Free set to 0 (no cap), Pro unset → Pro must not stop at 2160
    ("u", "free", {"YT_MAX_HEIGHT_USER": "0"}, 0),
    ("u", "pro", {"YT_MAX_HEIGHT_USER": "0"}, 0),
    ("u", "pro", {"YT_MAX_HEIGHT_USER": "4320", "YT_MAX_HEIGHT_PRO": "2160"}, 4320),
    ("u", "pro", {"YT_MAX_HEIGHT_PRO": "0"}, 0),
])
def test_youtube_cap_is_by_plan(monkeypatch, uid, tier, env, want):
    import app.core.quotas as q
    from app.api.routes import youtube_height_cap
    for k in ("YT_MAX_HEIGHT_PRO", "YT_MAX_HEIGHT_USER", "YT_MAX_HEIGHT_ANON", "FREE_MAX_HEIGHT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(q, "_get_tier", lambda _uid: tier)
    assert youtube_height_cap(uid) == want


@pytest.mark.parametrize("env,want", [
    ({}, (1080, 2160)),
    ({"YT_MAX_HEIGHT_USER": "0"}, (0, 0)),          # production 28/10
    ({"YT_MAX_HEIGHT_PRO": "1440"}, (1080, 1440)),
])
def test_plans_page_shows_the_paid_youtube_cap(monkeypatch, env, want):
    import fakeredis
    from app.core import plans
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(plans, "_r", lambda: r)
    for k in ("YT_MAX_HEIGHT_PRO", "YT_MAX_HEIGHT_USER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    t = {x["id"]: x for x in plans.build_plans()["tiers"]}
    assert (t["free"]["youtube_max_height"], t["pro"]["youtube_max_height"]) == want
