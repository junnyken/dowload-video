"""
Task #6172 (PLAN-32E P3) — Ed25519 claim tokens + signed policy.

Holds: no CLIENT_QUOTA_SIGNING_KEY → /client/version and claims are exactly
what app 0.9.x got before (no "policy", no "token") · with a key the policy
and tokens verify with the public key · a token is bound to claim id, url
hash, machine and expiry · a changed byte, another key, an expired token, a
wrong url or machine are all rejected · the checked-in cross-language fixture
(desktop/src-tauri/tests/fixtures/claim_p3.json, read by cargo test) verifies
here too. Keys are throwaway, generated in the test.
fakeredis + TestClient, no network.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from app.core import claim_signing
from app.core.auth_middleware import get_optional_user
from app.main import app as fastapi_app, limiter
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)
URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "desktop/src-tauri/tests/fixtures/claim_p3.json"


def dev(n: int) -> str:
    return hashlib.sha256(f"machine-{n}".encode()).hexdigest()


def new_key():
    k = Ed25519PrivateKey.generate()
    seed = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(seed).decode(), pub


@pytest.fixture(autouse=True)
def _env(monkeypatch, rc):
    from app.api import client_api, client_quota
    lims = {id(x): x for x in (limiter, client_quota.limiter, client_api.limiter)}.values()
    prev = [(x, x.enabled) for x in lims]
    for x in lims:
        x.enabled = False
    for k in ("CLIENT_QUOTA_MODE", "CLIENT_QUOTA_ENFORCE_FOR", "CLIENT_QUOTA_ENFORCE_USERS", "CLIENT_QUOTA_OFFLINE_GRACE",
              "CLIENT_QUOTA_SIGNING_KEY", "CLIENT_QUOTA_SIGNING_KID", "PLATFORM_DAILY_LIMIT_ANON", "QUOTA_SCOPE",
              "DESKTOP_MIN_VERSION", "DESKTOP_LATEST_VERSION"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "true")
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "enforce")
    monkeypatch.setattr("app.core.database.get_service_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    yield
    fastapi_app.dependency_overrides.pop(get_optional_user, None)
    for x, was in prev:
        x.enabled = was


@pytest.fixture
def key(monkeypatch):
    seed, pub = new_key()
    monkeypatch.setenv("CLIENT_QUOTA_SIGNING_KEY", seed)
    monkeypatch.setenv("CLIENT_QUOTA_SIGNING_KID", "t1")
    return {"t1": pub}


def claim(url=URL, device=None, **body):
    h = {"X-VG-Device": device} if device else {}
    return client.post("/api/v1/client/quota/claim", json={"url": url, **body}, headers=h)


def flip(s: str, i: int) -> str:
    c = "A" if s[i] != "A" else "B"
    return s[:i] + c + s[i + 1:]


# ── no key: old behaviour (app 0.9.x and 0.10 against an unsigning server) ──

def test_no_key_no_policy_no_token():
    v = client.get("/api/v1/client/version").json()
    assert "policy" not in v
    assert set(v) == {"latest", "minSupported", "notes", "downloadUrl", "features"}
    r = claim(device=dev(1))
    assert r.status_code == 200 and "token" not in r.json()
    b = client.post("/api/v1/client/quota/claim-batch", json={"items": [{"url": URL}]},
                    headers={"X-VG-Device": dev(1)}).json()
    assert "token" not in b["items"][0]
    assert claim_signing.policy(True, 3, "0.6.0") is None
    assert claim_signing.claim_token("abc", [URL], dev(1)) is None


def test_bad_key_is_off_not_a_crash(monkeypatch):
    monkeypatch.setenv("CLIENT_QUOTA_SIGNING_KEY", "not-base64-of-32-bytes!!")
    assert client.get("/api/v1/client/version").status_code == 200
    assert "policy" not in client.get("/api/v1/client/version").json()
    assert "token" not in claim(device=dev(1)).json()
    monkeypatch.setenv("CLIENT_QUOTA_SIGNING_KEY", base64.b64encode(b"x" * 31).decode())
    assert claim_signing.signing_enabled() is False


def test_app_09_sees_same_fields_plus_unknown_ones(monkeypatch):
    """0.9.x parses claim / version answers by name and ignores unknown
    fields (quota-core.ts decideClaim, update-core.ts). With a key the
    answers keep every old field with the same value; only new keys appear."""
    before_v = client.get("/api/v1/client/version").json()
    before_c = claim(url=URL + "&a=1", device=dev(2)).json()
    seed, _ = new_key()
    monkeypatch.setenv("CLIENT_QUOTA_SIGNING_KEY", seed)
    after_v = client.get("/api/v1/client/version").json()
    after_c = claim(url=URL + "&a=2", device=dev(3)).json()
    assert set(after_v) - set(before_v) == {"policy"}
    assert {k: after_v[k] for k in before_v} == before_v
    assert set(after_c) - set(before_c) == {"token"}
    for k in ("allowed", "platform", "alreadyCounted", "overLimit", "mode", "limit", "usedToday", "remaining"):
        assert after_c[k] == before_c[k], k


# ── with a key ───────────────────────────────────────────────────────────

def test_policy_signed_and_follows_flags(key, monkeypatch):
    monkeypatch.setenv("DESKTOP_MIN_VERSION", "0.6.0")
    monkeypatch.setenv("CLIENT_QUOTA_OFFLINE_GRACE", "2")
    p = claim_signing.verify(client.get("/api/v1/client/version").json()["policy"], key)
    assert p and p["v"] == 1 and p["kid"] == "t1" and p["requireToken"] is True
    assert p["grace"] == 2 and p["min"] == "0.6.0" and p["exp"] - p["iat"] == 24 * 3600
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "shadow")
    assert claim_signing.verify(client.get("/api/v1/client/version").json()["policy"], key)["requireToken"] is False
    monkeypatch.setenv("CLIENT_QUOTA_MODE", "enforce")
    monkeypatch.setenv("CLIENT_QUOTA_ENABLED", "false")
    assert claim_signing.verify(client.get("/api/v1/client/version").json()["policy"], key)["requireToken"] is False


def test_claim_token_bound_to_claim_url_machine(key):
    d = dev(4)
    body = claim(device=d).json()
    p = claim_signing.verify(body["token"], key)
    assert p is not None
    assert p["cid"] == body["claimId"] and p["uh"] == [claim_signing.url_hash(URL)]
    assert p["dev"] == d[:32] and p["exp"] - p["iat"] == 2 * 3600
    assert claim_signing.token_matches(p, URL, d)
    assert not claim_signing.token_matches(p, URL + "x", d)          # wrong url
    assert not claim_signing.token_matches(p, URL, dev(5))           # wrong machine


def test_claim_batch_items_carry_tokens(key):
    urls = [f"https://www.tiktok.com/@a/video/{i}" for i in range(3)]
    b = client.post("/api/v1/client/quota/claim-batch", json={"items": [{"url": u} for u in urls]},
                    headers={"X-VG-Device": dev(6)}).json()
    for u, it in zip(urls, b["items"]):
        p = claim_signing.verify(it["token"], key)
        assert p["cid"] == it["claimId"] and p["uh"] == [claim_signing.url_hash(u)]


def test_no_machine_header_no_token(key):
    r = claim()
    assert r.status_code == 200 and "token" not in r.json()


def test_refused_claim_has_no_token(key, monkeypatch):
    monkeypatch.setenv("PLATFORM_DAILY_LIMIT_ANON", "1")
    d = dev(7)
    assert claim(url=URL + "&n=1", device=d).status_code == 200
    r = claim(url=URL + "&n=2", device=d)
    assert r.status_code == 429 and "token" not in r.json()


def test_tampered_other_key_expired_rejected(key):
    t = claim_signing.claim_token("c" * 24, [URL], dev(8), now=1_000_000)
    assert claim_signing.verify(t, key, now=1_000_000 + 10)
    body, sig = t.split(".")
    for i in (0, 5, len(body) - 2):
        assert claim_signing.verify(flip(body, i) + "." + sig, key, now=1_000_010) is None
    assert claim_signing.verify(body + "." + flip(sig, 3), key, now=1_000_010) is None
    _, other = new_key()
    assert claim_signing.verify(t, {"t1": other}, now=1_000_010) is None       # signed by another key
    assert claim_signing.verify(t, {"k9": key["t1"]}, now=1_000_010) is None   # unknown kid
    assert claim_signing.verify(t, key, now=1_000_000 + 2 * 3600) is None      # expired
    assert claim_signing.verify("garbage", key) is None


def test_server_route_token_only_absolute_links(key):
    t = claim_signing.server_route_token(["https://cdn.example/v.mp4", "/api/v1/download-local?file=x", None], dev(9))
    p = claim_signing.verify(t, key)
    assert p["uh"] == [claim_signing.url_hash("https://cdn.example/v.mp4")] and p["cid"].startswith("s")
    assert claim_signing.server_route_token(["/api/v1/download-local?file=x"], dev(9)) is None
    assert claim_signing.server_route_token(["https://cdn.example/v.mp4"], None) is None


def test_gen_script_key_round_trips(monkeypatch):
    script = pathlib.Path(__file__).resolve().parents[1] / "scripts/gen_claim_signing_key.py"
    out = subprocess.run([sys.executable, str(script), "k7"], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    env = dict(line.split("=", 1) for line in out.strip().splitlines())
    kid, pub = env["VIDGRAB_CLAIM_PUBKEYS"].split(":")
    assert kid == "k7" == env["CLIENT_QUOTA_SIGNING_KID"]
    monkeypatch.setenv("CLIENT_QUOTA_SIGNING_KEY", env["CLIENT_QUOTA_SIGNING_KEY"])
    monkeypatch.setenv("CLIENT_QUOTA_SIGNING_KID", kid)
    assert claim_signing.public_key_b64() == pub
    t = claim_signing.claim_token("abc", [URL], dev(1))
    assert claim_signing.verify(t, {"k7": base64.b64decode(pub)})


def test_cross_language_fixture_verifies():
    """The same file cargo test reads (claim_token.rs). Made once with a
    throwaway key by scripts/make_claim_p3_fixture.py; the seed is not kept."""
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pubs = {k: base64.b64decode(v) for k, v in fx["keys"].items()}
    p = claim_signing.verify(fx["token"], pubs, now=fx["now"])
    assert p and claim_signing.token_matches(p, fx["url"], fx["device"]) and p["cid"] == fx["cid"]
    pol = claim_signing.verify(fx["policy"], pubs, now=fx["now"])
    assert pol["requireToken"] is True and pol["grace"] == fx["grace"]
    assert claim_signing.verify(fx["token"], pubs, now=fx["now"] + 2 * 3600) is None
