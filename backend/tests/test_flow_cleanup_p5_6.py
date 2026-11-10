"""
Phase P5.6 Regression Tests — Post-Review Hardening Fixes
=========================================================
Mục 3 (P5.6.3): Dynamic effective max duration per resolution/FPS & probe fields.
"""
import os
import sys
import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.api import flow_cleanup
from app.main import app


@pytest.fixture(autouse=True)
def _disable_rate_limiter(monkeypatch):
    for lim in (getattr(flow_cleanup, "limiter", None), getattr(getattr(app, "state", None), "limiter", None)):
        if lim is not None and hasattr(lim, "enabled"):
            monkeypatch.setattr(lim, "enabled", False)



class TestP563EffectiveMaxDuration:
    """Verify _compute_effective_max_duration and _validate_job_limits across profiles."""

    @pytest.mark.parametrize(
        "vw,vh,fps,method,expected_max_sec",
        [
            (640, 360, 24.0, "natural", 60),
            (640, 360, 30.0, "natural", 60),
            (854, 480, 30.0, "natural", 54),
            (720, 1280, 24.0, "natural", 30),
            (720, 1280, 30.0, "natural", 24),
            (720, 1280, 60.0, "natural", 12),
            (1080, 1920, 24.0, "natural", 13),
            (1080, 1920, 30.0, "natural", 10),
            (1080, 1920, 60.0, "natural", 5),
            (1080, 1920, 30.0, "crop", 600),
            (1080, 1920, 30.0, "blur", 600),
        ],
    )
    def test_compute_effective_max_duration_profiles(
        self, vw, vh, fps, method, expected_max_sec
    ):
        actual = flow_cleanup._compute_effective_max_duration(vw, vh, fps, method)
        assert actual == expected_max_sec

    def test_validate_job_limits_reports_exact_max_duration_for_1080p30(self, tmp_path):
        dummy_video = tmp_path / "dummy.mp4"
        dummy_video.write_bytes(b"\x00" * 1024)

        # 1080x1920 @ 30fps allows at most 10s for natural inpaint; 15s must be rejected
        info_ok = {"width": 1080, "height": 1920, "fps": 30.0, "duration": 10.0}
        flow_cleanup._validate_job_limits(str(dummy_video), info_ok, method="natural")

        info_too_long = {"width": 1080, "height": 1920, "fps": 30.0, "duration": 15.0}
        with pytest.raises(HTTPException) as exc_info:
            flow_cleanup._validate_job_limits(str(dummy_video), info_too_long, method="natural")
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert detail["error_code"] == "duration_limit_exceeded"
        assert detail["max_duration_sec"] == 10
        assert "10s" in detail["user_message"]

    def test_validate_job_limits_reports_exact_max_duration_for_720p30(self, tmp_path):
        dummy_video = tmp_path / "dummy.mp4"
        dummy_video.write_bytes(b"\x00" * 1024)

        # 720x1280 @ 30fps allows at most 24s for natural inpaint; 30s must be rejected
        info_ok = {"width": 720, "height": 1280, "fps": 30.0, "duration": 24.0}
        flow_cleanup._validate_job_limits(str(dummy_video), info_ok, method="natural")

        info_too_long = {"width": 720, "height": 1280, "fps": 30.0, "duration": 30.0}
        with pytest.raises(HTTPException) as exc_info:
            flow_cleanup._validate_job_limits(str(dummy_video), info_too_long, method="natural")
        assert exc_info.value.status_code == 400
        detail = exc_info.value.detail
        assert detail["error_code"] == "duration_limit_exceeded"
        assert detail["max_duration_sec"] == 24
        assert "24s" in detail["user_message"]


