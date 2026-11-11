"""
Checkout API — tạo đơn, xem đơn, huỷ đơn.

POST /api/v1/checkout/orders          — tạo đơn mới (VietQR hoặc PayPal)
GET  /api/v1/checkout/orders          — danh sách đơn của user
GET  /api/v1/checkout/orders/{id}     — chi tiết 1 đơn (poll trạng thái)
POST /api/v1/checkout/orders/{id}/cancel — huỷ đơn pending
POST /api/v1/checkout/plans           — xem catalog gói bán
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.admin import verify_admin
from app.core.auth_middleware import get_required_user

router = APIRouter()
admin_router = APIRouter()


class CreateOrderRequest(BaseModel):
    plan: str = "pro_month"        # 'pro_month' | 'pro_year'
    rail: str = "vietqr"           # 'vietqr' | 'paypal'
    return_path: Optional[str] = None
    return_url: Optional[str] = None


# ── POST /orders — tạo đơn ──────────────────────────────────────────

@router.post("/orders")
async def create_order(
    payload: CreateOrderRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Create a payment order with VietQR info or PayPal checkout URL."""
    from app.core import payments_auto  # noqa: PLC0415

    if not payments_auto.visible(request):
        raise HTTPException(status_code=404, detail="Not Found")

    from app.core import payment_orders as po  # noqa: PLC0415
    from app.core.plans import get_settings  # noqa: PLC0415

    plan = po.plan_by_id(payload.plan, user_id=user["id"])
    if not plan:
        raise HTTPException(status_code=400, detail="plan_not_found")

    rail = payload.rail.lower()
    if rail not in ("vietqr", "paypal"):
        raise HTTPException(status_code=400, detail="rail must be 'vietqr' or 'paypal'")

    target_tier = str(plan.get("tier") or "pro").lower()
    is_down, cur_tier, exp = po.check_downgrade_active_subscription(user["id"], target_tier)
    if is_down:
        msg = po.downgrade_user_message(cur_tier, target_tier, exp)
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "cannot_downgrade_active_subscription",
                "message": msg,
                "user_message": msg,
                "current_tier": cur_tier,
                "target_tier": target_tier,
                "subscription_expiry": exp,
            },
        )

    # Create order
    order, err = po.create_order(user["id"], payload.plan, rail)
    if err:
        if err == "cannot_downgrade_active_subscription":
            msg = po.downgrade_user_message(cur_tier or "pro", target_tier, exp)
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "cannot_downgrade_active_subscription",
                    "message": msg,
                    "user_message": msg,
                    "current_tier": cur_tier,
                    "target_tier": target_tier,
                    "subscription_expiry": exp,
                },
            )
        status = 409 if err == "too_many_pending" else 400
        raise HTTPException(status_code=status, detail=err)

    result: Dict[str, Any] = {"order": order}

    # VietQR info
    if rail == "vietqr":
        s = get_settings()
        bank_bin = s.get("bank_bin") or os.getenv("BANK_BIN", "970416")  # ACB default
        bank_account = s.get("bank_account", "")
        bank_holder = s.get("bank_holder", "")
        bank_name = s.get("bank_name", "")

        if not bank_account:
            raise HTTPException(status_code=503, detail="rail_not_configured")

        qr_url = po.qr_image_url(
            bank_bin=bank_bin,
            bank_account=bank_account,
            bank_holder=bank_holder,
            amount=order.get("amount", 0),
            content=order.get("code", ""),
        )
        result["vietqr"] = {
            "bank_bin": bank_bin,
            "bank_name": bank_name,
            "bank_account": bank_account,
            "bank_holder": bank_holder,
            "content": order.get("code", ""),
            "amount": order.get("amount", 0),
            "qr_url": qr_url,
        }
    elif rail == "paypal":
        # Create PayPal checkout via existing flow
        try:
            pp_url, pp_session = await _create_paypal_order(
                user, plan, order, return_path=payload.return_path
            )
            result["paypal"] = {
                "checkout_url": pp_url,
                "approve_url": pp_url,
                "session_id": pp_session,
            }
            # Store PayPal order ID as provider_ref
            from app.core.database import get_service_client  # noqa: PLC0415
            db = get_service_client()
            db.table("payment_orders").update({
                "provider_ref": pp_session,
            }).eq("id", order["id"]).execute()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"PayPal error: {str(e)[:100]}")

    return result


