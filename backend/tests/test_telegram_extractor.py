"""
Tests for Phase B1 — Telegram Extractor & User MTProto Auth
===========================================================
All HTTP and MTProto calls are mocked; no live network requests are made.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import telegram_auth
from app.core.error_codes import ERROR_META
from app.core.extraction_errors import classify_extraction_error
from app.core.source_classifier import classify
from app.services import telegram_extractor as tg
from app.services.extractor_registry import REGISTRY


@pytest.fixture(autouse=True)
def _clean_tg_state(monkeypatch):
    """Isolate in-memory sessions and disable real HTTP/MTProto calls."""
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("TELEGRAM_API_ID", "123456")
    monkeypatch.setenv("TELEGRAM_API_HASH", "abcdef1234567890abcdef1234567890")
    telegram_auth._MEMORY_SESSIONS.clear()
    telegram_auth._MEMORY_PENDING.clear()
    yield
    telegram_auth._MEMORY_SESSIONS.clear()
    telegram_auth._MEMORY_PENDING.clear()


# ── URL Detection & Parsing ────────────────────────────────────────────────

def test_parse_telegram_urls():
    p1 = tg.parse_telegram_url("https://t.me/durov/256")
    assert p1 is not None
    assert p1.kind == "public_post"
    assert p1.channel == "durov"
    assert p1.msg_id == 256

    p2 = tg.parse_telegram_url("https://t.me/s/telegram/102")
    assert p2 is not None
    assert p2.kind == "public_post"
    assert p2.channel == "telegram"
    assert p2.msg_id == 102

    p3 = tg.parse_telegram_url("https://t.me/s/telegram")
    assert p3 is not None
    assert p3.kind == "public_channel"
    assert p3.channel == "telegram"

    p4 = tg.parse_telegram_url("https://t.me/c/1987654321/42")
    assert p4 is not None
    assert p4.kind == "private_post"
    assert p4.chat_id == 1987654321
    assert p4.msg_id == 42

    p5 = tg.parse_telegram_url("https://telegram.me/durov/99")
    assert p5 is not None
    assert p5.kind == "public_post"
    assert p5.channel == "durov"
    assert p5.msg_id == 99


def test_registry_and_classifier_integration():
    ext = REGISTRY.detect("https://t.me/durov/256")
    assert ext.platform_name == "telegram"

    batch_ext = REGISTRY.detect("https://t.me/s/durov", batch=True)
    assert batch_ext.platform_name == "telegram"

    c_single = classify("https://t.me/durov/256")
    assert c_single.platform == "telegram"
    assert c_single.source_type == "single_video"
    assert c_single.normalized_id == "256"

    c_chan = classify("https://t.me/s/durov")
    assert c_chan.platform == "telegram"
    assert c_chan.source_type == "channel"
    assert c_chan.normalized_id == "durov"


# ── Public Web Embed Extraction (No Login Required) ────────────────────────

SAMPLE_PUBLIC_EMBED_HTML = """
<!DOCTYPE html>
<html>
<body>
<div class="tgme_widget_message" data-post="durov/256">
  <div class="tgme_widget_message_owner_name">Pavel Durov</div>
  <a class="tgme_widget_message_video_player" href="https://t.me/durov/256">
    <i class="tgme_widget_message_video_thumb" style="background-image:url('https://cdn4.telesco.pe/file/thumb123.jpg')"></i>
    <video src="https://cdn4.telesco.pe/file/video256.mp4" controls></video>
    <time class="tgme_widget_message_video_duration">01:25</time>
  </a>
  <div class="tgme_widget_message_text">Hello from Telegram!<br>Second line</div>
</div>
</body>
</html>
"""


def test_public_embed_video_extraction():
    def fake_get(url: str) -> httpx.Response:
        assert "embed=1" in url
        return httpx.Response(200, text=SAMPLE_PUBLIC_EMBED_HTML)

    ext = tg.TelegramExtractor()
    res = ext.extract_single("https://t.me/durov/256", http_get=fake_get)
    assert res.ok
    assert res.platform == "telegram"
    assert res.direct_url == "https://cdn4.telesco.pe/file/video256.mp4"
    assert res.thumbnail == "https://cdn4.telesco.pe/file/thumb123.jpg"
    assert res.title == "Hello from Telegram!"
    assert res.author == "Pavel Durov"
    assert res.duration_ms == 85000
    assert len(res.formats) == 1
    assert res.formats[0].url == "https://cdn4.telesco.pe/file/video256.mp4"


# ── Public Channel Batch Extraction (`t.me/s/<channel>`) ───────────────────

SAMPLE_CHANNEL_PREVIEW_HTML = """
<html>
<body>
  <div class="tgme_channel_info_header_title">Durov's Channel</div>
  <div class="tgme_widget_message" data-post="durov/101"></div>
  <div class="tgme_widget_message" data-post="durov/102"></div>
  <div class="tgme_widget_message" data-post="durov/103"></div>
