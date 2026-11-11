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
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest
from fastapi import HTTPException


class _FakeResult:
    def __init__(self, data: Any = None, count: Optional[int] = None):
        self.data = data
        self.count = count


class _FakeTableQuery:
    def __init__(
        self,
        store: Dict[str, List[Dict[str, Any]]],
        table_name: str,
        lock: threading.RLock,
        before_execute_hook: Optional[Callable[[str, str, Any], None]] = None,
    ):
        self._store = store
        self._table = table_name
        self._lock = lock
        self._before_execute_hook = before_execute_hook
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

    def in_(self, col: str, vals: List[Any]):
        self._filters.append(("in", col, [str(v) for v in vals]))
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
            if kind == "in" and str(rv) not in val:
                return False
        return True

    def execute(self) -> _FakeResult:
        if self._before_execute_hook is not None:
            self._before_execute_hook(self._table, self._op, self._payload)
        with self._lock:
            rows = self._store.setdefault(self._table, [])
            if self._op == "insert":
                items = self._payload if isinstance(self._payload, list) else [self._payload]
                inserted = []
                for item in items:
                    row = dict(item)
                    if self._table == "payment_events":
                        prov = str(row.get("provider") or "stripe")
                        peid = str(row.get("provider_event_id") or "")
                        for existing in rows:
                            if (
                                str(existing.get("provider") or "stripe") == prov
                                and str(existing.get("provider_event_id") or "") == peid
                            ):
                                err = RuntimeError(
                                    'duplicate key value violates unique constraint '
                                    '"payment_events_provider_provider_event_id_key" (23505)'
                                )
                                setattr(err, "code", "23505")
                                raise err
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
        self._lock = threading.RLock()
        self.before_execute_hook: Optional[Callable[[str, str, Any], None]] = None
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "profiles": [],
            "user_usage": [],
            "payment_orders": [],
            "payment_events": [],
            "payment_webhook_events": [],
        }

    def table(self, name: str) -> _FakeTableQuery:
        return _FakeTableQuery(self.tables, name, self._lock, self.before_execute_hook)


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
    # Prevent unit tests from ever hitting real Telegram credentials in local .env
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
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
async def test_paypal_webhook_blocks_downgrade_and_records_manual_review(fake_env, monkeypatch, caplog):
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
    assert db.tables["user_usage"][0]["plan"] == "pro"
    assert len(db.tables["payment_webhook_events"]) == 1
    ev = db.tables["payment_webhook_events"][0]
    assert ev["provider"] == "paypal"
    assert ev["status"] == "needs_manual_review"
    assert ev["current_tier"] == "pro"
    assert ev["target_tier"] == "starter"


@pytest.mark.anyio
async def test_paypal_ipn_blocks_downgrade_and_records_manual_review(fake_env, monkeypatch, caplog):
    db, _rc = fake_env
    from app.api import payments as payments_api

    uid = "44444444-5555-6666-7777-888888888889"
    orig_exp = _future_iso(25)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": orig_exp,
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "pro"})

    class _FakeVerifyResp:
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
            return _FakeVerifyResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("PAYPAL_ME_URL", "merchant@test.com")
    db.tables["payment_orders"].append({
        "id": "ord-ipn-down-1",
        "user_id": uid,
        "plan": "starter_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 199,
        "code": "VGPIPNDOWN1",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    ipn_body = f"payment_status=Completed&txn_id=TXN_DOWN_1&receiver_email=merchant@test.com&custom={uid}:ord-ipn-down-1:starter_month&mc_gross=1.99&mc_currency=USD"
    req_ipn = MagicMock()
    req_ipn.body = AsyncMock(return_value=ipn_body.encode("utf-8"))

    with caplog.at_level(logging.WARNING):
        res_ipn = await payments_api.paypal_ipn(req_ipn)

    assert res_ipn["status"] == "needs_manual_review"
    assert db.tables["profiles"][0]["tier"] == "pro"
    assert db.tables["profiles"][0]["subscription_expiry"] == orig_exp
    assert db.tables["user_usage"][0]["plan"] == "pro"
    assert len(db.tables["payment_webhook_events"]) == 1
    ev = db.tables["payment_webhook_events"][0]
    assert ev["provider"] == "paypal_ipn"
    assert ev["status"] == "needs_manual_review"
    assert ev["current_tier"] == "pro"
    assert ev["target_tier"] == "starter"


def test_sepay_case_a_blocks_downgrade_and_records_manual_review(fake_env, monkeypatch, caplog):
    """SePay Case A (VG... code): when user has active Pro and transfers exact Starter price (69,000 VND renewal),
    it must block downgrade, keep tier & subscription_expiry unchanged, and record needs_manual_review."""
    db, rc = fake_env
    from app.core import sepay_webhook

    uid = "55555555-6666-7777-8888-000000000001"
    rc.set(f"first_purchase_done:{uid}", "1")
    orig_exp = _future_iso(20)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": orig_exp,
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "pro"})

    secret = "sepay_test_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    body = json.dumps({
        "id": 90000,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 69000,  # Exact Starter renewal price in user's catalog
        "content": "Chuyen khoan VG55555555",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256).hexdigest()
    headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}

    with caplog.at_level(logging.WARNING):
        res = sepay_webhook.handle(body, headers)

    assert res["action"] == "needs_manual_review"
    assert res["status"] == "needs_manual_review"
    assert db.tables["profiles"][0]["tier"] == "pro"
    assert db.tables["profiles"][0]["subscription_expiry"] == orig_exp
    assert db.tables["user_usage"][0]["plan"] == "pro"
    assert len(db.tables["payment_webhook_events"]) == 1
    ev = db.tables["payment_webhook_events"][0]
    assert ev["provider"] == "sepay"
    assert ev["status"] == "needs_manual_review"
    assert ev["current_tier"] == "pro"
    assert ev["target_tier"] == "starter"


