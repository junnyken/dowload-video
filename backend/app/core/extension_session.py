"""
Extension session token (owner 22/10/2026)
==========================================
The browser extension used to keep the web's Supabase access token, which
lives one hour and was never refreshed: an hour after "Kết nối tài khoản" the
extension silently became a guest (5 downloads a day) while the person was a
signed-in Pro user on the site. Sharing the web's refresh token is not an
option — Supabase rotates it, and two holders revoke each other's session.

So the server gives the extension its own token:

    vgx_<base64url(payload)>.<base64url(HMAC-SHA256)>
    payload = {"sub": user id, "email": …, "ec": email confirmed at, "iat": …, "exp": iat + 30 days, "gen": n}

- Signed with a key derived from SUPABASE_SERVICE_KEY (sha256 of a fixed label
  + the key — the service key itself is never used as a MAC key and never
  leaves the server). Changing that key invalidates every extension token.
- `gen`: a per-user counter in Redis (vidgrab:extgen:<uid>). Sign-out from the
  extension or the site bumps it: every older token stops working at once.
  Redis down -> the counter is not checked (the token's own expiry still is).
- The extension renews it (POST /extension/session with the current token)
  while it is used; nothing to do for the person for 30 days at a time.
- It is a session, not an API key: never metered like one.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional

PREFIX = "vgx_"
TTL_S = 30 * 24 * 3600
_GEN_KEY = "vidgrab:extgen:{}"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key() -> Optional[bytes]:
    secret = (os.environ.get("EXTENSION_SESSION_KEY") or os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not secret:
        return None
    return hashlib.sha256(b"vidgrab-extension-session-v1:" + secret.encode()).digest()


def _rc():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _gen(user_id: str) -> Optional[int]:
    try:
        v = _rc().get(_GEN_KEY.format(user_id))
        return int(v) if v else 0
    except Exception:  # noqa: BLE001  Redis down: generation not checked
        return None


def revoke(user_id: str) -> bool:
    """Every extension token of this user stops working."""
    try:
        _rc().incr(_GEN_KEY.format(user_id))
        return True
    except Exception:  # noqa: BLE001
        return False


def issue(user_id: str, email: Optional[str], now: Optional[int] = None,
          email_confirmed_at: Optional[str] = None) -> Optional[Dict[str, Any]]:
    key = _key()
    if not key or not user_id:
        return None
    iat = int(now if now is not None else time.time())
    gen = _gen(user_id)
    payload = {"sub": str(user_id), "email": email or None, "ec": email_confirmed_at or None,
               "iat": iat, "exp": iat + TTL_S, "gen": gen or 0}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
    return {"token": f"{PREFIX}{body}.{sig}", "expiresAt": payload["exp"]}


def verify(token: str, now: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """{"id", "email"} for a valid, unexpired, unrevoked token, else None."""
    key = _key()
    if not key or not isinstance(token, str) or not token.startswith(PREFIX) or len(token) > 2048:
        return None
    try:
        body, sig = token[len(PREFIX):].split(".", 1)
        want = _b64(hmac.new(key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want, sig):
            return None
        p = json.loads(_unb64(body))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(p, dict) or not p.get("sub"):
        return None
    if int(p.get("exp") or 0) <= int(now if now is not None else time.time()):
        return None
    gen = _gen(str(p["sub"]))
    if gen is not None and int(p.get("gen") or 0) < gen:
        return None
    return {"id": str(p["sub"]), "email": p.get("email"), "email_confirmed_at": p.get("ec"), "via_extension": True}