_ALLOWED_RETURN_PATHS = {"/checkout", "/en/checkout", "/pricing", "/en/pricing"}


async def _create_paypal_order(
    user: Dict,
    plan: Dict,
    order: Dict,
    return_path: Optional[str] = None,
):
    """Create PayPal Order for the given plan, linking to our payment_order."""
    import httpx
    from urllib.parse import urlencode

    frontend = os.getenv("FRONTEND_URL", "").split(",")[0].strip().rstrip("/") or "https://dowloadvideo.io.vn"
    api_base = os.getenv("PUBLIC_API_URL", "").strip().rstrip("/") or "https://dvid-api.vibe1.tinhgon.xyz"
    safe_path = return_path if return_path in _ALLOWED_RETURN_PATHS else "/checkout"
    return_url = f"{frontend}{safe_path}?order_id={order['id']}&order={order['id']}&paypal_capture=1"
    cancel_url = f"{frontend}{safe_path}?order_id={order['id']}&order={order['id']}&canceled=1"
    usd_str = f"{plan['usd_cents'] / 100:.2f}"
    custom_val = f"{user['id']}:{order['id']}:{plan['id']}"

    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if pp_client_id and pp_secret:
        from app.api.payments import _paypal_base_url, _paypal_access_token  # noqa: PLC0415

        access_token = await _paypal_access_token(pp_client_id, pp_secret)
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "custom_id": custom_val,
                "invoice_id": order["code"],
                "description": f"VidGrab {plan['name_en']} ({order['code']})",
                "amount": {"currency_code": "USD", "value": usd_str},
            }],
            "application_context": {
                "brand_name": "VidGrab",
                "user_action": "PAY_NOW",
                "return_url": return_url,
                "cancel_url": cancel_url,
            },
        }

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{_paypal_base_url()}/v2/checkout/orders",
                json=body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code in (200, 201):
                odata = r.json()
                approve_url = next(
                    (lnk["href"] for lnk in odata.get("links", []) if lnk.get("rel") == "approve"),
                    None,
                )
                if approve_url:
                    return approve_url, odata.get("id", "")

    # Fallback: PayPal Email (Webscr Checkout + IPN auto-upgrade) or PayPal.Me
    from app.core.plans import get_settings as _get_plan_settings  # noqa: PLC0415
    paypal_me = (
        os.getenv("PAYPAL_ME_URL", "").strip()
        or (_get_plan_settings().get("paypal_me_url") or "").strip()
    )
    if not paypal_me:
        raise HTTPException(status_code=503, detail="PayPal not configured")

    if "@" in paypal_me and not paypal_me.startswith("http"):
        qs = urlencode({
            "cmd": "_xclick",
            "business": paypal_me,
            "item_name": f"VidGrab {plan['name_en']} - {order['code']}",
            "item_number": order["code"],
            "invoice": order["code"],
            "custom": custom_val,
            "amount": usd_str,
            "currency_code": "USD",
            "no_shipping": "1",
            "notify_url": f"{api_base}/api/v1/payments/paypal/ipn",
            "return": f"{frontend}/checkout?order_id={order['id']}&order={order['id']}",
            "cancel_return": cancel_url,
        })
        return f"https://www.paypal.com/cgi-bin/webscr?{qs}", f"ppemail_{order['code']}"

    if not paypal_me.startswith("http"):
        paypal_me = f"https://paypal.me/{paypal_me.lstrip('/')}"
    paypal_me = paypal_me.rstrip("/")
    return f"{paypal_me}/{usd_str}USD", f"ppme_{order['code']}"


# ── POST /orders/{id}/capture — PayPal capture khi user quay về ─────

