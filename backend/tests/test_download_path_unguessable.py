"""
Anyone who could name a file could download it.

GET /download-local has no session and no ownership check. Verified against
production with a bare curl carrying no credentials at all:

    GET /api/v1/download-local?filepath=/app/downloads/2033078270746905_….mp4
    -> HTTP 200 | 3,086,615 bytes | video/mp4

Its containment check is right — the path cannot escape downloads/ — but the
path was *guessable*. Files were named `%(id)s_%(format_id)s.%(ext)s`, and for
YouTube that is an 11-character public video id plus an itag from a short known
list. `kGWFwVWwJYU_18.mp4` is a couple of dozen guesses away from anyone who
knows the video. The app offers "use my cookies" for private and members-only
content, so a file one person decrypted with their own session sat in a shared
directory under a name derivable from the video id alone.

Signing the URL is the stronger fix, but /download-local has 42 call sites
including the Chrome extension — already installed on users' machines — so a
signature would break every client until each one shipped. An unguessable path
closes the same hole with no client change: every caller already receives the
exact path in its own API response.

These tests pin the property the fix depends on: knowing everything public
about a video must not be enough to name its file.
"""

from __future__ import annotations

import os
import re
import tempfile

import pytest

from app.services import downloader


@pytest.fixture(autouse=True)
def _fixed_secret(monkeypatch):
    """Pin the secret so tokens are reproducible inside a test."""
    monkeypatch.setattr(downloader, "_path_secret_cache", "secret-for-tests", raising=False)
    yield
    monkeypatch.setattr(downloader, "_path_secret_cache", None, raising=False)


YT_URL = "https://www.youtube.com/watch?v=kGWFwVWwJYU"


class TestThePathCannotBeDerivedFromPublicFacts:

    def test_the_old_guessable_name_is_gone(self):
        """`{video_id}_{itag}.mp4` was the whole attack. It must no longer be
        the name of anything."""
        tmpl = downloader._get_base_opts(YT_URL, quality="video_1080")["outtmpl"]
        assert not tmpl.endswith("%(id)s_%(format_id)s.%(ext)s"), (
            "the filename is still derivable from the video id and an itag"
        )

    def test_the_token_is_present_and_long_enough_to_matter(self):
        tmpl = downloader._get_base_opts(YT_URL, quality="video_1080")["outtmpl"]
        m = re.search(r"%\(id\)s_%\(format_id\)s_([0-9a-f]+)\.%\(ext\)s$", tmpl)
        assert m, f"unexpected outtmpl shape: {tmpl}"
        assert len(m.group(1)) >= 12, "too short to stand up to guessing"

    def test_two_urls_do_not_share_a_token(self):
        a = downloader._download_path_token("https://www.youtube.com/watch?v=aaaaaaaaaaa")
        b = downloader._download_path_token("https://www.youtube.com/watch?v=bbbbbbbbbbb")
        assert a != b

    def test_the_token_does_not_expose_the_secret(self):
        token = downloader._download_path_token(YT_URL)
        assert "secret-for-tests" not in token
        assert re.fullmatch(r"[0-9a-f]+", token)

    def test_the_secret_actually_changes_the_answer(self, monkeypatch):
        """A token that ignores the secret is a hash, not a capability —
        anyone could recompute it from the URL alone."""
        first = downloader._download_path_token(YT_URL)
        monkeypatch.setattr(downloader, "_path_secret_cache", "a-different-secret")
        assert downloader._download_path_token(YT_URL) != first


class TestReuseStillWorks:
    """The token is derived, not random, so the existing 'already downloaded,
    serve the file on disk' behaviour has to survive. A random name would have
    re-fetched every video every time."""

    def test_the_same_url_always_lands_on_the_same_path(self):
        first = downloader._get_base_opts(YT_URL, quality="video_1080")["outtmpl"]
        second = downloader._get_base_opts(YT_URL, quality="video_1080")["outtmpl"]
        assert first == second

    def test_the_path_stays_inside_the_downloads_directory(self):
        tmpl = downloader._get_base_opts(YT_URL, quality="video_1080")["outtmpl"]
        assert os.path.dirname(tmpl) == downloader.DOWNLOAD_DIR