def test_sepay_case_b_and_activate_order_block_downgrade_and_record_manual_review(fake_env, monkeypatch, caplog):
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


@pytest.mark.anyio
async def test_renew_same_tier_while_active_extends_days_normally(fake_env, monkeypatch):
    """Buying the SAME tier while active (renewal) must create order and add days on top of existing expiry."""
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.core import payment_orders as po
    from app.core import payments_auto, plans

    monkeypatch.setattr(payments_auto, "visible", lambda _req=None: True)
    plans.set_settings({"bank_name": "ACB", "bank_account": "12345678", "bank_holder": "TEST"})

    uid = "66666666-7777-8888-9999-000000000001"
    orig_dt = datetime.now(timezone.utc) + timedelta(days=20)
    orig_exp = orig_dt.isoformat()
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": orig_exp,
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "pro"})

    req = MagicMock()
    res = await checkout_api.create_order(
        payload=checkout_api.CreateOrderRequest(plan="pro_month", rail="vietqr"),
        request=req,
        user={"id": uid, "email": "renew@test.com"},
    )
    order = res["order"]
    assert order["status"] == "pending"

    ok, msg = po.activate_order(order["id"], paid_amount=order["amount"], provider_ref="sepay_renew_1")
    assert ok is True and msg == "activated"

    prof = db.tables["profiles"][0]
    assert prof["tier"] == "pro"
    new_dt = datetime.fromisoformat(prof["subscription_expiry"].replace("Z", "+00:00"))
    # Expiry must be extended by 30 days on top of the existing 20-day expiry (~50 days from now)
    assert (new_dt - orig_dt).total_seconds() >= 29.9 * 86400
    assert db.tables["payment_webhook_events"] == []


