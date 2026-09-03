"""
A cookie file yt-dlp cannot read was accepted, stored, and served forever.

Production's Facebook pool held one long browser `Cookie:` header line. The
upload endpoint base64'd it without looking, the pool reported it as a healthy
cookie, every download loaded it, and yt-dlp threw it away on every request —
so Facebook ran cookie-less while the dashboard said otherwise.

The skip was not silent, and that is the worse half. yt-dlp writes

    WARNING: skipping cookie file entry due to invalid length N: '<the line>'

with write_string() straight to stderr, past any logger. The line it prints is
the credential itself: c_user and xs for Facebook, auth_token for X. One bad
upload put a live session into the runtime log in plaintext on every request.

So: reject at the door, and never hand yt-dlp a line it will reject.
"""

from __future__ import annotations

import base64
import io
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout

import pytest

from app.core.cookie_pool import sanitize_netscape_cookies


# The shape that was actually in production, with the real session values
# replaced. One line, semicolon-separated, no tabs anywhere.
BROWSER_HEADER_LINE = (
    "datr=AAAAAAAAAAAAAAAAAAAAAAAA; sb=BBBBBBBBBBBBBBBBBBBB; ps_l=1; ps_n=1; "
    "c_user=100000000000000; fr=0aaaaaaaaaaaaaaaa.AWXXXXXXXXXXXXXXXXXX; "
    "xs=99%3AZZZZZZZZZZZZ%3A2%3A1780000000%3A-1%3A-1%3A%3AAcyyyyyyyyyy; "
    "wd=1365x945"
)

VALID_NETSCAPE = "\n".join([
    "# Netscape HTTP Cookie File",
    "",
    ".facebook.com\tTRUE\t/\tTRUE\t1900000000\tc_user\t100000000000000",
    ".facebook.com\tTRUE\t/\tTRUE\t1900000000\txs\tsome-session-value",
    "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t1900000000\tfr\tanother-value",
])


class TestSanitizerRecognisesTheProductionFailure:

    def test_a_browser_cookie_header_yields_no_entries(self):
        clean, entries, dropped = sanitize_netscape_cookies(BROWSER_HEADER_LINE)
        assert entries == 0, "a semicolon-joined header line carries no Netscape entries"
        assert dropped == 1
        assert clean == ""

    def test_a_real_netscape_export_survives_untouched(self):
        clean, entries, dropped = sanitize_netscape_cookies(VALID_NETSCAPE)
        assert dropped == 0
        assert entries == 3, "two plain entries plus the #HttpOnly_ one"
        assert "c_user" in clean and "xs" in clean and "fr" in clean

    def test_json_exports_are_rejected_too(self):
        payload = '[{"name": "c_user", "value": "100000000000000"}]'
        _clean, entries, dropped = sanitize_netscape_cookies(payload)
        assert entries == 0
        assert dropped >= 1

    def test_a_comments_only_file_carries_nothing(self):
        """'It parsed fine' is not the same as 'it has credentials'."""
        _clean, entries, _dropped = sanitize_netscape_cookies(
            "# Netscape HTTP Cookie File\n# exported by something\n\n"
        )
        assert entries == 0

    def test_a_non_numeric_expiry_is_dropped(self):
        """yt-dlp rejects these as hard as a wrong field count, and prints the
        same leaking warning — so the sanitizer has to catch them too."""
        line = ".facebook.com\tTRUE\t/\tTRUE\tnever\tc_user\t100000000000000"
        _clean, entries, dropped = sanitize_netscape_cookies(line)
        assert entries == 0
        assert dropped == 1

    def test_a_session_cookie_with_empty_expiry_is_kept(self):
        line = ".facebook.com\tTRUE\t/\tTRUE\t\tc_user\t100000000000000"
        _clean, entries, dropped = sanitize_netscape_cookies(line)
        assert (entries, dropped) == (1, 0)

    def test_the_partial_file_keeps_the_good_lines(self):
        mixed = VALID_NETSCAPE + "\n" + BROWSER_HEADER_LINE
        clean, entries, dropped = sanitize_netscape_cookies(mixed)
        assert (entries, dropped) == (3, 1)
        assert "c_user=100000000000000;" not in clean, "the header line must be gone"


