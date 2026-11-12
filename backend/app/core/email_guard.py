"""
Strict Email Guard for Sign-Up Anti-Abuse
=========================================
Enforces:
1. 100% Disposable / temporary email blocking via strict 5-provider whitelist:
   - Gmail:           @gmail.com, @googlemail.com
   - Outlook/Hotmail: @outlook.com, @hotmail.com, @live.com, @msn.com, @outlook.com.vn, @hotmail.com.vn
   - Apple iCloud:    @icloud.com, @me.com, @mac.com
   - Yahoo Mail:      @yahoo.com, @yahoo.com.vn, @ymail.com, @rocketmail.com
   - ProtonMail:      @proton.me, @protonmail.com, @pm.me
2. Anti-multi-account Gmail / alias tricks:
   - Blocks '+' sub-addressing (e.g. ten+1@gmail.com)
   - Normalizes Gmail dots and @googlemail.com (a.b.c@gmail.com -> abc@gmail.com)
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("app.email_guard")

STARTUP_EMAIL_GUARD_LOCK_KEY = "startup:email_guard"
STARTUP_EMAIL_GUARD_LOCK_TTL_SEC = 120


def _startup_lock_key(proc1_stat_path: str = "/proc/1/stat") -> str:
    """
    Build a per-container Redis lock key on Linux so a blue/green candidate container
    does not mask the promoted live container's startup log, while all uvicorn workers
    inside the same container share the exact same key.
    Falls back to `STARTUP_EMAIL_GUARD_LOCK_KEY` when `/proc/1/stat` is absent.
    """
    try:
        with open(proc1_stat_path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
        after_comm = raw.rsplit(")", 1)[1].split()
        pid1_start = after_comm[19]
        host = socket.gethostname() or "container"
        return f"{STARTUP_EMAIL_GUARD_LOCK_KEY}:{host}:{pid1_start}"
    except Exception:  # noqa: BLE001
        return STARTUP_EMAIL_GUARD_LOCK_KEY

# Optional pre-existing pool for SUPABASE_DB_URL if configured by host runtime.
_SOURCE_POOL: Any = None

ALLOWED_EMAIL_DOMAINS = frozenset({
    # Gmail
    "gmail.com",
    "googlemail.com",
    # Outlook / Hotmail / Microsoft
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "outlook.com.vn",
    "hotmail.com.vn",
    # Apple iCloud
    "icloud.com",
    "me.com",
    "mac.com",
    # Yahoo Mail
    "yahoo.com",
    "yahoo.com.vn",
    "ymail.com",
    "rocketmail.com",
    # ProtonMail
    "proton.me",
    "protonmail.com",
    "pm.me",
})

_DROP_TRIGGER_SQL = "DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users;"

_TRIGGER_INSTALL_SQL = """
CREATE TABLE IF NOT EXISTS public.signup_email_allowlist (
    email      text PRIMARY KEY CHECK (email = lower(email) AND position('@' in email) > 1),
    note       text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION public.reject_disposable_email_signup()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_email      text := lower(btrim(coalesce(NEW.email, '')));
    v_local      text;
    v_domain     text;
    v_norm_local text;
    v_allowed    constant text[] := ARRAY[
        'gmail.com', 'googlemail.com',
        'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'outlook.com.vn', 'hotmail.com.vn',
        'icloud.com', 'me.com', 'mac.com',
        'yahoo.com', 'yahoo.com.vn', 'ymail.com', 'rocketmail.com',
        'proton.me', 'protonmail.com', 'pm.me'
    ];
BEGIN
    IF v_email = '' OR position('@' in v_email) = 0 THEN
        RETURN NEW;
    END IF;

    IF EXISTS (SELECT 1 FROM public.signup_email_allowlist a WHERE a.email = v_email) THEN
        RETURN NEW;
    END IF;

    v_local  := split_part(v_email, '@', 1);
    v_domain := rtrim(split_part(v_email, '@', 2), '.');

    IF v_local = '' OR v_domain = '' THEN
        RAISE EXCEPTION 'VIDGRAB_INVALID_EMAIL: Invalid email address'
            USING ERRCODE = 'P0001';
    END IF;

    IF position('+' in v_local) > 0 THEN
        RAISE EXCEPTION 'VIDGRAB_PLUS_EMAIL: Sub-address emails containing (+) are not allowed'
            USING ERRCODE = 'P0001';
    END IF;

    IF NOT (v_domain = ANY (v_allowed)) THEN
        RAISE EXCEPTION 'VIDGRAB_DISPOSABLE_EMAIL: Email domain (%) is not in the allowed provider list', v_domain
            USING ERRCODE = 'P0001';
    END IF;

    IF v_domain IN ('gmail.com', 'googlemail.com') THEN
        v_norm_local := replace(v_local, '.', '');
        IF v_norm_local = '' THEN
            RAISE EXCEPTION 'VIDGRAB_INVALID_EMAIL: Invalid Gmail username'
                USING ERRCODE = 'P0001';
        END IF;

        IF EXISTS (
            SELECT 1
              FROM auth.users u
             WHERE u.id IS DISTINCT FROM NEW.id
               AND u.email IS NOT NULL
               AND rtrim(split_part(lower(u.email), '@', 2), '.') IN ('gmail.com', 'googlemail.com')
               AND replace(split_part(split_part(lower(u.email), '@', 1), '+', 1), '.', '') = v_norm_local
        ) THEN
            RAISE EXCEPTION 'VIDGRAB_DUPLICATE_GMAIL: A Gmail account with this canonical address already exists'
                USING ERRCODE = 'P0001';
        END IF;

        NEW.email := v_norm_local || '@gmail.com';
    ELSE
        NEW.email := v_local || '@' || v_domain;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_block_disposable ON auth.users;
CREATE TRIGGER on_auth_user_block_disposable
    BEFORE INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.reject_disposable_email_signup();
"""


def validate_and_normalize_email(raw_email: str) -> Tuple[bool, str, Optional[str]]:
    """
    Validate and normalize an email for sign-up.
    Returns (is_valid, normalized_email, error_reason).
    error_reason is one of:
      - None (when valid)
      - 'invalid_format'
      - 'plus_alias'
      - 'provider_not_allowed'
    """
    e = (raw_email or "").strip().lower()
    if not e or "@" not in e or e.count("@") != 1:
        return False, "", "invalid_format"

    local, domain = e.split("@", 1)
    domain = domain.rstrip(".")
    if not local or not domain:
        return False, "", "invalid_format"

    # 1. Block '+' sub-addresses (ten+1@gmail.com)
    if "+" in local:
        return False, "", "plus_alias"

    # 2. Strict 5-provider allowlist
    if domain not in ALLOWED_EMAIL_DOMAINS:
        return False, "", "provider_not_allowed"

    # 3. Normalize Gmail dots & @googlemail.com -> @gmail.com
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
        domain = "gmail.com"
        if not local:
            return False, "", "invalid_format"

    return True, f"{local}@{domain}", None


def check_duplicate_canonical_email(normalized_email: str) -> bool:
    """
    Check if any existing user in auth.users already has the same canonical email
    (specifically catching older Gmail accounts registered with dots before normalization).
    Queries SUPABASE_DB_URL (real auth.users, connect_timeout=5) when configured,
    falling back to the app Postgres DB otherwise. Returns False and logs a warning on error.
    """
    try:
        local, domain = normalized_email.split("@", 1)
        if domain == "gmail.com":
            sql = """
                SELECT 1
                  FROM auth.users
                 WHERE email IS NOT NULL
                   AND rtrim(split_part(lower(email), '@', 2), '.') IN ('gmail.com', 'googlemail.com')
                   AND replace(split_part(split_part(lower(email), '@', 1), '+', 1), '.', '') = %s
                 LIMIT 1
            """
            params: Tuple[Any, ...] = (local,)
        else:
            sql = "SELECT 1 FROM auth.users WHERE lower(btrim(email)) = %s LIMIT 1"
            params = (normalized_email,)

        from app.core.database import get_service_client, using_postgres  # noqa: PLC0415
        from app.core.db_migration import source_dsn  # noqa: PLC0415

        s_dsn = source_dsn()
        if s_dsn:
            # Reuse an existing pool if one is already open for s_dsn; never create a new pool per request.
            if _SOURCE_POOL is not None:
                if hasattr(_SOURCE_POOL, "run_sql"):
                    return bool(_SOURCE_POOL.run_sql(sql, params))
                conn = _SOURCE_POOL.getconn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql, params)
                        return cur.fetchone() is not None
                finally:
                    _SOURCE_POOL.putconn(conn)

            if using_postgres():
                pg_existing = get_service_client()
                pg_pool = getattr(pg_existing, "_pool", None)
                if pg_pool is not None and getattr(pg_pool, "_pool", None) is not None and getattr(pg_pool, "dsn", None) == s_dsn:
                    return bool(pg_existing.run_sql(sql, params))

            import psycopg2  # noqa: PLC0415
            conn = psycopg2.connect(s_dsn, connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return cur.fetchone() is not None
            finally:
                conn.close()

        if not using_postgres():
            return False
        pg = get_service_client()
        rows = pg.run_sql(sql, params)
        return bool(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("check_duplicate_canonical_email failed: %s", type(exc).__name__)
        return False


def ensure_db_email_guard(rc: Any = None) -> Dict[str, Any]:
    """
    Once per container startup (guarded by Redis `startup:email_guard`, SET NX EX 120):
      - On Vibe Host app Postgres (`target_dsn`): DROP trigger `on_auth_user_block_disposable`
        (keep function & allowlist table) -> `'dropped'`, so `ensure_app_user` can sync legacy
        custom-domain accounts without firing BEFORE INSERT before ON CONFLICT.
      - On Supabase Postgres (`source_dsn`): INSTALL trigger 043 -> `'installed'`.
        Logs a clear warning when `SUPABASE_DB_URL` is missing.
    """
    redis_client = rc
    lock_key = _startup_lock_key()
    try:
        if redis_client is None:
            from app.core.redis_client import get_redis  # noqa: PLC0415
            redis_client = get_redis()
        got = redis_client.set(
            lock_key,
            str(os.getpid()),
            nx=True,
            ex=STARTUP_EMAIL_GUARD_LOCK_TTL_SEC,
        )
        if not got:
            return {"status": "skipped_locked"}
    except Exception as redis_exc:  # noqa: BLE001
        logger.warning(
            "Email signup guard Redis lock unavailable (%s); continuing without lock",
            type(redis_exc).__name__,
        )

    import psycopg2  # noqa: PLC0415
    from app.core.db_migration import source_dsn, target_dsn  # noqa: PLC0415

    results: Dict[str, Any] = {}

    # 1. Target app DB (vidgrab-backup): drop the trigger only (keep function & allowlist table)
    t_dsn = target_dsn()
    if not t_dsn:
        results["target_postgres"] = "skipped_no_dsn"
    else:
        try:
            conn = psycopg2.connect(t_dsn, connect_timeout=10)
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('auth.users') IS NOT NULL")
                    has_auth = bool(cur.fetchone()[0])
                    if has_auth:
                        cur.execute(_DROP_TRIGGER_SQL)
                        results["target_postgres"] = "dropped"
                        try:
                            cur.execute(
                                "SELECT tgname FROM pg_trigger "
                                "WHERE tgrelid='auth.users'::regclass AND NOT tgisinternal "
                                "ORDER BY tgname"
                            )
                            results["target_auth_triggers"] = [r[0] for r in (cur.fetchall() or [])]
                            cur.execute(
                                "SELECT count(*) FROM public.profiles p "
                                "JOIN auth.users u ON u.id=p.id "
                                "WHERE split_part(u.email,'@',2) IN ('matbao.com','eml.agency')"
                            )
                            cnt_row = cur.fetchone()
                            results["target_custom_domain_profiles"] = int(cnt_row[0]) if cnt_row else 0
                        except Exception as verify_exc:  # noqa: BLE001
                            results["target_verify_error"] = type(verify_exc).__name__
                    else:
                        results["target_postgres"] = "no_auth_users_table"
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            results["target_postgres"] = f"error: {type(exc).__name__}"

    # 2. Source DB (Supabase): install trigger 043; warn clearly if SUPABASE_DB_URL is missing
    s_dsn = source_dsn()
    if not s_dsn:
        logger.warning(
            "[EmailGuard] SUPABASE_DB_URL is not configured — trigger on_auth_user_block_disposable "
            "(043) could NOT be verified or installed on Supabase auth.users!"
        )
        results["supabase_postgres"] = "skipped_no_dsn"
    else:
        try:
            conn = psycopg2.connect(s_dsn, connect_timeout=10)
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('auth.users') IS NOT NULL")
                    has_auth = bool(cur.fetchone()[0])
                    if has_auth:
                        cur.execute(_TRIGGER_INSTALL_SQL)
                        results["supabase_postgres"] = "installed"
                    else:
                        results["supabase_postgres"] = "no_auth_users_table"
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Email signup guard install failed on supabase_postgres: %s", type(exc).__name__)
            results["supabase_postgres"] = f"error: {type(exc).__name__}"

    return results
