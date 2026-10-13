"""Task #6332: the three probes that stayed red were real user failures.

- Rumble answers plain yt-dlp with 403; a Chrome fingerprint gets the page.
- A podcast RSS feed went to yt-dlp's generic extractor, which expands it
  into every episode and never finished (live /fetch-link: >90 s).
(Odysee's single-720p source is covered in test_format_selector_unknown_height.)
"""
import pytest

from app.services import downloader as d


@pytest.mark.parametrize("url,want", [
    ("https://rumble.com/vdmum1-moose.html", True),
    ("https://www.rumble.com/vdmum1-moose.html", True),
    ("https://notrumble.com/x", False),
    ("https://example.com/?u=rumble.com", False),
])
def test_is_rumble_url(url, want):
    assert d.is_rumble_url(url) is want


def test_rumble_opts_impersonate_when_available(monkeypatch):
    monkeypatch.setattr(d, "_impersonate_target", lambda: "chrome")
    assert d._get_base_opts("https://rumble.com/vdmum1-x.html").get("impersonate") == "chrome"


def test_rumble_opts_without_target_do_not_break(monkeypatch):
    monkeypatch.setattr(d, "_impersonate_target", lambda: None)
    assert "impersonate" not in d._get_base_opts("https://rumble.com/vdmum1-x.html")


def test_other_sites_not_impersonated(monkeypatch):
    monkeypatch.setattr(d, "_impersonate_target", lambda: "chrome")
    assert "impersonate" not in d._get_base_opts("https://odysee.com/@a:1/b:2")


@pytest.mark.parametrize("url,want", [
    ("https://feeds.npr.org/510318/podcast.xml", True),
    ("https://anchor.fm/s/abc/podcast/rss", True),
    ("https://example.com/feed", True),
    ("https://feeds.megaphone.fm/ABC123", True),
    ("https://example.com/show.rss?x=1", True),
    ("https://www.youtube.com/feed/subscriptions", False),
    ("https://www.reddit.com/r/videos/.rss", False),
    ("https://www.tiktok.com/@nba/video/1", False),
    ("https://odysee.com/@Odysee:8/call-an-ambulance:6", False),
    ("https://example.com/video.mp4", False),
])
def test_is_podcast_feed_url(url, want):
    assert d.is_podcast_feed_url(url) is want


def test_podcast_feed_is_resolved_before_ytdlp(monkeypatch):
    """The feed URL must never reach yt-dlp: it is replaced by the newest
    episode's audio URL and the request becomes audio."""
    seen = {}

    def fake_extract_podcast(url, episode_index=0):
        return {"title": "Ep 1", "thumbnail": "https://img/x.jpg",
                "direct_url": "https://cdn.example/ep1.mp3", "error_code": None}

    import app.services.podcast_extractor as pe
    monkeypatch.setattr(pe, "extract_podcast", fake_extract_podcast)

    class Stop(Exception):
        pass

    def spy(url, *a, **k):
        seen.setdefault("urls", []).append(url)
        seen.setdefault("q", []).append(k.get("quality"))
        raise Stop()

    monkeypatch.setattr(d, "_get_base_opts", spy)
    with pytest.raises(Exception):
        d._extract_video_info_impl("https://feeds.npr.org/510318/podcast.xml", quality="video_480")
    assert seen.get("urls"), "extraction never reached yt-dlp options"
    assert all("podcast.xml" not in u for u in seen["urls"])
    assert "https://cdn.example/ep1.mp3" in seen["urls"]


def test_podcast_feed_error_is_reported(monkeypatch):
    import app.services.podcast_extractor as pe
    monkeypatch.setattr(pe, "extract_podcast", lambda url, episode_index=0: {
        "error_code": "podcast_rss_invalid", "error_message": "Không thể tải RSS feed."})

    def no_ytdlp(*a, **k):   # the feed must not fall through to yt-dlp
        raise RuntimeError("reached yt-dlp")

    monkeypatch.setattr(d, "_get_base_opts", no_ytdlp)
    with pytest.raises(ValueError, match="RSS"):
        d._extract_video_info_impl("https://feeds.npr.org/510318/podcast.xml", quality="video")


def test_probe_reason_keeps_technical_cause(monkeypatch):
    from app.core import platform_probe as pp
    msg = ("Không thể tải video Instagram. Instagram yêu cầu đăng nhập. "
           "Vui lòng upload cookies Instagram (hết hạn sau ~7 ngày) "
           "qua Admin panel: POST /admin/cookies/upload"
           " (Lý do kỹ thuật: [Instagram] DeJjs2ipNPr: Requested content is not available)")

    def boom(*a, **k):
        raise ValueError(msg)

    import app.services.downloader as dl
    monkeypatch.setattr(dl, "extract_video_info_sync", boom)
    r = pp.probe_once("https://www.instagram.com/reel/DeJjs2ipNPr/", timeout=5)
    assert r["ok"] is False
    assert r["reason"].startswith("[Instagram] DeJjs2ipNPr: Requested content is not available")