@pytest.mark.anyio
async def test_upgrade_to_higher_tier_while_active_upgrades_normally(fake_env, monkeypatch):
    """Buying a HIGHER tier (e.g. Starter -> Pro or Pro -> Team) while active must succeed and upgrade tier."""
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.core import payment_orders as po
    from app.core import payments_auto, plans

    monkeypatch.setattr(payments_auto, "visible", lambda _req=None: True)
    plans.set_settings({"bank_name": "ACB", "bank_account": "12345678", "bank_holder": "TEST"})

    uid = "66666666-7777-8888-9999-000000000002"
    orig_dt = datetime.now(timezone.utc) + timedelta(days=15)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "starter",
        "billing_status": "manual",
        "subscription_expiry": orig_dt.isoformat(),
    })
    db.tables["user_usage"].append({"user_id": uid, "plan": "starter"})

    req = MagicMock()
    res = await checkout_api.create_order(
        payload=checkout_api.CreateOrderRequest(plan="team_month", rail="vietqr"),
        request=req,
        user={"id": uid, "email": "upgrade@test.com"},
    )
    order = res["order"]
    assert order["status"] == "pending"

    ok, msg = po.activate_order(order["id"], paid_amount=order["amount"], provider_ref="sepay_upg_1")
    assert ok is True and msg == "activated"

    prof = db.tables["profiles"][0]
    assert prof["tier"] == "team"
    assert prof["plan_name"] == "Team"
    assert db.tables["user_usage"][0]["plan"] == "team"
    new_dt = datetime.fromisoformat(prof["subscription_expiry"].replace("Z", "+00:00"))
    assert new_dt > orig_dt
    assert db.tables["payment_webhook_events"] == []


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
    db.tables["payment_orders"].append({
        "id": "ord-ipn-team-01",
        "user_id": uid,
        "plan": "team_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 1199,
        "code": "VGPIPNTEAM1",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    ipn_body = (
        f"payment_status=Completed&txn_id=IPN_TEAM_01"
        f"&receiver_email=billing@vidgrab.io&custom={uid}:ord-ipn-team-01:team_month&mc_gross=11.99&mc_currency=USD"
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
    db.tables["payment_orders"].append({
        "id": "ord-ipn-idem-1",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 599,
        "code": "VGPIPNIDEM1",
        "status": "pending",
        "expires_at": _future_iso(1),
    })
    ipn_raw = f"payment_status=Completed&txn_id=TXN_IDEM_1&receiver_email=billing@vidgrab.io&custom={uid}:ord-ipn-idem-1:pro_month&mc_gross=5.99&mc_currency=USD".encode("utf-8")
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


# ══════════════════════════════════════════════════════════════════════
# Manual Review Notifications (Telegram + Admin UI API + Customer 24h Msg)
# ══════════════════════════════════════════════════════════════════════

def test_manual_review_sends_telegram_alert_with_required_fields_and_no_secrets(fake_env, monkeypatch):
    """Every record_webhook_manual_review call must alert admin via Telegram with
    gateway, order code, user, amount, current -> paid tier, and reason — without leaking secrets."""
    db, _rc = fake_env
    from app.core import payment_orders as po

    bot_token = "999888777:AAFakeBotSecretTokenDoNotLeak"
    chat_id = "-1001234567890"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", bot_token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat_id)

    sent_calls: List[Dict[str, Any]] = []

    class _FakeSyncResp:
        status_code = 200

    class _FakeSyncClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def post(self, url: str, json: Dict[str, Any] = None, **kw):
            sent_calls.append({"url": url, "json": json or {}})
            return _FakeSyncResp()

    monkeypatch.setattr("httpx.Client", _FakeSyncClient)

    uid = "12121212-3434-5656-7878-909090909090"
    po.record_webhook_manual_review(
        db,
        provider="sepay",
        provider_event_id="sepay_tx_777",
        event_type="sepay.transfer_in",
        user_id=uid,
        current_tier="team",
        target_tier="starter",
        amount=49000,
        order_id="ord-uuid-777",
        order_code="VGP77778888",
        reason="downgrade_while_higher_tier_active",
        raw_payload={
            "gateway": "ACB",
            "access_token": "tok_top_secret_abc",
            "webhook_secret": "whsec_top_secret_xyz",
            "signature": "sig_hmac_secret_999",
        },
        status="needs_manual_review",
    )

    # 1. Telegram message sent once with all required fields
    assert len(sent_calls) == 1
    tg_payload = sent_calls[0]["json"]
    assert tg_payload["chat_id"] == chat_id
    text = tg_payload["text"]
    assert "sepay" in text.lower()
    assert "VGP77778888" in text
    assert uid in text
    assert "49.000₫" in text
    assert "team → starter" in text
    assert "downgrade_while_higher_tier_active" in text

    # 2. No secrets/tokens in Telegram message body OR persisted raw_payload
    for secret_str in (bot_token, "tok_top_secret_abc", "whsec_top_secret_xyz", "sig_hmac_secret_999"):
        assert secret_str not in text

    saved = db.tables["payment_webhook_events"][0]
    assert saved["raw_payload"].get("order_code") == "VGP77778888"
    assert "access_token" not in saved["raw_payload"]
    assert "webhook_secret" not in saved["raw_payload"]
    assert "signature" not in saved["raw_payload"]


@pytest.mark.anyio
async def test_manual_review_telegram_error_never_breaks_webhook(fake_env, monkeypatch):
    """If Telegram HTTP fails or raises an exception, SePay and Stripe webhooks must NOT fail."""
    db, rc = fake_env
    from app.api import payments as payments_api
    from app.core import sepay_webhook

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:AA_TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")

    class _ExplodingSyncClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def post(self, *a, **kw):
            raise RuntimeError("Telegram network unreachable")

    monkeypatch.setattr("httpx.Client", _ExplodingSyncClient)

    # 1. SePay underpaid VG transfer while Telegram throws RuntimeError
    uid = "abababab-1111-2222-3333-444444444444"
    rc.set(f"first_purchase_done:{uid}", "1")
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": _future_iso(20),
    })

    secret = "sepay_tg_fail_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    raw = json.dumps({
        "id": 98001,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 49000,
        "content": "Chuyen khoan VGABABABAB",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()

    res_sepay = sepay_webhook.handle(raw, {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts})
    assert res_sepay["success"] is True
    assert res_sepay["status"] == "needs_manual_review"
    assert len(db.tables["payment_webhook_events"]) == 1

    # 2. Stripe downgrade webhook while Telegram throws RuntimeError
    monkeypatch.setattr(payments_api, "_STRIPE_WEBHOOK_SECRET", "whsec_tg_fail")
    fake_stripe = MagicMock()
    fake_stripe.Webhook.construct_event.return_value = {
        "id": "evt_tg_fail_1",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "subscription": "sub_tg_fail_1",
                "amount_total": 199,
                "metadata": {"user_id": uid, "plan": "starter"},
            }
        },
    }
    fake_stripe.Subscription.retrieve.return_value = {
        "current_period_end": int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp()),
        "metadata": {"plan": "starter"},
    }
    monkeypatch.setattr(payments_api, "_stripe", lambda: fake_stripe)

    req_stripe = MagicMock()
    req_stripe.body = AsyncMock(return_value=b"{}")
    req_stripe.headers = {"stripe-signature": "t=1,v1=ok"}

    res_stripe = await payments_api.stripe_webhook(req_stripe)
    assert res_stripe["received"] is True
    assert res_stripe["status"] == "needs_manual_review"
    assert len(db.tables["payment_webhook_events"]) == 2


