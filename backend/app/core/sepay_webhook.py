"""
SePay Webhook Handler — xác thực + parse + xử lý giao dịch ngân hàng.

Xác thực: HMAC-SHA256
  Header: X-SePay-Signature: sha256=<hex>
  Header: X-SePay-Timestamp: <unix_ts>
  Chuỗi ký: "{timestamp}.{raw_body}"
  Secret: env SEPAY_WEBHOOK_SECRET

Tham khảo: https://developer.sepay.vn/en/sepay-webhooks/xac-thuc
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Regex to find VGP code in transfer content
_VGP_RE = re.compile(r"(VGP[0-9A-Z]{8})", re.IGNORECASE)

# Max time skew for timestamp validation (seconds)
MAX_TIMESTAMP_SKEW = 300  # 5 minutes


def _secret() -> str:
    """Get SePay webhook secret from env or admin settings."""
    secret = os.getenv("SEPAY_WEBHOOK_SECRET", "").strip()
    if secret:
        return secret
    try:
        from app.core.plans import get_settings  # noqa: PLC0415
        return (get_settings().get("sepay_webhook_secret") or "").strip()
    except Exception:
        return ""


def verify(raw_body: bytes, headers: Dict[str, str]) -> Tuple[bool, str]:
    """Verify SePay webhook HMAC-SHA256 signature.

    Returns (is_valid, error_message).
    """
    secret = _secret()
    if not secret:
        return False, "no_webhook_secret_configured"

    sig_header = headers.get("x-sepay-signature", "").strip()
    timestamp = headers.get("x-sepay-timestamp", "").strip()

    if not sig_header:
        return False, "missing_signature"
    if not timestamp:
        return False, "missing_timestamp"

    # Check timestamp skew
    try:
        ts = int(timestamp)
        now = int(time.time())
        if abs(now - ts) > MAX_TIMESTAMP_SKEW:
            return False, "timestamp_too_old"
    except (ValueError, TypeError):
        return False, "invalid_timestamp"

    # Build signing string: "{timestamp}.{raw_body}"
    signing_string = f"{timestamp}.".encode("utf-8") + raw_body

    # Compute expected HMAC
    expected = hmac.new(
        secret.encode("utf-8"),
        signing_string,
        hashlib.sha256,
    ).hexdigest()

    # Compare: signature format is "sha256=<hex>"
    actual = sig_header
    if actual.startswith("sha256="):
        actual = actual[7:]

    if not hmac.compare_digest(expected, actual):
        return False, "signature_mismatch"

    return True, ""


def parse(raw_body: bytes) -> Optional[Dict[str, Any]]:
    """Parse SePay webhook payload.

    SePay sends:
    {
        "id": 123456,
        "gateway": "ACB",
        "transactionDate": "2026-11-03 14:30:00",
        "accountNumber": "12345678",
        "subAccount": null,
        "transferType": "in",
        "transferAmount": 49000,
        "accumulated": 1234567,
        "code": null,
        "content": "VGP3X7KM2NQ thanh toan Pro",
        "referenceCode": "FT26307...",
        "description": "..."
    }
    """
    try:
        data = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    # Only process incoming transfers
    if data.get("transferType", "").lower() != "in":
        return None

    # Extract VGP code from the 'code' field or 'content' field
    code = (data.get("code") or "").strip().upper()
    if not code or not code.startswith("VGP"):
        # Try to find VGP code in the content/description
        content = data.get("content") or data.get("description") or ""
        match = _VGP_RE.search(content)
        if match:
            code = match.group(1).upper()
        else:
            code = ""

    # Parse amount
    try:
        amount = int(data.get("transferAmount") or 0)
    except (ValueError, TypeError):
        amount = 0

    # Parse transaction date (Vietnam timezone → UTC)
    tx_date_str = data.get("transactionDate") or ""
    tx_date = None
    if tx_date_str:
        try:
            # SePay sends Vietnam time (UTC+7)
            from zoneinfo import ZoneInfo
            naive = datetime.strptime(tx_date_str, "%Y-%m-%d %H:%M:%S")
            vn_tz = ZoneInfo("Asia/Ho_Chi_Minh")
            tx_date = naive.replace(tzinfo=vn_tz).astimezone(timezone.utc)
        except Exception:
            tx_date = datetime.now(timezone.utc)

    return {
        "sepay_id": str(data.get("id", "")),
        "gateway": data.get("gateway", ""),
        "code": code,
        "amount": amount,
        "tx_date": tx_date.isoformat() if tx_date else None,
        "reference": data.get("referenceCode", ""),
        "content_raw_length": len(data.get("content") or ""),
        # Do NOT log actual content — may contain PII
    }


def handle(raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
    """Full webhook handler: verify → parse → match order → activate.

    Always returns 200 with result dict (SePay retries on non-200).
    """
    from app.core.database import get_service_client  # noqa: PLC0415

    # 1. Verify signature
    valid, err = verify(raw_body, headers)
    if not valid:
        logger.warning("SePay webhook verification failed: %s", err)
        return {"success": False, "error": err}

    # 2. Parse payload
    parsed = parse(raw_body)
    if not parsed:
        return {"success": True, "action": "ignored_not_incoming"}

    sepay_id = parsed["sepay_id"]
    code = parsed["code"]
    amount = parsed["amount"]

    # 3. Record payment event (idempotency)
    db = get_service_client()
    try:
        existing = (
            db.table("payment_events")
            .select("id")
            .eq("provider", "sepay")
            .eq("provider_event_id", sepay_id)
            .maybe_single()
            .execute()
        )
        if existing.data:
            return {"success": True, "action": "duplicate", "sepay_id": sepay_id}
    except Exception:
        pass

    # 4. Find matching order by code
    if not code:
        _log_event(db, sepay_id, "sepay_no_code", amount, None, "unmatched")
        return {"success": True, "action": "unmatched_no_code"}

    from app.core.payment_orders import get_order_by_code, activate_order  # noqa: PLC0415

    order = get_order_by_code(code)
    if not order:
        _log_event(db, sepay_id, "sepay_code_not_found", amount, None, "unmatched")
        logger.info("SePay: code %s not found in pending orders", code)
        return {"success": True, "action": "unmatched_code_not_found", "code": code}

    order_id = order["id"]

    # 5. Activate order
    evidence = {
        "provider": "sepay",
        "sepay_id": sepay_id,
        "gateway": parsed["gateway"],
        "reference": parsed["reference"],
        "tx_date": parsed["tx_date"],
    }

    ok, msg = activate_order(
        order_id=order_id,
        paid_amount=amount,
        provider_ref=sepay_id,
        evidence=evidence,
    )

    match_type = "matched" if ok else msg
    _log_event(db, sepay_id, "sepay_payment", amount, order_id, match_type)

    logger.info(
        "SePay webhook: order=%s code=%s amount=%d result=%s",
        order_id, code, amount, msg,
    )

    return {"success": True, "action": msg, "order_id": str(order_id)}


def _log_event(
    db, sepay_id: str, event_type: str, amount: int,
    order_id: Optional[str], match_type: str,
) -> None:
    """Record in payment_events for audit trail."""
    try:
        db.table("payment_events").insert({
            "provider": "sepay",
            "provider_event_id": sepay_id,
            "event_type": event_type,
            "amount": amount,
            "order_id": order_id,
            "match_type": match_type,
            "processed": True,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        # Duplicate — already recorded
        if "unique" not in str(e).lower():
            logger.warning("SePay event log failed: %s", e)
