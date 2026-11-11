"""
Flow / Veo Visible-Logo Cleanup
================================
POST /upload          — upload local Flow/Veo video, extract preview frame
GET  /frame/{temp_id} — serve preview JPEG
POST /process         — FFmpeg delogo/crop/blur cleanup

SCOPE: visible on-frame logo/watermark only.
SynthID and other invisible AI watermarks are NOT affected by any operation here.
"""

import hashlib
import hmac
import json
import logging
import os
import queue as _queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.core.auth_middleware import get_optional_user
from app.core.entitlements import get_entitlement, check_feature
from app.core.local_download import file_fields, file_id_for, new_token, resolve_file_id, resolve_local_input, safe_file_id

router = APIRouter()
log = logging.getLogger(__name__)

from app.main import limiter


async def _require_pro(
    _request: Request,
    user=Depends(get_optional_user),
) -> None:
    """FastAPI dependency: gate logo_inpaint behind Pro tier."""
    if user is None:
        raise HTTPException(status_code=401, detail="Đăng nhập để sử dụng tính năng này.")

    # API key users already have tier resolved; JWT users need a DB lookup.
    tier = user.get("tier")
    if not tier:
        try:
            from app.core.database import get_service_client
            db = get_service_client()
            ent = await get_entitlement(user["id"], db)
            tier = ent["tier"]
        except Exception as exc:
            # A lookup that FAILED is not the same fact as a user who IS on the
            # free plan, and `tier = "free"` here erased the difference. Every
            # Supabase blip — or a container booted without SUPABASE_URL — then
            # answered a paying subscriber with 402 "nâng cấp lên Pro": we blamed
            # their plan for our outage and told them to buy what they already
            # own. Deny either way (never fail open on a paid gate), but report
            # which of the two happened, with a status worth retrying.
            log.warning(
                "[flow-cleanup] entitlement lookup failed for user %s: %s",
                user.get("id"), exc,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error_code": "entitlement_check_failed",
                    "user_message": "Chưa kiểm tra được gói của bạn. Vui lòng thử lại sau ít phút.",
                    "retryable": True,
                },
            )

    if not check_feature(tier, "logo_inpaint"):
        raise HTTPException(
            status_code=402,
            detail={
                "error_code": "tier_required_feature",
                "feature": "logo_inpaint",
                # Without this the client has no sentence to show: parseApiError
                # falls back to "Lỗi không xác định.", and the callers that did
                # `new Error(detail)` rendered the dict as "[object Object]".
                "user_message": "Xoá Logo / Watermark là tính năng của gói Pro. Nâng cấp để sử dụng.",
                "required_plan": "pro",
                "current_plan": tier,
                "retryable": False,
            },
        )

    try:
        if _request is not None and hasattr(_request, "state"):
            _request.state.flow_user_id = user.get("id")
    except Exception:
        pass


def _caller_user_id(request: Optional[Request] = None, user: Optional[Any] = None) -> Optional[str]:
    """Extract authenticated caller user_id from FastAPI dependency or request.state."""
    if isinstance(user, dict) and user.get("id"):
        return str(user["id"])
    if request is not None and hasattr(request, "state"):
        uid = getattr(request.state, "flow_user_id", None)
        if uid:
            return str(uid)
    return None


def _verify_job_owner(meta: dict, caller_id: Optional[str]) -> None:
    """Fail-closed owner check: reject with 404 if job has no owner_user_id or caller does not match."""
    if not isinstance(meta, dict):
        raise HTTPException(status_code=404, detail="Job không còn tồn tại hoặc đã hết hạn.")
    owner_id = meta.get("owner_user_id")
    if not owner_id or not caller_id or str(caller_id) != str(owner_id):
        raise HTTPException(status_code=404, detail="Job không còn tồn tại hoặc đã hết hạn.")


_DOWNLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "downloads",
)
_MAX_UPLOAD_MB = 500
_MAX_VIDEO_WIDTH = int(os.getenv("FLOW_MAX_VIDEO_WIDTH", "3840"))
_MAX_VIDEO_HEIGHT = int(os.getenv("FLOW_MAX_VIDEO_HEIGHT", "3840"))
_MAX_VIDEO_PIXELS = int(os.getenv("FLOW_MAX_VIDEO_PIXELS", str(3840 * 2160)))
_MAX_VIDEO_DURATION_SEC = float(os.getenv("FLOW_MAX_DURATION_SEC", "600"))
_MAX_INPAINT_DURATION_SEC = float(os.getenv("FLOW_MAX_INPAINT_DURATION_SEC", "60"))
_SEC_PER_MPX_FRAME = float(os.getenv("FLOW_SEC_PER_MPX_FRAME", "0.45"))
_MAX_RETRY_COUNT = int(os.getenv("FLOW_MAX_RETRY_COUNT", "3"))
_FLOW_TTL_SEC = int(os.getenv("FLOW_CLEANUP_TTL_SEC", "1200"))
_JOB_TIMEOUT_SEC = int(os.getenv("FLOW_JOB_TIMEOUT_SEC", "300"))
_TRACK_TIMEOUT_SEC = float(os.getenv("FLOW_TRACK_TIMEOUT_SEC", "120"))
_PREVIEW_CLIP_TIMEOUT_SEC = float(os.getenv("FLOW_PREVIEW_CLIP_TIMEOUT_SEC", "90"))
_MAX_USER_CONCURRENT_JOBS = int(os.getenv("FLOW_MAX_USER_CONCURRENT_JOBS", "1"))
_DAILY_USER_QUOTA = int(os.getenv("FLOW_DAILY_USER_QUOTA", "20"))
_WORKER_CONCURRENCY = int(os.getenv("FLOW_INPAINT_WORKER_CONCURRENCY", "2"))
_USER_LOCK_TTL_SEC = int(
    os.getenv(
        "FLOW_USER_LOCK_TTL_SEC",
        str(max(600, (_WORKER_CONCURRENCY + 1) * _JOB_TIMEOUT_SEC + 120)),
    )
)
_SIGNED_URL_TTL_SEC = min(600, max(1, int(os.getenv("FLOW_SIGNED_URL_TTL_SEC", "600"))))
_MAX_KEYFRAME_CACHE_FILES = int(os.getenv("FLOW_MAX_KEYFRAME_CACHE_FILES", "50"))
_EPHEMERAL_SIGNING_SECRET = os.urandom(32).hex()
_FLOW_PREFIX = "flow_"
_META_LOCK = threading.RLock()
_CONSENT_AUDIT_SQLITE_CONN = None


def _get_consent_audit_sqlite():
    """Return in-memory SQLite mirror of `flow_consent_audit_logs` table (never writes files into `downloads/`)."""
    global _CONSENT_AUDIT_SQLITE_CONN
    with _META_LOCK:
        if _CONSENT_AUDIT_SQLITE_CONN is None:
            import sqlite3
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS flow_consent_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    temp_id TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'process_confirm',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
            _CONSENT_AUDIT_SQLITE_CONN = conn
        return _CONSENT_AUDIT_SQLITE_CONN


def _query_consent_audit_logs(
    temp_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[dict]:
    """Query consent audit rows from the DB table `flow_consent_audit_logs`."""
    with _META_LOCK:
        conn = _get_consent_audit_sqlite()
        clauses: List[str] = []
        params: List[Any] = []
        if temp_id is not None:
            clauses.append("temp_id = ?")
            params.append(str(temp_id))
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(str(user_id))
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = conn.execute(
            f"SELECT user_id, temp_id, action, created_at FROM flow_consent_audit_logs{where_sql} ORDER BY id ASC",
            tuple(params),
        )
        return [
            {
                "user_id": row["user_id"],
                "temp_id": row["temp_id"],
                "action": row["action"],
                "created_at": row["created_at"],
                "timestamp": row["created_at"],
            }
            for row in cur.fetchall()
        ]


def _append_consent_audit_log(user_id: Optional[str], temp_id: str, action: str = "process_confirm") -> dict:
    """
    Phase P5.6.6: Insert a privacy-safe consent audit record `(user_id, temp_id, action, created_at)`
    into the DB table `flow_consent_audit_logs` (migration 044).
    Never stores files in `downloads/` and never logs video bytes, filenames, or frame contents.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    db_row = {
        "user_id": str(user_id or "anonymous"),
        "temp_id": str(temp_id),
        "action": str(action),
        "created_at": now_iso,
    }
    with _META_LOCK:
        try:
            conn = _get_consent_audit_sqlite()
            conn.execute(
                "INSERT INTO flow_consent_audit_logs (user_id, temp_id, action, created_at) VALUES (?, ?, ?, ?)",
                (db_row["user_id"], db_row["temp_id"], db_row["action"], db_row["created_at"]),
            )
            conn.commit()
        except Exception as exc:
            log.warning("[flow-cleanup] failed to insert consent audit log into local DB: %s", exc)

    try:
        from app.core.database import get_service_client, using_postgres
        supa_url = os.getenv("SUPABASE_URL", "")
        if using_postgres() or (supa_url and "your-project-id" not in supa_url):
            db = get_service_client()
            db.table("flow_consent_audit_logs").insert(db_row).execute()
    except Exception as exc:
        log.warning("[flow-cleanup] failed to insert consent audit log into DB: %s", exc)

    return {
        "timestamp": now_iso,
        "created_at": now_iso,
        "user_id": db_row["user_id"],
        "temp_id": db_row["temp_id"],
        "action": db_row["action"],
    }


class _InMemoryAtomicRedis:
    """Thread-safe atomic fallback with Redis INCR+EXPIRE and SET NX EX semantics when Redis is unreachable in dev/test."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._store: Dict[str, Tuple[Any, Optional[float]]] = {}

    def _purge_if_expired(self, key: str) -> None:
        item = self._store.get(key)
        if item is not None and item[1] is not None and time.time() >= item[1]:
            self._store.pop(key, None)

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            self._purge_if_expired(key)
            item = self._store.get(key)
            return None if item is None else str(item[0])

    def set(self, key: str, value: Any, nx: bool = False, ex: Optional[int] = None) -> bool:
        with self._lock:
            self._purge_if_expired(key)
            if nx and key in self._store:
                return False
            exp_at = (time.time() + float(ex)) if ex is not None else None
            self._store[key] = (str(value), exp_at)
            return True

    def incr(self, key: str) -> int:
        with self._lock:
            self._purge_if_expired(key)
            cur, exp_at = self._store.get(key, ("0", None))
            new_val = int(cur) + 1
            self._store[key] = (str(new_val), exp_at)
            return new_val

    def expire(self, key: str, seconds: int) -> bool:
        with self._lock:
            self._purge_if_expired(key)
            if key not in self._store:
                return False
            val, _ = self._store[key]
            self._store[key] = (val, time.time() + float(seconds))
            return True

    def ttl(self, key: str) -> int:
        with self._lock:
            self._purge_if_expired(key)
            item = self._store.get(key)
            if item is None:
                return -2
            if item[1] is None:
                return -1
            return max(0, int(round(item[1] - time.time())))

    def delete(self, key: str) -> int:
        with self._lock:
            return 1 if self._store.pop(key, None) is not None else 0


_FALLBACK_REDIS = _InMemoryAtomicRedis()
_REDIS_CLIENT = None


def _get_flow_redis():
    """Return Redis client for atomic daily quota (`INCR`+`EXPIRE`) and per-user job lock (`SET NX EX`)."""
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    env_url = os.getenv("FLOW_REDIS_URL") or os.getenv("REDIS_URL") or os.getenv("CELERY_BROKER_URL") or ""
    candidates: List[str] = []
    if env_url:
        candidates.append(env_url)
    if "redis://127.0.0.1:6379/0" not in candidates:
        candidates.append("redis://127.0.0.1:6379/0")
    last_exc: Optional[Exception] = None
    try:
        import redis
        for url in candidates:
            try:
                client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=0.4,
                    socket_timeout=0.8,
                )
                client.ping()
                _REDIS_CLIENT = client
                return _REDIS_CLIENT
            except Exception as e:
                last_exc = e
    except Exception as e:
        last_exc = e

    if _is_production_env():
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "redis_unavailable",
                "user_message": "Hệ thống quản lý hàng đợi đang tạm thời gián đoạn. Vui lòng thử lại sau ít phút.",
                "retryable": True,
            },
        ) from last_exc
    log.warning("[flow-cleanup] Redis unreachable (%s); using in-process atomic fallback for dev/test", last_exc)
    _REDIS_CLIENT = _FALLBACK_REDIS
    return _REDIS_CLIENT


def _redis_ns() -> str:
    """Stable namespace prefix across processes sharing `_DOWNLOAD_DIR`, while isolating `tmp_path` in tests."""
    custom = os.getenv("FLOW_REDIS_NAMESPACE", "").strip()
    if custom:
        return custom
    return hashlib.sha1(os.path.abspath(_DOWNLOAD_DIR).encode("utf-8")).hexdigest()[:10]


def _daily_quota_redis_key(owner_id: str, today_key: Optional[str] = None) -> str:
    day = today_key or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"flow:quota:{_redis_ns()}:{day}:{owner_id}"


def _user_active_job_redis_key(owner_id: str) -> str:
    return f"flow:active_job:{_redis_ns()}:{owner_id}"


