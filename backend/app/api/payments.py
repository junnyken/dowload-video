"""
Payments — Stripe Integration
==============================
POST /api/v1/payments/create-checkout-session  — create Stripe checkout, return URL
POST /api/v1/payments/webhook                  — Stripe webhook receiver
GET  /api/v1/payments/billing-status           — user billing + subscription info

Required env vars:
  STRIPE_SECRET_KEY       — sk_live_... or sk_test_...
  STRIPE_WEBHOOK_SECRET   — whsec_... (from Stripe dashboard)
  STRIPE_PRO_PRICE_ID     — price_... (monthly Pro plan price)
  FRONTEND_URL            — https://your-frontend.com (for redirect URLs; if
                            comma-separated, the first entry is used)

Webhook events handled:
  checkout.session.completed        → upgrade user to Pro
  customer.subscription.deleted     → downgrade at period end
  invoice.payment_failed            → set billing_status=past_due (3-day grace)
  customer.subscription.updated     → sync subscription state
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client as get_supabase_client

router = APIRouter()

_STRIPE_SECRET_KEY        = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_WEBHOOK_SECRET    = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_STRIPE_PRO_PRICE_ID      = os.getenv("STRIPE_PRO_PRICE_ID", "")
_STRIPE_PRO_YEAR_PRICE_ID = os.getenv("STRIPE_PRO_YEAR_PRICE_ID", "")
_STRIPE_PRO_MONTH_CENTS   = int(os.getenv("STRIPE_PRO_MONTH_USD_CENTS", "499") or 499)
_STRIPE_PRO_YEAR_CENTS    = int(os.getenv("STRIPE_PRO_YEAR_USD_CENTS", "4900") or 4900)
# FRONTEND_URL may list several comma-separated hosts (main.py allows all of
# them for CORS); redirects go to the first one, the primary domain.
_FRONTEND_URL          = (
    os.getenv("FRONTEND_URL", "").split(",")[0].strip().rstrip("/")
    or "https://dowloadvideo.io.vn"
)

GRACE_PERIOD_DAYS = 3   # keep Pro on payment failure for this many days


def _stripe():
    """Lazy import stripe so missing package doesn't break server boot."""
    try:
        import stripe as _s
        _s.api_key = _STRIPE_SECRET_KEY
        return _s
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Stripe SDK not installed. Run: pip install stripe",
        )


def _supabase():
    return get_supabase_client()


# ── Helpers ──────────────────────────────────────────────────────────

def _get_or_create_stripe_customer(user_id: str, email: str) -> str:
    """Return existing Stripe customer_id or create a new one."""
    stripe = _stripe()
    supabase = _supabase()

    try:
        res = supabase.table("profiles").select("stripe_customer_id").eq("id", user_id).single().execute()
        cid = (res.data or {}).get("stripe_customer_id")
        if cid:
            return cid
    except Exception:
        pass

    customer = stripe.Customer.create(email=email, metadata={"user_id": user_id})
    cid = customer["id"]

    try:
        supabase.table("profiles").update({"stripe_customer_id": cid}).eq("id", user_id).execute()
    except Exception as e:
        print(f"[Payments] Failed to store stripe_customer_id for {user_id}: {e}")

    return cid


def _upgrade_user(user_id: str, subscription_id: str, current_period_end: Optional[int] = None) -> None:
    supabase = _supabase()
    update: Dict[str, Any] = {
        "tier":                    "pro",
        "billing_status":          "active",
        "stripe_subscription_id":  subscription_id,
    }
    if current_period_end:
        update["subscription_expiry"] = datetime.fromtimestamp(
            current_period_end, tz=timezone.utc
        ).isoformat()
    try:
        supabase.table("profiles").update(update).eq("id", user_id).execute()
    except Exception as e:
        print(f"[Payments] upgrade_user failed for {user_id}: {e}")


def _downgrade_user(user_id: str, at_period_end: bool = True, period_end_ts: Optional[int] = None) -> None:
    """Schedule or immediately apply downgrade."""
    supabase = _supabase()
    if at_period_end and period_end_ts:
        # Keep Pro until billing period ends — set a future expiry, nightly job cleans up
        update = {
            "billing_status":         "canceling",
            "subscription_expiry":    datetime.fromtimestamp(period_end_ts, tz=timezone.utc).isoformat(),
        }
    else:
        update = {
            "tier":           "free",
            "billing_status": "canceled",
            "subscription_expiry": None,
            "stripe_subscription_id": None,
        }
    try:
        supabase.table("profiles").update(update).eq("id", user_id).execute()
    except Exception as e:
        print(f"[Payments] downgrade_user failed for {user_id}: {e}")


