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
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client as get_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter()

_STRIPE_SECRET_KEY        = os.getenv("STRIPE_SECRET_KEY", "")
_STRIPE_WEBHOOK_SECRET    = os.getenv("STRIPE_WEBHOOK_SECRET", "")
_STRIPE_PRO_PRICE_ID      = os.getenv("STRIPE_PRO_PRICE_ID", "")
_STRIPE_PRO_YEAR_PRICE_ID = os.getenv("STRIPE_PRO_YEAR_PRICE_ID", "")
_STRIPE_PRO_MONTH_CENTS   = int(os.getenv("STRIPE_PRO_MONTH_USD_CENTS", "399") or 399)
_STRIPE_PRO_YEAR_CENTS    = int(os.getenv("STRIPE_PRO_YEAR_USD_CENTS", "3900") or 3900)
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


def parse_usd_cents_decimal(raw_val: Any) -> Optional[int]:
    """Parse a positive USD string/number into exact integer cents via Decimal.

    Returns None when missing, non-numeric, <= 0, non-finite, or fractional cents.
    """
    if raw_val is None or isinstance(raw_val, bool):
        return None
    s = str(raw_val).strip()
    if not s:
        return None
    try:
        dec = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite() or dec <= Decimal("0"):
        return None
    cents_dec = dec * Decimal("100")
    if cents_dec != cents_dec.to_integral_value():
        return None
    return int(cents_dec)


def _expected_paypal_receiver_email() -> str:
    """Return configured merchant PayPal receiver email (lowercased), or '' if not configured."""
    candidates = [
        os.getenv("PAYPAL_RECEIVER_EMAIL", "").strip(),
        os.getenv("PAYPAL_BUSINESS_EMAIL", "").strip(),
        os.getenv("PAYPAL_MERCHANT_EMAIL", "").strip(),
        os.getenv("PAYPAL_ME_URL", "").strip(),
    ]
    try:
        from app.core.plans import get_settings as _get_plan_settings  # noqa: PLC0415
        candidates.append((_get_plan_settings().get("paypal_me_url") or "").strip())
    except Exception:
        pass
    for c in candidates:
        if c and "@" in c and not c.lower().startswith("http"):
            return c.lower()
    return ""


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