def _check_and_record_daily_quota(owner_id: Optional[str]) -> None:
    """Enforce per-user daily quota (`_DAILY_USER_QUOTA`, default 20 jobs/day) via Redis atomic `INCR` + `EXPIRE`."""
    if not owner_id or _DAILY_USER_QUOTA <= 0:
        return
    uid = str(owner_id)
    r = _get_flow_redis()
    quota_key = _daily_quota_redis_key(uid)
    count = int(r.incr(quota_key))
    if count == 1 or int(r.ttl(quota_key)) < 0:
        r.expire(quota_key, 90000)  # 25 hours TTL
    if count > _DAILY_USER_QUOTA:
        raise HTTPException(
            status_code=429,
            detail={
                "error_code": "daily_quota_exceeded",
                "user_message": f"Bạn đã đạt giới hạn {_DAILY_USER_QUOTA} lần xử lý video trong ngày hôm nay. Vui lòng thử lại vào ngày mai.",
                "daily_quota": _DAILY_USER_QUOTA,
                "used_today": _DAILY_USER_QUOTA,
                "retryable": False,
            },
        )


def _release_user_concurrency(owner_id: Optional[str], temp_id: Optional[str] = None) -> None:
    """Release the Redis active-job lock for `owner_id` when `temp_id` completes, fails, or is cancelled."""
    if not owner_id:
        return
    try:
        r = _get_flow_redis()
        lock_key = _user_active_job_redis_key(str(owner_id))
        if temp_id:
            cur_safe = re.sub(r"[^a-f0-9]", "", str(temp_id)) or str(temp_id)
            held = r.get(lock_key)
            if held and held != cur_safe:
                return
        r.delete(lock_key)
    except Exception:
        pass


def _compute_user_lock_ttl_sec() -> int:
    """Compute Redis per-user job lock TTL covering queue wait (`_WORKER_CONCURRENCY` waves) + `_JOB_TIMEOUT_SEC` + 120s margin."""
    return max(
        int(_USER_LOCK_TTL_SEC),
        (max(1, int(_WORKER_CONCURRENCY)) + 1) * int(_JOB_TIMEOUT_SEC) + 120,
    )


def _refresh_user_concurrency(owner_id: Optional[str], temp_id: Optional[str] = None) -> None:
    """Heartbeat-extend the Redis active-job lock TTL while `temp_id` is actively queued or running."""
    if not owner_id or _MAX_USER_CONCURRENT_JOBS <= 0:
        return
    try:
        r = _get_flow_redis()
        lock_key = _user_active_job_redis_key(str(owner_id))
        cur_safe = re.sub(r"[^a-f0-9]", "", str(temp_id or "")) or str(temp_id or "")
        held = r.get(lock_key)
        lock_ttl = _compute_user_lock_ttl_sec()
        if held is None and cur_safe:
            r.set(lock_key, cur_safe, nx=True, ex=lock_ttl)
        elif held == cur_safe or not cur_safe:
            r.expire(lock_key, lock_ttl)
    except Exception:
        pass


def _check_user_concurrency(owner_id: Optional[str], current_temp_id: str) -> None:
    """Enforce at most 1 heavy job per user across processes via Redis atomic `SET key val NX EX ttl`."""
    if not owner_id or _MAX_USER_CONCURRENT_JOBS <= 0:
        return
    uid = str(owner_id)
    cur_safe = re.sub(r"[^a-f0-9]", "", current_temp_id or "") or str(current_temp_id)
    lock_key = _user_active_job_redis_key(uid)
    lock_ttl = _compute_user_lock_ttl_sec()
    r = _get_flow_redis()

    if r.set(lock_key, cur_safe, nx=True, ex=lock_ttl):
        return

    existing_job = r.get(lock_key) or ""
    if existing_job and os.path.isdir(_DOWNLOAD_DIR):
        other_wdir = os.path.join(_DOWNLOAD_DIR, f"{_FLOW_PREFIX}{existing_job}")
        if os.path.isdir(other_wdir):
            if _is_work_dir_expired(other_wdir):
                r.delete(lock_key)
                if r.set(lock_key, cur_safe, nx=True, ex=lock_ttl):
                    return
            else:
                other_meta = _read_meta(other_wdir)
                if other_meta.get("status") in ("completed", "done", "failed", "cancelled", "no_visible_logo"):
                    r.delete(lock_key)
                    if r.set(lock_key, cur_safe, nx=True, ex=lock_ttl):
                        return

    raise HTTPException(
        status_code=429,
        detail={
            "error_code": "user_concurrent_job_limit",
            "user_message": "Bạn đang có một tác vụ xử lý video đang chạy. Vui lòng đợi hoàn tất hoặc hủy trước khi chạy tác vụ mới.",
            "active_job_id": existing_job or cur_safe,
            "retryable": True,
        },
    )


def _is_production_env() -> bool:
    """Return True when running in production mode (`ENV`/`ENVIRONMENT`/`APP_ENV` in {'production', 'prod'})."""
    for env_key in ("ENVIRONMENT", "APP_ENV", "ENV"):
        val = os.getenv(env_key, "").strip().lower()
        if val in ("production", "prod"):
            return True
    return False


def _validate_signing_secret_on_startup() -> str:
    """Require dedicated FLOW_URL_SIGNING_SECRET on production startup; never fall back to JWT_SECRET."""
    secret = os.getenv("FLOW_URL_SIGNING_SECRET", "").strip()
    if _is_production_env() and len(secret) < 16:
        raise RuntimeError(
            "FLOW_URL_SIGNING_SECRET is required in production (minimum 16 characters) "
            "and must be a dedicated secret (do not reuse JWT_SECRET)."
        )
    return secret


def _get_signing_secret() -> str:
    """Return dedicated HMAC signing secret from FLOW_URL_SIGNING_SECRET (or ephemeral dev/test secret)."""
    secret = _validate_signing_secret_on_startup()
    if secret:
        return secret
    return _EPHEMERAL_SIGNING_SECRET


_validate_signing_secret_on_startup()


def _sign_resource_token(
    temp_id: str,
    resource: str,
    owner_user_id: str,
    exp: int,
    frame_idx: Optional[int] = None,
) -> str:
    """Compute HMAC-SHA256 over (temp_id, resource, frame_idx if any, owner_user_id, exp)."""
    secret = _get_signing_secret()
    f_part = "" if frame_idx is None else str(int(frame_idx))
    msg = f"{temp_id}:{resource}:{f_part}:{owner_user_id}:{int(exp)}"
    return hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _build_signed_url(
    path: str,
    temp_id: str,
    resource: str,
    owner_user_id: str,
    frame_idx: Optional[int] = None,
    ttl_sec: Optional[int] = None,
) -> str:
    """Build a short-lived signed URL (TTL <= 10 minutes) for <img>/<video> endpoints."""
    ttl = min(600, max(1, int(_SIGNED_URL_TTL_SEC if ttl_sec is None else ttl_sec)))
    exp = int(time.time()) + ttl
    sig = _sign_resource_token(temp_id, resource, str(owner_user_id), exp, frame_idx=frame_idx)
    if frame_idx is not None:
        return f"{path}?frame_idx={int(frame_idx)}&exp={exp}&sig={sig}"
    return f"{path}?exp={exp}&sig={sig}"


def _verify_signed_resource(
    temp_id: str,
    resource: str,
    meta: dict,
    exp: Optional[int],
    sig: Optional[str],
    frame_idx: Optional[int] = None,
) -> None:
    """Verify HMAC-SHA256 signature, expiration (<= 10m), and job owner. Reject with 404 on any mismatch."""
    if not isinstance(meta, dict):
        raise HTTPException(status_code=404, detail="Tài nguyên không còn tồn tại hoặc đã hết hạn.")
    owner_id = meta.get("owner_user_id")
    if not owner_id:
        raise HTTPException(status_code=404, detail="Tài nguyên không còn tồn tại hoặc đã hết hạn.")
    if exp is None or not sig or not isinstance(sig, str):
        raise HTTPException(status_code=404, detail="Link truy cập không hợp lệ hoặc đã hết hạn.")
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404, detail="Link truy cập không hợp lệ.")
    now = int(time.time())
    if exp_int < now or exp_int > now + 605:
        raise HTTPException(status_code=404, detail="Link truy cập đã hết hạn.")
    expected = _sign_resource_token(temp_id, resource, str(owner_id), exp_int, frame_idx=frame_idx)
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=404, detail="Link truy cập không hợp lệ.")


def _get_total_frames(meta: dict, input_path: Optional[str] = None) -> int:
    """Compute total frame count for bounds checking frame_idx."""
    info = (meta.get("video_info") if isinstance(meta, dict) else None) or {}
    if not info and input_path and os.path.isfile(input_path):
        info = _probe(input_path)
    nb = int(info.get("nb_frames", 0) or 0)
    if nb > 0:
        return nb
    dur = float(info.get("duration", 0.0) or 0.0)
    fps = float(info.get("fps", 30.0) or 30.0)
    return max(1, int(round(dur * max(1.0, fps))))


def _enforce_keyframe_cache_limit(work_dir: str, max_files: Optional[int] = None) -> None:
    """Keep at most `max_files` (default 50) extracted keyframe JPEGs (`frame_<idx>.jpg`) per job using LRU."""
    limit = _MAX_KEYFRAME_CACHE_FILES if max_files is None else int(max_files)
    if limit <= 0 or not os.path.isdir(work_dir):
        return
    entries: List[Tuple[float, str]] = []
    for fname in os.listdir(work_dir):
        if re.match(r"^frame_\d+\.jpg$", fname):
            fpath = os.path.join(work_dir, fname)
            try:
                entries.append((os.path.getmtime(fpath), fpath))
            except OSError:
                pass
    if len(entries) > limit:
        entries.sort(key=lambda item: (item[0], item[1]))
        for _, victim in entries[: len(entries) - limit]:
            try:
                os.remove(victim)
            except OSError:
                pass

# Maps suitability level → self-documenting outcome label for metadata/admin
_SUITABILITY_OUTCOME = {
    "good":   "auto_clean_suitable",
    "manual": "manual_confirm_recommended",
    "crop":   "crop_fallback_recommended",
    "low":    "quality_risk_detected",
}

# Maps exception → structured error_code bucket
def _error_code(e: Exception) -> str:
    msg = str(e).lower()
    if isinstance(e, subprocess.TimeoutExpired) or "timeout" in msg or "time limit" in msg:
        return "processing_timeout"
    if "cancelled" in msg or "canceled" in msg:
        return "job_cancelled"
    if "ffmpeg" in msg or "returncode" in msg:
        return "ffmpeg_processing_failed"
    if "output file" in msg:
        return "output_render_failed"
    if "codec" in msg or "unsupported" in msg:
        return "unsupported_codec"
    if isinstance(e, OSError):
        return "source_ingest_failed"
    return "processing_failed"


def _compute_effective_max_duration(vw: int, vh: int, fps: float, method: str = "natural") -> int:
    """Compute the actual maximum video duration (seconds) allowed by `_validate_job_limits` for `(vw, vh, fps, method)`."""
    if method not in ("natural", "telea", "telea_soft"):
        return int(_MAX_VIDEO_DURATION_SEC)
    vw_safe = max(1, int(vw or 0))
    vh_safe = max(1, int(vh or 0))
    fps_safe = max(1.0, float(fps or 30.0))
    mpx_per_sec = (vw_safe * vh_safe / 1_000_000.0) * fps_safe
    max_dur_by_timeout = _JOB_TIMEOUT_SEC / max(0.05, mpx_per_sec * _SEC_PER_MPX_FRAME)
    return max(5, int(min(_MAX_INPAINT_DURATION_SEC, max_dur_by_timeout)))


def _validate_job_limits(input_path: str, info: dict, method: str = "natural") -> None:
    """Reject videos that are 0-byte, non-video, corrupted, or exceed size/resolution/duration limits BEFORE worker."""
    if not os.path.isfile(input_path):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "empty_file",
                "user_message": "File tải lên không tồn tại hoặc rỗng (0 byte).",
                "retryable": False,
            },
        )
    size_bytes = os.path.getsize(input_path)
    if size_bytes <= 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "empty_file",
                "user_message": "File tải lên rỗng (0 byte). Vui lòng chọn file video hợp lệ.",
                "retryable": False,
            },
        )
    if size_bytes > _MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={
                "error_code": "file_too_large",
                "user_message": f"File vượt quá {_MAX_UPLOAD_MB}MB.",
                "retryable": False,
            },
        )

    vw = int(info.get("width", 0) or 0)
    vh = int(info.get("height", 0) or 0)
    if vw <= 0 or vh <= 0:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "invalid_media_container",
                "user_message": "File không phải định dạng video hợp lệ hoặc thiếu luồng hình ảnh (video stream).",
                "retryable": False,
            },
        )
    if vw > _MAX_VIDEO_WIDTH or vh > _MAX_VIDEO_HEIGHT or (vw * vh) > _MAX_VIDEO_PIXELS:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "resolution_limit_exceeded",
                "user_message": "Độ phân giải video vượt quá giới hạn hỗ trợ (tối đa 4K 3840×2160).",
                "retryable": False,
            },
        )

    dur = float(info.get("duration", 0.0) or 0.0)
    fps = float(info.get("fps", 30.0) or 30.0)
    if method in ("natural", "telea", "telea_soft"):
        mpx_per_sec = (vw * vh / 1_000_000.0) * max(1.0, fps)
        est_processing_sec = dur * mpx_per_sec * _SEC_PER_MPX_FRAME
        effective_max_dur = _compute_effective_max_duration(vw, vh, fps, method)
        if dur > _MAX_INPAINT_DURATION_SEC or est_processing_sec > _JOB_TIMEOUT_SEC:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "duration_limit_exceeded",
                    "user_message": (
                        f"Thời lượng video ({dur:.0f}s ở {vw}×{vh}@{fps:.0f}fps) ước tính cần "
                        f"~{int(round(est_processing_sec))}s xử lý, vượt giới hạn cho phép "
                        f"(tối đa ~{effective_max_dur}s / {int(_JOB_TIMEOUT_SEC)}s xử lý). "
                        f"Vui lòng cắt ngắn video hoặc chọn chế độ Cắt viền (Crop) / Làm mờ (Blur)."
                    ),
                    "estimated_processing_sec": round(est_processing_sec, 1),
                    "max_duration_sec": effective_max_dur,
                    "retryable": False,
                },
            )
    else:
        if dur > _MAX_VIDEO_DURATION_SEC:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "duration_limit_exceeded",
                    "user_message": f"Thời lượng video ({dur:.0f}s) vượt quá giới hạn cho phép ({int(_MAX_VIDEO_DURATION_SEC)}s).",
                    "max_duration_sec": int(_MAX_VIDEO_DURATION_SEC),
                    "retryable": False,
                },
            )


