"""app.core.db_migration.probe: read-only, no password anywhere in its output."""
from __future__ import annotations

import json
import os

import fakeredis
import pytest

from app.core import db_migration as M

DSN = os.getenv("PG_TEST_DSN", "")


def test_describe_dsn_hides_the_password():
    d = M.describe_dsn("postgresql://postgres.abcdef:S3cret!pw@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres")
    assert d["kind"] == "supabase-pooler" and d["port"] == 6543 and d["user"] == "postgres"
    assert "S3cret" not in json.dumps(d) and "abcdef" not in json.dumps(d)
    assert M.describe_dsn("postgresql://u:p@db.xyz.supabase.co:5432/postgres")["kind"] == "supabase-direct"
    assert M.describe_dsn("") == {"set": False}


def test_unreachable_source_is_reported_not_raised(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:TopSecret@127.0.0.1:1/x")
    monkeypatch.delenv("APP_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rc = fakeredis.FakeRedis(decode_responses=True)
    rep = M.probe(rc)
    assert rep["source"]["ok"] is False and "connect" in rep["source"]["error"]
    out = capsys.readouterr().out + rc.get(M.PROBE_KEY)
    assert "TopSecret" not in out


@pytest.mark.skipif(not DSN, reason="PG_TEST_DSN not set")
def test_probe_reads_both_databases(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", DSN)
    monkeypatch.setenv("APP_DATABASE_URL", DSN)
    rep = M.probe(fakeredis.FakeRedis(decode_responses=True))
    assert rep["source"]["ok"] and rep["target"]["ok"]
    assert rep["source"]["server_version"].startswith("16")
    assert isinstance(rep["source"]["tables"], list)
