"""
Regression & security tests for `fix/payment-holes`:
1. PayPal IPN (`payments.py::paypal_ipn`):
   - Remove `elif user_id` direct upgrade branch; require internal server-created `order_id` owned by matching user.
   - Require configured receiver email (missing config -> 503); verify `mc_currency` matches order currency;
     parse `mc_gross` via `Decimal` -> cents; `< order["amount"]` -> underpaid + manual review + alert;
     remove `paid_cents or 499` fallback (`<= 0` or unparseable -> reject + manual review).
2. Legacy `POST /api/v1/payments/paypal-capture` & `checkout.py::capture_paypal_order`:
   - `/api/v1/payments/paypal-capture` returns 410 Gone; frontend `PricingPage.jsx` uses `/checkout/orders/{id}/capture`.
   - `capture_paypal_order`: parse amount via `Decimal` (no float drift, no default to `order["amount"]`);
     verify `currency_code`, `payee` merchant, `custom_id` == internal `order_id`; idempotency by capture ID via `payment_events` UNIQUE.
3. SePay webhook secret:
   - Remove `DEFAULT_SEPAY_SECRET` in `sepay_webhook.py` and hardcoded secret in `plans.py`.
   - Missing secret -> SePay webhook returns 503 + logs error.
4. Secondary payment safety fixes:
   - `activate_order`: expired/underpaid -> `payment_webhook_events` (`needs_manual_review`) + Telegram alert;
     SePay Case B missing/unknown code -> manual review + alert.
   - `activate_order`: `_upgrade_user_pro(..., raise_on_error=True)` failure after CAS `paid` -> `needs_manual_review` + alert.
   - `is_first_time_buyer`: DB is the source of truth; Redis is only a bounded-TTL cache.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

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
        self._limit: Optional[int] = None

    def select(self, *_args, **_kwargs):
        self._op = "select"
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

    from app.api import payments as payments_api
    from app.core import payment_orders as po
    from app.core import plans

    monkeypatch.setattr(po, "_db", lambda: db)
    monkeypatch.setattr(po, "ensure_schema", lambda force=False: True)
    monkeypatch.setattr(plans, "_r", lambda: rc)
    monkeypatch.setattr(payments_api, "_supabase", lambda: db)
    monkeypatch.setattr("app.core.database.get_service_client", lambda: db)
    monkeypatch.setattr("app.core.redis_client.get_redis", lambda: rc)
    monkeypatch.setattr("app.api.referrals.reward_referrer_on_pro_upgrade", lambda _uid: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    return db, rc


def _future_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _mock_verified_ipn(monkeypatch) -> None:
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


def _capture_telegram_alerts(monkeypatch) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "111222:AAFakeTestToken")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100555666")

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
            alerts.append({"url": url, "json": json or {}})
            return _FakeSyncResp()

    monkeypatch.setattr("httpx.Client", _FakeSyncClient)
    return alerts


# ══════════════════════════════════════════════════════════════════════
# 1. PayPal IPN (`backend/app/api/payments.py::paypal_ipn`)
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_paypal_ipn_rejects_custom_user_id_without_internal_order(fake_env, monkeypatch):
    """`elif user_id` must be removed: IPN with `custom=user_id::plan` (no server order_id) must NOT upgrade."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    _mock_verified_ipn(monkeypatch)
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")

    uid = "10000000-0000-0000-0000-000000000001"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    body = (
        f"payment_status=Completed&txn_id=IPN_NO_ORD_1"
        f"&receiver_email=billing@vidgrab.io&custom={uid}::pro_year&mc_gross=39.00&mc_currency=USD"
    ).encode("utf-8")
    req = MagicMock()
    req.body = AsyncMock(return_value=body)

    with pytest.raises(HTTPException) as exc:
        await payments_api.paypal_ipn(req)

    assert exc.value.status_code == 400
    assert db.tables["profiles"][0]["tier"] == "free"
    assert len(db.tables["payment_webhook_events"]) == 1
    assert db.tables["payment_webhook_events"][0]["status"] == "needs_manual_review"


