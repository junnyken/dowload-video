"""
Task #6402 — pool cookies are encrypted at rest.

Stored form "enc:v1:<b64(nonce+AES-GCM)>", key = HKDF(SUPABASE_SERVICE_KEY)
or COOKIE_POOL_ENCRYPTION_KEY; plaintext when neither exists. Identity (the
hash every health/meta key and the admin remove-by-hash API use) is the hash
of the PLAINTEXT, for legacy and encrypted entries alike.
fakeredis only; no network.
"""
from __future__ import annotations

import base64
import logging
import os
import time

import pytest

fakeredis = pytest.importorskip("fakeredis")

from app.api.admin import verify_admin  # noqa: E402
from app.core import cookie_crypto, cookie_pool as cp, cookie_probe  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.services import downloader  # noqa: E402

SECRET_VALUE = "SUPERSECRETSESSIONVALUE123"
FUTURE = int(time.time()) + 200 * 86400


def _text(value=SECRET_VALUE, name="sessionid"):
    return ("# Netscape HTTP Cookie File\n"
            f".tiktok.com\tTRUE\t/\tTRUE\t{FUTURE}\t{name}\t{value}\n")


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def rc(monkeypatch, tmp_path):
    r = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.redis_client._client", r)
    monkeypatch.setattr(cp, "get_redis", lambda: r)
    monkeypatch.setattr(downloader, "_COOKIE_DIR", str(tmp_path / "vg_cookies"))
    monkeypatch.setattr(downloader, "_TIKTOK_COOKIES_B64", "", raising=False)
    for k in ("COOKIE_DAILY_CAP", "COOKIE_LAST_PLATFORMS"):
        monkeypatch.delenv(k, raising=False)
    downloader._reset_request_cookies()
    yield r
    downloader._reset_request_cookies()


@pytest.fixture
def key_on(monkeypatch):
    monkeypatch.delenv(cookie_crypto.OVERRIDE_ENV, raising=False)
    monkeypatch.setenv(cookie_crypto.SOURCE_ENV, "unit-test-source-secret-A")


@pytest.fixture
def key_off(monkeypatch):
    monkeypatch.delenv(cookie_crypto.OVERRIDE_ENV, raising=False)
    monkeypatch.delenv(cookie_crypto.SOURCE_ENV, raising=False)


# ── crypto primitives ────────────────────────────────────────────────

def test_round_trip_and_format(key_on):
    v = _b64(_text())
    s = cookie_crypto.encrypt(v)
    assert s.startswith("enc:v1:") and SECRET_VALUE not in s and v not in s
    assert cookie_crypto.decrypt(s) == v
    assert cookie_crypto.encrypt(v) != s                      # random nonce
    assert cookie_crypto.encrypt(s) == s                      # never double-encrypts
    raw = base64.b64decode(s[len("enc:v1:"):])
    assert len(raw) == 12 + len(v) + 16                       # nonce + ct + GCM tag


def test_override_key_used_and_bad_override_ignored(monkeypatch):
    monkeypatch.setenv(cookie_crypto.SOURCE_ENV, "src")
    derived = cookie_crypto.current_key()
    k = base64.b64encode(b"k" * 32).decode()
    monkeypatch.setenv(cookie_crypto.OVERRIDE_ENV, k)
    assert cookie_crypto.current_key() == b"k" * 32
    monkeypatch.setenv(cookie_crypto.OVERRIDE_ENV, "not-base64-32-bytes")
    assert cookie_crypto.current_key() == derived             # ignored, never crashes


def test_no_secret_stores_plaintext_exactly_as_before(rc, key_off):
    v = _b64(_text())
    cp.add_cookie("tiktok", v)
    assert rc.lrange("cookie_pool:tiktok", 0, -1) == [v]
    assert cp.get_cookie_from_pool("tiktok") == v
    assert cp.migrate_cookie_pool()["enabled"] is False
    assert rc.lrange("cookie_pool:tiktok", 0, -1) == [v]


# ── pool behaviour with encryption on ────────────────────────────────

def test_add_stores_ciphertext_and_selection_returns_plaintext(rc, key_on):
    v = _b64(_text())
    cp.add_cookie("tiktok", v, label="acc")
    [stored] = rc.lrange("cookie_pool:tiktok", 0, -1)
    assert stored.startswith("enc:v1:") and SECRET_VALUE not in stored
    assert cp.get_cookie_from_pool("tiktok") == v
    assert cp._get_meta(rc, "tiktok", cp._hash(v))["label"] == "acc"


