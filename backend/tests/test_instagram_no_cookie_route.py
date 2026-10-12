"""Task #6258 — an empty Instagram cookie pool must not block public reels
when Cobalt-first / cookie-last can serve them (12/10: every Instagram
download answered 422 'Instagram yêu cầu đăng nhập' before Cobalt ran)."""
import pytest

from app.services import downloader as dl

REEL = "https://www.instagram.com/reel/DeJjs2ipNPr/"


class _Reached(BaseException):   # not swallowed by the Cobalt-first try/except
    pass


@pytest.fixture
def empty_pool(monkeypatch):
    monkeypatch.setattr(dl, "_INSTAGRAM_COOKIES_B64", "")
    monkeypatch.setattr("app.core.cookie_pool.get_cookie_from_pool", lambda p: None)
    monkeypatch.setattr("app.services.cobalt_service.cobalt_platform_tripped", lambda p: False)
    monkeypatch.delenv("COOKIE_LAST_PLATFORMS", raising=False)
    monkeypatch.delenv("COBALT_FIRST_PLATFORMS", raising=False)


def _reaches_cobalt(monkeypatch):
    def _stop(*a, **k):
        raise _Reached()
    monkeypatch.setattr(dl, "is_cobalt_available", lambda: True)
    monkeypatch.setattr(dl, "cobalt_with_meta", _stop)


def test_no_route_still_asks_for_a_cookie(empty_pool):
    with pytest.raises(ValueError, match="Instagram yêu cầu đăng nhập"):
        dl._extract_video_info_impl(REEL, "video")


def test_cobalt_first_serves_without_a_pool_cookie(empty_pool, monkeypatch):
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "facebook,instagram,twitter")
    _reaches_cobalt(monkeypatch)
    with pytest.raises(_Reached):                  # got past the gate to Cobalt-first
        dl._extract_video_info_impl(REEL, "video")


def test_cookie_last_counts_as_a_route(empty_pool, monkeypatch):
    monkeypatch.setenv("COOKIE_LAST_PLATFORMS", "instagram")
    assert dl.no_cookie_route("instagram") is True


def test_tripped_cobalt_is_not_a_route(empty_pool, monkeypatch):
    monkeypatch.setenv("COBALT_FIRST_PLATFORMS", "instagram")
    monkeypatch.setattr("app.services.cobalt_service.cobalt_platform_tripped", lambda p: True)
    assert dl.no_cookie_route("instagram") is False
