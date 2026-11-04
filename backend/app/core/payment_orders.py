"""
Payment Orders engine — tạo đơn, mã chuyển khoản, VietQR, kích hoạt Pro.

Mã đơn: VGP + 8 ký tự Crockford Base32 (khác mã tay VG + 8 hex).
Một đơn → một gói → một lần kích hoạt (compare-and-set).
"""
from __future__ import annotations

import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

# Crockford Base32 alphabet (no I, L, O, U to avoid confusion)
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

ORDER_TTL_HOURS = 24
MAX_PENDING_ORDERS = 3

# ── First-time buyer & Plan catalog ─────────────────────────────────

def is_first_time_buyer(user_id: Optional[str]) -> bool:
    """True only if the user has never completed a paid order or held a paid subscription.

    First-time buyers get the welcome discount ($6.99/mo vs $9.99/mo regular on Pro,
    $19.99/mo vs $29.99/mo regular on Team) + 500 Bonus API calls.
    Any subsequent purchase / renewal is charged at the regular price.
    """
    if not user_id:
        return True

    uid = str(user_id).strip()
    if not uid:
        return True

    # 1. Fast Redis check
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        rc = get_redis()
        if rc.get(f"first_purchase_done:{uid}") or rc.get(f"api_first_bonus_claimed:{uid}"):
            return False
    except Exception:
        pass

    # 2. Check payment_orders table for any previously paid order
    try:
        db = _db()
        res = (
            db.table("payment_orders")
            .select("id")
            .eq("user_id", uid)
            .eq("status", "paid")
            .limit(1)
            .execute()
        )
        if res.data:
            try:
                from app.core.redis_client import get_redis  # noqa: PLC0415
                get_redis().set(f"first_purchase_done:{uid}", "1")
            except Exception:
                pass
            return False
    except Exception:
        pass

    # 3. Check profiles table for any prior or active paid subscription
    try:
        db = _db()
        pres = (
            db.table("profiles")
            .select("tier, billing_status, subscription_expiry")
            .eq("id", uid)
            .maybe_single()
            .execute()
        )
        prof = pres.data or {}
        tier = (prof.get("tier") or "free").lower()
        bstatus = (prof.get("billing_status") or "none").lower()
        if tier in ("starter", "pro", "team", "enterprise") or bstatus in ("active", "manual", "canceling", "past_due") or prof.get("subscription_expiry"):
            return False
    except Exception:
        pass

    return True


