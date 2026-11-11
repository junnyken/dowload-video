"""
Auto-ensure Migrations 044 & 045 on plain PostgreSQL (Vibe Host vidgrab-backup)
===============================================================================

Follows the exact `ensure_schema()` pattern of Migration 042 in
`app/core/payment_orders.py`, adapted for plain PostgreSQL where Supabase's
`service_role` may not exist (`db_migration.py:_prepare_target`):

  - 044: `public.flow_consent_audit_logs` + 2 indexes + RLS + own-row SELECT policy
  - 045: `public.payment_webhook_events` + 3 indexes + RLS + conditional
         `TO service_role` policy (only created when `pg_roles` has `service_role`)

Runs once at FastAPI startup when `DB_BACKEND=postgres`, guarded by a short
Redis `SET NX` lock so multiple uvicorn workers do not overlap. Any failure is
logged (with credentials scrubbed) and reported to Telegram without crashing
the application.
"""
from __future__ import annotations

import html
import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TABLES_044_045 = ("flow_consent_audit_logs", "payment_webhook_events")
STARTUP_LOCK_KEY = "db:migrations:044_045:startup_lock"
STARTUP_LOCK_TTL_SEC = 20

_SCHEMA_044_045_ENSURED = False

MIGRATION_044_045_SQL = """
CREATE TABLE IF NOT EXISTS public.flow_consent_audit_logs (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT NOT NULL,
  temp_id     TEXT NOT NULL,
  action      TEXT NOT NULL DEFAULT 'process_confirm',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_flow_consent_audit_logs_user
  ON public.flow_consent_audit_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_flow_consent_audit_logs_temp_id
  ON public.flow_consent_audit_logs (temp_id);

ALTER TABLE public.flow_consent_audit_logs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'flow_consent_audit_logs'
      AND policyname = 'flow_consent_audit_logs_own'
  ) THEN
    CREATE POLICY "flow_consent_audit_logs_own" ON public.flow_consent_audit_logs
      FOR SELECT
      USING (user_id = current_setting('request.jwt.claims', true)::json->>'sub');
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.payment_webhook_events (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider          TEXT NOT NULL,
  provider_event_id TEXT,
  event_type        TEXT,
  user_id           TEXT,
  order_id          TEXT,
  current_tier      TEXT,
  target_tier       TEXT,
  amount            BIGINT,
  status            TEXT NOT NULL DEFAULT 'needs_manual_review',
  reason            TEXT,
  raw_payload       JSONB DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_status
  ON public.payment_webhook_events (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_user
  ON public.payment_webhook_events (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_webhook_events_provider_event
  ON public.payment_webhook_events (provider, provider_event_id);

ALTER TABLE public.payment_webhook_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename = 'payment_webhook_events'
        AND policyname = 'payment_webhook_events_service_role_only'
    ) THEN
      CREATE POLICY "payment_webhook_events_service_role_only"
        ON public.payment_webhook_events
        FOR ALL
        TO service_role
        USING (true)
        WITH CHECK (true);
    END IF;
  END IF;
END $$;
"""


def _scrub_error(text: str) -> str:
    """Remove any DSN credentials or passwords from error messages."""
    out = re.sub(r"(postgres(?:ql)?://[^:\s/]+:)[^@\s]+(@)", r"\1***\2", text or "")
    for env_key in ("APP_DATABASE_URL", "DATABASE_URL", "SUPABASE_DB_URL", "DB_PASSWORD", "PGPASSWORD"):
        secret = os.getenv(env_key, "")
        if secret and len(secret) >= 4:
            out = out.replace(secret, "***")
    return out[:300]


def ensure_044_045_schema(force: bool = False, db: Any = None) -> Dict[str, Any]:
    """
    Idempotently create `flow_consent_audit_logs` (044) and
    `payment_webhook_events` (045) on PostgreSQL when `DB_BACKEND=postgres`.
    Raises on SQL error so caller (`ensure_044_045_once_on_startup`) can log
    and alert cleanly.
    """
    global _SCHEMA_044_045_ENSURED
    from app.core import database  # noqa: PLC0415

    if db is None and not database.using_postgres():
        return {"ok": True, "status": "skipped_not_postgres"}

    if _SCHEMA_044_045_ENSURED and not force and db is None:
        return {"ok": True, "status": "already_ensured", "tables": list(TABLES_044_045)}

    client = db if db is not None else database.get_service_client()
    pool = getattr(client, "_pool", None)
    if pool is None:
        return {"ok": True, "status": "skipped_no_pg_pool"}

    pool.run(MIGRATION_044_045_SQL, (), fetch=False)
    for tbl in TABLES_044_045:
        if hasattr(pool, "columns"):
            pool.columns.pop(tbl, None)
        if hasattr(pool, "pkeys"):
            pool.pkeys.pop(tbl, None)

    if db is None:
        _SCHEMA_044_045_ENSURED = True
    return {"ok": True, "status": "ensured", "tables": list(TABLES_044_045)}


