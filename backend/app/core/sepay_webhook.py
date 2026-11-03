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

# Regex to find VGP order code (8 Crockford Base32) or VG user code (8 hex) in transfer content
_VGP_RE = re.compile(r"(VGP[0-9A-Z]{8})", re.IGNORECASE)
_VG_USER_RE = re.compile(r"\b(VG[0-9A-F]{8})\b", re.IGNORECASE)

# Max time skew for timestamp validation (seconds)
MAX_TIMESTAMP_SKEW = 300  # 5 minutes

DEFAULT_SEPAY_SECRET = "whsec_hZfaB5HXGJquU4rM3qDowUZYiPqMcU6Z"


def _secret() -> str:
    """Get SePay webhook secret from env or admin settings."""
    secret = os.getenv("SEPAY_WEBHOOK_SECRET", "").strip()
    if secret:
        return secret
    try:
        from app.core.plans import get_settings  # noqa: PLC0415
        s = (get_settings().get("sepay_webhook_secret") or "").strip()
        if s:
            return s
    except Exception:
        pass
    return DEFAULT_SEPAY_SECRET


def verify(raw_body: bytes, headers: Dict[str, str]) -> Tuple[bool, str]:
    """Verify SePay webhook HMAC-SHA256 signature (or Apikey header fallback).

    Returns (is_valid, error_message).
    """
    secret = _secret()
    if not secret:
        return False, "no_webhook_secret_configured"

    # Candidate secrets: full string and stripped 'whsec_' prefix
    candidates = [secret]
    if secret.startswith("whsec_") and len(secret) > 6:
        candidates.append(secret[6:])

    # 1. Check Authorization: Apikey <secret> or Bearer <secret>
    auth_header = headers.get("authorization", "").strip()
    if auth_header:
        parts = auth_header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() in ("apikey", "bearer"):
            token = parts[1].strip()
            for cand in candidates:
                if hmac.compare_digest(cand, token):
                    return True, ""

    # 2. Check HMAC-SHA256 signature
    sig_header = (
        headers.get("x-sepay-signature", "")
        or headers.get("x-webhook-signature", "")
        or headers.get("x-signature", "")
    ).strip()
    timestamp = (
        headers.get("x-sepay-timestamp", "")
        or headers.get("x-webhook-timestamp", "")
    ).strip()

    if not sig_header:
        return False, "missing_signature"

    actual = sig_header
    if actual.startswith("sha256="):
        actual = actual[7:]

    # If timestamp provided, validate skew and test "{timestamp}.{raw_body}"
    if timestamp:
        try:
            ts = int(timestamp)
            now = int(time.time())
            if abs(now - ts) > MAX_TIMESTAMP_SKEW:
                return False, "timestamp_too_old"
        except (ValueError, TypeError):
            return False, "invalid_timestamp"

        signing_ts = f"{timestamp}.".encode("utf-8") + raw_body
        for cand in candidates:
            expected_ts = hmac.new(cand.encode("utf-8"), signing_ts, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected_ts, actual):
                return True, ""

    # Also test direct HMAC over raw_body (used by some SePay webhook versions)
    for cand in candidates:
        expected_raw = hmac.new(cand.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected_raw, actual):
            return True, ""

    return False, "signature_mismatch"


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

    # Extract VGP order code or VG account code from 'code' or 'content'
    code = (data.get("code") or "").strip().upper()
    content = data.get("content") or data.get("description") or ""
    if not code or not (code.startswith("VGP") or code.startswith("VG")):
        match_vgp = _VGP_RE.search(content)
        if match_vgp:
            code = match_vgp.group(1).upper()
        else:
            match_vg = _VG_USER_RE.search(content)
            code = match_vg.group(1).upper() if match_vg else ""

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

    from app.core.payment_orders import get_order_by_code, activate_order, _upgrade_user_pro  # noqa: PLC0415

    # Case A: Account-based transfer code VG + 8 hex (from /pricing page)
    if code.startswith("VG") and not code.startswith("VGP") and len(code) == 10:
        from app.core import plans  # noqa: PLC0415
        prefix = plans.code_prefix(code)
        if prefix:
            lo = f"{prefix}-0000-0000-0000-000000000000"
            hi = f"{prefix}-ffff-ffff-ffff-ffffffffffff"
            rows = (
                db.table("profiles")
                .select("id")
                .gte("id", lo)
                .lte("id", hi)
                .limit(2)
                .execute()
            ).data or []
            if len(rows) == 1:
                uid = str(rows[0]["id"])
                s = plans.get_settings()
                price_month = int(s.get("price_month_vnd") or 49_000)
                price_year = int(s.get("price_year_vnd") or 399_000)
                if amount >= price_year:
                    days, plan_id = 365, "pro_year"
                elif amount >= price_month:
                    days, plan_id = 30, "pro_month"
                else:
                    _log_event(db, sepay_id, "sepay_vg_underpaid", amount, None, "underpaid")
                    return {"success": True, "action": "underpaid", "code": code, "user_id": uid}

                _upgrade_user_pro(uid, days, f"sepay_vg_{sepay_id}")
                now_iso = datetime.now(timezone.utc).isoformat()
                order_id_recorded = None
                try:
                    ins = db.table("payment_orders").insert({
                        "user_id": uid,
                        "plan": plan_id,
                        "rail": "vietqr",
                        "currency": "VND",
                        "amount": price_year if days == 365 else price_month,
                        "code": f"{code}_{sepay_id}",
                        "status": "paid",
                        "paid_amount": amount,
                        "paid_at": now_iso,
                        "activated_at": now_iso,
                        "provider_ref": sepay_id,
                        "evidence": {"provider": "sepay_vg_direct", "sepay_id": sepay_id, "gateway": parsed["gateway"]},
                    }).execute()
                    if ins.data:
                        order_id_recorded = ins.data[0].get("id")
                except Exception:
                    pass
                _log_event(db, sepay_id, "sepay_vg_payment", amount, order_id_recorded, "matched")
                return {"success": True, "action": "activated_vg_account", "user_id": uid, "days": days}

    # Case B: Order-based transfer code VGP + 8 chars (from /checkout page)
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
