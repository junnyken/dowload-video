"""
End-to-end regression tests for Step 3 backend paywall, quota, tier, and release fixes.
Runs purely in-process without external DB/Redis/network calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app, limiter
from tests._china_fakes import rc  # noqa: F401

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_overrides(monkeypatch, rc):
    monkeypatch.setattr("app.core.plans._r", lambda: rc)
    prev = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prev
    fastapi_app.dependency_overrides.clear()


def test_daily_cue_limit_for_tier():
    from app.api.transcript_translate import daily_cue_limit_for_tier

    assert daily_cue_limit_for_tier("free") == 0
    assert daily_cue_limit_for_tier("starter") == 2000
    assert daily_cue_limit_for_tier("pro") == 6000
    assert daily_cue_limit_for_tier("team") == 15000
    assert daily_cue_limit_for_tier("admin") == 15000


def test_asr_translate_enforces_paid_tier_and_starter_cue_limit(tmp_path, monkeypatch):
    from app.api.transcript_translate import resolve_identity
    from app.core.auth_middleware import get_optional_user

    srt_path = tmp_path / "asr.srt"
    srt_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    monkeypatch.setattr("app.api.transcript_translate._TRANSCRIPT_JOBS_DIR", str(tmp_path / "jobs"))

    # 1. Guest gets 402
    fastapi_app.dependency_overrides[get_optional_user] = lambda: None
    fastapi_app.dependency_overrides[resolve_identity] = lambda: None
    r_guest = client.post("/api/v1/transcript-asr/jobs/j1/translate", json={"target_lang": "vi"})
    assert r_guest.status_code == 402

    # 2. Free user gets 402
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-free"}
    fastapi_app.dependency_overrides[resolve_identity] = lambda: "u-free"
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda _uid: "free")
    r_free = client.post("/api/v1/transcript-asr/jobs/j1/translate", json={"target_lang": "vi"})
    assert r_free.status_code == 402

    # 3. Starter user reserves against p_limit=2000
    asr_db = MagicMock()
    asr_db.table.return_value.select.return_value.eq.return_value.single.return_value.execute.return_value.data = {
        "id": "j1", "user_id": "u-starter", "status": "done", "result_path": str(srt_path), "video_title": "Demo",
    }
    asr_db.rpc.return_value.execute.return_value.data = True
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-starter"}
    fastapi_app.dependency_overrides[resolve_identity] = lambda: "u-starter"
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda _uid: "starter")
    with patch("app.api.transcript_asr._get_db", return_value=asr_db), \
         patch("app.api.transcript_translate._get_translate_task", return_value=None):
        r_starter = client.post("/api/v1/transcript-asr/jobs/j1/translate", json={"target_lang": "vi"})
    assert r_starter.status_code == 200
    assert asr_db.rpc.call_args[0][1]["p_limit"] == 2000


def test_bulk_zip_rename_enforces_bulk_zip_gate(monkeypatch):
    from app.core.auth_middleware import get_optional_user

    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-free"}
    monkeypatch.setattr("app.core.quotas._get_tier", lambda _uid: "free")
    r_free = client.post("/api/v1/bulk-zip-rename", json={"batch_id": "b1", "pattern": "{title}"})
    assert r_free.status_code == 402
    detail = r_free.json()["detail"]
    assert detail["required_tier"] == "starter"
    assert detail["upgrade_url"] == "/pricing"

    # Starter passes the paywall and proceeds to query the batch (which returns 404 when empty)
    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.eq.return_value.execute.return_value.data = []
    fastapi_app.dependency_overrides[get_optional_user] = lambda: {"id": "u-starter"}
    monkeypatch.setattr("app.core.quotas._get_tier", lambda _uid: "starter")
    monkeypatch.setattr("app.api.batch_rename.get_service_client", lambda: fake_db)
    r_starter = client.post("/api/v1/bulk-zip-rename", json={"batch_id": "b1", "pattern": "{title}"})
    assert r_starter.status_code == 404


def test_package_zip_allows_starter_and_blocks_free(monkeypatch):
    from app.api.processing import _auth_optional_user

    fastapi_app.dependency_overrides[_auth_optional_user] = lambda: {"id": "u-free"}
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda _uid: "free")
    r_free = client.post("/api/v1/process/package-zip", json={"image_urls": ["https://cdn.example.com/1.jpg"]})
    assert r_free.status_code == 402

    fastapi_app.dependency_overrides[_auth_optional_user] = lambda: {"id": "u-starter"}
    monkeypatch.setattr("app.core.quotas.get_user_tier", lambda _uid: "starter")
    # Empty payload reaches payload validation (400), proving the tier gate passed
    r_starter = client.post("/api/v1/process/package-zip", json={"paths": [], "image_urls": []})
    assert r_starter.status_code == 400


def test_smart_analysis_team_limits_and_single_quota_check(monkeypatch):
    from app.api import smart_analysis as sa

    assert sa.team_limit() == 200
    assert sa.MAX_DURATION_TEAM_S == 10800.0

    calls = []
    monkeypatch.setattr(sa, "_check_analysis_quota", lambda user, req: calls.append(user) or (True, ""))
    monkeypatch.setattr(sa, "_get_db", lambda: None)
    monkeypatch.setattr(sa, "_get_analyze_task", lambda: SimpleNamespace(apply_async=lambda *a, **k: None))
    fastapi_app.dependency_overrides[sa.get_optional_user] = lambda: {"id": "u-team", "tier": "team"}

    r = client.post("/api/v1/analyze-media", json={"url": "https://cdn.example.com/movie.mp4", "duration_hint_s": 9000})
    assert r.status_code == 200
    assert len(calls) == 1


def test_storage_schedule_playlist_team_and_admin_unlimited():
    from app.api import playlists, schedule, storage

    for limits in (storage._PIN_LIMITS, schedule._SCHEDULE_LIMITS, playlists._PLAYLIST_LIMITS):
        assert limits["team"] == -1
        assert limits["enterprise"] == -1
        assert limits["admin"] == -1

    # _check_limit does not raise 402/403 when limit == -1
    schedule._check_limit(MagicMock(), "u-team", "team")
    schedule._check_limit(MagicMock(), "u-admin", "admin")


def test_user_me_returns_effective_tier_when_expired(monkeypatch):
    from app.core.auth_middleware import get_required_user

    expired = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": "u-exp", "tier": "pro", "billing_status": "manual", "subscription_expiry": expired}
    ]
    fastapi_app.dependency_overrides[get_required_user] = lambda: {"id": "u-exp", "email": "exp@example.com"}
    monkeypatch.setattr("app.api.user.get_supabase_client", lambda: fake_db)
    monkeypatch.setattr("app.core.entitlements.is_user_admin_db", lambda *a, **k: False)
    monkeypatch.setattr("app.api.flow_cleanup._get_flow_access_mode", lambda: "off")

    r = client.get("/api/v1/user/me")
    assert r.status_code == 200
    assert r.json()["tier"] == "free"