def test_legacy_plaintext_still_read(rc, key_on):
    v = _b64(_text())
    rc.rpush("cookie_pool:tiktok", v)
    assert cp.get_cookie_from_pool("tiktok") == v
    assert cp.has_selectable_cookie("tiktok")


def test_id_hash_identical_for_legacy_and_encrypted(key_on):
    v = _b64(_text())
    legacy_hash = cp._hash(v)
    assert cp._hash(cookie_crypto.encrypt(v)) == legacy_hash
    assert legacy_hash == __import__("hashlib").md5(v.encode()).hexdigest()[:16]  # today's id


def test_add_dedupes_across_forms(rc, key_on):
    v = _b64(_text())
    rc.rpush("cookie_pool:tiktok", v)                         # legacy copy
    assert cp.add_cookie("tiktok", v) == 1
    rc.delete("cookie_pool:tiktok")
    cp.add_cookie("tiktok", v)
    assert cp.add_cookie("tiktok", v) == 1                    # encrypted copy, new nonce


def test_remove_by_hash_on_encrypted_entries(rc, key_on):
    a, b = _b64(_text("A1")), _b64(_text("B2"))
    cp.add_cookie("tiktok", a)
    cp.add_cookie("tiktok", b)
    ha = cp._hash(a)
    rc.set(f"cookie_health:tiktok:{ha}", "expired")
    out = cp.remove_cookies_by_hash("tiktok", [ha])
    assert out["removed"] == [ha] and out["pool_size"] == 1
    assert rc.get(f"cookie_health:tiktok:{ha}") is None
    assert [cp._hash(c) for c in rc.lrange("cookie_pool:tiktok", 0, -1)] == [cp._hash(b)]


def test_health_marks_by_plaintext_apply_to_encrypted_entry(rc, key_on):
    v = _b64(_text())
    cp.add_cookie("tiktok", v)
    cp.mark_cookie_disabled("tiktok", v, reason="x")
    assert cp.get_cookie_from_pool("tiktok") is None
    report = cp.get_expiry_report("tiktok")
    assert report[0]["health_status"] == "disabled" and report[0]["storage"] == "encrypted"


def test_replace_cookie_content_keeps_identity_and_encrypts(rc, key_on):
    old = _b64(_text("OLDVAL"))
    new = _b64(_text("NEWVAL"))
    cp.add_cookie("tiktok", old, label="keep")
    assert cp.replace_cookie_content("tiktok", old, new)
    [stored] = rc.lrange("cookie_pool:tiktok", 0, -1)
    assert stored.startswith("enc:v1:") and cookie_crypto.decrypt(stored) == new
    assert cp._get_meta(rc, "tiktok", cp._hash(new))["label"] == "keep"


# ── migration ────────────────────────────────────────────────────────

def test_migration_idempotent_with_counts(rc, key_on):
    a, b, c = (_b64(_text(x)) for x in ("A", "B", "C"))
    rc.rpush("cookie_pool:tiktok", a, b)
    rc.rpush("cookie_pool:youtube", cookie_crypto.encrypt(c))
    rc.set(f"cookie_health:tiktok:{cp._hash(a)}", "soft")
    before = [cp._hash(x) for x in rc.lrange("cookie_pool:tiktok", 0, -1)]

    out = cp.migrate_cookie_pool()
    assert out["enabled"] is True
    assert out["platforms"]["tiktok"] == {"encrypted_now": 2, "already_encrypted": 0,
                                          "legacy_left": 0, "undecryptable": 0}
    assert out["platforms"]["youtube"]["already_encrypted"] == 1
    stored = rc.lrange("cookie_pool:tiktok", 0, -1)
    assert all(s.startswith("enc:v1:") for s in stored)
    assert [cp._hash(x) for x in stored] == before            # same order, same ids
    assert rc.get(f"cookie_health:tiktok:{cp._hash(a)}") == "soft"

    again = cp.migrate_cookie_pool()
    assert again["platforms"]["tiktok"] == {"encrypted_now": 0, "already_encrypted": 2,
                                            "legacy_left": 0, "undecryptable": 0}
    assert rc.lrange("cookie_pool:tiktok", 0, -1) == stored   # untouched second time
    st = cp.get_pool_status()["tiktok"]["storage"]
    assert st == {"encrypted": 2, "legacy": 0, "undecryptable": 0}


def test_startup_migration_runs_once_under_lock(rc, key_on):
    rc.rpush("cookie_pool:tiktok", _b64(_text()))
    assert cp.migrate_cookie_pool_once()["platforms"]["tiktok"]["encrypted_now"] == 1
    assert cp.migrate_cookie_pool_once() is None              # lock held
    assert rc.ttl(cp.MIGRATE_LOCK_KEY) > 0


