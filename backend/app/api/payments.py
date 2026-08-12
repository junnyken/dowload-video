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
  FRONTEND_URL            — https://your-frontend.com (for redirect URLs)

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

_STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_STRIPE_PRO_PRICE_ID   = os.getenv("STRIPE_PRO_PRICE_ID", "")
_FRONTEND_URL          = os.getenv("FRONTEND_URL", "https://vidgrab.io")

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
    success_url: Optional[str] = None
    cancel_url:  Optional[str] = None


@router.post("/create-checkout-session")
async def create_checkout_session(
    payload: CheckoutRequest,
    user: Dict[str, Any] = Depends(get_required_user),
):
    """
    Create a Stripe Checkout Session for the monthly Pro plan.
    Returns {checkout_url} — redirect user there.
    """
    if not _STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Thanh toán chưa được cấu hình. Vui lòng liên hệ admin.")
    if not _STRIPE_PRO_PRICE_ID:
        raise HTTPException(status_code=503, detail="Gói Pro chưa được cấu hình. Vui lòng liên hệ admin.")

    stripe = _stripe()
    customer_id = _get_or_create_stripe_customer(user["id"], user["email"])

    success_url = payload.success_url or f"{_FRONTEND_URL}/billing?success=1"
    cancel_url  = payload.cancel_url  or f"{_FRONTEND_URL}/billing?canceled=1"

    try:
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": _STRIPE_PRO_PRICE_ID, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": user["id"]},
            allow_promotion_codes=True,
            billing_address_collection="auto",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không tạo được phiên thanh toán: {str(e)[:200]}")

    return {"checkout_url": session.url, "session_id": session.id}


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
        # Dev mode: accept without signature (never do this in prod)
        try:
            event = json.loads(body)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
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
# GET /billing-status  (authenticated)
# ══════════════════════════════════════════════════════════════════════

@router.get("/billing-status")
async def billing_status(user: Dict[str, Any] = Depends(get_required_user)):
    """Return current tier, billing status, and subscription expiry for the user."""
    supabase = _supabase()
    try:
        res = (
            supabase.table("profiles")
            .select("tier, billing_status, subscription_expiry, stripe_customer_id, stripe_subscription_id")
            .eq("id", user["id"])
            .single()
            .execute()
        )
        profile = res.data or {}
    except Exception:
        profile = {}

    tier            = profile.get("tier", "free")
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
        # Fallback to base Pro price if specific price not configured
        price_id = _STRIPE_PRO_PRICE_ID
        if not price_id:
            raise HTTPException(status_code=503, detail=f"Gói {plan}/{period} chưa được cấu hình")

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
