"""TDD tests for fix/signup-guard:
1. ensure_db_email_guard drops trigger 043 on target app DB ('dropped') and installs
   only on Supabase source_dsn ('installed'); warns clearly when SUPABASE_DB_URL is missing.
2. Redis lock ('startup:email_guard', SET NX EX 120) ensures concurrent workers run DDL
   only once; the other gets 'skipped_locked'; Redis outage degrades gracefully without crashing.
3. Migration 046_drop_email_guard_on_app_db.sql removes BEFORE INSERT trigger on app DB
   while keeping reject_disposable_email_signup() and signup_email_allowlist, so
   ensure_app_user('...', 'user@matbao.com', ...) succeeds without RaiseException.
4. check_duplicate_canonical_email prefers SUPABASE_DB_URL (connect_timeout=5, reuses pool
   if available else closes short connection), falls back to app DB, and returns False + logs
   warning on error without leaking credentials.
5. POST /api/v1/user/precheck-signup enforces @limiter.limit("10/minute") per IP (11th -> 429).
"""
from __future__ import annotations

import logging
import os
import pathlib
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Tuple

import fakeredis
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION_043 = REPO_ROOT / "database" / "migrations" / "043_strict_email_signup_guard.sql"
MIGRATION_046 = REPO_ROOT / "database" / "migrations" / "046_drop_email_guard_on_app_db.sql"


class _FakeCursor:
    def __init__(self, conn: "_FakeConn"):
        self.conn = conn
        self._next_row: Any = (True,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: Any = None):
        self.conn.executed.append((sql, params))
        if "to_regclass('auth.users')" in sql:
            self._next_row = (self.conn.has_auth_users,)
        elif "SELECT 1" in sql and "FROM auth.users" in sql:
            self._next_row = (1,) if self.conn.duplicate_match else None

    def fetchone(self):
        return self._next_row


class _FakeConn:
    def __init__(self, dsn: str, *, has_auth_users: bool = True, duplicate_match: bool = False):
        self.dsn = dsn
        self.has_auth_users = has_auth_users
        self.duplicate_match = duplicate_match
        self.autocommit = False
        self.closed = False
        self.executed: List[Tuple[str, Any]] = []

    def cursor(self):
        return _FakeCursor(self)

    def close(self):
        self.closed = True


# ── 1. ensure_db_email_guard: target -> DROP, source -> CREATE, missing source warns ──

def test_ensure_db_email_guard_drops_target_and_installs_source(monkeypatch):
    import psycopg2
    from app.core import db_migration, email_guard

    rc = fakeredis.FakeStrictRedis(decode_responses=True)
    conns: dict[str, list[_FakeConn]] = {"target": [], "source": []}

    monkeypatch.setattr(db_migration, "target_dsn", lambda: "postgresql://u:pw@app-db:5432/vidgrab")
    monkeypatch.setattr(db_migration, "source_dsn", lambda: "postgresql://u:pw@supa-db:6543/postgres")

    def fake_connect(dsn, **kwargs):
        c = _FakeConn(dsn)
        if "app-db" in dsn:
            conns["target"].append(c)
        else:
            conns["source"].append(c)
        return c

    monkeypatch.setattr(psycopg2, "connect", fake_connect)

    res = email_guard.ensure_db_email_guard(rc=rc)
    assert res["target_postgres"] == "dropped"
    assert res["supabase_postgres"] == "installed"

    # Target DB only drops the trigger — never creates it, never drops function/table
    assert len(conns["target"]) == 1
    target_sql = "\n".join(s for s, _ in conns["target"][0].executed)
    assert "DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users" in target_sql
    assert "CREATE TRIGGER on_auth_user_block_disposable" not in target_sql
    assert "DROP FUNCTION" not in target_sql
    assert "DROP TABLE" not in target_sql
    assert conns["target"][0].closed is True

    # Source DB installs the trigger + function + allowlist table
    assert len(conns["source"]) == 1
    source_sql = "\n".join(s for s, _ in conns["source"][0].executed)
    assert "CREATE TRIGGER on_auth_user_block_disposable" in source_sql
    assert "reject_disposable_email_signup" in source_sql
    assert conns["source"][0].closed is True