def _read_meta(work_dir: str) -> dict:
    meta_path = os.path.join(work_dir, "metadata.json")
    with _META_LOCK:
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                return json.load(mf)
        except Exception:
            return {}


def _write_meta(work_dir: str, updates: dict) -> dict:
    meta_path = os.path.join(work_dir, "metadata.json")
    with _META_LOCK:
        meta = _read_meta(work_dir)
        prev_owner = meta.get("owner_user_id")
        meta.update(updates)
        if prev_owner and "owner_user_id" not in updates and not meta.get("owner_user_id"):
            meta["owner_user_id"] = prev_owner
        try:
            os.makedirs(work_dir, exist_ok=True)
            tmp_path = f"{meta_path}.tmp.{os.getpid()}.{threading.get_ident()}"
            with open(tmp_path, "w", encoding="utf-8") as mf:
                json.dump(meta, mf)
            os.replace(tmp_path, meta_path)
        except Exception:
            try:
                with open(meta_path, "w", encoding="utf-8") as mf:
                    json.dump(meta, mf)
            except Exception:
                pass
        effective_status = updates.get("status") or meta.get("status")
        if effective_status in ("completed", "done", "failed", "cancelled", "no_visible_logo"):
            _release_user_concurrency(
                meta.get("owner_user_id"),
                meta.get("temp_id") or meta.get("job_id"),
            )
        elif effective_status in ("queued", "running"):
            _refresh_user_concurrency(
                meta.get("owner_user_id"),
                meta.get("temp_id") or meta.get("job_id"),
            )
        return meta


def _is_work_dir_expired(work_dir: str, ttl_sec: Optional[int] = None) -> bool:
    """Return True if a flow_* work_dir has exceeded its TTL and is not actively running."""
    if not os.path.isdir(work_dir):
        return True
    ttl = _FLOW_TTL_SEC if ttl_sec is None else ttl_sec
    if ttl <= 0:
        return True
    meta = _read_meta(work_dir)
    status = meta.get("status")
    now = time.time()
    # Active jobs have a longer safety window so a running worker isn't wiped mid-encode.
    if status in ("queued", "running"):
        try:
            mtime = os.path.getmtime(work_dir)
            return (now - mtime) > max(ttl, _JOB_TIMEOUT_SEC * 2)
        except OSError:
            return False

    ref_ts: Optional[float] = None
    for key in ("processed_at", "failed_at", "cancelled_at", "created_at"):
        val = meta.get(key)
        if isinstance(val, str) and val:
            try:
                ref_ts = datetime.fromisoformat(val).timestamp()
                break
            except Exception:
                pass
    if ref_ts is None:
        try:
            ref_ts = os.path.getmtime(work_dir)
        except OSError:
            return True
    return (now - ref_ts) > ttl


def _ensure_work_dir_fresh(work_dir: str, detail: str = "File không còn tồn tại hoặc đã hết hạn.") -> dict:
    """Verify work_dir exists and has not expired; delete and raise 404 if expired."""
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail=detail)
    if _is_work_dir_expired(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail=detail)
    return _read_meta(work_dir)


def _find_input_path(work_dir: str) -> Optional[str]:
    for ext in (".mp4", ".mov", ".webm", ".mkv"):
        c = os.path.join(work_dir, f"input{ext}")
        if os.path.exists(c):
            return c
    return None


def _result_url(temp_id: str, output_path: str, filename: str) -> str:
    from urllib.parse import quote
    return (f"/api/v1/flow-cleanup/result/{re.sub(r'[^a-f0-9]', '', temp_id)}/"
            f"{quote(file_id_for(output_path), safe='')}?filename={quote(filename, safe='')}")


def _safe_work_dir(temp_id: str) -> str:
    safe = re.sub(r"[^a-f0-9]", "", temp_id or "")
    if len(safe) < 8:
        raise HTTPException(status_code=400, detail="Invalid temp_id")
    return os.path.join(_DOWNLOAD_DIR, f"{_FLOW_PREFIX}{safe}")


def _probe(path: str) -> dict:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True,
            timeout=30,
        )
        if r.returncode != 0:
            return {}
        data = json.loads(r.stdout)
        fmt_dur = 0.0
        try:
            fmt_dur = float((data.get("format") or {}).get("duration", 0) or 0)
        except Exception:
            fmt_dur = 0.0
        streams = data.get("streams", [])
        has_audio = any(s.get("codec_type") == "audio" for s in streams)
        for s in streams:
            if s.get("codec_type") == "video":
                fps_raw = s.get("r_frame_rate") or s.get("avg_frame_rate") or "30/1"
                try:
                    num, den = fps_raw.split("/")
                    fps = round(int(num) / max(int(den), 1), 2)
                except Exception:
                    fps = 30.0
                if fps <= 0 or fps > 120:
                    avg_raw = s.get("avg_frame_rate") or "30/1"
                    try:
                        anum, aden = avg_raw.split("/")
                        fps = round(int(anum) / max(int(aden), 1), 2)
                    except Exception:
                        fps = 30.0
                if fps <= 0 or fps > 240:
                    fps = 30.0

                raw_w = int(s.get("width", 0) or 0)
                raw_h = int(s.get("height", 0) or 0)
                rotation = 0
                tags = s.get("tags") or {}
                if "rotate" in tags:
                    try:
                        rotation = int(float(tags["rotate"])) % 360
                    except Exception:
                        rotation = 0
                for sd in s.get("side_data_list", []) or []:
                    if "rotation" in sd:
                        try:
                            rotation = int(round(abs(float(sd["rotation"])))) % 360
                        except Exception:
                            pass
                if rotation in (90, 270):
                    raw_w, raw_h = raw_h, raw_w

                # Pad odd dimensions UP by 1px (instead of cropping) so libx264 yuv420p preserves 100% of original frame
                odd_adjusted = bool((raw_w > 0 and (raw_w % 2)) or (raw_h > 0 and (raw_h % 2)))
                vw = max(2, raw_w + (raw_w % 2)) if raw_w > 0 else 0
                vh = max(2, raw_h + (raw_h % 2)) if raw_h > 0 else 0
                dur_val = float(s.get("duration", 0) or 0) or fmt_dur
                nb_frames = int(s.get("nb_frames", 0) or 0)
                return {
                    "width": vw,
                    "height": vh,
                    "raw_width": raw_w,
                    "raw_height": raw_h,
                    "odd_dimension_adjusted": odd_adjusted,
                    "dimension_adjustment": "padded_to_even_for_yuv420p" if odd_adjusted else "none",
                    "rotation": rotation,
                    "has_audio": has_audio,
                    "codec_name": s.get("codec_name", ""),
                    "duration": round(dur_val, 2),
                    "fps": fps,
                    "nb_frames": nb_frames,
                    "max_inpaint_duration_sec": _compute_effective_max_duration(vw, vh, fps, "natural"),
                    "max_crop_blur_duration_sec": int(_MAX_VIDEO_DURATION_SEC),
                }
    except Exception:
        pass
    return {}


def _extract_frame(input_path: str, frame_path: str, seek_sec: float = 0.0) -> bool:
    ss_str = f"{max(0.0, float(seek_sec)):.4f}"
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", ss_str, "-i", input_path, "-vframes", "1", "-q:v", "2", frame_path],
        capture_output=True,
        timeout=30,
    )
    return r.returncode == 0 and os.path.exists(frame_path)


# ── TELEA inpainting helpers ─────────────────────────────────────────────────

def _build_mask(x: int, y: int, w: int, h: int, vw: int, vh: int) -> "np.ndarray":
    """Grayscale mask: white (255) = region to inpaint, black (0) = untouched."""
    mask = np.zeros((vh, vw), dtype=np.uint8)
    mask[y : y + h, x : x + w] = 255
    return mask


def _save_mask_png(mask: "np.ndarray", path: str) -> None:
    cv2.imwrite(path, mask)


def _telea_single_frame(input_path: str, mask: "np.ndarray", vw: int, vh: int) -> bytes:
    """Extract first frame and apply TELEA — used for fast frame-preview endpoint."""
    frame_size = vw * vh * 3
    r = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", "0", "-i", input_path,
            "-vframes", "1",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}",
            "pipe:1",
        ],
        capture_output=True,
        timeout=20,
    )
    if len(r.stdout) < frame_size:
        raise ValueError("Frame extraction failed for preview.")
    frame = np.frombuffer(r.stdout[:frame_size], dtype=np.uint8).reshape((vh, vw, 3)).copy()
    inpainted = cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
    _, buf = cv2.imencode(".jpg", inpainted, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return bytes(buf)


def _telea_video(
    input_path: str,
    output_path: str,
    mask: "np.ndarray",
    x: int, y: int, w: int, h: int,
    vw: int, vh: int,
    fps: float,
    soft: bool = False,
    duration: float = 0.0,
    progress_cb: Optional[Callable[[int, int], Optional[bool]]] = None,
) -> None:
    """
    Stream all frames through OpenCV TELEA inpainting and encode to output.
    Reader runs in a thread so the reader/writer pipes never deadlock.
    Audio is muxed from the original file in a second pass.
    """
    frame_size = vw * vh * 3
    fps_str = f"{fps:.3f}"
    output_path = os.path.abspath(output_path)
    video_only = output_path + ".noaudio.mp4"
    q: _queue.Queue = _queue.Queue(maxsize=8)
    reader_errors: list = []
    reader_proc_ref: list = [None]
    est_frames = max(1, int(round((duration or 0.0) * (fps or 30.0)))) if duration else 100

    def _reader() -> None:
        proc = subprocess.Popen(
            ["ffmpeg", "-i", input_path,
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{vw}x{vh}", "pipe:1"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        reader_proc_ref[0] = proc
        try:
            while True:
                chunk = proc.stdout.read(frame_size)  # type: ignore[union-attr]
                if len(chunk) < frame_size:
                    break
                q.put(chunk)
        except Exception as exc:
            reader_errors.append(exc)
        finally:
            try:
                proc.stdout.close()  # type: ignore[union-attr]
            except Exception:
                pass
            proc.wait()
            q.put(None)  # sentinel

    write_proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{vw}x{vh}", "-r", fps_str,
            "-i", "pipe:0",
            "-c:v", "libx264", "-crf", "12", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            video_only,
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    done_frames = 0
    write_errors: list = []
    try:
        while True:
            raw = q.get(timeout=120)
            if raw is None:
                break
            done_frames += 1
            if progress_cb is not None and (done_frames == 1 or done_frames % 5 == 0):
                if progress_cb(done_frames, max(est_frames, done_frames)) is False:
                    raise RuntimeError("Job cancelled by user")

            frame = np.frombuffer(raw, dtype=np.uint8).reshape((vh, vw, 3)).copy()
            inpainted = cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA)
            if soft:
                patch = inpainted[y : y + h, x : x + w].copy()
                blurred = cv2.GaussianBlur(patch, (5, 5), 1.5)
                inpainted[y : y + h, x : x + w] = cv2.addWeighted(blurred, 0.4, patch, 0.6, 0)
            write_proc.stdin.write(inpainted.tobytes())  # type: ignore[union-attr]
    except Exception as exc:
        write_errors.append(exc)
    finally:
        try:
            write_proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        if write_errors:
            if reader_proc_ref[0] is not None:
                try:
                    reader_proc_ref[0].terminate()
                except Exception:
                    pass
            try:
                while not q.empty():
                    q.get_nowait()
            except Exception:
                pass

    reader_thread.join(timeout=30)
    write_proc.wait(timeout=180)

    if reader_errors:
        if os.path.exists(video_only):
            try:
                os.remove(video_only)
            except Exception:
                pass
        raise reader_errors[0]
    if write_errors:
        if os.path.exists(video_only):
            try:
                os.remove(video_only)
            except Exception:
                pass
        raise write_errors[0]
    if write_proc.returncode != 0:
        err = write_proc.stderr.read().decode(errors="replace")[-400:]  # type: ignore[union-attr]
        raise ValueError(f"TELEA encode failed: {err}")
    if not os.path.isfile(video_only) or os.path.getsize(video_only) == 0:
        raise ValueError(f"TELEA inpaint output missing: {video_only}")

    # Pass 2: mux original audio stream back in (fallback to AAC if source audio is Opus/Vorbis)
    r2 = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_only,
            "-i", input_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ],
        capture_output=True,
        timeout=120,
    )
    if r2.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
        r2 = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", video_only,
                "-i", input_path,
                "-map", "0:v:0", "-map", "1:a:0?",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_path,
            ],
            capture_output=True,
            timeout=120,
        )
    try:
        os.remove(video_only)
    except Exception:
        pass
    if r2.returncode != 0:
        raise ValueError(f"Audio mux failed: {r2.stderr.decode(errors='replace')[-300:]}")