@router.post("/orders/{order_id}/capture")
async def capture_paypal_order(
    order_id: str,
    request: Request,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """Capture PayPal payment on return and activate the order."""
    from app.core import payment_orders as po  # noqa: PLC0415

    order = po.get_order(order_id, user["id"])
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["rail"] != "paypal":
        raise HTTPException(status_code=400, detail="Not a PayPal order")
    if order["status"] != "pending":
        is_review = order["status"] in ("needs_manual_review", "underpaid")
        return {
            "success": order["status"] == "paid",
            "status": order["status"],
            "manual_review": is_review,
            "user_message": po.MANUAL_REVIEW_USER_MESSAGE if is_review else None,
            "order": order,
        }

    paypal_order_id = order.get("provider_ref", "").strip()
    if not paypal_order_id:
        raise HTTPException(status_code=400, detail="No PayPal reference")

    import httpx
    from datetime import datetime, timezone
    from app.api.payments import (  # noqa: PLC0415
        _expected_paypal_receiver_email,
        _is_unique_violation,
        _paypal_access_token,
        _paypal_base_url,
        _supabase,
        parse_usd_cents_decimal,
    )

    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not (pp_client_id and pp_secret):
        raise HTTPException(status_code=503, detail="PayPal not configured")

    expected_email = _expected_paypal_receiver_email()
    expected_merchant_id = os.getenv("PAYPAL_MERCHANT_ID", "").strip()
    if not (expected_email or expected_merchant_id):
        raise HTTPException(status_code=503, detail="PayPal merchant identity not configured")

    access_token = await _paypal_access_token(pp_client_id, pp_secret)

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{_paypal_base_url()}/v2/checkout/orders/{paypal_order_id}/capture",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code not in (200, 201):
            g = await client.get(
                f"{_paypal_base_url()}/v2/checkout/orders/{paypal_order_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            odata = g.json() if g.status_code == 200 else {}
        else:
            odata = r.json()

    if odata.get("status") != "COMPLETED":
        raise HTTPException(status_code=400, detail=f"PayPal not completed: {odata.get('status')}")

    db = _supabase()
    purchase_units = odata.get("purchase_units") or []
    captures = [
        cap
        for pu in purchase_units
        for cap in ((pu.get("payments") or {}).get("captures") or [])
    ]
    if not purchase_units or not captures:
        po.record_webhook_manual_review(
            db,
            provider="paypal_capture",
            provider_event_id=paypal_order_id,
            event_type="paypal.capture.missing_captures",
            user_id=str(user["id"]),
            order_id=str(order_id),
            order_code=str(order.get("code") or "") or None,
            raw_payload=odata if isinstance(odata, dict) else {},
            reason="missing_paypal_captures",
            status="needs_manual_review",
        )
        raise HTTPException(status_code=400, detail="Missing completed PayPal capture")

    pu0 = purchase_units[0]
    cap = captures[0]
    cap_status = str(cap.get("status") or "COMPLETED").strip().upper()
    capture_id = str(cap.get("id") or "").strip()
    if cap_status != "COMPLETED" or not capture_id:
        po.record_webhook_manual_review(
            db,
            provider="paypal_capture",
            provider_event_id=capture_id or paypal_order_id,
            event_type="paypal.capture.not_completed",
            user_id=str(user["id"]),
            order_id=str(order_id),
            order_code=str(order.get("code") or "") or None,
            raw_payload=odata if isinstance(odata, dict) else {},
            reason="capture_incomplete_or_missing_id",
            status="needs_manual_review",
        )
        raise HTTPException(status_code=400, detail="PayPal capture incomplete or missing capture id")

    # 1. Verify custom_id matches internal order_id (and user_id when colon-delimited)
    custom_id = str(pu0.get("custom_id") or cap.get("custom_id") or "").strip()
    expected_oid = str(order["id"])
    expected_uid = str(order["user_id"])
    if custom_id == expected_oid:
        custom_ok = True
    elif ":" in custom_id:
        c_parts = custom_id.split(":")
        custom_ok = len(c_parts) >= 2 and c_parts[0].strip() == expected_uid and c_parts[1].strip() == expected_oid
    else:
        custom_ok = False

    if not custom_ok:
        po.record_webhook_manual_review(
            db,
            provider="paypal_capture",
            provider_event_id=capture_id,
            event_type="paypal.capture.custom_id_mismatch",
            user_id=str(user["id"]),
            order_id=str(order_id),
            order_code=str(order.get("code") or "") or None,
            raw_payload=odata if isinstance(odata, dict) else {},
            reason="custom_id_mismatch",
            status="needs_manual_review",
        )
        raise HTTPException(status_code=400, detail="PayPal custom_id does not match internal order")

    # 2. Verify payee merchant matches configured merchant email / merchant_id
    payee = pu0.get("payee") or cap.get("payee") or {}
    payee_email = str(payee.get("email_address") or "").strip().lower()
    payee_merchant_id = str(payee.get("merchant_id") or "").strip()

    email_ok = bool(expected_email and payee_email and payee_email == expected_email)
    mid_ok = bool(expected_merchant_id and payee_merchant_id and payee_merchant_id == expected_merchant_id)
    email_conflict = bool(expected_email and payee_email and payee_email != expected_email)
    mid_conflict = bool(expected_merchant_id and payee_merchant_id and payee_merchant_id != expected_merchant_id)

    if (not (email_ok or mid_ok)) or email_conflict or mid_conflict:
        po.record_webhook_manual_review(
            db,
            provider="paypal_capture",
            provider_event_id=capture_id,
            event_type="paypal.capture.payee_mismatch",
            user_id=str(user["id"]),
            order_id=str(order_id),
            order_code=str(order.get("code") or "") or None,
            raw_payload=odata if isinstance(odata, dict) else {},
            reason="payee_merchant_mismatch",
            status="needs_manual_review",
        )
        raise HTTPException(status_code=400, detail="PayPal payee merchant mismatch")

    # 3. Verify currency_code matches order currency
    amt_obj = cap.get("amount") or {}
    currency_code = str(amt_obj.get("currency_code") or "").strip().upper()
    expected_currency = str(order.get("currency") or "USD").strip().upper()
    if currency_code != expected_currency:
        po.record_webhook_manual_review(
            db,
            provider="paypal_capture",
            provider_event_id=capture_id,
            event_type="paypal.capture.currency_mismatch",
            user_id=str(user["id"]),
            order_id=str(order_id),
            order_code=str(order.get("code") or "") or None,
            raw_payload=odata if isinstance(odata, dict) else {},
            reason=f"currency_mismatch:{currency_code or 'missing'}!={expected_currency}",
            status="needs_manual_review",
        )
        raise HTTPException(status_code=400, detail="PayPal currency mismatch")

    # 4. Parse amount via Decimal -> exact integer cents (never float, never default to order["amount"])
    paid_cents = parse_usd_cents_decimal(amt_obj.get("value"))
    if paid_cents is None:
        po.record_webhook_manual_review(
            db,
            provider="paypal_capture",
            provider_event_id=capture_id,
            event_type="paypal.capture.invalid_amount",
            user_id=str(user["id"]),
            order_id=str(order_id),
            order_code=str(order.get("code") or "") or None,
            raw_payload=odata if isinstance(odata, dict) else {},
            reason="invalid_or_zero_capture_amount",
            status="needs_manual_review",
        )
        raise HTTPException(status_code=400, detail="Invalid PayPal capture amount")

    # 5. Idempotency by capture_id via payment_events UNIQUE (provider='paypal_capture', provider_event_id=capture_id)
    try:
        db.table("payment_events").insert({
            "provider": "paypal_capture",
            "provider_event_id": capture_id,
            "event_type": "paypal.order.captured",
            "order_id": str(order_id),
            "amount": paid_cents,
            "match_type": "claiming",
            "processed": False,
            "raw_payload": {"paypal_order_id": paypal_order_id, "capture_id": capture_id},
        }).execute()
    except Exception as exc:
        if _is_unique_violation(exc):
            return {
                "success": False,
                "duplicate": True,
                "status": order["status"],
                "action": "duplicate_capture",
                "order": order,
            }
        raise HTTPException(status_code=503, detail="Payment event store unavailable") from exc

    evidence = {
        "provider": "paypal",
        "paypal_order_id": paypal_order_id,
        "capture_id": capture_id,
        "currency_code": currency_code,
        "status": odata.get("status"),
    }

    ok, msg = po.activate_order(
        order_id=order_id,
        paid_amount=paid_cents,
        provider_ref=capture_id,
        evidence=evidence,
    )
    try:
        db.table("payment_events").update({
            "match_type": "matched" if ok else msg,
            "processed": True,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "error": None if ok else msg,
        }).eq("provider", "paypal_capture").eq("provider_event_id", capture_id).execute()
    except Exception:
        pass
    updated_order = po.get_order(order_id, user["id"])
    actual_status = (updated_order or {}).get("status") or ("paid" if ok else msg)
    is_review = actual_status in ("needs_manual_review", "underpaid") or msg == "needs_manual_review"
    plan_info = po.plan_by_id(order.get("plan", "pro_month"))
    resolved_tier = (plan_info or {}).get("tier", "pro") if ok else None
    return {
        "success": ok,
        "status": "paid" if ok else actual_status,
        "action": msg,
        "tier": resolved_tier,
        "manual_review": is_review,
        "user_message": po.MANUAL_REVIEW_USER_MESSAGE if is_review else None,
        "order": updated_order,
    }


# ── GET /orders — danh sách đơn ──────────────────────────────────────

@router.get("/orders")
async def list_orders(
    user: Dict[str, Any] = Depends(get_required_user),
):
    from app.core import payment_orders as po  # noqa: PLC0415
    pending_review = po.get_user_pending_manual_review(user["id"])
    return {
        "orders": po.list_orders(user["id"]),
        "manual_review": pending_review,
        "user_message": po.MANUAL_REVIEW_USER_MESSAGE if pending_review else None,
    }


# ── GET /orders/{id} — chi tiết đơn (poll) ──────────────────────────

@router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    user: Dict[str, Any] = Depends(get_required_user),
):
    from app.core import payment_orders as po  # noqa: PLC0415

    order = po.get_order(order_id, user["id"])
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    st = order.get("status")
    is_review = st in ("needs_manual_review", "underpaid")
    return {
        "order": order,
        "status": st,
        "manual_review": is_review,
        "user_message": po.MANUAL_REVIEW_USER_MESSAGE if is_review else None,
    }


# ── POST /orders/{id}/cancel — huỷ đơn ──────────────────────────────

@router.post("/orders/{order_id}/cancel")
async def cancel_order_endpoint(
    order_id: str,
    user: Dict[str, Any] = Depends(get_required_user),
):
    from app.core import payment_orders as po  # noqa: PLC0415

    ok, msg = po.cancel_order(order_id, user["id"])
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"success": True, "action": msg}