@pytest.mark.anyio
async def test_paypal_ipn_rejects_order_belonging_to_different_user(fake_env, monkeypatch):
    """IPN must verify that the internal order_id in `custom` belongs to the matching user_id."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    _mock_verified_ipn(monkeypatch)
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")

    victim_uid = "10000000-0000-0000-0000-000000000002"
    attacker_uid = "10000000-0000-0000-0000-000000000003"
    db.tables["profiles"].append({"id": victim_uid, "tier": "free", "billing_status": "none"})
    db.tables["profiles"].append({"id": attacker_uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-victim-001",
        "user_id": victim_uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 399,
        "code": "VGPVICTIM01",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    body = (
        f"payment_status=Completed&txn_id=IPN_WRONG_OWNER_1"
        f"&receiver_email=billing@vidgrab.io&custom={attacker_uid}:ord-victim-001:pro_month"
        f"&mc_gross=3.99&mc_currency=USD"
    ).encode("utf-8")
    req = MagicMock()
    req.body = AsyncMock(return_value=body)

    with pytest.raises(HTTPException) as exc:
        await payments_api.paypal_ipn(req)

    assert exc.value.status_code == 400
    assert db.tables["payment_orders"][0]["status"] == "pending"
    assert all(p["tier"] == "free" for p in db.tables["profiles"])
    assert len(db.tables["payment_webhook_events"]) == 1


@pytest.mark.anyio
async def test_paypal_ipn_returns_503_when_receiver_email_missing_in_config(fake_env, monkeypatch):
    """Missing merchant receiver email configuration must fail-closed with HTTP 503 (never skip check)."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    _mock_verified_ipn(monkeypatch)
    for k in ("PAYPAL_ME_URL", "PAYPAL_RECEIVER_EMAIL", "PAYPAL_BUSINESS_EMAIL", "PAYPAL_MERCHANT_EMAIL"):
        monkeypatch.delenv(k, raising=False)

    uid = "10000000-0000-0000-0000-000000000004"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-ipn-503",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 399,
        "code": "VGPIPN50300",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    body = (
        f"payment_status=Completed&txn_id=IPN_NO_CFG_1"
        f"&receiver_email=attacker@evil.com&custom={uid}:ord-ipn-503:pro_month"
        f"&mc_gross=3.99&mc_currency=USD"
    ).encode("utf-8")
    req = MagicMock()
    req.body = AsyncMock(return_value=body)

    with pytest.raises(HTTPException) as exc:
        await payments_api.paypal_ipn(req)

    assert exc.value.status_code == 503
    assert db.tables["payment_orders"][0]["status"] == "pending"
    assert db.tables["profiles"][0]["tier"] == "free"


@pytest.mark.anyio
async def test_paypal_ipn_rejects_currency_mismatch(fake_env, monkeypatch):
    """`mc_currency` must strictly match `order["currency"]` (e.g. JPY/EUR on USD order -> rejected + manual review)."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    _mock_verified_ipn(monkeypatch)
    alerts = _capture_telegram_alerts(monkeypatch)
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")

    uid = "10000000-0000-0000-0000-000000000005"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-ipn-curr",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 399,
        "code": "VGPIPNCURR1",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    body = (
        f"payment_status=Completed&txn_id=IPN_EUR_1"
        f"&receiver_email=billing@vidgrab.io&custom={uid}:ord-ipn-curr:pro_month"
        f"&mc_gross=3.99&mc_currency=EUR"
    ).encode("utf-8")
    req = MagicMock()
    req.body = AsyncMock(return_value=body)

    with pytest.raises(HTTPException) as exc:
        await payments_api.paypal_ipn(req)

    assert exc.value.status_code == 400
    assert db.tables["payment_orders"][0]["status"] != "paid"
    assert db.tables["profiles"][0]["tier"] == "free"
    assert len(db.tables["payment_webhook_events"]) == 1
    assert len(alerts) == 1


@pytest.mark.anyio
async def test_paypal_ipn_decimal_underpaid_marks_underpaid_manual_review_and_alerts(fake_env, monkeypatch):
    """`mc_gross` parsed via Decimal -> cents; when `< order["amount"]`, marks underpaid + manual review + alert."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    _mock_verified_ipn(monkeypatch)
    alerts = _capture_telegram_alerts(monkeypatch)
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")

    uid = "10000000-0000-0000-0000-000000000006"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-ipn-under",
        "user_id": uid,
        "plan": "team_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 1999,
        "code": "VGPIPNUND01",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    body = (
        f"payment_status=Completed&txn_id=IPN_UNDER_1"
        f"&receiver_email=billing@vidgrab.io&custom={uid}:ord-ipn-under:team_month"
        f"&mc_gross=1.99&mc_currency=USD"
    ).encode("utf-8")
    req = MagicMock()
    req.body = AsyncMock(return_value=body)

    res = await payments_api.paypal_ipn(req)
    assert res["status"] == "needs_manual_review"
    assert db.tables["payment_orders"][0]["status"] == "underpaid"
    assert db.tables["payment_orders"][0]["paid_amount"] == 199
    assert db.tables["profiles"][0]["tier"] == "free"
    assert len(db.tables["payment_webhook_events"]) == 1
    assert db.tables["payment_webhook_events"][0]["status"] == "needs_manual_review"
    assert len(alerts) == 1


