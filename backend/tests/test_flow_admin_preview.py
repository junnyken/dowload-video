"""
Tests for Admin-Only Preview Mode on Logo Cleanup (`feat/logo-admin-preview`)
=============================================================================
Covers:
1. `FLOW_ACCESS_MODE`: `"off"` (default) | `"admin"` | `"pro"` across `/api/v1/flow-cleanup/*`
   matrix (`guest` / `free` / `pro` / `admin`), including signed-URL endpoints and client-spoofing rejection.
2. Admin-mode video limits: `<= 720p <= 30s`, `1080p <= 15s`, `> 1080p` rejected (both landscape & portrait).
3. Admin-mode global 1-job Redis lock across all users (`"đang bận, thử lại sau"`), released on completion/failure/cancel.
4. `GET /api/v1/user/me` & `GET /api/v1/plans/me` server-verified `is_admin` flag (never trusting client headers).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import flow_cleanup as fc
from app.core.auth_middleware import get_optional_user, get_required_user
from app.main import app


class _FakeQuery:
    def __init__(self, rows_by_table: dict[str, list[dict]]):
        self._rows_by_table = rows_by_table
        self._table = ""
        self._filters: list[tuple[str, object]] = []
        self._single = False
        self._limit: int | None = None

    def table(self, name: str) -> "_FakeQuery":
        q = _FakeQuery(self._rows_by_table)
        q._table = name
        return q

    def select(self, *_args, **_kwargs) -> "_FakeQuery":
        return self

    def eq(self, col: str, val: object) -> "_FakeQuery":
        self._filters.append((col, val))
        return self

    def limit(self, n: int) -> "_FakeQuery":
        self._limit = n
        return self

    def maybe_single(self) -> "_FakeQuery":
        self._single = True
        return self

    def execute(self):
        rows = list(self._rows_by_table.get(self._table, []))
        for col, val in self._filters:
            rows = [r for r in rows if r.get(col) == val]
        if self._limit is not None:
            rows = rows[: self._limit]
        res = MagicMock()
        if self._single:
            res.data = rows[0] if rows else None
        else:
            res.data = rows
        return res


def _make_fake_db(profiles: list[dict], admin_users: list[dict] | None = None):
    return _FakeQuery({
        "profiles": profiles,
        "admin_users": admin_users or [],
    })


@pytest.fixture()
def isolated_flow_env(tmp_path, monkeypatch):
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_DOWNLOAD_DIR", str(dl_dir))
    fake_redis = fc._InMemoryAtomicRedis()
    monkeypatch.setattr(fc, "_REDIS_CLIENT", fake_redis)
    monkeypatch.setenv("FLOW_REDIS_NAMESPACE", f"test_admin_preview_{tmp_path.name}")
    yield {"dl_dir": dl_dir, "redis": fake_redis}
    app.dependency_overrides.pop(get_optional_user, None)
    app.dependency_overrides.pop(get_required_user, None)


def _seed_uploaded_job(dl_dir, owner_id: str, width: int = 1280, height: int = 720, duration: float = 10.0) -> str:
    temp_id = ("a1b2c3d4" * 4)[:32]
    return _seed_uploaded_job_with_id(dl_dir, temp_id, owner_id, width=width, height=height, duration=duration)


def _seed_uploaded_job_with_id(
    dl_dir,
    temp_id: str,
    owner_id: str,
    width: int = 1280,
    height: int = 720,
    duration: float = 10.0,
) -> str:
    wdir = dl_dir / f"flow_{temp_id}"
    wdir.mkdir(parents=True, exist_ok=True)
    in_mp4 = wdir / "input.mp4"
    in_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
    (wdir / "preview.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 128)
    (wdir / "preview_clean.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 128)
    (wdir / "preview_clip_clean.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)
    (wdir / "cleaned_testout.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 512)
    fc._write_meta(
        str(wdir),
        {
            "temp_id": temp_id,
            "job_id": temp_id,
            "owner_user_id": owner_id,
            "status": "uploaded",
            "progress": 0,
            "video_info": {
                "width": width,
                "height": height,
                "duration": duration,
                "fps": 30.0,
                "nb_frames": int(duration * 30),
            },
        },
    )
    return temp_id


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Default FLOW_ACCESS_MODE + Full Access Matrix (off / admin / pro)
# ═══════════════════════════════════════════════════════════════════════════════

class TestFlowAccessModeMatrix:

    def test_default_mode_when_env_unset_is_off(self, monkeypatch):
        monkeypatch.delenv("FLOW_ACCESS_MODE", raising=False)
        assert fc._get_flow_access_mode() == "off"

        monkeypatch.setenv("FLOW_ACCESS_MODE", "invalid_value")
        assert fc._get_flow_access_mode() == "off"

    @pytest.mark.parametrize("role_kind", ["guest", "free", "pro", "admin"])
    def test_mode_off_rejects_everyone_across_endpoints(self, isolated_flow_env, monkeypatch, role_kind):
        monkeypatch.delenv("FLOW_ACCESS_MODE", raising=False)

        profiles = [
            {"id": "u-free", "email": "free@test.com", "tier": "free", "billing_status": "none"},
            {"id": "u-pro", "email": "pro@test.com", "tier": "pro", "billing_status": "active"},
            {"id": "u-admin", "email": "admin@test.com", "tier": "admin", "role": "admin", "billing_status": "active"},
        ]
        user_map = {
            "guest": None,
            "free": {"id": "u-free", "email": "free@test.com"},
            "pro": {"id": "u-pro", "email": "pro@test.com"},
            "admin": {"id": "u-admin", "email": "admin@test.com"},
        }
        cur_user = user_map[role_kind]
        owner_id = (cur_user or {}).get("id", "u-admin")
        tid = _seed_uploaded_job(isolated_flow_env["dl_dir"], owner_id=owner_id)
        cleaned_fid = fc.file_id_for(str(isolated_flow_env["dl_dir"] / f"flow_{tid}" / "cleaned_testout.mp4"))
        signed_frame = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", owner_id)
        signed_clean = fc._build_signed_url(f"/api/v1/flow-cleanup/preview-clean/{tid}", tid, "preview_clean", owner_id)
        signed_clip = fc._build_signed_url(f"/api/v1/flow-cleanup/preview-clip-video/{tid}", tid, "preview_clip", owner_id)

        async def _override_user():
            return cur_user

        app.dependency_overrides[get_optional_user] = _override_user
        fake_db = _make_fake_db(profiles)
        client = TestClient(app)

        with patch("app.core.database.get_service_client", return_value=fake_db):
            assert client.get(f"/api/v1/flow-cleanup/status/{tid}").status_code in (403, 404)
            assert client.post("/api/v1/flow-cleanup/upload", files={"file": ("v.mp4", b"1234", "video/mp4")}).status_code in (403, 404)
            assert client.post("/api/v1/flow-cleanup/process", json={"temp_id": tid, "consent_confirmed": True}).status_code in (403, 404)
            assert client.post("/api/v1/flow-cleanup/from-local", json={"local_path": "x.mp4"}).status_code in (403, 404)
            assert client.post(f"/api/v1/flow-cleanup/detect/{tid}").status_code in (403, 404)
            assert client.post(f"/api/v1/flow-cleanup/track/{tid}", json={}).status_code in (403, 404)
            assert client.post("/api/v1/flow-cleanup/preview-frame", json={"temp_id": tid, "preset": "lower-right"}).status_code in (403, 404)
            assert client.post("/api/v1/flow-cleanup/preview-clip", json={"temp_id": tid, "preset": "lower-right"}).status_code in (403, 404)
            assert client.post(f"/api/v1/flow-cleanup/cancel/{tid}").status_code in (403, 404)
            assert client.post(f"/api/v1/flow-cleanup/retry/{tid}").status_code in (403, 404)
            assert client.post("/api/v1/flow-cleanup/no-logo", json={"temp_id": tid}).status_code in (403, 404)
            assert client.get(f"/api/v1/flow-cleanup/frame-url/{tid}").status_code in (403, 404)
            assert client.get(f"/api/v1/flow-cleanup/result/{tid}/{cleaned_fid}").status_code in (403, 404)
            assert client.get(signed_frame).status_code in (403, 404)
            assert client.get(signed_clean).status_code in (403, 404)
            assert client.get(signed_clip).status_code in (403, 404)

    def test_mode_admin_blocks_guest_free_and_pro_even_with_spoofed_headers(self, isolated_flow_env, monkeypatch):
        """Regression: Pro users calling API directly MUST be rejected (403) when FLOW_ACCESS_MODE='admin'."""
        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        monkeypatch.setenv("ADMIN_SECRET_TOKEN", "real-admin-panel-secret")

        profiles = [
            {"id": "u-free", "email": "free@test.com", "tier": "free", "billing_status": "none"},
            {"id": "u-pro", "email": "pro@test.com", "tier": "pro", "billing_status": "active"},
            {"id": "u-team", "email": "team@test.com", "tier": "team", "billing_status": "active"},
            # Leftover enterprise with billing_status='none' must NOT count as admin
            {"id": "u-leftover", "email": "leftover@test.com", "tier": "enterprise", "billing_status": "none"},
        ]
        fake_db = _make_fake_db(profiles)
        client = TestClient(app)

        # 1. Guest -> 401
        async def _guest():
            return None
        app.dependency_overrides[get_optional_user] = _guest
        with patch("app.core.database.get_service_client", return_value=fake_db):
            assert client.get("/api/v1/flow-cleanup/status/a1b2c3d4e5f6a1b2").status_code == 401

        # 2. Free, Pro, Team, Leftover-Enterprise, and API-key Pro -> ALL 403 even if spoofing X-Admin-Token!
        spoof_headers = {
            "X-Admin-Token": "real-admin-panel-secret",
            "X-Is-Admin": "true",
            "X-User-Role": "admin",
        }
        for non_admin_user in (
            {"id": "u-free", "email": "free@test.com"},
            {"id": "u-pro", "email": "pro@test.com"},
            {"id": "u-pro", "email": "pro@test.com", "tier": "pro", "via_api_key": True},
            {"id": "u-team", "email": "team@test.com"},
            {"id": "u-leftover", "email": "leftover@test.com"},
            {"id": "rapidapi:spoof", "tier": "enterprise", "via_rapidapi": True},
        ):
            tid = _seed_uploaded_job(isolated_flow_env["dl_dir"], owner_id=non_admin_user["id"])

            async def _u(u=non_admin_user):
                return u

            app.dependency_overrides[get_optional_user] = _u
            with patch("app.core.database.get_service_client", return_value=fake_db):
                r_st = client.get(f"/api/v1/flow-cleanup/status/{tid}", headers=spoof_headers)
                assert r_st.status_code == 403, f"Expected 403 for {non_admin_user}, got {r_st.status_code}"
                r_up = client.post(
                    "/api/v1/flow-cleanup/upload",
                    files={"file": ("v.mp4", b"1234", "video/mp4")},
                    headers=spoof_headers,
                )
                assert r_up.status_code == 403

                # Signed frame URL owned by a non-admin user must ALSO be rejected in admin mode
                signed_non_admin = fc._build_signed_url(
                    f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", non_admin_user["id"]
                )
                assert client.get(signed_non_admin).status_code in (403, 404)

    @pytest.mark.parametrize(
        "admin_profile,admin_users_rows",
        [
            ({"id": "adm-1", "email": "a1@test.com", "tier": "admin", "billing_status": "active"}, []),
            ({"id": "adm-2", "email": "a2@test.com", "tier": "enterprise", "billing_status": "active"}, []),
            ({"id": "adm-3", "email": "a3@test.com", "tier": "free", "role": "admin", "billing_status": "none"}, []),
            (
                {"id": "adm-4", "email": "owner@vidgrab.io", "tier": "free", "billing_status": "none"},
                [{"email": "owner@vidgrab.io", "role": "superadmin", "is_active": True}],
            ),
        ],
    )
    def test_mode_admin_allows_db_verified_admin(
        self, isolated_flow_env, monkeypatch, admin_profile, admin_users_rows
    ):
        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        uid = admin_profile["id"]
        tid = _seed_uploaded_job(isolated_flow_env["dl_dir"], owner_id=uid)
        cleaned_fid = fc.file_id_for(str(isolated_flow_env["dl_dir"] / f"flow_{tid}" / "cleaned_testout.mp4"))
        signed_frame = fc._build_signed_url(f"/api/v1/flow-cleanup/frame/{tid}", tid, "frame", uid)

        async def _adm():
            return {"id": uid, "email": admin_profile["email"]}

        app.dependency_overrides[get_optional_user] = _adm
        fake_db = _make_fake_db([admin_profile], admin_users=admin_users_rows)
        client = TestClient(app)

        with patch("app.core.database.get_service_client", return_value=fake_db):
            r = client.get(f"/api/v1/flow-cleanup/status/{tid}")
            assert r.status_code == 200, r.text
            assert client.get(signed_frame).status_code == 200
            assert client.get(f"/api/v1/flow-cleanup/result/{tid}/{cleaned_fid}").status_code == 200

    def test_mode_admin_fails_closed_503_when_db_down(self, isolated_flow_env, monkeypatch):
        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        tid = _seed_uploaded_job(isolated_flow_env["dl_dir"], owner_id="adm-1")

        async def _adm():
            return {"id": "adm-1", "email": "a1@test.com"}

        app.dependency_overrides[get_optional_user] = _adm
        client = TestClient(app)

        with patch("app.core.database.get_service_client", side_effect=RuntimeError("DB unreachable")):
            r = client.get(f"/api/v1/flow-cleanup/status/{tid}")
            assert r.status_code == 503
            assert r.json()["detail"]["error_code"] == "entitlement_check_failed"

    def test_mode_pro_preserves_existing_pro_and_admin_behavior(self, isolated_flow_env, monkeypatch):
        monkeypatch.setenv("FLOW_ACCESS_MODE", "pro")
        profiles = [
            {"id": "u-free", "email": "free@test.com", "tier": "free", "billing_status": "none"},
            {"id": "u-pro", "email": "pro@test.com", "tier": "pro", "billing_status": "active"},
            {"id": "u-admin", "email": "admin@test.com", "tier": "admin", "billing_status": "active"},
        ]
        fake_db = _make_fake_db(profiles)
        client = TestClient(app)

        # Guest -> 401
        async def _guest():
            return None
        app.dependency_overrides[get_optional_user] = _guest
        assert client.get("/api/v1/flow-cleanup/status/a1b2c3d4e5f6a1b2").status_code == 401

        # Free -> 402
        tid_free = _seed_uploaded_job_with_id(isolated_flow_env["dl_dir"], "b1b2c3d4" * 4, "u-free")
        async def _free():
            return {"id": "u-free", "email": "free@test.com"}
        app.dependency_overrides[get_optional_user] = _free
        with patch("app.core.database.get_service_client", return_value=fake_db):
            assert client.get(f"/api/v1/flow-cleanup/status/{tid_free}").status_code == 402

        # Pro -> 200
        tid_pro = _seed_uploaded_job_with_id(isolated_flow_env["dl_dir"], "c1b2c3d4" * 4, "u-pro")
        async def _pro():
            return {"id": "u-pro", "email": "pro@test.com"}
        app.dependency_overrides[get_optional_user] = _pro
        with patch("app.core.database.get_service_client", return_value=fake_db):
            assert client.get(f"/api/v1/flow-cleanup/status/{tid_pro}").status_code == 200

        # Admin -> 200
        tid_adm = _seed_uploaded_job_with_id(isolated_flow_env["dl_dir"], "d1b2c3d4" * 4, "u-admin")
        async def _adm():
            return {"id": "u-admin", "email": "admin@test.com"}
        app.dependency_overrides[get_optional_user] = _adm
        with patch("app.core.database.get_service_client", return_value=fake_db):
            assert client.get(f"/api/v1/flow-cleanup/status/{tid_adm}").status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Admin-Mode Resolution & Duration Limits (1080p <= 15s, <= 720p <= 30s)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminModeVideoLimits:

    def test_admin_mode_720p_allows_up_to_30s_and_rejects_over_30s(self, tmp_path, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        dummy = tmp_path / "vid.mp4"
        dummy.write_bytes(b"\x00" * 1024)

        # Landscape 1280x720 and Portrait 720x1280 at 30s -> allowed
        for vw, vh in ((1280, 720), (720, 1280), (854, 480)):
            fc._validate_job_limits(str(dummy), {"width": vw, "height": vh, "duration": 30.0, "fps": 30.0}, method="natural")
            fc._validate_job_limits(str(dummy), {"width": vw, "height": vh, "duration": 30.0, "fps": 30.0}, method="crop")

        # Over 30s at 720p -> rejected (even for crop!)
        for vw, vh in ((1280, 720), (720, 1280)):
            for method in ("natural", "crop"):
                with pytest.raises(HTTPException) as exc:
                    fc._validate_job_limits(str(dummy), {"width": vw, "height": vh, "duration": 30.5, "fps": 30.0}, method=method)
                assert exc.value.status_code == 400
                assert exc.value.detail["error_code"] == "duration_limit_exceeded"

    def test_admin_mode_1080p_allows_up_to_15s_and_rejects_over_15s(self, tmp_path, monkeypatch):
        from fastapi import HTTPException

        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        dummy = tmp_path / "vid1080.mp4"
        dummy.write_bytes(b"\x00" * 1024)

        # Landscape 1920x1080 and Portrait 1080x1920 at 15s -> allowed
        for vw, vh in ((1920, 1080), (1080, 1920)):
            fc._validate_job_limits(str(dummy), {"width": vw, "height": vh, "duration": 15.0, "fps": 30.0}, method="natural")
            fc._validate_job_limits(str(dummy), {"width": vw, "height": vh, "duration": 15.0, "fps": 30.0}, method="crop")

        # 16s at 1080p -> rejected (even for crop!)
        for vw, vh in ((1920, 1080), (1080, 1920)):
            for method in ("natural", "crop"):
                with pytest.raises(HTTPException) as exc:
                    fc._validate_job_limits(str(dummy), {"width": vw, "height": vh, "duration": 16.0, "fps": 30.0}, method=method)
                assert exc.value.status_code == 400
                assert exc.value.detail["error_code"] == "duration_limit_exceeded"

    def test_admin_mode_rejects_above_1080p_while_pro_mode_allows_it(self, tmp_path, monkeypatch):
        from fastapi import HTTPException

        dummy = tmp_path / "vid4k.mp4"
        dummy.write_bytes(b"\x00" * 1024)

        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        with pytest.raises(HTTPException) as exc:
            fc._validate_job_limits(str(dummy), {"width": 3840, "height": 2160, "duration": 5.0, "fps": 30.0}, method="natural")
        assert exc.value.status_code == 400
        assert exc.value.detail["error_code"] == "resolution_limit_exceeded"

        # Pro mode still allows 4K resolution (up to 4K 3840x2160)
        monkeypatch.setenv("FLOW_ACCESS_MODE", "pro")
        fc._validate_job_limits(str(dummy), {"width": 3840, "height": 2160, "duration": 2.0, "fps": 30.0}, method="natural")
        fc._validate_job_limits(str(dummy), {"width": 3840, "height": 2160, "duration": 10.0, "fps": 30.0}, method="crop")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Global 1-Job Redis Concurrency Lock in Admin Mode
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminModeGlobalSingleJobLock:

    def test_second_concurrent_job_across_different_admins_is_rejected_until_first_finishes(
        self, isolated_flow_env, monkeypatch
    ):
        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        profiles = [
            {"id": "adm-a", "email": "a@test.com", "tier": "admin", "billing_status": "active"},
            {"id": "adm-b", "email": "b@test.com", "tier": "admin", "billing_status": "active"},
        ]
        fake_db = _make_fake_db(profiles)

        tid_1 = _seed_uploaded_job_with_id(isolated_flow_env["dl_dir"], "11112222" * 4, "adm-a", 1280, 720, 10.0)
        tid_2 = _seed_uploaded_job_with_id(isolated_flow_env["dl_dir"], "33334444" * 4, "adm-b", 1280, 720, 10.0)

        client = TestClient(app)

        with patch("app.core.database.get_service_client", return_value=fake_db), \
             patch.object(fc, "_probe", return_value={"width": 1280, "height": 720, "duration": 10.0, "fps": 30.0}), \
             patch.object(fc, "_enqueue_cleanup_task", side_effect=["celery-task-1", "celery-task-2"]):

            # Admin A dispatches Job 1 -> queued (acquires global lock)
            async def _adm_a():
                return {"id": "adm-a", "email": "a@test.com"}
            app.dependency_overrides[get_optional_user] = _adm_a

            r1 = client.post(
                "/api/v1/flow-cleanup/process",
                json={
                    "temp_id": tid_1,
                    "preset": "lower-right",
                    "method": "natural",
                    "region_adjusted": True,
                    "consent_confirmed": True,
                },
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["status"] == "queued"

            # Admin B tries to dispatch Job 2 while Job 1 is active -> 429 "đang bận, thử lại sau"
            async def _adm_b():
                return {"id": "adm-b", "email": "b@test.com"}
            app.dependency_overrides[get_optional_user] = _adm_b

            r2_busy = client.post(
                "/api/v1/flow-cleanup/process",
                json={
                    "temp_id": tid_2,
                    "preset": "lower-right",
                    "method": "natural",
                    "region_adjusted": True,
                    "consent_confirmed": True,
                },
            )
            assert r2_busy.status_code == 429, r2_busy.text
            msg = r2_busy.json()["detail"]["user_message"].lower()
            assert "đang bận" in msg
            assert "thử lại sau" in msg

            # Job 1 finishes -> lock released -> Admin B's Job 2 now succeeds!
            wdir_1 = isolated_flow_env["dl_dir"] / f"flow_{tid_1}"
            fc._write_meta(str(wdir_1), {"status": "completed", "progress": 100})

            r2_ok = client.post(
                "/api/v1/flow-cleanup/process",
                json={
                    "temp_id": tid_2,
                    "preset": "lower-right",
                    "method": "natural",
                    "region_adjusted": True,
                    "consent_confirmed": True,
                },
            )
            assert r2_ok.status_code == 200, r2_ok.text
            assert r2_ok.json()["status"] == "queued"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Authenticated Profile /me Endpoint Reports Server-Verified Admin Status
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserMeReportsServerVerifiedAdmin:

    def test_user_me_returns_is_admin_from_db_never_headers(self, monkeypatch):
        monkeypatch.setenv("FLOW_ACCESS_MODE", "admin")
        monkeypatch.setenv("ADMIN_SECRET_TOKEN", "panel-secret")

        profiles = [
            {"id": "u-pro", "email": "pro@test.com", "display_name": "Pro", "tier": "pro", "billing_status": "active"},
            {"id": "u-admin", "email": "adm@test.com", "display_name": "Admin", "tier": "enterprise", "billing_status": "active"},
        ]
        fake_db = _make_fake_db(profiles)
        client = TestClient(app)

        # Pro user sending X-Admin-Token header still gets is_admin=False
        async def _pro():
            return {"id": "u-pro", "email": "pro@test.com"}
        app.dependency_overrides[get_required_user] = _pro
        try:
            with patch("app.api.user.get_supabase_client", return_value=fake_db), \
                 patch("app.core.database.get_service_client", return_value=fake_db):
                r_pro = client.get("/api/v1/user/me", headers={"X-Admin-Token": "panel-secret"})
                assert r_pro.status_code == 200
                body_pro = r_pro.json()
                assert body_pro["is_admin"] is False
                assert body_pro["flow_access_mode"] == "admin"

            # DB-verified Admin user gets is_admin=True
            async def _adm():
                return {"id": "u-admin", "email": "adm@test.com"}
            app.dependency_overrides[get_required_user] = _adm
            with patch("app.api.user.get_supabase_client", return_value=fake_db), \
                 patch("app.core.database.get_service_client", return_value=fake_db):
                r_adm = client.get("/api/v1/user/me")
                assert r_adm.status_code == 200
                body_adm = r_adm.json()
                assert body_adm["is_admin"] is True
                assert body_adm["flow_access_mode"] == "admin"
        finally:
            app.dependency_overrides.pop(get_required_user, None)

    def test_admin_tier_preserved_even_when_billing_status_canceled(self):
        from app.core.entitlements import resolve_effective_tier, is_user_admin_db

        profile = {"id": "u-adm2", "email": "owner@test.com", "tier": "admin", "billing_status": "canceled"}
        assert resolve_effective_tier(profile) == "admin"
        fake_db = _make_fake_db([profile])
        assert is_user_admin_db("u-adm2", email="owner@test.com", db=fake_db) is True