# ── GET /plans — catalog gói bán ─────────────────────────────────────

@router.get("/plans")
async def get_checkout_plans(request: Request):
    from app.core import payments_auto  # noqa: PLC0415
    from app.core.auth_middleware import get_optional_user  # noqa: PLC0415

    if not payments_auto.visible(request):
        raise HTTPException(status_code=404, detail="Not Found")

    user = await get_optional_user(request)
    uid = str(user["id"]) if user and user.get("id") else None

    from app.core import payment_orders as po  # noqa: PLC0415
    return {
        "plans": po.catalog(user_id=uid),
        "is_first_time_buyer": po.is_first_time_buyer(uid),
    }


# ── Admin endpoints ──────────────────────────────────────────────────

@router.post("/admin/ensure-schema")
@admin_router.post("/ensure-schema")
async def admin_ensure_schema(_=Depends(verify_admin)):
    """Run Migration 042 idempotently on Postgres Vibe Host."""
    from app.core import payment_orders as po  # noqa: PLC0415
    ok = po.ensure_schema(force=True)
    return {"success": ok, "migration": "042_payment_orders"}


@router.get("/admin/orders/stats")
@admin_router.get("/orders/stats")
async def admin_orders_stats(_=Depends(verify_admin)):
    from app.core import payment_orders as po  # noqa: PLC0415
    return po.admin_order_stats()