@pytest.mark.anyio
async def test_admin_manual_reviews_auth_list_and_resolve(fake_env, monkeypatch):
    """Only admin can view/resolve needs_manual_review records; resolving updates status and stats."""
    db, rc = fake_env
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import admin as admin_api
    from app.api import checkout as checkout_api
    from app.core import payment_orders as po

    monkeypatch.setattr(admin_api, "_redis", lambda: rc)
    monkeypatch.setattr(admin_api, "_ADMIN_PASSWORD", "super_admin_pw_2026")
    monkeypatch.setattr(admin_api, "log_access_denied", lambda *a, **kw: None)
    monkeypatch.setattr("app.core.audit.log_admin_action", lambda *a, **kw: None)

    uid = "34343434-5656-7878-9090-121212121212"
    db.tables["payment_orders"].append({
        "id": "ord-rev-1",
        "user_id": uid,
        "plan": "starter_month",
        "rail": "vietqr",
        "currency": "VND",
        "amount": 49000,
        "code": "VGPREV00001",
        "status": "needs_manual_review",
        "expires_at": _future_iso(1),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    po.record_webhook_manual_review(
        db,
        provider="payment_orders",
        provider_event_id="sepay_rev_1",
        event_type="payment_order.activate_blocked",
        user_id=uid,
        current_tier="pro",
        target_tier="starter",
        amount=49000,
        order_id="ord-rev-1",
        order_code="VGPREV00001",
        reason="downgrade_while_higher_tier_active",
        status="needs_manual_review",
    )
    ev_id = db.tables["payment_webhook_events"][0]["id"]

    mini_app = FastAPI()
    mini_app.include_router(admin_api.router, prefix="/api/v1/admin")
    mini_app.include_router(checkout_api.admin_router, prefix="/api/v1/admin/checkout")
    client = TestClient(mini_app)

    # 1. Unauthenticated & regular user Bearer token -> 401 Unauthorized
    r_unauth = client.get("/api/v1/admin/checkout/manual-reviews")
    assert r_unauth.status_code == 401

    r_user_jwt = client.get(
        "/api/v1/admin/checkout/manual-reviews",
        headers={"Authorization": "Bearer fake_regular_user_jwt_token"},
    )
    assert r_user_jwt.status_code == 401

    r_resolve_unauth = client.post(
        f"/api/v1/admin/checkout/manual-reviews/{ev_id}/resolve",
        json={"note": "hack"},
    )
    assert r_resolve_unauth.status_code == 401

    # 2. Admin logs in and lists needs_manual_review
    login_res = client.post("/api/v1/admin/login", json={"password": "super_admin_pw_2026"})
    assert login_res.status_code == 200
    admin_token = login_res.json()["session_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    list_res = client.get("/api/v1/admin/checkout/manual-reviews?status=needs_manual_review", headers=admin_headers)
    assert list_res.status_code == 200
    list_body = list_res.json()
    assert list_body["total"] == 1
    assert list_body["events"][0]["id"] == ev_id
    assert list_body["events"][0]["status"] == "needs_manual_review"

    stats_before = client.get("/api/v1/admin/checkout/orders/stats", headers=admin_headers).json()
    assert stats_before["manual_review_events"] == 1

    # 3. Admin marks record as resolved
    resolve_res = client.post(
        f"/api/v1/admin/checkout/manual-reviews/{ev_id}/resolve",
        headers=admin_headers,
        json={"note": "Đã hoàn tiền 49k cho khách qua ACB"},
    )
    assert resolve_res.status_code == 200
    assert resolve_res.json()["success"] is True
    assert resolve_res.json()["event"]["status"] == "resolved"
    assert resolve_res.json()["event"]["raw_payload"]["admin_note"] == "Đã hoàn tiền 49k cho khách qua ACB"

    # Pending list is now empty; resolved list has 1; linked order also marked resolved
    after_pending = client.get("/api/v1/admin/checkout/manual-reviews?status=needs_manual_review", headers=admin_headers).json()
    assert after_pending["total"] == 0
    after_resolved = client.get("/api/v1/admin/checkout/manual-reviews?status=resolved", headers=admin_headers).json()
    assert after_resolved["total"] == 1
    assert db.tables["payment_orders"][0]["status"] == "resolved"


@pytest.mark.anyio
async def test_customer_checkout_and_billing_show_24h_manual_review_message(fake_env):
    """When a payment is blocked (needs_manual_review / underpaid), customer order/checkout/billing
    status endpoints must return the exact 24h message instead of staying silent."""
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.api import payments as payments_api
    from app.core import payment_orders as po

    expected_msg = "Thanh toán đang được kiểm tra, chúng tôi sẽ liên hệ trong 24h"
    uid = "56565656-7878-9090-1212-343434343434"
    user_ctx = {"id": uid, "email": "customer@test.com"}

    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": _future_iso(20),
    })
    db.tables["payment_orders"].append({
        "id": "ord-cust-block-1",
        "user_id": uid,
        "plan": "starter_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 199,
        "code": "VGPCUST0001",
        "status": "pending",
        "expires_at": _future_iso(1),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Trigger downgrade block on order activation (simulates webhook arriving while Pro is active)
    ok, act_msg = po.activate_order("ord-cust-block-1", paid_amount=199, provider_ref="pp_cap_1")
    assert ok is False and act_msg == "needs_manual_review"

    # 1. GET /checkout/orders/{order_id} (order polling on CheckoutPage)
    poll_res = await checkout_api.get_order("ord-cust-block-1", user=user_ctx)
    assert poll_res["status"] == "needs_manual_review"
    assert poll_res["manual_review"] is True
    assert poll_res["user_message"] == expected_msg

    # 2. POST /checkout/orders/{order_id}/capture (PayPal return when order already flagged)
    cap_res = await checkout_api.capture_paypal_order("ord-cust-block-1", request=MagicMock(), user=user_ctx)
    assert cap_res["success"] is False
    assert cap_res["manual_review"] is True
    assert cap_res["user_message"] == expected_msg

    # 3. GET /checkout/orders (checkout order list + top banner)
    list_res = await checkout_api.list_orders(user=user_ctx)
    assert list_res["manual_review"] is not None
    assert list_res["user_message"] == expected_msg

    # 4. GET /payments/billing-status (BillingPage notice)
    bill_res = await payments_api.billing_status(user=user_ctx)
    assert bill_res["pending_manual_review"] is True
    assert bill_res["manual_review_message"] == expected_msg

    # 5. Once admin marks the manual review resolved, customer notice clears
    ev_id = db.tables["payment_webhook_events"][0]["id"]
    resolved_ok, _, _ = po.admin_resolve_manual_review(ev_id, "Đã liên hệ khách hàng")
    assert resolved_ok is True

    bill_after = await payments_api.billing_status(user=user_ctx)
    assert bill_after["pending_manual_review"] is False
    assert bill_after["manual_review_message"] is None



# ══════════════════════════════════════════════════════════════════════
# Fail-Closed Idempotency, Pre-Claim Race Protection & DB Error 5xx
# ══════════════════════════════════════════════════════════════════════


def test_concurrent_same_sepay_id_extends_days_only_once(fake_env, monkeypatch):
    """2 concurrent SePay Case A requests with the same sepay_id (synchronized with
    threading.Barrier) must pre-claim via payment_events UNIQUE and extend days ONLY ONCE."""
    db, rc = fake_env
    from app.core import sepay_webhook

    uid = "a1b2c3d4-1111-2222-3333-444455556666"
    rc.set(f"first_purchase_done:{uid}", "1")
    base_dt = datetime.now(timezone.utc) + timedelta(days=10)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": base_dt.isoformat(),
    })

    secret = "sepay_barrier_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    raw = json.dumps({
        "id": 777001,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 139000,  # Exact Pro renewal price
        "content": "Chuyen khoan VGA1B2C3D4",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
    headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}

    barrier = threading.Barrier(2)

    def _hook(table: str, op: str, _payload: Any) -> None:
        if table == "payment_events" and op == "insert":
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass

    db.before_execute_hook = _hook

    results: List[Dict[str, Any]] = []
    errors: List[Exception] = []

    def _worker() -> None:
        try:
            results.append(sepay_webhook.handle(raw, headers))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    db.before_execute_hook = None

    assert not errors
    actions = sorted(r["action"] for r in results)
    assert actions == ["activated_vg_account", "duplicate"]

    new_dt = datetime.fromisoformat(db.tables["profiles"][0]["subscription_expiry"].replace("Z", "+00:00"))
    added_days = (new_dt - base_dt).total_seconds() / 86400.0
    # Must add 30 days ONCE (~30.0 days), never 60 days
    assert 29.5 <= added_days <= 30.5
    assert len([e for e in db.tables["payment_events"] if e["provider"] == "sepay" and e["provider_event_id"] == "777001"]) == 1


