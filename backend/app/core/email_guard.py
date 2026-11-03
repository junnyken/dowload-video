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
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("app.email_guard")

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
    """
    try:
        from app.core.database import using_postgres, get_service_client  # noqa: PLC0415
        if not using_postgres():
            return False
        local, domain = normalized_email.split("@", 1)
        pg = get_service_client()
        if domain == "gmail.com":
            rows = pg.run_sql(
                """
                SELECT 1
                  FROM auth.users
                 WHERE email IS NOT NULL
                   AND rtrim(split_part(lower(email), '@', 2), '.') IN ('gmail.com', 'googlemail.com')
                   AND replace(split_part(split_part(lower(email), '@', 1), '+', 1), '.', '') = %s
                 LIMIT 1
                """,
                (local,),
            )
            return bool(rows)
        rows = pg.run_sql(
            "SELECT 1 FROM auth.users WHERE lower(btrim(email)) = %s LIMIT 1",
            (normalized_email,),
        )
        return bool(rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("check_duplicate_canonical_email failed: %s", exc)
        return False


def ensure_db_email_guard() -> Dict[str, Any]:
    """
    Install Migration 043 trigger on both Vibe Host Postgres (target_dsn)
    and Supabase Postgres (source_dsn, if configured).
    Safe and idempotent.
    """
    import psycopg2  # noqa: PLC0415
    from app.core.db_migration import source_dsn, target_dsn  # noqa: PLC0415

    results: Dict[str, Any] = {}
    for label, dsn in (("target_postgres", target_dsn()), ("supabase_postgres", source_dsn())):
        if not dsn:
            results[label] = "skipped_no_dsn"
            continue
        try:
            conn = psycopg2.connect(dsn, connect_timeout=10)
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('auth.users') IS NOT NULL")
                    has_auth = bool(cur.fetchone()[0])
                    if has_auth:
                        cur.execute(_TRIGGER_INSTALL_SQL)
                        results[label] = "installed"
                    else:
                        results[label] = "no_auth_users_table"
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            results[label] = f"error: {type(exc).__name__}"
    return results
