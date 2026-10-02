"""
/fetch-link must answer a bad link with 4xx, not 500.

Measured on production 2026-10-02 before this change: a non-video page
(https://example.com/not-a-video) and a made-up TikTok id both came back as
HTTP 500. Those are the user's input being wrong. Only genuine server-side
faults (extractor crash, upstream blocking us, timeout) may be 5xx.

`detail` must stay the same plain Vietnamese string the frontend already
renders; the classification rides alongside as `error_code`.
"""

import pytest

from app.core.extraction_errors import classify_extraction_error


# Real reason strings, copied from the live API / yt-dlp source.
GENERIC_404 = (
    "Không thể trích xuất thông tin video. Vui lòng kiểm tra: link đúng không, video có "
    "Public không, hoặc video có bị xoá/riêng tư không. (Lý do kỹ thuật: [generic] "
    "not-a-video: Unable to download webpage: HTTP Error 404: Not Found (caused by "
    "<HTTPError 404: Not Found>))"
)
TIKTOK_10204 = (
    "Không thể trích xuất thông tin video. Vui lòng kiểm tra: link đúng không, video có "
    "Public không, hoặc video có bị xoá/riêng tư không. (Lý do kỹ thuật: [TikTok] "
    "7000000000000000000: Your IP address is blocked from accessing this post)"
)


def _wrap(reason: str) -> str:
    return ("Không thể trích xuất thông tin video. Vui lòng kiểm tra: link đúng không, "
            "video có Public không, hoặc video có bị xoá/riêng tư không. "
            f"(Lý do kỹ thuật: {reason})")


class TestClassifier:

    @pytest.mark.parametrize("msg,status,code", [
        (GENERIC_404, 400, "unsupported_url"),
        (_wrap("Unsupported URL: https://example.org/page"), 400, "unsupported_url"),
        (TIKTOK_10204, 404, "video_unavailable"),
        (_wrap("[youtube] abc: Video unavailable"), 404, "video_unavailable"),
        (_wrap("[youtube] abc: This video has been removed by the uploader"), 404, "video_unavailable"),
        (_wrap("[youtube] abc: Private video. Sign in if you've been granted access"), 422,
         "private_or_login_required"),
        (_wrap("[TikTok] 1: You do not have permission to view this post. Log into an account"),
         422, "private_or_login_required"),
        (_wrap("[youtube] abc: The uploader has not made this video available in your country"),
         422, "geo_blocked"),
        (_wrap("[youtube] abc: This video is DRM protected"), 422, "drm_protected"),
    ])
    def test_user_input_errors_are_4xx(self, msg, status, code):
        assert classify_extraction_error(msg) == (status, code)

    @pytest.mark.parametrize("msg,status", [
        # YouTube's throttle wording contains "Video unavailable" — it is us
        # being blocked, not the video being gone.
        (_wrap("[youtube] abc: Video unavailable. This content isn't available, try again later."), 503),
        (_wrap("[youtube] abc: Sign in to confirm you're not a bot"), 503),
        (_wrap("[Instagram] x: Requested content is not available, rate-limit reached or login required"), 503),
        (_wrap("[TikTok] 1: Unable to download webpage: HTTP Error 502: Bad Gateway"), 502),
        ("Quá thời gian chờ (600s). Link có thể bị chặn bởi captcha hoặc server phản hồi chậm. "
         "Vui lòng thử lại sau.", 504),
        ("Không thể tải video YouTube. YouTube đang chặn bot — vui lòng thử lại sau 30 giây.", 503),
    ])
    def test_server_side_faults_stay_5xx(self, msg, status):
        assert classify_extraction_error(msg)[0] == status

    def test_crash_is_5xx(self):
        assert classify_extraction_error("'NoneType' object is not subscriptable",
                                         TypeError("x"))[0] >= 500

    def test_friendly_prefix_alone_does_not_decide(self):
        """The downloader's Facebook text says 'riêng tư … đã bị xoá' for EVERY
        Facebook failure. Without a technical reason we cannot know, so it must
        not be classified as the user's fault."""
        msg = ("Không thể tải video Facebook. Video có thể ở chế độ riêng tư, đã bị xoá, "
               "hoặc Facebook đang chặn máy chủ tải.")
        assert classify_extraction_error(msg)[0] >= 500


# ── HTTP layer ──────────────────────────────────────────────────────────

@pytest.fixture
def _route_env(monkeypatch, tmp_path):
    """Get /fetch-link past the guards that are not under test."""
    import app.api.routes as routes
    from app.core import disk_guardrail, ssrf_guard
    monkeypatch.setattr(disk_guardrail, "_DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(disk_guardrail, "check_can_accept_job", lambda *a, **k: (True, ""))
    monkeypatch.setattr(routes, "_preflight_disk_check", lambda: None)
    monkeypatch.setattr(ssrf_guard, "_resolve", lambda h: ("93.184.216.34",))
    return routes


def _raise(exc):
    async def _fake(*a, **k):
        raise exc
    return _fake


@pytest.mark.parametrize("url,exc,status,code", [
    ("https://example.com/not-a-video", ValueError(GENERIC_404), 400, "unsupported_url"),
    ("https://www.tiktok.com/@x/video/7000000000000000000", ValueError(TIKTOK_10204), 404,
     "video_unavailable"),
    ("https://www.instagram.com/p/abc/", ValueError(_wrap("[Instagram] abc: Video unavailable")),
     404, "video_unavailable"),
    ("https://www.tiktok.com/@x/video/1", ValueError(_wrap("[TikTok] 1: Private video")),
     422, "private_or_login_required"),
])
def test_fetch_link_bad_link_is_4xx_with_error_code(app, _route_env, monkeypatch, url, exc, status, code):
    monkeypatch.setattr(_route_env, "extract_video_info", _raise(exc))
    r = app.post("/api/v1/fetch-link", json={"url": url, "quality": "video"})
    assert r.status_code == status, r.text
    body = r.json()
    assert body["error_code"] == code
    # Frontend contract: detail is still a non-empty plain string.
    assert isinstance(body["detail"], str) and body["detail"]


def test_fetch_link_extractor_crash_is_5xx(app, _route_env, monkeypatch):
    monkeypatch.setattr(_route_env, "extract_video_info",
                        _raise(KeyError("formats")))
    r = app.post("/api/v1/fetch-link",
                 json={"url": "https://www.tiktok.com/@x/video/1", "quality": "video"})
    assert 500 <= r.status_code < 600, r.text
    assert r.json()["error_code"] in ("provider_unavailable", "processing_failed")
    assert isinstance(r.json()["detail"], str)


def test_fetch_link_upstream_timeout_is_504(app, _route_env, monkeypatch):
    monkeypatch.setattr(_route_env, "extract_video_info", _raise(ValueError(
        "Quá thời gian chờ (600s). Link có thể bị chặn bởi captcha hoặc server phản hồi chậm. "
        "Vui lòng thử lại sau.")))
    r = app.post("/api/v1/fetch-link",
                 json={"url": "https://www.tiktok.com/@x/video/1", "quality": "video"})
    assert r.status_code == 504
    assert r.json()["error_code"] == "extraction_timeout"