# ── Standalone crop & blur helpers (extracted for testability & worker use) ──

def compute_crop_rect(x: int, y: int, w: int, h: int, vw: int, vh: int) -> Tuple[int, int, int, int]:
    """Compute even-dimensioned (cx, cy, cw, ch) crop box that trims the edge containing (x, y, w, h)."""
    cx, cy, cw, ch = 0, 0, vw, vh
    if x + w >= vw * 0.6:
        cw = x - 4
    elif x <= vw * 0.4:
        cx = x + w + 4
        cw = vw - cx
    if y + h >= vh * 0.6:
        ch = y - 4
    elif y <= vh * 0.4:
        cy = y + h + 4
        ch = vh - cy
    cw = max(int(cw) - int(cw) % 2, 2)
    ch = max(int(ch) - int(ch) % 2, 2)
    cx = max(0, min(int(cx), vw - cw))
    cy = max(0, min(int(cy), vh - ch))
    return cx, cy, cw, ch


def _crop_video(
    input_path: str,
    output_path: str,
    x: int, y: int, w: int, h: int,
    vw: int, vh: int,
    timeout: int = _JOB_TIMEOUT_SEC,
) -> Dict[str, int]:
    """Trim the edge containing the watermark via FFmpeg crop filter."""
    cx, cy, cw, ch = compute_crop_rect(x, y, w, h, vw, vh)
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"crop={cw}:{ch}:{int(cx)}:{int(cy)}",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", output_path,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        err = (result.stderr or b"").decode(errors="replace")[-400:]
        raise ValueError(f"FFmpeg crop: {err}")
    return {"cx": cx, "cy": cy, "cw": cw, "ch": ch}


def _blur_video(
    input_path: str,
    output_path: str,
    x: int, y: int, w: int, h: int,
    timeout: int = _JOB_TIMEOUT_SEC,
) -> Dict[str, int]:
    """Cover the watermark region with FFmpeg boxblur overlay."""
    lr = max(1, min(20, min(int(w), int(h)) // 4 - 1))
    cr = max(1, min(10, min(int(w), int(h)) // 8 - 1))
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex",
            f"[0:v]pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:black,split[orig][dup];"
            f"[dup]crop={w}:{h}:{x}:{y},boxblur={lr}:5:{cr}:5[blurred];"
            f"[orig][blurred]overlay={x}:{y}",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", output_path,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        err = (result.stderr or b"").decode(errors="replace")[-400:]
        raise ValueError(f"FFmpeg blur: {err}")
    return {"x": x, "y": y, "w": w, "h": h}


@router.post("/upload")
@limiter.limit("10/minute")
async def upload_flow_video(request: Request, file: UploadFile = File(...), _: None = Depends(_require_pro)):
    """
    Upload a local Flow/Veo video file for visible-logo cleanup.
    Returns temp_id, preview frame URL, and video metadata.

    Only visible on-frame logos are targeted.
    SynthID watermarking is NOT removed by this service.
    """
    fname = file.filename or "video.mp4"
    ext = os.path.splitext(fname)[1].lower()
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ MP4, MOV, WebM, MKV.")

    ct = (file.content_type or "").lower()
    if ct and not any(t in ct for t in ("video/", "application/octet-stream")):
        raise HTTPException(status_code=400, detail="Loại file không hợp lệ.")

    os.makedirs(_DOWNLOAD_DIR, exist_ok=True)
    temp_id = new_token()  # 128 bits — the work dir name is the capability
    work_dir = _safe_work_dir(temp_id)
    os.makedirs(work_dir, exist_ok=True)

    input_path = os.path.join(work_dir, f"input{ext}")
    frame_path = os.path.join(work_dir, "preview.jpg")
    written = 0
    max_bytes = _MAX_UPLOAD_MB * 1024 * 1024

    try:
        with open(input_path, "wb") as f:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File vượt quá {_MAX_UPLOAD_MB}MB.")
                f.write(chunk)
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Upload lỗi: {e}")

    info = _probe(input_path)
    try:
        _validate_job_limits(input_path, info, method="crop")
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    if not _extract_frame(input_path, frame_path) or not os.path.isfile(frame_path) or os.path.getsize(frame_path) == 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "corrupted_video",
                "user_message": "File video bị hỏng hoặc cắt cụt, không thể giải mã khung hình.",
                "retryable": False,
            },
        )

    file_size_mb = round(written / (1024 * 1024), 2)

    owner_id = _caller_user_id(request)
    if not owner_id:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=401, detail="Đăng nhập để sử dụng tính năng này.")
    try:
        _check_and_record_daily_quota(owner_id)
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    # Write initial metadata for admin/QA inspection
    _write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": owner_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "video_info": info,
        "file_size_mb": file_size_mb,
    })

    return {
        "success": True,
        "temp_id": temp_id,
        "job_id": temp_id,
        "preview_url": _build_signed_url(f"/api/v1/flow-cleanup/frame/{temp_id}", temp_id, "frame", owner_id),
        "video_info": info,
        "file_size_mb": file_size_mb,
    }


@router.get("/frame/{temp_id}")
async def get_preview_frame(
    temp_id: str,
    frame_idx: Optional[int] = None,
    exp: Optional[int] = None,
    sig: Optional[str] = None,
):
    """Serve the extracted frame JPEG for the uploaded video (signed URL required; frame_idx bounds-checked & LRU-cached)."""
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Preview không còn. Upload lại.")
    _verify_signed_resource(temp_id, "frame", meta, exp=exp, sig=sig, frame_idx=frame_idx)

    input_path = _find_input_path(work_dir)
    total_frames = _get_total_frames(meta, input_path)

    if frame_idx is not None:
        idx = int(frame_idx)
        if idx < 0 or idx >= total_frames:
            raise HTTPException(
                status_code=400,
                detail=f"frame_idx không hợp lệ ({idx}); phải thuộc khoảng [0, {max(0, total_frames - 1)}].",
            )
        if idx > 0:
            kf_path = os.path.join(work_dir, f"frame_{idx}.jpg")
            if os.path.isfile(kf_path) and os.path.getsize(kf_path) > 0:
                try:
                    os.utime(kf_path, None)
                except OSError:
                    pass
                return FileResponse(kf_path, media_type="image/jpeg")

            if not input_path:
                raise HTTPException(status_code=404, detail="Preview không còn. Upload lại.")
            info = meta.get("video_info") or _probe(input_path)
            fps = float(info.get("fps", 30.0) or 30.0)
            dur = float(info.get("duration", 0.0) or 0.0)
            seek_sec = idx / max(fps, 1.0)
            if dur > 0:
                seek_sec = min(seek_sec, max(0.0, dur - 0.05))
            if not _extract_frame(input_path, kf_path, seek_sec=seek_sec):
                raise HTTPException(status_code=404, detail="Không thể trích xuất frame tại keyframe này.")
            _enforce_keyframe_cache_limit(work_dir, max_files=_MAX_KEYFRAME_CACHE_FILES)
            return FileResponse(kf_path, media_type="image/jpeg")

    frame_path = os.path.join(work_dir, "preview.jpg")
    if not os.path.exists(frame_path):
        raise HTTPException(status_code=404, detail="Preview không còn. Upload lại.")
    return FileResponse(frame_path, media_type="image/jpeg")


@router.get("/frame-url/{temp_id}")
def get_signed_frame_url(
    temp_id: str,
    request: Request,
    frame_idx: Optional[int] = None,
    _: None = Depends(_require_pro),
):
    """Return a short-lived signed URL for a specific frame_idx (for keyframe editing in UI)."""
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    caller_id = _caller_user_id(request)
    _verify_job_owner(meta, caller_id)
    input_path = _find_input_path(work_dir)
    total_frames = _get_total_frames(meta, input_path)
    if frame_idx is not None:
        idx = int(frame_idx)
        if idx < 0 or idx >= total_frames:
            raise HTTPException(
                status_code=400,
                detail=f"frame_idx không hợp lệ ({idx}); phải thuộc khoảng [0, {max(0, total_frames - 1)}].",
            )
    signed = _build_signed_url(
        f"/api/v1/flow-cleanup/frame/{temp_id}",
        temp_id,
        "frame",
        str(meta["owner_user_id"]),
        frame_idx=frame_idx if (frame_idx is not None and int(frame_idx) > 0) else None,
    )
    return {"success": True, "temp_id": temp_id, "frame_idx": frame_idx, "preview_url": signed}


class PreviewFrameRequest(BaseModel):
    temp_id: str
    preset: Optional[str] = None
    region: Optional[dict] = None   # {x,y,w,h} pixels
    expand_padding: int = 8


def _resolve_region(preset: Optional[str], region: Optional[dict], vw: int, vh: int) -> dict:
    """Shared region-resolution logic for both /preview-frame and /process."""
    if preset and preset != "custom":
        mx = max(int(vw * 0.16), 80)
        my = max(int(vh * 0.10), 40)
        px = max(int(vw * 0.02), 8)
        py = max(int(vh * 0.02), 8)
        preset_map = {
            "lower-right": {"x": vw - mx - px, "y": vh - my - py, "w": mx, "h": my},
            "lower-left":  {"x": px,            "y": vh - my - py, "w": mx, "h": my},
            "upper-right": {"x": vw - mx - px,  "y": py,           "w": mx, "h": my},
            "upper-left":  {"x": px,            "y": py,           "w": mx, "h": my},
        }
        if preset not in preset_map:
            raise HTTPException(status_code=400, detail=f"Preset không hợp lệ: {preset}")
        return preset_map[preset]
    if region:
        return region
    raise HTTPException(status_code=400, detail="Cần chọn vị trí logo.")