@pytest.mark.anyio
async def test_paypal_ipn_zero_or_unparseable_mc_gross_rejected_never_defaults_to_499(fake_env, monkeypatch):
    """Remove `paid_cents or 499`: `0.00` or unparseable `mc_gross` on a $3.99 (399c < 499c) order must NEVER activate."""
    db, _rc = fake_env
    from app.api import payments as payments_api

    _mock_verified_ipn(monkeypatch)
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")

    uid = "10000000-0000-0000-0000-000000000007"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-ipn-zero",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 399,
        "code": "VGPIPNZERO1",
        "status": "pending",
        "expires_at": _future_iso(1),
    })
    db.tables["payment_orders"].append({
        "id": "ord-ipn-nan",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 399,
        "code": "VGPIPNNAN01",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    for idx, (oid, bad_gross) in enumerate([("ord-ipn-zero", "0.00"), ("ord-ipn-nan", "abc")], start=1):
        req = MagicMock()
        req.body = AsyncMock(
            return_value=(
                f"payment_status=Completed&txn_id=IPN_BAD_AMT_{idx}"
                f"&receiver_email=billing@vidgrab.io&custom={uid}:{oid}:pro_month"
                f"&mc_gross={bad_gross}&mc_currency=USD"
            ).encode("utf-8")
        )
        with pytest.raises(HTTPException) as exc:
            await payments_api.paypal_ipn(req)
        assert exc.value.status_code == 400

    assert db.tables["profiles"][0]["tier"] == "free"
    assert all(o["status"] != "paid" for o in db.tables["payment_orders"])
    assert len(db.tables["payment_webhook_events"]) == 2


# ══════════════════════════════════════════════════════════════════════
# 2. Legacy `/payments/paypal-capture` (410) & `checkout.py::capture_paypal_order`
# ══════════════════════════════════════════════════════════════════════


def test_legacy_payments_paypal_capture_returns_410_and_frontend_migrated():
    """`POST /api/v1/payments/paypal-capture` must return 410 Gone, and `PricingPage.jsx` must use `/checkout/orders/{id}/capture`."""
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app

    client = TestClient(fastapi_app, raise_server_exceptions=False)
    res = client.post("/api/v1/payments/paypal-capture", json={"order_id": "PAYPAL_OLD_123"})
    assert res.status_code == 410

    pricing_jsx = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "PricingPage.jsx"
    ).read_text(encoding="utf-8")
    assert "/payments/paypal-capture" not in pricing_jsx
    assert "/checkout/orders/" in pricing_jsx and "/capture" in pricing_jsx


@pytest.mark.anyio
async def test_checkout_capture_parses_decimal_19_99_and_rejects_missing_or_zero_amount(fake_env, monkeypatch):
    """`Decimal('19.99') * 100` must equal 1999 (unlike `int(float('19.99') * 100) == 1998`),
    and missing/zero capture amounts must NEVER fall back to `order['amount']`."""
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.api import payments as payments_api

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "pp_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "pp_sec")
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")
    monkeypatch.setattr(payments_api, "_paypal_access_token", AsyncMock(return_value="tok_test"))

    uid = "20000000-0000-0000-0000-000000000001"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-dec-1999",
        "user_id": uid,
        "plan": "team_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 1999,
        "code": "VGPDEC01999",
        "status": "pending",
        "provider_ref": "PP_ORD_1999",
        "expires_at": _future_iso(1),
    })

    capture_payloads: Dict[str, Any] = {
        "PP_ORD_1999": {
            "id": "PP_ORD_1999",
            "status": "COMPLETED",
            "purchase_units": [{
                "custom_id": "ord-dec-1999",
                "payee": {"email_address": "billing@vidgrab.io"},
                "payments": {
                    "captures": [{
                        "id": "CAP_1999_OK",
                        "status": "COMPLETED",
                        "amount": {"currency_code": "USD", "value": "19.99"},
                    }]
                },
            }],
        },
        "PP_ORD_NOCAP": {
            "id": "PP_ORD_NOCAP",
            "status": "COMPLETED",
            "purchase_units": [{
                "custom_id": "ord-no-cap",
                "payee": {"email_address": "billing@vidgrab.io"},
                "payments": {"captures": []},
            }],
        },
    }

    class _FakeResp:
        def __init__(self, data: Dict[str, Any]):
            self.status_code = 200
            self._data = data

        def json(self):
            return self._data

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url: str, *a, **kw):
            pp_id = url.split("/v2/checkout/orders/")[1].split("/")[0]
            return _FakeResp(capture_payloads[pp_id])

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    # 1. Exact $19.99 -> 1999 cents -> activated (fails on old int(float("19.99") * 100) == 1998!)
    res_ok = await checkout_api.capture_paypal_order("ord-dec-1999", request=MagicMock(), user={"id": uid})
    assert res_ok["success"] is True
    assert db.tables["payment_orders"][0]["status"] == "paid"
    assert db.tables["payment_orders"][0]["paid_amount"] == 1999
    assert db.tables["profiles"][0]["tier"] == "team"

    # 2. Missing captures -> rejected (must NEVER default to order["amount"]!)
    uid2 = "20000000-0000-0000-0000-000000000002"
    db.tables["profiles"].append({"id": uid2, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-no-cap",
        "user_id": uid2,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 399,
        "code": "VGPNOCAP001",
        "status": "pending",
        "provider_ref": "PP_ORD_NOCAP",
        "expires_at": _future_iso(1),
    })

    with pytest.raises(HTTPException) as exc:
        await checkout_api.capture_paypal_order("ord-no-cap", request=MagicMock(), user={"id": uid2})
    assert exc.value.status_code == 400
    assert db.tables["payment_orders"][1]["status"] != "paid"
    assert db.tables["profiles"][1]["tier"] == "free"


