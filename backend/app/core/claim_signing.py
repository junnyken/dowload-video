"""
Ed25519 signatures for the Windows app's quota (PLAN-32E §5.2 (a), P3, task #6172)
==================================================================================
The app (>= 0.10.0) checks these in Rust before it lets yt-dlp start, so a
fake API answer in the webview (hosts file + own CA + mitmproxy saying
"allowed": true) is no longer enough to download past the daily allowance.

Two signed strings, same format:

    b64url(payload JSON) + "." + b64url(Ed25519 signature over the first part)

The signature covers the ASCII of the encoded payload, so nobody has to
re-serialise JSON byte-for-byte (Python and Rust read the same bytes).

  policy  GET /client/version → "policy"
          {v:1, kid, requireToken, grace, min, iat, exp(+24 h)}
          requireToken = CLIENT_QUOTA_ENABLED and MODE=enforce and
          ENFORCE_FOR non-empty. Tells the app whether a download needs a token
          and how many offline downloads a day it may grant itself.
  token   claim / claim-batch items → "token"; /fetch-link and
          /client/douyin/video (app only) → "vgToken"
          {v:1, kid, cid, uh:[sha256(url)[:32] …], dev, iat, exp(+2 h)}
          uh = hash of the EXACT url strings the app will hand to Rust;
          dev = first 32 hex of the X-VG-Device header (the machine).

Key: CLIENT_QUOTA_SIGNING_KEY = base64 of the raw 32-byte Ed25519 seed,
CLIENT_QUOTA_SIGNING_KID (default "k1"). Generate with
backend/scripts/gen_claim_signing_key.py. Absent or unreadable key → no policy,
no tokens: every answer is exactly what it was before P3 and app 0.10 behaves
like 0.9 (PLAN-32E §7.4 rollback). Rotation: docs/desktop/P3-KEY-ROTATION.md.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import secrets
import time
from typing import Dict, Iterable, Optional

logger = logging.getLogger(__name__)

TOKEN_TTL_SEC = 2 * 3600
POLICY_TTL_SEC = 24 * 3600
DEFAULT_KID = "k1"
_KID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")

_cache: Dict[str, object] = {"raw": None, "key": None}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def url_hash(url: str) -> str:
    """sha256 of the url string as sent, first 32 hex (Rust: claim_token.rs)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def decode_seed(raw: str) -> Optional[bytes]:
    """base64 (standard or url-safe, padding optional) of exactly 32 bytes."""
    s = (raw or "").strip()
    if not s:
        return None
    for dec in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            seed = dec(s + "=" * (-len(s) % 4))
        except (binascii.Error, ValueError):
            continue
        if len(seed) == 32:
            return seed
    return None


def _private_key():
    raw = (os.environ.get("CLIENT_QUOTA_SIGNING_KEY") or "").strip()
    if not raw:
        return None
    if _cache["raw"] == raw:
        return _cache["key"]
    key = None
    seed = decode_seed(raw)
    if seed is None:
        # Never log the value itself.
        logger.warning("CLIENT_QUOTA_SIGNING_KEY is set but is not base64 of 32 bytes: "
                       "claim tokens and policy are OFF")
    else:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: PLC0415
        key = Ed25519PrivateKey.from_private_bytes(seed)
    _cache["raw"], _cache["key"] = raw, key
    return key


def signing_enabled() -> bool:
    return _private_key() is not None


def kid() -> str:
    k = (os.environ.get("CLIENT_QUOTA_SIGNING_KID") or DEFAULT_KID).strip()
    return k if _KID_RE.match(k) else DEFAULT_KID


def public_key_b64() -> Optional[str]:
    """Standard base64 of the 32-byte public key (what the app embeds)."""
    key = _private_key()
    if key is None:
        return None
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode("ascii")


def sign(payload: dict) -> Optional[str]:
    key = _private_key()
    if key is None:
        return None
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return body + "." + _b64url(key.sign(body.encode("ascii")))


def claim_token(claim_id: str, urls: Iterable[Optional[str]], device: Optional[str],
                now: Optional[int] = None) -> Optional[str]:
    """Token for one claim. None (field left out) without a key or without
    the app's machine header — Rust compares `dev` with its own machine."""
    if not signing_enabled() or not device:
        return None
    hashes = [url_hash(u) for u in urls if u]
    if not hashes:
        return None
    ts = int(time.time()) if now is None else int(now)
    return sign({"v": 1, "kid": kid(), "cid": claim_id, "uh": hashes, "dev": device.lower()[:32],
                 "iat": ts, "exp": ts + TOKEN_TTL_SEC})


def server_route_token(urls: Iterable[Optional[str]], device: Optional[str]) -> Optional[str]:
    """Token for a download the SERVER already counted (/fetch-link direct
    link, Douyin CDN link). Only absolute http(s) links are bound; a
    server-relative "/api/…" file lives on the API host, which Rust lets
    through without a token."""
    absolute = [u for u in urls if isinstance(u, str) and u.lower().startswith(("http://", "https://"))]
    return claim_token("s" + secrets.token_hex(11), absolute, device)


def policy(require_token: bool, grace: int, min_version: str, now: Optional[int] = None) -> Optional[str]:
    if not signing_enabled():
        return None
    ts = int(time.time()) if now is None else int(now)
    return sign({"v": 1, "kid": kid(), "requireToken": bool(require_token), "grace": max(0, int(grace)),
                 "min": (min_version or "")[:32], "iat": ts, "exp": ts + POLICY_TTL_SEC})


def token_matches(payload: dict, url: str, device: str) -> bool:
    """The binding checks Rust makes after the signature (claim_token.rs):
    the url hash is listed and the machine is this one."""
    uh = payload.get("uh")
    hashes = uh if isinstance(uh, list) else [uh]
    return url_hash(url) in hashes and payload.get("dev") == (device or "").lower()[:32]


def verify(signed: str, public_keys: Dict[str, bytes], now: Optional[int] = None) -> Optional[dict]:
    """Python twin of the Rust check (tests, support tooling). Returns the
    payload when the signature is right for the payload's kid and it has not
    expired; None otherwise."""
    try:
        body, sig = signed.split(".")
        payload = json.loads(_b64url_dec(body))
        pub = public_keys.get(payload.get("kid"))
        if pub is None or payload.get("v") != 1:
            return None
        from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(_b64url_dec(sig), body.encode("ascii"))
        except InvalidSignature:
            return None
        ts = int(time.time()) if now is None else int(now)
        if int(payload.get("exp", 0)) <= ts:
            return None
        return payload
    except Exception:  # noqa: BLE001  malformed = invalid
        return None
