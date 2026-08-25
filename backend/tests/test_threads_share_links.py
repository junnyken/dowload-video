"""
Threads /share/<code> links.

The app's "Copy link" button produces threads.com/share/<code>. That path
matched neither the post pattern (/@handle/post/, /t/, /p/) nor the profile
pattern (/@handle), so it classified as "unsupported" and came back to the user
as "Đây là trang cá nhân Threads" — an error about a profile, for a link that
was not a profile. It sent people looking for a problem with their link instead
of ours.

The share code is NOT the post shortcode, which is why it cannot simply be
added to the post pattern: extract_threads_post locks post_id out of the URL
and then accepts only the embedded node whose code matches, so a share code
would lock an id that can never match and fail in a new way. Share links have
to be redirected to the real permalink first.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import threads_extractor as tx

SHARE_URL = "https://www.threads.com/share/Fca206Ivq/"
POST_URL = "https://www.threads.com/@someone/post/ABC123"


class TestClassification:

    def test_share_link_is_its_own_kind(self):
        assert tx.classify_threads_url(SHARE_URL) == "share"

    def test_share_link_is_not_mistaken_for_a_profile(self):
        """The bug: a share link reported as a profile page."""
        assert tx.is_threads_profile_url(SHARE_URL) is False

    def test_share_link_is_not_treated_as_a_post(self):
        """Treating it as a post would lock a share code as the post id."""
        assert tx.is_threads_post_url(SHARE_URL) is False

    @pytest.mark.parametrize("url,kind", [
        ("https://www.threads.com/@handle/post/ABC123", "post"),
        ("https://www.threads.net/t/ABC123", "post"),
        ("https://www.threads.com/p/ABC123", "post"),
        ("https://www.threads.com/@handle", "profile"),
        ("https://www.threads.com/search?q=x", "unsupported"),
    ])
    def test_existing_shapes_are_unchanged(self, url, kind):
        assert tx.classify_threads_url(url) == kind

    def test_share_detection_requires_a_threads_host(self):
        assert tx.is_threads_share_url("https://example.com/share/ABC123") is False


class TestResolution:

    def _resp(self, final_url):
        r = MagicMock()
        r.url = final_url
        return r

    def _client(self, resp):
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    def test_resolves_to_the_permalink(self):
        with patch.object(tx.httpx, "AsyncClient",
                          return_value=self._client(self._resp(POST_URL))):
            got = asyncio.new_event_loop().run_until_complete(
                tx._resolve_share_url(SHARE_URL))
        assert got == POST_URL

    def test_landing_somewhere_that_is_not_a_post_is_a_failure(self):
        """Threads answers a dead share code with the home page or a login
        wall. Following that and calling it a post would be worse than
        admitting the link did not resolve."""
        with patch.object(tx.httpx, "AsyncClient",
                          return_value=self._client(self._resp("https://www.threads.com/"))):
            got = asyncio.new_event_loop().run_until_complete(
                tx._resolve_share_url(SHARE_URL))
        assert got is None

    def test_network_failure_returns_none_rather_than_raising(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=RuntimeError("connection reset"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(tx.httpx, "AsyncClient", return_value=client):
            got = asyncio.new_event_loop().run_until_complete(
                tx._resolve_share_url(SHARE_URL))
        assert got is None


class TestDispatch:

    def test_unresolvable_share_link_says_so_in_plain_terms(self):
        """Not "this is a profile" — the previous message pointed the user at
        the wrong thing entirely."""
        with patch.object(tx, "_resolve_share_url", new=AsyncMock(return_value=None)):
            result = asyncio.new_event_loop().run_until_complete(
                tx.extract_threads(SHARE_URL))
        assert result.get("success") is not True
        blob = str(result)
        assert "chia sẻ" in blob
        assert "trang cá nhân" not in blob

    def test_resolved_share_link_reaches_the_post_extractor(self):
        with patch.object(tx, "_resolve_share_url", new=AsyncMock(return_value=POST_URL)), \
             patch.object(tx, "extract_threads_post",
                          new=AsyncMock(return_value={"success": True})) as post:
            result = asyncio.new_event_loop().run_until_complete(
                tx.extract_threads(SHARE_URL))
        assert result == {"success": True}
        assert post.await_args[0][0] == POST_URL, (
            "the post extractor must receive the RESOLVED url — handed the "
            "share url it would lock a share code as the post id"
        )
