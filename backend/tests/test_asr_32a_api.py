"""Phase 32A — POST /transcript-asr/jobs check order (flag → quota → global
ceiling → disk guard → provider), refunds, formats, cancel, and the admin
ASR routes. No network, no real Redis/Supabase."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from app.services.asr import budget
from app.services.asr.types import ProviderBadOutput, Segment, TranscriptResult
from tests._asr_fakes import PINNED_NOW, asr_env  # noqa: F401 (fixture)

client = TestClient(fastapi_app, raise_server_exceptions=False)


@pytest.fixture
def identity():
    from app.api.transcript_translate import resolve_identity, resolve_quota_subject
    fastapi_app.dependency_overrides[resolve_identity] = lambda: "user-1"
    fastapi_app.dependency_overrides[resolve_quota_subject] = lambda: "user-1"
    yield "user-1"
    fastapi_app.dependency_overrides.pop(resolve_identity, None)
    fastapi_app.dependency_overrides.pop(resolve_quota_subject, None)


@pytest.fixture
def provider_spy(monkeypatch):
    """Records every provider construction — the paid call can only happen
    through a constructed provider, so 'never constructed' = 'never paid'."""
    import app.services.asr as asr_pkg
    real = asr_pkg.get_provider
    calls = []

    def spy(name=None):
        calls.append(name)
        return real(name)

    monkeypatch.setattr(asr_pkg, "get_provider", spy)
    return calls


def _ffprobe(sec):
    r = MagicMock()
    r.returncode, r.stdout, r.stderr = 0, str(sec), ""
    return r


def _post(asr_env, video, *, duration=120, task=None, disk_ok=True):
    disk = patch("app.api.routes._preflight_disk_check") if disk_ok else patch(
        "app.api.routes._preflight_disk_check",
        side_effect=HTTPException(status_code=507, detail={"error_code": "disk_full"}),
    )
    if task is None:
        task = MagicMock()
        task.apply_async.return_value.id = "celery-1"
    with patch("app.api.processing._guard_local_path", return_value=str(video)), \
         patch("subprocess.run", return_value=_ffprobe(duration)), \
         patch("app.api.transcript_asr._get_db", return_value=asr_env.db), \
         patch("app.api.transcript_asr._get_transcribe_task", return_value=task), disk:
        return client.post("/api/v1/transcript-asr/jobs", json={"video_local_path": "fid", "video_title": "T"})


@pytest.fixture
def video(tmp_path):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    return v


def _enable(monkeypatch, key=True):
    monkeypatch.setenv("ASR_ENABLED", "true")
    if key:
        monkeypatch.setenv("GEMINI_API_KEY", "k")


# ── check order ───────────────────────────────────────────────────────────

def test_flag_off_refuses_before_anything(asr_env, identity, video, provider_spy, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")   # everything else would let the job through
    task = MagicMock()
    resp = _post(asr_env, video, task=task)
    assert resp.status_code == 503
    assert resp.json()["error_code"] == "asr_disabled"
    assert isinstance(resp.json()["detail"], str)  # current frontend renders detail as text
    assert provider_spy == []
    assert asr_env.db.rpc_calls == []
    task.apply_async.assert_not_called()
    assert budget.snapshot()["spend_usd"] == 0


def test_flag_off_keeps_get_endpoints_working(asr_env, identity):
    asr_env.db.tables["transcript_asr_jobs"] = [
        {"id": "j1", "user_id": "user-1", "status": "failed", "error_message": "[provider_bad_output] Lỗi X"},
    ]
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        resp = client.get("/api/v1/transcript-asr/jobs")
    assert resp.status_code == 200
    job = resp.json()["jobs"][0]
    assert job["error_code"] == "provider_bad_output" and job["error"] == "Lỗi X"


def test_quota_exceeded_never_reaches_budget_or_provider(asr_env, identity, video, provider_spy, monkeypatch):
    _enable(monkeypatch)
    asr_env.db.rpc_handlers["reserve_transcript_asr_usage"] = lambda p: False
    resp = _post(asr_env, video)
    assert resp.status_code == 422
    assert provider_spy == []
    assert budget.snapshot()["spend_usd"] == 0


def test_ceiling_reached_refunds_quota_and_alerts_once(asr_env, identity, video, provider_spy, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("ASR_SPEND_CEILING_USD", "0.004")   # 2 min * 0.003 = 0.006 > ceiling
    r1 = _post(asr_env, video)
    r2 = _post(asr_env, video)
    assert r1.status_code == r2.status_code == 503
    assert r1.json()["error_code"] == "spend_ceiling_reached"
    assert provider_spy == []
    assert len(asr_env.db.calls("refund_transcript_asr_usage")) == 2
    assert asr_env.db.calls("refund_transcript_asr_usage")[0]["p_minutes"] == 2.0
    assert budget.snapshot()["spend_usd"] == 0                  # reservation rolled back
    assert len(asr_env.alerts) == 1                             # deduped per day


def test_global_minutes_cap(asr_env, identity, video, provider_spy, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("ASR_GLOBAL_DAILY_MINUTES_CAP", "1")
    resp = _post(asr_env, video, duration=120)
    assert resp.json()["error_code"] == "spend_ceiling_reached"
    assert provider_spy == []


def test_killswitch_refuses_and_refunds(asr_env, identity, video, provider_spy, monkeypatch):
    _enable(monkeypatch)
    budget.set_killswitch(True)
    resp = _post(asr_env, video)
    assert resp.status_code == 503 and resp.json()["error_code"] == "asr_paused"
    assert provider_spy == []
    assert len(asr_env.db.calls("refund_transcript_asr_usage")) == 1


def test_redis_down_fails_closed(asr_env, identity, video, provider_spy, monkeypatch):
    _enable(monkeypatch)

    def boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(budget, "_r", boom)
    resp = _post(asr_env, video)
    assert resp.status_code == 503 and resp.json()["error_code"] == "budget_unavailable"
    assert provider_spy == []


def test_disk_guard_refunds_and_releases(asr_env, identity, video, provider_spy, monkeypatch):
    _enable(monkeypatch)
    resp = _post(asr_env, video, disk_ok=False)
    assert resp.status_code == 507
    assert provider_spy == []
    assert len(asr_env.db.calls("refund_transcript_asr_usage")) == 1
    snap = budget.snapshot()
    assert snap["spend_usd"] == 0 and snap["minutes"] == 0


def test_provider_unavailable_is_clean_and_refunds(asr_env, identity, video, monkeypatch):
    _enable(monkeypatch, key=False)
    resp = _post(asr_env, video)
    assert resp.status_code == 503 and resp.json()["error_code"] == "provider_unavailable"
    assert len(asr_env.db.calls("refund_transcript_asr_usage")) == 1
    assert budget.snapshot()["spend_usd"] == 0
    assert asr_env.db.tables.get("transcript_asr_jobs", []) == []


def test_queue_failure_marks_failed_refunds_and_errors(asr_env, identity, video, monkeypatch):
    _enable(monkeypatch)
    task = MagicMock()
    task.apply_async.side_effect = ConnectionError("broker down")
    resp = _post(asr_env, video, task=task)
    assert resp.status_code == 503 and resp.json()["error_code"] == "queue_unavailable"
    [row] = asr_env.db.tables["transcript_asr_jobs"]
    assert row["status"] == "failed" and row["error_message"].startswith("[queue_unavailable]")
    assert len(asr_env.db.calls("refund_transcript_asr_usage")) == 1
    snap = budget.snapshot()
    assert snap["spend_usd"] == 0 and snap["minutes"] == 0


def test_happy_create_reserves_budget_and_records_meta(asr_env, identity, video, monkeypatch):
    _enable(monkeypatch)
    resp = _post(asr_env, video, duration=120)
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["id"]
    meta = budget.meta_get(job_id)
    assert meta["provider"] == "gemini" and meta["quota_key"] == "user-1"
    assert float(meta["est_cost"]) == pytest.approx(0.006)
    assert meta["usage_date"] == PINNED_NOW.date().isoformat()
    assert budget.snapshot()["spend_usd"] == pytest.approx(0.006)
    assert asr_env.db.tables["transcript_asr_jobs"][0]["celery_task_id"] == "celery-1"


# ── formats ───────────────────────────────────────────────────────────────

SRT = "1\n00:00:01,000 --> 00:00:02,500\nXin chào\n\n2\n00:00:03,000 --> 00:00:04,000\nCảm ơn\n"


@pytest.fixture
def done_job(asr_env, tmp_path):
    p = tmp_path / "asr_result_x.srt"
    p.write_text(SRT, encoding="utf-8")
    asr_env.db.tables["transcript_asr_jobs"] = [{
        "id": "j1", "user_id": "user-1", "status": "done", "result_path": str(p),
        "video_title": "Video", "detected_language": "vi", "duration_sec": 5,
    }]
    return p


def _download(asr_env, fmt=None):
    url = "/api/v1/transcript-asr/jobs/j1/download" + (f"?format={fmt}" if fmt else "")
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        return client.get(url)


def test_download_default_is_srt(asr_env, identity, done_job):
    r = _download(asr_env)
    assert r.status_code == 200 and r.text == SRT


def test_download_vtt(asr_env, identity, done_job):
    r = _download(asr_env, "vtt")
    assert r.status_code == 200
    assert r.text.startswith("WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.500\nXin chào")
    assert "," not in r.text.split("\n")[3]
    assert r.headers["content-type"].startswith("text/vtt")


def test_download_txt(asr_env, identity, done_job):
    r = _download(asr_env, "txt")
    assert r.text == "Xin chào\nCảm ơn\n"


def test_download_json_from_sidecar_or_srt(asr_env, identity, done_job):
    r = _download(asr_env, "json")
    data = r.json()
    assert data["segments"][0] == {"start": 1.0, "end": 2.5, "text": "Xin chào"}
    sidecar = done_job.with_suffix(".json")
    sidecar.write_text(json.dumps({"segments": [{"start": 1, "end": 2, "text": "s", "speaker": "S1"}]}), encoding="utf-8")
    assert _download(asr_env, "json").json()["segments"][0]["speaker"] == "S1"


def test_download_rejects_unknown_format(asr_env, identity, done_job):
    assert _download(asr_env, "docx").status_code == 422


# ── cancel = delete of an unfinished job ──────────────────────────────────

def test_delete_queued_job_refunds_once(asr_env, identity):
    asr_env.db.tables["transcript_asr_jobs"] = [
        {"id": "j1", "user_id": "user-1", "status": "queued", "duration_sec": 120,
         "created_at": PINNED_NOW.isoformat(), "celery_task_id": None},
    ]
    budget.meta_set("j1", quota_key="ip:1.2.3.4", minutes="2", usage_date="2030-01-15",
                    spend_day=budget.day_key(), est_cost="0.006")
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        assert client.delete("/api/v1/transcript-asr/jobs/j1").status_code == 200
    refunds = asr_env.db.calls("refund_transcript_asr_usage")
    assert refunds == [{"p_user_id": "ip:1.2.3.4", "p_minutes": 2.0, "p_usage_date": "2030-01-15"}]
    assert asr_env.db.tables["transcript_asr_jobs"] == []


def test_refund_degrades_when_migration_missing(asr_env, identity):
    """Without migration 033 the RPC does not exist: cancel still works."""
    del asr_env.db.rpc_handlers["refund_transcript_asr_usage"]
    asr_env.db.tables["transcript_asr_jobs"] = [
        {"id": "j1", "user_id": "user-1", "status": "queued", "duration_sec": 60, "created_at": PINNED_NOW.isoformat()},
    ]
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        assert client.delete("/api/v1/transcript-asr/jobs/j1").status_code == 200


# ── admin ─────────────────────────────────────────────────────────────────

ADMIN_ROUTES = [
    ("get", "/api/v1/admin/asr/summary", None),
    ("post", "/api/v1/admin/asr/killswitch", {"on": True}),
    ("post", "/api/v1/admin/asr/selftest", {"file_id": "x"}),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_admin_routes_require_admin(asr_env, method, path, body):
    r = getattr(client, method)(path, **({"json": body} if body else {}))
    assert r.status_code == 401
    r = getattr(client, method)(path, headers={"Authorization": "Bearer nope"}, **({"json": body} if body else {}))
    assert r.status_code == 401
    assert budget.snapshot()["killswitch"] is False


@pytest.fixture
def admin(monkeypatch):
    # The dependency object the routes were built with — other test files
    # importlib.reload(app.api.admin), so app.api.admin.verify_admin may be a
    # different function object by now.
    from app.api.admin_asr import verify_admin
    audits = []
    fastapi_app.dependency_overrides[verify_admin] = lambda: None
    monkeypatch.setattr("app.api.admin_asr.log_admin_action", lambda req, action, **kw: audits.append((action, kw)))
    yield audits
    fastapi_app.dependency_overrides.pop(verify_admin, None)


def test_killswitch_toggle_is_audited(asr_env, admin):
    assert client.post("/api/v1/admin/asr/killswitch", json={"on": True}).json()["killswitch"] is True
    assert budget.killswitch_on() is True
    client.post("/api/v1/admin/asr/killswitch", json={"on": False})
    assert budget.killswitch_on() is False
    assert [a for a, _ in admin] == ["admin.asr.killswitch", "admin.asr.killswitch"]
    assert admin[0][1]["metadata"] == {"on": True}


def test_summary_shape(asr_env, admin):
    today = PINNED_NOW.isoformat()
    asr_env.db.tables["transcript_asr_jobs"] = [
        {"id": "a", "status": "done", "duration_sec": 120, "created_at": today,
         "updated_at": PINNED_NOW.replace(minute=2).isoformat(), "error_message": None},
        {"id": "b", "status": "failed", "duration_sec": 60, "created_at": today,
         "updated_at": today, "error_message": "[provider_bad_output] x"},
        {"id": "c", "status": "queued", "duration_sec": 60, "created_at": today, "updated_at": today},
        {"id": "old", "status": "done", "duration_sec": 600, "created_at": "2030-01-14T23:00:00+00:00",
         "updated_at": "2030-01-14T23:10:00+00:00"},
    ]
    budget.meta_set("a", provider="gemini", est_cost="0.006", actual_cost="0.006", started_ts="100", finished_ts="130")
    budget.meta_set("b", provider="whisper", est_cost="0.006", actual_cost="0.006")
    budget.reserve(0.012, 3)
    with patch("app.api.admin_asr._get_db", return_value=asr_env.db):
        body = client.get("/api/v1/admin/asr/summary").json()
    assert body["jobs_today"] == 3
    assert body["jobs_by_status"] == {"done": 1, "failed": 1, "queued": 1}
    assert body["failure_rate"] == 0.5
    assert body["failures_by_code"] == {"provider_bad_output": 1}
    assert body["avg_processing_sec"] == 30.0 and body["avg_turnaround_sec"] == 120.0
    assert body["spend_today_usd"] == pytest.approx(0.012) and body["spend_ceiling_usd"] == 3.0
    assert body["per_provider"]["gemini"]["done"] == 1 and body["per_provider"]["whisper"]["failed"] == 1
    assert body["enabled"] is False and body["provider"] == "gemini" and body["killswitch"] is False


class _FakeProvider:
    name = "gemini"
    model = "gemini-test"

    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def estimate_cost_usd(self, duration_sec, diarize=False):
        return round(duration_sec / 60 * 0.003, 6)

    def transcribe(self, path, *, language=None, diarize=False, duration_sec=None):
        self.calls.append(duration_sec)
        if self.exc:
            raise self.exc
        return self.result


def _selftest(asr_env, provider, duration=300):
    with patch("app.api.processing._guard_local_path", return_value="/x/v.mp4"), \
         patch("app.services.asr.get_provider", return_value=provider), \
         patch("app.services.asr.chunking.probe_duration_sec", return_value=duration), \
         patch("app.services.asr.chunking.extract_audio"), \
         patch("app.api.routes._preflight_disk_check"):
        return client.post("/api/v1/admin/asr/selftest", json={"file_id": "fid"})


def test_selftest_caps_at_60s_and_counts_against_ceiling(asr_env, admin):
    prov = _FakeProvider(TranscriptResult([Segment(0.5, 2.0, "Xin chào")], "vi", 60))
    r = _selftest(asr_env, prov)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert prov.calls == [60.0] and body["clip_sec"] == 60.0
    assert body["segments"] == [{"start": 0.5, "end": 2.0, "text": "Xin chào"}]
    assert "00:00:00,500 --> 00:00:02,000" in body["srt_preview"]
    assert set(body["timings_ms"]) == {"extract_ms", "provider_ms", "total_ms"}
    assert budget.snapshot()["spend_usd"] == pytest.approx(0.003)
    assert admin[-1][0] == "admin.asr.selftest" and admin[-1][1]["metadata"]["ok"] is True


def test_selftest_works_while_flag_off_but_respects_ceiling(asr_env, admin, monkeypatch):
    monkeypatch.setenv("ASR_SPEND_CEILING_USD", "0.001")
    prov = _FakeProvider(TranscriptResult([], "vi", 60))
    r = _selftest(asr_env, prov)
    assert r.status_code == 503 and r.json()["error_code"] == "spend_ceiling_reached"
    assert prov.calls == []


def test_selftest_bad_output_reports_raw_and_stays_counted(asr_env, admin):
    prov = _FakeProvider(exc=ProviderBadOutput("không phải JSON", raw="garbage"))
    r = _selftest(asr_env, prov)
    assert r.status_code == 502
    assert r.json()["error_code"] == "provider_bad_output" and r.json()["raw_output"] == "garbage"
    assert budget.snapshot()["spend_usd"] == pytest.approx(0.003)


# ── GET /transcript-asr/quota ─────────────────────────────────────────────

def test_quota_reads_usage_and_reset(asr_env, identity, monkeypatch):
    _enable(monkeypatch)
    asr_env.db.tables["transcript_asr_usage"] = [
        {"user_id": "user-1", "usage_date": "2030-01-15", "minutes_used": 37.5},
        {"user_id": "user-1", "usage_date": "2030-01-14", "minutes_used": 99},
        {"user_id": "other", "usage_date": "2030-01-15", "minutes_used": 5},
    ]
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        r = client.get("/api/v1/transcript-asr/quota")
    assert r.status_code == 200
    assert r.json() == {
        "enabled": True, "unavailable_reason": None, "minutes_used": 37.5,
        "minutes_limit": 120, "per_job_max_minutes": 45,
        "reset_at_utc": "2030-01-16T00:00:00+00:00",
    }


def test_quota_no_row_today_is_true_zero(asr_env, identity, monkeypatch):
    _enable(monkeypatch)
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        assert client.get("/api/v1/transcript-asr/quota").json()["minutes_used"] == 0.0


def test_quota_unreadable_usage_is_null_not_zero(asr_env, identity, monkeypatch):
    _enable(monkeypatch)
    asr_env.db.fail_on[("transcript_asr_usage", "select")] = True
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        body = client.get("/api/v1/transcript-asr/quota").json()
    assert body["minutes_used"] is None and body["enabled"] is True


def test_quota_flag_off_and_killswitch_disable(asr_env, identity, monkeypatch):
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        off = client.get("/api/v1/transcript-asr/quota").json()
        assert (off["enabled"], off["unavailable_reason"]) == (False, "asr_disabled")
        _enable(monkeypatch)
        budget.set_killswitch(True)
        paused = client.get("/api/v1/transcript-asr/quota").json()
        assert (paused["enabled"], paused["unavailable_reason"]) == (False, "asr_paused")


def test_quota_redis_down_fails_closed(asr_env, identity, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(budget, "_r", lambda: (_ for _ in ()).throw(RuntimeError("redis down")))
    with patch("app.api.transcript_asr._get_db", return_value=asr_env.db):
        body = client.get("/api/v1/transcript-asr/quota").json()
    assert (body["enabled"], body["unavailable_reason"]) == (False, "budget_unavailable")


def test_selftest_preview_is_cut_like_a_user_job(asr_env, admin):
    """Live 2026-10-06: the preview showed Gemini's raw 17 s segments while
    user jobs (transcribe_chunked) got them cut into 3-5 s cues."""
    from tests.test_asr_cue_split import REAL
    prov = _FakeProvider(TranscriptResult(list(REAL), "vi", 60))
    body = _selftest(asr_env, prov).json()
    assert body["ok"] is True
    assert body["provider_segment_count"] == 3 and body["segment_count"] > 3
    assert all(s["end"] - s["start"] <= 7.0 * 1.3 and len(s["text"]) <= 84 for s in body["segments"])