def _past_due_user(user_id: str) -> None:
    """Mark billing as past_due — keep Pro for grace period."""
    supabase = _supabase()
    grace_until = (datetime.now(timezone.utc) + timedelta(days=GRACE_PERIOD_DAYS)).isoformat()
    try:
        supabase.table("profiles").update({
            "billing_status":      "past_due",
            "subscription_expiry": grace_until,
        }).eq("id", user_id).execute()
    except Exception as e:
        print(f"[Payments] past_due_user failed for {user_id}: {e}")


def _user_id_from_customer(customer_id: str) -> Optional[str]:
    """Look up our user_id by Stripe customer_id."""
    try:
        res = _supabase().table("profiles").select("id").eq("stripe_customer_id", customer_id).single().execute()
        return (res.data or {}).get("id")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
# POST /create-checkout-session
# ══════════════════════════════════════════════════════════════════════

class CheckoutRequest(BaseModel):
    period:      Optional[str] = "month"   # "month" | "year"
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None


class PayPalCaptureRequest(BaseModel):
    order_id: str


def _paypal_base_url() -> str:
    mode = os.getenv("PAYPAL_MODE", "live").strip().lower()
    return "https://api-m.sandbox.paypal.com" if mode == "sandbox" else "https://api-m.paypal.com"


