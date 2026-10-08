"""
Task #6127 — make pool cookies last longer and spend them less.

A1  COOKIE_LAST_PLATFORMS: Facebook / Instagram / X try anonymously first; a
    pool cookie is spent only when that fails, and never on an answer no
    cookie can change (video gone).
B1  The session the platform refreshed (yt-dlp re-saves its jar into the
    cookie file after each use) is written back to the pool — only while
    every login cookie is still there — keeping the cookie's identity.
B2  A cookie is picked per request (not per worker process), kept for the
    whole request, with a soft per-account daily cap; X failures rotate too.
B3  Daily re-test of every pooled cookie the probes support.
fakeredis + a stub YoutubeDL; no network.
"""
from __future__ import annotations

import base64
import os
import time

import pytest

fakeredis = pytest.importorskip("fakeredis")

from app.core import cookie_pool as cp  # noqa: E402
from app.services import downloader  # noqa: E402

FB_URL = "https://www.facebook.com/reel/1608261137553863"
FUTURE = int(time.time()) + 200 * 86400


def jar(*extra: tuple[str, str], logged_in: bool = True, domain: str = ".facebook.com") -> str:
    rows = [("datr", "d1")]
    if logged_in:
        rows += [("c_user", "100000000000001"), ("xs", "xs-secret"), ("fr", "fr1")]
    rows += list(extra)
    lines = ["# Netscape HTTP Cookie File"]
    lines += [f"{domain}\tTRUE\t/\tTRUE\t{FUTURE}\t{n}\t{v}" for n, v in rows]
    return "\n".join(lines) + "\n"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def rc(monkeypatch):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    monkeypatch.setattr(downloader, "_FACEBOOK_COOKIES_B64", "", raising=False)
    monkeypatch.setattr(downloader, "_COOKIE_DIR", str(_tmpdir()))
    for k in ("COOKIE_LAST_PLATFORMS", "COOKIE_DAILY_CAP"):
        monkeypatch.delenv(k, raising=False)
    downloader._reset_request_cookies()
    yield r
    downloader._reset_request_cookies()


_TMP = None


def _tmpdir():
    import tempfile
    global _TMP
    _TMP = tempfile.mkdtemp(prefix="vgck_")
    return _TMP


def add(platform="facebook", text=None, label="acc") -> str:
    v = b64(text or jar())
    cp.add_cookie(platform, v, label=label)
    return v


# ── B2: soft daily cap ──────────────────────────────────────────────────────

def test_daily_cap_prefers_an_account_under_its_cap_but_never_refuses(rc, monkeypatch):
    monkeypatch.setenv("COOKIE_DAILY_CAP", "facebook=2")
    a = add(text=jar(("a", "1")), label="A")
    b = add(text=jar(("b", "1")), label="B")
    rc.set(cp._uses_key("facebook", cp._hash(a)), 2)          # A at its cap, and least recently used
    assert cp.get_cookie_from_pool("facebook") == b
    assert cp.uses_today("facebook", b) == 1
    rc.set(cp._uses_key("facebook", cp._hash(b)), 5)
    rc.delete(f"cookie_cooldown:facebook:{cp._hash(a)}", f"cookie_cooldown:facebook:{cp._hash(b)}")
    assert cp.get_cookie_from_pool("facebook") in (a, b)     # all over the cap: still a cookie


def test_cap_parsing():
    assert cp.daily_cap("instagram") == 40
    os.environ["COOKIE_DAILY_CAP"] = "instagram=0, reddit=7"
    try:
        assert cp.daily_cap("instagram") is None and cp.daily_cap("reddit") == 7
    finally:
        del os.environ["COOKIE_DAILY_CAP"]
    assert cp.daily_cap("pinterest") is None


# ── B2: per request, sticky within it ───────────────────────────────────────

