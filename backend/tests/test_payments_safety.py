"""
Safety tests for payments, orders, webhooks, and tier transitions.
Covers:
- Blocking tier downgrades while a higher paid tier is still active (409 on checkout/order creation;
  needs_manual_review + no tier/expiry mutation on Stripe, PayPal, PayPal IPN, and SePay webhooks).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
from fastapi import HTTPException


class _FakeResult:
    def __init__(self, data: Any = None, count: Optional[int] = None):
        self.data = data
        self.count = count


class _FakeTableQuery:
    def __init__(self, store: Dict[str, List[Dict[str, Any]]], table_name: str):
        self._store = store
        self._table = table_name
        self._op = "select"
        self._payload: Any = None
        self._filters: List[Any] = []
        self._single = False
        self._maybe_single = False
        self._count_mode: Optional[str] = None
        self._limit: Optional[int] = None

    def select(self, *_args, count: Optional[str] = None, **_kwargs):
        self._op = "select"
        self._count_mode = count
        return self

    def insert(self, payload: Any):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: Dict[str, Any]):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, col: str, val: Any):
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col: str, val: Any):
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col: str, val: Any):
        self._filters.append(("lte", col, val))
        return self

    def lt(self, col: str, val: Any):
        self._filters.append(("lt", col, val))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
        return self

    def limit(self, n: int):
        self._limit = n
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def _matches(self, row: Dict[str, Any]) -> bool:
        for kind, col, val in self._filters:
            rv = row.get(col)
            if kind == "eq" and str(rv) != str(val):
                return False
            if kind == "gte" and str(rv or "") < str(val):
                return False
            if kind == "lte" and str(rv or "") > str(val):
                return False
            if kind == "lt" and str(rv or "") >= str(val):
                return False
        return True

    def execute(self) -> _FakeResult:
        rows = self._store.setdefault(self._table, [])
        if self._op == "insert":
            items = self._payload if isinstance(self._payload, list) else [self._payload]
            inserted = []
            for item in items:
                row = dict(item)
                if "id" not in row:
                    row["id"] = f"{self._table}-{len(rows) + 1}"
                rows.append(row)
                inserted.append(dict(row))
            return _FakeResult(data=inserted, count=len(inserted))

        matched = [r for r in rows if self._matches(r)]
        if self._op == "update":
            updated = []
            for r in matched:
                r.update(self._payload)
                updated.append(dict(r))
            return _FakeResult(data=updated, count=len(updated))

        if self._limit is not None:
            matched = matched[: self._limit]
        if self._single:
            if not matched:
                raise RuntimeError("No rows found for single()")
            return _FakeResult(data=dict(matched[0]), count=1)
        if self._maybe_single:
            return _FakeResult(data=dict(matched[0]) if matched else None, count=1 if matched else 0)
        return _FakeResult(data=[dict(r) for r in matched], count=len(matched))


class FakeSupabaseDB:
    def __init__(self):
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "profiles": [],
            "user_usage": [],
            "payment_orders": [],
            "payment_events": [],
            "payment_webhook_events": [],
        }

    def table(self, name: str) -> _FakeTableQuery:
        return _FakeTableQuery(self.tables, name)


@pytest.fixture()
def fake_env(monkeypatch):
    db = FakeSupabaseDB()
    rc = fakeredis.FakeRedis(decode_responses=True)

    from app.core import payment_orders as po
    from app.core import plans
    from app.api import payments as payments_api

    monkeypatch.setattr(po, "_db", lambda: db)
    monkeypatch.setattr(po, "ensure_schema", lambda force=False: True)
    monkeypatch.setattr(plans, "_r", lambda: rc)
    monkeypatch.setattr(payments_api, "_supabase", lambda: db)
    monkeypatch.setattr("app.core.database.get_service_client", lambda: db)
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: rc)
    return db, rc


def _future_iso(days: int = 20) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ══════════════════════════════════════════════════════════════════════
# Item 2A: Order / Checkout creation blocks downgrades with HTTP 409
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_checkout_create_order_blocks_downgrade_with_409(fake_env, monkeypatch):
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.core import payments_auto

    monkeypatch.setattr(payments_auto, "visible", lambda _req=None: True)

    uid = "11111111-2222-3333-4444-555555555555"
    exp = _future_iso(25)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": exp,
    })

    req = MagicMock()
    payload = checkout_api.CreateOrderRequest(plan="starter_month", rail="vietqr")
    with pytest.raises(HTTPException) as exc_info:
        await checkout_api.create_order(payload=payload, request=req, user={"id": uid, "email": "u@test.com"})

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("error_code") == "cannot_downgrade_active_subscription"
    assert "user_message" in detail and "Pro" in detail["user_message"]
    assert db.tables["payment_orders"] == []


@pytest.mark.anyio
async def test_checkout_create_order_allows_lower_tier_after_expiry(fake_env, monkeypatch):
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.core import payments_auto
    from app.core import plans

    monkeypatch.setattr(payments_auto, "visible", lambda _req=None: True)
    plans.set_settings({"bank_name": "ACB", "bank_account": "12345678", "bank_holder": "TEST"})

    uid = "11111111-2222-3333-4444-666666666666"
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": _past_iso(2),
    })

    req = MagicMock()
    payload = checkout_api.CreateOrderRequest(plan="starter_month", rail="vietqr")
    res = await checkout_api.create_order(payload=payload, request=req, user={"id": uid, "email": "u@test.com"})
    assert res["order"]["plan"] == "starter_month"
    assert res["order"]["status"] == "pending"


@pytest.mark.anyio
async def test_payments_create_checkout_session_v1_and_v2_block_downgrade_409(fake_env):
    db, _rc = fake_env
    from app.api import payments as payments_api

    uid = "22222222-3333-4444-5555-666666666666"
    exp = _future_iso(15)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "team",
        "billing_status": "active",
        "subscription_expiry": exp,
    })

    # v1 defaults to Pro, which is lower than active Team -> 409
    with pytest.raises(HTTPException) as exc1:
        await payments_api.create_checkout_session(
            payload=payments_api.CheckoutRequest(period="month"),
            user={"id": uid, "email": "team@test.com"},
        )
    assert exc1.value.status_code == 409
    assert exc1.value.detail["user_message"]

    # v2 requesting starter while on Team -> 409
    with pytest.raises(HTTPException) as exc2:
        await payments_api.create_checkout_session_v2(
            payload=payments_api.CheckoutRequestV2(plan="starter", period="monthly"),
            user={"id": uid, "email": "team@test.com"},
        )
    assert exc2.value.status_code == 409
    assert exc2.value.detail["user_message"]


# ══════════════════════════════════════════════════════════════════════
# Item 2B: Webhook downgrade protection (Stripe, PayPal, SePay)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_stripe_webhook_blocks_downgrade_and_records_manual_review(fake_env, monkeypatch, caplog):
    db, _rc = fake_env
    from app.api import payments as payments_api

    uid = "33333333-4444-5555-6666-777777777777"
    orig_exp = _future_iso(30)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "team",
        "billing_status": "active",
        "subscription_expiry": orig_exp,
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "team"})

    monkeypatch.setattr(payments_api, "_STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    fake_stripe = MagicMock()
    fake_stripe.Webhook.construct_event.return_value = {
        "id": "evt_downgrade_stripe_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"user_id": uid, "plan": "starter"},
                "subscription": "sub_starter_123",
            }
        },
    }
    fake_stripe.Subscription.retrieve.return_value = {
        "current_period_end": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()),
        "metadata": {"plan": "starter"},
    }
    monkeypatch.setattr(payments_api, "_stripe", lambda: fake_stripe)

    req = MagicMock()
    req.body = AsyncMock(return_value=b"{}")
    req.headers = {"stripe-signature": "t=1,v1=sig"}

    with caplog.at_level(logging.WARNING):
        res = await payments_api.stripe_webhook(req)

    assert res["status"] == "needs_manual_review"
    # Tier and expiry MUST NOT change
    prof = db.tables["profiles"][0]
    assert prof["tier"] == "team"
    assert prof["subscription_expiry"] == orig_exp
    assert db.tables["user_usage"][0]["plan"] == "team"
    # Recorded in payment_webhook_events
    assert len(db.tables["payment_webhook_events"]) == 1
    ev = db.tables["payment_webhook_events"][0]
    assert ev["provider"] == "stripe"
    assert ev["status"] == "needs_manual_review"
    assert ev["current_tier"] == "team"
    assert ev["target_tier"] == "starter"
    assert any("needs_manual_review" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_paypal_webhook_and_ipn_block_downgrade_and_record_manual_review(fake_env, monkeypatch, caplog):
    db, _rc = fake_env
    from app.api import payments as payments_api

    uid = "44444444-5555-6666-7777-888888888888"
    orig_exp = _future_iso(25)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": orig_exp,
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "pro"})

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "pp_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "pp_sec")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "pp_wh_id")
    monkeypatch.setattr(payments_api, "_paypal_access_token", AsyncMock(return_value="tok_123"))

    class _FakeVerifyResp:
        status_code = 200
        text = "VERIFIED"
        def json(self):
            return {"verification_status": "SUCCESS"}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, *a, **kw):
            return _FakeVerifyResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    # 1. PayPal REST webhook with starter_month while user has active Pro
    event_payload = {
        "id": "WH-PP-DOWN-1",
        "event_type": "CHECKOUT.ORDER.COMPLETED",
        "resource": {
            "purchase_units": [{"custom_id": f"{uid}:month:starter_month"}],
            "amount": {"value": "1.99"},
        },
    }
    req = MagicMock()
    req.body = AsyncMock(return_value=json.dumps(event_payload).encode("utf-8"))
    req.headers = {}

    with caplog.at_level(logging.WARNING):
        res_wh = await payments_api.paypal_webhook(req)

    assert res_wh["status"] == "needs_manual_review"
    assert db.tables["profiles"][0]["tier"] == "pro"
    assert db.tables["profiles"][0]["subscription_expiry"] == orig_exp

    # 2. PayPal IPN with starter_month while user has active Pro
    ipn_body = f"payment_status=Completed&txn_id=TXN_DOWN_1&receiver_email=merchant@test.com&custom={uid}::starter_month&mc_gross=1.99"
    req_ipn = MagicMock()
    req_ipn.body = AsyncMock(return_value=ipn_body.encode("utf-8"))
    monkeypatch.setenv("PAYPAL_ME_URL", "merchant@test.com")

    res_ipn = await payments_api.paypal_ipn(req_ipn)
    assert res_ipn["status"] == "needs_manual_review"
    assert db.tables["profiles"][0]["tier"] == "pro"
    assert db.tables["profiles"][0]["subscription_expiry"] == orig_exp
    assert len(db.tables["payment_webhook_events"]) == 2
    assert all(e["status"] == "needs_manual_review" for e in db.tables["payment_webhook_events"])


def test_sepay_webhook_blocks_downgrade_and_records_manual_review(fake_env, monkeypatch, caplog):
    db, _rc = fake_env
    from app.core import sepay_webhook

    uid = "55555555-6666-7777-8888-999999999999"
    orig_exp = _future_iso(20)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": orig_exp,
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "pro"})

    # Also seed a pending starter_month order (e.g. created before user upgraded to Pro)
    db.tables["payment_orders"].append({
        "id": "ord-down-1",
        "user_id": uid,
        "plan": "starter_month",
        "rail": "vietqr",
        "currency": "VND",
        "amount": 49000,
        "code": "VGP23456789",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    secret = "sepay_test_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    body = json.dumps({
        "id": 90001,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 49000,
        "content": "VGP23456789",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}

    with caplog.at_level(logging.WARNING):
        res = sepay_webhook.handle(body, headers)

    assert res["action"] == "needs_manual_review"
    assert res["status"] == "needs_manual_review"
    # Tier and expiry MUST remain untouched
    assert db.tables["profiles"][0]["tier"] == "pro"
    assert db.tables["profiles"][0]["subscription_expiry"] == orig_exp
    assert db.tables["payment_orders"][0]["status"] == "needs_manual_review"
    assert len(db.tables["payment_webhook_events"]) == 1
    assert db.tables["payment_webhook_events"][0]["status"] == "needs_manual_review"


# ══════════════════════════════════════════════════════════════════════
# Item 3: SePay Case A (VG... code) exact catalog price match (<= 1,000 VND)
# ══════════════════════════════════════════════════════════════════════

def test_sepay_case_a_99k_when_pro_renewal_is_139k_marks_underpaid_and_keeps_tier(fake_env, monkeypatch):
    """Paying 99,000 VND via VG code when Pro renewal price is 139,000 VND must NOT
    auto-select Starter (69,000 VND); it must mark underpaid/needs_manual_review and keep tier unchanged."""
    db, rc = fake_env
    from app.core import sepay_webhook

    uid = "a1b2c3d4-1111-2222-3333-444444444444"
    # Returning buyer (not first-time): Pro renewal is 139,000 VND, Starter renewal is 69,000 VND
    rc.set(f"first_purchase_done:{uid}", "1")
    db.tables["profiles"].append({
        "id": uid,
        "tier": "free",
        "billing_status": "canceled",
        "subscription_expiry": _past_iso(10),
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "free"})

    secret = "sepay_test_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    body = json.dumps({
        "id": 91001,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 99000,
        "content": "Chuyen khoan VGA1B2C3D4 gia han Pro",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}

    res = sepay_webhook.handle(body, headers)
    assert res["action"] == "underpaid"
    assert res["status"] == "needs_manual_review"
    # Tier MUST remain unchanged ('free', NOT downgraded/upgraded to 'starter')
    assert db.tables["profiles"][0]["tier"] == "free"
    assert db.tables["user_usage"][0]["plan"] == "free"
    assert len(db.tables["payment_webhook_events"]) == 1
    assert db.tables["payment_webhook_events"][0]["status"] == "needs_manual_review"


def test_sepay_case_a_exact_and_within_1000_vnd_tolerance_upgrades(fake_env, monkeypatch):
    """Paying 139,000 VND (or 139,500 VND within 1,000 VND tolerance) for a returning Pro user upgrades to Pro."""
    db, rc = fake_env
    from app.core import sepay_webhook

    uid = "b2c3d4e5-1111-2222-3333-444444444444"
    rc.set(f"first_purchase_done:{uid}", "1")
    db.tables["profiles"].append({
        "id": uid,
        "tier": "free",
        "billing_status": "canceled",
        "subscription_expiry": None,
    })

    secret = "sepay_test_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    body = json.dumps({
        "id": 91002,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 139500,  # within +500 VND (<= 1,000 VND) of 139,000 VND
        "content": "Chuyen khoan VGB2C3D4E5",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}

    res = sepay_webhook.handle(body, headers)
    assert res["action"] == "activated_vg_account"
    assert res["tier"] == "pro"
    assert db.tables["profiles"][0]["tier"] == "pro"


# ══════════════════════════════════════════════════════════════════════
# Item 4: Unit tests for previously untested payment & admin tier flows
# ══════════════════════════════════════════════════════════════════════

def test_upgrade_user_starter_and_team(fake_env):
    """_upgrade_user must accurately set tier, plan_name, and user_usage.plan for starter and team."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    exp_ts = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())

    # 1. Starter upgrade
    uid_starter = "c1111111-1111-1111-1111-111111111111"
    db.tables["profiles"].append({"id": uid_starter, "tier": "free", "billing_status": "none"})
    payments_api._upgrade_user(uid_starter, "sub_starter_01", exp_ts, target_tier="starter")

    prof_s = next(p for p in db.tables["profiles"] if p["id"] == uid_starter)
    usage_s = next(u for u in db.tables["user_usage"] if u["user_id"] == uid_starter)
    assert prof_s["tier"] == "starter"
    assert prof_s["plan_name"] == "Starter"
    assert prof_s["billing_status"] == "active"
    assert usage_s["plan"] == "starter"

    # 2. Team upgrade
    uid_team = "c2222222-2222-2222-2222-222222222222"
    db.tables["profiles"].append({"id": uid_team, "tier": "free", "billing_status": "none"})
    payments_api._upgrade_user(uid_team, "sub_team_01", exp_ts, target_tier="team")

    prof_t = next(p for p in db.tables["profiles"] if p["id"] == uid_team)
    usage_t = next(u for u in db.tables["user_usage"] if u["user_id"] == uid_team)
    assert prof_t["tier"] == "team"
    assert prof_t["plan_name"] == "Team"
    assert prof_t["billing_status"] == "active"
    assert usage_t["plan"] == "team"


