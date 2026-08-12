"""
Disk Guardrail
==============
Monitors disk usage and enforces actions at configurable thresholds.

Thresholds (all configurable via env):
  DISK_WARN_PCT=70    — log warning, send Telegram alert
  DISK_HIGH_PCT=80    — reject new bulk/batch jobs
  DISK_CRITICAL_PCT=90 — reject ALL new download jobs + trigger emergency cleanup
  DISK_EMERGENCY_PCT=95 — force-delete oldest files until back below DISK_HIGH_PCT

Actions are idempotent and rate-limited (alert sent max 1/hour per threshold).
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Tuple

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.structured_log import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------

_WARN_PCT = float(os.getenv("DISK_WARN_PCT", "70"))
_HIGH_PCT = float(os.getenv("DISK_HIGH_PCT", "80"))
_CRITICAL_PCT = float(os.getenv("DISK_CRITICAL_PCT", "90"))
_EMERGENCY_PCT = float(os.getenv("DISK_EMERGENCY_PCT", "95"))

# Path we monitor — same as the downloads directory
_DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/app/downloads")

# Endpoints that accept new download work
_DOWNLOAD_ENDPOINTS = {"/api/v1/fetch-link", "/api/v1/bulk-download"}

# Rate-limit: at most one alert per hour per threshold level
_ALERT_RATE_LIMIT_TTL = 3600  # seconds

ThresholdLevel = Literal["ok", "warn", "high", "critical", "emergency"]


# ---------------------------------------------------------------------------
# DiskState
# ---------------------------------------------------------------------------

@dataclass
class DiskState:
    """Snapshot of disk usage for the downloads partition."""

    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_pct: float
    threshold_level: ThresholdLevel

    # Convenience properties
    @property
    def free_gb(self) -> float:
        return self.free_bytes / 1_073_741_824

    @property
    def used_gb(self) -> float:
        return self.used_bytes / 1_073_741_824

    @property
    def total_gb(self) -> float:
        return self.total_bytes / 1_073_741_824


# ---------------------------------------------------------------------------
# Core monitoring
# ---------------------------------------------------------------------------

def get_disk_state(path: Optional[str] = None) -> DiskState:
    """
    Return a :class:`DiskState` for *path* (defaults to ``DOWNLOAD_DIR``).

    The ``threshold_level`` field reflects the highest threshold currently
    breached:

    * ``"emergency"`` ≥ ``DISK_EMERGENCY_PCT``
    * ``"critical"``  ≥ ``DISK_CRITICAL_PCT``
    * ``"high"``      ≥ ``DISK_HIGH_PCT``
    * ``"warn"``      ≥ ``DISK_WARN_PCT``
    * ``"ok"``        otherwise
    """
    check_path = path or _DOWNLOAD_DIR
    # Ensure the directory exists before probing (it may not on a fresh container)
    Path(check_path).mkdir(parents=True, exist_ok=True)

    usage = shutil.disk_usage(check_path)
    used_pct = (usage.used / usage.total) * 100 if usage.total else 0.0

    if used_pct >= _EMERGENCY_PCT:
        level: ThresholdLevel = "emergency"
    elif used_pct >= _CRITICAL_PCT:
        level = "critical"
    elif used_pct >= _HIGH_PCT:
        level = "high"
    elif used_pct >= _WARN_PCT:
        level = "warn"
    else:
        level = "ok"

    return DiskState(
        path=check_path,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        used_pct=round(used_pct, 2),
        threshold_level=level,
    )


# ---------------------------------------------------------------------------
# Job acceptance gate
# ---------------------------------------------------------------------------

def check_can_accept_job(job_type: str = "single") -> Tuple[bool, str]:
    """
    Decide whether a new job of *job_type* can be accepted given current disk state.

    Parameters
    ----------
    job_type:
        ``"single"``  — a single-URL download request
        ``"bulk"``    — a multi-URL bulk download
        ``"batch"``   — a scheduled/batch operation

    Returns
    -------
    ``(True, "")``
        Job is accepted.
    ``(False, "<reason>")``
        Job is rejected; *reason* is a user-facing message (English/Vietnamese
        mixed is fine here — the caller can localise further).
    """
    state = get_disk_state()
    level = state.threshold_level

    # Emergency: block everything
    if level == "emergency":
        _maybe_alert(state)
        return (
            False,
            f"disk_emergency: only {state.free_gb:.1f} GB free ({state.used_pct:.0f}% used). "
            "All downloads paused. Please contact support.",
        )

    # Critical: block everything; trigger background cleanup
    if level == "critical":
        _maybe_alert(state)
        return (
            False,
            f"disk_critical: only {state.free_gb:.1f} GB free ({state.used_pct:.0f}% used). "
            "Try again later.",
        )

    # High: allow single but block bulk/batch
    if level == "high":
        if job_type in ("bulk", "batch"):
            _maybe_alert(state)
            return (
                False,
                f"disk_high: only {state.free_gb:.1f} GB free. "
                "Bulk/batch downloads are temporarily paused.",
            )

    # Warn: log but accept
    if level == "warn":
        _maybe_alert(state)

    return True, ""


# ---------------------------------------------------------------------------
# Emergency cleanup
# ---------------------------------------------------------------------------

def emergency_cleanup(target_free_pct: float = 82.0) -> int:
    """
    Delete oldest files in the downloads directory until disk usage
    drops below *target_free_pct* percent, or all non-active files
    are gone.

    Files accessed within the last 60 seconds are considered active
    (in-flight downloads) and are never deleted.

    Returns the number of files deleted.
    """
    download_dir = Path(_DOWNLOAD_DIR)
    now = time.time()
    active_cutoff = now - 60  # files touched in the last 60 s are "active"

    # Collect candidates: (mtime, path)
    candidates: list[tuple[float, Path]] = []
    for dirpath, _, filenames in os.walk(download_dir):
        for fname in filenames:
            fp = Path(dirpath) / fname
            try:
                st = fp.stat()
                # Skip active downloads
                if st.st_atime > active_cutoff:
                    continue
                candidates.append((st.st_mtime, fp))
            except FileNotFoundError:
                pass

    # Sort oldest first
    candidates.sort(key=lambda t: t[0])

    deleted = 0
    for _, fp in candidates:
        state = get_disk_state()
        free_pct = 100.0 - state.used_pct
        if free_pct >= (100.0 - target_free_pct):
            break  # we've freed enough space
        try:
            fp.unlink()
            deleted += 1
            logger.info(
                "emergency_cleanup_deleted",
                extra={"path": str(fp), "deleted_count": deleted},
            )
        except FileNotFoundError:
            pass  # race: already deleted
        except OSError as exc:
            logger.warning("emergency_cleanup_error", extra={"path": str(fp), "error": str(exc)})

    state_after = get_disk_state()
    logger.warning(
        "emergency_cleanup_complete",
        extra={
            "deleted_count": deleted,
            "used_pct_after": state_after.used_pct,
            "free_gb_after": round(state_after.free_gb, 2),
        },
    )
    return deleted


# ---------------------------------------------------------------------------
# Redis-backed rate-limited alerting
# ---------------------------------------------------------------------------

def _maybe_alert(state: DiskState) -> None:
    """
    Send a Telegram alert for *state.threshold_level* if one has not been
    sent in the last hour.  Falls back gracefully when Redis or the
    notifications module are unavailable.
    """
    level = state.threshold_level

    # --- Rate-limit gate via Redis ---
    redis_key = f"disk_alert:{level}"
    try:
        from app.core.redis_client import get_redis  # noqa: PLC0415
        redis = get_redis()
        already_sent = redis.get(redis_key)
        if already_sent:
            return
        redis.setex(redis_key, _ALERT_RATE_LIMIT_TTL, "1")
    except Exception as exc:
        # Redis unavailable — still log and try to alert, but avoid hammering
        logger.debug("disk_alert_redis_unavailable", extra={"error": str(exc)})

    # --- Log the alert ---
    log_fn = logger.error if level in ("critical", "emergency") else logger.warning
    log_fn(
        "disk_pressure_alert",
        extra={
            "threshold_level": level,
            "used_pct": state.used_pct,
            "free_gb": round(state.free_gb, 2),
            "total_gb": round(state.total_gb, 2),
        },
    )

    # --- Telegram notification (best-effort) ---
    try:
        from app.core.notifications import send_telegram_alert  # noqa: PLC0415
        msg = (
            f"[VidGrab] Disk {level.upper()}: "
            f"{state.used_pct:.0f}% used — only {state.free_gb:.1f} GB free "
            f"out of {state.total_gb:.0f} GB total on {state.path}"
        )
        import asyncio  # noqa: PLC0415
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(send_telegram_alert(msg))
        except RuntimeError:
            # No running event loop (e.g. called from sync context)
            asyncio.run(send_telegram_alert(msg))
    except Exception as exc:
        logger.debug("disk_alert_telegram_failed", extra={"error": str(exc)})


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------

class DiskGuardrailMiddleware(BaseHTTPMiddleware):
    """
    Intercepts POST requests to download endpoints and rejects them with
    HTTP 503 when disk pressure is too high.

    The rejection body is JSON-compatible with the VidGrab error envelope::

        {
            "error_code": "disk_pressure",
            "user_message": "...",
            "retryable": true
        }
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path in _DOWNLOAD_ENDPOINTS:
            # Infer job type from path
            job_type = "bulk" if "bulk" in request.url.path else "single"
            can_accept, reason = check_can_accept_job(job_type)

            if not can_accept:
                body = json.dumps(
                    {
                        "error_code": "disk_pressure",
                        "user_message": reason,
                        "retryable": True,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                return Response(
                    content=body,
                    status_code=503,
                    media_type="application/json",
                    headers={"Retry-After": "300"},
                )

        return await call_next(request)


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------

def setup_disk_guardrail(app: FastAPI) -> None:
    """
    Wire the disk guardrail into a FastAPI application.

    Registers:
    * :class:`DiskGuardrailMiddleware` — rejects new jobs under disk pressure
    * A startup event that logs an initial disk health check and triggers
      emergency cleanup if the disk is already in EMERGENCY state on boot.

    Call this before any route registration so middleware is outermost::

        setup_disk_guardrail(app)
        app.include_router(router)
    """

    app.add_middleware(DiskGuardrailMiddleware)

    @app.on_event("startup")
    async def _disk_startup_check() -> None:
        state = get_disk_state()
        log_fn = (
            logger.error
            if state.threshold_level in ("critical", "emergency")
            else logger.warning
            if state.threshold_level in ("warn", "high")
            else logger.info
        )
        log_fn(
            "disk_startup_check",
            extra={
                "threshold_level": state.threshold_level,
                "used_pct": state.used_pct,
                "free_gb": round(state.free_gb, 2),
                "total_gb": round(state.total_gb, 2),
                "path": state.path,
            },
        )

        if state.threshold_level == "emergency":
            logger.error(
                "disk_emergency_on_startup",
                extra={"used_pct": state.used_pct, "action": "triggering_cleanup"},
            )
            import asyncio  # noqa: PLC0415

            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, emergency_cleanup)

        elif state.threshold_level == "critical":
            _maybe_alert(state)