def test_one_pick_per_request_and_rotation_between_requests(rc):
    a = add(text=jar(("a", "1")), label="A")
    b = add(text=jar(("b", "1")), label="B")
    p1 = downloader._get_facebook_cookies_file()
    assert downloader._get_facebook_cookies_file() == p1           # same request: same account
    first = downloader._active_cookie("facebook")
    assert cp.uses_today("facebook", first) == 1                   # counted once, not per layer
    downloader._reset_request_cookies()                            # next download
    downloader._get_facebook_cookies_file()
    assert downloader._active_cookie("facebook") == ({a, b} - {first}).pop()
    assert oct(os.stat(p1).st_mode & 0o777) == "0o600"


def test_rotation_blocks_the_request_cookie_and_picks_another(rc):
    add(text=jar(("a", "1")), label="A")
    add(text=jar(("b", "1")), label="B")
    downloader._get_facebook_cookies_file()
    bad = downloader._active_cookie("facebook")
    downloader._rotate_cookie("facebook", hard=True)
    assert rc.get(f"cookie_health:facebook:{cp._hash(bad)}") == "hard"
    downloader._get_facebook_cookies_file()
    assert downloader._active_cookie("facebook") != bad


# ── B1: refreshed session written back ──────────────────────────────────────

def test_refreshed_session_rules():
    old = jar()
    assert cp.refreshed_session_ok("facebook", old, jar(("presence", "p2")))
    assert not cp.refreshed_session_ok("facebook", old, jar(logged_in=False))          # logged out
    no_datr = "\n".join(l for l in jar(("presence", "p2")).splitlines() if "\tdatr\t" not in l) + "\n"
    assert not cp.refreshed_session_ok("facebook", old, no_datr)                      # half-written / lost a cookie
    assert not cp.refreshed_session_ok("facebook", old, "# saved by yt-dlp\n" + old)   # nothing new
    reordered = "\n".join(reversed(old.strip().splitlines())) + "\n"
    assert not cp.refreshed_session_ok("facebook", old, reordered)
    assert not cp.refreshed_session_ok("facebook", jar(logged_in=False), jar(("x", "1"), logged_in=False))


def test_harvest_saves_the_refreshed_jar_and_keeps_identity(rc):
    v = add(label="tai-khoan-1")
    rc.set(f"cookie_lastused:facebook:{cp._hash(v)}", 123)
    path = downloader._get_facebook_cookies_file()
    with open(path, "w", encoding="utf-8") as f:                    # what yt-dlp leaves after a download
        f.write("# Netscape HTTP Cookie File\n# This file is generated by yt-dlp.\n" + jar(("presence", "new")))
    downloader._reset_request_cookies()
    new_path = downloader._get_facebook_cookies_file()
    pool = rc.lrange("cookie_pool:facebook", 0, -1)
    assert len(pool) == 1 and pool[0] != v
    saved = base64.b64decode(pool[0]).decode()
    assert "presence\tnew" in saved and "xs\txs-secret" in saved
    meta = cp._get_meta(rc, "facebook", cp._hash(pool[0]))
    assert meta["label"] == "tai-khoan-1" and meta["refresh_count"] == 1
    assert rc.get(f"cookie_meta:facebook:{cp._hash(v)}") is None
    assert downloader._active_cookie("facebook") == pool[0] and new_path != path
    assert not os.path.exists(path)


def test_harvest_never_saves_a_logged_out_jar(rc):
    v = add()
    path = downloader._get_facebook_cookies_file()
    with open(path, "w", encoding="utf-8") as f:
        f.write(jar(("presence", "new"), logged_in=False))
    downloader._reset_request_cookies()
    downloader._get_facebook_cookies_file()
    assert rc.lrange("cookie_pool:facebook", 0, -1) == [v]


def test_write_back_is_rate_limited_per_account(rc):
    v = add()
    v2 = b64(jar(("presence", "2")))
    assert cp.replace_cookie_content("facebook", v, v2)
    assert not cp.replace_cookie_content("facebook", v2, b64(jar(("presence", "3"))))   # within 10 min
    assert not cp.replace_cookie_content("facebook", "gone", b64(jar(("p", "4"))))