class TestSanitizedOutputNeverMakesYtDlpWarn:
    """The property that actually matters, pinned against the real cookiejar.

    Anything else is a restatement of the sanitizer's own rules; this asks
    yt-dlp itself. If its format handling shifts, this fails instead of a
    credential quietly reappearing in the logs.
    """

    @staticmethod
    def _load_and_capture(text: str) -> str:
        from yt_dlp.cookies import YoutubeDLCookieJar

        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            err, out = io.StringIO(), io.StringIO()
            with redirect_stderr(err), redirect_stdout(out):
                try:
                    YoutubeDLCookieJar(path).load()
                except Exception as exc:  # noqa: BLE001 — LoadError is a valid outcome
                    return f"{err.getvalue()}{out.getvalue()}{exc}"
            return err.getvalue() + out.getvalue()
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_the_raw_bad_file_does_leak_the_line(self):
        """Establish the leak is real before asserting the fix removes it —
        otherwise the test below passes for the wrong reason."""
        noise = self._load_and_capture(BROWSER_HEADER_LINE + "\n")
        assert "skipping cookie file entry" in noise
        assert "c_user=100000000000000" in noise, (
            "yt-dlp prints the offending line verbatim — this is the leak"
        )

    @pytest.mark.parametrize("source", [VALID_NETSCAPE, VALID_NETSCAPE + "\n" + BROWSER_HEADER_LINE])
    def test_sanitized_output_loads_in_silence(self, source):
        clean, entries, _dropped = sanitize_netscape_cookies(source)
        assert entries > 0
        noise = self._load_and_capture(clean)
        assert noise == "", f"yt-dlp still complained about the cleaned file: {noise!r}"


class TestTheDownloadPathRefusesAnUnusableCookie:

    def test_no_cookie_file_is_written_and_nothing_is_logged(self, monkeypatch):
        """The bad cookie must not reach yt-dlp, and the refusal must not
        quote it — a log line is exactly where it must not end up."""
        from app.services import downloader

        monkeypatch.setattr(downloader, "_cookies_cache", {}, raising=False)
        monkeypatch.setattr(downloader, "_active_cookie_b64", {}, raising=False)

        b64 = base64.b64encode(BROWSER_HEADER_LINE.encode()).decode()
        buf = io.StringIO()
        with redirect_stdout(buf):
            path = downloader._get_cookies_file("facebook", b64)

        assert path is None, "an unloadable cookie must be treated as no cookie"
        logged = buf.getvalue()
        assert "c_user" not in logged and "100000000000000" not in logged, (
            f"the refusal leaked cookie content: {logged!r}"
        )
        assert "Netscape" in logged, "the operator still needs to be told why"

    def test_a_good_cookie_still_reaches_yt_dlp(self, monkeypatch):
        from app.services import downloader

        monkeypatch.setattr(downloader, "_cookies_cache", {}, raising=False)
        monkeypatch.setattr(downloader, "_active_cookie_b64", {}, raising=False)

        b64 = base64.b64encode(VALID_NETSCAPE.encode()).decode()
        path = downloader._get_cookies_file("facebook", b64)
        try:
            assert path and os.path.exists(path)
            assert "c_user" in open(path, encoding="utf-8").read()
        finally:
            if path and os.path.exists(path):
                os.remove(path)


class TestTheUploadEndpointRejectsIt:

    def test_a_browser_header_line_is_a_400_not_a_stored_cookie(self):
        from fastapi import HTTPException

        from app.api.admin import _validated_cookie_b64

        with pytest.raises(HTTPException) as caught:
            _validated_cookie_b64(BROWSER_HEADER_LINE.encode(), "facebook")
        assert caught.value.status_code == 400
        assert "Netscape" in caught.value.detail

    def test_the_rejection_does_not_quote_the_cookie(self):
        from fastapi import HTTPException

        from app.api.admin import _validated_cookie_b64

        with pytest.raises(HTTPException) as caught:
            _validated_cookie_b64(BROWSER_HEADER_LINE.encode(), "facebook")
        assert "c_user" not in caught.value.detail
        assert "100000000000000" not in caught.value.detail

    def test_a_valid_file_passes_and_comes_back_sanitized(self):
        from app.api.admin import _validated_cookie_b64

        b64, entries, dropped = _validated_cookie_b64(VALID_NETSCAPE.encode(), "facebook")
        assert (entries, dropped) == (3, 0)
        assert "c_user" in base64.b64decode(b64).decode()

    def test_a_mixed_file_is_stored_without_the_bad_line(self):
        from app.api.admin import _validated_cookie_b64

        mixed = (VALID_NETSCAPE + "\n" + BROWSER_HEADER_LINE).encode()
        b64, entries, dropped = _validated_cookie_b64(mixed, "facebook")
        assert (entries, dropped) == (3, 1)
        stored = base64.b64decode(b64).decode()
        assert "wd=1365x945" not in stored, (
            "the unusable line must not be persisted — it would be printed "
            "verbatim by yt-dlp on every request that loads this cookie"
        )
