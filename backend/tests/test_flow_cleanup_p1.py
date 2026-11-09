"""
Phase P1 tests for Flow/Veo Visible-Logo Cleanup queue & safe API
=================================================================
Covers:
  1. Celery task `inpaint_logo_task` registration and routing to `media` queue.
  2. Async queueing in `/process` -> `job_id`, `/status/{job_id}` polling (`queued` -> `running` -> `done`), progress %.
  3. `/cancel/{job_id}` and mid-processing cancellation via `progress_cb`.
  4. `/retry/{job_id}` after failure/cancellation with `retry_count` increment.
  5. Timeout handling (`processing_timeout`).
  6. Pre-worker rejection of jobs exceeding limits (file size, resolution > 4K, duration) BEFORE entering worker.
  7. Expired file cleanup by TTL (both on-access and via `periodic_cleanup_downloads`).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.api import flow_inpaint as fi
from app.core.celery_app import celery_app
from app.core.local_download import new_token
from app.main import app
from app.tasks import media_tasks


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    """Disable slowapi rate limiter during unit tests so >5 /process calls per minute don't return 429."""
    for lim in (getattr(fc, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)


@pytest.fixture
def pro_user(monkeypatch):
    """Authenticate requests as a Pro user."""
    async def _pro():
        return {"id": "pro-user-p1", "email": "pro@test.local", "tier": "pro"}

    app.dependency_overrides[fc.get_optional_user] = _pro
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def free_user(monkeypatch):
    """Authenticate requests as a Free user."""
    async def _free():
        return {"id": "free-user-p1", "email": "free@test.local", "tier": "free"}

    app.dependency_overrides[fc.get_optional_user] = _free
    yield
    app.dependency_overrides.pop(fc.get_optional_user, None)


@pytest.fixture
def sample_session(tmp_path, monkeypatch):
    """Create a small playable video session in a temporary DOWNLOAD_DIR."""
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))

    temp_id = new_token()
    work_dir = fc._safe_work_dir(temp_id)
    os.makedirs(work_dir, exist_ok=True)
    input_path = os.path.join(work_dir, "input.mp4")

    # Encode a tiny 10-frame 320x180 clip
    vw, vh, fps, n_frames = 320, 180, 10.0, 10
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}", "-r", str(fps), "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", input_path,
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    rng = np.random.default_rng(42)
    for _ in range(n_frames):
        frame = rng.integers(50, 200, size=(vh, vw, 3), dtype=np.uint8)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait(timeout=30)

    info = {"width": vw, "height": vh, "duration": 1.0, "fps": fps}
    fc._write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": "pro-user-p1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "video_info": info,
        "file_size_mb": 0.05,
    })
    return {
        "temp_id": temp_id,
        "work_dir": work_dir,
        "input_path": input_path,
        "downloads_dir": str(dl_dir),
        "info": info,
    }


