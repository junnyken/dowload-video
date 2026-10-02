"""
POST /api/v1/formats/probe — measured width/height/codec for TikWM formats.

Body: {"urls": [..up to 4..]} — only URLs that /fetch-link itself issued.
Response: {"success": true, "results": [{url, width, height, vcodec, acodec,
bitrate_kbps, duration} | {url, error}]}

Security model lives in app/services/format_probe.py. No auth, same as
/fetch-link; rate-limited per client IP.
"""

from typing import Any, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.main import limiter
from app.services import format_probe

router = APIRouter()


class ProbeRequest(BaseModel):
    urls: List[Any]


@router.post("/formats/probe")
@limiter.limit("20/minute")
async def probe_formats(payload: ProbeRequest, request: Request):
    urls = payload.urls or []
    if not urls:
        raise HTTPException(status_code=400, detail="urls is required")
    if len(urls) > format_probe.MAX_URLS:
        raise HTTPException(
            status_code=400,
            detail=f"Tối đa {format_probe.MAX_URLS} đường dẫn mỗi lần.",
        )
    results = await format_probe.probe_urls(urls)
    return {"success": True, "results": results}
