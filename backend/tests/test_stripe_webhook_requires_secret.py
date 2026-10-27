"""27/10/2026: with STRIPE_WEBHOOK_SECRET unset (production), /payments/webhook
accepted UNSIGNED events — anyone could POST checkout.session.completed with a
user id and get Pro. No secret now means no webhook (404), and nothing is granted."""
import json

from fastapi.testclient import TestClient

import app.api.payments as payments
from app.main import app

FORGED = {"id": "evt_forged", "type": "checkout.session.completed",
          "data": {"object": {"metadata": {"user_id": "victim-or-self"}, "subscription": "sub_fake"}}}


def test_unsigned_event_is_refused_without_secret(monkeypatch):
    monkeypatch.setattr(payments, "_STRIPE_WEBHOOK_SECRET", "")
    granted, recorded = [], []
    monkeypatch.setattr(payments, "_upgrade_user", lambda *a, **k: granted.append(a))
    monkeypatch.setattr(payments, "_downgrade_user", lambda *a, **k: granted.append(a))
    monkeypatch.setattr(payments, "_record_payment_event", lambda *a, **k: recorded.append(a) or True)
    r = TestClient(app).post("/api/v1/payments/webhook", content=json.dumps(FORGED),
                             headers={"Content-Type": "application/json"})
    assert r.status_code == 404
    assert granted == [] and recorded == []


def test_bad_signature_is_refused_with_secret(monkeypatch):
    monkeypatch.setattr(payments, "_STRIPE_WEBHOOK_SECRET", "whsec_test")
    granted = []
    monkeypatch.setattr(payments, "_upgrade_user", lambda *a, **k: granted.append(a))

    class FakeStripe:
        class Webhook:
            @staticmethod
            def construct_event(body, sig, secret):
                raise ValueError("No signatures found matching the expected signature")
    monkeypatch.setattr(payments, "_stripe", lambda: FakeStripe)
    r = TestClient(app).post("/api/v1/payments/webhook", content=json.dumps(FORGED),
                             headers={"Content-Type": "application/json", "stripe-signature": "t=1,v1=bad"})
    assert r.status_code == 400 and granted == []
