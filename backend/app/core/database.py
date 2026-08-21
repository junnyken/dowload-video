"""
Supabase Database Connection Module
====================================
Initializes and provides a Supabase client instance
using credentials loaded from environment variables.
"""

import os
from functools import lru_cache
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables from .env file
load_dotenv()

SUPABASE_URL: str         = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str         = os.getenv("SUPABASE_KEY", "")          # anon key (existing)
SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")  # service_role key (for backend ops)


def _validate_credentials() -> None:
    """Validate that Supabase credentials are configured."""
    if not SUPABASE_URL:
        raise ValueError(
            "SUPABASE_URL is not set. "
            "Please add it to your .env file."
        )
    if not SUPABASE_KEY:
        raise ValueError(
            "SUPABASE_KEY is not set. "
            "Please add it to your .env file."
        )


@lru_cache(maxsize=1)
def get_service_client() -> Client:
    """
    Service-role Supabase client for backend-only operations.
    Bypasses RLS. Falls back to anon client if SUPABASE_SERVICE_KEY not set.
    Never expose this key to the frontend.
    """
    _validate_credentials()
    key = SUPABASE_SERVICE_KEY or SUPABASE_KEY  # fallback to anon if not configured
    return create_client(SUPABASE_URL, key)


@lru_cache(maxsize=1)
def get_anon_client() -> Client:
    """
    Anon-key client. RLS applies, and since the backend never attaches an end
    user's JWT to it, `auth.uid()` is always NULL — so any policy written as
    `USING (id = auth.uid())` denies every row. Only reach for this when anon
    really is the right role for the table.
    """
    _validate_credentials()
    return create_client(SUPABASE_URL, SUPABASE_KEY)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    The backend's database client — service-role whenever it is configured.

    This handed back an anon-key client before, which is the wrong role for
    server-side work: the backend acts on its own behalf and never sets a user
    session on the client, so `auth.uid()` is NULL on every query it makes. Any
    table with a per-user policy — `profiles` has `USING (id = auth.uid()::TEXT)`
    — returned ZERO rows to every backend read and accepted ZERO rows on every
    write. RLS filters instead of raising, so all of it failed silently:

      - admin "Tài khoản đăng ký" showed 0 while `profiles` held real accounts
      - the signup chart read a flat 0 for every day
      - quotas.py resolved every paying user's tier back to 'free'
      - admin "Đổi gói" reported success and changed nothing

    Authorization is enforced in application code — each query filters by the
    user id taken from the verified JWT, and admin routes sit behind
    verify_admin — which is the same assumption the permissive `USING (true)`
    policies on download_jobs/user_usage were already written against.

    Falls back to the anon key when SUPABASE_SERVICE_KEY is unset, so a
    half-configured environment degrades rather than failing to boot.
    """
    _validate_credentials()
    key = SUPABASE_SERVICE_KEY or SUPABASE_KEY
    return create_client(SUPABASE_URL, key)


# Convenience alias
supabase = None


def init_db() -> Client:
    """
    Initialize the database connection and return the client.
    Call this during application startup.
    """
    global supabase
    supabase = get_supabase_client()
    print("Supabase client initialized successfully.")
    return supabase


# Columns that MUST exist — code will silently fail if they're absent
_REQUIRED_COLUMNS = {
    "download_jobs": ["id", "original_url", "status", "quality", "file_size_mb",
                      "local_file_path", "local_mp3_path", "thumbnail_url",
                      "downloaded_height", "is_audio_only", "platform", "source"],
}


def validate_schema() -> None:
    """
    Probe Supabase tables for required columns.
    Logs warnings but never raises — startup must not be blocked by schema issues.
    """
    try:
        client = get_supabase_client()
        for table, cols in _REQUIRED_COLUMNS.items():
            try:
                client.table(table).select(",".join(cols)).limit(1).execute()
                print(f"[Schema] ✓ {table} ({len(cols)} columns OK)")
            except Exception as col_err:
                err_str = str(col_err)
                missing_hint = ""
                for c in cols:
                    if c in err_str:
                        missing_hint = f" — column '{c}' may be missing"
                        break
                print(f"[Schema] ⚠ {table}{missing_hint}: {err_str[:120]}")
    except Exception as e:
        print(f"[Schema] Could not validate (Supabase unavailable?): {e}")