def ensure_044_045_once_on_startup(rc: Any = None) -> Dict[str, Any]:
    """
    Startup entrypoint: runs `ensure_044_045_schema(force=True)` at most once
    across concurrent uvicorn workers via Redis `SET NX`.
    Never raises — logs and sends a Telegram alert on failure.
    """
    from app.core import database  # noqa: PLC0415

    if not database.using_postgres():
        return {"ok": True, "status": "skipped_not_postgres"}

    redis_client = rc
    lock_acquired = False
    try:
        if redis_client is None:
            from app.core.redis_client import get_redis  # noqa: PLC0415
            redis_client = get_redis()
        got = redis_client.set(
            STARTUP_LOCK_KEY,
            str(os.getpid()),
            nx=True,
            ex=STARTUP_LOCK_TTL_SEC,
        )
        if not got:
            return {"ok": True, "status": "skipped_locked"}
        lock_acquired = True
    except Exception as redis_exc:  # noqa: BLE001
        logger.warning("[Migrations044_045] Redis lock unavailable (%s); running idempotent ensure", type(redis_exc).__name__)

    try:
        return ensure_044_045_schema(force=True)
    except Exception as exc:  # noqa: BLE001
        if lock_acquired and redis_client is not None:
            try:
                redis_client.delete(STARTUP_LOCK_KEY)
            except Exception:  # noqa: BLE001
                pass
        safe_err = _scrub_error(f"{type(exc).__name__}: {exc}")
        logger.error("[Migrations044_045] Startup ensure failed: %s", safe_err)
        print(f"[Startup] Migrations 044/045 ensure FAILED: {safe_err}", flush=True)
        try:
            from app.core.notifications import send_telegram_message_sync  # noqa: PLC0415
            send_telegram_message_sync(
                "⚠️ <b>Lỗi tự tạo bảng 044/045 lúc khởi động</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"📌 Chi tiết: <code>{html.escape(safe_err)}</code>",
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "status": "error", "error": safe_err}


def inspect_044_045_with_cursor(cur: Any) -> Dict[str, Any]:
    """Read-only check of tables 044 & 045 using an existing psycopg2 cursor."""
    cur.execute(
        """SELECT tablename, rowsecurity
             FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')"""
    )
    tbl_rows = {r[0]: bool(r[1]) for r in cur.fetchall()}

    cur.execute(
        """SELECT tablename, indexname
             FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
            ORDER BY tablename, indexname"""
    )
    idx_map: Dict[str, list[str]] = {t: [] for t in TABLES_044_045}
    for tname, iname in cur.fetchall():
        idx_map.setdefault(tname, []).append(iname)

    cur.execute(
        """SELECT tablename, policyname
             FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
            ORDER BY tablename, policyname"""
    )
    pol_map: Dict[str, list[str]] = {t: [] for t in TABLES_044_045}
    for tname, pname in cur.fetchall():
        pol_map.setdefault(tname, []).append(pname)

    out: Dict[str, Any] = {}
    for tbl in TABLES_044_045:
        exists = tbl in tbl_rows
        indexes = idx_map.get(tbl, [])
        policies = pol_map.get(tbl, [])
        rls_enabled = tbl_rows.get(tbl, False)
        out[tbl] = {
            "exists": exists,
            "index_count": len(indexes),
            "indexes": indexes,
            "rls_enabled": rls_enabled,
            "policy_count": len(policies),
            "policies": policies,
        }

    # Expected total indexes: PK + 2 custom on 044 (>= 3), PK + 3 custom on 045 (>= 4)
    out["all_ready"] = bool(
        out["flow_consent_audit_logs"]["exists"]
        and out["flow_consent_audit_logs"]["rls_enabled"]
        and out["flow_consent_audit_logs"]["index_count"] >= 3
        and out["payment_webhook_events"]["exists"]
        and out["payment_webhook_events"]["rls_enabled"]
        and out["payment_webhook_events"]["index_count"] >= 4
    )
    return out


