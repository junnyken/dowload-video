"""
Telegram User MTProto Authentication API — Phase B1
====================================================
Allows users to authenticate their own Telegram account via MTProto (Telethon)
so VidGrab can download media from channels/groups the user is a member of,
while strictly respecting Telegram's `noforwards` content protection flag.

Endpoints (mounted at `/api/v1/telegram/auth`):
  - GET  /status       — server config status + user's active encrypted session info
  - POST /send-code    — initiate Telegram login code request
  - POST /verify-code  — complete sign-in (with optional 2FA password), store encrypted StringSession
  - POST /logout       — revoke and delete stored encrypted session

Security:
  - StringSession is encrypted at rest using AES-256-GCM (`cookie_crypto` + HKDF fallback)
    and masked using `secret_store.last4`.
  - Raw StringSession is NEVER returned in API responses or logs.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth_middleware import get_optional_user
from app.services.china_platforms.secret_store import last4

logger = logging.getLogger(__name__)

router = APIRouter()

_PENDING_TTL_SEC = 600          # 10 minutes for OTP verification
_SESSION_PREFIX = "tg:mtproto:session:"
_PENDING_PREFIX = "tg:mtproto:pending:"
_ENC_PREFIX = "enc:tg:v1:"

# In-memory fallback store when Redis is unavailable (local dev / unit tests)
_MEMORY_SESSIONS: dict[str, str] = {}
_MEMORY_PENDING: dict[str, tuple[str, float]] = {}


# ── Encryption Helpers ─────────────────────────────────────────────────────

def _derive_fallback_key() -> bytes:
    """Derive a 32-byte AES-GCM key from env secrets (never plaintext in storage)."""
    seed = (
        os.getenv("TELEGRAM_SESSION_SECRET")
        or os.getenv("COOKIE_POOL_ENCRYPTION_KEY")
        or os.getenv("SUPABASE_SERVICE_KEY")
        or "vidgrab-telegram-mtproto-secret-v1"
    )
    return hashlib.sha256(seed.encode("utf-8")).digest()


def encrypt_session_data(plaintext: str) -> str:
    """Encrypt session string or JSON payload before storing in Redis/memory."""
    if not plaintext:
        return ""
    if plaintext.startswith(_ENC_PREFIX) or plaintext.startswith("enc:v1:"):
        return plaintext
    # First try cookie_crypto if configured with SUPABASE_SERVICE_KEY
    try:
        from app.core import cookie_crypto
        if cookie_crypto.enabled():
            b64_inner = base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
            return cookie_crypto.encrypt(b64_inner)
    except Exception:
        pass
    # Guaranteed AES-256-GCM encryption even when SUPABASE_SERVICE_KEY is unset
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _derive_fallback_key()
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _ENC_PREFIX + base64.b64encode(nonce + ct).decode("ascii")


def decrypt_session_data(stored: str) -> Optional[str]:
    """Decrypt stored session string or JSON payload. Returns None on failure."""
    if not stored:
        return None
    if stored.startswith("enc:v1:"):
        try:
            from app.core import cookie_crypto
            dec_b64 = cookie_crypto.decrypt(stored)
            return base64.b64decode(dec_b64).decode("utf-8")
        except Exception:
            return None
    if stored.startswith(_ENC_PREFIX):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw = base64.b64decode(stored[len(_ENC_PREFIX):])
            if len(raw) <= 12:
                return None
            key = _derive_fallback_key()
            return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")
        except Exception:
            return None
    return stored


# ── Storage Helpers (Redis + In-Memory Fallback) ───────────────────────────

def _get_redis_safe():
    if not os.getenv("REDIS_URL"):
        return None
    try:
        from app.core.redis_client import get_redis
        r = get_redis()
        if r is not None:
            r.ping()
        return r
    except Exception:
        return None


def _user_storage_key(user: Optional[Dict[str, Any]]) -> str:
    if user and user.get("id"):
        return str(user["id"])
    return "default"


def mask_phone(phone: str) -> str:
    cleaned = re.sub(r"\s+", "", phone or "")
    if len(cleaned) <= 5:
        return "***"
    return f"{cleaned[:4]}****{cleaned[-3:]}"


def _normalize_phone(phone: str) -> str:
    cleaned = re.sub(r"[\s\-\(\)]", "", (phone or "").strip())
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def save_pending_login(user_key: str, phone: str, payload: dict) -> None:
    norm_phone = _normalize_phone(phone)
    key = f"{_PENDING_PREFIX}{user_key}:{norm_phone}"
    enc = encrypt_session_data(json.dumps(payload))
    r = _get_redis_safe()
    if r is not None:
        try:
            r.set(key, enc, ex=_PENDING_TTL_SEC)
            return
        except Exception:
            pass
    _MEMORY_PENDING[key] = (enc, time.time() + _PENDING_TTL_SEC)


def load_pending_login(user_key: str, phone: str) -> Optional[dict]:
    norm_phone = _normalize_phone(phone)
    key = f"{_PENDING_PREFIX}{user_key}:{norm_phone}"
    enc = None
    r = _get_redis_safe()
    if r is not None:
        try:
            raw = r.get(key)
            if raw:
                enc = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except Exception:
            pass
    if not enc and key in _MEMORY_PENDING:
        val, exp = _MEMORY_PENDING[key]
        if time.time() <= exp:
            enc = val
        else:
            _MEMORY_PENDING.pop(key, None)
    if not enc:
        return None
    dec = decrypt_session_data(enc)
    if not dec:
        return None
    try:
        return json.loads(dec)
    except Exception:
        return None


def delete_pending_login(user_key: str, phone: str) -> None:
    norm_phone = _normalize_phone(phone)
    key = f"{_PENDING_PREFIX}{user_key}:{norm_phone}"
    r = _get_redis_safe()
    if r is not None:
        try:
            r.delete(key)
        except Exception:
            pass
    _MEMORY_PENDING.pop(key, None)


def save_user_session(user_key: str, session_record: dict) -> None:
    """Encrypt and store user's MTProto StringSession + metadata."""
    key = f"{_SESSION_PREFIX}{user_key}"
    enc = encrypt_session_data(json.dumps(session_record))
    r = _get_redis_safe()
    if r is not None:
        try:
            r.set(key, enc)
        except Exception:
            pass
    _MEMORY_SESSIONS[key] = enc