class TestPrivateDownloadsAreIsolated:
    """"Use my cookies" is the case that made this worth fixing: content only
    that person can reach must not land where an anonymous fetch of the same
    URL would look."""

    @staticmethod
    def _cookie_file(body: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        return path

    def test_a_cookie_authenticated_download_gets_its_own_path(self):
        anon = downloader._download_path_token(YT_URL)
        path = self._cookie_file(".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tprivate")
        try:
            assert downloader._download_path_token(YT_URL, path) != anon
        finally:
            os.remove(path)

    def test_two_different_accounts_do_not_share_a_path(self):
        a = self._cookie_file(".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\taccount-a")
        b = self._cookie_file(".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\taccount-b")
        try:
            assert downloader._download_path_token(YT_URL, a) != downloader._download_path_token(YT_URL, b)
        finally:
            os.remove(a)
            os.remove(b)

    def test_the_same_account_reuses_its_own_file(self):
        """Isolation must not cost that user their own cache."""
        body = ".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tsame-account"
        a, b = self._cookie_file(body), self._cookie_file(body)
        try:
            assert downloader._download_path_token(YT_URL, a) == downloader._download_path_token(YT_URL, b)
        finally:
            os.remove(a)
            os.remove(b)

    def test_an_unreadable_cookie_file_never_falls_back_to_the_anonymous_path(self):
        anon = downloader._download_path_token(YT_URL)
        token = downloader._download_path_token(YT_URL, "/nonexistent/cookies.txt")
        assert token != anon, (
            "a cookie file we could not read must not silently downgrade a "
            "private download onto the shared path"
        )

    def test_isolate_outtmpl_rewrites_the_template(self):
        opts = downloader._get_base_opts(YT_URL, quality="video_1080")
        before = opts["outtmpl"]
        path = self._cookie_file(".youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tprivate")
        try:
            downloader._isolate_outtmpl(opts, YT_URL, path)
            assert opts["outtmpl"] != before
            assert os.path.dirname(opts["outtmpl"]) == downloader.DOWNLOAD_DIR
        finally:
            os.remove(path)

    def test_isolate_outtmpl_leaves_metadata_only_opts_alone(self):
        """video_fast never writes a file; there is no template to rewrite and
        inventing one would start downloading where we used to stream."""
        opts = downloader._get_base_opts(YT_URL, quality="video_fast")
        assert "outtmpl" not in opts
        downloader._isolate_outtmpl(opts, YT_URL, "/tmp/whatever.txt")
        assert "outtmpl" not in opts


class TestTheSecretItself:

    def test_it_never_degrades_to_a_fixed_string(self, monkeypatch):
        """If Redis is down and the fallback were a constant, the token would
        be computable by anyone reading this source file."""
        monkeypatch.setattr(downloader, "_path_secret_cache", None, raising=False)
        monkeypatch.delenv("DOWNLOAD_PATH_SECRET", raising=False)

        def _boom():
            raise RuntimeError("redis down")

        monkeypatch.setattr(downloader, "get_redis", _boom)
        first = downloader._download_path_secret()
        monkeypatch.setattr(downloader, "_path_secret_cache", None, raising=False)
        second = downloader._download_path_secret()
        assert first != second, "the Redis-down fallback is a constant"
        assert len(first) >= 32

    def test_an_explicit_env_secret_wins(self, monkeypatch):
        monkeypatch.setattr(downloader, "_path_secret_cache", None, raising=False)
        monkeypatch.setenv("DOWNLOAD_PATH_SECRET", "operator-chosen-value")
        assert downloader._download_path_secret() == "operator-chosen-value"
