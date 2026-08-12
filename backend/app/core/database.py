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
def get_supabase_client() -> Client:
    """
    Creates and returns a cached Supabase client instance.
    
    The client is cached using lru_cache so that only one
    instance is created throughout the application lifecycle.
    
    Returns:
        Client: An initialized Supabase client.
    
    Raises:
        ValueError: If SUPABASE_URL or SUPABASE_KEY is not set.
    """
    _validate_credentials()
    client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return client


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
    "download_jobs": ["id", "url", "status", "quality", "file_size_mb",
                      "local_file_path", "local_mp3_path", "thumbnail_url",
                      "downloaded_height", "is_audio_only"],
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