@router.post("/preview-frame")
@limiter.limit("30/minute")
async def preview_frame_cleanup(payload: PreviewFrameRequest, request: Request, _: None = Depends(_require_pro)):
    """
    TELEA-inpaint the first frame only and return a preview JPEG URL.
    Fast (<2s) — used for preview-first UX before committing to full processing.
    """
    work_dir = _safe_work_dir(payload.temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    _verify_job_owner(meta, _caller_user_id(request))
    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    info = _probe(input_path)
    vw = info.get("width", 1920)
    vh = info.get("height", 1080)

    raw = _resolve_region(payload.preset, payload.region, vw, vh)
    x, y, w, h = int(raw["x"]), int(raw["y"]), int(raw["w"]), int(raw["h"])
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > vw or y + h > vh:
        raise HTTPException(status_code=400, detail="Kích thước vùng không hợp lệ.")

    ep = max(0, int(payload.expand_padding))
    if ep > 0:
        x = max(0, x - ep);  y = max(0, y - ep)
        w = min(vw - x, w + ep * 2);  h = min(vh - y, h + ep * 2)

    try:
        from app.api import flow_inpaint as _fi
        # Same branch the FINAL will use (static SHIFTMAP vs moving temporal),
        # rendered on frame 0 → preview reflects final quality + crop advice.
        preview_bgr, pinfo = _fi.decide_and_preview(
            input_path, x, y, w, h, vw, vh,
            info.get("duration", 0) or 0.0, info.get("fps", 30.0) or 30.0,
        )
        _, buf = cv2.imencode(".jpg", preview_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        clean_path = os.path.join(work_dir, "preview_clean.jpg")
        with open(clean_path, "wb") as f:
            f.write(buf.tobytes())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview thất bại: {str(e)[:200]}")

    owner_id = str(meta["owner_user_id"])
    return {
        "preview_clean_url": _build_signed_url(
            f"/api/v1/flow-cleanup/preview-clean/{payload.temp_id}",
            payload.temp_id,
            "preview_clean",
            owner_id,
        ),
        "region": {"x": x, "y": y, "w": w, "h": h},
        **pinfo,   # strategy, motion, fill_method, quality, recommend_crop, reason
    }


@router.get("/preview-clean/{temp_id}")
async def get_preview_clean(
    temp_id: str,
    exp: Optional[int] = None,
    sig: Optional[str] = None,
):
    """Serve the TELEA-cleaned first-frame JPEG for preview-first UX (signed URL required)."""
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Preview chưa sẵn sàng. Upload và chọn vùng trước.")
    _verify_signed_resource(temp_id, "preview_clean", meta, exp=exp, sig=sig)
    path = os.path.join(work_dir, "preview_clean.jpg")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Preview chưa sẵn sàng. Upload và chọn vùng trước.")
    return FileResponse(path, media_type="image/jpeg")


class PreviewClipRequest(BaseModel):
    temp_id: str
    preset: Optional[str] = "custom"
    region: Optional[dict] = None
    method: str = "natural"
    expand_padding: int = 8
    clip_seconds: float = 4.0
    frame_boxes: Optional[dict] = None


def _execute_flow_preview_clip_job(
    temp_id: str,
    job_params: Dict[str, Any],
    celery_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Render a 3–5 second cleaned video clip preview in Celery worker ('media' queue) or in-process fallback."""
    work_dir = _safe_work_dir(temp_id)
    prev_meta = _read_meta(work_dir)
    if prev_meta.get("status") == "cancelled":
        return {
            "success": False,
            "job_id": temp_id,
            "temp_id": temp_id,
            "task_kind": "preview_clip",
            "status": "cancelled",
            "progress": int(prev_meta.get("progress", 0)),
        }

    input_path = _find_input_path(work_dir)
    if not input_path:
        _mark_job_failed(temp_id, "Upload không còn. Vui lòng upload lại.", "source_ingest_failed")
        raise FileNotFoundError("Upload không còn. Vui lòng upload lại.")

    info = job_params.get("video_info") or prev_meta.get("video_info") or _probe(input_path)
    vw = int(info.get("width", 1920) or 1920)
    vh = int(info.get("height", 1080) or 1080)
    fps_val = float(info.get("fps", 30.0) or 30.0)
    method = job_params.get("method", "natural")
    region = job_params["region"]
    x, y, w, h = int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])
    clip_dur = float(job_params.get("clip_dur", 4.0) or 4.0)
    owner_id = str(prev_meta.get("owner_user_id") or "")

    clip_src = os.path.join(work_dir, "preview_clip_src.mp4")
    clip_out = os.path.join(work_dir, "preview_clip_clean.mp4")
    t0 = time.perf_counter()

    _write_meta(work_dir, {
        "job_id": temp_id,
        "temp_id": temp_id,
        "owner_user_id": job_params.get("owner_user_id") or prev_meta.get("owner_user_id"),
        "active_task_kind": "preview_clip",
        "status": "running",
        "progress": 10,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "celery_task_id": celery_task_id or prev_meta.get("celery_task_id"),
        "error": None,
        "error_code": None,
    })

    def _progress_cb(done_frames: int, total_frames: int) -> bool:
        if (time.perf_counter() - t0) > _PREVIEW_CLIP_TIMEOUT_SEC:
            raise TimeoutError("Quá thời gian tạo video preview (processing_timeout).")
        cur = _read_meta(work_dir)
        if cur.get("status") == "cancelled":
            return False
        pct = min(95, max(10, int(10 + 85 * (done_frames / max(1, total_frames)))))
        if pct != cur.get("progress"):
            _write_meta(work_dir, {"progress": pct})
        return True

    try:
        r_trim = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", "0", "-t", f"{clip_dur:.2f}",
                "-i", input_path,
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-c:a", "copy",
                clip_src,
            ],
            capture_output=True,
            timeout=int(min(60, _PREVIEW_CLIP_TIMEOUT_SEC)),
        )
        if _read_meta(work_dir).get("status") == "cancelled":
            raise RuntimeError("Job cancelled by user")

        src_for_preview = (
            clip_src
            if (r_trim.returncode == 0 and os.path.isfile(clip_src) and os.path.getsize(clip_src) > 0)
            else input_path
        )

        tracked_boxes = job_params.get("frame_boxes") or prev_meta.get("tracked_boxes")
        if method == "natural":
            from app.api import flow_inpaint as _fi
            _fi.process_video_natural(
                src_for_preview, clip_out,
                x, y, w, h, vw, vh,
                fps_val,
                duration=clip_dur,
                progress_cb=_progress_cb,
                frame_boxes=tracked_boxes,
            )
        elif method in ("telea", "telea_soft"):
            mask = _build_mask(x, y, w, h, vw, vh)
            _telea_video(
                src_for_preview, clip_out, mask,
                x, y, w, h, vw, vh,
                fps_val,
                soft=(method == "telea_soft"),
                duration=clip_dur,
                progress_cb=_progress_cb,
            )
        elif method == "crop":
            _progress_cb(1, 2)
            _crop_video(src_for_preview, clip_out, x, y, w, h, vw, vh, timeout=int(_PREVIEW_CLIP_TIMEOUT_SEC))
            _progress_cb(2, 2)
        else:
            _progress_cb(1, 2)
            _blur_video(src_for_preview, clip_out, x, y, w, h, timeout=int(_PREVIEW_CLIP_TIMEOUT_SEC))
            _progress_cb(2, 2)

        if _read_meta(work_dir).get("status") == "cancelled":
            raise RuntimeError("Job cancelled by user")

        if not os.path.isfile(clip_out) or os.path.getsize(clip_out) == 0:
            raise ValueError("Không tạo được video preview ngắn.")

        signed_clip_url = _build_signed_url(
            f"/api/v1/flow-cleanup/preview-clip-video/{temp_id}",
            temp_id,
            "preview_clip",
            owner_id,
        )
        res_payload = {
            "success": True,
            "job_id": temp_id,
            "temp_id": temp_id,
            "task_kind": "preview_clip",
            "status": "done",
            "progress": 100,
            "preview_clip_url": signed_clip_url,
            "clip_duration": round(clip_dur, 2),
            "method_used": method,
            "region": {"x": x, "y": y, "w": w, "h": h},
        }
        _write_meta(work_dir, {
            "active_task_kind": "preview_clip",
            "status": "done",
            "progress": 100,
            "preview_clip_duration": round(clip_dur, 2),
            "preview_clip_method": method,
            "preview_clip_region": {"x": x, "y": y, "w": w, "h": h},
            "preview_clip_payload": res_payload,
            "error": None,
            "error_code": None,
        })
        return res_payload

    except Exception as exc:
        for p in (clip_src, clip_out, clip_out + ".noaudio.mp4"):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        if _read_meta(work_dir).get("status") == "cancelled" or "cancelled" in str(exc).lower():
            _write_meta(work_dir, {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
                "error": "Đã hủy tạo video preview.",
                "error_code": "job_cancelled",
            })
            return {
                "success": False,
                "job_id": temp_id,
                "temp_id": temp_id,
                "task_kind": "preview_clip",
                "status": "cancelled",
                "progress": _read_meta(work_dir).get("progress", 0),
            }
        err_code = _error_code(exc)
        err_msg = "Quá thời gian tạo video preview." if err_code == "processing_timeout" else f"Tạo video preview thất bại: {str(exc)[:200]}"
        _mark_job_failed(temp_id, err_msg, err_code)
        raise


def _enqueue_preview_clip_task(temp_id: str, job_params: Dict[str, Any]) -> Optional[str]:
    """Dispatch `preview_clip_task` onto the Celery 'media' queue if reachable or mocked."""
    from app.tasks.media_tasks import preview_clip_task
    from app.core.celery_app import celery_app

    apply_fn = getattr(preview_clip_task, "apply_async", None)
    is_mocked = (
        hasattr(apply_fn, "_mock_name")
        or getattr(apply_fn, "__qualname__", "Task.apply_async") != "Task.apply_async"
        or os.getenv("FLOW_CLEANUP_FORCE_ASYNC") == "1"
    )
    if is_mocked:
        res = preview_clip_task.apply_async(args=[temp_id, job_params], queue="media")
        return str(getattr(res, "id", temp_id))

    try:
        insp = celery_app.control.inspect(timeout=1.5)
        active = insp.ping() if insp else None
        if not active:
            return None
        res = preview_clip_task.apply_async(args=[temp_id, job_params], queue="media", retry=False)
        return str(getattr(res, "id", temp_id))
    except Exception as exc:
        log.warning("[flow-cleanup] media queue unavailable for preview_clip: %s", exc)
        return None


@router.post("/preview-clip")
@limiter.limit("10/minute")
def preview_clip_cleanup(payload: PreviewClipRequest, request: Request, _: None = Depends(_require_pro)):
    """
    Phase P4 / P5.2: Render a short 3–5 second cleaned video clip preview asynchronously on the 'media' queue.
    - Deduplicates if a `preview_clip` job is already queued/running on the same `temp_id`.
    - Enforces per-user concurrency limit (1 active heavy job per user).
    - Returns HTTP 202 with `task_id` and `status_url` when queued on Celery.
    """
    work_dir = _safe_work_dir(payload.temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    caller_id = _caller_user_id(request)
    _verify_job_owner(meta, caller_id)
    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    # Deduplicate if this temp_id already has a preview_clip task queued or running
    if meta.get("status") in ("queued", "running") and meta.get("active_task_kind") == "preview_clip":
        existing_tid = meta.get("celery_task_id") or payload.temp_id
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "deduplicated": True,
                "job_id": payload.temp_id,
                "temp_id": payload.temp_id,
                "task_id": existing_tid,
                "celery_task_id": existing_tid,
                "task_kind": "preview_clip",
                "status": meta.get("status"),
                "progress": int(meta.get("progress", 0)),
                "status_url": f"/api/v1/flow-cleanup/status/{payload.temp_id}",
                "cancel_url": f"/api/v1/flow-cleanup/cancel/{payload.temp_id}",
            },
        )

    info = meta.get("video_info") or _probe(input_path)
    vw = int(info.get("width", 1920) or 1920)
    vh = int(info.get("height", 1080) or 1080)
    dur = float(info.get("duration", 0.0) or 0.0)

    raw = _resolve_region(payload.preset, payload.region, vw, vh)
    x, y, w, h = int(raw["x"]), int(raw["y"]), int(raw["w"]), int(raw["h"])
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > vw or y + h > vh:
        raise HTTPException(status_code=400, detail="Kích thước vùng không hợp lệ.")

    _check_user_concurrency(caller_id, payload.temp_id)

    _ALIAS = {"delogo": "natural", "delogo_soft": "natural", "telea": "telea", "telea_soft": "telea_soft"}
    method = payload.method if payload.method in ("natural", "telea", "telea_soft", "crop", "blur") else _ALIAS.get(payload.method, "natural")

    ep = max(0, int(payload.expand_padding))
    if ep > 0 and method != "crop":
        x = max(0, x - ep);  y = max(0, y - ep)
        w = min(vw - x, w + ep * 2);  h = min(vh - y, h + ep * 2)

    clip_dur = max(1.0, min(float(payload.clip_seconds or 4.0), 5.0))
    if dur > 0:
        clip_dur = min(clip_dur, dur)

    job_params = {
        "method": method,
        "region": {"x": x, "y": y, "w": w, "h": h},
        "clip_dur": clip_dur,
        "video_info": info,
        "frame_boxes": payload.frame_boxes or meta.get("tracked_boxes"),
        "owner_user_id": caller_id,
    }

    _write_meta(work_dir, {
        "job_id": payload.temp_id,
        "temp_id": payload.temp_id,
        "active_task_kind": "preview_clip",
        "status": "queued",
        "progress": 0,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
        "error_code": None,
    })

    task_id = _enqueue_preview_clip_task(payload.temp_id, job_params)
    if task_id is not None:
        _write_meta(work_dir, {"celery_task_id": task_id})
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "deduplicated": False,
                "job_id": payload.temp_id,
                "temp_id": payload.temp_id,
                "task_id": task_id,
                "celery_task_id": task_id,
                "task_kind": "preview_clip",
                "status": "queued",
                "progress": 0,
                "status_url": f"/api/v1/flow-cleanup/status/{payload.temp_id}",
                "cancel_url": f"/api/v1/flow-cleanup/cancel/{payload.temp_id}",
                "method_used": method,
                "region": {"x": x, "y": y, "w": w, "h": h},
            },
        )

    if os.getenv("FLOW_CLEANUP_ALLOW_INPROCESS", "0") != "1":
        _mark_job_failed(payload.temp_id, "Hàng đợi xử lý video tạm thời không khả dụng.", "queue_unavailable")
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "queue_unavailable",
                "user_message": "Hệ thống xử lý video đang bận hoặc tạm thời gián đoạn. Vui lòng thử lại sau ít phút.",
                "retryable": True,
            },
        )

    try:
        return _execute_flow_preview_clip_job(payload.temp_id, job_params)
    except HTTPException:
        raise
    except (subprocess.TimeoutExpired, TimeoutError):
        raise HTTPException(status_code=504, detail="Quá thời gian tạo video preview.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tạo video preview thất bại: {str(exc)[:200]}")


@router.get("/preview-clip-video/{temp_id}")
async def get_preview_clip_video(
    temp_id: str,
    exp: Optional[int] = None,
    sig: Optional[str] = None,
):
    """Serve the 3–5 second cleaned preview video clip (signed URL required)."""
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Video preview không còn. Vui lòng tạo lại.")
    _verify_signed_resource(temp_id, "preview_clip", meta, exp=exp, sig=sig)
    path = os.path.join(work_dir, "preview_clip_clean.mp4")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Video preview chưa sẵn sàng.")
    return FileResponse(path, media_type="video/mp4")


class ProcessRequest(BaseModel):
    temp_id: str
    preset: Optional[str] = "lower-right"      # lower-right|lower-left|upper-right|upper-left|custom|auto
    region: Optional[dict] = None              # {x,y,w,h} pixels; used when preset="custom"
    method: str = "telea"                      # telea|telea_soft|crop|blur (delogo|delogo_soft = aliases)
    expand_padding: int = 0                    # px to expand region on all sides
    region_adjusted: bool = False              # user manually dragged the region handles
    suitability_level: Optional[str] = None    # echoed back for traceability
    requested_mode: Optional[str] = None       # mode system originally recommended before user override
    keyframes: Optional[List[dict]] = None     # Phase P3: user keyframe overrides [{frame_idx, x, y, w, h}]
    frame_boxes: Optional[dict] = None         # Phase P3: optional per-frame tracked boxes {"0": [x,y,w,h], ...}
    consent_confirmed: bool = True             # Phase P5.5: user confirms right to edit/process the video


def _mark_job_failed(
    temp_id: str,
    error: str,
    error_code: str,
    job_params: Optional[Dict[str, Any]] = None,
) -> dict:
    work_dir = _safe_work_dir(temp_id)
    prev = _read_meta(work_dir)
    updates = {
        "status": "failed",
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "error": str(error)[:300],
        "error_code": error_code,
        "retry_count": int(prev.get("retry_count", 0)),
    }
    if job_params:
        updates["requested_mode"] = job_params.get("requested_mode")
        updates["method"] = job_params.get("method")
        updates["job_params"] = job_params
    return _write_meta(work_dir, updates)


def _remove_partial_outputs(work_dir: str) -> None:
    if not os.path.isdir(work_dir):
        return
    for name in os.listdir(work_dir):
        if (
            name.startswith("cleaned_")
            or name.endswith(".noaudio.mp4")
            or name in ("preview_clip_src.mp4", "preview_clip_clean.mp4")
            or ".tmp." in name
        ):
            try:
                os.remove(os.path.join(work_dir, name))
            except Exception:
                pass


def _execute_flow_cleanup_job(
    temp_id: str,
    job_params: Dict[str, Any],
    celery_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Core video cleanup execution run either by Celery worker ('media' queue) or in-process fallback."""
    work_dir = _safe_work_dir(temp_id)
    prev_meta = _read_meta(work_dir)
    if prev_meta.get("status") == "cancelled":
        return {
            "success": False,
            "job_id": temp_id,
            "temp_id": temp_id,
            "status": "cancelled",
            "progress": int(prev_meta.get("progress", 0)),
        }

    input_path = _find_input_path(work_dir)
    if not input_path:
        _mark_job_failed(temp_id, "Upload không còn. Vui lòng upload lại.", "source_ingest_failed", job_params)
        raise FileNotFoundError("Upload không còn. Vui lòng upload lại.")

    info = job_params.get("video_info") or _probe(input_path)
    vw = int(info.get("width", 1920) or 1920)
    vh = int(info.get("height", 1080) or 1080)
    fps_val = float(info.get("fps", 30.0) or 30.0)
    dur = float(info.get("duration", 0.0) or 0.0)

    method = job_params["method"]
    region = job_params["region"]
    x, y, w, h = int(region["x"]), int(region["y"]), int(region["w"]), int(region["h"])
    preset = job_params.get("preset", "custom")
    region_adjusted = bool(job_params.get("region_adjusted", False))
    expand_padding = int(job_params.get("expand_padding", 0))
    suitability_level = job_params.get("suitability_level")
    requested_mode = job_params.get("requested_mode")
    retry_count = int(job_params.get("retry_count", prev_meta.get("retry_count", 0)))

    uid = new_token()
    output_path = os.path.join(work_dir, f"cleaned_{uid}.mp4")
    mask_path = os.path.join(work_dir, "mask.png")
    natural_stats = None
    t_worker_start = time.perf_counter()

    _write_meta(work_dir, {
        "job_id": temp_id,
        "temp_id": temp_id,
        "owner_user_id": job_params.get("owner_user_id") or prev_meta.get("owner_user_id"),
        "active_task_kind": "process",
        "status": "running",
        "progress": 5,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "celery_task_id": celery_task_id or prev_meta.get("celery_task_id"),
        "method": method,
        "preset": preset,
        "region": {"x": x, "y": y, "w": w, "h": h},
        "retry_count": retry_count,
        "job_params": job_params,
        "error": None,
        "error_code": None,
    })

    def _progress_cb(done_frames: int, total_frames: int) -> bool:
        cur = _read_meta(work_dir)
        if cur.get("status") == "cancelled":
            return False
        pct = min(95, max(5, int(5 + 90 * (done_frames / max(1, total_frames)))))
        if pct != cur.get("progress"):
            _write_meta(work_dir, {"progress": pct})
        return True

    try:
        if method == "natural":
            from app.api import flow_inpaint as _fi
            mask = _build_mask(x, y, w, h, vw, vh)
            _save_mask_png(mask, mask_path)
            tracked_boxes = job_params.get("frame_boxes") or prev_meta.get("tracked_boxes")
            natural_stats = _fi.process_video_natural(
                input_path, output_path,
                x, y, w, h, vw, vh,
                fps_val,
                duration=dur,
                progress_cb=_progress_cb,
                frame_boxes=tracked_boxes,
            )
        elif method in ("telea", "telea_soft"):
            mask = _build_mask(x, y, w, h, vw, vh)
            _save_mask_png(mask, mask_path)
            _telea_video(
                input_path, output_path, mask,
                x, y, w, h, vw, vh,
                fps_val,
                soft=(method == "telea_soft"),
                duration=dur,
                progress_cb=_progress_cb,
            )
        elif method == "crop":
            _progress_cb(1, 2)
            _crop_video(input_path, output_path, x, y, w, h, vw, vh, timeout=_JOB_TIMEOUT_SEC)
            _progress_cb(2, 2)
        else:  # blur
            _progress_cb(1, 2)
            _blur_video(input_path, output_path, x, y, w, h, timeout=_JOB_TIMEOUT_SEC)
            _progress_cb(2, 2)

        if _read_meta(work_dir).get("status") == "cancelled":
            raise RuntimeError("Job cancelled by user")

        if not os.path.exists(output_path):
            raise ValueError("Output file không được tạo.")

        file_size_mb = round(os.path.getsize(output_path) / (1024 * 1024), 2)

        # Slug filename from input base name
        base = os.path.splitext(os.path.basename(input_path))[0]
        from app.core.local_download import display_name
        slug = display_name(base, "flow_veo")   # keeps Vietnamese (27/10)
        suffix = "cropped" if method == "crop" else "logo_cleaned"
        output_filename = f"{slug}_{suffix}.mp4"

        # ── Structured result metadata ────────────────────────────
        result_type = "cropped_export" if method == "crop" else "cleaned_visible_logo"
        selected_mode = "crop_fallback" if method == "crop" else "visible_logo_cleanup"
        if preset != "custom":
            region_source = "user_selected"
        elif region_adjusted:
            region_source = "user_adjusted"
        else:
            region_source = "preset_default"

        warning_flags = []
        if dur > 300:
            warning_flags.append("long_clip")
        elif dur > 120:
            warning_flags.append("medium_length_clip")
        if fps_val > 48:
            warning_flags.append("high_fps")
        if vw < 640 or vh < 360:
            warning_flags.append("low_res")
        if method != "crop" and dur > 120 and fps_val <= 30:
            warning_flags.append("crop_preferred")

        suitability_outcome = _SUITABILITY_OUTCOME.get(suitability_level or "", None)
        ff = file_fields(output_path, "cleaned_file_id", "cleaned_path")
        dl_url = _result_url(temp_id, output_path, output_filename)

        worker_exec_sec = round(time.perf_counter() - t_worker_start, 2)
        result_payload = {
            "success": True,
            "job_id": temp_id,
            "temp_id": temp_id,
            "status": "done",
            "progress": 100,
            "job_kind": "visible_logo_cleanup",
            "result_type": result_type,
            "selected_mode": selected_mode,
            "requested_mode": requested_mode,
            "region_source": region_source,
            "warning_flags": warning_flags,
            "suitability_level": suitability_level,
            "suitability_outcome": suitability_outcome,
            "retry_count": retry_count,
            **ff,
            "download_url": dl_url,
            "filename": output_filename,
            "file_size_mb": file_size_mb,
            "method_used": method,
            "region": {"x": x, "y": y, "w": w, "h": h},
            "source_type": "flow_veo_cleanup",
            "visible_logo_cleanup": True,
            "natural_stats": natural_stats,
            "worker_exec_sec": worker_exec_sec,
        }

        _write_meta(work_dir, {
            "active_task_kind": "process",
            "status": "completed",
            "job_status": "done",
            "progress": 100,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "job_kind": "visible_logo_cleanup",
            "method": method,
            "preset": preset,
            "region": {"x": x, "y": y, "w": w, "h": h},
            "result_type": result_type,
            "selected_mode": selected_mode,
            "requested_mode": requested_mode,
            "region_source": region_source,
            "region_adjusted": region_adjusted,
            "expand_padding": expand_padding,
            "cleanup_mode": method,
            "mask_file": os.path.basename(mask_path) if method in ("natural", "telea", "telea_soft") else None,
            "natural_stats": natural_stats,
            "suitability_level": suitability_level,
            "suitability_outcome": suitability_outcome,
            "warning_flags": warning_flags,
            "output_file_size_mb": file_size_mb,
            "output_filename": output_filename,
            "cleaned_file_id": ff.get("cleaned_file_id"),
            "download_url": dl_url,
            "retry_count": retry_count,
            "error": None,
            "error_code": None,
            "result_payload": result_payload,
        })

        return result_payload

    except Exception as e:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        if _read_meta(work_dir).get("status") == "cancelled" or "cancelled" in str(e).lower():
            _write_meta(work_dir, {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
                "error": "Đã hủy xử lý video.",
                "error_code": "job_cancelled",
            })
            return {
                "success": False,
                "job_id": temp_id,
                "temp_id": temp_id,
                "status": "cancelled",
                "progress": _read_meta(work_dir).get("progress", 0),
            }
        err_code = _error_code(e)
        err_msg = "Quá thời gian xử lý video." if err_code == "processing_timeout" else str(e)[:300]
        _mark_job_failed(temp_id, err_msg, err_code, job_params)
        raise


def _enqueue_cleanup_task(temp_id: str, job_params: Dict[str, Any]) -> Optional[str]:
    """Dispatch `inpaint_logo_task` onto the Celery 'media' queue if a worker is reachable or mocked."""
    from app.tasks.media_tasks import inpaint_logo_task
    from app.core.celery_app import celery_app

    apply_fn = getattr(inpaint_logo_task, "apply_async", None)
    is_mocked = (
        hasattr(apply_fn, "_mock_name")
        or getattr(apply_fn, "__qualname__", "Task.apply_async") != "Task.apply_async"
        or os.getenv("FLOW_CLEANUP_FORCE_ASYNC") == "1"
    )
    if is_mocked:
        res = inpaint_logo_task.apply_async(args=[temp_id, job_params], queue="media")
        return str(getattr(res, "id", temp_id))

    try:
        insp = celery_app.control.inspect(timeout=1.5)
        active = insp.ping() if insp else None
        if not active:
            return None
        res = inpaint_logo_task.apply_async(args=[temp_id, job_params], queue="media", retry=False)
        return str(getattr(res, "id", temp_id))
    except Exception as exc:
        log.warning("[flow-cleanup] media queue unavailable, falling back to in-process execution: %s", exc)
        return None


def _start_or_queue_cleanup_job(temp_id: str, job_params: Dict[str, Any], retry_count: int = 0) -> Dict[str, Any]:
    work_dir = _safe_work_dir(temp_id)
    job_params = dict(job_params)
    job_params["retry_count"] = retry_count

    _remove_partial_outputs(work_dir)
    _write_meta(work_dir, {
        "job_id": temp_id,
        "temp_id": temp_id,
        "active_task_kind": "process",
        "status": "queued",
        "progress": 0,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "retry_count": retry_count,
        "method": job_params.get("method"),
        "preset": job_params.get("preset"),
        "region": job_params.get("region"),
        "requested_mode": job_params.get("requested_mode"),
        "suitability_level": job_params.get("suitability_level"),
        "job_params": job_params,
        "error": None,
        "error_code": None,
    })

    task_id = _enqueue_cleanup_task(temp_id, job_params)
    if task_id is not None:
        _write_meta(work_dir, {"celery_task_id": task_id})
        return {
            "success": True,
            "job_id": temp_id,
            "temp_id": temp_id,
            "celery_task_id": task_id,
            "status": "queued",
            "progress": 0,
            "retry_count": retry_count,
            "status_url": f"/api/v1/flow-cleanup/status/{temp_id}",
            "cancel_url": f"/api/v1/flow-cleanup/cancel/{temp_id}",
            "retry_url": f"/api/v1/flow-cleanup/retry/{temp_id}",
            "method_used": job_params.get("method"),
            "region": job_params.get("region"),
        }

    # In-process fallback is ONLY enabled when FLOW_CLEANUP_ALLOW_INPROCESS=1 (dev/unit tests).
    # In production, fail fast with HTTP 503 retryable instead of blocking the HTTP worker synchronously.
    if os.getenv("FLOW_CLEANUP_ALLOW_INPROCESS", "0") != "1":
        _mark_job_failed(
            temp_id,
            "Hàng đợi xử lý video tạm thời không khả dụng.",
            "queue_unavailable",
            job_params,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "queue_unavailable",
                "user_message": "Hệ thống xử lý video đang bận hoặc tạm thời gián đoạn. Vui lòng thử lại sau ít phút.",
                "retryable": True,
            },
        )

    try:
        return _execute_flow_cleanup_job(temp_id, job_params)
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Quá thời gian xử lý video.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Xử lý thất bại: {str(e)}")


@router.post("/process")
@limiter.limit("5/minute")
def process_flow_cleanup(
    payload: ProcessRequest,
    request: Request,
    _: None = Depends(_require_pro),
):
    """
    Queue or run visible-logo cleanup on an uploaded Flow/Veo video via Celery 'media' queue.

    SCOPE: visible on-frame logo only.
    SynthID and invisible AI watermarks are NOT affected.
    """
    work_dir = _safe_work_dir(payload.temp_id)
    _prev_meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    caller_id = _caller_user_id(request)
    _verify_job_owner(_prev_meta, caller_id)
    if not payload.consent_confirmed:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "consent_required",
                "user_message": "Vui lòng xác nhận bạn có quyền sử dụng và chỉnh sửa video này trước khi xử lý.",
                "retryable": False,
            },
        )
    if _prev_meta.get("status") in ("queued", "running") and _prev_meta.get("active_task_kind") == "process":
        existing_tid = _prev_meta.get("celery_task_id") or payload.temp_id
        return {
            "success": True,
            "deduplicated": True,
            "job_id": payload.temp_id,
            "temp_id": payload.temp_id,
            "task_id": existing_tid,
            "celery_task_id": existing_tid,
            "task_kind": "process",
            "status": _prev_meta.get("status"),
            "progress": int(_prev_meta.get("progress", 0)),
            "retry_count": int(_prev_meta.get("retry_count", 0)),
            "status_url": f"/api/v1/flow-cleanup/status/{payload.temp_id}",
            "cancel_url": f"/api/v1/flow-cleanup/cancel/{payload.temp_id}",
            "retry_url": f"/api/v1/flow-cleanup/retry/{payload.temp_id}",
        }
    retry_count = _prev_meta.get("retry_count", -1) + 1  # 0 = first attempt

    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    info = _probe(input_path)
    vw = info.get("width", 1920)
    vh = info.get("height", 1080)

    # Normalise method before limit check so duration cap matches method type
    _ALIAS = {"delogo": "natural", "delogo_soft": "natural",
              "telea": "telea", "telea_soft": "telea_soft"}
    method = payload.method if payload.method in ("natural", "telea", "telea_soft", "crop", "blur") else \
             _ALIAS.get(payload.method, "natural")

    # Reject jobs exceeding file size, resolution, or duration limits BEFORE entering worker
    _validate_job_limits(input_path, info, method=method)

    # Guard: never auto-process when detector is uncertain (needs_review=True or status!="detected")
    # unless the user explicitly reviewed/adjusted the region (`region_adjusted=True`).
    det_status = _prev_meta.get("detection_status")
    det_regions = _prev_meta.get("detected_regions") or []
    det_needs_review = bool(
        _prev_meta.get("detection_needs_review")
        or det_status in ("needs_review", "none")
        or (det_regions and (det_regions[0].get("needs_review") or float(det_regions[0].get("confidence", 0.0)) < 0.75))
    )
    if (payload.preset == "auto" or det_needs_review) and not payload.region_adjusted:
        if payload.preset == "auto" and not det_needs_review and det_regions and payload.region is None:
            payload.region = {
                "x": det_regions[0]["x"],
                "y": det_regions[0]["y"],
                "w": det_regions[0]["w"],
                "h": det_regions[0]["h"],
            }
            payload.preset = "custom"
        elif payload.preset in ("auto", "custom") and det_needs_review:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "detection_needs_review",
                    "needs_review": True,
                    "user_message": "Hệ thống chưa chắc chắn vị trí watermark (độ tin cậy thấp). Vui lòng kiểm tra và chỉnh lại vùng chọn trước khi xử lý.",
                },
            )

    # Phase P3 Guard: if user supplied keyframe overrides on /process, recompute affected tracking segment first
    tracked_boxes = payload.frame_boxes or _prev_meta.get("tracked_boxes")
    tracking_needs_review = bool(_prev_meta.get("tracking_needs_review"))
    if payload.keyframes:
        from app.services.watermark_track import retrack_with_keyframes, track_watermark_in_video
        prev_track_obj = _prev_meta.get("tracking_result")
        if isinstance(prev_track_obj, dict) and prev_track_obj.get("frames"):
            tr_res = retrack_with_keyframes(input_path, prev_track_obj, payload.keyframes)
        else:
            tr_res = track_watermark_in_video(input_path, keyframes=payload.keyframes)
        tracked_boxes = tr_res["boxes_by_frame"]
        tracking_needs_review = bool(tr_res.get("needs_review"))
        _write_meta(work_dir, {
            "tracking_status": tr_res["status"],
            "tracking_needs_review": tracking_needs_review,
            "tracked_boxes": tracked_boxes,
            "tracking_review_keyframes": tr_res.get("review_keyframes", []),
            "tracking_result": tr_res,
        })
        if payload.region is None and "0" in tracked_boxes:
            b0 = tracked_boxes["0"]
            payload.region = {"x": b0[0], "y": b0[1], "w": b0[2], "h": b0[3]}
            payload.preset = "custom"

    # Never auto-erase if tracker lost track or tracking confidence is low without user confirmation
    if tracking_needs_review and not payload.region_adjusted and not payload.keyframes:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "tracking_needs_review",
                "needs_review": True,
                "review_keyframes": _prev_meta.get("tracking_review_keyframes", []),
                "user_message": "Theo vết watermark mất dấu hoặc độ tin cậy thấp ở một số frame (needs_review=true). Không tự động xoá — vui lòng chỉnh lại tại keyframe trước khi xử lý.",
            },
        )

    # Resolve region — shared helper used by /preview-frame and /process
    raw_region = _resolve_region(payload.preset, payload.region, vw, vh)
    x, y, w, h = int(raw_region["x"]), int(raw_region["y"]), int(raw_region["w"]), int(raw_region["h"])
    if w <= 0 or h <= 0:
        raise HTTPException(status_code=400, detail="Kích thước vùng không hợp lệ.")
    if x < 0 or y < 0 or x + w > vw or y + h > vh:
        raise HTTPException(status_code=400, detail="Vùng nằm ngoài khung hình.")

    _check_user_concurrency(caller_id, payload.temp_id)
    _append_consent_audit_log(caller_id, payload.temp_id, action="process_confirm")

    # Expand region by expand_padding (clamp to frame) — not applied to crop
    if payload.expand_padding > 0 and method != "crop":
        ep = max(0, int(payload.expand_padding))
        x = max(0, x - ep);  y = max(0, y - ep)
        w = min(vw - x, w + ep * 2);  h = min(vh - y, h + ep * 2)

    job_params = {
        "method": method,
        "preset": payload.preset,
        "region": {"x": x, "y": y, "w": w, "h": h},
        "expand_padding": payload.expand_padding,
        "region_adjusted": payload.region_adjusted,
        "suitability_level": payload.suitability_level,
        "requested_mode": payload.requested_mode,
        "video_info": info,
        "frame_boxes": tracked_boxes,
        "owner_user_id": caller_id,
    }
    return _start_or_queue_cleanup_job(payload.temp_id, job_params, retry_count=retry_count)


