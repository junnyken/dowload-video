"""
Production logs carried the Telegram bot token in plaintext.

httpx logs every request it makes at INFO — "HTTP Request: POST <full url>" —
and a Telegram bot token lives in the URL path, so every alert the app sent
wrote its own credential to the log:

    HTTP Request: POST https://api.telegram.org/bot<id>:<secret>/sendMessage

Anyone with log access could take over the alerting bot. Found by reading
production logs while verifying an unrelated deploy, which is exactly how the
Facebook session cookie leak was found: a secret reaching the log through a
path nobody was watching.

Two details decide whether a fix actually works here:

  * httpx passes the URL as a %-arg, not inside the message string. A redactor
    that only rewrites record.msg passes its own tests and changes nothing.
  * configure_logging() runs in app/main.py, which the Celery worker never
    imports — and the leaking lines were the worker's. A fix living in the
    JSON formatter would have missed the only place it mattered.
"""

from __future__ import annotations

import logging

import pytest

from app.core.structured_log import install_secret_redaction, redact_secrets


TELEGRAM_URL = (
    "https://api.telegram.org/bot8534211781:AAFy6EwC9fHD6gRixXhUet70eTlgqwvRVFc/sendMessage"
)


class TestTheLeakedShape:

    def test_the_telegram_token_is_removed(self):
        out = redact_secrets(f"HTTP Request: POST {TELEGRAM_URL}")
        assert "AAFy6EwC9fHD6gRixXhUet70eTlgqwvRVFc" not in out
        assert "<redacted>" in out

    def test_the_line_stays_useful(self):
        """Silencing httpx would have removed this line entirely — and those
        same lines are how the broken stale-job query was spotted."""
        out = redact_secrets(f"HTTP Request: POST {TELEGRAM_URL}")
        assert "api.telegram.org" in out
        assert "sendMessage" in out
        assert "bot8534211781" in out, "the bot id is not the secret; keep it readable"

    def test_supabase_diagnostics_survive_untouched(self):
        line = (
            'HTTP Request: GET https://x.supabase.co/rest/v1/download_jobs'
            '?select=id&job_stage=eq.stale&status=eq.processing&limit=1 "HTTP/2 200 OK"'
        )
        assert redact_secrets(line) == line


class TestOtherCredentialShapes:

    @pytest.mark.parametrize("secret,line", [
        ("supersecretvalue123456", "GET https://api.example.com/x?api_key=supersecretvalue123456"),
        ("supersecretvalue123456", "GET https://api.example.com/x?a=1&access_token=supersecretvalue123456&b=2"),
        ("hunter2hunter2hunter2", "https://user:hunter2hunter2hunter2@example.com/path"),
        ("abcdefghijklmnopqrstuvwx", "Authorization: Bearer abcdefghijklmnopqrstuvwx"),
        ("AAAABBBBCCCCDDDDEEEEFFFF", "token github_pat_AAAABBBBCCCCDDDDEEEEFFFF used"),
    ])
    def test_it_is_gone(self, secret, line):
        assert secret not in redact_secrets(line)

    def test_ordinary_urls_are_left_alone(self):
        """Over-redaction makes logs useless, which is its own outage."""
        for line in (
            "HTTP Request: GET https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "downloaded /app/downloads/2033078270746905_2312344482923926v_a1b2c3.mp4",
            "[Downloader] yt-dlp downloaded: 1080p, 12.3MB, format=137+140",
        ):
            assert redact_secrets(line) == line


class TestItWorksThroughTheLoggingStack:
    """The property that matters. redact_secrets() being correct proves
    nothing if records never reach it."""

    @pytest.fixture(autouse=True)
    def _installed(self):
        install_secret_redaction()

    def test_a_percent_arg_is_redacted(self, caplog):
        """This is how httpx actually logs — the URL is an arg, not part of
        the message. A redactor that only touches record.msg would pass every
        other test in this file and leak in production."""
        logger = logging.getLogger("httpx")
        with caplog.at_level(logging.INFO, logger="httpx"):
            logger.info('HTTP Request: %s %s "%s"', "POST", TELEGRAM_URL, "HTTP/1.1 200 OK")

        rendered = caplog.records[-1].getMessage()
        assert "AAFy6EwC9fHD6gRixXhUet70eTlgqwvRVFc" not in rendered
        assert "api.telegram.org" in rendered

    def test_the_arg_is_redacted_when_it_is_not_a_string(self, caplog):
        """The one that was missed, and the reason the first fix shipped and
        did nothing.

        httpx does not log a str — it logs request.url, an httpx.URL object.
        The first version checked isinstance(a, str), walked past it, and
        production kept printing the token while this file stayed green
        because every test here handed it a plain string.
        """
        httpx = pytest.importorskip("httpx")
        request = httpx.Request("POST", TELEGRAM_URL)
        assert not isinstance(request.url, str), (
            "httpx changed: this test only means something while the URL is "
            "an object rather than a string"
        )

        logger = logging.getLogger("httpx")
        with caplog.at_level(logging.INFO, logger="httpx"):
            # Byte-for-byte the call httpx._client makes.
            logger.info(
                'HTTP Request: %s %s "%s %d %s"',
                request.method, request.url, "HTTP/1.1", 200, "OK",
            )

        rendered = caplog.records[-1].getMessage()
        assert "AAFy6EwC9fHD6gRixXhUet70eTlgqwvRVFc" not in rendered, (
            "the URL object slipped through — this is exactly what leaked"
        )
        assert "api.telegram.org" in rendered
        assert "200" in rendered, "the %d argument must still render"

    def test_numeric_args_are_left_alone(self, caplog):
        """Turning an int into a str breaks a '%d' template. A redactor that
        corrupts unrelated log lines is worse than the leak."""
        logger = logging.getLogger("numbers")
        with caplog.at_level(logging.INFO, logger="numbers"):
            logger.info("status %d in %.2f seconds", 503, 1.5)
        assert caplog.records[-1].getMessage() == "status 503 in 1.50 seconds"

    def test_a_plain_message_is_redacted(self, caplog):
        logger = logging.getLogger("anything")
        with caplog.at_level(logging.INFO, logger="anything"):
            logger.info(f"sending via {TELEGRAM_URL}")
        assert "AAFy6EwC9fHD6gRixXhUet70eTlgqwvRVFc" not in caplog.records[-1].getMessage()

    def test_installing_twice_does_not_stack_factories(self):
        """Called from both configure_logging() and celery_app import."""
        install_secret_redaction()
        install_secret_redaction()
        logger = logging.getLogger("dup-check")
        record = logger.makeRecord(
            "dup-check", logging.INFO, __file__, 1, "%s", (TELEGRAM_URL,), None
        )
        assert "AAFy6EwC9fHD6gRixXhUet70eTlgqwvRVFc" not in record.getMessage()

    def test_a_bad_record_does_not_break_logging(self, caplog):
        """A logging hook must never be the reason something fails."""
        logger = logging.getLogger("weird")
        with caplog.at_level(logging.INFO, logger="weird"):
            logger.info("value=%s", object())
        assert caplog.records, "the record was dropped"


class TestTheWorkerGetsItToo:
    """The leak was in Celery worker output, and the worker never imports
    app.main — so importing the Celery app has to be enough on its own."""

    def test_importing_celery_app_installs_redaction(self):
        import app.core.celery_app  # noqa: F401
        import app.core.structured_log as sl

        assert sl._redaction_installed, (
            "the worker would format its own logs with no redaction, which is "
            "the exact process that leaked the token"
        )