def _upgrade_user(user_id: str, subscription_id: str, current_period_end: Optional[int] = None, target_tier: str = "pro") -> None:
    supabase = _supabase()
    is_one_time = str(subscription_id or "").startswith(("paypal_", "ls_"))
    # Resolve tier — accept any tier known to the quota/entitlement system.
    from app.core.quotas import TIER_PERMISSIONS  # noqa: PLC0415
    tier = target_tier if target_tier in TIER_PERMISSIONS and target_tier != "free" else "pro"

    from app.core import payment_orders as po  # noqa: PLC0415
    is_down, cur_tier, _exp = po.check_downgrade_active_subscription(user_id, tier, db=supabase)
    if is_down:
        po.record_webhook_manual_review(
            supabase,
            provider="payments",
            provider_event_id=str(subscription_id or ""),
            event_type="upgrade_user_downgrade_blocked",
            user_id=str(user_id),
            current_tier=cur_tier,
            target_tier=tier,
            reason="downgrade_while_higher_tier_active",
            status="needs_manual_review",
        )
        return

    # Resolve human-friendly plan name for the profiles table.
    try:
        from app.core.entitlements import get_plan_def  # noqa: PLC0415
        plan_name = get_plan_def(tier)["name"]
    except Exception:
        plan_name = tier.capitalize()
    update: Dict[str, Any] = {
        "tier":                    tier,
        "billing_status":          "manual" if is_one_time else "active",
        "stripe_subscription_id":  None if is_one_time else subscription_id,
        "plan_name":               plan_name,
    }
    if current_period_end:
        now = datetime.now(timezone.utc)
        target_dt = datetime.fromtimestamp(current_period_end, tz=timezone.utc)
        # Extend from existing unexpired manual/active expiry so early renewal never loses days
        try:
            row = (supabase.table("profiles").select("billing_status, subscription_expiry")
                   .eq("id", user_id).single().execute()).data or {}
            cur = row.get("subscription_expiry")
            if cur and is_one_time:
                cur_dt = datetime.fromisoformat(str(cur).replace("Z", "+00:00"))
                if cur_dt > now:
                    delta = max(timedelta(days=1), target_dt - now)
                    target_dt = cur_dt + delta
        except Exception:
            pass
        update["subscription_expiry"] = target_dt.isoformat()
    try:
        p_res = supabase.table("profiles").update(update).eq("id", user_id).execute()
        if not p_res.data:
            supabase.table("profiles").insert({"id": user_id, **update}).execute()
    except Exception as e:
        print(f"[Payments] upgrade_user failed for {user_id}: {e}")
        return

    # Keep user_usage.plan in sync for admin stats
    try:
        u_res = supabase.table("user_usage").update({"plan": tier}).eq("user_id", user_id).execute()
        if not u_res.data:
            supabase.table("user_usage").insert({
                "user_id": user_id,
                "plan": tier,
                "downloads_today": 0,
                "downloads_this_month": 0,
                "last_reset_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
    except Exception:
        pass

    # First-time Pro welcome bonus (+500 API calls) & mark first_purchase_done
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        from app.core import api_quota as aq  # noqa: PLC0415
        rc = get_redis()
        first_flag = f"api_first_bonus_claimed:{user_id}"
        done_flag = f"first_purchase_done:{user_id}"
        if not rc.get(first_flag) and not rc.get(done_flag):
            rc.set(first_flag, "1")
            rc.set(f"api_bonus:{user_id}:{aq.month_of()}", 500, ex=40 * 86400)
        rc.set(done_flag, "1")
    except Exception:
        pass


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

    if not (at_period_end and period_end_ts):
        try:
            supabase.table("user_usage").update({"plan": "free"}).eq("user_id", user_id).execute()
        except Exception:
            pass


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
    from app.core import payment_orders as po  # noqa: PLC0415
    is_down, cur_tier, exp = po.check_downgrade_active_subscription(str(user["id"]), "pro", db=_supabase())
    if is_down:
        msg = po.downgrade_user_message(cur_tier, "pro", exp)
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "cannot_downgrade_active_subscription",
                "message": msg,
                "user_message": msg,
                "current_tier": cur_tier,
                "target_tier": "pro",
                "subscription_expiry": exp,
            },
        )
    plan_obj = po.plan_by_id("pro_year" if is_year else "pro_month", user_id=str(user["id"]))
    eff_cents = int(plan_obj["usd_cents"]) if plan_obj else (_STRIPE_PRO_YEAR_CENTS if is_year else _STRIPE_PRO_MONTH_CENTS)
    usd_str = f"{eff_cents / 100:.2f}"

    # 1. PayPal Orders v2 REST API or PayPal Email IPN — always backed by internal payment_orders
    pp_client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
    pp_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
    paypal_me = os.getenv("PAYPAL_ME_URL", "").strip()
    if not paypal_me:
        try:
            from app.core.plans import get_settings as _get_plan_settings
            paypal_me = (_get_plan_settings().get("paypal_me_url") or "").strip()
        except Exception:
            paypal_me = ""

    plan_code = "pro_year" if is_year else "pro_month"
    if (pp_client_id and pp_secret) or (paypal_me and "@" in paypal_me and not paypal_me.startswith("http")):
        from app.api.checkout import _create_paypal_order  # noqa: PLC0415
        order, err = po.create_order(str(user["id"]), plan_code, rail="paypal")
        if err:
            status_code = 409 if err in ("too_many_pending", "cannot_downgrade_active_subscription") else 400
            raise HTTPException(status_code=status_code, detail=err)
        if not plan_obj:
            plan_obj = {"id": plan_code, "name_en": "Pro Yearly" if is_year else "Pro Monthly", "usd_cents": eff_cents}
        pp_url, pp_session = await _create_paypal_order(user, plan_obj, order, return_path="/pricing")
        _supabase().table("payment_orders").update({"provider_ref": pp_session}).eq("id", order["id"]).execute()
        return {"checkout_url": pp_url, "session_id": pp_session, "order_id": order["id"]}

    # 2. Simple PayPal.me link (manual review link)
    if paypal_me:
        if not paypal_me.startswith("http"):
            paypal_me = f"https://paypal.me/{paypal_me.lstrip('/')}"
        paypal_me = paypal_me.rstrip("/")
        amt = f"{usd_str}USD"
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
async def paypal_capture(request: Request = None):
    """Removed legacy PayPal capture endpoint — use POST /api/v1/checkout/orders/{order_id}/capture."""
    raise HTTPException(
        status_code=410,
        detail="Endpoint /api/v1/payments/paypal-capture has been removed. Use POST /api/v1/checkout/orders/{order_id}/capture.",
    )


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
    is_new = _record_payment_event(event_id, event_type, event.get("data", {}), provider="stripe")
    if not is_new:
        return {"received": True, "type": event_type, "duplicate": True}

    # ── checkout.session.completed ───────────────────────────────────
    if event_type == "checkout.session.completed":
        meta = data_obj.get("metadata") or {}
        user_id = meta.get("user_id")
        plan_tier = (meta.get("plan") or "pro").lower()
        sub_id  = data_obj.get("subscription")
        if user_id and sub_id:
            # Fetch subscription to get period_end
            try:
                stripe = _stripe()
                sub = stripe.Subscription.retrieve(sub_id)
                period_end = sub.get("current_period_end")
                sub_meta = sub.get("metadata") or {}
                if not meta.get("plan") and sub_meta.get("plan"):
                    plan_tier = str(sub_meta.get("plan")).lower()
            except Exception:
                period_end = None
            from app.core import payment_orders as po  # noqa: PLC0415
            is_down, cur_tier, _exp = po.check_downgrade_active_subscription(user_id, plan_tier, db=_supabase())
            if is_down:
                raw_amt = data_obj.get("amount_total")
                amt_int = int(raw_amt) if isinstance(raw_amt, (int, float)) else None
                po.record_webhook_manual_review(
                    _supabase(),
                    provider="stripe",
                    provider_event_id=event_id,
                    event_type=event_type,
                    user_id=str(user_id),
                    current_tier=cur_tier,
                    target_tier=plan_tier,
                    amount=amt_int,
                    raw_payload=data_obj if isinstance(data_obj, dict) else {},
                    reason="downgrade_while_higher_tier_active",
                    status="needs_manual_review",
                )
                return {
                    "received": True,
                    "type": event_type,
                    "action": "needs_manual_review",
                    "status": "needs_manual_review",
                }
            _upgrade_user(user_id, sub_id, period_end, target_tier=plan_tier)
            try:
                from app.api.referrals import reward_referrer_on_pro_upgrade
                reward_referrer_on_pro_upgrade(user_id)
            except Exception:
                pass
            print(f"[Payments] ✅ Upgraded {user_id} to {plan_tier} (sub={sub_id})")

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
        plan_tier   = ((data_obj.get("metadata") or {}).get("plan") or "pro").lower()
        user_id     = _user_id_from_customer(customer_id)
        if user_id:
            if status in ("active", "trialing"):
                from app.core import payment_orders as po  # noqa: PLC0415
                is_down, cur_tier, _exp = po.check_downgrade_active_subscription(user_id, plan_tier, db=_supabase())
                if is_down:
                    po.record_webhook_manual_review(
                        _supabase(),
                        provider="stripe",
                        provider_event_id=event_id,
                        event_type=event_type,
                        user_id=str(user_id),
                        current_tier=cur_tier,
                        target_tier=plan_tier,
                        raw_payload=data_obj if isinstance(data_obj, dict) else {},
                        reason="downgrade_while_higher_tier_active",
                        status="needs_manual_review",
                    )
                    return {
                        "received": True,
                        "type": event_type,
                        "action": "needs_manual_review",
                        "status": "needs_manual_review",
                    }
                _upgrade_user(user_id, sub_id, period_end, target_tier=plan_tier)
            elif status in ("canceled", "unpaid", "paused"):
                _downgrade_user(user_id, at_period_end=bool(period_end), period_end_ts=period_end)

    # ── invoice.payment_failed ────────────────────────────────────────
    elif event_type == "invoice.payment_failed":
        customer_id = data_obj.get("customer")
        user_id     = _user_id_from_customer(customer_id)
        if user_id:
            _past_due_user(user_id)
            print(f"[Payments] ⚠ Payment failed for {user_id} — grace period applied")

    _mark_payment_event_done(event_id, provider="stripe")
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
        logger.error("SePay webhook rejected: SEPAY_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=503, detail="SePay webhook secret not configured")

    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    from app.core.sepay_webhook import verify, handle  # noqa: PLC0415

    # Quick signature check — reject before parsing
    valid, err = verify(body, headers)
    if not valid:
        if err == "no_webhook_secret_configured":
            logger.error("SePay webhook rejected: SEPAY_WEBHOOK_SECRET is not configured")
            raise HTTPException(status_code=503, detail="SePay webhook secret not configured")
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
    is_new = _record_payment_event(event_id, event_type, {"source": "paypal_webhook"}, provider="paypal_webhook")
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
                plan_id = parts[2] if len(parts) > 2 else "pro_month"

                if order_id and order_id not in ("month", "year"):
                    # Activate via payment_orders
                    try:
                        from app.core import payment_orders as po  # noqa: PLC0415
                        from app.core.payment_orders import activate_order  # noqa: PLC0415
                        paid_amount = 0
                        try:
                            amt_obj = resource.get("amount", {})
                            paid_amount = int(float(amt_obj.get("value", "0")) * 100)
                        except Exception:
                            paid_amount = 0

                        ok, act_msg = activate_order(
                            order_id=order_id,
                            paid_amount=paid_amount or 99999,  # if can't parse, assume paid
                            provider_ref=event_id,
                            evidence={"provider": "paypal_webhook", "event_type": event_type},
                        )
                        if act_msg == "needs_manual_review":
                            return {
                                "received": True,
                                "type": event_type,
                                "action": "needs_manual_review",
                                "status": "needs_manual_review",
                            }
                        if not ok and not str(act_msg).startswith("order_already_"):
                            days = 365 if "year" in plan_id else 30
                            exp = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
                            tier_prefix = plan_id.split("_")[0] if "_" in plan_id else "pro"
                            is_down, cur_tier, _exp = po.check_downgrade_active_subscription(
                                user_id, tier_prefix, db=_supabase()
                            )
                            if is_down:
                                po.record_webhook_manual_review(
                                    _supabase(),
                                    provider="paypal",
                                    provider_event_id=event_id,
                                    event_type=event_type,
                                    user_id=str(user_id),
                                    current_tier=cur_tier,
                                    target_tier=tier_prefix,
                                    amount=paid_amount,
                                    order_id=str(order_id),
                                    raw_payload=resource if isinstance(resource, dict) else {},
                                    reason="downgrade_while_higher_tier_active",
                                    status="needs_manual_review",
                                )
                                return {
                                    "received": True,
                                    "type": event_type,
                                    "action": "needs_manual_review",
                                    "status": "needs_manual_review",
                                }
                            _upgrade_user(user_id, f"paypal_wh_{event_id}", exp, target_tier=tier_prefix)
                    except Exception as e:
                        print(f"[PayPal Webhook] activate error: {e}")
                else:
                    # Direct PayPal Orders v2 flow (custom_id = user_id:month or user_id:year)
                    from app.core import payment_orders as po  # noqa: PLC0415
                    days = 365 if (order_id == "year" or "year" in plan_id) else 30
                    exp = int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())
                    tier_prefix = plan_id.split("_")[0] if "_" in plan_id else "pro"
                    is_down, cur_tier, _exp = po.check_downgrade_active_subscription(
                        user_id, tier_prefix, db=_supabase()
                    )
                    if is_down:
                        direct_amt = 0
                        try:
                            amt_obj = resource.get("amount", {})
                            if not amt_obj and resource.get("purchase_units"):
                                amt_obj = (resource.get("purchase_units") or [{}])[0].get("amount", {})
                            direct_amt = int(round(float(amt_obj.get("value", "0")) * 100))
                        except Exception:
                            direct_amt = 0
                        po.record_webhook_manual_review(
                            _supabase(),
                            provider="paypal",
                            provider_event_id=event_id,
                            event_type=event_type,
                            user_id=str(user_id),
                            current_tier=cur_tier,
                            target_tier=tier_prefix,
                            amount=direct_amt or None,
                            raw_payload=resource if isinstance(resource, dict) else {},
                            reason="downgrade_while_higher_tier_active",
                            status="needs_manual_review",
                        )
                        return {
                            "received": True,
                            "type": event_type,
                            "action": "needs_manual_review",
                            "status": "needs_manual_review",
                        }
                    _upgrade_user(user_id, f"paypal_wh_{event_id}", exp, target_tier=tier_prefix)

    _mark_payment_event_done(event_id, provider="paypal_webhook")
    return {"received": True, "type": event_type}


