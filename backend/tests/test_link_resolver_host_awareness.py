"""TikTok short-link targets must never be rewritten into Douyin URLs."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.utils import link_resolver as resolver


TIKTOK_URL = "https://www.tiktok.com/@giaynhasam/video/7693454318795541768"
DOUYIN_URL = "https://www.douyin.com/video/7394012345678901234"


@pytest.mark.parametrize("url, expected", [
    (TIKTOK_URL + "?_r=1&_t=ZS-9Awvm9AgkEw", TIKTOK_URL),
    (DOUYIN_URL + "?foo=1", DOUYIN_URL),
    ("https://www.iesdouyin.com/share/video/7394012345678901234/", DOUYIN_URL),
    ("https://www.douyin.com/?modal_id=7394012345678901234&aweme_id=7394012345678901234", DOUYIN_URL),
    ("https://www.douyin.com/?item_ids=7394012345678901234", DOUYIN_URL),
    ("https://example.com/video/7394012345678901234", "https://example.com/video/7394012345678901234"),
])
def test_canonicalize_only_rewrites_douyin_hosts(url, expected):
    assert resolver._canonicalize_douyin(url) == expected


@pytest.mark.parametrize("url", [
    "https://v.douyin.com/abc/",
    DOUYIN_URL,
    "https://www.iesdouyin.com/share/video/7394012345678901234",
    "https://V.DOUYIN.COM/abc/",
    "v.douyin.com/abc/",
    "复制链接 https://v.douyin.com/abc/ 打开Dou音搜索",
])
def test_douyin_host_detection(url):
    assert resolver.is_douyin_url(url)


@pytest.mark.parametrize("url", [
    TIKTOK_URL,
    "https://vt.tiktok.com/ZSgsnUuTm/",
    "https://vm.tiktok.com/abc/",
    "https://example.com/?r=douyin.com",
    "https://example.com/?r=https://www.douyin.com/video/7394012345678901234",
    "https://example.com/douyin.com/video/7394012345678901234",
    "https://douyin.com.example.com/video/7394012345678901234",
    "https://notdouyin.com/video/7394012345678901234",
    "https://douyin.com@example.com/video/7394012345678901234",
    "https://[invalid/",
    "",
])
def test_non_douyin_urls_are_not_classified_as_douyin(url):
    assert not resolver.is_douyin_url(url)


@pytest.mark.parametrize("short_url", [
    "https://vt.tiktok.com/ZSgsnUuTm/",
    "https://vm.tiktok.com/abc/",
])
@pytest.mark.parametrize("attempt", ["mobile", "android", "follow"])
def test_tiktok_short_links_preserve_platform_for_every_redirect_strategy(monkeypatch, short_url, attempt):
    target = TIKTOK_URL + "?_r=1&_t=ZS-9Awvm9AgkEw"
    responses = {
        "mobile": [target],
        "android": [None, target],
        "follow": [None, None],
    }
    redirect = AsyncMock(side_effect=responses[attempt])
    follow = AsyncMock(return_value=target)
    douyin = AsyncMock(side_effect=AssertionError("TikTok must not use the Douyin waterfall"))
    monkeypatch.setattr(resolver, "_try_resolve_302", redirect)
    monkeypatch.setattr(resolver, "_try_resolve_follow", follow)
    monkeypatch.setattr(resolver, "resolve_douyin_shortlink", douyin)

    result = asyncio.run(resolver.resolve_short_url_async(short_url))

    assert result == TIKTOK_URL
    assert not resolver.is_douyin_url(result)
    assert redirect.await_count == (1 if attempt == "mobile" else 2)
    assert follow.await_count == (1 if attempt == "follow" else 0)
    douyin.assert_not_awaited()


def test_douyin_short_link_keeps_waterfall(monkeypatch):
    douyin = AsyncMock(return_value=DOUYIN_URL)
    generic = AsyncMock(side_effect=AssertionError("Douyin should use its waterfall"))
    monkeypatch.setattr(resolver, "resolve_douyin_shortlink", douyin)
    monkeypatch.setattr(resolver, "_try_resolve_302", generic)

    assert asyncio.run(resolver.resolve_short_url_async("https://v.douyin.com/abc/")) == DOUYIN_URL
    douyin.assert_awaited_once()
    generic.assert_not_awaited()
