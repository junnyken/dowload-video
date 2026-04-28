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

SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")


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