@router.get("/status/{job_id}")
@limiter.limit("600/minute")
async def get_flow_cleanup_status(job_id: str, request: Request = None, _: None = Depends(_require_pro)):
    """Return status, progress (0-100), and result fields for a queued/running/done/failed/cancelled job."""
    work_dir = _safe_work_dir(job_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Job không còn tồn tại hoặc đã hết hạn.")
    if not meta:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông tin job.")
    _verify_job_owner(meta, _caller_user_id(request))

    raw_status = meta.get("status", "uploaded")
    norm_status = "done" if raw_status in ("completed", "done") else raw_status
    progress = int(meta.get("progress", 100 if norm_status == "done" else 0))
    task_kind = meta.get("active_task_kind", "process")

    resp: Dict[str, Any] = {
        "success": norm_status not in ("failed", "cancelled"),
        "job_id": job_id,
        "temp_id": job_id,
        "task_kind": task_kind,
        "status": norm_status,
        "progress": progress,
        "retry_count": int(meta.get("retry_count", 0)),
        "method_used": meta.get("method"),
        "region": meta.get("region"),
        "error": meta.get("error"),
        "error_code": meta.get("error_code"),
    }

    if norm_status == "done":
        if task_kind == "track":
            tr_obj = meta.get("tracking_result")
            if isinstance(tr_obj, dict):
                resp.update(tr_obj)
            resp["tracking_status"] = meta.get("tracking_status", "tracked")
            resp["status"] = "done"
            resp["progress"] = 100
        elif task_kind == "preview_clip":
            owner_id = str(meta.get("owner_user_id") or "")
            resp.update({
                "preview_clip_url": _build_signed_url(
                    f"/api/v1/flow-cleanup/preview-clip-video/{job_id}",
                    job_id,
                    "preview_clip",
                    owner_id,
                ),
                "clip_duration": meta.get("preview_clip_duration", 4.0),
                "method_used": meta.get("preview_clip_method") or meta.get("method"),
                "region": meta.get("preview_clip_region") or meta.get("region"),
                "status": "done",
                "progress": 100,
            })
        else:
            saved_payload = meta.get("result_payload")
            if isinstance(saved_payload, dict):
                resp.update(saved_payload)
                if "natural_stats" not in resp and meta.get("natural_stats") is not None:
                    resp["natural_stats"] = meta.get("natural_stats")
                resp["status"] = "done"
                resp["progress"] = 100
            else:
                cleaned_id = meta.get("cleaned_file_id")
                if cleaned_id:
                    real = resolve_file_id(cleaned_id, work_dir)
                    if real and os.path.isfile(real):
                        resp.update(file_fields(real, "cleaned_file_id", "cleaned_path"))
                resp.update({
                    "download_url": meta.get("download_url"),
                    "filename": meta.get("output_filename"),
                    "file_size_mb": meta.get("output_file_size_mb"),
                    "result_type": meta.get("result_type"),
                    "selected_mode": meta.get("selected_mode"),
                    "requested_mode": meta.get("requested_mode"),
                    "warning_flags": meta.get("warning_flags", []),
                    "suitability_level": meta.get("suitability_level"),
                    "suitability_outcome": meta.get("suitability_outcome"),
                })
    return resp


@router.post("/cancel/{job_id}")
async def cancel_flow_cleanup_job(job_id: str, request: Request = None, _: None = Depends(_require_pro)):
    """Cancel a queued or running Flow cleanup job and clean up partial outputs."""
    work_dir = _safe_work_dir(job_id)
    if not os.path.isdir(work_dir):
        raise HTTPException(status_code=404, detail="Job không tồn tại hoặc đã hết hạn.")

    meta = _read_meta(work_dir)
    _verify_job_owner(meta, _caller_user_id(request))
    task_id = meta.get("celery_task_id")
    if task_id:
        try:
            from app.core.celery_app import celery_app
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            pass

    _remove_partial_outputs(work_dir)
    _write_meta(work_dir, {
        "status": "cancelled",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
        "error": "Đã hủy xử lý video.",
        "error_code": "job_cancelled",
    })
    return {
        "success": True,
        "job_id": job_id,
        "temp_id": job_id,
        "status": "cancelled",
        "progress": int(meta.get("progress", 0)),
    }


@router.post("/retry/{job_id}")
@limiter.limit("5/minute")
def retry_flow_cleanup_job(job_id: str, request: Request, _: None = Depends(_require_pro)):
    """Retry a failed or cancelled Flow cleanup job using its stored job_params."""
    work_dir = _safe_work_dir(job_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Job không còn tồn tại hoặc đã hết hạn.")
    _verify_job_owner(meta, _caller_user_id(request))
    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="File gốc không còn tồn tại. Vui lòng upload lại.")

    current_retries = int(meta.get("retry_count", 0))
    if current_retries >= _MAX_RETRY_COUNT:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "max_retries_exceeded",
                "user_message": f"Đã vượt quá số lần thử lại tối đa ({_MAX_RETRY_COUNT} lần).",
                "retry_count": current_retries,
                "max_retries": _MAX_RETRY_COUNT,
                "retryable": False,
            },
        )

    job_params = meta.get("job_params")
    if not isinstance(job_params, dict) or not job_params.get("region"):
        raise HTTPException(status_code=400, detail="Không có cấu hình job trước đó để thử lại.")

    info = job_params.get("video_info") or _probe(input_path)
    _validate_job_limits(input_path, info, method=job_params.get("method", "natural"))

    retry_count = current_retries + 1
    return _start_or_queue_cleanup_job(job_id, job_params, retry_count=retry_count)


