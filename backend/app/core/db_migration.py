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


# ── copy (phase 1.4) ─────────────────────────────────────────────────────────
#
# 1. target: Supabase-compatible names (app/core/supabase_compat.sql)
# 2. auth.users rows (id, email, bcrypt hash, confirmation, metadata) — the
#    public tables' foreign keys point at them
# 3. pg_dump -Fc of schema public (no owner, no grants) -> pg_restore, one
#    transaction; RLS policies are skipped when the Supabase roles cannot be
#    created (the backend connects as the owner, RLS never applied to it)
# 4. exact row counts of every table on both sides
#
# Never switches the app over: DB_BACKEND stays as it is.

import re as _re
import tempfile as _tempfile

COPY_KEY = "dbmig:copy"
COPY_LOCK = "dbmig:copy:lock"
KEEP_TABLES = {"db_backups"}           # the nightly backup's own table on the target
AUTH_COLUMNS = ("id", "email", "encrypted_password", "email_confirmed_at", "raw_user_meta_data",
                "raw_app_meta_data", "last_sign_in_at", "banned_until", "created_at", "updated_at")
_COMPAT_SQL = os.path.join(os.path.dirname(__file__), "supabase_compat.sql")


def _bin(tool: str) -> str:
    """PG_TOOLS_DIR lets tests point at wrappers; the image has them on PATH."""
    d = os.getenv("PG_TOOLS_DIR", "")
    return os.path.join(d, tool) if d else (shutil.which(tool) or tool)


def _scrub(text: str, *dsns: str) -> str:
    for dsn in dsns:
        try:
            pw = urlparse(dsn).password
        except Exception:  # noqa: BLE001
            pw = None
        if pw:
            text = text.replace(pw, "***")
    return _re.sub(r"(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@", r"\1***@", text)


class _Log:
    def __init__(self, rc, dsns):
        self.rc, self.dsns = rc, dsns
        self.state: Dict[str, Any] = {"started": time.time(), "steps": [], "status": "running"}
        self._save()

    def step(self, msg: str, **extra) -> None:
        self.state["steps"].append({"t": round(time.time() - self.state["started"], 1),
                                    "msg": _scrub(msg, *self.dsns)[:500], **extra})
        self._save()

    def done(self, status: str, **extra) -> Dict[str, Any]:
        self.state.update(status=status, finished=time.time(), **extra)
        self._save()
        return self.state

    def _save(self) -> None:
        try:
            self.rc.set(COPY_KEY, json.dumps(self.state, default=str), ex=30 * 86400)
        except Exception:  # noqa: BLE001
            pass


def _connect(dsn: str):
    import psycopg2  # noqa: PLC0415
    conn = psycopg2.connect(dsn, connect_timeout=15)
    conn.autocommit = True
    return conn


