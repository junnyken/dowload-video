"""Admin proxy pool "all" ("Tất cả nền tảng") — fallback ONLY (owner 12/10/2026).

Owner: "nó chỉ là dự phòng thôi, nhưng đừng để cho nó lấy dung lượng video
… tốn kém rất nhiều". So: used only for ONE retry after a platform blocked
the server IP, only for the metadata phase; video bytes stay direct; YouTube
and platforms with their own pool proxy are never touched.
"""
import pytest

from app.services import downloader as d

TT = "https://www.tiktok.com/@x/video/7390205678729579807"
FB = "https://www.facebook.com/watch/?v=123"
YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
ALL = "http://u:p@all.invalid:1"


@pytest.fixture
def pools(monkeypatch):
    table = {"all": ALL}
    monkeypatch.setattr("app.core.proxy_pool.get_proxy_from_pool", lambda p: table.get(p))
    return table


@pytest.mark.parametrize("err,blocked", [
    ("ERROR: [TikTok] 123: Unable to extract webpage video data", True),
    ("HTTP Error 403: Forbidden", True),
    ("HTTP Error 429: Too Many Requests", True),
    ("Sign in to confirm you're not a bot", True),
    ("This video is private", False),
    ("Video has been removed by the user", False),
    ("HTTP Error 404: Not Found", False),
    ("Unsupported URL", False),
])
def test_block_detection(err, blocked):
    assert d.looks_ip_blocked(err) is blocked


def test_fallback_proxy_rules(pools):
    assert d.fallback_proxy_for(TT) == ALL
    assert d.fallback_proxy_for(YT) is None                  # YouTube has its own system
    pools["tiktok"] = "http://own.invalid:2"
    assert d.fallback_proxy_for(TT) is None                  # own proxy was already used
    pools.pop("all")
    assert d.fallback_proxy_for(FB) is None                  # empty pool → no retry


def _patch_impl(monkeypatch, outcomes, seen):
    def impl(url, *a, **k):
        seen.append({"fallback": d._FALLBACK_PROXY.get(),
                     "meta": d._get_base_opts(url, phase="metadata").get("proxy"),
                     "dl": d._get_base_opts(url, phase="download").get("proxy")})
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o
    monkeypatch.setattr(d, "_extract_video_info_impl", impl)
    monkeypatch.setattr(d, "get_proxy_config_for_phase", lambda url, phase="metadata": None)


def test_blocked_extraction_retries_once_metadata_only(pools, monkeypatch):
    seen = []
    _patch_impl(monkeypatch, [ValueError("HTTP Error 403: Forbidden"), {"title": "ok"}], seen)
    assert d.extract_video_info_sync(TT, quality="video") == {"title": "ok"}
    assert [s["fallback"] for s in seen] == [None, ALL]
    assert seen[0]["meta"] is None                           # first try: direct, as today
    assert seen[1]["meta"] == ALL                            # retry: info through the proxy
    assert seen[1]["dl"] is None                             # video bytes NEVER through it
    assert d._FALLBACK_PROXY.get() is None                   # reset after the call


def test_no_retry_for_content_errors_youtube_or_empty_pool(pools, monkeypatch):
    for url, err in [(TT, "This video is private"), (YT, "HTTP Error 403: Forbidden")]:
        seen = []
        _patch_impl(monkeypatch, [ValueError(err)], seen)
        with pytest.raises(ValueError):
            d.extract_video_info_sync(url, quality="video")
        assert len(seen) == 1
    pools.pop("all")
    seen = []
    _patch_impl(monkeypatch, [ValueError("HTTP Error 403: Forbidden")], seen)
    with pytest.raises(ValueError):
        d.extract_video_info_sync(TT, quality="video")
    assert len(seen) == 1


def test_failed_retry_reports_the_original_error(pools, monkeypatch):
    seen = []
    _patch_impl(monkeypatch, [ValueError("HTTP Error 403: Forbidden"), ValueError("other")], seen)
    with pytest.raises(ValueError, match="403"):
        d.extract_video_info_sync(TT, quality="video")
    assert len(seen) == 2


def test_facebook_retry_ladder_never_downloads_through_the_fallback(monkeypatch):
    monkeypatch.setattr(d, "_impersonate_target", lambda: "chrome")
    token = d._FALLBACK_PROXY.set(ALL)
    try:
        plan = d._facebook_retry_plan({"proxy": ALL, "cookiefile": "/tmp/c.txt"})
    finally:
        d._FALLBACK_PROXY.reset(token)
    assert plan and all("proxy" not in o for _, o in plan)
    # a platform's own proxy (existing behaviour) is left as it was
    plan = d._facebook_retry_plan({"proxy": "http://own.invalid:2", "cookiefile": "/tmp/c.txt"})
    assert all(o.get("proxy") == "http://own.invalid:2" for _, o in plan)


def test_channel_watch_uses_all_pool_after_its_own(monkeypatch):
    from app.services import channel_watch as cw
    table = {"all": ALL}
    monkeypatch.setattr("app.core.proxy_pool.get_proxy_from_pool", lambda p: table.get(p))
    assert cw._watch_proxy() == ALL
    table["tiktok_watch"] = "http://watch.invalid:3"
    assert cw._watch_proxy() == "http://watch.invalid:3"
