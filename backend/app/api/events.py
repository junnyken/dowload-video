"""
Phase 14 — Event Tracking
==========================
POST /api/v1/events/track — fire and forget event ingestion.
Events are buffered in Redis (list vg:events:buffer) and flushed
to Supabase analytics_events table by a periodic Celery task.
"""

import json
import time
import os
from typing import Optional, Any
from fastapi import APIRouter, Request, Header
from pydantic import BaseModel

router = APIRouter()

_BUFFER_KEY = "vg:events:buffer"
_MAX_BUFFER = 5000  # drop oldest if buffer exceeds this


class EventPayload(BaseModel):
    event_name: str
    anonymous_id: Optional[str] = None
    properties: Optional[dict] = None
    experiment_variants: Optional[dict] = None


@router.post("/track")
async def track_event(
    payload: EventPayload,
    request: Request,
    x_vg_source: Optional[str] = Header(None, alias="X-VG-Source"),
):
    """
    Buffer an analytics event in Redis.
    Non-blocking: always returns 204 regardless of Redis state.
    """
    try:
        import redis as _redis
        rc = _redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        # Resolve user from JWT if present (optional)
        user_id = None
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            try:
                from app.core.auth_middleware import get_supabase_user
                user_id = await get_supabase_user(auth[7:])
            except Exception:
                pass

        event = {
            "event_name": payload.event_name,
            "user_id": user_id,
            "anonymous_id": payload.anonymous_id,
            "source": x_vg_source or "web",
            "properties": payload.properties or {},
            "experiment_variants": payload.experiment_variants or {},
            "ts": int(time.time()),
        }
        rc.rpush(_BUFFER_KEY, json.dumps(event))
        # Trim to avoid memory blow-up
        if rc.llen(_BUFFER_KEY) > _MAX_BUFFER:
            rc.ltrim(_BUFFER_KEY, -_MAX_BUFFER, -1)
    except Exception as e:
        print(f"[Events] buffer error (non-fatal): {e}")

    from fastapi.responses import Response
    return Response(status_code=204)


@router.get("/experiments")
async def get_experiments():
    """Return active experiment config for frontend feature flags."""
    try:
        from app.core.database import get_supabase_client
        sb = get_supabase_client()
        rows = sb.table("experiment_config").select("key,value").eq("active", True).execute()
        return {r["key"]: r["value"] for r in (rows.data or [])}
    except Exception as e:
        return {}
