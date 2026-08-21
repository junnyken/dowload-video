"""
Backup task — the parts that decide whether a backup is trustworthy.

The failure mode that matters here is not "the task crashed", which is loud.
It is "the task reported success while quietly saving fewer rows than exist",
which looks identical to a good backup right up until a restore.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.tasks import backup_tasks as bt


class TestOrderColumn:
    """Offset paging with no ORDER BY can repeat or skip rows between pages."""

    def test_prefers_id(self):
        assert bt._order_column({"id": 1, "created_at": "x", "name": "n"}) == "id"

    def test_falls_back_through_known_columns(self):
        assert bt._order_column({"created_at": "x"}) == "created_at"
        assert bt._order_column({"user_id": "u", "downloads_today": 3}) == "user_id"

    def test_returns_none_when_nothing_stable_exists(self):
        assert bt._order_column({"metric": "m", "value": 1}) is None


class TestDumpTable:

    def _client(self, pages):
        """pages: list of (status_code, json) returned in order."""
        client = MagicMock()
        responses = []
        for status, body in pages:
            r = MagicMock()
            r.status_code = status
            r.json.return_value = body
            r.text = ""
            responses.append(r)
        client.get.side_effect = responses
        return client

    def test_empty_table_is_not_an_error(self):
        rows, warn = bt._dump_table(self._client([(200, [])]), "u", "k", "t")
        assert rows == [] and warn is None

    def test_pages_until_a_short_batch(self):
        full = [{"id": i} for i in range(bt.PAGE)]
        tail = [{"id": bt.PAGE}]
        client = self._client([(200, [{"id": 0}]), (200, full), (200, tail)])

        rows, warn = bt._dump_table(client, "u", "k", "t")

        assert len(rows) == bt.PAGE + 1
        assert warn is None

    def test_hitting_the_row_cap_is_reported_not_swallowed(self, monkeypatch):
        """A silently truncated backup is worse than none — you stop worrying."""
        monkeypatch.setattr(bt, "MAX_ROWS_PER_TABLE", bt.PAGE)
        full = [{"id": i} for i in range(bt.PAGE)]
        client = self._client([(200, [{"id": 0}]), (200, full)])

        rows, warn = bt._dump_table(client, "u", "k", "big")

        assert len(rows) == bt.PAGE
        assert warn is not None and "INCOMPLETE" in warn

    def test_http_error_surfaces_as_a_warning(self):
        client = MagicMock()
        r = MagicMock()
        r.status_code = 403
        r.text = "denied"
        client.get.return_value = r

        rows, warn = bt._dump_table(client, "u", "k", "secret")

        assert rows == []
        assert warn and "403" in warn


class TestBackupTaskGuards:

    def test_missing_credentials_does_not_raise(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        result = bt.backup_database_daily()
        assert result["ok"] is False
        assert "missing" in result["error"]


class TestOffsiteCopy:
    """The Supabase Storage copy lives in the same project as the data it
    protects. Whether a genuinely off-site copy exists must be reported, never
    assumed."""

    S3_VARS = ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET",
               "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY")

    def test_no_target_configured_is_not_an_error(self, monkeypatch):
        for v in self.S3_VARS:
            monkeypatch.delenv(v, raising=False)
        assert bt._s3_configured() is False
        assert bt._upload_offsite("f.gz", b"x") is None

    def test_partial_configuration_counts_as_unconfigured(self, monkeypatch):
        for v in self.S3_VARS:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("BACKUP_S3_ENDPOINT", "https://r2.example.com")
        monkeypatch.setenv("BACKUP_S3_BUCKET", "b")
        assert bt._s3_configured() is False, (
            "half-configured must not read as 'off-site backup exists'"
        )

    def test_configured_but_failing_upload_is_reported(self, monkeypatch):
        for v in self.S3_VARS:
            monkeypatch.setenv(v, "x")

        import sys
        fake = MagicMock()
        fake.client.side_effect = RuntimeError("bucket unreachable")
        monkeypatch.setitem(sys.modules, "boto3", fake)

        err = bt._upload_offsite("f.gz", b"x")
        assert err and "FAILED" in err, (
            "a silent off-site failure would leave you believing in a backup "
            "that does not exist"
        )

    def test_configured_and_working_returns_no_error(self, monkeypatch):
        for v in self.S3_VARS:
            monkeypatch.setenv(v, "x")

        import sys
        fake = MagicMock()
        monkeypatch.setitem(sys.modules, "boto3", fake)

        assert bt._upload_offsite("f.gz", b"payload") is None
        fake.client.return_value.put_object.assert_called_once()
        kwargs = fake.client.return_value.put_object.call_args.kwargs
        assert kwargs["Key"] == "vidgrab/f.gz"
        assert kwargs["Body"] == b"payload"
