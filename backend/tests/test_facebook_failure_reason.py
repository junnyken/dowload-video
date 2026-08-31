"""
Every Facebook failure was reported as "Facebook yêu cầu đăng nhập".

A user reported that a Facebook reel would not download. The link was public and
downloaded fine outside the server — 1.7 MB, format `hd`, no cookies needed — so
whatever went wrong was server-side, and the message was simply wrong.

It was wrong by construction. Base opts set `ignoreerrors`, so extract_info
returns None instead of raising: the `except primary_err` branch that logs the
reason never ran, and the only logger was attached to YouTube. The real yt-dlp
error reached neither the user nor the log, and the code then asserted a cause
it had never established — the same sentence for a deleted video, a broken
extractor and a blocked server IP.

These tests cover the reporting (the reason must survive) and the recovery
(Facebook must retry without the cookie / with a browser fingerprint before
declaring a public video private).
"""

from __future__ import annotations

import pytest

from app.services import downloader


class TestTheReasonSurvives:

    def test_logger_collects_errors_into_a_caller_owned_sink(self):
        sink: list = []
        logger = downloader._YTDLPLogger("/FB", sink=sink, verbose=False)
        logger.error("ERROR: [facebook] 123: Cannot parse data")
        assert sink == ["ERROR: [facebook] 123: Cannot parse data"]

    def test_a_quiet_logger_still_reports_warnings_and_errors(self, capsys):
        logger = downloader._YTDLPLogger("/FB", verbose=False)
        logger.debug("[download] 12% of 1.70MiB")
        logger.info("Downloading webpage")
        logger.warning("something looks off")
        logger.error("it broke")
        out = capsys.readouterr().out
        assert "12% of 1.70MiB" not in out and "Downloading webpage" not in out
        assert "something looks off" in out and "it broke" in out

    def test_reason_strips_the_boilerplate_meant_for_bug_reporters(self):
        reason = downloader._ytdlp_failure_reason([
            "ERROR: [facebook] 1608261137553863: Cannot parse data; please "
            "report this issue on https://github.com/yt-dlp/yt-dlp/issues?q= , "
            "filling out the appropriate issue template. Confirm you are on "
            "the latest version using yt-dlp -U"
        ])
        assert reason == "[facebook] 1608261137553863: Cannot parse data"

    def test_no_errors_means_no_reason_rather_than_an_empty_parenthetical(self):
        assert downloader._ytdlp_failure_reason([]) == ""
        assert downloader._ytdlp_failure_reason(None) == ""
        assert downloader._ytdlp_failure_reason(["", "   "]) == ""

    def test_a_bare_extractor_prefix_is_not_a_reason(self):
        """"[facebook] 123:" with the message stripped explains nothing —
        keep looking for an error line that actually says something."""
        assert downloader._ytdlp_failure_reason(["[facebook] 123: "]) == ""
        assert downloader._ytdlp_failure_reason(
            ["ERROR: Unsupported URL: https://x.invalid/v", "[facebook] 123:"]
        ) == "Unsupported URL: https://x.invalid/v"

    def test_a_long_reason_is_trimmed_not_dropped(self):
        reason = downloader._ytdlp_failure_reason(["ERROR: " + "x" * 500])
        assert len(reason) == 300 and reason.endswith("...")