async def _paypal_access_token(client_id: str, client_secret: str) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.post(
            f"{_paypal_base_url()}/v1/oauth2/token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        if res.status_code != 200:
            raise HTTPException(status_code=502, detail=f"PayPal OAuth failed: {res.text[:160]}")
        return res.json()["access_token"]


@router.post("/create-checkout-session")
async def create_checkout_session(
    payload: CheckoutRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Create an international Checkout Session for the Monthly or Yearly Pro plan.
    Supports:
      1. PayPal REST API Orders v2 (PAYPAL_CLIENT_ID + PAYPAL_CLIENT_SECRET) — automatic Pro activation on return.
      2. PayPal.me quick link (PAYPAL_ME_URL, e.g. https://paypal.me/yourname).
      3. Direct Checkout URLs (Lemon Squeezy / Polar.sh) via INTERNATIONAL_CHECKOUT_URL_MONTH / _YEAR.
      4. Stripe Checkout Session via STRIPE_SECRET_KEY.
    Returns {checkout_url} — redirect user there.
    """
    is_year = (payload.period or "month").lower() == "year"
    usd_str = f"{(_STRIPE_PRO_YEAR_CENTS if is_year else _STRIPE_PRO_MONTH_CENTS) / 100:.2f}"

    # 1. PayPal Orders v2 REST API (100% automatic verification & upgrade)
    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if pp_client_id and pp_secret:
        import httpx
        access_token = await _paypal_access_token(pp_client_id, pp_secret)
        ret_base = (payload.cancel_url or f"{_FRONTEND_URL}/pricing").split("?")[0]
        return_url = f"{ret_base}?paypal_capture=1&period={'year' if is_year else 'month'}"
        cancel_url = f"{ret_base}?canceled=1"
        order_body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "custom_id": f"{user['id']}:{'year' if is_year else 'month'}",
                    "description": f"VidGrab Pro ({'Yearly - 365 days' if is_year else 'Monthly - 30 days'})",
                    "amount": {
                        "currency_code": "USD",
                        "value": usd_str,
                    },
                }
            ],
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
                json=order_body,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code not in (200, 201):
                raise HTTPException(status_code=502, detail=f"Không tạo được đơn PayPal: {r.text[:160]}")
            odata = r.json()
            approve_url = next((lnk["href"] for lnk in odata.get("links", []) if lnk.get("rel") == "approve"), None)
            if not approve_url:
                raise HTTPException(status_code=502, detail="PayPal không trả về đường dẫn thanh toán.")
            return {"checkout_url": approve_url, "session_id": odata.get("id", "")}

    # 2. Simple PayPal.me link (zero API keys needed)
    paypal_me = os.getenv("PAYPAL_ME_URL", "").strip()
    if not paypal_me:
        try:
            from app.core.plans import get_settings as _get_plan_settings
            paypal_me = (_get_plan_settings().get("paypal_me_url") or "").strip()
        except Exception:
            paypal_me = ""
    if paypal_me:
        if "@" in paypal_me and not paypal_me.startswith("http"):
            # PayPal email entered: use standard PayPal webscr checkout link
            from urllib.parse import urlencode
            qs = urlencode({
                "cmd": "_xclick",
                "business": paypal_me,
                "item_name": f"VidGrab Pro ({'Yearly' if is_year else 'Monthly'}) - {user['id'][:8]}",
                "amount": usd_str,
                "currency_code": "USD",
            })
            return {"checkout_url": f"https://www.paypal.com/cgi-bin/webscr?{qs}", "session_id": f"ppemail_{user['id'][:8]}"}
        if not paypal_me.startswith("http"):
            paypal_me = f"https://paypal.me/{paypal_me.lstrip('/')}"
        paypal_me = paypal_me.rstrip("/")
        amt = "49USD" if is_year else "4.99USD"
        return {"checkout_url": f"{paypal_me}/{amt}", "session_id": f"ppme_{user['id'][:8]}"}

    # 3. Direct Checkout URL (Lemon Squeezy / Polar.sh)
    direct_url = (
        os.getenv("INTERNATIONAL_CHECKOUT_URL_YEAR", "").strip()
        if is_year
        else os.getenv("INTERNATIONAL_CHECKOUT_URL_MONTH", "").strip()
    ) or os.getenv("INTERNATIONAL_CHECKOUT_URL", "").strip()

    if direct_url:
        from urllib.parse import urlencode
        sep = "&" if "?" in direct_url else "?"
        params = urlencode({
            "checkout[email]": user.get("email") or "",
            "checkout[custom][user_id]": user["id"],
            "checkout[custom][period]": "year" if is_year else "month",
        })
        return {"checkout_url": f"{direct_url}{sep}{params}", "session_id": f"ext_{user['id'][:8]}"}

    stripe_key = os.getenv("STRIPE_SECRET_KEY", "") or _STRIPE_SECRET_KEY
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Thanh toán quốc tế chưa được cấu hình. Vui lòng dùng chuyển khoản hoặc liên hệ admin.")

    stripe = _stripe()
    stripe.api_key = stripe_key
    customer_id = _get_or_create_stripe_customer(user["id"], user["email"])
    price_id = (
        (os.getenv("STRIPE_PRO_YEAR_PRICE_ID", "") or _STRIPE_PRO_YEAR_PRICE_ID)
        if is_year
        else (os.getenv("STRIPE_PRO_PRICE_ID", "") or _STRIPE_PRO_PRICE_ID)
    )

    if price_id:
        line_item: Dict[str, Any] = {"price": price_id, "quantity": 1}
    else:
        unit_amount = _STRIPE_PRO_YEAR_CENTS if is_year else _STRIPE_PRO_MONTH_CENTS
        interval = "year" if is_year else "month"
        plan_name = "VidGrab Pro (Yearly)" if is_year else "VidGrab Pro (Monthly)"
        line_item = {
            "price_data": {
                "currency": "usd",
                "unit_amount": unit_amount,
                "recurring": {"interval": interval},
                "product_data": {
                    "name": plan_name,
                    "description": "High-speed video & audio downloads, 4K YouTube, bulk channels & API access",
                },
            },
            "quantity": 1,
        }

    success_url = payload.success_url or f"{_FRONTEND_URL}/billing?success=1"
    cancel_url  = payload.cancel_url  or f"{_FRONTEND_URL}/pricing?canceled=1"

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[line_item],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": user["id"], "period": "year" if is_year else "month"},
            allow_promotion_codes=True,
            billing_address_collection="auto",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không tạo được phiên thanh toán: {str(e)[:200]}")

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/paypal-capture")
async def paypal_capture(
    payload: PayPalCaptureRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Capture an approved PayPal Order on return from PayPal Checkout,
    verify COMPLETED status, and automatically upgrade the user to Pro.
    """
    import httpx
    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    if not (pp_client_id and pp_secret):
        raise HTTPException(status_code=503, detail="PayPal API not configured")

    order_id = (payload.order_id or "").strip()
    if not order_id:
        raise HTTPException(status_code=400, detail="Missing order_id")

    access_token = await _paypal_access_token(pp_client_id, pp_secret)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{_paypal_base_url()}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if r.status_code not in (200, 201):
            # Also check if already captured
            g = await client.get(
                f"{_paypal_base_url()}/v2/checkout/orders/{order_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            odata = g.json() if g.status_code == 200 else {}
        else:
            odata = r.json()

    if odata.get("status") != "COMPLETED":
        raise HTTPException(status_code=400, detail=f"Thanh toán PayPal chưa hoàn tất (status={odata.get('status')}).")

    units = odata.get("purchase_units") or []
    custom_id = ""
    if units:
        custom_id = units[0].get("custom_id") or ""
        if not custom_id:
            captures = ((units[0].get("payments") or {}).get("captures") or [])
            if captures:
                custom_id = captures[0].get("custom_id") or ""

    period = "year" if custom_id.endswith(":year") else "month"
    days = 365 if period == "year" else 30
    exp = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
    _upgrade_user(user["id"], f"paypal_{order_id}", exp)
    try:
        from app.api.referrals import reward_referrer_on_pro_upgrade
        reward_referrer_on_pro_upgrade(user["id"])
    except Exception:
        pass

    return {"success": True, "tier": "pro", "period": period, "order_id": order_id}


@router.post("/lemonsqueezy-webhook")
async def lemonsqueezy_webhook(request: Request):
    """
    Receive and verify Lemon Squeezy webhook events (order_created / subscription_created).
    Verifies HMAC-SHA256 X-Signature against LEMONSQUEEZY_WEBHOOK_SECRET.
    Automatically activates Pro for 30 or 365 days and rewards referrers.
    """
    import hashlib
    import hmac
    secret = os.getenv("LEMONSQUEEZY_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise HTTPException(status_code=404, detail="Not Found")
    body = await request.body()
    sig = (request.headers.get("X-Signature") or "").strip()
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not sig or not hmac.compare_digest(sig, digest):
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body.decode("utf-8"))
    meta = payload.get("meta") or {}
    event_name = meta.get("event_name") or ""
    custom = meta.get("custom_data") or {}
    user_id = (custom.get("user_id") or "").strip()
    period = (custom.get("period") or "month").strip().lower()

    if event_name in ("order_created", "subscription_created", "subscription_payment_success") and user_id:
        days = 365 if period == "year" else 30
        exp = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
        order_id = str((payload.get("data") or {}).get("id") or "ls_order")
        _upgrade_user(user_id, f"ls_{order_id}", exp)
        try:
            from app.api.referrals import reward_referrer_on_pro_upgrade
            reward_referrer_on_pro_upgrade(user_id)
        except Exception:
            pass
    return {"received": True}


# ══════════════════════════════════════════════════════════════════════
# POST /webhook  (Stripe sends events here)
# ══════════════════════════════════════════════════════════════════════

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Receive and verify Stripe webhook events.
    Must be called with raw body — do not parse JSON before this handler.
    """
    body      = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not _STRIPE_WEBHOOK_SECRET:
        # 27/10/2026: this branch accepted UNSIGNED events in production (the
        # secret was never set there): anyone could POST checkout.session.completed
        # with a user id and get Pro, or subscription.deleted and downgrade someone.
        # No secret = Stripe is not set up = no webhook.
        raise HTTPException(status_code=404, detail="Not Found")
    else:
        stripe = _stripe()
        try:
            event = stripe.Webhook.construct_event(body, sig_header, _STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook signature invalid: {e}")

    event_type = event.get("type", "")
    event_id   = event.get("id", "")
    data_obj   = event.get("data", {}).get("object", {})

    # Idempotency: skip if already processed
    is_new = _record_payment_event(event_id, event_type, event.get("data", {}))
    if not is_new:
        return {"received": True, "type": event_type, "duplicate": True}

    # ── checkout.session.completed ───────────────────────────────────
    if event_type == "checkout.session.completed":
        user_id = (data_obj.get("metadata") or {}).get("user_id")
        sub_id  = data_obj.get("subscription")
        if user_id and sub_id:
            # Fetch subscription to get period_end
            try:
                stripe = _stripe()
                sub = stripe.Subscription.retrieve(sub_id)
                period_end = sub.get("current_period_end")
            except Exception:
                period_end = None
            _upgrade_user(user_id, sub_id, period_end)
            try:
                from app.api.referrals import reward_referrer_on_pro_upgrade
                reward_referrer_on_pro_upgrade(user_id)
            except Exception:
                pass
            print(f"[Payments] ✅ Upgraded {user_id} to Pro (sub={sub_id})")

    # ── customer.subscription.deleted ────────────────────────────────
    elif event_type == "customer.subscription.deleted":
        customer_id = data_obj.get("customer")
        period_end  = data_obj.get("current_period_end")
        user_id     = _user_id_from_customer(customer_id)
        if user_id:
            _downgrade_user(user_id, at_period_end=False)
            print(f"[Payments] ⬇ Downgraded {user_id} (subscription deleted)")

    # ── customer.subscription.updated ────────────────────────────────
    elif event_type == "customer.subscription.updated":
        customer_id = data_obj.get("customer")
        sub_id      = data_obj.get("id")
        status      = data_obj.get("status")
        period_end  = data_obj.get("current_period_end")
        user_id     = _user_id_from_customer(customer_id)
        if user_id:
            if status in ("active", "trialing"):
                _upgrade_user(user_id, sub_id, period_end)
            elif status in ("canceled", "unpaid", "paused"):
                _downgrade_user(user_id, at_period_end=bool(period_end), period_end_ts=period_end)

    # ── invoice.payment_failed ────────────────────────────────────────
    elif event_type == "invoice.payment_failed":
        customer_id = data_obj.get("customer")
        user_id     = _user_id_from_customer(customer_id)
        if user_id:
            _past_due_user(user_id)
            print(f"[Payments] ⚠ Payment failed for {user_id} — grace period applied")

    _mark_payment_event_done(event_id)
    return {"received": True, "type": event_type}


# ══════════════════════════════════════════════════════════════════════
# POST /sepay/webhook  (SePay VietQR bank transfer notifications)
# ══════════════════════════════════════════════════════════════════════

@router.post("/sepay/webhook")
async def sepay_webhook(request: Request):
    """
    Receive SePay webhook for incoming bank transfers (VietQR/ACB).
    Verifies HMAC-SHA256 signature, matches VGP code, activates order.
    Always returns 200 for verified events (SePay retries on non-200).
    """
    # Check if SePay is configured
    from app.core.sepay_webhook import _secret  # noqa: PLC0415
    if not _secret():
        raise HTTPException(status_code=404, detail="Not Found")

    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    from app.core.sepay_webhook import verify, handle  # noqa: PLC0415

    # Quick signature check — reject before parsing
    valid, err = verify(body, headers)
    if not valid:
        if err == "no_webhook_secret_configured":
            raise HTTPException(status_code=404, detail="Not Found")
        raise HTTPException(status_code=401, detail=err)

    # Full handling (parse + match + activate)
    result = handle(body, headers)
    return result


# ══════════════════════════════════════════════════════════════════════
# POST /paypal/webhook  (PayPal server-to-server webhook)
# ══════════════════════════════════════════════════════════════════════

@router.post("/paypal/webhook")
async def paypal_webhook(request: Request):
    """
    Receive PayPal webhook events (CHECKOUT.ORDER.COMPLETED,
    PAYMENT.CAPTURE.COMPLETED). Safety net for users who close browser
    before returning to the site.

    Verification: calls PayPal API to verify webhook signature.
    """
    import httpx

    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    pp_webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()

    if not (pp_client_id and pp_secret and pp_webhook_id):
        raise HTTPException(status_code=404, detail="Not Found")

    body = await request.body()

    # Verify with PayPal API
    access_token = await _paypal_access_token(pp_client_id, pp_secret)
    verify_body = {
        "auth_algo": request.headers.get("paypal-auth-algo", ""),
        "cert_url": request.headers.get("paypal-cert-url", ""),
        "transmission_id": request.headers.get("paypal-transmission-id", ""),
        "transmission_sig": request.headers.get("paypal-transmission-sig", ""),
        "transmission_time": request.headers.get("paypal-transmission-time", ""),
        "webhook_id": pp_webhook_id,
        "webhook_event": json.loads(body),
    }

    async with httpx.AsyncClient(timeout=20) as client:
        vr = await client.post(
            f"{_paypal_base_url()}/v1/notifications/verify-webhook-signature",
            json=verify_body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        )
        if vr.status_code != 200:
            raise HTTPException(status_code=401, detail="PayPal verification failed")
        vdata = vr.json()
        if vdata.get("verification_status") != "SUCCESS":
            raise HTTPException(status_code=401, detail="PayPal signature invalid")

    event = json.loads(body)
    event_type = event.get("event_type", "")
    resource = event.get("resource", {})

    # Idempotency
    event_id = event.get("id", "")
    is_new = _record_payment_event(event_id, event_type, {"source": "paypal_webhook"})
    if not is_new:
        return {"received": True, "duplicate": True}

    if event_type in ("CHECKOUT.ORDER.COMPLETED", "PAYMENT.CAPTURE.COMPLETED"):
        # Extract custom_id which has format: user_id:order_id:plan_id
        custom_id = ""
        if event_type == "CHECKOUT.ORDER.COMPLETED":
            units = resource.get("purchase_units", [])
            if units:
                custom_id = units[0].get("custom_id", "") or ""
        elif event_type == "PAYMENT.CAPTURE.COMPLETED":
            custom_id = resource.get("custom_id", "") or ""

        if custom_id and ":" in custom_id:
            parts = custom_id.split(":")
            if len(parts) >= 2:
                user_id = parts[0]
                order_id = parts[1] if len(parts) > 1 else ""

                if order_id:
                    # Activate via payment_orders
                    try:
                        from app.core.payment_orders import activate_order  # noqa: PLC0415
                        paid_amount = 0
                        try:
                            amt_obj = resource.get("amount", {})
                            paid_amount = int(float(amt_obj.get("value", "0")) * 100)
                        except Exception:
                            paid_amount = 0

                        activate_order(
                            order_id=order_id,
                            paid_amount=paid_amount or 99999,  # if can't parse, assume paid
                            provider_ref=event_id,
                            evidence={"provider": "paypal_webhook", "event_type": event_type},
                        )
                    except Exception as e:
                        print(f"[PayPal Webhook] activate error: {e}")
                else:
                    # Legacy flow: upgrade directly
                    _upgrade_user(user_id, f"paypal_wh_{event_id}", None)

    _mark_payment_event_done(event_id)
    return {"received": True, "type": event_type}


# ══════════════════════════════════════════════════════════════════════
# GET /billing-status  (authenticated)
# ══════════════════════════════════════════════════════════════════════

@router.get("/billing-status")
async def billing_status(user: Dict[str, Any] = Depends(get_required_user)):
    """Return current tier, billing status, and subscription expiry for the user."""
    supabase = _supabase()
    try:
        res = (
            supabase.table("profiles")
            .select("tier, billing_status, subscription_expiry, grace_period_ends_at, "
                    "stripe_customer_id, stripe_subscription_id")
            .eq("id", user["id"])
            .single()
            .execute()
        )
        profile = res.data or {}
    except Exception:
        profile = {}

    # The frontend badges and feature locks off this response, so it has to be
    # the tier the backend will actually honour — not the raw profiles.tier.
    # Returning the declared value is how an account could show ENTERPRISE in
    # the account menu while every require_feature gate refused it.
    from app.core.entitlements import resolve_effective_tier
    tier            = resolve_effective_tier(profile)
    billing_status_ = profile.get("billing_status", "none")
    expiry          = profile.get("subscription_expiry")

    # If subscription was "canceling" and period has passed → actually downgrade
    if billing_status_ == "canceling" and expiry:
        try:
            exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                _downgrade_user(user["id"], at_period_end=False)
                tier            = "free"
                billing_status_ = "canceled"
                expiry          = None
        except Exception:
            pass

    return {
        "tier":                 tier,
        "billing_status":       billing_status_,
        "subscription_expiry":  expiry,
        "has_payment_method":   bool(profile.get("stripe_customer_id")),
        "can_manage_billing":   bool(profile.get("stripe_subscription_id")),
    }


# ══════════════════════════════════════════════════════════════════════
# POST /create-portal-session  — Stripe Customer Portal (self-service)
# ══════════════════════════════════════════════════════════════════════

class PortalRequest(BaseModel):
    return_url: Optional[str] = None


@router.post("/create-portal-session")
async def create_portal_session(
    payload: PortalRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Create a Stripe Customer Portal session so users can manage their
    subscription, update payment method, download invoices, or cancel.
    """
    if not _STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")

    supabase = _supabase()
    try:
        res = supabase.table("profiles").select("stripe_customer_id").eq("id", user["id"]).single().execute()
        customer_id = (res.data or {}).get("stripe_customer_id")
    except Exception:
        customer_id = None

    if not customer_id:
        raise HTTPException(status_code=400, detail="Không tìm thấy thông tin thanh toán. Vui lòng đăng ký gói trước.")

    return_url = payload.return_url or f"{_FRONTEND_URL}/billing"

    try:
        stripe = _stripe()
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không mở được trang quản lý: {str(e)[:200]}")

    return {"portal_url": session.url}


# ══════════════════════════════════════════════════════════════════════
# POST /create-checkout-session  (team / yearly variants)
# ══════════════════════════════════════════════════════════════════════

class CheckoutRequestV2(BaseModel):
    plan: str = "pro"                   # 'pro', 'team', 'api'
    period: str = "monthly"             # 'monthly', 'yearly'
    seats: Optional[int] = None         # team only
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None


_STRIPE_PLAN_PRICES = {
    # (plan, period) → env var name
    ("pro",  "monthly"): "STRIPE_PRO_PRICE_ID",
    ("pro",  "yearly"):  "STRIPE_PRO_YEARLY_PRICE_ID",
    ("team", "monthly"): "STRIPE_TEAM_PRICE_ID",
    ("team", "yearly"):  "STRIPE_TEAM_YEARLY_PRICE_ID",
    ("api",  "monthly"): "STRIPE_API_PRICE_ID",
    ("api",  "yearly"):  "STRIPE_API_YEARLY_PRICE_ID",
}


@router.post("/create-checkout-session-v2")
async def create_checkout_session_v2(
    payload: CheckoutRequestV2,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Enhanced checkout supporting team plans, yearly billing, and seat selection.
    Falls back to v1 checkout-session for simple Pro monthly.
    """
    if not _STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")

    plan   = payload.plan.lower()
    period = payload.period.lower()
    seats  = max(1, payload.seats or 1)

    env_key  = _STRIPE_PLAN_PRICES.get((plan, period))
    price_id = os.getenv(env_key or "", "") if env_key else ""

    if not price_id:
        # Until 13/10 a missing price fell back to the Pro MONTHLY price: a
        # "Team" or "API" buyer paid for and got Pro, a yearly buyer was
        # billed monthly. A plan/period without its own price is not on sale.
        raise HTTPException(status_code=400, detail=f"Gói {plan}/{period} chưa mở bán.")

    stripe      = _stripe()
    customer_id = _get_or_create_stripe_customer(user["id"], user["email"])

    success_url = payload.success_url or f"{_FRONTEND_URL}/billing?success=1&plan={plan}"
    cancel_url  = payload.cancel_url  or f"{_FRONTEND_URL}/pricing?canceled=1"

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": seats}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": user["id"], "plan": plan, "period": period, "seats": seats},
            allow_promotion_codes=True,
            billing_address_collection="auto",
            subscription_data={
                "metadata": {"user_id": user["id"], "plan": plan, "seats": seats}
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Checkout error: {str(e)[:200]}")

    return {"checkout_url": session.url, "session_id": session.id, "plan": plan, "period": period}


# ── Idempotent webhook wrapper ────────────────────────────────────────────────

def _record_payment_event(event_id: str, event_type: str, raw_payload: Any) -> bool:
    """
    Insert payment_events row for idempotency.
    Returns True if new event, False if already processed (duplicate).
    """
    try:
        db = _supabase()
        res = db.table("payment_events").select("processed").eq("provider_event_id", event_id).maybe_single().execute()
        if res.data:
            return False  # already handled

        db.table("payment_events").insert({
            "provider": "stripe",
            "provider_event_id": event_id,
            "event_type": event_type,
            "processed": False,
            "raw_payload": raw_payload if isinstance(raw_payload, dict) else {},
        }).execute()
        return True
    except Exception as exc:
        print(f"[Payments] payment_events insert error: {exc}")
        return True  # allow processing even if log fails


def _mark_payment_event_done(event_id: str, error: Optional[str] = None) -> None:
    try:
        db = _supabase()
        db.table("payment_events").update({
            "processed": error is None,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }).eq("provider_event_id", event_id).execute()
    except Exception:
        pass