def test_replace_moves_health_and_usage(rc):
    v = add()
    h = cp._hash(v)
    rc.setex(f"cookie_health:facebook:{h}", 900, "soft")
    rc.set(cp._uses_key("facebook", h), 7)
    v2 = b64(jar(("presence", "2")))
    assert cp.replace_cookie_content("facebook", v, v2)
    h2 = cp._hash(v2)
    assert rc.get(f"cookie_health:facebook:{h2}") == "soft" and 0 < rc.ttl(f"cookie_health:facebook:{h2}") <= 900
    assert cp.uses_today("facebook", v2) == 7
    assert rc.get(f"cookie_health:facebook:{h}") is None


# ── A1: cookie last ─────────────────────────────────────────────────────────

def test_flag_parsing_and_base_opts(rc, monkeypatch):
    add()
    assert downloader._get_base_opts(FB_URL).get("cookiefile")                 # default: cookie first
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook, X")
    assert downloader.cookie_last("twitter") and not downloader.cookie_last("youtube")
    assert not downloader._get_base_opts(FB_URL).get("cookiefile")
    assert downloader._get_base_opts(FB_URL, force_cookie=True).get("cookiefile")
    assert not downloader.cookie_retry_worthwhile("ERROR: [facebook] 1: Video unavailable")
    assert downloader.cookie_retry_worthwhile("ERROR: [facebook] 1: login required")


class _StubYDL:
    """yt-dlp under ignoreerrors: failure = logger.error + None."""
    calls: list = []
    LOGIN_WALL = "ERROR: [facebook] 1608261137553863: This video is only available for registered users"
    anon_error = LOGIN_WALL
    anon_raises = False
    cookie_error = None

    def __init__(self, opts):
        self.opts = opts
        type(self).calls.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        log = self.opts.get("logger")
        if self.opts.get("cookiefile"):
            if self.cookie_error:
                log and log.error(self.cookie_error)
                return None
            return {"id": "1608261137553863", "title": "reel", "ext": "mp4",
                    "url": "https://video.xx.fbcdn.net/v/reel.mp4",
                    "formats": [{"format_id": "hd", "url": "https://video.xx.fbcdn.net/v/reel.mp4", "ext": "mp4"}]}
        if self.anon_error:
            if self.anon_raises:
                raise Exception(self.anon_error)
            log and log.error(self.anon_error)
            return None
        return {"id": "1608261137553863", "title": "public reel", "ext": "mp4",
                "url": "https://video.xx.fbcdn.net/v/pub.mp4",
                "formats": [{"format_id": "sd", "url": "https://video.xx.fbcdn.net/v/pub.mp4", "ext": "mp4"}]}


@pytest.fixture
def stub(rc, monkeypatch):
    _StubYDL.calls = []
    _StubYDL.anon_error = _StubYDL.LOGIN_WALL
    _StubYDL.cookie_error = None
    _StubYDL.anon_raises = False
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _StubYDL)
    monkeypatch.setattr(downloader, "is_cobalt_available", lambda: False)
    monkeypatch.setattr(downloader, "_impersonate_target", lambda: None)
    return _StubYDL


def run():
    return downloader._extract_video_info_impl(FB_URL, quality="video_fast")


def test_flag_off_keeps_cookie_first(stub):
    v = add()
    stub.anon_error = None
    run()
    assert stub.calls[0].get("cookiefile")
    assert cp.uses_today("facebook", v) == 1


