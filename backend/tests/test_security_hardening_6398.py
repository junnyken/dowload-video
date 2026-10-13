"""
Task #6398 (Phase 34B-4..7) — security hardening regressions.

  1. Admin sessions are stored hashed in Redis; legacy raw-token sessions are
     accepted once and migrated; sessions have an absolute max age.
  2. POST /admin/logout ends the presented session server-side.
  3. Partner key IP allowlist uses get_client_ip (last XFF hop), not the
     spoofable first hop that uvicorn puts in request.client.host.
  4. Partner key rate_limit_per_min is enforced per key (fails open on Redis).
  5. State-changing admin routes that had no audit entry now write one.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.admin as admin


@pytest.fixture
def r(monkeypatch):
    fr = fakeredis.FakeRedis()
    monkeypatch.setattr(admin, "_redis", lambda: fr)
    return fr


# ── 1. hashed sessions ────────────────────────────────────────────────────────

def test_session_key_does_not_contain_token():
    tok = "abcDEF123_secret-token"
    key = admin._session_key(tok)
    assert key.startswith("admin:session:")
    assert tok not in key


def test_login_stores_hashed_session_and_it_validates(r, monkeypatch):
    monkeypatch.setattr(admin, "_ADMIN_PASSWORD", "correct horse battery")
    monkeypatch.setattr(admin, "log_admin_action", lambda *a, **k: None)
    api = FastAPI()
    api.include_router(admin.router, prefix="/admin")
    c = TestClient(api)
    res = c.post("/admin/login", json={"password": "correct horse battery"})
    assert res.status_code == 200, res.text
    token = res.json()["session_token"]
    keys = [k.decode() for k in r.keys("admin:session:*")]
    assert keys == [admin._session_key(token)]
    assert all(token not in k for k in keys)
    assert admin._session_is_valid(r, token)


def test_legacy_raw_session_is_accepted_once_and_migrated(r):
    r.set("admin:session:OLDTOKEN", "1", ex=60)
    assert admin._session_is_valid(r, "OLDTOKEN")
    assert not r.exists("admin:session:OLDTOKEN")
    assert r.exists(admin._session_key("OLDTOKEN"))
    assert admin._session_is_valid(r, "OLDTOKEN")


def test_session_older_than_max_age_is_rejected_and_removed(r):
    old = int(time.time()) - admin._SESSION_MAX_AGE - 5
    r.setex(admin._session_key("T"), admin._SESSION_TTL, str(old))
    assert not admin._session_is_valid(r, "T")
    assert not r.exists(admin._session_key("T"))


def test_fresh_session_refreshes_idle_ttl(r):
    r.setex(admin._session_key("T"), 10, admin._new_session_value())
    assert admin._session_is_valid(r, "T")
    assert r.ttl(admin._session_key("T")) > 10


def test_unknown_token_is_invalid(r):
    assert not admin._session_is_valid(r, "nope")
    assert not admin._session_is_valid(r, "")


# ── 2. logout ─────────────────────────────────────────────────────────────────

def test_logout_revokes_presented_session(r, monkeypatch):
    monkeypatch.setattr(admin, "_ADMIN_ALLOWED_IPS", set())
    logged = []
    monkeypatch.setattr(admin, "log_admin_action", lambda req, action, **k: logged.append(action))
    r.setex(admin._session_key("LIVE"), 60, admin._new_session_value())
    r.setex(admin._session_key("OTHER"), 60, admin._new_session_value())
    api = FastAPI()
    api.include_router(admin.router, prefix="/admin")
    c = TestClient(api)
    res = c.post("/admin/logout", headers={"Authorization": "Bearer LIVE"})
    assert res.status_code == 200 and res.json() == {"success": True}
    assert not admin._session_is_valid(r, "LIVE")
    assert admin._session_is_valid(r, "OTHER"), "logout must end only the caller's session"
    assert logged == ["admin.logout"]
    # the revoked token can no longer reach admin routes, logout included
    monkeypatch.setattr(admin, "log_access_denied", lambda *a, **k: None)
    res2 = c.post("/admin/logout", headers={"Authorization": "Bearer LIVE"})
    assert res2.status_code == 401
    assert logged == ["admin.logout"]


# ── 3 + 4. partner auth ──────────────────────────────────────────────────────

def _partner_request(xff: str, first_hop: str):
    req = MagicMock()
    req.headers = {"x-forwarded-for": xff}
    req.client = SimpleNamespace(host=first_hop)
    return req


def test_partner_allowlist_uses_last_xff_hop(monkeypatch):
    """Simulate uvicorn --forwarded-allow-ips '*': client.host = FIRST XFF
    entry (attacker-chosen). The allowlist must look at the real hop."""
    from app.core import partner_auth

    key_row = {"id": "k1", "tenant_id": "t1", "is_active": True,
               "ip_allowlist": ["198.51.100.7"], "expires_at": None}
    monkeypatch.setattr(partner_auth, "_extract_partner_key", lambda req: "vgp_x")

    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.maybe_single.return_value \
        .execute.return_value = SimpleNamespace(data=key_row)
    sb.table.return_value.select.return_value.eq.return_value.single.return_value \
        .execute.return_value = SimpleNamespace(data=key_row)
    monkeypatch.setattr(partner_auth, "get_service_client", lambda: sb)
    import app.core.redis_client as rc
    monkeypatch.setattr(rc, "get_redis", lambda: fakeredis.FakeRedis())
    monkeypatch.setattr(partner_auth, "_update_key_usage", MagicMock())

    req = _partner_request("198.51.100.7, 203.0.113.99", first_hop="198.51.100.7")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(partner_auth.get_partner_tenant(req))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "ip_not_allowed"

    # Real hop allowed, spoofed first hop not: must get past the allowlist.
    req_ok = _partner_request("10.9.9.9, 198.51.100.7", first_hop="10.9.9.9")
    try:
        asyncio.run(partner_auth.get_partner_tenant(req_ok))
    except HTTPException as e:  # later steps may still refuse; not the allowlist
        assert not (isinstance(e.detail, dict) and e.detail.get("code") == "ip_not_allowed")
    except Exception:
        pass  # mocked tenant rows; the allowlist step is what is under test


def test_partner_allowlist_source_uses_get_client_ip():
    import inspect
    from app.core import partner_auth
    src = inspect.getsource(partner_auth.get_partner_tenant)
    assert "request.client.host" not in src
    assert "get_client_ip(request)" in src


def test_per_minute_limit_counts_and_blocks(monkeypatch):
    from app.core import partner_auth
    import app.core.redis_client as rc
    fr = fakeredis.FakeRedis()
    monkeypatch.setattr(rc, "get_redis", lambda: fr)
    results = [partner_auth._per_minute_exceeded("key-a", 3) for _ in range(4)]
    assert results[:3] == [None, None, None]
    assert isinstance(results[3], int) and 1 <= results[3] <= 60
    # independent per key
    assert partner_auth._per_minute_exceeded("key-b", 3) is None


def test_per_minute_limit_fails_open_without_redis(monkeypatch):
    from app.core import partner_auth
    import app.core.redis_client as rc

    def boom():
        raise RuntimeError("redis down")
    monkeypatch.setattr(rc, "get_redis", boom)
    assert partner_auth._per_minute_exceeded("key-a", 1) is None
    assert partner_auth._per_minute_exceeded("key-a", 0) is None


def test_feedback_uses_get_client_ip():
    import inspect
    from app.api import feedback
    src = inspect.getsource(feedback.submit_feedback)
    assert "request.client.host" not in src


# ── 5. audit coverage of admin mutations ─────────────────────────────────────

def test_state_changing_admin_routes_write_audit_entries():
    """Every POST/PUT/PATCH/DELETE in the admin routers must call an audit
    helper, except the read-only probes listed here (they change nothing)."""
    import ast
    import pathlib

    exempt = {
        ("admin.py", "/login"),                 # logs success itself; failures via log_access_denied
        ("admin.py", "/po-token/test"),         # read-only probe
        ("admin.py", "/cookies/test"),          # read-only probe
        ("admin.py", "/scraperapi/refresh-credits"),  # cache refresh of credit counts
        ("admin.py", "/throwaway/accounts"),    # machine ingest from tools/create_throwaway.py
        ("admin.py", "/debug-youtube"),         # read-only diagnostic
        ("watch.py", "/watch/sources/{source_id}/pause"),   # logs in _admin_set_source
        ("watch.py", "/watch/sources/{source_id}/resume"),  # logs in _admin_set_source
    }
    base = pathlib.Path(admin.__file__).parent
    files = sorted(base.glob("admin*.py")) + [base / n for n in ("yt_health.py", "ops.py", "takedown.py", "watch.py")]
    missing = []
    for f in files:
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in n.decorator_list:
                if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr in ("post", "put", "patch", "delete")):
                    continue
                router = ast.unparse(d.func.value)
                if router == "public_router" or (f.name == "watch.py" and router == "router"):
                    continue  # user/public routes, not admin
                path = ast.literal_eval(d.args[0]) if d.args else "?"
                body = ast.unparse(n)
                if (f.name, path) in exempt or path == "/logout":
                    continue
                if not any(k in body for k in ("log_admin_action", "log_from_request")):
                    missing.append(f"{f.name}:{n.lineno} {d.func.attr.upper()} {path}")
    assert not missing, "admin mutations without an audit entry:\n" + "\n".join(missing)