class TestCeleryMediaQueueWiring:

    def test_inpaint_logo_task_is_routed_to_media_queue(self):
        routes = celery_app.conf.task_routes or {}
        assert "inpaint_logo_task" in routes
        assert routes["inpaint_logo_task"].get("queue") == "media"
        assert media_tasks.inpaint_logo_task.name == "inpaint_logo_task"

    def test_process_dispatches_to_media_queue_and_returns_job_id(self, pro_user, sample_session, monkeypatch):
        dispatched = []

        class _FakeAsyncResult:
            id = "celery-task-12345"

        def _fake_apply_async(args=None, queue=None, **kwargs):
            dispatched.append({"args": args, "queue": queue})
            return _FakeAsyncResult()

        monkeypatch.setattr(media_tasks.inpaint_logo_task, "apply_async", _fake_apply_async)

        client = TestClient(app)
        res = client.post(
            "/api/v1/flow-cleanup/process",
            json={
                "temp_id": sample_session["temp_id"],
                "preset": "lower-right",
                "method": "crop",
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["job_id"] == sample_session["temp_id"]
        assert body["status"] == "queued"
        assert body["progress"] == 0
        assert len(dispatched) == 1
        assert dispatched[0]["queue"] == "media"
        assert dispatched[0]["args"][0] == sample_session["temp_id"]

        # Poll /status/{job_id} while queued
        st = client.get(f"/api/v1/flow-cleanup/status/{body['job_id']}").json()
        assert st["status"] == "queued"
        assert st["progress"] == 0

        # Now run worker task and verify status transitions to done (100%)
        task_args = dispatched[0]["args"]
        media_tasks.inpaint_logo_task(task_args[0], task_args[1])

        st_done = client.get(f"/api/v1/flow-cleanup/status/{body['job_id']}").json()
        assert st_done["status"] == "done"
        assert st_done["progress"] == 100
        assert st_done["cleaned_file_id"].startswith("cleaned_")
        assert st_done["download_url"].startswith(f"/api/v1/flow-cleanup/result/{body['job_id']}/")

    def test_e2e_celery_worker_media_queue_execution(self, pro_user, sample_session, monkeypatch):
        """End-to-end test with FLOW_CLEANUP_ALLOW_INPROCESS=0 where a background Celery worker thread consumes the 'media' queue."""
        import queue as _q
        import threading

        media_queue: _q.Queue = _q.Queue()
        stop_evt = threading.Event()

        class _FakeAsyncResult:
            def __init__(self, tid: str):
                self.id = tid

        def _broker_apply_async(args=None, queue=None, **kwargs):
            assert queue == "media"
            tid = f"celery-e2e-{new_token()[:8]}"
            media_queue.put((tid, args or []))
            return _FakeAsyncResult(tid)

        def _celery_worker_loop():
            while not stop_evt.is_set():
                try:
                    tid, task_args = media_queue.get(timeout=0.1)
                except _q.Empty:
                    continue
                try:
                    media_tasks.inpaint_logo_task(task_args[0], task_args[1])
                finally:
                    media_queue.task_done()

        monkeypatch.setenv("FLOW_CLEANUP_ALLOW_INPROCESS", "0")
        monkeypatch.setenv("FLOW_CLEANUP_FORCE_ASYNC", "1")
        monkeypatch.setattr(media_tasks.inpaint_logo_task, "apply_async", _broker_apply_async)

        worker_thread = threading.Thread(target=_celery_worker_loop, daemon=True)
        worker_thread.start()
        try:
            client = TestClient(app)
            res = client.post(
                "/api/v1/flow-cleanup/process",
                json={
                    "temp_id": sample_session["temp_id"],
                    "preset": "lower-right",
                    "method": "crop",
                },
            )
            assert res.status_code == 200
            pdata = res.json()
            assert pdata["status"] == "queued"
            job_id = pdata["job_id"]

            final_st = None
            for _ in range(30):
                time.sleep(0.15)
                st = client.get(f"/api/v1/flow-cleanup/status/{job_id}").json()
                if st["status"] == "done":
                    final_st = st
                    break
            assert final_st is not None, f"Worker did not complete job in time (last status: {st})"
            assert final_st["progress"] == 100
            dl = client.get(final_st["download_url"])
            assert dl.status_code == 200
            assert len(dl.content) > 500
        finally:
            stop_evt.set()
            worker_thread.join(timeout=2)


class TestCancelRetryAndTimeout:

    def test_cancel_stops_running_inpaint_and_cleans_partial_files(self, pro_user, sample_session):
        temp_id = sample_session["temp_id"]
        work_dir = sample_session["work_dir"]
        client = TestClient(app)

        # Simulate user cancelling on frame 5 via progress_cb
        progress_seen = []
        orig_natural = fi.process_video_natural

        def _cancelling_natural(*args, **kwargs):
            orig_cb = kwargs.get("progress_cb")

            def _wrapped_cb(done, total):
                progress_seen.append(done)
                res = orig_cb(done, total) if orig_cb else True
                if done >= 1:
                    client.post(f"/api/v1/flow-cleanup/cancel/{temp_id}")
                    return orig_cb(done, total) if orig_cb else False
                return res

            kwargs["progress_cb"] = _wrapped_cb
            return orig_natural(*args, **kwargs)

        job_params = {
            "method": "natural",
            "preset": "custom",
            "region": {"x": 200, "y": 120, "w": 80, "h": 40},
            "video_info": sample_session["info"],
        }
        orig_fn = fi.process_video_natural
        fi.process_video_natural = _cancelling_natural
        try:
            res = fc._execute_flow_cleanup_job(temp_id, job_params)
        finally:
            fi.process_video_natural = orig_fn

        assert res["status"] == "cancelled"
        assert len(progress_seen) >= 1
        st = client.get(f"/api/v1/flow-cleanup/status/{temp_id}").json()
        assert st["status"] == "cancelled"
        # No partial cleaned_*.mp4 or *.noaudio.mp4 left behind
        leftovers = [f for f in os.listdir(work_dir) if f.startswith("cleaned_") or f.endswith(".noaudio.mp4")]
        assert leftovers == []

    def test_retry_after_failure_increments_retry_count_and_succeeds(self, pro_user, sample_session):
        temp_id = sample_session["temp_id"]
        work_dir = sample_session["work_dir"]
        fc._write_meta(work_dir, {
            "status": "failed",
            "error": "Simulated failure",
            "error_code": "ffmpeg_processing_failed",
            "retry_count": 0,
            "job_params": {
                "method": "crop",
                "preset": "lower-right",
                "region": {"x": 220, "y": 130, "w": 80, "h": 40},
                "video_info": sample_session["info"],
            },
        })

        client = TestClient(app)
        r = client.post(f"/api/v1/flow-cleanup/retry/{temp_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["retry_count"] == 1
        assert data["status"] == "done"
        assert data["download_url"]

    def test_timeout_marks_job_failed_with_processing_timeout(self, pro_user, sample_session, monkeypatch):
        temp_id = sample_session["temp_id"]

        def _timeout_crop(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=300)

        monkeypatch.setattr(fc, "_crop_video", _timeout_crop)
        client = TestClient(app)
        r = client.post(
            "/api/v1/flow-cleanup/process",
            json={"temp_id": temp_id, "preset": "lower-right", "method": "crop"},
        )
        assert r.status_code == 504
        st = client.get(f"/api/v1/flow-cleanup/status/{temp_id}").json()
        assert st["status"] == "failed"
        assert st["error_code"] == "processing_timeout"


class TestPreWorkerLimitRejection:

    def test_rejects_excessive_resolution_before_worker(self, pro_user, sample_session, monkeypatch):
        called = []
        monkeypatch.setattr(fc, "_enqueue_cleanup_task", lambda *a, **kw: called.append(1))
        monkeypatch.setattr(fc, "_probe", lambda _p: {"width": 7680, "height": 4320, "duration": 5.0, "fps": 30.0})

        client = TestClient(app)
        r = client.post(
            "/api/v1/flow-cleanup/process",
            json={"temp_id": sample_session["temp_id"], "preset": "lower-right", "method": "natural"},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        msg = detail["user_message"] if isinstance(detail, dict) else str(detail)
        assert "Độ phân giải" in msg
        assert called == [], "worker must not be called when resolution exceeds 4K"

    def test_rejects_excessive_duration_before_worker(self, pro_user, sample_session, monkeypatch):
        called = []
        monkeypatch.setattr(fc, "_enqueue_cleanup_task", lambda *a, **kw: called.append(1))
        monkeypatch.setattr(fc, "_probe", lambda _p: {"width": 1280, "height": 720, "duration": 900.0, "fps": 30.0})

        client = TestClient(app)
        r = client.post(
            "/api/v1/flow-cleanup/process",
            json={"temp_id": sample_session["temp_id"], "preset": "lower-right", "method": "natural"},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        msg = detail["user_message"] if isinstance(detail, dict) else str(detail)
        assert "Thời lượng" in msg
        assert called == [], "worker must not be called when duration exceeds limit"

    def test_rejects_when_estimated_processing_time_exceeds_soft_time_limit(self, pro_user, sample_session, monkeypatch):
        """A 45s 1280x720@30fps video (~559s estimated CPU time) exceeds 300s soft_time_limit and must be rejected."""
        called = []
        monkeypatch.setattr(fc, "_enqueue_cleanup_task", lambda *a, **kw: called.append(1))
        monkeypatch.setattr(fc, "_probe", lambda _p: {"width": 1280, "height": 720, "duration": 45.0, "fps": 30.0})

        client = TestClient(app)
        r = client.post(
            "/api/v1/flow-cleanup/process",
            json={"temp_id": sample_session["temp_id"], "preset": "lower-right", "method": "natural"},
        )
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["error_code"] == "duration_limit_exceeded"
        assert detail["estimated_processing_sec"] > 300
        assert "ước tính" in detail["user_message"]
        assert called == [], "worker must not be called when estimated CPU time exceeds 300s"

    def test_rejects_oversized_file_before_worker(self, pro_user, sample_session, monkeypatch):
        called = []
        monkeypatch.setattr(fc, "_enqueue_cleanup_task", lambda *a, **kw: called.append(1))
        monkeypatch.setattr(fc, "_MAX_UPLOAD_MB", 0)  # force size > max

        client = TestClient(app)
        r = client.post(
            "/api/v1/flow-cleanup/process",
            json={"temp_id": sample_session["temp_id"], "preset": "lower-right", "method": "crop"},
        )
        assert r.status_code == 413
        assert called == []

    def test_production_without_worker_returns_503_when_inprocess_disabled(self, pro_user, sample_session, monkeypatch):
        """When FLOW_CLEANUP_ALLOW_INPROCESS=0 (production default) and Celery worker is down, return 503 retryable."""
        monkeypatch.setenv("FLOW_CLEANUP_ALLOW_INPROCESS", "0")
        monkeypatch.setattr(fc, "_enqueue_cleanup_task", lambda *a, **kw: None)

        client = TestClient(app)
        r = client.post(
            "/api/v1/flow-cleanup/process",
            json={"temp_id": sample_session["temp_id"], "preset": "lower-right", "method": "crop"},
        )
        assert r.status_code == 503
        detail = r.json()["detail"]
        assert detail["error_code"] == "queue_unavailable"
        assert detail["retryable"] is True

    def test_retry_exceeding_max_3_retries_returns_400(self, pro_user, sample_session):
        temp_id = sample_session["temp_id"]
        work_dir = sample_session["work_dir"]
        fc._write_meta(work_dir, {
            "status": "failed",
            "retry_count": 3,
            "job_params": {
                "method": "crop",
                "preset": "lower-right",
                "region": {"x": 220, "y": 130, "w": 80, "h": 40},
                "video_info": sample_session["info"],
            },
        })
        client = TestClient(app)
        r = client.post(f"/api/v1/flow-cleanup/retry/{temp_id}")
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "max_retries_exceeded"


class TestTTLAndAccessControl:

    def test_expired_job_returns_404_and_removes_work_dir(self, pro_user, sample_session):
        temp_id = sample_session["temp_id"]
        work_dir = sample_session["work_dir"]
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        fc._write_meta(work_dir, {
            "status": "completed",
            "processed_at": old_time,
            "created_at": old_time,
        })
        old_epoch = time.time() - 7200
        os.utime(work_dir, (old_epoch, old_epoch))

        client = TestClient(app)
        r = client.get(f"/api/v1/flow-cleanup/status/{temp_id}")
        assert r.status_code == 404
        assert not os.path.exists(work_dir), "expired work_dir should be removed on access"

    def test_periodic_cleanup_removes_expired_flow_dir_but_keeps_running_job(self, sample_session, monkeypatch):
        from app.tasks import video_tasks

        dl_dir = sample_session["downloads_dir"]
        active_dir = sample_session["work_dir"]
        # Mark sample_session as running even with an older mtime
        fc._write_meta(active_dir, {"status": "running"})

        # Create a second expired flow_* directory
        exp_id = new_token()
        exp_dir = fc._safe_work_dir(exp_id)
        os.makedirs(exp_dir, exist_ok=True)
        old_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        fc._write_meta(exp_dir, {"status": "completed", "processed_at": old_iso, "created_at": old_iso})
        old_ts = time.time() - 7200
        os.utime(exp_dir, (old_ts, old_ts))

        monkeypatch.setattr(video_tasks, "_downloads_dir", lambda: dl_dir)
        video_tasks.periodic_cleanup_downloads(max_age_minutes=20)

        assert not os.path.exists(exp_dir), "expired flow_* dir must be deleted by periodic cleanup"
        assert os.path.exists(active_dir), "running flow_* job must NOT be deleted by periodic cleanup"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/flow-cleanup/status/0123456789abcdef"),
            ("POST", "/api/v1/flow-cleanup/cancel/0123456789abcdef"),
            ("POST", "/api/v1/flow-cleanup/retry/0123456789abcdef"),
        ],
    )
    def test_new_queue_endpoints_enforce_require_pro(self, free_user, method, path):
        client = TestClient(app)
        r = client.request(method, path)
        assert r.status_code == 402
        assert r.json()["detail"]["error_code"] == "tier_required_feature"

    def test_job_ownership_prevents_pro_user_b_from_accessing_user_a_job(self, sample_session):
        """User A (Pro) owns the job; User B (also Pro) must receive 404 on /status, /cancel, /retry, /result, /process."""
        temp_id = sample_session["temp_id"]
        work_dir = sample_session["work_dir"]
        client = TestClient(app)

        # 1. Authenticate as User A (Pro) and process job to create a cleaned result
        async def _user_a():
            return {"id": "user-a-id", "email": "usera@test.local", "tier": "pro"}

        app.dependency_overrides[fc.get_optional_user] = _user_a
        try:
            fc._write_meta(work_dir, {"owner_user_id": "user-a-id"})
            r_proc = client.post(
                "/api/v1/flow-cleanup/process",
                json={"temp_id": temp_id, "preset": "lower-right", "method": "crop"},
            )
            assert r_proc.status_code == 200
            cleaned_file_id = r_proc.json()["cleaned_file_id"]

            # User A can view status and download result
            assert client.get(f"/api/v1/flow-cleanup/status/{temp_id}").status_code == 200
            assert client.get(f"/api/v1/flow-cleanup/result/{temp_id}/{cleaned_file_id}").status_code == 200
        finally:
            app.dependency_overrides.pop(fc.get_optional_user, None)

        # 2. Switch to User B (also Pro!) — must get 404 on every endpoint for User A's job
        async def _user_b():
            return {"id": "user-b-id", "email": "userb@test.local", "tier": "pro"}

        app.dependency_overrides[fc.get_optional_user] = _user_b
        try:
            assert client.get(f"/api/v1/flow-cleanup/status/{temp_id}").status_code == 404
            assert client.post(f"/api/v1/flow-cleanup/cancel/{temp_id}").status_code == 404
            assert client.post(f"/api/v1/flow-cleanup/retry/{temp_id}").status_code == 404
            assert client.get(f"/api/v1/flow-cleanup/result/{temp_id}/{cleaned_file_id}").status_code == 404
            assert client.post(
                "/api/v1/flow-cleanup/process",
                json={"temp_id": temp_id, "preset": "lower-right", "method": "crop"},
            ).status_code == 404
            assert client.post(
                "/api/v1/flow-cleanup/preview-frame",
                json={"temp_id": temp_id, "preset": "lower-right"},
            ).status_code == 404
        finally:
            app.dependency_overrides.pop(fc.get_optional_user, None)
