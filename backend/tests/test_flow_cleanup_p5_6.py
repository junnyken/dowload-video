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


