"""
Phase P5.5 tests — Worker Concurrency Cap, Consent Audit Log & Consent Rejection Guard
======================================================================================
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.main import app
from app.tasks import media_tasks
from benchmarks.generate_hardening_fixtures import HARDENING_DIR, generate_all_hardening_fixtures


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user():
    async def _pro():
        return {"id": "pro-user-p55", "email": "pro55@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


def test_worker_concurrency_default_is_2():
    """P5.5: Default worker concurrency for the `media` queue is 2 (`FLOW_INPAINT_WORKER_CONCURRENCY=2`)."""
    assert fc._WORKER_CONCURRENCY == 2
    assert media_tasks.FLOW_INPAINT_WORKER_CONCURRENCY == 2


def test_process_consent_required_and_audit_log_written(tmp_path, monkeypatch, pro_user):
    """
    P5.5:
    - `POST /process` with `consent_confirmed=False` is rejected with HTTP 400 (`consent_required`).
    - `POST /process` with `consent_confirmed=True` appends `(timestamp, user_id, temp_id, action="process_confirm")`
      to `downloads/flow_consent_audit.jsonl` without storing video bytes or filenames.
    """
    dl_dir = tmp_path / "dl_p55"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
    monkeypatch.setenv("FLOW_CLEANUP_ALLOW_INPROCESS", "1")

    fixtures = generate_all_hardening_fixtures(HARDENING_DIR)
    client = TestClient(app)

    with open(fixtures["h264_aac"], "rb") as f:
        up = client.post("/api/v1/flow-cleanup/upload", files={"file": ("secret_clip_name.mp4", f, "video/mp4")})
    assert up.status_code == 200
    tid = up.json()["temp_id"]

    audit_file = dl_dir / "flow_consent_audit.jsonl"

    # 1. Unchecked consent -> HTTP 400 (`consent_required`), no audit log entry written
    r_deny = client.post(
        "/api/v1/flow-cleanup/process",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": {"x": 20, "y": 20, "w": 40, "h": 24},
            "method": "crop",
            "region_adjusted": True,
            "consent_confirmed": False,
        },
    )
    assert r_deny.status_code == 400
    assert r_deny.json()["detail"]["error_code"] == "consent_required"
    assert not audit_file.exists()

    # 2. Confirmed consent -> HTTP 200 and audit log entry written without video filename/bytes
    r_ok = client.post(
        "/api/v1/flow-cleanup/process",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": {"x": 20, "y": 20, "w": 40, "h": 24},
            "method": "crop",
            "region_adjusted": True,
            "consent_confirmed": True,
        },
    )
    assert r_ok.status_code == 200
    assert audit_file.is_file()

    lines = [line.strip() for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert set(entry.keys()) == {"timestamp", "user_id", "temp_id", "action"}
    assert entry["user_id"] == "pro-user-p55"
    assert entry["temp_id"] == tid
    assert entry["action"] == "process_confirm"
    assert "secret_clip_name" not in lines[0]


def test_e2e_pro_full_lifecycle_detect_track_keyframe_ab_clip_process_download_and_ttl(tmp_path, monkeypatch, pro_user):
    """
    P5.5 E2E Scenario 1 (Pro User Full Lifecycle):
      login (Pro) -> upload -> auto-detect -> track -> keyframe edit -> A/B preview frame (signed URL)
      -> 4s preview clip (signed URL) -> process (with consent audit log) -> download result -> TTL expiry returns 404.
    """
    from datetime import datetime, timedelta, timezone

    dl_dir = tmp_path / "dl_e2e_pro"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
    monkeypatch.setenv("FLOW_CLEANUP_ALLOW_INPROCESS", "1")

    fixtures = generate_all_hardening_fixtures(HARDENING_DIR)
    client = TestClient(app)

    # 1. Upload
    with open(fixtures["h264_aac"], "rb") as f:
        r_up = client.post("/api/v1/flow-cleanup/upload", files={"file": ("pro_clip.mp4", f, "video/mp4")})
    assert r_up.status_code == 200, r_up.text
    up_data = r_up.json()
    tid = up_data["temp_id"]
    signed_frame_url = up_data["preview_url"]
    assert client.get(signed_frame_url).status_code == 200

    # 2. Auto-detect
    r_det = client.post(f"/api/v1/flow-cleanup/detect/{tid}")
    assert r_det.status_code == 200, r_det.text
    det_data = r_det.json()
    assert "regions" in det_data

    # 3. Track
    box = {"x": 240, "y": 130, "w": 60, "h": 34}
    r_trk = client.post(f"/api/v1/flow-cleanup/track/{tid}", json={"region": box})
    assert r_trk.status_code == 200, r_trk.text
    trk_data = r_trk.json()
    assert "boxes_by_frame" in trk_data

    # 4. Keyframe edit & re-track
    r_kf = client.post(
        f"/api/v1/flow-cleanup/track/{tid}",
        json={"region": box, "keyframes": [{"frame_idx": 5, "x": 238, "y": 128, "w": 62, "h": 36}]},
    )
    assert r_kf.status_code == 200, r_kf.text

    # 5. A/B preview frame + signed URL
    r_pf = client.post(
        "/api/v1/flow-cleanup/preview-frame",
        json={"temp_id": tid, "preset": "custom", "region": box, "method": "natural", "expand_padding": 6},
    )
    assert r_pf.status_code == 200, r_pf.text
    signed_clean_url = r_pf.json()["preview_clean_url"]
    assert client.get(signed_clean_url).status_code == 200

    # 6. 4s preview clip + signed URL
    r_pc = client.post(
        "/api/v1/flow-cleanup/preview-clip",
        json={"temp_id": tid, "preset": "custom", "region": box, "method": "natural", "clip_seconds": 1.5},
    )
    assert r_pc.status_code == 200, r_pc.text
    signed_clip_url = r_pc.json()["preview_clip_url"]
    assert client.get(signed_clip_url).status_code == 200

    # 7. Process with consent confirmed
    r_proc = client.post(
        "/api/v1/flow-cleanup/process",
        json={
            "temp_id": tid,
            "preset": "custom",
            "region": box,
            "method": "natural",
            "region_adjusted": True,
            "consent_confirmed": True,
        },
    )
    assert r_proc.status_code == 200, r_proc.text
    proc_data = r_proc.json()
    assert proc_data["status"] == "done"
    cleaned_file_id = proc_data["cleaned_file_id"]

    # 8. Download cleaned video
    r_dl = client.get(f"/api/v1/flow-cleanup/result/{tid}/{cleaned_file_id}")
    assert r_dl.status_code == 200
    assert len(r_dl.content) > 1000

    # 9. Simulate TTL expiration -> returns 404 everywhere & deletes work_dir
    work_dir = fc._safe_work_dir(tid)
    expired_ts = (datetime.now(timezone.utc) - timedelta(seconds=fc._FLOW_TTL_SEC + 60)).isoformat()
    fc._write_meta(work_dir, {"created_at": expired_ts, "processed_at": expired_ts, "status": "completed"})

    assert client.get(f"/api/v1/flow-cleanup/status/{tid}").status_code == 404
    assert client.get(f"/api/v1/flow-cleanup/result/{tid}/{cleaned_file_id}").status_code == 404
    assert client.get(signed_frame_url).status_code == 404
    assert not os.path.exists(work_dir)


def test_e2e_free_user_receives_402_upgrade_prompt(tmp_path, monkeypatch):
    """P5.5 E2E Scenario 2 (Free User): Free user receives HTTP 402 (`tier_required_feature`) with `required_plan='pro'`."""
    async def _free():
        return {"id": "free-user-p55", "email": "free55@test.local", "tier": "free"}

    app.dependency_overrides[fc.get_optional_user] = _free
    try:
        dl_dir = tmp_path / "dl_e2e_free"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
        fixtures = generate_all_hardening_fixtures(HARDENING_DIR)
        client = TestClient(app)

        with open(fixtures["h264_aac"], "rb") as f:
            r_up = client.post("/api/v1/flow-cleanup/upload", files={"file": ("clip.mp4", f, "video/mp4")})
        assert r_up.status_code == 402
        detail = r_up.json()["detail"]
        assert detail["error_code"] == "tier_required_feature"
        assert detail["required_plan"] == "pro"
    finally:
        app.dependency_overrides.pop(fc.get_optional_user, None)