@pytest.mark.anyio
async def test_checkout_capture_rejects_wrong_currency_payee_or_custom_id(fake_env, monkeypatch):
    """`capture_paypal_order` must reject mismatched `currency_code`, `payee`, or `custom_id != order_id`."""
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.api import payments as payments_api

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "pp_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "pp_sec")
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")
    monkeypatch.setattr(payments_api, "_paypal_access_token", AsyncMock(return_value="tok_test"))

    uid = "20000000-0000-0000-0000-000000000003"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})

    cases = [
        ("ord-bad-curr", "PP_BAD_CURR", "ord-bad-curr", "billing@vidgrab.io", "EUR", "CAP_BAD_1"),
        ("ord-bad-payee", "PP_BAD_PAYEE", "ord-bad-payee", "attacker@evil.com", "USD", "CAP_BAD_2"),
        ("ord-bad-cust", "PP_BAD_CUST", "some-other-order-id", "billing@vidgrab.io", "USD", "CAP_BAD_3"),
    ]
    payload_by_pp: Dict[str, Any] = {}
    for oid, pp_ref, cust_id, payee_email, curr, cap_id in cases:
        db.tables["payment_orders"].append({
            "id": oid,
            "user_id": uid,
            "plan": "pro_month",
            "rail": "paypal",
            "currency": "USD",
            "amount": 399,
            "code": f"VGP{cap_id[-8:]:0>8}",
            "status": "pending",
            "provider_ref": pp_ref,
            "expires_at": _future_iso(1),
        })
        payload_by_pp[pp_ref] = {
            "id": pp_ref,
            "status": "COMPLETED",
            "purchase_units": [{
                "custom_id": cust_id,
                "payee": {"email_address": payee_email},
                "payments": {
                    "captures": [{
                        "id": cap_id,
                        "status": "COMPLETED",
                        "amount": {"currency_code": curr, "value": "3.99"},
                    }]
                },
            }],
        }

    class _FakeResp:
        def __init__(self, data: Dict[str, Any]):
            self.status_code = 200
            self._data = data

        def json(self):
            return self._data

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url: str, *a, **kw):
            pp_id = url.split("/v2/checkout/orders/")[1].split("/")[0]
            return _FakeResp(payload_by_pp[pp_id])

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    for oid, *_rest in cases:
        with pytest.raises(HTTPException) as exc:
            await checkout_api.capture_paypal_order(oid, request=MagicMock(), user={"id": uid})
        assert exc.value.status_code == 400

    assert db.tables["profiles"][0]["tier"] == "free"
    assert all(o["status"] != "paid" for o in db.tables["payment_orders"])