def load_user_session(user_key: str = "default") -> Optional[dict]:
    """Load and decrypt user's MTProto session record. Falls back to 'default' if needed."""
    for uk in ([user_key, "default"] if user_key != "default" else ["default"]):
        key = f"{_SESSION_PREFIX}{uk}"
        enc = None
        r = _get_redis_safe()
        if r is not None:
            try:
                raw = r.get(key)
                if raw:
                    enc = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            except Exception:
                pass
        if not enc:
            enc = _MEMORY_SESSIONS.get(key)
        if enc:
            dec = decrypt_session_data(enc)
            if dec:
                try:
                    data = json.loads(dec)
                    if isinstance(data, dict) and data.get("session_str"):
                        return data
                except Exception:
                    pass
    return None


def get_raw_stored_session(user_key: str = "default") -> Optional[str]:
    """Return raw encrypted string from storage (for tests verifying encryption at rest)."""
    key = f"{_SESSION_PREFIX}{user_key}"
    r = _get_redis_safe()
    if r is not None:
        try:
            raw = r.get(key)
            if raw:
                return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except Exception:
            pass
    return _MEMORY_SESSIONS.get(key)


def delete_user_session(user_key: str = "default") -> bool:
    key = f"{_SESSION_PREFIX}{user_key}"
    removed = False
    r = _get_redis_safe()
    if r is not None:
        try:
            removed = bool(r.delete(key)) or removed
        except Exception:
            pass
    if key in _MEMORY_SESSIONS:
        _MEMORY_SESSIONS.pop(key, None)
        removed = True
    return removed


