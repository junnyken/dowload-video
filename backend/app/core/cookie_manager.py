"""
Cookie Manager — Phase 26-B
============================
Thin public adapter over app.core.cookie_pool.

This module is the import target that container expanders already reference:
  from app.core.cookie_manager import has_cookies_for
  from app.core.cookie_manager import get_cookie_file

Previously missing → caused silent ImportError (wrapped in try/except) that
made _has_cookies() always return False for Instagram and Twitter.

Exposes a clean API; all Redis-level mechanics stay in cookie_pool.py.
"""
from __future__ import annotations

import base64
import os
import tempfile
from typing import Optional

from app.core.cookie_pool import get_cookie_from_pool


def has_cookies_for(platform: str) -> bool:
    """
    Return True if the platform has at least one usable (non-hard-blocked) cookie.
    Checks Redis pool first, then env-var fallback (PLATFORM_COOKIES_B64).
    """
    cookie = get_cookie_from_pool(platform)
    if cookie:
        return True
    return bool(os.environ.get(f"{platform.upper()}_COOKIES_B64", "").strip())


def get_cookie_file(platform: str) -> Optional[str]:
    """
    Get the best available cookie for *platform* and write it to a temporary
    Netscape-format cookie file.  Returns the file path, or None if no cookie
    is available.  Caller is responsible for deleting the file after use.
    """
    cookie_b64 = get_cookie_from_pool(platform)
    if not cookie_b64:
        cookie_b64 = os.environ.get(f"{platform.upper()}_COOKIES_B64", "").strip()

    if not cookie_b64:
        return None

    try:
        cookie_bytes = base64.b64decode(cookie_b64)
    except Exception:
        return None

    tmp = tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=".txt",
        prefix=f"vg_{platform}_",
        delete=False,
    )
    tmp.write(cookie_bytes)
    tmp.close()
    return tmp.name
