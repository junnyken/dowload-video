"""
Web Push delivery (Phase 33 foundations, task #6256).

Before this module push was dead: pywebpush was in requirements but never
imported, the server POSTed bare JSON (no encryption, no VAPID) that every
push service rejects, and it POSTed to whatever endpoint a client had
submitted — an SSRF primitive.

Now:
  * payloads are encrypted (aes128gcm) and VAPID-signed via pywebpush;
  * endpoints must be https on a known browser push service (allow-list,
    PUSH_SERVICE_HOSTS / PUSH_SERVICE_SUFFIXES) AND pass the same public-IP
    check as user webhooks (app.core.webhook_guard: fresh DNS, request pinned
    to the validated IP, no redirects, 10 s, bounded read);
  * a 404/410 from the push service (subscription expired/unsubscribed)
    deletes that subscription row, as does an endpoint that fails the
    allow-list (rows stored before this check existed);
  * without a VAPID private key push is disabled: one log line, every send
    returns 0, nothing raises.

Environment (names only; values are secrets):
  PUSH_VAPID_PRIVATE_KEY  base64url raw P-256 private key (32 bytes) or DER/PEM
                          (alias VAPID_PRIVATE_KEY)
  PUSH_VAPID_PUBLIC_KEY   optional; base64url uncompressed public key. When set
                          it must match the private key — the key served to
                          browsers is always derived from the private key
                          (alias VAPID_PUBLIC_KEY)
  PUSH_VAPID_SUBJECT      "mailto:..." or "https://..." contact for the push
                          services (alias VAPID_SUBJECT); falls back to
                          FRONTEND_URL when that is an https URL
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.webhook_guard import (
    WebhookTarget,
    WebhookUrlError,
    post_to_target,
    validate_webhook_url,
)

logger = logging.getLogger(__name__)

# Push service hosts browsers hand out in PushSubscription.endpoint.
#   Chrome / Chromium / Opera / Samsung:  fcm.googleapis.com
#   Firefox:                              updates.push.services.mozilla.com
#   Safari (macOS 13+, iOS 16.4+):        web.push.apple.com (Apple documents
#                                         "*.push.apple.com" as the range)
#   Edge (Windows Push Notification Svc): <node>.notify.windows.com
PUSH_SERVICE_HOSTS = frozenset({
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "web.push.apple.com",
})
PUSH_SERVICE_SUFFIXES = (
    ".push.apple.com",
    ".notify.windows.com",
)

PUSH_TTL_SECONDS = 24 * 3600
MAX_ENDPOINT_LEN = 1024


class PushEndpointError(ValueError):
    """Endpoint is not an acceptable push service URL. `permanent` = retrying
    cannot help (the row should be dropped)."""

    def __init__(self, message: str, *, permanent: bool = True):
        super().__init__(message)
        self.permanent = permanent


# ── Endpoint validation ──────────────────────────────────────────────

def _host_allowed(host: str) -> bool:
    return host in PUSH_SERVICE_HOSTS or any(
        host.endswith(sfx) and len(host) > len(sfx) for sfx in PUSH_SERVICE_SUFFIXES
    )


def check_endpoint_shape(endpoint: str) -> str:
    """Syntax + allow-list only (no DNS). Returns the normalised host."""
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise PushEndpointError("endpoint is empty")
    endpoint = endpoint.strip()
    if len(endpoint) > MAX_ENDPOINT_LEN:
        raise PushEndpointError("endpoint is too long")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise PushEndpointError("endpoint is not a valid URL")
    if parts.scheme.lower() != "https":
        raise PushEndpointError("endpoint must be https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise PushEndpointError("endpoint must not contain credentials")
    if port not in (None, 443):
        raise PushEndpointError("endpoint must use the default https port")
    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not _host_allowed(host):
        raise PushEndpointError("endpoint is not a known browser push service")
    return host


def validate_push_endpoint(endpoint: str) -> WebhookTarget:
    """Allow-list check, then the webhook public-address check (fresh DNS).
    Raises PushEndpointError."""
    check_endpoint_shape(endpoint)
    try:
        return validate_webhook_url(endpoint.strip())
    except WebhookUrlError as e:
        raise PushEndpointError(str(e).replace("webhook_url", "endpoint"),
                                permanent=e.permanent) from e


# ── VAPID configuration ──────────────────────────────────────────────

_cfg_lock = threading.Lock()
_logged: set[str] = set()


def _log_once(level: int, msg: str, *args) -> None:
    with _cfg_lock:
        if msg in _logged:
            return
        _logged.add(msg)
    logger.log(level, msg, *args)


def _env(*names: str) -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _load_vapid():
    """(Vapid, public_key_b64url, subject) or None when push is disabled."""
    priv = _env("PUSH_VAPID_PRIVATE_KEY", "VAPID_PRIVATE_KEY")
    subject = _env("PUSH_VAPID_SUBJECT", "VAPID_SUBJECT")
    if not subject:
        fe = _env("FRONTEND_URL")
        if fe.startswith("https://"):
            subject = fe.rstrip("/")
    reason = None
    vapid = None
    public = ""
    if not priv:
        reason = "PUSH_VAPID_PRIVATE_KEY not set"
    elif not (subject.startswith("mailto:") or subject.startswith("https://")):
        reason = "PUSH_VAPID_SUBJECT not set (mailto:... or https://...)"
    else:
        try:
            from cryptography.hazmat.primitives import serialization
            from py_vapid import Vapid

            vapid = Vapid.from_string(private_key=priv.replace("\\n", "\n"))
            public = _b64url(vapid.public_key.public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            ))
        except Exception as e:
            vapid = None
            reason = f"PUSH_VAPID_PRIVATE_KEY unreadable ({type(e).__name__})"
    if vapid is None:
        _log_once(logging.WARNING, "[Push] Web Push disabled: %s", reason)
        return None
    env_pub = _env("PUSH_VAPID_PUBLIC_KEY", "VAPID_PUBLIC_KEY")
    if env_pub and env_pub.rstrip("=") != public:
        _log_once(logging.ERROR, "[Push] PUSH_VAPID_PUBLIC_KEY does not match the private key; "
                  "serving the key derived from PUSH_VAPID_PRIVATE_KEY")
    return vapid, public, subject


def public_key() -> str:
    """VAPID public key for PushManager.subscribe(), '' when push is disabled."""
    cfg = _load_vapid()
    return cfg[1] if cfg else ""


def is_enabled() -> bool:
    return _load_vapid() is not None


# ── Sending ───────────────────────────────────────────────────────────

class _Resp:
    """The slice of requests.Response pywebpush reads."""

    def __init__(self, status: int, error: Optional[str]):
        self.status_code = status
        self.reason = error or ""
        self.text = ""
        self.headers: dict = {}


class _PinnedSession:
    """requests.Session stand-in handed to pywebpush: re-validates the
    endpoint (allow-list + fresh DNS) and sends through webhook_guard."""

    def post(self, url, data=None, headers=None, timeout=None, **_kw):
        target = validate_push_endpoint(url)
        res = post_to_target(target, data or b"", dict(headers or {}))
        return _Resp(res.status_code, res.error)


def _send_one(cfg, sub: dict, data: str) -> int:
    """Returns the push service HTTP status. Raises PushEndpointError."""
    from pywebpush import WebPushException, webpush

    vapid, _pub, subject = cfg
    subscription_info = {
        "endpoint": sub["endpoint"],
        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth_key"]},
    }
    try:
        resp = webpush(
            subscription_info,
            data=data,
            vapid_private_key=vapid,
            vapid_claims={"sub": subject},
            ttl=PUSH_TTL_SECONDS,
            requests_session=_PinnedSession(),
        )
        return int(resp.status_code)
    except WebPushException as e:
        resp = getattr(e, "response", None)
        if resp is not None:
            return int(resp.status_code)
        raise PushEndpointError(f"bad subscription: {e}"[:200])


def _delete_subscription(sb, row: dict) -> None:
    try:
        q = sb.table("push_subscriptions").delete()
        if row.get("id"):
            q = q.eq("id", row["id"])
        else:
            q = q.eq("user_id", row.get("user_id")).eq("endpoint", row.get("endpoint"))
        q.execute()
    except Exception as e:
        logger.warning("[Push] could not delete dead subscription: %s", type(e).__name__)


def send_push(user_id: str, payload: dict[str, Any]) -> int:
    """Send `payload` (title/body/url/tag…, read by public/sw.js) to every
    subscription of `user_id`. Returns how many the push services accepted.
    Never raises."""
    if not user_id:
        return 0
    cfg = _load_vapid()
    if cfg is None:
        return 0
    try:
        from app.core.database import get_service_client

        sb = get_service_client()
        rows = (
            sb.table("push_subscriptions")
            .select("id, user_id, endpoint, p256dh, auth_key")
            .eq("user_id", str(user_id))
            .execute()
        ).data or []
    except Exception as e:
        logger.warning("[Push] subscription lookup failed for user %s: %s", user_id, type(e).__name__)
        return 0

    data = json.dumps(payload, ensure_ascii=False)
    sent = 0
    for row in rows:
        if not row.get("endpoint") or not row.get("p256dh") or not row.get("auth_key"):
            continue
        try:
            status = _send_one(cfg, row, data)
        except PushEndpointError as e:
            if e.permanent:
                logger.info("[Push] dropping subscription %s: %s", row.get("id"), e)
                _delete_subscription(sb, row)
            else:
                logger.info("[Push] subscription %s skipped: %s", row.get("id"), e)
            continue
        except Exception as e:
            logger.warning("[Push] send failed for subscription %s: %s", row.get("id"), type(e).__name__)
            continue
        if 200 <= status < 300:
            sent += 1
        elif status in (404, 410):
            # Expired or unsubscribed in the browser — the service will never
            # accept this endpoint again.
            _delete_subscription(sb, row)
        else:
            logger.info("[Push] push service answered %s for subscription %s", status, row.get("id"))
    return sent