def probe_044_045_tables(dsn: Optional[str] = None, db: Any = None) -> Dict[str, Any]:
    """
    Read-only inspection of `flow_consent_audit_logs` and `payment_webhook_events`
    on the active/target Postgres database. Never returns connection secrets.
    """
    from app.core import database  # noqa: PLC0415
    from app.core.db_migration import describe_dsn, target_dsn  # noqa: PLC0415

    # Allow testing via PgClient `_pool` directly when `db` is passed
    client = db if db is not None else (database.get_service_client() if database.using_postgres() else None)
    pool = getattr(client, "_pool", None) if client is not None else None
    resolved_dsn = dsn or target_dsn()

    if pool is not None and dsn is None:
        try:
            tbl_rows = {
                r["tablename"]: bool(r["rowsecurity"])
                for r in pool.run(
                    """SELECT tablename, rowsecurity
                         FROM pg_tables
                        WHERE schemaname = 'public'
                          AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')"""
                )
            }
            idx_map: Dict[str, list[str]] = {t: [] for t in TABLES_044_045}
            for r in pool.run(
                """SELECT tablename, indexname
                     FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
                    ORDER BY tablename, indexname"""
            ):
                idx_map.setdefault(r["tablename"], []).append(r["indexname"])

            pol_map: Dict[str, list[str]] = {t: [] for t in TABLES_044_045}
            for r in pool.run(
                """SELECT tablename, policyname
                     FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
                    ORDER BY tablename, policyname"""
            ):
                pol_map.setdefault(r["tablename"], []).append(r["policyname"])

            tables_info: Dict[str, Any] = {}
            for tbl in TABLES_044_045:
                exists = tbl in tbl_rows
                indexes = idx_map.get(tbl, [])
                policies = pol_map.get(tbl, [])
                rls_enabled = tbl_rows.get(tbl, False)
                tables_info[tbl] = {
                    "exists": exists,
                    "index_count": len(indexes),
                    "indexes": indexes,
                    "rls_enabled": rls_enabled,
                    "policy_count": len(policies),
                    "policies": policies,
                }
            all_ready = bool(
                tables_info["flow_consent_audit_logs"]["exists"]
                and tables_info["flow_consent_audit_logs"]["rls_enabled"]
                and tables_info["flow_consent_audit_logs"]["index_count"] >= 3
                and tables_info["payment_webhook_events"]["exists"]
                and tables_info["payment_webhook_events"]["rls_enabled"]
                and tables_info["payment_webhook_events"]["index_count"] >= 4
            )
            return {
                "ok": True,
                "db_backend": database.DB_BACKEND,
                "target": describe_dsn(resolved_dsn),
                "tables": tables_info,
                "all_ready": all_ready,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "db_backend": database.DB_BACKEND,
                "target": describe_dsn(resolved_dsn),
                "error": _scrub_error(f"{type(exc).__name__}: {exc}"),
            }

    if not resolved_dsn:
        return {
            "ok": False,
            "db_backend": database.DB_BACKEND,
            "target": {"set": False},
            "error": "target postgres DSN not configured",
        }

    import psycopg2  # noqa: PLC0415

    try:
        conn = psycopg2.connect(
            resolved_dsn,
            connect_timeout=10,
            options="-c statement_timeout=15000",
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "db_backend": database.DB_BACKEND,
            "target": describe_dsn(resolved_dsn),
            "error": _scrub_error(f"connect: {type(exc).__name__}: {exc}"),
        }

    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            inspected = inspect_044_045_with_cursor(cur)
        all_ready = bool(inspected.pop("all_ready", False))
        return {
            "ok": True,
            "db_backend": database.DB_BACKEND,
            "target": describe_dsn(resolved_dsn),
            "tables": inspected,
            "all_ready": all_ready,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "db_backend": database.DB_BACKEND,
            "target": describe_dsn(resolved_dsn),
            "error": _scrub_error(f"{type(exc).__name__}: {exc}"),
        }
    finally:
        conn.close()
