"""Tests for the Supabase keep-alive ping — must never raise, even on failure."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tasks.keepalive_tasks import supabase_keepalive_ping


def test_keepalive_ping_queries_a_lightweight_table():
    db = MagicMock()
    with patch("app.tasks.keepalive_tasks.get_service_client", return_value=db):
        supabase_keepalive_ping()

    db.table.assert_called_once_with("user_usage")
    db.table.return_value.select.assert_called_once_with("id")
    db.table.return_value.select.return_value.limit.assert_called_once_with(1)


def test_keepalive_ping_never_raises_on_db_failure():
    db = MagicMock()
    db.table.side_effect = Exception("boom")
    with patch("app.tasks.keepalive_tasks.get_service_client", return_value=db):
        supabase_keepalive_ping()  # must not raise