</body>
</html>
"""


def test_public_channel_batch_extraction():
    def fake_get(url: str) -> httpx.Response:
        assert url == "https://t.me/s/durov"
        return httpx.Response(200, text=SAMPLE_CHANNEL_PREVIEW_HTML)

    ext = tg.TelegramExtractor()
    batch = ext.extract_batch("https://t.me/s/durov", limit=2, http_get=fake_get)
    assert batch.ok
    assert batch.channel_title == "Durov's Channel"
    assert len(batch.entries) == 2
    assert batch.entries[0].url == "https://t.me/durov/101"
    assert batch.entries[1].url == "https://t.me/durov/102"


# ── Protected Content (`noforwards`) & Private Login Enforcement ──────────

def test_private_channel_without_login_raises_telegram_login_required():
    ext = tg.TelegramExtractor()
    with pytest.raises(ValueError, match="telegram_login_required") as exc_info:
        ext.extract_single("https://t.me/c/1987654321/42")

    status, code = classify_extraction_error(str(exc_info.value), exc_info.value)
    assert status == 422
    assert code == "telegram_login_required"
    assert "telegram_login_required" in ERROR_META


def test_noforwards_in_mtproto_refuses_with_telegram_protected_content(monkeypatch):
    # Store a valid encrypted user session
    telegram_auth.save_user_session("default", {
        "session_str": "1BVtsOHwBu_mock_session_string_123456789",
        "phone_masked": "+8490****567",
        "username": "tester",
        "api_id": 123456,
        "api_hash": "abcdef1234567890",
    })

    async def fake_inspect(info, session_record):
        # Simulate message.noforwards = True or chat.noforwards = True
        raise tg.TelegramExtractionError("telegram_protected_content")

    monkeypatch.setattr(tg, "inspect_message_mtproto", fake_inspect)

    ext = tg.TelegramExtractor()
    with pytest.raises(ValueError, match="telegram_protected_content") as exc_info:
        ext.extract_single("https://t.me/c/1987654321/42")

    status, code = classify_extraction_error(str(exc_info.value), exc_info.value)
    assert status == 422
    assert code == "telegram_protected_content"
    assert "telegram_protected_content" in ERROR_META


def test_embed_noforwards_html_refuses_with_telegram_protected_content():
    protected_html = """
    <div class="tgme_widget_message tgme_noforwards" data-post="secretchan/99">
      <div class="tgme_widget_message_error"> Copying and forwarding is not allowed in this channel </div>
    </div>
    """

    def fake_get(url: str) -> httpx.Response:
        return httpx.Response(200, text=protected_html)

    ext = tg.TelegramExtractor()
    with pytest.raises(ValueError, match="telegram_protected_content"):
        ext.extract_single("https://t.me/secretchan/99", http_get=fake_get)


# ── User MTProto Auth Endpoints & Encryption at Rest ──────────────────────

def test_telegram_auth_router_flow_and_encryption(monkeypatch):
    app = FastAPI()
    app.include_router(telegram_auth.router, prefix="/api/v1/telegram/auth")
    client = TestClient(app)

    # 1. Check initial status
    r0 = client.get("/api/v1/telegram/auth/status")
    assert r0.status_code == 200
    assert r0.json()["connected"] is False
    assert r0.json()["server_configured"] is True

    # 2. Mock mtproto_send_code
    async def fake_send_code(phone: str, api_id: int, api_hash: str) -> dict:
        return {
            "phone_code_hash": "hash_abc_123",
            "temp_session": "TEMP_RAW_STRING_SESSION_XYZ",
        }

    monkeypatch.setattr(telegram_auth, "mtproto_send_code", fake_send_code)

    r_send = client.post("/api/v1/telegram/auth/send-code", json={"phone": "+84901234567"})
    assert r_send.status_code == 200
    assert r_send.json()["status"] == "code_sent"
    assert r_send.json()["phone_masked"] == "+849****567"

    # 3. Mock 2FA required first when password is not supplied, then success with password
    raw_secret_session = "1BVtsOHwBu_SUPER_SECRET_TELETHON_SESSION_987654321"

    async def fake_verify_code(*, phone, code, phone_code_hash, temp_session, api_id, api_hash, password=None):
        assert phone_code_hash == "hash_abc_123"
        assert temp_session == "TEMP_RAW_STRING_SESSION_XYZ"
        if not password:
            raise telegram_auth.Telegram2FARequired("2FA password needed")
        assert password == "my_cloud_2fa"
        return {
            "session_str": raw_secret_session,
            "username": "vidgrab_user",
            "user_id": 777888,
            "phone": phone,
        }

    monkeypatch.setattr(telegram_auth, "mtproto_verify_code", fake_verify_code)

    # Without password -> 422 requires_2fa
    r_2fa = client.post("/api/v1/telegram/auth/verify-code", json={
        "phone": "+84901234567",
        "code": "54321",
    })
    assert r_2fa.status_code == 422
    assert r_2fa.json()["detail"]["requires_2fa"] is True

    # With 2FA password -> 200 connected
    r_ver = client.post("/api/v1/telegram/auth/verify-code", json={
        "phone": "+84901234567",
        "code": "54321",
        "password": "my_cloud_2fa",
    })
    assert r_ver.status_code == 200
    assert r_ver.json()["connected"] is True
    assert r_ver.json()["username"] == "vidgrab_user"

    # Verify encryption at rest: raw stored string must NOT contain plaintext session
    raw_stored = telegram_auth.get_raw_stored_session("default")
    assert raw_stored is not None
    assert raw_secret_session not in raw_stored
    assert raw_stored.startswith(("enc:tg:v1:", "enc:v1:"))

    # Status endpoint never leaks raw session_str
    r_st = client.get("/api/v1/telegram/auth/status")
    assert r_st.status_code == 200
    st_data = r_st.json()
    assert st_data["connected"] is True
    assert st_data["session_last4"] == "4321"
    assert raw_secret_session not in r_st.text

    # 4. Logout revokes and deletes session
    r_out = client.post("/api/v1/telegram/auth/logout")
    assert r_out.status_code == 200
    assert r_out.json()["connected"] is False
    assert telegram_auth.get_raw_stored_session("default") is None