# ══════════════════════════════════════════════════════════════════════
# POST /paypal/ipn  (PayPal Instant Payment Notification for Email Checkout)
# ══════════════════════════════════════════════════════════════════════

@router.post("/paypal/ipn")
async def paypal_ipn(request: Request):
    """
    Verify PayPal IPN callback with https://ipnpb.paypal.com/cgi-bin/webscr
    (cmd=_notify-validate) and activate ONLY server-created internal orders
    after verifying merchant receiver email, currency, and Decimal amount.
    """
    import httpx
    from urllib.parse import parse_qs

    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty IPN payload")

    # Mandatory receiver email configuration check (never skip when missing)
    expected_email = _expected_paypal_receiver_email()
    if not expected_email:
        logger.error("PayPal IPN rejected: merchant receiver email is not configured")
        raise HTTPException(status_code=503, detail="PayPal receiver email not configured")

    mode = os.getenv("PAYPAL_MODE", "live").strip().lower()
    ipn_url = (
        "https://ipnpb.sandbox.paypal.com/cgi-bin/webscr"
        if mode == "sandbox"
        else "https://ipnpb.paypal.com/cgi-bin/webscr"
    )
    verify_payload = b"cmd=_notify-validate&" + raw_body

    async with httpx.AsyncClient(timeout=20) as client:
        vr = await client.post(
            ipn_url,
            content=verify_payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "VidGrab-IPN-Verification/1.0",
            },
        )
        if vr.status_code != 200 or vr.text.strip() != "VERIFIED":
            raise HTTPException(status_code=401, detail="IPN not verified by PayPal")

    parsed = {k: v[0] for k, v in parse_qs(raw_body.decode("utf-8", errors="replace")).items() if v}
    payment_status = (parsed.get("payment_status") or "").strip()
    txn_id = (parsed.get("txn_id") or "").strip()
    rec_email = (parsed.get("receiver_email") or "").strip().lower()
    biz_email = (parsed.get("business") or "").strip().lower()
    receiver = rec_email if rec_email else biz_email
    custom = (parsed.get("custom") or "").strip()
    mc_currency = (parsed.get("mc_currency") or "").strip().upper()
    raw_gross = parsed.get("mc_gross") if "mc_gross" in parsed else parsed.get("payment_gross")

    from app.core import payment_orders as po  # noqa: PLC0415
    from app.core.payment_orders import activate_order  # noqa: PLC0415

    # Receiver email must strictly match configured merchant email
    if not receiver or receiver != expected_email:
        po.record_webhook_manual_review(
            _supabase(),
            provider="paypal_ipn",
            provider_event_id=f"ipn_{txn_id or 'unmatched_receiver'}",
            event_type="paypal.ipn.receiver_mismatch",
            raw_payload=parsed,
            reason="receiver_email_mismatch",
            status="needs_manual_review",
        )
        raise HTTPException(status_code=400, detail="Receiver email mismatch")

    if payment_status.lower() != "completed" or not txn_id:
        return {"received": True, "status": payment_status}

    if not _record_payment_event(
        f"ipn_{txn_id}",
        "paypal.ipn.completed",
        {"txn_id": txn_id},
        provider="paypal_ipn",
    ):
        return {"received": True, "duplicate": True}

    # Only activate when `custom` references a server-created internal order owned by matching user
    if ":" in custom:
        parts = custom.split(":")
        custom_user_id = parts[0].strip()
        order_id = parts[1].strip() if len(parts) >= 2 else ""
        order = po.get_order(order_id) if (custom_user_id and order_id) else None
        owner_ok = bool(order and str(order.get("user_id") or "") == custom_user_id)
    else:
        custom_user_id = ""
        order_id = custom.strip()
        order = po.get_order(order_id) if order_id else None
        owner_ok = bool(order)

    if not order or not owner_ok:
        po.record_webhook_manual_review(
            _supabase(),
            provider="paypal_ipn",
            provider_event_id=f"ipn_{txn_id}",
            event_type="paypal.ipn.invalid_order",
            user_id=custom_user_id or (str(order["user_id"]) if order else None),
            order_id=str(order["id"]) if order else (order_id or None),
            raw_payload=parsed,
            reason="order_not_found_or_user_mismatch",
            status="needs_manual_review",
        )
        _mark_payment_event_done(f"ipn_{txn_id}", error="order_not_found_or_user_mismatch", provider="paypal_ipn")
        raise HTTPException(status_code=400, detail="Invalid or unmatched internal order in IPN custom")

    expected_currency = str(order.get("currency") or "USD").strip().upper()
    if mc_currency != expected_currency:
        if order.get("status") == "pending":
            _supabase().table("payment_orders").update({
                "status": "needs_manual_review",
                "provider_ref": txn_id,
                "admin_note": f"IPN currency mismatch: {mc_currency or 'missing'} != {expected_currency}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", str(order["id"])).eq("status", "pending").execute()
        po.record_webhook_manual_review(
            _supabase(),
            provider="paypal_ipn",
            provider_event_id=f"ipn_{txn_id}",
            event_type="paypal.ipn.currency_mismatch",
            user_id=str(order["user_id"]),
            order_id=str(order["id"]),
            order_code=str(order.get("code") or "") or None,
            raw_payload=parsed,
            reason=f"currency_mismatch:{mc_currency or 'missing'}!={expected_currency}",
            status="needs_manual_review",
        )
        _mark_payment_event_done(f"ipn_{txn_id}", error="currency_mismatch", provider="paypal_ipn")
        raise HTTPException(status_code=400, detail="IPN currency mismatch")

    paid_cents = parse_usd_cents_decimal(raw_gross)
    if paid_cents is None:
        if order.get("status") == "pending":
            _supabase().table("payment_orders").update({
                "status": "needs_manual_review",
                "provider_ref": txn_id,
                "admin_note": f"IPN invalid/zero amount: {raw_gross!r}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", str(order["id"])).eq("status", "pending").execute()
        po.record_webhook_manual_review(
            _supabase(),
            provider="paypal_ipn",
            provider_event_id=f"ipn_{txn_id}",
            event_type="paypal.ipn.invalid_amount",
            user_id=str(order["user_id"]),
            order_id=str(order["id"]),
            order_code=str(order.get("code") or "") or None,
            raw_payload=parsed,
            reason="invalid_or_zero_amount",
            status="needs_manual_review",
        )
        _mark_payment_event_done(f"ipn_{txn_id}", error="invalid_or_zero_amount", provider="paypal_ipn")
        raise HTTPException(status_code=400, detail="Invalid or zero IPN payment amount")

    _ok, act_msg = activate_order(
        order_id=str(order["id"]),
        paid_amount=paid_cents,
        provider_ref=txn_id,
        evidence={"provider": "paypal_ipn", "txn_id": txn_id, "mc_currency": mc_currency, "mc_gross": str(raw_gross)},
    )
    if act_msg == "underpaid":
        existing_rev = (
            _supabase()
            .table("payment_webhook_events")
            .select("id")
            .in_("provider_event_id", [txn_id, f"ipn_{txn_id}"])
            .limit(1)
            .execute()
        ).data or []
        if not existing_rev:
            plan_info = po.plan_by_id(order.get("plan", "pro_month"), user_id=order.get("user_id"))
            po.record_webhook_manual_review(
                _supabase(),
                provider="paypal_ipn",
                provider_event_id=f"ipn_{txn_id}",
                event_type="paypal.ipn.underpaid",
                user_id=str(order["user_id"]),
                target_tier=(plan_info or {}).get("tier", "pro"),
                amount=paid_cents,
                order_id=str(order["id"]),
                order_code=str(order.get("code") or "") or None,
                raw_payload=parsed,
                reason=f"underpaid:{paid_cents}<{order['amount']}",
                status="needs_manual_review",
            )
        _mark_payment_event_done(f"ipn_{txn_id}", provider="paypal_ipn")
        return {
            "received": True,
            "verified": True,
            "txn_id": txn_id,
            "action": "underpaid",
            "status": "needs_manual_review",
        }
    if act_msg == "needs_manual_review":
        _mark_payment_event_done(f"ipn_{txn_id}", provider="paypal_ipn")
        return {
            "received": True,
            "verified": True,
            "txn_id": txn_id,
            "action": "needs_manual_review",
            "status": "needs_manual_review",
        }

    _mark_payment_event_done(f"ipn_{txn_id}", provider="paypal_ipn")
    return {"received": True, "verified": True, "txn_id": txn_id}


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

    from app.core import payment_orders as po  # noqa: PLC0415
    pending_review = po.get_user_pending_manual_review(str(user["id"]), db=supabase)

    return {
        "tier":                  tier,
        "billing_status":        billing_status_,
        "subscription_expiry":   expiry,
        "has_payment_method":    bool(profile.get("stripe_customer_id")),
        "can_manage_billing":    bool(profile.get("stripe_subscription_id")),
        "pending_manual_review": bool(pending_review),
        "manual_review":         pending_review,
        "manual_review_message": po.MANUAL_REVIEW_USER_MESSAGE if pending_review else None,
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
    ("starter", "monthly"): "STRIPE_STARTER_PRICE_ID",
    ("starter", "yearly"):  "STRIPE_STARTER_YEARLY_PRICE_ID",
    ("pro",     "monthly"): "STRIPE_PRO_PRICE_ID",
    ("pro",     "yearly"):  "STRIPE_PRO_YEARLY_PRICE_ID",
    ("team",    "monthly"): "STRIPE_TEAM_PRICE_ID",
    ("team",    "yearly"):  "STRIPE_TEAM_YEARLY_PRICE_ID",
    ("api",     "monthly"): "STRIPE_API_PRICE_ID",
    ("api",     "yearly"):  "STRIPE_API_YEARLY_PRICE_ID",
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
    plan   = payload.plan.lower()
    period = payload.period.lower()
    seats  = max(1, payload.seats or 1)

    from app.core import payment_orders as po  # noqa: PLC0415
    is_down, cur_tier, exp = po.check_downgrade_active_subscription(str(user["id"]), plan, db=_supabase())
    if is_down:
        msg = po.downgrade_user_message(cur_tier, plan, exp)
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "cannot_downgrade_active_subscription",
                "message": msg,
                "user_message": msg,
                "current_tier": cur_tier,
                "target_tier": plan,
                "subscription_expiry": exp,
            },
        )

    if not _STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Billing not configured")

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


def _record_payment_event(
    event_id: str,
    event_type: str,
    raw_payload: Any,
    provider: str = "stripe",
) -> bool:
    """
    Insert payment_events row for idempotency (fail-closed).
    - Checks both `provider` and legacy `"stripe"` on SELECT so events stored
      before provider separation are still recognized as duplicates.
    - If INSERT hits UNIQUE (provider, provider_event_id) -> duplicate (False).
    - Any other DB error -> raises HTTP 503 so Stripe/PayPal retries without
      processing or mutating tier.
    """
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing webhook event id")

    providers_to_check = list(dict.fromkeys([provider, "stripe"]))
    try:
        db = _supabase()
        res = (
            db.table("payment_events")
            .select("id, provider, processed")
            .eq("provider_event_id", event_id)
            .in_("provider", providers_to_check)
            .execute()
        )
        if res.data:
            return False  # already handled (exact provider or legacy "stripe")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[Payments] payment_events select error ({provider}, {event_id}): {exc}")
        raise HTTPException(status_code=503, detail="Payment event store unavailable") from exc

    try:
        db.table("payment_events").insert({
            "provider": provider,
            "provider_event_id": event_id,
            "event_type": event_type,
            "processed": False,
            "raw_payload": raw_payload if isinstance(raw_payload, dict) else {},
        }).execute()
        return True
    except Exception as exc:
        if _is_unique_violation(exc):
            return False
        print(f"[Payments] payment_events insert error ({provider}, {event_id}): {exc}")
        raise HTTPException(status_code=503, detail="Payment event store unavailable") from exc


def _mark_payment_event_done(
    event_id: str,
    error: Optional[str] = None,
    provider: Optional[str] = None,
) -> None:
    try:
        db = _supabase()
        q = db.table("payment_events").update({
            "processed": error is None,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }).eq("provider_event_id", event_id)
        if provider:
            q = q.eq("provider", provider)
        q.execute()
    except Exception:
        pass