def catalog(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Plans on sale — first-time promo price for new buyers, regular price for renewals."""
    from app.core.plans import get_settings  # noqa: PLC0415
    s = get_settings()
    first_time = is_first_time_buyer(user_id)

    st_m_cents = int(s.get("starter_month_usd_cents") or 499)
    st_y_cents = int(s.get("starter_year_usd_cents") or 3500)

    # Pro: First-time ($6.99 / 149k, $49 / 990k) vs Regular Renewal ($9.99 / 199k, $69 / 1.490k)
    pro_first_m_cents = int(s.get("price_month_usd_cents") or os.getenv("STRIPE_PRO_MONTH_USD_CENTS", "699") or 699)
    pro_first_y_cents = int(s.get("price_year_usd_cents") or os.getenv("STRIPE_PRO_YEAR_USD_CENTS", "4900") or 4900)
    pro_first_m_vnd = int(s.get("price_month_vnd") or 149_000)
    pro_first_y_vnd = int(s.get("price_year_vnd") or 990_000)

    pro_reg_m_cents = int(s.get("regular_month_usd_cents") or 999)
    pro_reg_y_cents = int(s.get("regular_year_usd_cents") or 6900)
    pro_reg_m_vnd = int(s.get("regular_month_vnd") or 199_000)
    pro_reg_y_vnd = int(s.get("regular_year_vnd") or 1_490_000)

    # Team: First-time ($19.99 / 449k, $149 / 2.990k) vs Regular Renewal ($29.99 / 599k, $219 / 4.490k)
    tm_first_m_cents = int(s.get("team_month_usd_cents") or 1999)
    tm_first_y_cents = int(s.get("team_year_usd_cents") or 14900)
    tm_first_m_vnd = int(s.get("team_month_vnd") or 449_000)
    tm_first_y_vnd = int(s.get("team_year_vnd") or 2_990_000)

    tm_reg_m_cents = int(s.get("team_regular_month_usd_cents") or 2999)
    tm_reg_y_cents = int(s.get("team_regular_year_usd_cents") or 21900)
    tm_reg_m_vnd = int(s.get("team_regular_month_vnd") or 599_000)
    tm_reg_y_vnd = int(s.get("team_regular_year_vnd") or 4_490_000)

    eff_pro_m_cents = pro_first_m_cents if first_time else pro_reg_m_cents
    eff_pro_y_cents = pro_first_y_cents if first_time else pro_reg_y_cents
    eff_pro_m_vnd = pro_first_m_vnd if first_time else pro_reg_m_vnd
    eff_pro_y_vnd = pro_first_y_vnd if first_time else pro_reg_y_vnd

    eff_tm_m_cents = tm_first_m_cents if first_time else tm_reg_m_cents
    eff_tm_y_cents = tm_first_y_cents if first_time else tm_reg_y_cents
    eff_tm_m_vnd = tm_first_m_vnd if first_time else tm_reg_m_vnd
    eff_tm_y_vnd = tm_first_y_vnd if first_time else tm_reg_y_vnd

    plans = [
        {
            "id": "starter_month",
            "tier": "starter",
            "name": "Starter Tháng",
            "name_en": "Starter Monthly",
            "vnd": s.get("starter_month_vnd", 99_000),
            "usd_cents": st_m_cents,
            "usd": round(st_m_cents / 100, 2),
            "regular_vnd": s.get("starter_month_vnd", 99_000),
            "regular_usd": round(st_m_cents / 100, 2),
            "is_first_time_offer": False,
            "days": 30,
        },
        {
            "id": "starter_year",
            "tier": "starter",
            "name": "Starter Năm",
            "name_en": "Starter Yearly",
            "vnd": s.get("starter_year_vnd", 690_000),
            "usd_cents": st_y_cents,
            "usd": round(st_y_cents / 100, 2),
            "regular_vnd": s.get("starter_year_vnd", 690_000),
            "regular_usd": round(st_y_cents / 100, 2),
            "is_first_time_offer": False,
            "days": 365,
        },
        {
            "id": "pro_month",
            "tier": "pro",
            "name": "Pro Tháng" if first_time else "Pro Tháng (Gia hạn)",
            "name_en": "Pro Monthly" if first_time else "Pro Monthly (Renewal)",
            "vnd": eff_pro_m_vnd,
            "usd_cents": eff_pro_m_cents,
            "usd": round(eff_pro_m_cents / 100, 2),
            "first_time_vnd": pro_first_m_vnd,
            "first_time_usd": round(pro_first_m_cents / 100, 2),
            "regular_vnd": pro_reg_m_vnd,
            "regular_usd": round(pro_reg_m_cents / 100, 2),
            "original_usd": round(pro_reg_m_cents / 100, 2),
            "bonus_api_calls": 500 if first_time else 0,
            "is_first_time_offer": first_time,
            "days": 30,
        },
        {
            "id": "pro_year",
            "tier": "pro",
            "name": "Pro Năm" if first_time else "Pro Năm (Gia hạn)",
            "name_en": "Pro Yearly" if first_time else "Pro Yearly (Renewal)",
            "vnd": eff_pro_y_vnd,
            "usd_cents": eff_pro_y_cents,
            "usd": round(eff_pro_y_cents / 100, 2),
            "first_time_vnd": pro_first_y_vnd,
            "first_time_usd": round(pro_first_y_cents / 100, 2),
            "regular_vnd": pro_reg_y_vnd,
            "regular_usd": round(pro_reg_y_cents / 100, 2),
            "original_usd": round(pro_reg_y_cents / 100, 2),
            "bonus_api_calls": 500 if first_time else 0,
            "is_first_time_offer": first_time,
            "days": 365,
        },
        {
            "id": "team_month",
            "tier": "team",
            "name": "Team / Business API Tháng" if first_time else "Team / Business API Tháng (Gia hạn)",
            "name_en": "Team / Business API Monthly" if first_time else "Team / Business API Monthly (Renewal)",
            "vnd": eff_tm_m_vnd,
            "usd_cents": eff_tm_m_cents,
            "usd": round(eff_tm_m_cents / 100, 2),
            "first_time_vnd": tm_first_m_vnd,
            "first_time_usd": round(tm_first_m_cents / 100, 2),
            "regular_vnd": tm_reg_m_vnd,
            "regular_usd": round(tm_reg_m_cents / 100, 2),
            "original_usd": round(tm_reg_m_cents / 100, 2),
            "is_first_time_offer": first_time,
            "days": 30,
        },
        {
            "id": "team_year",
            "tier": "team",
            "name": "Team / Business API Năm" if first_time else "Team / Business API Năm (Gia hạn)",
            "name_en": "Team / Business API Yearly" if first_time else "Team / Business API Yearly (Renewal)",
            "vnd": eff_tm_y_vnd,
            "usd_cents": eff_tm_y_cents,
            "usd": round(eff_tm_y_cents / 100, 2),
            "first_time_vnd": tm_first_y_vnd,
            "first_time_usd": round(tm_first_y_cents / 100, 2),
            "regular_vnd": tm_reg_y_vnd,
            "regular_usd": round(tm_reg_y_cents / 100, 2),
            "original_usd": round(tm_reg_y_cents / 100, 2),
            "is_first_time_offer": first_time,
            "days": 365,
        },
    ]
    return plans


def plan_by_id(plan_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for p in catalog(user_id=user_id):
        if p["id"] == plan_id:
            return p
    return None


# ── Code generation ─────────────────────────────────────────────────

def new_code() -> str:
    """Generate VGP + 8 Crockford Base32 characters."""
    chars = "".join(secrets.choice(_CROCKFORD) for _ in range(8))
    return f"VGP{chars}"


# ── VietQR URL ──────────────────────────────────────────────────────

def qr_image_url(
    bank_bin: str,
    bank_account: str,
    bank_holder: str,
    amount: int,
    content: str,
) -> Optional[str]:
    """Generate VietQR image URL. Returns None if bank info missing."""
    if not (bank_bin and bank_account):
        return None
    return (
        f"https://img.vietqr.io/image/{bank_bin}-{bank_account}-compact2.png"
        f"?amount={amount}"
        f"&addInfo={quote(content)}"
        f"&accountName={quote(bank_holder or '')}"
    )


# ── DB operations ───────────────────────────────────────────────────

_SCHEMA_ENSURED = False

_MIGRATION_042_SQL = """
CREATE TABLE IF NOT EXISTS payment_orders (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          TEXT NOT NULL,
  plan             TEXT NOT NULL DEFAULT 'pro_month',
  rail             TEXT NOT NULL DEFAULT 'vietqr',
  currency         TEXT NOT NULL DEFAULT 'VND',
  amount           BIGINT NOT NULL,
  code             TEXT NOT NULL UNIQUE,
  status           TEXT NOT NULL DEFAULT 'pending',
  paid_amount      BIGINT DEFAULT 0,
  paid_at          TIMESTAMPTZ,
  activated_at     TIMESTAMPTZ,
  expires_at       TIMESTAMPTZ,
  provider_ref     TEXT,
  evidence         JSONB DEFAULT '{}',
  admin_note       TEXT,
  created_at       TIMESTAMPTZ DEFAULT NOW(),
  updated_at       TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_orders_user ON payment_orders (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payment_orders_status ON payment_orders (status, expires_at);
CREATE INDEX IF NOT EXISTS idx_payment_orders_code ON payment_orders (code);
ALTER TABLE payment_events
  ADD COLUMN IF NOT EXISTS order_id    UUID,
  ADD COLUMN IF NOT EXISTS match_type  TEXT,
  ADD COLUMN IF NOT EXISTS amount      BIGINT;
"""


def ensure_schema(force: bool = False) -> bool:
    """Auto-run Migration 042 on Postgres Vibe Host if not yet applied."""
    global _SCHEMA_ENSURED
    if _SCHEMA_ENSURED and not force:
        return True
    try:
        from app.core.database import get_service_client  # noqa: PLC0415
        db = get_service_client()
        pool = getattr(db, "_pool", None)
        if pool is not None:
            pool.run(_MIGRATION_042_SQL, (), fetch=False)
            pool.columns.pop("payment_orders", None)
            pool.columns.pop("payment_events", None)
            pool.pkeys.pop("payment_orders", None)
        _SCHEMA_ENSURED = True
        return True
    except Exception as exc:
        print(f"[PaymentOrders] ensure_schema warning: {exc}")
        return False


def _db():
    ensure_schema()
    from app.core.database import get_service_client  # noqa: PLC0415
    return get_service_client()


def create_order(
    user_id: str,
    plan_id: str,
    rail: str = "vietqr",
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Create a pending payment order.

    Returns (order_dict, error_message).
    error_message is None on success.
    """
    plan = plan_by_id(plan_id, user_id=user_id)
    if not plan:
        return {}, "plan_not_found"

    db = _db()

    # Check max pending orders
    try:
        res = (
            db.table("payment_orders")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .eq("status", "pending")
            .execute()
        )
        if (res.count or 0) >= MAX_PENDING_ORDERS:
            return {}, "too_many_pending"
    except Exception:
        pass

    # Determine currency & amount
    if rail == "vietqr":
        currency = "VND"
        amount = plan["vnd"]
    else:
        currency = "USD"
        amount = plan["usd_cents"]

    code = new_code()
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=ORDER_TTL_HOURS)).isoformat()

    order_data = {
        "user_id": user_id,
        "plan": plan_id,
        "rail": rail,
        "currency": currency,
        "amount": amount,
        "code": code,
        "status": "pending",
        "expires_at": expires_at,
    }

    try:
        res = db.table("payment_orders").insert(order_data).execute()
        order = res.data[0] if res.data else order_data
        return order, None
    except Exception as e:
        # Code collision (extremely unlikely) — retry once
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            order_data["code"] = new_code()
            res = db.table("payment_orders").insert(order_data).execute()
            order = res.data[0] if res.data else order_data
            return order, None
        raise


def get_order(order_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch an order by ID. If user_id provided, verify ownership."""
    db = _db()
    q = db.table("payment_orders").select("*").eq("id", order_id)
    if user_id:
        q = q.eq("user_id", user_id)
    try:
        res = q.single().execute()
        return res.data
    except Exception:
        return None


def get_order_by_code(code: str) -> Optional[Dict[str, Any]]:
    """Fetch a pending order by transfer code."""
    db = _db()
    try:
        res = (
            db.table("payment_orders")
            .select("*")
            .eq("code", code.upper().strip())
            .eq("status", "pending")
            .single()
            .execute()
        )
        return res.data
    except Exception:
        return None


def list_orders(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """List recent orders for a user."""
    db = _db()
    try:
        res = (
            db.table("payment_orders")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception:
        return []


def activate_order(order_id: str, paid_amount: int, provider_ref: str = "",
                   evidence: Optional[Dict] = None) -> Tuple[bool, str]:
    """Activate a paid order — compare-and-set from pending → paid.

    Returns (success, message).
    """
    db = _db()

    # Fetch order
    try:
        res = db.table("payment_orders").select("*").eq("id", order_id).single().execute()
        order = res.data
    except Exception:
        return False, "order_not_found"

    if not order:
        return False, "order_not_found"
    if order["status"] != "pending":
        return False, f"order_already_{order['status']}"

    # Check if expired
    exp = order.get("expires_at")
    if exp:
        try:
            exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
            if exp_dt < datetime.now(timezone.utc):
                # Late payment
                db.table("payment_orders").update({
                    "status": "expired",
                    "paid_amount": paid_amount,
                    "paid_at": datetime.now(timezone.utc).isoformat(),
                    "provider_ref": provider_ref,
                    "evidence": evidence or {},
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", order_id).eq("status", "pending").execute()
                return False, "order_expired_late_payment"
        except Exception:
            pass

    # Check amount
    if paid_amount < order["amount"]:
        db.table("payment_orders").update({
            "status": "underpaid",
            "paid_amount": paid_amount,
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "provider_ref": provider_ref,
            "evidence": evidence or {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", order_id).eq("status", "pending").execute()
        return False, "underpaid"

    now = datetime.now(timezone.utc)

    # Compare-and-set: pending → paid
    update_res = db.table("payment_orders").update({
        "status": "paid",
        "paid_amount": paid_amount,
        "paid_at": now.isoformat(),
        "activated_at": now.isoformat(),
        "provider_ref": provider_ref,
        "evidence": evidence or {},
        "updated_at": now.isoformat(),
    }).eq("id", order_id).eq("status", "pending").execute()

    if not update_res.data:
        return False, "concurrent_activation"

    # Upgrade user
    plan = plan_by_id(order["plan"])
    days = plan["days"] if plan else 30
    target_tier = plan.get("tier", "pro") if plan else "pro"
    _upgrade_user_pro(order["user_id"], days, f"order_{order_id}", target_tier=target_tier)

    return True, "activated"


def _upgrade_user_pro(user_id: str, days: int, subscription_id: str, target_tier: str = "pro") -> None:
    """Upgrade user to paid tier (starter/pro/team) for N days using the manual grant flow."""
    db = _db()
    from app.api.admin import _manual_pro_fields  # noqa: PLC0415

    fields = _manual_pro_fields(db, user_id, days)
    fields["tier"] = target_tier or "pro"

    try:
        p_res = db.table("profiles").update(fields).eq("id", user_id).execute()
        if not p_res.data:
            db.table("profiles").insert({"id": user_id, **fields}).execute()
    except Exception as e:
        print(f"[PaymentOrders] upgrade failed for {user_id}: {e}")
        return

    # Sync user_usage.plan
    try:
        u_res = db.table("user_usage").update({"plan": target_tier or "pro"}).eq("user_id", user_id).execute()
        if not u_res.data:
            db.table("user_usage").insert({
                "user_id": user_id,
                "plan": target_tier or "pro",
                "downloads_today": 0,
                "downloads_this_month": 0,
                "last_reset_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
    except Exception:
        pass

    # First-time Pro welcome bonus: +500 API calls ONLY on 1st purchase, then mark first_purchase_done
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        from app.core import api_quota as aq  # noqa: PLC0415
        rc = get_redis()
        first_flag = f"api_first_bonus_claimed:{user_id}"
        done_flag = f"first_purchase_done:{user_id}"
        if (target_tier or "pro") == "pro" and not rc.get(first_flag) and not rc.get(done_flag):
            rc.set(first_flag, "1")
            rc.set(f"api_bonus:{user_id}:{aq.month_of()}", 500, ex=40 * 86400)
        rc.set(done_flag, "1")
    except Exception:
        pass

    # Reward referrer
    try:
        from app.api.referrals import reward_referrer_on_pro_upgrade  # noqa: PLC0415
        reward_referrer_on_pro_upgrade(user_id)
    except Exception:
        pass

    print(f"[PaymentOrders] ✅ Upgraded {user_id} to Pro ({days}d, ref={subscription_id})")


def cancel_order(order_id: str, user_id: str) -> Tuple[bool, str]:
    """Cancel a pending order."""
    db = _db()
    try:
        res = db.table("payment_orders").update({
            "status": "canceled",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", order_id).eq("user_id", user_id).eq("status", "pending").execute()
        if res.data:
            return True, "canceled"
        return False, "not_found_or_not_pending"
    except Exception as e:
        return False, str(e)


def expire_pending_orders() -> int:
    """Expire orders past their TTL. Called periodically."""
    db = _db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        res = db.table("payment_orders").update({
            "status": "expired",
            "updated_at": now,
        }).eq("status", "pending").lt("expires_at", now).execute()
        count = len(res.data) if res.data else 0
        if count:
            print(f"[PaymentOrders] Expired {count} pending orders")
        return count
    except Exception as e:
        print(f"[PaymentOrders] expire error: {e}")
        return 0


# ── Admin operations ────────────────────────────────────────────────

def admin_list_orders(
    status: Optional[str] = None,
    rail: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    q_text: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """List orders for admin dashboard, returns (orders, total_count)."""
    db = _db()
    q = db.table("payment_orders").select("*", count="exact")
    if status and status != "all":
        q = q.eq("status", status)
    if rail and rail != "all":
        q = q.eq("rail", rail)
    if q_text:
        term = q_text.strip()
        if term.upper().startswith("VGP"):
            q = q.ilike("code", f"%{term}%")
        else:
            q = q.ilike("user_id", f"%{term}%")
    try:
        res = q.order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        rows = res.data or []
        return rows, int(res.count if res.count is not None else len(rows))
    except Exception:
        return [], 0


def admin_order_stats() -> Dict[str, Any]:
    """Aggregate KPI counts for admin OrdersPage."""
    db = _db()
    stats = {
        "total": 0,
        "pending": 0,
        "paid": 0,
        "expired": 0,
        "canceled": 0,
        "underpaid": 0,
        "revenue_vnd": 0,
    }
    try:
        res = db.table("payment_orders").select("status, amount, paid_amount, currency").limit(2000).execute()
        for r in res.data or []:
            st = r.get("status") or "pending"
            stats["total"] += 1
            if st in stats:
                stats[st] += 1
            if st == "paid":
                amt = int(r.get("paid_amount") or r.get("amount") or 0)
                if r.get("currency") == "USD":
                    stats["revenue_vnd"] += amt * 255
                else:
                    stats["revenue_vnd"] += amt
    except Exception:
        pass
    return stats


def admin_activate_order(order_id: str, admin_note: str = "") -> Tuple[bool, str]:
    """Admin manually activates an order (e.g. underpaid but close enough)."""
    db = _db()
    try:
        res = db.table("payment_orders").select("*").eq("id", order_id).single().execute()
        order = res.data
    except Exception:
        return False, "not_found"

    if not order:
        return False, "not_found"
    if order["status"] == "paid":
        return False, "already_activated"

    now = datetime.now(timezone.utc)
    paid_amount = order.get("paid_amount") or order["amount"]

    db.table("payment_orders").update({
        "status": "paid",
        "paid_amount": paid_amount,
        "paid_at": now.isoformat(),
        "activated_at": now.isoformat(),
        "admin_note": admin_note or "Admin manual activation",
        "updated_at": now.isoformat(),
    }).eq("id", order_id).execute()

    plan = plan_by_id(order["plan"])
    days = plan["days"] if plan else 30
    target_tier = plan.get("tier", "pro") if plan else "pro"
    _upgrade_user_pro(order["user_id"], days, f"admin_order_{order_id}", target_tier=target_tier)

    return True, "activated_by_admin"