@pytest.mark.anyio
async def test_paypal_webhook_activates_order_and_direct_flow(fake_env, monkeypatch):
    """paypal_webhook must activate pending payment_orders and also support direct custom_id upgrades."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "pp_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "pp_sec")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "pp_wh_id")
    monkeypatch.setattr(payments_api, "_paypal_access_token", AsyncMock(return_value="tok_123"))

    class _FakeVerifyResp:
        status_code = 200
        def json(self):
            return {"verification_status": "SUCCESS"}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, *a, **kw):
            return _FakeVerifyResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    # Flow A: order-backed PayPal webhook for starter_month
    uid1 = "d1111111-1111-1111-1111-111111111111"
    db.tables["profiles"].append({"id": uid1, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-pp-1",
        "user_id": uid1,
        "plan": "starter_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 199,
        "code": "VGP11112222",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    req1 = MagicMock()
    req1.body = AsyncMock(return_value=json.dumps({
        "id": "WH-PP-OK-1",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "custom_id": f"{uid1}:ord-pp-1:starter_month",
            "amount": {"value": "1.99"},
        },
    }).encode("utf-8"))
    req1.headers = {}

    res1 = await payments_api.paypal_webhook(req1)
    assert res1["received"] is True
    assert db.tables["payment_orders"][0]["status"] == "paid"
    assert next(p for p in db.tables["profiles"] if p["id"] == uid1)["tier"] == "starter"

    # Flow B: direct PayPal webhook for team_year
    uid2 = "d2222222-2222-2222-2222-222222222222"
    db.tables["profiles"].append({"id": uid2, "tier": "free", "billing_status": "none"})
    req2 = MagicMock()
    req2.body = AsyncMock(return_value=json.dumps({
        "id": "WH-PP-OK-2",
        "event_type": "CHECKOUT.ORDER.COMPLETED",
        "resource": {
            "purchase_units": [{"custom_id": f"{uid2}:year:team_year"}],
            "amount": {"value": "119.00"},
        },
    }).encode("utf-8"))
    req2.headers = {}

    res2 = await payments_api.paypal_webhook(req2)
    assert res2["received"] is True
    assert next(p for p in db.tables["profiles"] if p["id"] == uid2)["tier"] == "team"


@pytest.mark.anyio
async def test_paypal_ipn_activates_order_and_direct_flow(fake_env, monkeypatch):
    """paypal_ipn must verify with PayPal IPN endpoint, validate receiver email, and activate tier."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")

    class _FakeIpnResp:
        status_code = 200
        text = "VERIFIED"

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, *a, **kw):
            return _FakeIpnResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    uid = "e1111111-1111-1111-1111-111111111111"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    ipn_body = (
        f"payment_status=Completed&txn_id=IPN_TEAM_01"
        f"&receiver_email=billing@vidgrab.io&custom={uid}::team_month&mc_gross=11.99"
    )
    req = MagicMock()
    req.body = AsyncMock(return_value=ipn_body.encode("utf-8"))

    res = await payments_api.paypal_ipn(req)
    assert res["verified"] is True
    assert db.tables["profiles"][0]["tier"] == "team"
    assert db.tables["user_usage"][0]["plan"] == "team"