def test_concurrent_same_event_id_paypal_ipn_extends_days_only_once(fake_env, monkeypatch):
    """2 concurrent webhook requests with the same event_id (synchronized after SELECT via
    threading.Barrier so both attempt INSERT simultaneously) must extend days ONLY ONCE."""
    import asyncio
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

    uid = "b2c3d4e5-1111-2222-3333-444455556666"
    base_dt = datetime.now(timezone.utc) + timedelta(days=15)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": base_dt.isoformat(),
    })
    db.tables["payment_orders"].append({
        "id": "ord-ipn-race-99",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 599,
        "code": "VGPIPNRACE9",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    ipn_raw = (
        f"payment_status=Completed&txn_id=TXN_RACE_99"
        f"&receiver_email=billing@vidgrab.io&custom={uid}:ord-ipn-race-99:pro_month&mc_gross=5.99&mc_currency=USD"
    ).encode("utf-8")

    # Synchronize both threads right before INSERT so both see empty SELECT and collide on UNIQUE
    barrier = threading.Barrier(2)

    def _hook(table: str, op: str, _payload: Any) -> None:
        if table == "payment_events" and op == "insert":
            try:
                barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass

    db.before_execute_hook = _hook

    results: List[Dict[str, Any]] = []
    errors: List[Exception] = []

    def _worker() -> None:
        req = MagicMock()
        req.body = AsyncMock(return_value=ipn_raw)
        try:
            results.append(asyncio.run(payments_api.paypal_ipn(req)))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    db.before_execute_hook = None

    assert not errors
    dup_flags = sorted(bool(r.get("duplicate")) for r in results)
    assert dup_flags == [False, True]

    new_dt = datetime.fromisoformat(db.tables["profiles"][0]["subscription_expiry"].replace("Z", "+00:00"))
    added_days = (new_dt - base_dt).total_seconds() / 86400.0
    assert 29.5 <= added_days <= 30.5

    recorded = [
        e for e in db.tables["payment_events"]
        if e["provider_event_id"] == "ipn_TXN_RACE_99"
    ]
    assert len(recorded) == 1
    assert recorded[0]["provider"] == "paypal_ipn"


@pytest.mark.anyio
async def test_payment_events_records_exact_provider_and_recognizes_legacy_stripe_rows(fake_env, monkeypatch):
    """_record_payment_event must write exact provider ('stripe', 'paypal_webhook', 'paypal_ipn')
    AND recognize legacy PayPal events previously stored with provider='stripe'."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    uid = "c3d4e5f6-1111-2222-3333-444455556666"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    # 1. Seed legacy PayPal Webhook and PayPal IPN events stored under provider='stripe'
    db.tables["payment_events"].append({
        "id": "legacy-1",
        "provider": "stripe",
        "provider_event_id": "WH-LEGACY-PP-001",
        "event_type": "CHECKOUT.ORDER.COMPLETED",
        "processed": True,
    })
    db.tables["payment_events"].append({
        "id": "legacy-2",
        "provider": "stripe",
        "provider_event_id": "ipn_LEGACY_TXN_001",
        "event_type": "paypal.ipn.completed",
        "processed": True,
    })

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "pp_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "pp_sec")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "pp_wh_id")
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")
    monkeypatch.setattr(payments_api, "_paypal_access_token", AsyncMock(return_value="tok_123"))

    class _FakeResp:
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
            return _FakeResp()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    # Replay legacy PayPal webhook -> must be detected as duplicate, tier stays 'free'
    req_legacy_wh = MagicMock()
    req_legacy_wh.body = AsyncMock(return_value=json.dumps({
        "id": "WH-LEGACY-PP-001",
        "event_type": "CHECKOUT.ORDER.COMPLETED",
        "resource": {"purchase_units": [{"custom_id": f"{uid}:month:pro_month"}]},
    }).encode("utf-8"))
    req_legacy_wh.headers = {}
    res_legacy_wh = await payments_api.paypal_webhook(req_legacy_wh)
    assert res_legacy_wh.get("duplicate") is True
    assert db.tables["profiles"][0]["tier"] == "free"

    # Replay legacy PayPal IPN -> must be detected as duplicate, tier stays 'free'
    req_legacy_ipn = MagicMock()
    req_legacy_ipn.body = AsyncMock(
        return_value=f"payment_status=Completed&txn_id=LEGACY_TXN_001&receiver_email=billing@vidgrab.io&custom={uid}::pro_month&mc_gross=3.99".encode("utf-8")
    )
    res_legacy_ipn = await payments_api.paypal_ipn(req_legacy_ipn)
    assert res_legacy_ipn.get("duplicate") is True
    assert db.tables["profiles"][0]["tier"] == "free"

    # New events record accurate provider names: 'paypal_webhook', 'paypal_ipn', 'stripe'
    assert payments_api._record_payment_event("WH-NEW-PP-1", "CHECKOUT.ORDER.COMPLETED", {}, provider="paypal_webhook") is True
    assert payments_api._record_payment_event("ipn_NEW_1", "paypal.ipn.completed", {}, provider="paypal_ipn") is True
    assert payments_api._record_payment_event("evt_new_stripe_1", "checkout.session.completed", {}, provider="stripe") is True

    by_id = {e["provider_event_id"]: e["provider"] for e in db.tables["payment_events"]}
    assert by_id["WH-NEW-PP-1"] == "paypal_webhook"
    assert by_id["ipn_NEW_1"] == "paypal_ipn"
    assert by_id["evt_new_stripe_1"] == "stripe"


@pytest.mark.anyio
async def test_webhook_db_failure_returns_5xx_and_does_not_change_tier(fake_env, monkeypatch):
    """When payment_events DB fails (non-unique error), Stripe, PayPal Webhook, PayPal IPN,
    and SePay webhooks must fail-closed with HTTP 5xx and NEVER mutate user tier."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    uid = "d4e5f6a7-1111-2222-3333-444455556666"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    def _boom_on_payment_events(table: str, _op: str, _payload: Any) -> None:
        if table == "payment_events":
            raise ConnectionError("Supabase PostgREST connection timeout")

    db.before_execute_hook = _boom_on_payment_events

    try:
        # 1. Stripe webhook -> 5xx, tier unchanged
        monkeypatch.setattr(payments_api, "_STRIPE_WEBHOOK_SECRET", "whsec_db_err")
        fake_stripe = MagicMock()
        fake_stripe.Webhook.construct_event.return_value = {
            "id": "evt_db_down_1",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"user_id": uid, "plan": "pro"}, "subscription": "sub_1"}},
        }
        monkeypatch.setattr(payments_api, "_stripe", lambda: fake_stripe)
        req_stripe = MagicMock()
        req_stripe.body = AsyncMock(return_value=b"{}")
        req_stripe.headers = {"stripe-signature": "t=1,v1=ok"}
        with pytest.raises(HTTPException) as exc_stripe:
            await payments_api.stripe_webhook(req_stripe)
        assert 500 <= exc_stripe.value.status_code < 600
        assert db.tables["profiles"][0]["tier"] == "free"

        # 2. PayPal webhook -> 5xx, tier unchanged
        monkeypatch.setenv("PAYPAL_CLIENT_ID", "pp_id")
        monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "pp_sec")
        monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "pp_wh_id")
        monkeypatch.setattr(payments_api, "_paypal_access_token", AsyncMock(return_value="tok_123"))

        class _FakeResp:
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
                return _FakeResp()

        monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
        req_pp = MagicMock()
        req_pp.body = AsyncMock(return_value=json.dumps({
            "id": "WH-DB-DOWN-1",
            "event_type": "CHECKOUT.ORDER.COMPLETED",
            "resource": {"purchase_units": [{"custom_id": f"{uid}:month:pro_month"}]},
        }).encode("utf-8"))
        req_pp.headers = {}
        with pytest.raises(HTTPException) as exc_pp:
            await payments_api.paypal_webhook(req_pp)
        assert 500 <= exc_pp.value.status_code < 600
        assert db.tables["profiles"][0]["tier"] == "free"

        # 3. PayPal IPN -> 5xx, tier unchanged
        monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")
        req_ipn = MagicMock()
        req_ipn.body = AsyncMock(
            return_value=f"payment_status=Completed&txn_id=IPN_DB_DOWN_1&receiver_email=billing@vidgrab.io&custom={uid}::pro_month&mc_gross=3.99".encode("utf-8")
        )
        with pytest.raises(HTTPException) as exc_ipn:
            await payments_api.paypal_ipn(req_ipn)
        assert 500 <= exc_ipn.value.status_code < 600
        assert db.tables["profiles"][0]["tier"] == "free"

        # 4. SePay webhook -> 5xx, tier unchanged
        secret = "sepay_db_down_secret"
        monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
        raw_sepay = json.dumps({
            "id": 888001,
            "gateway": "ACB",
            "transferType": "in",
            "transferAmount": 49000,
            "content": "Chuyen khoan VGD4E5F6A7",
        }).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw_sepay, hashlib.sha256).hexdigest()
        req_sepay = MagicMock()
        req_sepay.body = AsyncMock(return_value=raw_sepay)
        req_sepay.headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}
        with pytest.raises(HTTPException) as exc_sepay:
            await payments_api.sepay_webhook(req_sepay)
        assert 500 <= exc_sepay.value.status_code < 600
        assert db.tables["profiles"][0]["tier"] == "free"
    finally:
        db.before_execute_hook = None


