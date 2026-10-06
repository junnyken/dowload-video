"""`"x.com" in url` matched dropbox.com, netflix.com, xbox.com... (2026-10-06)."""
import pytest

from app.core.twitter_host import is_twitter_url


@pytest.mark.parametrize("url", [
    "https://x.com/a/status/1", "https://twitter.com/a/status/1",
    "https://mobile.twitter.com/a", "https://www.x.com/a", "x.com/a/status/1",
    "HTTPS://X.COM/A",
])
def test_twitter_hosts(url):
    assert is_twitter_url(url)


@pytest.mark.parametrize("url", [
    "https://www.dropbox.com/s/abc/v.mp4", "https://www.netflix.com/watch/1",
    "https://xbox.com/clip", "https://fx.com/v", "https://example.com/?next=x.com",
    "https://notx.com.evil.io/v", "", None,
])
def test_not_twitter(url):
    assert not is_twitter_url(url)


def test_platform_key_no_longer_counts_dropbox_as_twitter():
    from app.api.routes import _get_platform_key
    assert _get_platform_key("https://www.dropbox.com/s/abc/v.mp4") == "other"
    assert _get_platform_key("https://x.com/a/status/1") == "twitter"