# ── Telethon / MTProto Abstraction ─────────────────────────────────────────

def is_telethon_installed() -> bool:
    try:
        import telethon  # noqa: F401
        return True
    except ImportError:
        return False


def get_server_api_credentials(
    override_api_id: Optional[int] = None,
    override_api_hash: Optional[str] = None,
) -> tuple[Optional[int], Optional[str]]:
    api_id = override_api_id
    if not api_id:
        raw_id = (os.getenv("TELEGRAM_API_ID") or "").strip()
        if raw_id.isdigit():
            api_id = int(raw_id)
    api_hash = (override_api_hash or os.getenv("TELEGRAM_API_HASH") or "").strip() or None
    return api_id, api_hash


class Telegram2FARequired(Exception):
    """Raised when the Telegram account requires a 2FA cloud password."""


async def mtproto_send_code(phone: str, api_id: int, api_hash: str) -> dict:
    """
    Connect via Telethon, call send_code_request(phone), and return
    {"phone_code_hash": str, "temp_session": str}.
    Can be monkeypatched in tests.
    """
    if not is_telethon_installed():
        raise RuntimeError("Thư viện telethon chưa được cài đặt trên máy chủ.")
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), int(api_id), str(api_hash))
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        temp_session = client.session.save()
        return {
            "phone_code_hash": getattr(sent, "phone_code_hash", ""),
            "temp_session": temp_session,
        }
    finally:
        await client.disconnect()


async def mtproto_verify_code(
    *,
    phone: str,
    code: str,
    phone_code_hash: str,
    temp_session: str,
    api_id: int,
    api_hash: str,
    password: Optional[str] = None,
) -> dict:
    """
    Complete Telethon sign-in using the code (and 2FA password if needed).
    Returns {"session_str": str, "username": str|None, "user_id": int|None, "phone": str}.
    Raises Telegram2FARequired if 2FA password is required but not provided.
    Can be monkeypatched in tests.
    """
    if not is_telethon_installed():
        raise RuntimeError("Thư viện telethon chưa được cài đặt trên máy chủ.")
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(temp_session), int(api_id), str(api_hash))
    await client.connect()
    try:
        try:
            user_obj = await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=phone_code_hash,
            )
        except Exception as exc:
            if exc.__class__.__name__ == "SessionPasswordNeededError":
                if not password:
                    raise Telegram2FARequired("Tài khoản Telegram bật xác thực 2 bước (2FA). Vui lòng nhập mật khẩu.") from exc
                user_obj = await client.sign_in(password=password)
            else:
                raise
        session_str = client.session.save()
        if not user_obj:
            user_obj = await client.get_me()
        return {
            "session_str": session_str,
            "username": getattr(user_obj, "username", None),
            "user_id": getattr(user_obj, "id", None),
            "phone": getattr(user_obj, "phone", None) or phone,
        }
    finally:
        await client.disconnect()


async def mtproto_logout(session_record: dict) -> None:
    """Best-effort Telethon log_out() to revoke the session on Telegram servers."""
    if not is_telethon_installed():
        return
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        api_id, api_hash = get_server_api_credentials(
            session_record.get("api_id"),
            session_record.get("api_hash"),
        )
        if not api_id or not api_hash:
            return
        client = TelegramClient(StringSession(session_record["session_str"]), int(api_id), str(api_hash))
        await client.connect()
        try:
            await client.log_out()
        finally:
            await client.disconnect()
    except Exception as exc:
        logger.debug("Telethon log_out best-effort ignored: %s", exc)


# ── Request Models ─────────────────────────────────────────────────────────

class SendCodeRequest(BaseModel):
    phone: str
    api_id: Optional[int] = None
    api_hash: Optional[str] = None