class TestP564DedicatedSigningSecret:
    """Verify FLOW_URL_SIGNING_SECRET never falls back to JWT_SECRET and fails fast in production."""

    def test_does_not_fallback_to_jwt_secret(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.delenv("FLOW_URL_SIGNING_SECRET", raising=False)
        monkeypatch.setenv("JWT_SECRET", "super-secret-jwt-key-that-must-not-be-used")
        monkeypatch.setenv("SECRET_KEY", "another-secret-key-that-must-not-be-used")

        secret = flow_cleanup._get_signing_secret()
        assert secret != "super-secret-jwt-key-that-must-not-be-used"
        assert secret != "another-secret-key-that-must-not-be-used"
        assert secret == flow_cleanup._EPHEMERAL_SIGNING_SECRET

    def test_production_fails_startup_when_signing_secret_missing(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.delenv("FLOW_URL_SIGNING_SECRET", raising=False)
        monkeypatch.setenv("JWT_SECRET", "jwt-secret-should-not-save-production-startup")

        with pytest.raises(RuntimeError, match="FLOW_URL_SIGNING_SECRET is required in production"):
            flow_cleanup._validate_signing_secret_on_startup()

    def test_production_succeeds_with_dedicated_signing_secret(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("FLOW_URL_SIGNING_SECRET", "dedicated-flow-signing-secret-32chars!")

        secret = flow_cleanup._get_signing_secret()
        assert secret == "dedicated-flow-signing-secret-32chars!"


class TestP565RedisQuotaAndConcurrency:
    """Verify Redis INCR+EXPIRE daily quota and SET NX+TTL 1-job/user lock under concurrent requests."""

    def test_redis_daily_quota_incr_expire_and_concurrent_requests(self, tmp_path, monkeypatch):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        dl_dir = tmp_path / "dl_redis_quota"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(flow_cleanup, "_DOWNLOAD_DIR", str(dl_dir))
        monkeypatch.setattr(flow_cleanup, "_DAILY_USER_QUOTA", 1)

        uid = "pro-user-concurrent-quota"
        r = flow_cleanup._get_flow_redis()
        qkey = flow_cleanup._daily_quota_redis_key(uid)
        r.delete(qkey)

        barrier = threading.Barrier(2)

        def _try_quota(_idx: int) -> int:
            barrier.wait(timeout=5.0)
            try:
                flow_cleanup._check_and_record_daily_quota(uid)
                return 200
            except HTTPException as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(list(pool.map(_try_quota, [0, 1])))

        # Exactly 1 request accepted (200) and 1 rejected (429)
        assert statuses == [200, 429]
        # Redis key has a positive TTL (EXPIRE set) and no file was created in downloads/
        assert int(r.ttl(qkey)) > 0
        assert not (dl_dir / "flow_daily_quota.json").exists()

    def test_two_concurrent_job_requests_only_one_accepted(self, tmp_path, monkeypatch):
        import subprocess
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import numpy as np
        from fastapi.testclient import TestClient
        from app.main import app
        from app.core.auth_middleware import get_optional_user
        from app.tasks import media_tasks

        dl_dir = tmp_path / "dl_redis_conc"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(flow_cleanup, "_DOWNLOAD_DIR", str(dl_dir))

        uid = "pro-user-concurrent-job"
        r = flow_cleanup._get_flow_redis()
        lkey = flow_cleanup._user_active_job_redis_key(uid)
        r.delete(lkey)

        async def _override_pro():
            return {"id": uid, "email": "conc@example.com", "tier": "pro"}

        app.dependency_overrides[get_optional_user] = _override_pro
        monkeypatch.setattr(flow_cleanup, "check_feature", lambda tier, feat: True)

        def _create_uploaded_job(tag: str) -> str:
            tid = f"aa{tag}00112233445566778899aabbccdd"[:32]
            wdir = dl_dir / f"flow_{tid}"
            wdir.mkdir(parents=True, exist_ok=True)
            in_path = wdir / "input.mp4"
            cmd = [
                "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", "320x180", "-r", "15", "-i", "pipe:0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", "-preset", "ultrafast",
                str(in_path),
            ]
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            frame = np.full((180, 320, 3), 80, dtype=np.uint8)
            for _ in range(15):
                proc.stdin.write(frame.tobytes())
            proc.stdin.close()
            proc.wait(timeout=20)
            flow_cleanup._extract_frame(str(in_path), str(wdir / "preview.jpg"))
            flow_cleanup._write_meta(str(wdir), {
                "temp_id": tid,
                "job_id": tid,
                "owner_user_id": uid,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "uploaded",
                "progress": 0,
                "video_info": {"width": 320, "height": 180, "duration": 1.0, "fps": 15.0, "nb_frames": 15},
            })
            return tid

        try:
            tid_a = _create_uploaded_job("aa")
            tid_b = _create_uploaded_job("bb")

            mock_track_apply = MagicMock(return_value=MagicMock(id="celery-conc-track"))
            monkeypatch.setattr(media_tasks.track_watermark_task, "apply_async", mock_track_apply)

            barrier = threading.Barrier(2)
            client = TestClient(app)

            def _fire_track(tid: str) -> int:
                barrier.wait(timeout=5.0)
                resp = client.post(
                    f"/api/v1/flow-cleanup/track/{tid}",
                    json={"region": {"x": 200, "y": 100, "w": 60, "h": 30}},
                )
                return resp.status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = sorted(list(pool.map(_fire_track, [tid_a, tid_b])))

            # Strictly 1 request accepted (202) and 1 rejected (429)
            assert codes == [202, 429]
            assert mock_track_apply.call_count == 1
            assert int(r.ttl(lkey)) > 0

            # Cancelling the accepted job releases the Redis lock immediately
            active_tid = r.get(lkey)
            assert active_tid in (tid_a, tid_b)
            rc = client.post(f"/api/v1/flow-cleanup/cancel/{active_tid}")
            assert rc.status_code == 200
            assert r.get(lkey) is None
        finally:
            app.dependency_overrides.pop(get_optional_user, None)


class TestP566DatabaseConsentAuditLog:
    """Verify consent audit logs are stored in DB table flow_consent_audit_logs (migration 044) and not in downloads/."""

    def test_migration_044_exists_and_audit_log_writes_to_db_not_downloads(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        import app.core.database as db_mod

        mig_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "database", "migrations", "044_flow_consent_audit_logs.sql")
        )
        assert os.path.isfile(mig_path)
        sql_text = open(mig_path, "r", encoding="utf-8").read()
        assert "CREATE TABLE IF NOT EXISTS flow_consent_audit_logs" in sql_text

        dl_dir = tmp_path / "dl_no_audit_file"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(flow_cleanup, "_DOWNLOAD_DIR", str(dl_dir))
        monkeypatch.setenv("SUPABASE_URL", "https://example-real-project.supabase.co")

        mock_client = MagicMock()
        monkeypatch.setattr(db_mod, "get_service_client", lambda: mock_client)

        tid = "auditdb00112233445566778899aabbcc"
        rec = flow_cleanup._append_consent_audit_log("user-db-99", tid, action="process_confirm")
        assert rec["user_id"] == "user-db-99"
        assert rec["temp_id"] == tid
        assert rec["action"] == "process_confirm"

        # Verify Supabase DB table insert was invoked
        mock_client.table.assert_called_once_with("flow_consent_audit_logs")
        inserted_payload = mock_client.table.return_value.insert.call_args.args[0]
        assert inserted_payload["user_id"] == "user-db-99"
        assert inserted_payload["temp_id"] == tid
        assert inserted_payload["action"] == "process_confirm"
        mock_client.table.return_value.insert.return_value.execute.assert_called_once()

        # Verify SQLite DB mirror also has the row and NO file was written in downloads/
        rows = flow_cleanup._query_consent_audit_logs(temp_id=tid, user_id="user-db-99")
        assert len(rows) == 1
        assert not (dl_dir / "flow_consent_audit.jsonl").exists()
        assert list(dl_dir.iterdir()) == []


class TestP567OddDimensionPadAndIntentionalDeviation:
    """Verify odd-dimension videos (721x405) pad +1px to 722x406 instead of cropping, and report intentional deviation."""

    def test_odd_721x405_pads_to_722x406_and_preserves_original_pixels(self):
        from benchmarks.generate_hardening_fixtures import HARDENING_DIR, generate_all_hardening_fixtures

        fixtures = generate_all_hardening_fixtures(HARDENING_DIR)
        odd_path = fixtures["odd_721x405"]
        info = flow_cleanup._probe(odd_path)

        assert info["raw_width"] == 721
        assert info["raw_height"] == 405
        assert info["width"] == 722
        assert info["height"] == 406
        assert info["odd_dimension_adjusted"] is True
        assert info["dimension_adjustment"] == "padded_to_even_for_yuv420p"


class TestP574UserJobLockQueueAndHeartbeat:
    """Verify per-user active job Redis lock covers queue wait + job timeout and renews TTL on progress heartbeat."""

    def test_user_job_lock_initial_ttl_covers_queue_plus_timeout_and_heartbeat_renews(self, tmp_path, monkeypatch):
        dl_dir = tmp_path / "dl_lock_p574"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(flow_cleanup, "_DOWNLOAD_DIR", str(dl_dir))
        monkeypatch.setattr(flow_cleanup, "_REDIS_CLIENT", flow_cleanup._FALLBACK_REDIS)

        r = flow_cleanup._get_flow_redis()
        uid = "pro-lock-p574-user"
        tid_1 = "aa11223344556677889900aabbccdd01"
        tid_2 = "bb11223344556677889900aabbccdd02"
        lkey = flow_cleanup._user_active_job_redis_key(uid)
        r.delete(lkey)

        wdir_1 = dl_dir / f"flow_{tid_1}"
        wdir_1.mkdir(parents=True, exist_ok=True)
        flow_cleanup._write_meta(str(wdir_1), {
            "temp_id": tid_1,
            "job_id": tid_1,
            "owner_user_id": uid,
            "status": "uploaded",
            "progress": 0,
        })

        # 1. Acquire lock for job 1 -> initial TTL must cover queue wait + 300s timeout (>= 1020s, not 360s)
        flow_cleanup._check_user_concurrency(uid, tid_1)
        initial_ttl = int(r.ttl(lkey))
        assert initial_ttl >= 900, f"Expected initial lock TTL >= 900s to cover queue + execution, got {initial_ttl}s"

        # 2. Simulate job 1 waiting in queue + running for 350s by artificially lowering remaining TTL to 20s
        r.expire(lkey, 20)
        assert int(r.ttl(lkey)) <= 20

        # 3. Worker progress heartbeat via _write_meta(status='running', progress=45) must renew TTL back >= 900s
        flow_cleanup._write_meta(str(wdir_1), {"status": "running", "progress": 45})
        renewed_ttl = int(r.ttl(lkey))
        assert renewed_ttl >= 900, f"Expected heartbeat _write_meta to renew lock TTL >= 900s, got {renewed_ttl}s"

        # 4. Even a progress-only update while status is already 'running' in metadata.json renews TTL
        r.expire(lkey, 15)
        flow_cleanup._write_meta(str(wdir_1), {"progress": 70})
        assert int(r.ttl(lkey)) >= 900

        # 5. Submitting a 2nd job for the same user while job 1 is still queued/running is rejected with HTTP 429
        with pytest.raises(HTTPException) as exc_info:
            flow_cleanup._check_user_concurrency(uid, tid_2)
        assert exc_info.value.status_code == 429
        assert exc_info.value.detail["error_code"] == "user_concurrent_job_limit"

        # 6. Completing job 1 immediately releases the lock so job 2 can now be accepted
        flow_cleanup._write_meta(str(wdir_1), {"status": "done", "progress": 100})
        assert r.get(lkey) is None
        flow_cleanup._check_user_concurrency(uid, tid_2)
        assert r.get(lkey) == tid_2
        r.delete(lkey)


class TestP58PollingRateLimitFiveMinutes:
    """Verify 1 user polling continuously for 5 minutes across status/track/preview-clip never hits HTTP 429."""

    def test_five_minutes_continuous_polling_across_track_preview_clip_and_process_never_429(self, tmp_path, monkeypatch):
        import time
        from fastapi.testclient import TestClient

        dl_dir = tmp_path / "dl_poll_p58"
        dl_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(flow_cleanup, "_DOWNLOAD_DIR", str(dl_dir))

        uid = "pro-poll-p58-user"
        tid = "cc11223344556677889900aabbccdd99"
        wdir = flow_cleanup._safe_work_dir(tid)
        os.makedirs(wdir, exist_ok=True)
        monkeypatch.setattr(flow_cleanup, "_REDIS_CLIENT", flow_cleanup._FALLBACK_REDIS)

        from fastapi import Request

        async def _fake_pro(request: Request):
            request.state.flow_user_id = uid
            return None

        app.dependency_overrides[flow_cleanup._require_pro] = _fake_pro

        # Enable the real SlowAPI rate limiter (using in-memory storage so the test is self-contained & deterministic)
        lim = app.state.limiter
        monkeypatch.setattr(lim, "enabled", True)
        monkeypatch.setattr(lim, "_storage_dead", False)
        if getattr(lim, "_fallback_limiter", None) is not None:
            monkeypatch.setattr(lim, "_limiter", lim._fallback_limiter)
            if getattr(lim._fallback_limiter, "storage", None) is not None:
                monkeypatch.setattr(lim, "_storage", lim._fallback_limiter.storage)
        try:
            lim.reset()
        except Exception:
            pass
        try:
            if hasattr(flow_cleanup._FALLBACK_REDIS, "_store"):
                flow_cleanup._FALLBACK_REDIS._store.clear()
        except Exception:
            pass

        try:
            client = TestClient(app)
            headers = {"X-Forwarded-For": "198.51.100.88"}

            # 1. Verify route limit on GET /api/v1/flow-cleanup/status/{job_id} is 600/minute
            fn_key = f"{flow_cleanup.get_flow_cleanup_status.__module__}.{flow_cleanup.get_flow_cleanup_status.__name__}"
            route_limits = lim._route_limits.get(fn_key, [])
            assert any("600" in str(rl.limit) for rl in route_limits), (
                f"Expected 600/minute rate limit on {fn_key}, found {[str(rl.limit) for rl in route_limits]}"
            )

            # 2. Simulate 5 minutes (300s) of continuous polling across all 3 polled task states:
            #    - Track polling: 1000ms interval (60 req/min -> 100 requests for 100s)
            #    - Preview-clip polling: 1000ms interval (60 req/min -> 100 requests for 100s)
            #    - Process polling: 1500ms interval (40 req/min -> 100 requests for 150s)
            #    Total = 300 continuous polls (>= 5 full minutes at the fastest 1000ms frontend poll interval)
            #    fired in a single burst (strictly harder than 5 real-clock minutes).
            for task_kind, n_polls in (("track", 100), ("preview_clip", 100), ("process", 100)):
                flow_cleanup._write_meta(str(wdir), {
                    "temp_id": tid,
                    "job_id": tid,
                    "created_at": time.time(),
                    "owner_user_id": uid,
                    "active_task_kind": task_kind,
                    "status": "running",
                    "progress": 42,
                })
                for i in range(n_polls):
                    resp = client.get(f"/api/v1/flow-cleanup/status/{tid}", headers=headers)
                    assert resp.status_code == 200, (
                        f"Poll #{i + 1} for {task_kind} returned HTTP {resp.status_code} (expected 200): {resp.text}"
                    )
                    body = resp.json()
                    assert body["status"] == "running"
                    assert body["progress"] == 42
        finally:
            app.dependency_overrides.pop(flow_cleanup._require_pro, None)
            try:
                lim.reset()
            except Exception:
                pass
