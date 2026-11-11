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

# Maximum allowed difference (VND) when matching an account-level VG transfer
# against the user's plan catalog (Case A).
VG_PRICE_TOLERANCE_VND = 1_000


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
    return ""


def verify(raw_body: bytes, headers: Dict[str, str]) -> Tuple[bool, str]:
    """Verify SePay webhook HMAC-SHA256 signature (or Apikey header fallback).

    Returns (is_valid, error_message).
    """
    secret = _secret()
    if not secret:
        logger.error("SePay webhook verification rejected: SEPAY_WEBHOOK_SECRET is not configured")
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


def _is_unique_violation(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    msg = f"{code} {exc}".lower()
    return (
        code == "23505"
        or "23505" in msg
        or "duplicate key" in msg
        or "unique constraint" in msg
        or "already exists" in msg
        or "unique" in msg
    )


def _claim_event(db, sepay_id: str, amount: int) -> str:
    """
    Atomically pre-claim (provider='sepay', provider_event_id=sepay_id) via INSERT
    on UNIQUE (provider, provider_event_id) BEFORE any tier mutation.
    Returns 'claimed' or 'duplicate'. Raises HTTP 503 on any non-unique DB error
    so SePay retries later (fail-closed).
    """
    try:
        db.table("payment_events").insert({
            "provider": "sepay",
            "provider_event_id": sepay_id,
            "event_type": "sepay_claiming",
            "amount": amount,
            "match_type": "claiming",
            "processed": False,
        }).execute()
        return "claimed"
    except Exception as exc:
        if _is_unique_violation(exc):
            return "duplicate"
        logger.error("SePay payment_events pre-claim DB error (sepay_id=%s): %s", sepay_id, exc)
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(status_code=503, detail="Payment event store unavailable") from exc


def handle(raw_body: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
    """Full webhook handler: verify → parse → pre-claim → match order → activate.

    Returns 200 with result dict for verified & recorded events; raises HTTP 503
    if payment_events DB pre-claim fails so SePay retries safely.
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

    # 3. Pre-claim payment event BEFORE any upgrade or order activation (idempotency via UNIQUE)
    db = get_service_client()
    if _claim_event(db, sepay_id, amount) == "duplicate":
        return {"success": True, "action": "duplicate", "sepay_id": sepay_id}

    from app.core.payment_orders import (  # noqa: PLC0415
        get_order_by_code,
        activate_order,
        _upgrade_user_pro,
        check_downgrade_active_subscription,
        record_webhook_manual_review,
    )

    # 4. Find matching order by code
    if not code:
        record_webhook_manual_review(
            db,
            provider="sepay",
            provider_event_id=sepay_id,
            event_type="sepay_no_code",
            amount=amount,
            raw_payload=parsed,
            reason="unmatched_no_code",
            status="needs_manual_review",
        )
        _log_event(db, sepay_id, "sepay_no_code", amount, None, "needs_manual_review")
        return {"success": True, "action": "unmatched_no_code", "status": "needs_manual_review"}

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
                from app.core.payment_orders import catalog as _po_catalog  # noqa: PLC0415
                cat = _po_catalog(user_id=uid)
                matched_plan = None
                best_diff: Optional[int] = None
                for p in cat:
                    plan_vnd = int(p.get("vnd") or 0)
                    if plan_vnd <= 0:
                        continue
                    diff = abs(amount - plan_vnd)
                    if diff <= VG_PRICE_TOLERANCE_VND and (best_diff is None or diff < best_diff):
                        matched_plan = p
                        best_diff = diff

                if not matched_plan:
                    record_webhook_manual_review(
                        db,
                        provider="sepay",
                        provider_event_id=sepay_id,
                        event_type="sepay_vg_underpaid",
                        user_id=uid,
                        amount=amount,
                        raw_payload=parsed,
                        reason="amount_not_matching_user_catalog",
                        status="needs_manual_review",
                    )
                    _log_event(db, sepay_id, "sepay_vg_underpaid", amount, None, "underpaid")
                    return {
                        "success": True,
                        "action": "underpaid",
                        "status": "needs_manual_review",
                        "code": code,
                        "user_id": uid,
                    }

                days = int(matched_plan.get("days") or 30)
                plan_id = str(matched_plan.get("id") or "pro_month")
                target_tier = str(matched_plan.get("tier") or "pro")
                plan_amount = int(matched_plan.get("vnd") or amount)

                is_down, cur_tier, _exp = check_downgrade_active_subscription(uid, target_tier, db=db)
                if is_down:
                    record_webhook_manual_review(
                        db,
                        provider="sepay",
                        provider_event_id=sepay_id,
                        event_type="sepay_vg_downgrade",
                        user_id=uid,
                        current_tier=cur_tier,
                        target_tier=target_tier,
                        amount=amount,
                        raw_payload=parsed,
                        reason="downgrade_while_higher_tier_active",
                        status="needs_manual_review",
                    )
                    _log_event(db, sepay_id, "sepay_vg_downgrade", amount, None, "needs_manual_review")
                    return {
                        "success": True,
                        "action": "needs_manual_review",
                        "status": "needs_manual_review",
                        "code": code,
                        "user_id": uid,
                    }

                try:
                    _upgrade_user_pro(
                        uid,
                        days,
                        f"sepay_vg_{sepay_id}",
                        target_tier=target_tier,
                        raise_on_error=True,
                    )
                except Exception as upg_exc:
                    err_text = str(upg_exc)[:500]
                    logger.error(
                        "SePay Case A upgrade failed after pre-claim (sepay_id=%s user=%s): %s",
                        sepay_id, uid, err_text,
                    )
                    record_webhook_manual_review(
                        db,
                        provider="sepay",
                        provider_event_id=sepay_id,
                        event_type="sepay_vg_upgrade_failed",
                        user_id=uid,
                        target_tier=target_tier,
                        amount=amount,
                        raw_payload=parsed,
                        reason=f"upgrade_failed:{type(upg_exc).__name__}",
                        status="needs_manual_review",
                    )
                    _log_event(
                        db,
                        sepay_id,
                        "sepay_vg_upgrade_failed",
                        amount,
                        None,
                        "failed",
                        processed=False,
                        error=err_text,
                    )
                    return {
                        "success": True,
                        "action": "failed",
                        "status": "failed",
                        "code": code,
                        "user_id": uid,
                    }

                now_iso = datetime.now(timezone.utc).isoformat()
                order_id_recorded = None
                try:
                    ins = db.table("payment_orders").insert({
                        "user_id": uid,
                        "plan": plan_id,
                        "rail": "vietqr",
                        "currency": "VND",
                        "amount": plan_amount,
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
                return {"success": True, "action": "activated_vg_account", "user_id": uid, "days": days, "tier": target_tier}

    # Case B: Order-based transfer code VGP + 8 chars (from /checkout page)
    order = get_order_by_code(code)
    if not order:
        non_pending_uid: Optional[str] = None
        non_pending_oid: Optional[str] = None
        try:
            any_ord = (
                db.table("payment_orders")
                .select("id, user_id, status")
                .eq("code", code.upper().strip())
                .limit(1)
                .execute()
            ).data or []
            if any_ord:
                non_pending_oid = str(any_ord[0].get("id") or "") or None
                non_pending_uid = str(any_ord[0].get("user_id") or "") or None
        except Exception:
            pass
        record_webhook_manual_review(
            db,
            provider="sepay",
            provider_event_id=sepay_id,
            event_type="sepay_code_not_found",
            user_id=non_pending_uid,
            order_id=non_pending_oid,
            order_code=code,
            amount=amount,
            raw_payload=parsed,
            reason="unmatched_code_not_found",
            status="needs_manual_review",
        )
        _log_event(db, sepay_id, "sepay_code_not_found", amount, non_pending_oid, "needs_manual_review")
        logger.info("SePay: code %s not found in pending orders -> manual review", code)
        return {"success": True, "action": "unmatched_code_not_found", "status": "needs_manual_review", "code": code}

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

    out: Dict[str, Any] = {"success": True, "action": msg, "order_id": str(order_id)}
    if msg == "needs_manual_review":
        out["status"] = "needs_manual_review"
    return out


def _log_event(
    db, sepay_id: str, event_type: str, amount: int,
    order_id: Optional[str], match_type: str,
    processed: bool = True,
    error: Optional[str] = None,
) -> None:
    """Finalize pre-claimed row in payment_events for audit trail (or insert if absent)."""
    fields: Dict[str, Any] = {
        "event_type": event_type,
        "amount": amount,
        "order_id": order_id,
        "match_type": match_type,
        "processed": processed,
        "processed_at": datetime.now(timezone.utc).isoformat() if processed else None,
        "error": error,
    }
    try:
        upd = (
            db.table("payment_events")
            .update(fields)
            .eq("provider", "sepay")
            .eq("provider_event_id", sepay_id)
            .execute()
        )
        if not upd.data:
            db.table("payment_events").insert({
                "provider": "sepay",
                "provider_event_id": sepay_id,
                **fields,
            }).execute()
    except Exception as e:
        if not _is_unique_violation(e):
            logger.warning("SePay event finalize failed: %s", e)
