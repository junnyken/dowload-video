"""
Cookie pool encryption at rest (task #6402)
===========================================
Pool cookies (Netscape cookies.txt, base64) are live login sessions. Stored
as plain base64 in Redis, anyone who can read Redis (a dump, a backup with
secrets, a debug `LRANGE`) holds working Facebook / Instagram / X accounts.

Stored form:  "enc:v1:" + base64(nonce[12] + AES-256-GCM ciphertext+tag)
The plaintext inside is the SAME base64 string that used to be stored, so
everything downstream (downloader, probes, refresh) keeps working on it.
Values without the prefix are legacy plaintext and stay readable.

Key — no new environment variable is required:
  1. COOKIE_POOL_ENCRYPTION_KEY (optional; base64 of exactly 32 bytes) — for a
     future rotation away from the derived key.
  2. otherwise HKDF-SHA256(SUPABASE_SERVICE_KEY, info="vidgrab-cookie-pool-v1").
     SUPABASE_SERVICE_KEY is the backend's service-role key: the API and every
     download worker already carry it (docker-compose*.yml), and the app cannot
     do its database work without it.
  3. neither present (tests, local dev) → cookies are stored as plaintext,
     exactly as before, and that is logged once. Never raises.

If the source secret changes, entries written under the old key cannot be
decrypted: they are reported as "undecryptable" and skipped by selection —
never a crash, never the value in a log. Re-upload those cookies (or set
COOKIE_POOL_ENCRYPTION_KEY to the old derived key).
"""

from __future__ import annotations

import base64
import binascii
import functools
import os
import threading
from typing import Optional

PREFIX = "enc:v1:"
HKDF_INFO = b"vidgrab-cookie-pool-v1"
SOURCE_ENV = "SUPABASE_SERVICE_KEY"
OVERRIDE_ENV = "COOKIE_POOL_ENCRYPTION_KEY"
_NONCE_LEN = 12

_logged: set[str] = set()
_log_lock = threading.Lock()


class CookieUndecryptable(Exception):
    """Stored cookie could not be decrypted with the current key. Carries no
    part of the value."""


def _log_once(tag: str, msg: str) -> None:
    with _log_lock:
        if tag in _logged:
            return
        _logged.add(tag)
    print(f"[CookieCrypto] {msg}")


@functools.lru_cache(maxsize=8)
def _derive(source: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=HKDF_INFO).derive(source.encode("utf-8"))


def current_key() -> Optional[bytes]:
    """32-byte key, or None when encryption is unavailable (plaintext mode).
    Read on every call so an env change (tests, rotation) takes effect."""
    override = (os.getenv(OVERRIDE_ENV) or "").strip()
    if override:
        try:
            k = base64.b64decode(override, validate=True)
            if len(k) == 32:
                return k
        except (binascii.Error, ValueError):
            pass
        _log_once("bad_override",
                  f"{OVERRIDE_ENV} is not base64 of 32 bytes — ignored, "
                  f"using the key derived from {SOURCE_ENV}")
    source = (os.getenv(SOURCE_ENV) or "").strip()
    if not source:
        _log_once("no_key",
                  f"no {SOURCE_ENV} / {OVERRIDE_ENV}: cookie pool is stored "
                  f"WITHOUT encryption (dev/test mode)")
        return None
    return _derive(source)


def enabled() -> bool:
    return current_key() is not None


def is_encrypted(stored: str) -> bool:
    return isinstance(stored, str) and stored.startswith(PREFIX)


def encrypt(plain_b64: str) -> str:
    """Stored form of a cookie. Plaintext (unchanged) when no key; an already
    encrypted value is returned as is."""
    if is_encrypted(plain_b64):
        return plain_b64
    key = current_key()
    if key is None:
        return plain_b64
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plain_b64.encode("utf-8"), None)
    return PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt(stored: str) -> str:
    """Plaintext base64 of a stored cookie. Legacy values pass through.
    Raises CookieUndecryptable (no value in the message) on failure."""
    if not is_encrypted(stored):
        return stored
    key = current_key()
    if key is None:
        raise CookieUndecryptable("no key configured")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.b64decode(stored[len(PREFIX):], validate=True)
        if len(raw) <= _NONCE_LEN:
            raise ValueError("short")
        return AESGCM(key).decrypt(raw[:_NONCE_LEN], raw[_NONCE_LEN:], None).decode("utf-8")
    except Exception as exc:
        raise CookieUndecryptable(type(exc).__name__) from None


def decode(stored: str) -> Optional[str]:
    """decrypt() that returns None instead of raising."""
    try:
        return decrypt(stored)
    except CookieUndecryptable:
        return None


def storage_state(stored: str) -> str:
    """"encrypted" | "legacy" | "undecryptable" — never the value."""
    if not is_encrypted(stored):
        return "legacy"
    return "encrypted" if decode(stored) is not None else "undecryptable"
