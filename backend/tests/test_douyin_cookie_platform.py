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
                         "twitter", "douyin", "xiaohongshu", "bilibili",
                         "reddit"):
            assert platform in _VALID_PLATFORMS, (
                f"{platform} has cookie-loading code but cannot be uploaded"
            )

    def test_no_extractor_asks_the_pool_for_a_platform_that_cannot_be_uploaded(self):
        """The general form of the Douyin bug, so the next one is caught here
        rather than by a user reporting that a platform never works.

        Xiaohongshu was the second instance: its own docstring says 'Add XHS
        cookie to admin: platform "xiaohongshu"', and the upload endpoint
        answered 400 to exactly that.
        """
        import pathlib
        import re

        from app.api.admin import _VALID_PLATFORMS

        root = pathlib.Path(__file__).resolve().parent.parent / "app"
        pattern = re.compile(
            r'(?:get_cookie_from_pool|_get_cookies_file)\(\s*"([a-z0-9_]+)"'
        )
        wanted = set()
        for path in root.rglob("*.py"):
            wanted |= set(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))

        missing = sorted(wanted - set(_VALID_PLATFORMS))
        assert not missing, (
            f"these platforms request a cookie from the pool but cannot be "
            f"uploaded, so the pool can never satisfy them: {missing}"
        )


class TestExpiryReportCoversEveryPlatform:
    """The expiry report is the early-warning surface. It iterated a fixed
    ("youtube","tiktok","facebook","instagram") and nothing else, so a douyin
    or twitter cookie was invisible there — those would have expired with no
    warning at all, which is exactly the failure the report exists to prevent.
    """

    def test_platforms_are_discovered_from_the_pool(self):
        import inspect
        from app.api import admin
        src = inspect.getsource(admin.cookie_expiry_report)
        assert "get_pool_status" in src, (
            "a hardcoded platform list silently drops any cookie the pool "
            "gained since the list was written"
        )

    def test_the_known_four_are_still_always_reported(self):
        """A platform with an empty pool must report as empty, not disappear
        from the dashboard."""
        import inspect
        from app.api import admin
        src = inspect.getsource(admin.cookie_expiry_report)
        for platform in ("youtube", "tiktok", "facebook", "instagram"):
            assert f'"{platform}"' in src


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
