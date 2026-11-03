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

from app.core.auth_middleware import get_required_user

router = APIRouter()


class CreateOrderRequest(BaseModel):
    plan: str = "pro_month"        # 'pro_month' | 'pro_year'
    rail: str = "vietqr"           # 'vietqr' | 'paypal'


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

    plan = po.plan_by_id(payload.plan)
    if not plan:
        raise HTTPException(status_code=400, detail="plan_not_found")

    rail = payload.rail.lower()
    if rail not in ("vietqr", "paypal"):
        raise HTTPException(status_code=400, detail="rail must be 'vietqr' or 'paypal'")

    # Create order
    order, err = po.create_order(user["id"], payload.plan, rail)
    if err:
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
            pp_url, pp_session = await _create_paypal_order(user, plan, order)
            result["paypal"] = {
                "checkout_url": pp_url,
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


async def _create_paypal_order(user: Dict, plan: Dict, order: Dict):
    """Create PayPal Order for the given plan, linking to our payment_order."""
    import httpx

    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not (pp_client_id and pp_secret):
        raise HTTPException(status_code=503, detail="PayPal API not configured")

    from app.api.payments import _paypal_base_url, _paypal_access_token  # noqa: PLC0415

    access_token = await _paypal_access_token(pp_client_id, pp_secret)
    usd_str = f"{plan['usd_cents'] / 100:.2f}"

    frontend = os.getenv("FRONTEND_URL", "").split(",")[0].strip().rstrip("/") or "https://dowloadvideo.io.vn"
    return_url = f"{frontend}/checkout?order={order['id']}&paypal_capture=1"
    cancel_url = f"{frontend}/checkout?order={order['id']}&canceled=1"

    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "custom_id": f"{user['id']}:{order['id']}:{plan['id']}",
            "description": f"VidGrab {plan['name_en']}",
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
        if r.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"PayPal: {r.text[:120]}")
        odata = r.json()

    approve_url = next(
        (lnk["href"] for lnk in odata.get("links", []) if lnk.get("rel") == "approve"),
        None,
    )
    if not approve_url:
        raise HTTPException(status_code=502, detail="PayPal no approve URL")

    return approve_url, odata.get("id", "")


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
        return {"success": order["status"] == "paid", "status": order["status"]}

    paypal_order_id = order.get("provider_ref", "").strip()
    if not paypal_order_id:
        raise HTTPException(status_code=400, detail="No PayPal reference")

    import httpx
    from app.api.payments import _paypal_base_url, _paypal_access_token  # noqa: PLC0415

    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not (pp_client_id and pp_secret):
        raise HTTPException(status_code=503, detail="PayPal not configured")

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

    # Get actual paid amount in cents
    captures = []
    for pu in odata.get("purchase_units", []):
        for cap in (pu.get("payments") or {}).get("captures", []):
            captures.append(cap)

    paid_cents = order["amount"]  # default to expected
    if captures:
        try:
            paid_cents = int(float(captures[0].get("amount", {}).get("value", "0")) * 100)
        except Exception:
            pass

    evidence = {
        "provider": "paypal",
        "paypal_order_id": paypal_order_id,
        "status": odata.get("status"),
    }

    ok, msg = po.activate_order(
        order_id=order_id,
        paid_amount=paid_cents,
        provider_ref=paypal_order_id,
        evidence=evidence,
    )

    return {"success": ok, "action": msg, "tier": "pro" if ok else None}


# ── GET /orders — danh sách đơn ──────────────────────────────────────

@router.get("/orders")
async def list_orders(
    user: Dict[str, Any] = Depends(get_required_user),
):
    from app.core import payment_orders as po  # noqa: PLC0415
    return {"orders": po.list_orders(user["id"])}


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
    return {"order": order}


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

    if not payments_auto.visible(request):
        raise HTTPException(status_code=404, detail="Not Found")

    from app.core import payment_orders as po  # noqa: PLC0415
    return {"plans": po.catalog()}


# ── Admin endpoints ──────────────────────────────────────────────────

@router.get("/admin/orders")
async def admin_list_orders(
    status: Optional[str] = None,
    rail: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    request: Request = None,
    _=Depends(get_required_user),
):
    from app.core.quotas import is_admin_request  # noqa: PLC0415
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin only")

    from app.core import payment_orders as po  # noqa: PLC0415
    return {"orders": po.admin_list_orders(status, rail, limit, offset)}


class AdminActivateRequest(BaseModel):
    reason: str = ""


@router.post("/admin/orders/{order_id}/activate")
async def admin_activate_order(
    order_id: str,
    payload: AdminActivateRequest,
    request: Request,
    user: Dict[str, Any] = Depends(get_required_user),
):
    from app.core.quotas import is_admin_request  # noqa: PLC0415
    if not is_admin_request(request):
        raise HTTPException(status_code=403, detail="Admin only")

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