class VerifyCodeRequest(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/status")
async def telegram_auth_status(user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    """Return server MTProto readiness and current user's session status."""
    api_id, api_hash = get_server_api_credentials()
    user_key = _user_storage_key(user)
    sess = load_user_session(user_key)
    server_configured = bool(api_id and api_hash)
    connected = bool(sess and sess.get("session_str"))
    return {
        "server_configured": server_configured,
        "telethon_installed": is_telethon_installed(),
        "connected": connected,
        "phone_masked": sess.get("phone_masked") if connected else None,
        "username": sess.get("username") if connected else None,
        "telegram_user_id": sess.get("telegram_user_id") if connected else None,
        "session_last4": last4(sess.get("session_str", "")) if connected else None,
    }


@router.post("/send-code")
async def telegram_send_code(
    body: SendCodeRequest,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    """Initiate Telegram login code request and store encrypted pending state."""
    phone = _normalize_phone(body.phone)
    if not phone or len(phone) < 7:
        raise HTTPException(status_code=400, detail="Số điện thoại không hợp lệ.")

    api_id, api_hash = get_server_api_credentials(body.api_id, body.api_hash)
    if not api_id or not api_hash:
        raise HTTPException(
            status_code=400,
            detail="Máy chủ chưa cấu hình TELEGRAM_API_ID / TELEGRAM_API_HASH. Vui lòng nhập API ID và API Hash.",
        )

    user_key = _user_storage_key(user)
    try:
        res = await mtproto_send_code(phone, api_id, api_hash)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Không gửi được mã xác thực Telegram: {exc}")

    save_pending_login(user_key, phone, {
        "phone": phone,
        "phone_code_hash": res["phone_code_hash"],
        "temp_session": res.get("temp_session", ""),
        "api_id": api_id,
        "api_hash": api_hash,
        "created_at": time.time(),
    })

    return {
        "status": "code_sent",
        "phone_masked": mask_phone(phone),
        "expires_in_seconds": _PENDING_TTL_SEC,
    }


@router.post("/verify-code")
async def telegram_verify_code(
    body: VerifyCodeRequest,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
):
    """Complete Telethon sign-in (with 2FA support) and store encrypted StringSession."""
    phone = _normalize_phone(body.phone)
    code = (body.code or "").strip()
    if not phone or not code:
        raise HTTPException(status_code=400, detail="Vui lòng nhập số điện thoại và mã OTP.")

    user_key = _user_storage_key(user)
    pending = load_pending_login(user_key, phone)
    if not pending:
        raise HTTPException(
            status_code=400,
            detail="Phiên xác thực đã hết hạn hoặc không tồn tại. Vui lòng gửi lại mã.",
        )

    try:
        signed = await mtproto_verify_code(
            phone=phone,
            code=code,
            phone_code_hash=pending["phone_code_hash"],
            temp_session=pending.get("temp_session", ""),
            api_id=int(pending["api_id"]),
            api_hash=str(pending["api_hash"]),
            password=body.password,
        )
    except Telegram2FARequired as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "telegram_2fa_required",
                "requires_2fa": True,
                "user_message": str(exc),
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Xác thực Telegram thất bại: {exc}")

    session_record = {
        "session_str": signed["session_str"],
        "phone_masked": mask_phone(phone),
        "username": signed.get("username"),
        "telegram_user_id": signed.get("user_id"),
        "api_id": pending["api_id"],
        "api_hash": pending["api_hash"],
        "connected_at": time.time(),
    }
    save_user_session(user_key, session_record)
    delete_pending_login(user_key, phone)

    return {
        "status": "connected",
        "connected": True,
        "phone_masked": session_record["phone_masked"],
        "username": session_record["username"],
        "telegram_user_id": session_record["telegram_user_id"],
    }


@router.post("/logout")
async def telegram_logout(user: Optional[Dict[str, Any]] = Depends(get_optional_user)):
    """Revoke and delete the stored encrypted Telegram session."""
    user_key = _user_storage_key(user)
    sess = load_user_session(user_key)
    if sess:
        await mtproto_logout(sess)
    removed = delete_user_session(user_key)
    return {
        "status": "logged_out",
        "connected": False,
        "removed": removed,
    }
