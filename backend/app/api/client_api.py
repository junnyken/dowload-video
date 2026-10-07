"""
Desktop client API (VidGrab Desktop C1)
=======================================
  POST /client/history   — app uploads finished-download metadata (upsert on user + clientId)
  GET  /client/history   — the signed-in user's own rows (limit<=200, `before` cursor on createdAt)
  GET  /client/version   — update info from env, no auth

Both /client/history routes sit behind CLIENT_API_ENABLED (default OFF, read
at call time) and answer 503 {detail, error_code:"client_api_disabled"} when
off — checked BEFORE auth. If migration 034 has not been applied the routes
answer 503 error_code "storage_not_ready" instead of 500.

Privacy: the body model ignores unknown fields, so a local file path (or
anything else the client sends) is dropped, never stored.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.auth_middleware import get_required_user
from app.core.extraction_errors import ExtractionHTTPException
from app.main import limiter

logger = logging.getLogger(__name__)

router = APIRouter()

TABLE = "desktop_downloads"
MAX_ITEMS = 100
MAX_LIST_LIMIT = 200
_STATES = ("completed", "failed")


def client_api_enabled() -> bool:
    raw = os.environ.get("CLIENT_API_ENABLED")
    return bool(raw) and raw.strip().lower() in ("1", "true", "yes", "on")


def _require_enabled() -> None:
    if not client_api_enabled():
        raise ExtractionHTTPException(
            503, "Tính năng đồng bộ lịch sử từ app Windows chưa được bật.", "client_api_disabled")


def _get_db():
    from app.core.database import get_service_client  # noqa: PLC0415
    return get_service_client()


# ── models ───────────────────────────────────────────────────────────────

class HistoryItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")  # file paths etc. are dropped

    clientId: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=500)
    platform: str = Field(default="", max_length=64)
    formatLabel: str = Field(default="", max_length=128)
    fileSize: Optional[int] = Field(default=None, ge=0, le=2**53)
    state: str
    errorCode: Optional[str] = Field(default=None, max_length=128)
    finishedAt: Optional[datetime] = None

    @field_validator("url")
    @classmethod
    def _http_url(cls, v: str) -> str:
        low = v.strip().lower()
        if not (low.startswith("http://") or low.startswith("https://")) or " " in v.strip():
            raise ValueError("url must be http(s)")
        return v.strip()

    @field_validator("state")
    @classmethod
    def _state(cls, v: str) -> str:
        if v not in _STATES:
            raise ValueError("state must be completed or failed")
        return v


class HistoryIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    deviceId: str = Field(default="", max_length=128)
    clientVersion: str = Field(default="", max_length=64)
    items: List[HistoryItemIn] = Field(max_length=MAX_ITEMS)


# ── helpers ──────────────────────────────────────────────────────────────

def _storage_not_ready(exc: Exception) -> bool:
    """Table missing (migration 034 not applied): Postgres 42P01 / PostgREST PGRST205."""
    msg = str(exc).lower()
    return "42p01" in msg or "pgrst205" in msg or (
        TABLE in msg and ("does not exist" in msg or "schema cache" in msg))


def _storage_response(exc: Exception):
    if _storage_not_ready(exc):
        return JSONResponse(status_code=503, content={
            "detail": "Kho lưu lịch sử app Windows chưa sẵn sàng.",
            "error_code": "storage_not_ready"})
    logger.exception("client_api storage error")
    return JSONResponse(status_code=500, content={
        "detail": "Không thể truy cập kho lịch sử.", "error_code": "storage_error"})


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    return v.astimezone(timezone.utc).isoformat() if isinstance(v, datetime) else str(v)


def _row_out(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": r.get("id"),
        "clientId": r.get("client_id"),
        "deviceId": r.get("device_id"),
        "clientVersion": r.get("client_version"),
        "url": r.get("url"),
        "title": r.get("title"),
        "platform": r.get("platform"),
        "formatLabel": r.get("format_label"),
        "fileSize": r.get("file_size"),
        "state": r.get("state"),
        "errorCode": r.get("error_code"),
        "finishedAt": r.get("finished_at"),
        "createdAt": r.get("created_at"),
    }


# ── routes ───────────────────────────────────────────────────────────────

@router.post("/client/history")
@limiter.limit("30/minute")
async def post_client_history(
    request: Request,
    body: HistoryIn,
    _flag: None = Depends(_require_enabled),
    user=Depends(get_required_user),
):
    uid = str(user["id"])
    # Last write wins for a clientId repeated inside one batch (Postgres
    # rejects an ON CONFLICT batch that touches the same row twice).
    by_id: Dict[str, HistoryItemIn] = {}
    for it in body.items:
        by_id[it.clientId] = it
    if not by_id:
        return {"accepted": 0, "ids": []}

    def row(it: HistoryItemIn) -> Dict[str, Any]:
        return {
            "user_id": uid, "client_id": it.clientId,
            "device_id": body.deviceId or None, "client_version": body.clientVersion or None,
            "url": it.url, "title": it.title or None, "platform": it.platform or None,
            "format_label": it.formatLabel or None, "file_size": it.fileSize,
            "state": it.state, "error_code": it.errorCode,
            "finished_at": _iso(it.finishedAt),
        }

    try:
        db = _get_db()
        existing = (
            db.table(TABLE).select("client_id").eq("user_id", uid)
            .in_("client_id", list(by_id)).execute()
        ).data or []
        seen = {r["client_id"] for r in existing}
        # New rows get distinct, increasing created_at values so the
        # `before` cursor never skips rows that share a transaction timestamp.
        # Existing rows are upserted WITHOUT created_at (keeps their position).
        base = datetime.now(timezone.utc)
        new_rows, old_rows = [], []
        for i, (cid, it) in enumerate(by_id.items()):
            r = row(it)
            if cid in seen:
                old_rows.append(r)
            else:
                r["created_at"] = _iso(base + timedelta(microseconds=i))
                new_rows.append(r)
        for batch in (new_rows, old_rows):
            if batch:
                db.table(TABLE).upsert(batch, on_conflict="user_id,client_id").execute()
    except Exception as exc:  # noqa: BLE001
        return _storage_response(exc)
    return {"accepted": len(by_id), "ids": list(by_id)}


@router.get("/client/history")
@limiter.limit("60/minute")
async def get_client_history(
    request: Request,
    limit: int = 50,
    before: Optional[str] = None,
    _flag: None = Depends(_require_enabled),
    user=Depends(get_required_user),
):
    limit = max(1, min(int(limit), MAX_LIST_LIMIT))
    cursor = None
    if before:
        try:
            cursor = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="before must be an ISO-8601 timestamp")
    try:
        q = _get_db().table(TABLE).select("*").eq("user_id", str(user["id"]))
        if cursor is not None:
            q = q.lt("created_at", _iso(cursor if cursor.tzinfo else cursor.replace(tzinfo=timezone.utc)))
        res = q.order("created_at", desc=True).limit(limit).execute()
    except Exception as exc:  # noqa: BLE001
        return _storage_response(exc)
    items = [_row_out(r) for r in (res.data or [])]
    out: Dict[str, Any] = {"items": items}
    if len(items) == limit:
        out["nextBefore"] = items[-1]["createdAt"]
    return out


@router.get("/client/version")
@limiter.limit("60/minute")
async def get_client_version(request: Request):
    env = os.environ.get
    latest = (env("DESKTOP_LATEST_VERSION") or "0.1.0").strip()
    return {
        "latest": latest,
        "minSupported": (env("DESKTOP_MIN_VERSION") or "0.1.0").strip(),
        "notes": env("DESKTOP_RELEASE_NOTES") or "",
        "downloadUrl": env("DESKTOP_DOWNLOAD_URL") or "",
        # Task #6087: the app reads its quota mode here without an extra call.
        "features": _features(),
    }


def _features() -> Dict[str, Any]:
    from app.api import client_quota  # noqa: PLC0415
    return {
        "clientQuota": client_quota.quota_enabled(),
        "clientQuotaMode": client_quota.quota_mode(),
        "offlineGrace": client_quota.offline_grace(),
    }
