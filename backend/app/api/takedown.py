"""
Content takedown requests (task #6335, Phase 34D-0).
====================================================
  POST /api/v1/takedown                       public, 5 requests / hour / IP
  GET  /api/v1/admin/takedown                 admin, newest first
  POST /api/v1/admin/takedown/{id}/status     admin, audited

Stored in the Redis list `takedown:requests` (newest at the head, capped at
1000) as JSON. The requester's IP is never stored raw: only a salted sha256.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from app.api.admin import verify_admin
from app.core.audit import log_admin_action
from app.core.client_ip import get_client_ip

logger = logging.getLogger(__name__)

public_router = APIRouter()
admin_router = APIRouter()

LIST_KEY = "takedown:requests"
LIST_CAP = 1000
RATE_LIMIT = 5
RATE_WINDOW_SEC = 3600
MAX_LEN = 2000
STATUSES = ("new", "reviewing", "done", "rejected")
CAPACITIES = ("owner", "agent")

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,}$")
_HTTPS = re.compile(r"^https://[^\s/$.?#][^\s]*$", re.IGNORECASE)


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _ip_hash(ip: str) -> str:
    salt = os.getenv("TAKEDOWN_IP_SALT", "vidgrab-takedown")
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


class TakedownRequest(BaseModel):
    # wording: BA review (error messages below)
    name: str
    email: str
    capacity: str
    content_url: str
    related: Optional[str] = ""
    description: str
    declaration: bool
    website: Optional[str] = ""  # honeypot: real users leave it empty

    @field_validator("name", "description")
    @classmethod
    def _required_text(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Trường này là bắt buộc")
        if len(v) > MAX_LEN:
            raise ValueError(f"Tối đa {MAX_LEN} ký tự")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) > MAX_LEN or not _EMAIL.match(v):
            raise ValueError("Email không hợp lệ")
        return v

    @field_validator("capacity")
    @classmethod
    def _capacity(cls, v: str) -> str:
        if v not in CAPACITIES:
            raise ValueError("Tư cách không hợp lệ")
        return v

    @field_validator("content_url")
    @classmethod
    def _content_url(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) > MAX_LEN or not _HTTPS.match(v):
            raise ValueError("Link phải bắt đầu bằng https://")
        return v

    @field_validator("related")
    @classmethod
    def _related(cls, v: Optional[str]) -> str:
        v = (v or "").strip()
        if len(v) > MAX_LEN:
            raise ValueError(f"Tối đa {MAX_LEN} ký tự")
        return v

    @field_validator("declaration")
    @classmethod
    def _declaration(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("Bạn cần xác nhận thông tin là đúng")
        return v

    @field_validator("website")
    @classmethod
    def _website(cls, v: Optional[str]) -> str:
        return (v or "")[:200]


@public_router.post("/takedown", status_code=201)
async def submit_takedown(req: TakedownRequest, request: Request):
    if req.website:
        # Bot filled the hidden field: answer like a success, store nothing.
        return {"ok": True, "id": uuid.uuid4().hex[:12]}
    iph = _ip_hash(get_client_ip(request))
    try:
        r = _r()
        rl_key = f"takedown:rl:{iph[:32]}"
        n = r.incr(rl_key)
        if n == 1:
            r.expire(rl_key, RATE_WINDOW_SEC)
        if n > RATE_LIMIT:
            raise HTTPException(
                status_code=429,
                # wording: BA review
                detail="Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau 1 giờ.")
        rec = {
            "id": uuid.uuid4().hex[:12],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "new",
            "name": req.name,
            "email": req.email,
            "capacity": req.capacity,
            "content_url": req.content_url,
            "related": req.related,
            "description": req.description,
            "ip_hash": iph,
        }
        r.lpush(LIST_KEY, json.dumps(rec, ensure_ascii=False))
        r.ltrim(LIST_KEY, 0, LIST_CAP - 1)
    except HTTPException:
        raise
    except Exception:
        logger.exception("takedown: store failed")
        # wording: BA review
        raise HTTPException(status_code=503, detail="Chưa gửi được yêu cầu. Vui lòng thử lại sau ít phút.")
    await _notify_new_request(rec)
    return {"ok": True, "id": rec["id"]}


async def _notify_new_request(rec: dict) -> None:
    """Tell the owner on Telegram (same channel as other alerts): without it
    a request waits until someone opens the admin page. Never blocks or
    fails the submission; no requester e-mail/name in the message."""
    try:
        from app.core.notifications import send_telegram_message  # noqa: PLC0415
        from urllib.parse import urlsplit  # noqa: PLC0415
        host = urlsplit(rec.get("content_url") or "").hostname or "?"
        # wording: BA review
        await send_telegram_message(
            f"📩 Yêu cầu gỡ nội dung mới #{rec['id']} ({host}). Xem tại admin → Gỡ nội dung.", parse_mode="")
    except Exception:  # noqa: BLE001
        logger.info("takedown: telegram notice skipped")


def _load(r) -> list[Dict[str, Any]]:
    out = []
    for raw in r.lrange(LIST_KEY, 0, LIST_CAP - 1):
        try:
            out.append(json.loads(_s(raw)))
        except Exception:
            continue
    return out


@admin_router.get("/takedown", dependencies=[Depends(verify_admin)])
async def list_takedown():
    try:
        items = _load(_r())
    except Exception:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    return {"items": items, "total": len(items)}


class StatusBody(BaseModel):
    status: str


@admin_router.post("/takedown/{req_id}/status", dependencies=[Depends(verify_admin)])
async def set_takedown_status(req_id: str, body: StatusBody, request: Request):
    if body.status not in STATUSES:
        raise HTTPException(status_code=422, detail="status must be one of: " + ", ".join(STATUSES))
    try:
        r = _r()
        for idx, raw in enumerate(r.lrange(LIST_KEY, 0, LIST_CAP - 1)):
            try:
                rec = json.loads(_s(raw))
            except Exception:
                continue
            if rec.get("id") == req_id:
                old = rec.get("status")
                rec["status"] = body.status
                r.lset(LIST_KEY, idx, json.dumps(rec, ensure_ascii=False))
                log_admin_action(request, "admin.takedown.status", resource_type="takedown",
                                 resource_id=req_id, metadata={"from": old, "to": body.status})
                return {"ok": True, "id": req_id, "status": body.status}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    raise HTTPException(status_code=404, detail="Not found")
