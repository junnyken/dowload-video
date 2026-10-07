"""
Admin-managed Apify token for the China access layer.

Precedence admin-stored > env CHINA_ACCESS_APIFY_TOKEN > none; the token is
validated with a free Apify call (mocked here) before it is stored, and it is
never echoed by an endpoint, the audit log, or logs. The legacy APIFY_TOKEN
stays untouched. No network: httpx.MockTransport / a patched validator.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
import pytest

from app.services.china_platforms import secret_store, settings
from app.services.china_platforms.errors import redact
from app.services.china_platforms.normalized_models import ChinaResolveRequest
from tests._china_fakes import DY_URL, PUBLIC_CTX, clean_env, flags_on, rc  # noqa: F401
from tests.test_admin_download_metrics import admin  # noqa: F401

ADMIN_TOK = "apify_api_ADMINsecretTOKEN0000wxyz"
ENV_TOK = "apify_api_ENVsecretTOKEN00000000abcd"
BASE = "/api/v1/admin/china-platforms/apify/token"


def apify_transport(valid_tokens=(ADMIN_TOK, ENV_TOK), seen=None):
    seen = seen if seen is not None else []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        tok = req.headers.get("authorization", "").removeprefix("Bearer ")
        if tok not in valid_tokens:
            return httpx.Response(401, json={"error": {"type": "invalid-token",
                                                       "message": "Authentication token is not valid."}})
        if req.url.path.endswith("/users/me"):
            return httpx.Response(200, json={"data": {"username": "vidgrab-cn", "plan": {"id": "FREE"},
                                                      "isPaying": False, "proxy": {"password": "PROXYPASS"}}})
        if req.url.path.endswith("/users/me/limits"):
            return httpx.Response(200, json={"data": {
                "monthlyUsageCycle": {"startAt": "2026-10-01T00:00:00Z", "endAt": "2026-10-31T23:59:59Z"},
                "limits": {"maxMonthlyUsageUsd": 5}, "current": {"monthlyUsageUsd": 0.42}}})
        return httpx.Response(404)
    return httpx.MockTransport(handler), seen


@pytest.fixture
def mock_apify(monkeypatch):
    import app.api.admin_china_platforms as mod
    t, seen = apify_transport()

    async def validator(token):
        return await secret_store.validate(token, transport=t)
    monkeypatch.setattr(mod, "_apify_validator", lambda: validator)
    return seen


@pytest.fixture
def audit(monkeypatch):
    import app.api.admin_china_platforms as mod
    rows = []
    monkeypatch.setattr(mod, "log_admin_action", lambda req, action, **kw: rows.append((action, kw)))
    return rows


def _no_secret(text: str):
    for bad in (ADMIN_TOK, ENV_TOK, "ADMINsecret", "ENVsecret", "PROXYPASS"):
        assert bad not in text, bad


# ── precedence ──────────────────────────────────────────────────────────────

class TestPrecedence:

    def test_none(self, clean_env, rc):
        assert secret_store.resolve_apify_token() == ("", "none")
        assert settings.apify_token() == ""

    def test_env_then_admin_wins_then_delete_falls_back(self, clean_env, rc):
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", ENV_TOK)
        assert secret_store.resolve_apify_token() == (ENV_TOK, "env")
        secret_store.store(ADMIN_TOK, {"account": {}}, now_iso="t", ip=None)
        assert secret_store.resolve_apify_token() == (ADMIN_TOK, "admin")
        assert settings.apify_token() == ADMIN_TOK
        secret_store.delete()
        assert secret_store.resolve_apify_token() == (ENV_TOK, "env")

    def test_redis_down_falls_back_to_env(self, clean_env, monkeypatch):
        from tests._china_fakes import down_redis
        monkeypatch.setattr("app.core.redis_client._client", down_redis())
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", ENV_TOK)
        assert secret_store.resolve_apify_token() == (ENV_TOK, "env")

    def test_provider_reads_token_at_call_time(self, flags_on, rc):
        from app.services.china_platforms.adapters.douyin import DouyinAdapter
        prov = DouyinAdapter().build_providers()["apify_douyin"]
        assert prov.is_configured() is False
        secret_store.store(ADMIN_TOK, {"account": {}}, now_iso="t", ip=None)
        assert prov.is_configured() is True          # same instance, no restart
        sent = []

        def handler(req):
            sent.append(req.headers.get("authorization"))
            return httpx.Response(402)
        prov._transport = httpx.MockTransport(handler)
        with pytest.raises(Exception):
            asyncio.run(prov.resolve(ChinaResolveRequest(url=DY_URL), PUBLIC_CTX))
        assert sent == [f"Bearer {ADMIN_TOK}"]

    def test_admin_token_never_becomes_the_legacy_env_var(self, clean_env, rc):
        import app.services.apify_service as legacy
        secret_store.store(ADMIN_TOK, {"account": {}}, now_iso="t", ip=None)
        assert os.getenv("APIFY_TOKEN") is None
        assert not hasattr(legacy, "APIFY_TOKEN")      # legacy path removed, task #6055
        assert rc.keys("*APIFY_TOKEN*") == [] and rc.keys("apify*") == []

    def test_admin_token_is_redacted_everywhere(self, clean_env, rc):
        secret_store.store(ADMIN_TOK, {"account": {}}, now_iso="t", ip=None)
        assert ADMIN_TOK not in redact(f"boom Authorization: Bearer {ADMIN_TOK}")


# ── validation (free Apify calls, mocked) ───────────────────────────────────

class TestValidate:

    def test_valid_returns_account_and_usage_without_proxy_password(self):
        t, seen = apify_transport()
        info = asyncio.run(secret_store.validate(ADMIN_TOK, transport=t))
        assert info["account"] == {"username": "vidgrab-cn", "plan": "FREE", "is_paying": False}
        assert info["usage"]["monthly_usage_usd"] == 0.42 and info["usage"]["max_monthly_usage_usd"] == 5
        assert [p for _, p in seen] == ["/v2/users/me", "/v2/users/me/limits"]
        assert all(m == "GET" for m, _ in seen)         # no actor run, nothing billable
        assert "PROXYPASS" not in json.dumps(info)

    def test_invalid_token(self):
        t, _ = apify_transport(valid_tokens=())
        with pytest.raises(secret_store.TokenRejected) as ei:
            asyncio.run(secret_store.validate(ADMIN_TOK, transport=t))
        assert ei.value.code == "invalid_token" and ADMIN_TOK not in str(ei.value) + ei.value.message

    def test_unreachable(self):
        def boom(req):
            raise httpx.ConnectError(f"cannot connect {req.headers.get('authorization')}")
        with pytest.raises(secret_store.TokenRejected) as ei:
            asyncio.run(secret_store.validate(ADMIN_TOK, transport=httpx.MockTransport(boom)))
        assert ei.value.code == "apify_unreachable" and ADMIN_TOK not in ei.value.message

    @pytest.mark.parametrize("raw", ["", "short", "apify api with spaces but long enough?!", "x" * 300, None])
    def test_sanitize_rejects(self, raw):
        with pytest.raises(secret_store.TokenRejected):
            secret_store.sanitize(raw)

    def test_sanitize_strips_whitespace(self):
        assert secret_store.sanitize(f"  {ADMIN_TOK}\n") == ADMIN_TOK


# ── endpoints ───────────────────────────────────────────────────────────────

class TestEndpoints:

    def test_requires_admin(self, app, rc):
        assert app.get(BASE).status_code == 401
        assert app.post(BASE, json={"token": ADMIN_TOK, "reason": "x" * 5}).status_code == 401

    def test_set_get_test_delete_never_echo_token(self, app, admin, clean_env, rc, mock_apify, audit, caplog):
        caplog.set_level(logging.DEBUG)
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", ENV_TOK)
        r0 = app.get(BASE)
        assert r0.json()["source"] == "env" and r0.json()["last4"] == "abcd"
        r = app.post(BASE, json={"token": f" {ADMIN_TOK} ", "reason": "new account"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["configured"] and body["source"] == "admin" and body["last4"] == "wxyz"
        assert body["account"]["username"] == "vidgrab-cn" and body["usage"]["monthly_usage_usd"] == 0.42
        assert body["validated_at"] and body["set_at"]
        t = app.post(BASE + "/test")
        assert t.status_code == 200 and t.json()["valid"] is True
        ov = app.get("/api/v1/admin/china-platforms").json()
        assert ov["apify_configured"] is True and ov["apify_token_source"] == "admin"
        d = app.request("DELETE", BASE, json={"reason": "rotate"})
        assert d.status_code == 200 and d.json()["source"] == "env" and d.json()["removed"] is True
        assert "CHINA_ACCESS_APIFY_TOKEN" in d.json()["note"]
        for resp in (r0, r, t, d, app.get(BASE), app.get("/api/v1/admin/china-platforms")):
            _no_secret(resp.text)
        _no_secret(json.dumps(audit, default=str))
        _no_secret(caplog.text)
        actions = [a for a, _ in audit]
        assert actions == ["admin.china_access.apify_token_set", "admin.china_access.apify_token_tested",
                           "admin.china_access.apify_token_deleted"]
        assert audit[0][1]["metadata"]["new"] == {"source": "admin", "last4": "wxyz"}
        assert audit[0][1]["metadata"]["prior"] == {"source": "env", "last4": "abcd"}

    def test_invalid_token_rejected_and_not_stored(self, app, admin, clean_env, rc, mock_apify, audit):
        bad = "apify_api_WRONGwrongWRONG0000qqqq"
        r = app.post(BASE, json={"token": bad, "reason": "try"})
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_token"
        assert rc.get(secret_store.TOKEN_KEY) is None and app.get(BASE).json()["configured"] is False
        assert bad not in r.text and bad not in json.dumps(audit, default=str)
        assert audit[0][0] == "admin.china_access.apify_token_rejected"

    def test_bad_body_never_echoes_token(self, app, admin, clean_env, rc, mock_apify, audit):
        # Missing reason: FastAPI's 422 would echo the input; this endpoint must not.
        r = app.post(BASE, json={"token": ADMIN_TOK})
        assert r.status_code == 400 and ADMIN_TOK not in r.text
        r = app.post(BASE, json={"token": ADMIN_TOK, "reason": f"pasted {ADMIN_TOK} by mistake"})
        assert r.status_code == 200
        _no_secret(json.dumps(audit, default=str))
        r = app.post(BASE, json={"token": "bad token!", "reason": "fmt"})
        assert r.status_code == 400 and r.json()["detail"]["error"] == "invalid_format"
        assert app.post(BASE, content=b"not json", headers={"content-type": "application/json"}).status_code == 400

    def test_delete_without_env_says_none(self, app, admin, clean_env, rc, mock_apify, audit):
        app.post(BASE, json={"token": ADMIN_TOK, "reason": "set"})
        d = app.request("DELETE", BASE, json={"reason": "gone"})
        assert d.json()["source"] == "none" and d.json()["configured"] is False
        assert app.request("DELETE", BASE, json={}).status_code == 422     # reason required

    def test_test_without_token_404(self, app, admin, clean_env, rc, mock_apify):
        assert app.post(BASE + "/test").status_code == 404

    def test_env_validation_record_ignored_after_env_token_changes(self, app, admin, clean_env, rc,
                                                                  mock_apify, audit):
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", ENV_TOK)
        assert app.post(BASE + "/test").json()["account"]["username"] == "vidgrab-cn"
        clean_env.setenv("CHINA_ACCESS_APIFY_TOKEN", "apify_api_OTHERotherOTHER000zzzz")
        assert app.get(BASE).json()["account"] is None