@pytest.mark.anyio
async def test_checkout_capture_idempotent_by_capture_id_via_payment_events_unique(fake_env, monkeypatch):
    """`capture_paypal_order` must claim `(provider='paypal_capture', provider_event_id=capture_id)` in `payment_events` UNIQUE."""
    db, _rc = fake_env
    from app.api import checkout as checkout_api
    from app.api import payments as payments_api

    monkeypatch.setenv("PAYPAL_CLIENT_ID", "pp_id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "pp_sec")
    monkeypatch.setenv("PAYPAL_ME_URL", "billing@vidgrab.io")
    monkeypatch.setattr(payments_api, "_paypal_access_token", AsyncMock(return_value="tok_test"))

    uid = "20000000-0000-0000-0000-000000000004"
    base_exp = datetime.now(timezone.utc) + timedelta(days=10)
    db.tables["profiles"].append({
        "id": uid,
        "tier": "pro",
        "billing_status": "manual",
        "subscription_expiry": base_exp.isoformat(),
    })
    db.tables["payment_orders"].append({
        "id": "ord-cap-idem-1",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 599,
        "code": "VGPCAPIDEM1",
        "status": "pending",
        "provider_ref": "PP_CAP_IDEM_1",
        "expires_at": _future_iso(1),
    })
    db.tables["payment_orders"].append({
        "id": "ord-cap-idem-2",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "paypal",
        "currency": "USD",
        "amount": 599,
        "code": "VGPCAPIDEM2",
        "status": "pending",
        "provider_ref": "PP_CAP_IDEM_2",
        "expires_at": _future_iso(1),
    })

    class _FakeResp:
        def __init__(self, oid: str):
            self.status_code = 200
            self._oid = oid

        def json(self):
            return {
                "id": f"PP_{self._oid}",
                "status": "COMPLETED",
                "purchase_units": [{
                    "custom_id": f"{uid}:{self._oid}:pro_month",
                    "payee": {"email_address": "billing@vidgrab.io"},
                    "payments": {
                        "captures": [{
                            "id": "CAP_SHARED_UNIQUE_001",
                            "status": "COMPLETED",
                            "amount": {"currency_code": "USD", "value": "5.99"},
                        }]
                    },
                }],
            }

    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url: str, *a, **kw):
            pp_ref = url.split("/v2/checkout/orders/")[1].split("/")[0]
            oid = "ord-cap-idem-1" if pp_ref == "PP_CAP_IDEM_1" else "ord-cap-idem-2"
            return _FakeResp(oid)

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    res1 = await checkout_api.capture_paypal_order("ord-cap-idem-1", request=MagicMock(), user={"id": uid})
    assert res1["success"] is True
    exp_after_first = db.tables["profiles"][0]["subscription_expiry"]
    assert any(
        e.get("provider") == "paypal_capture" and e.get("provider_event_id") == "CAP_SHARED_UNIQUE_001"
        for e in db.tables["payment_events"]
    )

    # Reusing the exact same PayPal capture_id on a second pending order must be blocked by payment_events UNIQUE
    res2 = await checkout_api.capture_paypal_order("ord-cap-idem-2", request=MagicMock(), user={"id": uid})
    assert res2.get("duplicate") is True or res2.get("success") is False
    assert db.tables["payment_orders"][1]["status"] == "pending"
    assert db.tables["profiles"][0]["subscription_expiry"] == exp_after_first


