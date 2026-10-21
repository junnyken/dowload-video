"""
Moving VidGrab's data from Supabase to the Vibe Host Postgres (21/10/2026)
==========================================================================

docs/MIGRATION-DB-VIBEHOST.md. Runs inside the backend container, the only
place that holds both connection strings:

  SUPABASE_DB_URL                  source (Supabase Postgres, set by the owner)
  APP_DATABASE_URL or DATABASE_URL target (the Postgres Vibe Host provides)

probe() is read-only and cheap: it reads catalog statistics (pg_class.reltuples
estimates, never count(*)), so it costs the Supabase disk IO budget almost
nothing. Its report goes to Redis (dbmig:probe, 7 days) and to the log
as one line. Never logs or returns a password: hosts are reported by kind.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger("app.db_migration")

PROBE_KEY = "dbmig:probe"
LOCK_KEY = "dbmig:probe:lock"


def source_dsn() -> str:
    return os.getenv("SUPABASE_DB_URL", "").strip()


def target_dsn() -> str:
    return (os.getenv("APP_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def describe_dsn(dsn: str) -> Dict[str, Any]:
    """Where a DSN points, without the password or the full host."""
    if not dsn:
        return {"set": False}
    try:
        u = urlparse(dsn)
        host = u.hostname or ""
        kind = ("supabase-pooler" if "pooler.supabase.com" in host
                else "supabase-direct" if host.endswith(".supabase.co")
                else "other")
        return {"set": True, "scheme": u.scheme, "kind": kind, "port": u.port,
                "user": (u.username or "").split(".")[0], "database": (u.path or "/").lstrip("/"),
                "host_tail": ".".join(host.split(".")[-2:]) if host else ""}
    except Exception:  # noqa: BLE001
        return {"set": True, "parse_error": True}


def _inspect(dsn: str, *, with_auth: bool) -> Dict[str, Any]:
    import psycopg2  # noqa: PLC0415
    started = time.monotonic()
    out: Dict[str, Any] = {"target": describe_dsn(dsn)}
    try:
        conn = psycopg2.connect(dsn, connect_timeout=15,
                                options="-c statement_timeout=20000")
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"connect: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
        return out
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SHOW server_version")
            out["server_version"] = cur.fetchone()[0]
            cur.execute("SELECT pg_database_size(current_database())")
            out["db_size_mb"] = round(cur.fetchone()[0] / 1048576, 1)
            cur.execute(
                """SELECT c.relname, c.relkind, greatest(c.reltuples, 0)::bigint,
                          pg_total_relation_size(c.oid)
                     FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p')
                    ORDER BY pg_total_relation_size(c.oid) DESC""")
            out["tables"] = [{"name": r[0], "kind": {"r": "table", "v": "view", "m": "matview", "p": "table"}[r[1]],
                              "rows_est": int(r[2]), "size_kb": int(r[3] // 1024)} for r in cur.fetchall()]
            cur.execute("SELECT extname FROM pg_extension ORDER BY 1")
            out["extensions"] = [r[0] for r in cur.fetchall()]
            if with_auth:
                cur.execute("SELECT to_regclass('auth.users') IS NOT NULL")
                if cur.fetchone()[0]:
                    cur.execute("SELECT greatest(reltuples, 0)::bigint FROM pg_class WHERE oid = 'auth.users'::regclass")
                    out["auth_users_est"] = int(cur.fetchone()[0])
                    cur.execute("SELECT has_table_privilege('auth.users', 'SELECT')")
                    out["auth_users_readable"] = bool(cur.fetchone()[0])
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    finally:
        conn.close()
    out["took_s"] = round(time.monotonic() - started, 2)
    return out


def _tool_version(tool: str) -> Optional[str]:
    path = shutil.which(tool)
    if not path:
        return None
    try:
        return subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return path


def probe(rc=None) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "at": time.time(),
        "pg_dump": _tool_version("pg_dump"),
        "psql": _tool_version("psql"),
        "source": _inspect(source_dsn(), with_auth=True) if source_dsn() else {"target": {"set": False}},
        "target": _inspect(target_dsn(), with_auth=False) if target_dsn() else {"target": {"set": False}},
    }
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        (rc or get_redis()).set(PROBE_KEY, json.dumps(report), ex=7 * 86400)
    except Exception:  # noqa: BLE001
        pass
    s, t = report["source"], report["target"]
    summary = {
        "pg_dump": report["pg_dump"],
        "source": {k: s.get(k) for k in ("ok", "error", "server_version", "db_size_mb", "auth_users_est",
                                         "auth_users_readable", "took_s")} | {"dsn": s.get("target"),
                                                                              "tables": len(s.get("tables") or [])},
        "target": {k: t.get(k) for k in ("ok", "error", "server_version", "db_size_mb", "took_s")}
        | {"dsn": t.get("target"), "tables": len(t.get("tables") or [])},
        "largest_source_tables": [(x["name"], x["rows_est"], x["size_kb"]) for x in (s.get("tables") or [])[:12]],
    }
    print("[DBMigration] probe " + json.dumps(summary, ensure_ascii=False), flush=True)
    return report


def probe_once_per_deploy() -> None:
    """Background, at startup, when SUPABASE_DB_URL is set: one probe per
    container start (Redis lock so the two uvicorn workers do not both run)."""
    if not source_dsn():
        return
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        if not get_redis().set(LOCK_KEY, str(os.getpid()), nx=True, ex=300):
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        probe()
    except Exception as exc:  # noqa: BLE001
        print(f"[DBMigration] probe failed: {type(exc).__name__}", flush=True)
