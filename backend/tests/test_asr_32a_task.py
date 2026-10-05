"""Phase 32A — transcribe_video_task + stuck-job sweeper with mocked ffmpeg
and providers: happy path with chunk offsets, failure refunds, no retry
after a paid call, redelivery protection, sweeper refund-at-most-once."""
from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.services.asr import budget
from app.services.asr.types import ProviderBadOutput, Segment, TranscriptResult
from app.tasks import transcript_asr_tasks as T
from tests._asr_fakes import PINNED_NOW, asr_env  # noqa: F401 (fixture)


class _Provider:
    name = "gemini"

    def __init__(self, script):
        self.script = list(script)   # per call: TranscriptResult or Exception
        self.calls = []

    def estimate_cost_usd(self, duration_sec, diarize=False):
        return round(duration_sec / 60 * 0.01, 6)

    def transcribe(self, path, *, language=None, diarize=False, duration_sec=None):
        self.calls.append((path, duration_sec))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def task_env(asr_env, monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(T, "get_service_client", lambda: asr_env.db)
    monkeypatch.setattr(T, "_DOWNLOADS_DIR", str(tmp_path / "dl"))

    def fake_extract(src, dst, **kw):
        open(dst, "wb").write(b"audio")

    def fake_cut(src, off, length, out, timeout=300):
        open(out, "wb").write(b"chunk")

    monkeypatch.setattr("app.services.asr.chunking.extract_audio", fake_extract)
    monkeypatch.setattr("app.services.asr.pipeline.cut_chunk", fake_cut)
    monkeypatch.setattr("app.services.asr.chunking.probe_duration_sec", lambda p: 100.0)
    monkeypatch.setenv("ASR_CHUNK_SEC", "60")   # 100s -> chunks (0,60) + (60,40)

    asr_env.db.tables["transcript_asr_jobs"] = [{
        "id": "j1", "user_id": "user-1", "status": "queued", "video_local_path": str(video),
        "duration_sec": 100, "timeout_retry_count": 0, "created_at": PINNED_NOW.isoformat(),
        "updated_at": PINNED_NOW.isoformat(),
    }]
    day = budget.reserve(0.016667, 100 / 60)
    budget.meta_set("j1", provider="gemini", quota_key="user-1", minutes=f"{100/60:.4f}",
                    usage_date="2030-01-15", spend_day=day, est_cost="0.016667")
    asr_env.tmp = tmp_path
    return asr_env


def _use(monkeypatch, provider):
    monkeypatch.setattr("app.services.asr.get_provider", lambda name=None: provider)


def _row(env):
    return env.db.tables["transcript_asr_jobs"][0]


def test_happy_path_merges_chunk_offsets_and_settles(task_env, monkeypatch):
    prov = _Provider([
        TranscriptResult([Segment(1.0, 3.0, "một"), Segment(55.0, 59.5, "hai")], "vi", 60),
        TranscriptResult([Segment(0.5, 2.0, "ba")], "vi", 40),
    ])
    _use(monkeypatch, prov)
    out = T.transcribe_video_task.run("j1")
    assert out["status"] == "done", out
    row = _row(task_env)
    assert row["status"] == "done" and row["progress_pct"] == 100 and row["detected_language"] == "vi"
    assert [c[1] for c in prov.calls] == [60.0, 40.0]
    srt = open(row["result_path"], encoding="utf-8").read()
    assert "00:01:00,500 --> 00:01:02,000\nba" in srt   # second chunk shifted by 60s
    side = json.load(open(T.sidecar_json_path(row["result_path"]), encoding="utf-8"))
    assert side["segments"][2] == {"start": 60.5, "end": 62.0, "text": "ba"}
    assert task_env.db.calls("refund_transcript_asr_usage") == []
    meta = budget.meta_get("j1")
    assert float(meta["actual_cost"]) == pytest.approx(0.016667, abs=1e-5)
    assert meta["paid_calls"] == "2"
    # no temp audio/chunks left behind
    import os
    left = [f for f in os.listdir(task_env.tmp / "dl") if not f.startswith("asr_result_")]
    assert left == []


def test_bad_output_fails_job_and_refunds_once(task_env, monkeypatch):
    _use(monkeypatch, _Provider([ProviderBadOutput("thời gian đi lùi")]))
    out = T.transcribe_video_task.run("j1")
    assert out == {"status": "failed", "job_id": "j1", "error_code": "provider_bad_output"}
    assert _row(task_env)["error_message"].startswith("[provider_bad_output]")
    assert task_env.db.calls("refund_transcript_asr_usage") == [
        {"p_user_id": "user-1", "p_minutes": round(100 / 60, 4), "p_usage_date": "2030-01-15"}]
    # second delivery of the same message: terminal, nothing happens
    assert T.transcribe_video_task.run("j1")["status"] == "already_failed"
    assert len(task_env.db.calls("refund_transcript_asr_usage")) == 1


def test_no_speech_is_a_clean_failure(task_env, monkeypatch):
    _use(monkeypatch, _Provider([TranscriptResult([], "vi", 60), TranscriptResult([], "vi", 40)]))
    assert T.transcribe_video_task.run("j1")["error_code"] == "no_speech"


def test_soft_timeout_after_paid_call_does_not_retry(task_env, monkeypatch):
    prov = _Provider([TranscriptResult([Segment(0, 1, "a")], "vi", 60), SoftTimeLimitExceeded()])
    _use(monkeypatch, prov)
    requeue = MagicMock()
    monkeypatch.setattr(T.transcribe_video_task, "apply_async", requeue)
    out = T.transcribe_video_task.run("j1")
    assert out["error_code"] == "timeout"
    requeue.assert_not_called()
    assert _row(task_env)["status"] == "failed"
    assert len(task_env.db.calls("refund_transcript_asr_usage")) == 1
    assert budget.meta_get("j1")["paid_calls"] == "2"   # both attempted calls billed


def test_soft_timeout_before_any_paid_call_requeues(task_env, monkeypatch):
    prov = _Provider([])
    _use(monkeypatch, prov)

    def slow_extract(src, dst, **kw):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr("app.services.asr.chunking.extract_audio", slow_extract)
    requeue = MagicMock()
    monkeypatch.setattr(T.transcribe_video_task, "apply_async", requeue)
    out = T.transcribe_video_task.run("j1")
    assert out["status"] == "requeued"
    requeue.assert_called_once_with(args=["j1"])
    assert _row(task_env)["status"] == "queued" and _row(task_env)["timeout_retry_count"] == 1
    assert prov.calls == []
    assert "settled" not in budget.meta_get("j1")   # still budgeted for the retry


def test_redelivered_job_mid_run_is_failed_not_rebilled(task_env, monkeypatch):
    _row(task_env)["status"] = "transcribing"
    prov = _Provider([])
    _use(monkeypatch, prov)
    out = T.transcribe_video_task.run("j1")
    assert out["error_code"] == "interrupted"
    assert prov.calls == []
    assert len(task_env.db.calls("refund_transcript_asr_usage")) == 1


def test_provider_unavailable_in_task(task_env, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = T.transcribe_video_task.run("j1")
    assert out["error_code"] == "provider_unavailable"
    snap = budget.snapshot()
    assert snap["spend_usd"] == 0 and snap["minutes"] == 0   # nothing paid -> fully released


# ── sweeper ───────────────────────────────────────────────────────────────

def test_sweeper_fails_and_refunds_stuck_jobs_once(task_env):
    old = (PINNED_NOW - timedelta(hours=1)).isoformat()
    fresh = (PINNED_NOW - timedelta(minutes=5)).isoformat()
    task_env.db.tables["transcript_asr_jobs"] = [
        {"id": "stuck-q", "user_id": "u1", "status": "queued", "duration_sec": 60,
         "created_at": old, "updated_at": old},
        {"id": "stuck-t", "user_id": "u2", "status": "transcribing", "duration_sec": 120,
         "created_at": old, "updated_at": old},
        {"id": "running", "user_id": "u3", "status": "transcribing", "duration_sec": 60,
         "created_at": fresh, "updated_at": fresh},
        {"id": "finished", "user_id": "u4", "status": "done", "duration_sec": 60,
         "created_at": old, "updated_at": old},
    ]
    day = budget.day_key()
    budget.meta_set("stuck-q", spend_day=day, est_cost="0.01", minutes="1")
    budget.meta_set("stuck-t", spend_day=day, est_cost="0.02", minutes="2")
    before = budget.snapshot()["spend_usd"]

    assert T.sweep_stuck_transcript_asr_jobs_task.run() == {"swept": 2}
    status = {r["id"]: r["status"] for r in task_env.db.tables["transcript_asr_jobs"]}
    assert status == {"stuck-q": "failed", "stuck-t": "failed", "running": "transcribing", "finished": "done"}
    refunded = sorted(p["p_user_id"] for p in task_env.db.calls("refund_transcript_asr_usage"))
    assert refunded == ["u1", "u2"]
    # queued never reached the provider -> its estimate is released
    assert budget.snapshot()["spend_usd"] == pytest.approx(before - 0.01)

    assert T.sweep_stuck_transcript_asr_jobs_task.run() == {"swept": 0}
    assert len(task_env.db.calls("refund_transcript_asr_usage")) == 2


def test_diarization_flag_labels_srt_only_when_on(task_env, monkeypatch):
    def run(flag):
        task_env.db.tables["transcript_asr_jobs"][0].update(status="queued")
        if flag:
            monkeypatch.setenv("ASR_DIARIZATION_ENABLED", "true")
        else:
            monkeypatch.delenv("ASR_DIARIZATION_ENABLED", raising=False)
        _use(monkeypatch, _Provider([
            TranscriptResult([Segment(1.0, 2.0, "chào", speaker="S1")], "vi", 60),
            TranscriptResult([Segment(1.0, 2.0, "dạ", speaker="S2")], "vi", 40),
        ]))
        T.transcribe_video_task.run("j1")
        return open(_row(task_env)["result_path"], encoding="utf-8").read()

    assert "[S1]" not in run(False)
    srt = run(True)
    assert "[S1] chào" in srt and "[S2] dạ" in srt
