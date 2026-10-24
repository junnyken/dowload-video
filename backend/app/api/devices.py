"""
"Thiết bị của tôi" (owner 24/10/2026, app.core.device_link)
===========================================================
The person sees the devices their account is used on (website, extension,
Windows app; one PC = one device) and signs out the ones they no longer use,
instead of being surprised when the plan's limit signs one out.

  GET  /api/v1/devices                 {devices: [{id, kind, label, last_seen, current}],
                                        max_devices, mode}
  POST /api/v1/devices/{id}/signout    {ok: true} | 404
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.core import device_link
from app.core.auth_middleware import get_required_user
from app.main import limiter

router = APIRouter()


@router.get("/devices")
@limiter.limit("30/minute")
async def my_devices(request: Request, user=Depends(get_required_user)):
    import asyncio  # noqa: PLC0415
    uid = str(user["id"])
    try:
        st = device_link.get_settings()
        devices = device_link.my_devices(uid, device_link.device_from_request(request))
        tier = await asyncio.to_thread(device_link._tier, uid)
    except Exception:  # noqa: BLE001  Redis down
        return JSONResponse(status_code=503, content={"detail": "Chưa xem được danh sách thiết bị, hãy thử lại sau.",
                                                      "error_code": "devices_unavailable"})
    return {"devices": devices, "max_devices": device_link.max_devices(tier, st), "mode": st["effective_mode"]}


@router.post("/devices/{device_id}/signout")
@limiter.limit("20/minute")
async def sign_out_device(device_id: str, request: Request, user=Depends(get_required_user)):
    try:
        dev = device_link.sign_out_device(str(user["id"]), device_id)
    except Exception:  # noqa: BLE001
        dev = None
    if not dev:
        # wording: BA review
        return JSONResponse(status_code=404, content={"detail": "Không tìm thấy thiết bị này.", "error_code": "device_not_found"})
    return {"ok": True, "current": dev == device_link.device_from_request(request)}