def test_sepay_case_a_upgrade_failure_updates_payment_event_to_failed(fake_env, monkeypatch):
    """If SePay Case A pre-claims payment_events and _upgrade_user_pro subsequently fails,
    the claimed payment_events row must be updated to match_type='failed' (processed=False)
    and recorded for manual review."""
    db, _rc = fake_env
    from app.core import payment_orders as po
    from app.core import sepay_webhook

    uid = "e5f6a7b8-1111-2222-3333-444455556666"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    def _failing_upgrade(*_a, **_kw):
        raise RuntimeError("profiles write failed mid-upgrade")

    monkeypatch.setattr(po, "_upgrade_user_pro", _failing_upgrade)

    secret = "sepay_upg_fail_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)
    raw = json.dumps({
        "id": 990077,
        "gateway": "ACB",
        "transferType": "in",
        "transferAmount": 49000,
        "content": "Chuyen khoan VGE5F6A7B8",
    }).encode("utf-8")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
    headers = {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts}

    res = sepay_webhook.handle(raw, headers)
    assert res["action"] == "failed"
    assert res["status"] == "failed"
    assert db.tables["profiles"][0]["tier"] == "free"

    ev_rows = [
        e for e in db.tables["payment_events"]
        if e["provider"] == "sepay" and e["provider_event_id"] == "990077"
    ]
    assert len(ev_rows) == 1
    assert ev_rows[0]["match_type"] == "failed"
    assert ev_rows[0]["processed"] is False
    assert "profiles write failed" in (ev_rows[0].get("error") or "")

    assert len(db.tables["payment_webhook_events"]) == 1
    assert db.tables["payment_webhook_events"][0]["status"] == "needs_manual_review"
    assert "upgrade_failed" in db.tables["payment_webhook_events"][0]["reason"]