def test_public_video_spends_no_cookie(stub, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    v = add()
    stub.anon_error = None
    out = run()
    assert out and all(not o.get("cookiefile") for o in stub.calls)
    assert cp.uses_today("facebook", v) == 0


def test_login_wall_retries_once_with_the_cookie(stub, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    v = add()
    out = run()
    assert out and out.get("title") == "reel"
    assert [bool(o.get("cookiefile")) for o in stub.calls][:2] == [False, True]
    assert sum(bool(o.get("cookiefile")) for o in stub.calls) == 1
    assert cp.uses_today("facebook", v) == 1


def test_a_gone_video_never_spends_a_cookie(stub, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    v = add()
    stub.anon_error = "ERROR: [facebook] 1608261137553863: Video unavailable"
    with pytest.raises(ValueError):
        run()
    assert all(not o.get("cookiefile") for o in stub.calls)
    assert cp.uses_today("facebook", v) == 0


def test_cookie_retry_hitting_a_checkpoint_blocks_that_cookie(stub, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    v = add()
    stub.cookie_error = "ERROR: [facebook] 1: checkpoint required"
    with pytest.raises(ValueError):
        run()
    assert rc_get_health(v) == "hard"


def test_anonymous_failure_does_not_blame_a_cookie_it_never_used(stub, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    v = add()
    stub.anon_error = "ERROR: [facebook] 1: HTTP Error 429: Too Many Requests"
    stub.anon_raises = True        # the except branch, where rotation lives
    stub.cookie_error = "ERROR: [facebook] 1: Cannot parse data"
    with pytest.raises(ValueError):
        run()
    assert rc_get_health(v) is None


def rc_get_health(v):
    from app.core.redis_client import get_redis
    return get_redis().get(f"cookie_health:facebook:{cp._hash(v)}")


# ── B3: daily re-test ───────────────────────────────────────────────────────

def test_daily_retest_marks_dead_cookies_and_alerts(rc, monkeypatch):
    from app.core import cookie_probe
    from app.tasks import video_tasks
    alive = add("tiktok", jar(domain=".tiktok.com"), label="song")
    dead = add("tiktok", jar(("x", "1"), domain=".tiktok.com"), label="chet")
    add("tiktok", jar(("y", "1"), domain=".tiktok.com"), label="tat")
    cp.mark_cookie_disabled("tiktok", rc.lrange("cookie_pool:tiktok", 0, -1)[2])
    monkeypatch.setitem(cookie_probe.PROBES, "tiktok",
                        lambda c, proxy: ("rejected", "đăng xuất") if c == dead else ("ok", "ok"))
    monkeypatch.setattr(cookie_probe, "PER_PLATFORM_GAP_MS", 1)
    monkeypatch.setattr("time.sleep", lambda s: None)
    sent = []
    monkeypatch.setattr("app.core.notifications.send_telegram_message_sync", lambda m: sent.append(m))
    out = video_tasks.retest_cookie_pool_daily.run()
    assert out["tested"] == 2 and out["dead"] == 1
    assert rc.get(f"cookie_health:tiktok:{cp._hash(dead)}") == "expired"
    assert rc.get(f"cookie_health:tiktok:{cp._hash(alive)}") is None
    assert len(sent) == 1 and "chet" in sent[0] and "song" not in sent[0]


def test_expiry_check_covers_every_platform_with_cookies(rc, monkeypatch):
    add("reddit", jar(domain=".reddit.com"))
    add("facebook")
    assert cp.pooled_platforms() == ["facebook", "reddit"]


# ── Cobalt before the cookie, outcome stats, fast-failing anonymous attempt ──

def stats(rc):
    import time as _tm
    return rc.hgetall(f"cookie_last:stats:{_tm.strftime('%Y-%m-%d', _tm.gmtime())}")


def test_cobalt_serves_before_any_cookie_is_spent(stub, rc, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    v = add()
    monkeypatch.setattr(downloader, "is_cobalt_available", lambda: True)
    got = []
    monkeypatch.setattr(downloader, "download_social_via_cobalt",
                        lambda url, d, p: got.append(p) or {"url": None, "title": "via cobalt", "ext": "mp4",
                                                            "id": "c1", "extractor": "cobalt_facebook",
                                                            "filepath": __file__})
    run()
    assert got == ["facebook"]
    assert all(not o.get("cookiefile") for o in stub.calls)
    assert cp.uses_today("facebook", v) == 0
    assert stats(rc) == {"facebook|cobalt_ok": "1"}


def test_stats_per_step(stub, rc, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    add()
    run()                                                   # login wall → cookie
    stub.anon_error = None
    run()                                                   # public → anonymous
    stub.anon_error = "ERROR: [facebook] 1: Video unavailable"
    with pytest.raises(ValueError):
        run()                                               # gone → nothing tried
    assert stats(rc) == {"facebook|cookie_ok": "1", "facebook|anon_ok": "1", "facebook|gone": "1"}


def test_all_fail_is_counted_once(stub, rc, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    add()
    stub.cookie_error = "ERROR: [facebook] 1: Cannot parse data"
    with pytest.raises(ValueError):
        run()
    assert stats(rc) == {"facebook|all_fail": "1"}


def test_anonymous_metadata_attempt_fails_fast(rc, monkeypatch):
    add("instagram", jar(("ds_user_id", "1"), ("sessionid", "s"), ("csrftoken", "c"), domain=".instagram.com"))
    ig = "https://www.instagram.com/reel/DFQe23tOWKz/"
    slow = downloader._get_base_opts(ig, phase="metadata")
    assert slow.get("cookiefile") and slow.get("retries") == 2          # cookie-first: unchanged
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "instagram")
    fast = downloader._get_base_opts(ig, phase="metadata")
    assert not fast.get("cookiefile") and fast["retries"] == 0 and fast["socket_timeout"] <= 10
    assert downloader._get_base_opts(ig, phase="download")["retries"] == 2   # bytes keep retries
    assert downloader._get_base_opts(ig, phase="metadata", force_cookie=True)["retries"] == 2


def test_cobalt_api_key_is_sent_when_configured(monkeypatch):
    from app.services import cobalt_service as cs
    sent = []

    class R:
        def json(self):
            return {"status": "error", "error": {"code": "x"}}

    monkeypatch.setattr(cs, "_healthy_cobalt_instances", lambda: ["http://cobalt.test/"])
    monkeypatch.setattr(cs.httpx, "post", lambda url, json, headers, timeout: sent.append(headers) or R())
    monkeypatch.delenv("COBALT_API_KEY", raising=False)
    cs.fetch_cobalt_stream("https://x.com/a/status/1")
    monkeypatch.setenv("COBALT_API_KEY", "k-123")
    cs.fetch_cobalt_stream("https://x.com/a/status/1")
    assert "Authorization" not in sent[0] and sent[1]["Authorization"] == "Api-Key k-123"


@pytest.fixture
def media(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    jpg = tmp_path / "cover.mp4"           # what Cobalt handed back for a reel: a JPEG named .mp4
    vid = tmp_path / "real.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64", "-frames:v", "1",
                    "-f", "image2", "-c:v", "mjpeg", str(jpg)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=64x64:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(vid)], check=True)
    return jpg, vid


def test_an_image_is_not_a_video(media):
    from app.services.cobalt_service import is_real_video
    jpg, vid = media
    assert not is_real_video(str(jpg)) and is_real_video(str(vid))
    assert not is_real_video(str(jpg.parent / "missing.mp4"))


def test_cobalt_image_answer_is_not_served(media, monkeypatch):
    from app.services import cobalt_service as cs
    jpg, vid = media
    monkeypatch.setattr(cs, "_download_social_via_cobalt",
                        lambda u, d, p: {"filepath": str(jpg), "extractor": "cobalt_instagram"})
    assert cs.download_social_via_cobalt("https://www.instagram.com/reel/x/", "/tmp", "instagram") is None
    assert not jpg.exists()
    monkeypatch.setattr(cs, "_download_social_via_cobalt",
                        lambda u, d, p: {"filepath": str(vid), "extractor": "cobalt_instagram"})
    assert cs.download_social_via_cobalt("https://www.instagram.com/reel/x/", "/tmp", "instagram")


def test_sabr_shortfall_compares_with_what_the_video_offers():
    f = lambda *hs: {"formats": [{"vcodec": "avc1", "height": h} for h in hs] + [{"vcodec": "none", "height": None}]}
    assert not downloader.sabr_shortfall(f(144, 240), 240, 4320)        # 240p-only video: nothing missing
    assert downloader.sabr_shortfall(f(240, 720, 1080), 240, 1080)      # SABR cut it to 240p
    assert not downloader.sabr_shortfall(f(240, 720, 1080), 1080, 4320)
    assert downloader.sabr_shortfall({}, 0, 1080)                       # nothing downloaded
    assert downloader.sabr_shortfall({}, 240, 1080)                     # no format list: old rule


# ── default HD: portrait capped by its short side; Cobalt-first ──────────────

def _pick(formats):
    """formats worst → best, the order every yt-dlp extractor hands over."""
    import yt_dlp
    fmt = downloader._get_base_opts("https://example.com/v")["format"]
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        sel = ydl.build_format_selector(fmt)
        ctx = {"formats": formats, "incomplete_formats": False, "has_merged_format": True}
        out = list(sel(ctx))
    return out[0]["format_id"] if out else None


def _f(fid, w, h, v="none", a="none", ext="mp4", **kw):
    return {"format_id": fid, "width": w, "height": h, "vcodec": v, "acodec": a, "ext": ext,
            "url": f"https://cdn.test/{fid}", "protocol": "https", **kw}


def test_default_hd_takes_the_1080p_portrait_stream():
    # X: progressive formats with no codec info (measured 08/10: 480x854 was picked over 716x1276)
    x = [_f("http-632", 320, 570, v=None, a=None), _f("http-950", 480, 854, v=None, a=None),
         _f("http-2176", 716, 1276, v=None, a=None)]
    assert _pick(x) == "http-2176"
    # YouTube-like portrait: 1080x1920 avc1 is "1080p"
    yt = [_f("140", None, None, a="mp4a.40.2", ext="m4a"), _f("136", 720, 1280, v="avc1.4d401f"),
          _f("137", 1080, 1920, v="avc1.640028")]
    assert _pick(yt) == "137+140"
    # landscape unchanged: still capped at 1080 tall
    land = [_f("140", None, None, a="mp4a.40.2", ext="m4a"), _f("137", 1920, 1080, v="avc1.640028"),
            _f("401", 3840, 2160, v="avc1.640033")]
    assert _pick(land) == "137+140"
    # 4K portrait (2160x3840) is not "1080p"
    tall = [_f("140", None, None, a="mp4a.40.2", ext="m4a"), _f("p1080", 1080, 1920, v="avc1.640028"),
            _f("p4k", 2160, 3840, v="avc1.640033")]
    assert _pick(tall) == "p1080+140"


def test_cobalt_first_flag():
    os.environ["COBALT_FIRST_PLATFORMS"] = "instagram, X"
    try:
        assert downloader.cobalt_first("instagram", "video") and downloader.cobalt_first("twitter", "video_720")
        assert not downloader.cobalt_first("facebook", "video")
        assert not downloader.cobalt_first("instagram", "mp3_128")      # audio: not Cobalt-first
        assert not downloader.cobalt_first("instagram", "video_4k")
        assert not downloader.cobalt_first(None, "video")
    finally:
        del os.environ["COBALT_FIRST_PLATFORMS"]


def test_cobalt_first_serves_without_touching_yt_dlp(stub, rc, monkeypatch):
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "facebook")
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    v = add()
    monkeypatch.setattr(downloader, "is_cobalt_available", lambda: True)
    monkeypatch.setattr(downloader, "download_social_via_cobalt",
                        lambda url, d, p: {"url": None, "title": "via cobalt", "ext": "mp4", "id": "c1",
                                           "extractor": "cobalt_facebook", "filepath": __file__})
    out = run()
    assert out and stub.calls == []
    assert cp.uses_today("facebook", v) == 0
    assert stats(rc) == {"facebook|cobalt_first_ok": "1"}


def test_cobalt_first_miss_falls_back_and_is_not_retried(stub, rc, monkeypatch):
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "facebook")
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "facebook")
    add()
    monkeypatch.setattr(downloader, "is_cobalt_available", lambda: True)
    calls = []
    monkeypatch.setattr(downloader, "download_social_via_cobalt", lambda url, d, p: calls.append(p) or None)
    out = run()                                     # anon login wall → cookie
    assert out and calls == ["facebook"]            # Cobalt asked once, not again before the cookie
    assert stats(rc) == {"facebook|cobalt_first_miss": "1", "facebook|cookie_ok": "1"}
