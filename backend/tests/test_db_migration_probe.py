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


def test_admin_endpoints(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.api.admin_db_migration import verify_admin
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:TopSecret@127.0.0.1:1/x")
    client = TestClient(fastapi_app, raise_server_exceptions=False)
    assert client.get("/api/v1/admin/db-migration").status_code in (401, 403)
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        assert client.get("/api/v1/admin/db-migration").json()["probe"] is None
        body = client.post("/api/v1/admin/db-migration/probe")
        assert body.status_code == 200 and "TopSecret" not in body.text
        assert body.json()["probe"]["source"]["ok"] is False
        assert client.get("/api/v1/admin/db-migration").json()["probe"]["source"]["ok"] is False
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_copy_endpoint_needs_confirmation_and_one_run_at_a_time(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    from app.api.admin_db_migration import verify_admin
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/y")
    started = []
    monkeypatch.setattr(M, "copy_all", lambda **kw: started.append(kw))
    client = TestClient(fastapi_app, raise_server_exceptions=False)
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    try:
        base = "/api/v1/admin/db-migration/copy"
        assert client.post(base, json={}).status_code == 400
        r.set(M.COPY_LOCK, "1")
        assert client.post(base, json={"confirm": "CHEP"}).status_code == 409
        r.delete(M.COPY_LOCK)
        assert client.post(base, json={"confirm": "CHEP", "replace": True}).json() == {"started": True}
        import time
        for _ in range(50):
            if started:
                break
            time.sleep(0.05)
        assert started == [{"replace": True, "rc": r}]
        assert client.get(base).json()["running"] is False
    finally:
        fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_scrub_hides_passwords():
    assert "pw1" not in M._scrub("connect to postgresql://u:pw1@h/db failed", "postgresql://u:pw1@h/db")
    assert M._scrub("x postgres://a:b@c/d") == "x postgres://a:***@c/d"


def test_toc_kinds():
    assert M._toc_kind("4521; 0 0 POLICY public download_jobs p1 postgres") == "POLICY"
    assert M._toc_kind("4600; 0 16400 ROW SECURITY public download_jobs postgres") == "ROW SECURITY"
    assert M._toc_kind("6; 2615 2200 SCHEMA - public pg_database_owner") == "SCHEMA"
    assert M._toc_kind("4400; 0 0 COMMENT - SCHEMA public pg_database_owner") == "COMMENT_SCHEMA"
    assert M._toc_kind("3; 0 16384 TABLE DATA public plans postgres") == "TABLE DATA"
    assert M._toc_kind("; comment") == ""