# ══════════════════════════════════════════════════════════════════════
# 3. SePay Webhook Secret (`sepay_webhook.py` & `plans.py`)
# ══════════════════════════════════════════════════════════════════════


def test_sepay_no_default_secret_in_module_or_plan_settings(fake_env, monkeypatch):
    """`DEFAULT_SEPAY_SECRET` must be deleted and `plans.get_settings()['sepay_webhook_secret']` must default to ''."""
    _db, _rc = fake_env
    from app.core import plans, sepay_webhook

    monkeypatch.delenv("SEPAY_WEBHOOK_SECRET", raising=False)
    assert not hasattr(sepay_webhook, "DEFAULT_SEPAY_SECRET")
    assert sepay_webhook._secret() == ""
    assert plans.get_settings()["sepay_webhook_secret"] == ""


@pytest.mark.anyio
async def test_sepay_webhook_returns_503_and_logs_error_when_secret_unconfigured(fake_env, monkeypatch, caplog):
    """When no SePay secret is configured, `/api/v1/payments/sepay/webhook` must return 503 and log an ERROR."""
    _db, _rc = fake_env
    from app.api import payments as payments_api

    monkeypatch.delenv("SEPAY_WEBHOOK_SECRET", raising=False)
    req = MagicMock()
    req.body = AsyncMock(return_value=b'{"id":1,"transferType":"in","transferAmount":99000}')
    req.headers = {}

    with caplog.at_level(logging.ERROR):
        with pytest.raises(HTTPException) as exc:
            await payments_api.sepay_webhook(req)

    assert exc.value.status_code == 503
    assert any(r.levelno >= logging.ERROR and "sepay" in r.message.lower() for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════
# 4. Secondary Bugs (`activate_order`, SePay Case B unmatched, `is_first_time_buyer`)
# ══════════════════════════════════════════════════════════════════════


def test_activate_order_expired_and_underpaid_record_manual_review_and_alert(fake_env, monkeypatch):
    """`activate_order` on expired or underpaid orders must record `needs_manual_review` and send Telegram alerts."""
    db, _rc = fake_env
    from app.core import payment_orders as po

    alerts = _capture_telegram_alerts(monkeypatch)
    uid = "40000000-0000-0000-0000-000000000001"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-exp-001",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "vietqr",
        "currency": "VND",
        "amount": 99000,
        "code": "VGPEXP00001",
        "status": "pending",
        "expires_at": _past_iso(1),
    })
    db.tables["payment_orders"].append({
        "id": "ord-und-001",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "vietqr",
        "currency": "VND",
        "amount": 99000,
        "code": "VGPUND00001",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    ok_exp, msg_exp = po.activate_order("ord-exp-001", paid_amount=99000, provider_ref="sepay_exp_1")
    assert ok_exp is False and msg_exp == "order_expired_late_payment"

    ok_und, msg_und = po.activate_order("ord-und-001", paid_amount=50000, provider_ref="sepay_und_1")
    assert ok_und is False and msg_und == "underpaid"

    assert len(db.tables["payment_webhook_events"]) == 2
    assert all(ev["status"] == "needs_manual_review" for ev in db.tables["payment_webhook_events"])
    assert len(alerts) == 2


def test_sepay_case_b_no_code_and_unknown_code_record_manual_review_and_alert(fake_env, monkeypatch):
    """SePay incoming transfer with missing code or unknown code must record manual review + send Telegram alert."""
    db, _rc = fake_env
    from app.core import sepay_webhook

    alerts = _capture_telegram_alerts(monkeypatch)
    secret = "sepay_case_b_secret"
    monkeypatch.setenv("SEPAY_WEBHOOK_SECRET", secret)

    def _send(sepay_id: int, content: str) -> Dict[str, Any]:
        raw = json.dumps({
            "id": sepay_id,
            "gateway": "ACB",
            "transferType": "in",
            "transferAmount": 99000,
            "content": content,
        }).encode("utf-8")
        ts = str(int(time.time()))
        sig = hmac.new(secret.encode("utf-8"), f"{ts}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()
        return sepay_webhook.handle(raw, {"x-sepay-signature": f"sha256={sig}", "x-sepay-timestamp": ts})

    res_no_code = _send(70001, "Chuyen khoan nang cap tai khoan")
    assert res_no_code["action"] == "unmatched_no_code"

    res_not_found = _send(70002, "Thanh toan VGP99998888")
    assert res_not_found["action"] == "unmatched_code_not_found"

    assert len(db.tables["payment_webhook_events"]) == 2
    assert all(ev["status"] == "needs_manual_review" for ev in db.tables["payment_webhook_events"])
    assert len(alerts) == 2


def test_activate_order_upgrade_error_after_cas_marks_needs_manual_review_and_alerts(fake_env, monkeypatch):
    """If `_upgrade_user_pro(..., raise_on_error=True)` fails after CAS `paid`, order must become `needs_manual_review` + alert."""
    db, _rc = fake_env
    from app.core import payment_orders as po

    alerts = _capture_telegram_alerts(monkeypatch)
    uid = "40000000-0000-0000-0000-000000000002"
    db.tables["profiles"].append({"id": uid, "tier": "free", "billing_status": "none"})
    db.tables["payment_orders"].append({
        "id": "ord-upg-err-1",
        "user_id": uid,
        "plan": "pro_month",
        "rail": "vietqr",
        "currency": "VND",
        "amount": 99000,
        "code": "VGPUPGERR01",
        "status": "pending",
        "expires_at": _future_iso(1),
    })

    def _fail_profile_write(table: str, op: str, _payload: Any) -> None:
        if table == "profiles" and op in ("update", "insert"):
            raise RuntimeError("Simulated profiles write failure")

    db.before_execute_hook = _fail_profile_write
    try:
        ok, msg = po.activate_order("ord-upg-err-1", paid_amount=99000, provider_ref="sepay_upg_err_1")
    finally:
        db.before_execute_hook = None

    assert ok is False
    assert msg == "needs_manual_review"
    assert db.tables["payment_orders"][0]["status"] == "needs_manual_review"
    assert db.tables["profiles"][0]["tier"] == "free"
    assert len(db.tables["payment_webhook_events"]) == 1
    assert db.tables["payment_webhook_events"][0]["status"] == "needs_manual_review"
    assert len(alerts) == 1


def test_is_first_time_buyer_db_is_source_of_truth_and_redis_is_only_cache(fake_env):
    """DB is the source of truth for `is_first_time_buyer`; Redis is only a bounded-TTL cache."""
    db, rc = fake_env
    from app.core import payment_orders as po

    # 1. User had a canceled subscription in DB (`subscription_expiry=None`), Redis flushed -> MUST be False + cached with TTL
    uid_returning = "40000000-0000-0000-0000-000000000003"
    db.tables["profiles"].append({
        "id": uid_returning,
        "tier": "free",
        "billing_status": "canceled",
        "subscription_expiry": None,
    })
    rc.flushall()
    assert po.is_first_time_buyer(uid_returning) is False
    cache_keys = rc.keys(f"*{uid_returning}*")
    assert cache_keys and all(rc.ttl(k) > 0 for k in cache_keys)

    # 2. Brand-new user in DB (`free`/`none`, no paid orders), even if legacy Redis flag exists -> DB wins (`True`)
    uid_new = "40000000-0000-0000-0000-000000000004"
    db.tables["profiles"].append({
        "id": uid_new,
        "tier": "free",
        "billing_status": "none",
        "subscription_expiry": None,
    })
    rc.set(f"first_purchase_done:{uid_new}", "1")
    assert po.is_first_time_buyer(uid_new) is True