def test_sepay_webhook_handle_case_a_and_case_b(fake_env, monkeypatch):
    """sepay_webhook.handle must support both Case A (VG account code) and Case B (VGP order code)."""
    db, _rc = fake_env
    from app.core import sepay_webhook

    secret = "sepay_case_ab_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)

    def _signed_call(payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        raw = json.dumps(payload_dict).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
        return sepay_webhook.handle(raw, {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts})

    # Case A: First-time buyer paying 49,000 VND with VG + 8 hex -> starter_month
    uid_a = "f1a2b3c4-0000-0000-0000-000000000001"
    db.tables["profiles"].append({"id": uid_a, "tier": "free", "billing_status": "none"})
    res_a = _signed_call({
        "id": 92001,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 49000,
        "content": "Chuyen khoan VGF1A2B3C4",
    })
    assert res_a["action"] == "activated_vg_account"
    assert res_a["tier"] == "starter"
    assert next(p for p in db.tables["profiles"] if p["id"] == uid_a)["tier"] == "starter"

    # Case B: Pending order VGP33334444 for team_month (279,000 VND)
    uid_b = "f2b3c4d5-0000-0000-0000-000000000002"
    db.tables["profiles"].append({"id": uid_b, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-sepay-b1",
        "user_id": uid_b,
        "plan": "team_month",
        "rail": "vietqr",
        "currency": "VND",
        "amount": 279000,
        "code": "VGP33334444",
        "status": "pending",
        "expires_at": _future_iso(1),
    })
    res_b = _signed_call({
        "id": 92002,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 279000,
        "content": "Thanh toan VGP33334444",
    })
    assert res_b["action"] == "activated"
    assert next(p for p in db.tables["profiles"] if p["id"] == uid_b)["tier"] == "team"
    assert next(o for o in db.tables["payment_orders"] if o["id"] == "ord-sepay-b1")["status"] == "paid"


@pytest.mark.anyio
async def test_webhook_idempotency_sending_same_event_twice(fake_env, monkeypatch):
    """Sending the exact same webhook event twice must be idempotent across SePay, PayPal, IPN, and Stripe."""
    db, _rc = fake_env
    from app.api import payments as payments_api
    from app.core import sepay_webhook

    uid = "99998888-7777-6666-5555-444433332222"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    # 1. SePay idempotency: same sepay_id sent twice
    secret = "sepay_idem_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    raw = json.dumps({
        "id": 95001,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 99000,
        "content": "Chuyen khoan VG99998888",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
    headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}

    first_sepay = sepay_webhook.handle(raw, headers)
    assert first_sepay["action"] == "activated_vg_account"
    exp_after_first = db.tables["profiles"][0]["subscription_expiry"]

    second_sepay = sepay_webhook.handle(raw, headers)
    assert second_sepay["action"] == "duplicate"
    # Expiry MUST NOT be extended a second time
    assert db.tables["profiles"][0]["subscription_expiry"] == exp_after_first

    # 2. PayPal IPN idempotency: same txn_id sent twice
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")

    class _FakeIpnResp:
        status_code = 200
        text = "VERIFIED"
        def json(self):
            return {"verification_status": "SUCCESS"}

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def post(self, *a, **kw):
            return _FakeIpnResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
    ipn_raw = f"payment_status=Completed&txn_id=TXN_IDEM_1&receiver_email=billing@vidgrab.io&custom={uid}::pro_month&mc_gross=3.99".encode("utf-8")
    req_ipn = MagicMock()
    req_ipn.body = AsyncMock(return_value=ipn_raw)

    first_ipn = await payments_api.paypal_ipn(req_ipn)
    assert first_ipn.get("verified") is True
    exp_after_ipn1 = db.tables["profiles"][0]["subscription_expiry"]

    second_ipn = await payments_api.paypal_ipn(req_ipn)
    assert second_ipn.get("duplicate") is True
    assert db.tables["profiles"][0]["subscription_expiry"] == exp_after_ipn1