def _public_tables(conn) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def _exact_counts(conn, tables) -> Dict[str, int]:
    out = {}
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f'SELECT count(*) FROM public."{t}"')
            out[t] = int(cur.fetchone()[0])
        cur.execute("SELECT to_regclass('auth.users') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("SELECT count(*) FROM auth.users")
            out["auth.users"] = int(cur.fetchone()[0])
    return out


def _prepare_target(conn, log) -> bool:
    """Compat schema; returns whether the Supabase roles exist (else policies are skipped)."""
    with open(_COMPAT_SQL, encoding="utf-8") as fh:
        sql = fh.read()
    roles_sql = _re.search(r"DO \$\$ BEGIN\s+IF NOT EXISTS \(SELECT 1 FROM pg_roles.*?END \$\$;", sql, _re.S).group(0)
    rest = sql.replace(roles_sql, "")
    with conn.cursor() as cur:
        try:
            cur.execute(roles_sql)
        except Exception as exc:  # noqa: BLE001
            log.step(f"roles anon/authenticated/service_role not created ({type(exc).__name__}): RLS policies skipped")
        cur.execute(rest)
        cur.execute("SELECT count(*) FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')")
        return cur.fetchone()[0] == 3


def _copy_auth_users(src, dst, log) -> int:
    from psycopg2.extras import Json, execute_values  # noqa: PLC0415
    with src.cursor() as cur:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'auth' AND table_name = 'users'")
        have = {r[0] for r in cur.fetchall()}
        cols = [c for c in AUTH_COLUMNS if c in have]
        cur.execute(f"SELECT {', '.join(cols)} FROM auth.users ORDER BY created_at NULLS FIRST")
        rows = cur.fetchall()
    rows = [tuple(Json(v) if isinstance(v, (dict, list)) else v for v in r) for r in rows]
    with dst.cursor() as cur:
        cur.execute("ALTER TABLE auth.users DISABLE TRIGGER USER")
        try:
            if rows:
                execute_values(cur, f"INSERT INTO auth.users ({', '.join(cols)}) VALUES %s "
                                    f"ON CONFLICT (id) DO UPDATE SET "
                                    + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id"), rows)
        finally:
            cur.execute("ALTER TABLE auth.users ENABLE TRIGGER USER")
    log.step(f"auth.users: {len(rows)} accounts copied")
    return len(rows)


def _run(args, log, dsns, *, stdin=None, timeout=900) -> str:
    p = subprocess.run(args, stdin=stdin, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(_scrub(f"{os.path.basename(args[0])} exit {p.returncode}: {p.stderr.strip()[-1500:]}", *dsns))
    return p.stdout


def copy_all(*, replace: bool = False, rc=None) -> Dict[str, Any]:
    """Copy Supabase -> target. replace=True drops the target's public tables
    first (except KEEP_TABLES); without it a target that already holds app
    tables is refused."""
    from app.core.redis_client import get_redis  # noqa: PLC0415
    rc = rc or get_redis()
    src_dsn, dst_dsn = source_dsn(), target_dsn()
    log = _Log(rc, (src_dsn, dst_dsn))
    if not src_dsn or not dst_dsn:
        return log.done("failed", error="SUPABASE_DB_URL hoặc DATABASE_URL chưa đặt")
    try:
        src, dst = _connect(src_dsn), _connect(dst_dsn)
    except Exception as exc:  # noqa: BLE001
        return log.done("failed", error=_scrub(f"connect: {exc}", src_dsn, dst_dsn)[:300])
    try:
        existing = [t for t in _public_tables(dst) if t not in KEEP_TABLES]
        if existing and not replace:
            return log.done("refused", error=f"đích đã có {len(existing)} bảng; chọn 'chép đè' để thay")
        if existing:
            with dst.cursor() as cur:
                for t in existing:
                    cur.execute(f'DROP TABLE IF EXISTS public."{t}" CASCADE')
                cur.execute("""SELECT 'DROP ' || CASE p.prokind WHEN 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END
                                      || ' IF EXISTS public.' || quote_ident(p.proname)
                                      || '(' || pg_get_function_identity_arguments(p.oid) || ') CASCADE'
                                 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                                 LEFT JOIN pg_depend d ON d.objid = p.oid AND d.deptype = 'e'
                                WHERE n.nspname = 'public' AND d.objid IS NULL""")
                for (stmt,) in cur.fetchall():
                    cur.execute(stmt)
                cur.execute("""SELECT 'DROP ' || CASE c.relkind WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW'
                                      ELSE 'SEQUENCE' END || ' IF EXISTS public.' || quote_ident(c.relname) || ' CASCADE'
                                 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                                WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm', 'S')""")
                for (stmt,) in cur.fetchall():
                    cur.execute(stmt)
                cur.execute("""SELECT 'DROP TYPE IF EXISTS public.' || quote_ident(t.typname) || ' CASCADE'
                                 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                                WHERE n.nspname = 'public' AND t.typtype IN ('e', 'c', 'd')
                                  AND NOT EXISTS (SELECT 1 FROM pg_class c WHERE c.reltype = t.oid)""")
                for (stmt,) in cur.fetchall():
                    cur.execute(stmt)
            log.step(f"target: {len(existing)} old tables dropped (kept {sorted(KEEP_TABLES)})")
        roles_ok = _prepare_target(dst, log)
        log.step("target: Supabase-compatible auth schema ready")
        _copy_auth_users(src, dst, log)

        with _tempfile.TemporaryDirectory(prefix="dbmig-") as tmp:
            dump = os.path.join(tmp, "public.dump")
            with open(dump, "wb") as fh:
                p = subprocess.run([_bin("pg_dump"), "-Fc", "--schema=public", "--no-owner", "--no-privileges",
                                    "-d", src_dsn], stdout=fh, stderr=subprocess.PIPE, timeout=900)
            if p.returncode != 0:
                raise RuntimeError(_scrub(f"pg_dump exit {p.returncode}: {p.stderr.decode(errors='replace')[-1500:]}",
                                          src_dsn, dst_dsn))
            log.step(f"pg_dump: {round(os.path.getsize(dump) / 1048576, 2)} MB")
            with open(dump, "rb") as fh:
                toc = _run([_bin("pg_restore"), "-l"], log, (src_dsn, dst_dsn), stdin=fh)
            keep, dropped = [], {}
            for line in toc.splitlines():
                kind = _toc_kind(line)
                skip = kind in ("SCHEMA", "COMMENT_SCHEMA", "EXTENSION", "COMMENT_EXTENSION") \
                    or (kind in ("POLICY", "ROW SECURITY") and not roles_ok)
                if skip:
                    dropped[kind] = dropped.get(kind, 0) + 1
                    line = ";" + line
                keep.append(line)
            listing = os.path.join(tmp, "list.txt")
            with open(listing, "w", encoding="utf-8") as fh:
                fh.write("\n".join(keep) + "\n")
            log.step(f"pg_restore: skipping {dropped}" if dropped else "pg_restore: full list")
            # pg_restore 17 always sends SET transaction_timeout, which a 16
            # server rejects: render SQL, drop that line, load it with psql in
            # one transaction.
            with open(dump, "rb") as fh:
                script = _run([_bin("pg_restore"), "--no-owner", "--no-privileges", "-L", listing, "-f", "-"],
                              log, (src_dsn, dst_dsn), stdin=fh)
            script = "\n".join(l for l in script.splitlines() if not l.startswith("SET transaction_timeout"))
            sql_path = os.path.join(tmp, "restore.sql")
            with open(sql_path, "w", encoding="utf-8") as fh:
                fh.write(script)
            with open(sql_path, "rb") as fh:
                _run([_bin("psql"), "-X", "-q", "--single-transaction", "-v", "ON_ERROR_STOP=1",
                      "-d", dst_dsn], log, (src_dsn, dst_dsn), stdin=fh)
            log.step(f"restore: done ({round(len(script) / 1048576, 2)} MB of SQL)")

        tables = _public_tables(src)
        src_counts = _exact_counts(src, tables)
        dst_counts = _exact_counts(dst, tables)
        diff = {t: [src_counts.get(t), dst_counts.get(t)] for t in sorted(set(src_counts) | set(dst_counts))
                if src_counts.get(t) != dst_counts.get(t)}
        log.step(f"counts: {len(src_counts)} tables compared, {len(diff)} differ")
        return log.done("ok" if not diff else "mismatch", counts=src_counts, diff=diff)
    except Exception as exc:  # noqa: BLE001
        return log.done("failed", error=_scrub(f"{type(exc).__name__}: {exc}", src_dsn, dst_dsn)[:1500])
    finally:
        src.close()
        dst.close()


def _toc_kind(line: str) -> str:
    """pg_restore -l line -> object type ('TABLE', 'POLICY', 'SCHEMA', ...)."""
    if not line or line.startswith(";"):
        return ""
    m = _re.match(r"^\d+;\s*\d+\s+\d+\s+(.*)$", line)
    if not m:
        return ""
    rest = m.group(1)
    for kind in ("ROW SECURITY", "COMMENT - SCHEMA", "COMMENT - EXTENSION", "TABLE DATA", "SEQUENCE SET",
                 "FK CONSTRAINT", "DEFAULT ACL", "MATERIALIZED VIEW"):
        if rest.startswith(kind):
            return kind.replace("COMMENT - ", "COMMENT_")
    return rest.split(" ", 1)[0]


def copy_in_background(*, replace: bool) -> bool:
    """Start copy_all in a thread; False when one is already running."""
    import threading  # noqa: PLC0415
    from app.core.redis_client import get_redis  # noqa: PLC0415
    rc = get_redis()
    if not rc.set(COPY_LOCK, str(os.getpid()), nx=True, ex=1800):
        return False

    def run():
        try:
            copy_all(replace=replace, rc=rc)
        finally:
            rc.delete(COPY_LOCK)
    threading.Thread(target=run, name="dbmig-copy", daemon=True).start()
    return True