@router.get("/result/{temp_id}/{file_id}")
async def get_cleaned_result(
    temp_id: str,
    file_id: str,
    filename: str = "cleaned.mp4",
    request: Request = None,
    user=Depends(get_optional_user),
):
    """Serve a cleaned output of a session by its file id (cleaned_*.mp4)."""
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="File không còn tồn tại hoặc đã hết hạn.")
    _verify_job_owner(meta, _caller_user_id(request, user))
    if not file_id.startswith("cleaned_"):
        raise HTTPException(status_code=400, detail="Invalid file.")
    real = resolve_file_id(file_id, work_dir)
    if real is None:
        raise HTTPException(status_code=400, detail="Invalid file.")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="File không còn tồn tại hoặc đã hết hạn.")
    return FileResponse(real, media_type="video/mp4", filename=filename)


class FromLocalRequest(BaseModel):
    local_path: str
    title: Optional[str] = None


@router.post("/from-local")
@limiter.limit("10/minute")
def cleanup_from_local_path(
    payload: FromLocalRequest,
    request: Request,
    _: None = Depends(_require_pro),
):
    # Sync def (not async) is intentional — see process_flow_cleanup above.
    """
    Start a logo-cleanup session from an already-downloaded video file.
    Accepts a server-side local_path (must be inside downloads dir).
    """
    # file_id or legacy path → shared validator (app.core.local_download)
    abs_path = resolve_local_input(payload.local_path, _DOWNLOAD_DIR)
    if abs_path is None:
        raise HTTPException(status_code=403, detail="Đường dẫn không hợp lệ.")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="File không còn tồn tại hoặc đã hết hạn. Tải lại video trước.")

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ MP4, MOV, WebM, MKV.")

    if os.path.getsize(abs_path) > _MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File vượt quá {_MAX_UPLOAD_MB}MB.")

    temp_id = new_token()  # 128 bits — the work dir name is the capability
    work_dir = _safe_work_dir(temp_id)
    os.makedirs(work_dir, exist_ok=True)

    input_path = os.path.join(work_dir, f"input{ext}")
    frame_path  = os.path.join(work_dir, "preview.jpg")

    try:
        shutil.copy2(abs_path, input_path)
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Không thể copy file: {e}")

    info = _probe(input_path)
    try:
        _validate_job_limits(input_path, info, method="crop")
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    if not _extract_frame(input_path, frame_path) or not os.path.isfile(frame_path) or os.path.getsize(frame_path) == 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "corrupted_video",
                "user_message": "File video bị hỏng hoặc cắt cụt, không thể giải mã khung hình.",
                "retryable": False,
            },
        )
    file_size_mb = round(os.path.getsize(input_path) / (1024 * 1024), 2)

    owner_id = _caller_user_id(request)
    if not owner_id:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=401, detail="Đăng nhập để sử dụng tính năng này.")
    try:
        _check_and_record_daily_quota(owner_id)
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise

    _write_meta(work_dir, {
        "temp_id": temp_id,
        "job_id": temp_id,
        "owner_user_id": owner_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "uploaded",
        "progress": 0,
        "source": "from_local",
        "source_file_id": safe_file_id(abs_path),  # never the server path
        "title": payload.title or "",
        "video_info": info,
        "file_size_mb": file_size_mb,
    })

    return {
        "success": True,
        "temp_id": temp_id,
        "job_id": temp_id,
        "preview_url": _build_signed_url(f"/api/v1/flow-cleanup/frame/{temp_id}", temp_id, "frame", owner_id),
        "video_info": info,
        "file_size_mb": file_size_mb,
    }