@router.get("/admin/orders")
@admin_router.get("/orders")
async def admin_list_orders(
    status: Optional[str] = None,
    rail: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    _=Depends(verify_admin),
):
    from app.core import payment_orders as po  # noqa: PLC0415
    actual_limit = limit if limit is not None else max(1, min(per_page, 100))
    actual_offset = offset if offset is not None else max(0, (page - 1) * actual_limit)
    orders, total = po.admin_list_orders(status, rail, actual_limit, actual_offset, q)
    total_pages = max(1, (total + actual_limit - 1) // actual_limit)
    return {
        "orders": orders,
        "total": total,
        "page": page,
        "per_page": actual_limit,
        "total_pages": total_pages,
    }


class AdminActivateRequest(BaseModel):
    reason: str = ""


@router.post("/admin/orders/{order_id}/activate")
@admin_router.post("/orders/{order_id}/activate")
async def admin_activate_order(
    order_id: str,
    payload: AdminActivateRequest,
    request: Request,
    _=Depends(verify_admin),
):
    from app.core import payment_orders as po  # noqa: PLC0415
    from app.core.audit import log_admin_action  # noqa: PLC0415

    ok, msg = po.admin_activate_order(order_id, payload.reason)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    log_admin_action(
        request, "admin.order.activated",
        resource_type="payment_order", resource_id=order_id,
        metadata={"reason": payload.reason},
    )
    return {"success": True, "action": msg}


class AdminResolveReviewRequest(BaseModel):
    note: str = ""


@router.get("/admin/manual-reviews")
@admin_router.get("/manual-reviews")
async def admin_list_manual_reviews(
    status: Optional[str] = "needs_manual_review",
    page: int = 1,
    per_page: int = 20,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    _=Depends(verify_admin),
):
    from app.core import payment_orders as po  # noqa: PLC0415

    actual_limit = limit if limit is not None else max(1, min(per_page, 100))
    actual_offset = offset if offset is not None else max(0, (page - 1) * actual_limit)
    events, total = po.admin_list_manual_reviews(status=status, limit=actual_limit, offset=actual_offset)
    total_pages = max(1, (total + actual_limit - 1) // actual_limit)
    return {
        "events": events,
        "total": total,
        "page": page,
        "per_page": actual_limit,
        "total_pages": total_pages,
    }


@router.post("/admin/manual-reviews/{event_id}/resolve")
@admin_router.post("/manual-reviews/{event_id}/resolve")
async def admin_resolve_manual_review(
    event_id: str,
    payload: AdminResolveReviewRequest,
    request: Request,
    _=Depends(verify_admin),
):
    from app.core import payment_orders as po  # noqa: PLC0415
    from app.core.audit import log_admin_action  # noqa: PLC0415

    ok, msg, ev = po.admin_resolve_manual_review(event_id, payload.note)
    if not ok:
        if msg == "not_found":
            raise HTTPException(status_code=404, detail="Manual review event not found")
        raise HTTPException(status_code=400, detail=msg)

    log_admin_action(
        request,
        "admin.payment_review.resolved",
        resource_type="payment_webhook_event",
        resource_id=event_id,
        metadata={"note": payload.note},
    )
    return {"success": True, "action": msg, "event": ev}


@router.post("/admin/orders/{order_id}/simulate-sepay")
@admin_router.post("/orders/{order_id}/simulate-sepay")
async def admin_simulate_sepay(
    order_id: str,
    request: Request,
    _=Depends(verify_admin),
):
    """Simulate a real HMAC-SHA256 signed SePay webhook for a pending order."""
    import hashlib
    import hmac
    import json
    import time
    import uuid
    from app.core import payment_orders as po  # noqa: PLC0415
    from app.core import sepay_webhook as sw  # noqa: PLC0415
    from app.core.audit import log_admin_action  # noqa: PLC0415

    order = po.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    secret = sw._secret()
    if not secret:
        raise HTTPException(status_code=400, detail="Chưa cấu hình SePay Webhook Secret")

    sim_tx_id = f"sim-{uuid.uuid4().hex[:8]}"
    raw_body = json.dumps({
        "id": sim_tx_id,
        "gateway": "ACB",
        "transactionDate": "2026-11-03 22:30:00",
        "accountNumber": "12345678",
        "transferType": "in",
        "transferAmount": int(order["amount"]),
        "code": order["code"],
        "content": f"{order['code']} thanh toan VidGrab Pro",
        "referenceCode": f"SIM_{sim_tx_id.upper()}",
    }).encode("utf-8")

    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw_body, hashlib.sha256).hexdigest()
    headers = {
        "x-sepay-timestamp": ts,
        "x-sepay-signature": f"sha256={sig}",
    }

    result = sw.handle(raw_body, headers)
    log_admin_action(
        request, "admin.order.simulate_sepay",
        resource_type="payment_order", resource_id=order_id,
        metadata={"code": order["code"], "result": result},
    )
    return result