# ── key changed → undecryptable, skipped, never leaked ───────────────

def test_wrong_key_entry_is_skipped_without_exception_or_leak(rc, key_on, monkeypatch, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    bad = _b64(_text(SECRET_VALUE))
    good = _b64(_text("OTHERFINE"))
    cp.add_cookie("tiktok", bad)
    monkeypatch.setenv(cookie_crypto.SOURCE_ENV, "rotated-secret-B")   # key changed
    rc.rpush("cookie_pool:tiktok", good)                                 # a legacy one still works

    assert cp.get_cookie_from_pool("tiktok") == good
    rc.delete(f"cookie_cooldown:tiktok:{cp._hash(good)}")
    status = cp.get_pool_status()["tiktok"]
    assert status["storage"] == {"encrypted": 0, "legacy": 1, "undecryptable": 1}
    assert status["healthy"] == 1
    report = cp.get_expiry_report("tiktok")
    assert report[0]["health_status"] == "undecryptable" and report[0]["usable"] is False
    out = cp.migrate_cookie_pool()["platforms"]["tiktok"]
    assert out["undecryptable"] == 1 and out["encrypted_now"] == 1
    # admin can still remove it by the id the list shows
    assert cp.remove_cookies_by_hash("tiktok", [report[0]["hash"]])["pool_size"] == 1

    printed = capsys.readouterr()
    for blob in (caplog.text, printed.out, printed.err):
        assert SECRET_VALUE not in blob and bad not in blob


def test_only_undecryptable_means_no_cookie(rc, key_on, monkeypatch):
    cp.add_cookie("tiktok", _b64(_text()))
    monkeypatch.setenv(cookie_crypto.SOURCE_ENV, "rotated-secret-B")
    assert cp.get_cookie_from_pool("tiktok") is None
    assert cp.has_selectable_cookie("tiktok") is False


def test_probe_gets_plaintext_and_reports_undecryptable(rc, key_on, monkeypatch):
    v = _b64(_text())
    cp.add_cookie("tiktok", v)
    seen = []
    res = cookie_probe.retest_cookie("tiktok", cp._hash(v),
                                     probe=lambda c, proxy: (seen.append(c) or ("ok", "fine")))
    assert seen == [v] and res["status"] == "ok"
    monkeypatch.setenv(cookie_crypto.SOURCE_ENV, "rotated-secret-B")
    stored = rc.lrange("cookie_pool:tiktok", 0, -1)[0]
    res = cookie_probe.retest_cookie("tiktok", cp._hash(stored), probe=lambda c, p: ("ok", ""))
    assert res["status"] == "undecryptable"


# ── downloader writes decrypted text ─────────────────────────────────

def test_downloader_cookie_file_holds_decrypted_text(rc, key_on):
    text = _text()
    cp.add_cookie("tiktok", _b64(text))
    assert rc.lrange("cookie_pool:tiktok", 0, -1)[0].startswith("enc:v1:")
    path = downloader._get_cookies_file("tiktok", "")
    assert path and os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        body = f.read()
    assert f"sessionid\t{SECRET_VALUE}" in body and "enc:v1:" not in body


# ── admin endpoint ───────────────────────────────────────────────────

@pytest.fixture
def client(rc, monkeypatch):
    from fastapi.testclient import TestClient
    calls = []
    monkeypatch.setattr("app.api.admin.log_admin_action", lambda *a, **k: calls.append((a, k)))
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    c = TestClient(fastapi_app)
    c.audit_calls = calls
    yield c
    fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_encrypt_all_endpoint_counts_and_audits(client, rc, key_on):
    v = _b64(_text())
    rc.rpush("cookie_pool:tiktok", v)
    r = client.post("/api/v1/admin/cookies/encrypt-all")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["platforms"]["tiktok"]["encrypted_now"] == 1
    assert SECRET_VALUE not in r.text and v not in r.text
    [(args, kw)] = client.audit_calls
    assert args[1] == "admin.cookie.encrypt_all" and kw["metadata"]["encrypted_now"] == 1
    assert v not in repr(client.audit_calls)
    status = client.get("/api/v1/admin/cookies/status").json()
    assert status["pools"]["tiktok"]["storage"]["encrypted"] == 1


def test_encrypt_all_requires_admin(rc):
    from fastapi.testclient import TestClient
    r = TestClient(fastapi_app).post("/api/v1/admin/cookies/encrypt-all")
    assert r.status_code in (401, 403)
