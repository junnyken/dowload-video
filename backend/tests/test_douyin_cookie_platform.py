"""
Douyin was locked out of the cookie pool entirely.

The Douyin extractor asks the pool for a douyin cookie on every request, but
the admin upload endpoint's platform allowlist did not contain "douyin" — it
answered 400. So a Douyin cookie could never be added through the API, the pool
never held one, and every Douyin download came back with "Douyin requires a
valid cookie for every video, including public ones".

Nobody had forgotten to upload a cookie. There was no way to.
"""

from __future__ import annotations


class TestPlatformIsAccepted:

    def test_douyin_is_in_the_upload_allowlist(self):
        from app.api.admin import _VALID_PLATFORMS
        assert "douyin" in _VALID_PLATFORMS

    def test_the_platforms_with_extractors_can_all_receive_cookies(self):
        """Every platform the download path resolves a cookie file for must be
        uploadable, or that code reads from a pool that can never be filled."""
        from app.api.admin import _VALID_PLATFORMS
        for platform in ("youtube", "tiktok", "facebook", "instagram",
                         "twitter", "douyin"):
            assert platform in _VALID_PLATFORMS, (
                f"{platform} has cookie-loading code but cannot be uploaded"
            )


class TestDouyinAuthCookies:
    """The pool uses these names to tell a real cookie from junk and to work
    out an expiry."""

    def test_durable_guest_tokens_are_recognised(self):
        from app.core.cookie_pool import _AUTH_COOKIES
        names = _AUTH_COOKIES.get("douyin", set())
        for token in ("ttwid", "odin_tt", "UIFID"):
            assert token in names

    def test_the_single_use_nonce_is_not_treated_as_auth(self):
        """__ac_nonce is an anti-bot nonce valid for minutes. Listing it would
        make every freshly uploaded Douyin cookie look expired within the
        hour."""
        from app.core.cookie_pool import _AUTH_COOKIES
        assert "__ac_nonce" not in _AUTH_COOKIES.get("douyin", set())

    def test_an_unknown_platform_still_requires_nothing(self):
        """Adding douyin must not change the permissive default other
        platforms rely on."""
        from app.core.cookie_pool import _AUTH_COOKIES
        assert _AUTH_COOKIES.get("some_new_platform", set()) == set()