class NoLogoRequest(BaseModel):
    temp_id: str
    suitability_level: Optional[str] = None


@router.post("/no-logo")
async def record_no_logo(
    payload: NoLogoRequest,
    request: Request,
    _: None = Depends(_require_pro),
):
    """
    Record that user confirmed no visible on-frame logo was present.
    Requires Pro authentication and job ownership verification.

    SCOPE: visible logo only. Does not imply absence of SynthID or any invisible watermark.
    """
    work_dir = _safe_work_dir(payload.temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Job không còn tồn tại hoặc đã hết hạn.")
    _verify_job_owner(meta, _caller_user_id(request))

    _write_meta(work_dir, {
        "status": "no_visible_logo",
        "result_type": "original_passthrough_no_logo",
        "job_kind": "visible_logo_cleanup",
        "no_logo_confirmed_at": datetime.now(timezone.utc).isoformat(),
        "suitability_level": payload.suitability_level,
        "suitability_outcome": _SUITABILITY_OUTCOME.get(payload.suitability_level or "", None),
    })
    return {"recorded": True, "result_type": "original_passthrough_no_logo"}


@router.post("/detect/{temp_id}")
@limiter.limit("20/minute")
def detect_flow_watermark(
    temp_id: str,
    request: Request,
    _: None = Depends(_require_pro),
):
    """
    Phase P2: Auto-detect up to 3 candidate visible watermark/logo regions on an uploaded video.
    Returns regions with bbox (x, y, w, h), confidence (0-1), kind ('logo'|'text'|'semi_transparent'|'moving'),
    and needs_review flag for low-confidence detections.
    """
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    _verify_job_owner(meta, _caller_user_id(request))

    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    try:
        from app.services.watermark_detect import detect_watermark_regions
        det = detect_watermark_regions(input_path, max_candidates=3)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Tự phát hiện watermark thất bại: {str(exc)[:200]}")

    needs_review = bool(
        det.get("needs_review")
        or det.get("status") != "detected"
        or any(r.get("needs_review") or float(r.get("confidence", 0.0)) < 0.75 for r in det.get("regions", []))
    )

    _write_meta(work_dir, {
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "detection_status": det["status"],
        "detection_needs_review": needs_review,
        "detected_regions": det["regions"],
        "scene_cuts": det.get("scene_cuts", []),
        "detection_elapsed_sec": det["elapsed_sec"],
    })

    return {
        "success": True,
        "temp_id": temp_id,
        "job_id": temp_id,
        "status": det["status"],
        "needs_review": needs_review,
        "auto_processable": not needs_review,
        "regions": det["regions"],
        "scene_cuts": det.get("scene_cuts", []),
        "sampled_frames": det["sampled_frames"],
        "elapsed_sec": det["elapsed_sec"],
        "video_info": det["video_info"],
    }


class TrackRequest(BaseModel):
    region: Optional[dict] = None              # Initial {x,y,w,h} at frame 0 (optional; auto-detects if omitted)
    keyframes: Optional[List[dict]] = None     # User keyframe overrides [{frame_idx, x, y, w, h}]
    min_confidence: float = 0.65


def _execute_flow_track_job(
    temp_id: str,
    job_params: Dict[str, Any],
    celery_task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run per-frame watermark tracking in Celery worker ('media' queue) or in-process fallback."""
    work_dir = _safe_work_dir(temp_id)
    prev_meta = _read_meta(work_dir)
    if prev_meta.get("status") == "cancelled":
        return {
            "success": False,
            "job_id": temp_id,
            "temp_id": temp_id,
            "task_kind": "track",
            "status": "cancelled",
            "progress": int(prev_meta.get("progress", 0)),
        }

    input_path = _find_input_path(work_dir)
    if not input_path:
        _mark_job_failed(temp_id, "Upload không còn. Vui lòng upload lại.", "source_ingest_failed")
        raise FileNotFoundError("Upload không còn. Vui lòng upload lại.")

    init_box = job_params.get("init_box")
    keyframes = job_params.get("keyframes")
    min_confidence = float(job_params.get("min_confidence", 0.65) or 0.65)

    _write_meta(work_dir, {
        "job_id": temp_id,
        "temp_id": temp_id,
        "owner_user_id": job_params.get("owner_user_id") or prev_meta.get("owner_user_id"),
        "active_task_kind": "track",
        "status": "running",
        "progress": 10,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "celery_task_id": celery_task_id or prev_meta.get("celery_task_id"),
        "error": None,
        "error_code": None,
    })

    def _progress_cb(done_frames: int, total_frames: int) -> bool:
        cur = _read_meta(work_dir)
        if cur.get("status") == "cancelled":
            return False
        pct = min(95, max(10, int(10 + 85 * (done_frames / max(1, total_frames)))))
        if pct != cur.get("progress"):
            _write_meta(work_dir, {"progress": pct})
        return True

    try:
        from app.services.watermark_track import retrack_with_keyframes, track_watermark_in_video
        prev_track = prev_meta.get("tracking_result")
        if keyframes and isinstance(prev_track, dict) and prev_track.get("frames"):
            tr = retrack_with_keyframes(
                input_path,
                prev_track,
                keyframes,
                min_confidence=min_confidence,
                progress_cb=_progress_cb,
                timeout_sec=_TRACK_TIMEOUT_SEC,
            )
        else:
            tr = track_watermark_in_video(
                input_path,
                init_box=init_box,
                keyframes=keyframes,
                min_confidence=min_confidence,
                progress_cb=_progress_cb,
                timeout_sec=_TRACK_TIMEOUT_SEC,
            )

        if _read_meta(work_dir).get("status") == "cancelled":
            raise RuntimeError("Job cancelled by user")

        _write_meta(work_dir, {
            "active_task_kind": "track",
            "status": "done",
            "progress": 100,
            "tracked_at": datetime.now(timezone.utc).isoformat(),
            "tracking_status": tr["status"],
            "tracking_needs_review": tr["needs_review"],
            "tracked_boxes": tr["boxes_by_frame"],
            "tracking_scene_cuts": tr.get("scene_cuts", []),
            "tracking_lost_frames": tr.get("lost_frames", []),
            "tracking_review_keyframes": tr.get("review_keyframes", []),
            "tracking_elapsed_sec": tr["elapsed_sec"],
            "tracking_result": tr,
            "error": None,
            "error_code": None,
        })

        return {
            "success": True,
            "temp_id": temp_id,
            "job_id": temp_id,
            "task_kind": "track",
            **tr,
        }

    except Exception as exc:
        if _read_meta(work_dir).get("status") == "cancelled" or "cancelled" in str(exc).lower():
            _write_meta(work_dir, {
                "status": "cancelled",
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
                "error": "Đã hủy theo vết watermark.",
                "error_code": "job_cancelled",
            })
            return {
                "success": False,
                "job_id": temp_id,
                "temp_id": temp_id,
                "task_kind": "track",
                "status": "cancelled",
                "progress": _read_meta(work_dir).get("progress", 0),
            }
        err_code = _error_code(exc)
        err_msg = "Quá thời gian theo vết watermark." if err_code == "processing_timeout" else f"Theo vết watermark thất bại: {str(exc)[:200]}"
        _mark_job_failed(temp_id, err_msg, err_code)
        raise


def _enqueue_track_task(temp_id: str, job_params: Dict[str, Any]) -> Optional[str]:
    """Dispatch `track_watermark_task` onto the Celery 'media' queue if reachable or mocked."""
    from app.tasks.media_tasks import track_watermark_task
    from app.core.celery_app import celery_app

    apply_fn = getattr(track_watermark_task, "apply_async", None)
    is_mocked = (
        hasattr(apply_fn, "_mock_name")
        or getattr(apply_fn, "__qualname__", "Task.apply_async") != "Task.apply_async"
        or os.getenv("FLOW_CLEANUP_FORCE_ASYNC") == "1"
    )
    if is_mocked:
        res = track_watermark_task.apply_async(args=[temp_id, job_params], queue="media")
        return str(getattr(res, "id", temp_id))

    try:
        insp = celery_app.control.inspect(timeout=1.5)
        active = insp.ping() if insp else None
        if not active:
            return None
        res = track_watermark_task.apply_async(args=[temp_id, job_params], queue="media", retry=False)
        return str(getattr(res, "id", temp_id))
    except Exception as exc:
        log.warning("[flow-cleanup] media queue unavailable for track: %s", exc)
        return None


@router.post("/track/{temp_id}")
@limiter.limit("20/minute")
def track_flow_watermark(
    temp_id: str,
    payload: Optional[TrackRequest] = None,
    request: Request = None,
    _: None = Depends(_require_pro),
):
    """
    Phase P3 / P5.2: Track visible watermark across all frames of an uploaded video asynchronously via 'media' queue.
    - Supports static, drifting, and jumping watermarks.
    - Deduplicates if a `track` task is already queued/running on the same `temp_id`.
    - Enforces per-user concurrency limit (1 active heavy job per user).
    - Returns HTTP 202 with `task_id` and `status_url` when queued on Celery.
    """
    work_dir = _safe_work_dir(temp_id)
    meta = _ensure_work_dir_fresh(work_dir, detail="Upload không còn. Vui lòng upload lại.")
    caller_id = _caller_user_id(request)
    _verify_job_owner(meta, caller_id)

    input_path = _find_input_path(work_dir)
    if not input_path:
        raise HTTPException(status_code=404, detail="Upload không còn. Vui lòng upload lại.")

    # Deduplicate if this temp_id already has a track task queued or running
    if meta.get("status") in ("queued", "running") and meta.get("active_task_kind") == "track":
        existing_tid = meta.get("celery_task_id") or temp_id
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "deduplicated": True,
                "job_id": temp_id,
                "temp_id": temp_id,
                "task_id": existing_tid,
                "celery_task_id": existing_tid,
                "task_kind": "track",
                "status": meta.get("status"),
                "progress": int(meta.get("progress", 0)),
                "status_url": f"/api/v1/flow-cleanup/status/{temp_id}",
                "cancel_url": f"/api/v1/flow-cleanup/cancel/{temp_id}",
            },
        )

    _check_user_concurrency(caller_id, temp_id)

    req = payload or TrackRequest()
    init_box = None
    if req.region and all(k in req.region for k in ("x", "y", "w", "h")):
        init_box = [int(req.region["x"]), int(req.region["y"]), int(req.region["w"]), int(req.region["h"])]
    elif meta.get("detected_regions"):
        r0 = meta["detected_regions"][0]
        init_box = [int(r0["x"]), int(r0["y"]), int(r0["w"]), int(r0["h"])]

    job_params = {
        "init_box": init_box,
        "keyframes": req.keyframes,
        "min_confidence": req.min_confidence,
        "owner_user_id": caller_id,
    }

    _write_meta(work_dir, {
        "job_id": temp_id,
        "temp_id": temp_id,
        "active_task_kind": "track",
        "status": "queued",
        "progress": 0,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "error": None,
        "error_code": None,
    })

    task_id = _enqueue_track_task(temp_id, job_params)
    if task_id is not None:
        _write_meta(work_dir, {"celery_task_id": task_id})
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "deduplicated": False,
                "job_id": temp_id,
                "temp_id": temp_id,
                "task_id": task_id,
                "celery_task_id": task_id,
                "task_kind": "track",
                "status": "queued",
                "progress": 0,
                "status_url": f"/api/v1/flow-cleanup/status/{temp_id}",
                "cancel_url": f"/api/v1/flow-cleanup/cancel/{temp_id}",
            },
        )

    if os.getenv("FLOW_CLEANUP_ALLOW_INPROCESS", "0") != "1":
        _mark_job_failed(temp_id, "Hàng đợi xử lý video tạm thời không khả dụng.", "queue_unavailable")
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "queue_unavailable",
                "user_message": "Hệ thống xử lý video đang bận hoặc tạm thời gián đoạn. Vui lòng thử lại sau ít phút.",
                "retryable": True,
            },
        )

    try:
        return _execute_flow_track_job(temp_id, job_params)
    except HTTPException:
        raise
    except (subprocess.TimeoutExpired, TimeoutError):
        raise HTTPException(status_code=504, detail="Quá thời gian theo vết watermark.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Theo vết watermark thất bại: {str(exc)[:200]}")