class TestEveryPlatformGetsALogger:

    def test_facebook_opts_carry_a_logger_and_the_shared_sink(self):
        """Regression: only YouTube had one, so a Facebook failure was silent."""
        sink: list = []
        opts = downloader._get_base_opts(
            "https://www.facebook.com/reel/1608261137553863",
            phase="metadata", quality="video_fast", error_sink=sink,
        )
        assert isinstance(opts.get("logger"), downloader._YTDLPLogger)
        opts["logger"].error("boom")
        assert sink == ["boom"]

    def test_youtube_keeps_its_verbose_logging(self):
        opts = downloader._get_base_opts(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", phase="metadata")
        assert opts["logger"]._prefix == "/YT"
        assert opts["logger"]._verbose is True

    def test_other_platforms_log_warnings_only(self):
        opts = downloader._get_base_opts(
            "https://www.facebook.com/reel/123", phase="metadata")
        assert opts["logger"]._prefix == "/FB"
        assert opts["logger"]._verbose is False


class TestFacebookRetryLadder:

    def test_a_cookie_is_retried_without_the_cookie(self):
        """A stale/checkpointed pool cookie turns a public video into a login
        wall. Anonymous is what everyone else gets, and it works for public
        content — try it before telling the user the video is private."""
        plan = downloader._facebook_retry_plan(
            {"format": "best", "cookiefile": "/tmp/facebook_cookies.txt"})
        assert plan, "a cookied attempt must have at least one retry"
        label, opts = plan[0]
        assert "cookie" in label
        assert "cookiefile" not in opts
        assert opts["format"] == "best"

    def test_the_original_opts_are_never_mutated(self):
        original = {"format": "best", "cookiefile": "/tmp/c.txt"}
        downloader._facebook_retry_plan(original)
        assert original == {"format": "best", "cookiefile": "/tmp/c.txt"}

    def test_impersonation_is_offered_when_curl_cffi_is_installed(self):
        target = downloader._impersonate_target()
        plan = downloader._facebook_retry_plan({"format": "best"})
        if target is None:
            assert plan == []
        else:
            assert [o.get("impersonate") for _, o in plan] == [target]

    def test_no_cookie_and_no_curl_cffi_means_no_pointless_retry(self, monkeypatch):
        monkeypatch.setattr(downloader, "_impersonate_target", lambda: None)
        assert downloader._facebook_retry_plan({"format": "best"}) == []


class _StubYDL:
    """Stands in for yt_dlp.YoutubeDL: reports an error the way yt-dlp does
    under `ignoreerrors` — through the logger, returning None, raising nothing."""

    calls: list = []
    error_line = ("ERROR: [facebook] 1608261137553863: Cannot parse data; "
                  "please report this issue on https://github.com/yt-dlp/yt-dlp/issues")

    def __init__(self, opts):
        self.opts = opts
        type(self).calls.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        logger = self.opts.get("logger")
        if logger is not None:
            logger.error(self.error_line)
        return None


class TestTheMessageTellsTheTruth:

    @pytest.fixture
    def failing_facebook(self, monkeypatch):
        _StubYDL.calls = []
        monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _StubYDL)
        monkeypatch.setattr(downloader, "is_cobalt_available", lambda: False)
        with pytest.raises(ValueError) as exc:
            downloader._extract_video_info_impl(
                "https://www.facebook.com/reel/1608261137553863",
                quality="video_fast",
            )
        return str(exc.value)

    def test_the_real_yt_dlp_reason_reaches_the_user(self, failing_facebook):
        assert "Cannot parse data" in failing_facebook
        assert "Lý do kỹ thuật" in failing_facebook

    def test_it_no_longer_asserts_a_login_it_never_established(self, failing_facebook):
        assert "yêu cầu đăng nhập" not in failing_facebook
        assert "Không thể tải video Facebook" in failing_facebook

    def test_the_bug_reporting_boilerplate_is_not_shown_to_the_user(self, failing_facebook):
        assert "github.com" not in failing_facebook
        assert "please report this issue" not in failing_facebook

    def test_facebook_retried_before_giving_up(self, failing_facebook):
        """The first attempt failing is not evidence the video is private."""
        if downloader._impersonate_target() is None:
            pytest.skip("curl_cffi not installed — no retry is possible")
        assert len(_StubYDL.calls) >= 2
        assert any(o.get("impersonate") is not None for o in _StubYDL.calls)

    def test_every_attempt_shares_one_error_sink(self, failing_facebook):
        sinks = {id(o["logger"].errors) for o in _StubYDL.calls if o.get("logger")}
        assert len(sinks) == 1, "a retry's error must survive into the message"