def test_ensure_db_email_guard_warns_clearly_when_supabase_db_url_missing(monkeypatch, caplog):
    import psycopg2
    from app.core import db_migration, email_guard

    rc = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(db_migration, "target_dsn", lambda: "postgresql://u:pw@app-db:5432/vidgrab")
    monkeypatch.setattr(db_migration, "source_dsn", lambda: "")
    monkeypatch.setattr(psycopg2, "connect", lambda dsn, **kw: _FakeConn(dsn))

    with caplog.at_level(logging.WARNING, logger="app.email_guard"):
        res = email_guard.ensure_db_email_guard(rc=rc)

    assert res["target_postgres"] == "dropped"
    assert res["supabase_postgres"] == "skipped_no_dsn"
    assert any("SUPABASE_DB_URL" in rec.message for rec in caplog.records)


# ── 2. Redis lock ('startup:email_guard', SET NX EX 120) + Redis failure resilience ──

def test_concurrent_ensure_db_email_guard_executes_ddl_once(monkeypatch):
    import psycopg2
    from app.core import db_migration, email_guard

    rc = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(db_migration, "target_dsn", lambda: "postgresql://u:pw@app-db:5432/vidgrab")
    monkeypatch.setattr(db_migration, "source_dsn", lambda: "postgresql://u:pw@supa-db:6543/postgres")

    connect_lock = threading.Lock()
    connect_calls: List[str] = []

    def fake_connect(dsn, **kwargs):
        with connect_lock:
            connect_calls.append(dsn)
        return _FakeConn(dsn)

    monkeypatch.setattr(psycopg2, "connect", fake_connect)

    barrier = threading.Barrier(2)

    def worker_call():
        barrier.wait(timeout=5)
        return email_guard.ensure_db_email_guard(rc=rc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(worker_call)
        f2 = pool.submit(worker_call)
        results = [f1.result(timeout=5), f2.result(timeout=5)]

    ttl = rc.ttl("startup:email_guard")
    assert 0 < ttl <= 120

    ran = [r for r in results if r.get("target_postgres") == "dropped" and r.get("supabase_postgres") == "installed"]
    locked = [r for r in results if r.get("status") == "skipped_locked" or r == {"status": "skipped_locked"}]
    assert len(ran) == 1
    assert len(locked) == 1
    # Only the winning worker connected to target + source (2 connections total, not 4)
    assert len(connect_calls) == 2


def test_ensure_db_email_guard_runs_safely_when_redis_errors(monkeypatch):
    import psycopg2
    from app.core import db_migration, email_guard, redis_client

    def broken_redis():
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(redis_client, "get_redis", broken_redis)
    monkeypatch.setattr(db_migration, "target_dsn", lambda: "postgresql://u:pw@app-db:5432/vidgrab")
    monkeypatch.setattr(db_migration, "source_dsn", lambda: "postgresql://u:pw@supa-db:6543/postgres")
    monkeypatch.setattr(psycopg2, "connect", lambda dsn, **kw: _FakeConn(dsn))

    res = email_guard.ensure_db_email_guard()
    assert res["target_postgres"] == "dropped"
    assert res["supabase_postgres"] == "installed"


# ── 3. Migration 046 + ensure_app_user allows user@matbao.com on app DB ──

class _SimulatedAppDbClient:
    """Simulates PostgreSQL BEFORE INSERT trigger execution before ON CONFLICT on auth.users."""

    def __init__(self):
        self.trigger_043_active = True
        self.function_043_exists = True
        self.allowlist_table_exists = True
        self.auth_users: dict[str, dict[str, Any]] = {}

    def apply_sql(self, sql: str) -> None:
        if "DROP FUNCTION" in sql.upper():
            self.function_043_exists = False
        if "DROP TABLE" in sql.upper() and "signup_email_allowlist" in sql:
            self.allowlist_table_exists = False
        if "DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users" in sql:
            self.trigger_043_active = False

    def run_sql(self, sql: str, params: Any = None):
        if "INSERT INTO auth.users" in sql:
            uid, email, confirmed = params
            # PostgreSQL runs BEFORE INSERT triggers BEFORE checking ON CONFLICT!
            if self.trigger_043_active:
                from app.core.email_guard import ALLOWED_EMAIL_DOMAINS
                domain = email.lower().split("@", 1)[1] if "@" in email else ""
                if domain and domain not in ALLOWED_EMAIL_DOMAINS:
                    raise RuntimeError(f"VIDGRAB_DISPOSABLE_EMAIL: Email domain ({domain}) is not in the allowed provider list")
            self.auth_users[str(uid)] = {"id": str(uid), "email": email, "email_confirmed_at": confirmed}
            return [(str(uid),)]
        return []


def test_migration_046_drops_trigger_and_unblocks_ensure_app_user(monkeypatch):
    from app.core import database, db_migration, redis_client

    assert MIGRATION_046.is_file(), "046_drop_email_guard_on_app_db.sql must exist"
    sql_046 = MIGRATION_046.read_text(encoding="utf-8")
    assert "DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users" in sql_046
    assert "DROP FUNCTION" not in sql_046.upper()
    assert "DROP TABLE" not in sql_046.upper()

    sim_db = _SimulatedAppDbClient()
    rc = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(database, "using_postgres", lambda: True)
    monkeypatch.setattr(database, "get_service_client", lambda: sim_db)
    monkeypatch.setattr(redis_client, "get_redis", lambda: rc)

    uid = str(uuid.uuid4())
    # Before 046: BEFORE INSERT trigger blocks syncing existing custom-domain account
    with pytest.raises(RuntimeError, match="VIDGRAB_DISPOSABLE_EMAIL"):
        db_migration.ensure_app_user(uid, "user@matbao.com", "2026-08-19T08:35:11Z")

    # Apply 046 SQL on app DB: trigger dropped, function & allowlist preserved
    sim_db.apply_sql(sql_046)
    assert sim_db.trigger_043_active is False
    assert sim_db.function_043_exists is True
    assert sim_db.allowlist_table_exists is True

    # Now ensure_app_user succeeds for user@matbao.com (both insert and conflict update)
    db_migration.ensure_app_user(uid, "user@matbao.com", "2026-08-19T08:35:11Z")
    assert sim_db.auth_users[uid]["email"] == "user@matbao.com"


@pytest.mark.skipif(not os.getenv("PG_TEST_DSN"), reason="PG_TEST_DSN not set")
def test_migration_046_against_real_postgres(monkeypatch):
    import psycopg2
    from app.core import database, db_migration, redis_client
    from app.core.pg_client import PgClient

    dsn = os.environ["PG_TEST_DSN"]
    compat_sql = (REPO_ROOT / "backend" / "app" / "core" / "supabase_compat.sql").read_text(encoding="utf-8")
    sql_043 = MIGRATION_043.read_text(encoding="utf-8")
    sql_046 = MIGRATION_046.read_text(encoding="utf-8")

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(compat_sql)
            cur.execute(sql_043)
            cur.execute(sql_046)
            cur.execute(
                "SELECT tgname FROM pg_trigger WHERE tgrelid = 'auth.users'::regclass AND tgname = 'on_auth_user_block_disposable'"
            )
            assert cur.fetchall() == []
            cur.execute("SELECT to_regprocedure('public.reject_disposable_email_signup()') IS NOT NULL")
            assert cur.fetchone()[0] is True
            cur.execute("SELECT to_regclass('public.signup_email_allowlist') IS NOT NULL")
            assert cur.fetchone()[0] is True
    finally:
        conn.close()

    pg = PgClient(dsn)
    rc = fakeredis.FakeStrictRedis(decode_responses=True)
    monkeypatch.setattr(database, "using_postgres", lambda: True)
    monkeypatch.setattr(database, "get_service_client", lambda: pg)
    monkeypatch.setattr(redis_client, "get_redis", lambda: rc)

    uid = str(uuid.uuid4())
    db_migration.ensure_app_user(uid, "user@matbao.com", "2026-08-19T08:35:11Z")
    rows = pg.run_sql("SELECT email FROM auth.users WHERE id = %s::uuid", (uid,))
    assert rows and rows[0][0] == "user@matbao.com"


# ── 4. check_duplicate_canonical_email prefers SUPABASE_DB_URL ──

def test_check_duplicate_canonical_email_prefers_source_dsn(monkeypatch):
    import psycopg2
    from app.core import database, db_migration, email_guard

    captured_kwargs: dict[str, Any] = {}
    created_conns: List[_FakeConn] = []

    def fake_connect(dsn, **kwargs):
        captured_kwargs["dsn"] = dsn
        captured_kwargs.update(kwargs)
        c = _FakeConn(dsn, duplicate_match=True)
        created_conns.append(c)
        return c

    def should_not_call_app_db():
        raise AssertionError("App DB should not be queried when SUPABASE_DB_URL is set")

    monkeypatch.setattr(db_migration, "source_dsn", lambda: "postgresql://u:secretpw@supa-db:6543/postgres")
    monkeypatch.setattr(psycopg2, "connect", fake_connect)
    monkeypatch.setattr(database, "using_postgres", lambda: False)
    monkeypatch.setattr(database, "get_service_client", should_not_call_app_db)

    assert email_guard.check_duplicate_canonical_email("exampleuser@gmail.com") is True
    assert captured_kwargs["dsn"] == "postgresql://u:secretpw@supa-db:6543/postgres"
    assert captured_kwargs.get("connect_timeout") == 5
    assert len(created_conns) == 1
    assert created_conns[0].closed is True


def test_check_duplicate_canonical_email_reuses_existing_pool_when_available(monkeypatch):
    import psycopg2
    from app.core import db_migration, email_guard

    class _FakePool:
        def __init__(self):
            self.borrowed = 0
            self.returned = 0
            self.conn = _FakeConn("postgresql://u:pw@supa-db:6543/postgres", duplicate_match=True)

        def getconn(self):
            self.borrowed += 1
            return self.conn

        def putconn(self, c):
            assert c is self.conn
            self.returned += 1

    fake_pool = _FakePool()
    monkeypatch.setattr(db_migration, "source_dsn", lambda: "postgresql://u:pw@supa-db:6543/postgres")
    monkeypatch.setattr(email_guard, "_SOURCE_POOL", fake_pool)
    monkeypatch.setattr(
        psycopg2,
        "connect",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Must reuse existing pool, not open new conn")),
    )

    assert email_guard.check_duplicate_canonical_email("exampleuser@gmail.com") is True
    assert fake_pool.borrowed == 1
    assert fake_pool.returned == 1


def test_check_duplicate_canonical_email_falls_back_to_app_db(monkeypatch):
    from app.core import database, db_migration, email_guard

    class _DummyPg:
        def __init__(self):
            self.calls = 0

        def run_sql(self, sql, params=None):
            self.calls += 1
            return [(1,)]

    pg = _DummyPg()
    monkeypatch.setattr(db_migration, "source_dsn", lambda: "")
    monkeypatch.setattr(database, "using_postgres", lambda: True)
    monkeypatch.setattr(database, "get_service_client", lambda: pg)

    assert email_guard.check_duplicate_canonical_email("exampleuser@gmail.com") is True
    assert pg.calls == 1


def test_check_duplicate_canonical_email_error_returns_false_and_warns_safely(monkeypatch, caplog):
    import psycopg2
    from app.core import db_migration, email_guard

    def boom(dsn, **kwargs):
        raise psycopg2.OperationalError("connection to postgresql://u:supersecret123@supa-db/postgres timed out")

    monkeypatch.setattr(db_migration, "source_dsn", lambda: "postgresql://u:supersecret123@supa-db:6543/postgres")
    monkeypatch.setattr(psycopg2, "connect", boom)

    with caplog.at_level(logging.WARNING, logger="app.email_guard"):
        assert email_guard.check_duplicate_canonical_email("exampleuser@gmail.com") is False

    assert any("check_duplicate_canonical_email" in rec.message for rec in caplog.records)
    for rec in caplog.records:
        assert "supersecret123" not in rec.getMessage()


# ── 5. POST /api/v1/user/precheck-signup rate limit 10/minute per IP ──

def test_precheck_signup_rate_limited_10_per_minute_per_ip(monkeypatch):
    import app.main as m
    from app.core import email_guard

    monkeypatch.setattr(email_guard, "check_duplicate_canonical_email", lambda _e: False)
    client = TestClient(m.app)

    ip1 = "198.51.100.201"
    ip2 = "198.51.100.202"

    statuses = [
        client.post(
            "/api/v1/user/precheck-signup",
            json={"email": f"validuser{i}@gmail.com"},
            headers={"x-forwarded-for": ip1},
        ).status_code
        for i in range(11)
    ]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429

    # Different IP has its own 10/minute bucket
    r_other = client.post(
        "/api/v1/user/precheck-signup",
        json={"email": "otherip@gmail.com"},
        headers={"x-forwarded-for": ip2},
    )
    assert r_other.status_code == 200