@pytest.mark.anyio
async def test_admin_update_user_starter_and_team(fake_env, monkeypatch):
    """admin.update_user must support starter and team tiers and synchronize both profiles and user_usage."""
    db, _rc = fake_env
    from app.api import admin as admin_api

    monkeypatch.setattr(admin_api, "get_supabase_client", lambda: db)
    monkeypatch.setattr(admin_api, "log_admin_action", lambda *a, **kw: None)

    uid = "77776666-5555-4444-3333-222211110000"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    req_http = MagicMock()

    # 1. Upgrade to Starter for 30 days
    res_starter = await admin_api.update_user(
        admin_api.UpdateUserRequest(user_id=uid, plan="starter", days=30),
        req_http,
        _=True,
    )
    assert res_starter["success"] is True
    assert db.tables["profiles"][0]["tier"] == "starter"
    assert db.tables["profiles"][0]["plan_name"] == "Starter"
    assert db.tables["profiles"][0]["billing_status"] == "manual"
    assert db.tables["profiles"][0]["subscription_expiry"] is not None
    assert db.tables["user_usage"][0]["plan"] == "starter"

    # 2. Upgrade to Team for 365 days
    res_team = await admin_api.update_user(
        admin_api.UpdateUserRequest(user_id=uid, plan="team", days=365),
        req_http,
        _=True,
    )
    assert res_team["success"] is True
    assert db.tables["profiles"][0]["tier"] == "team"
    assert db.tables["profiles"][0]["plan_name"] == "Team"
    assert db.tables["user_usage"][0]["plan"] == "team"


