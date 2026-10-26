"""
Admin — device linking ("Liên kết thiết bị", owner 24/10/2026)
==============================================================
See app.core.device_link. Mounted under /api/v1/admin, verify_admin on every
route, every change audited.

  GET  /admin/device-links              settings + 7-day counters + last events
                                        + devices used by several accounts
  GET  /admin/device-links/user/{uid}   that account's devices (8-char ids)
  POST /admin/device-links/settings     {"mode"?: off|shadow|enforce,
                                         "enforce_at"?: epoch | null,
                                         "free_max"?: int, "paid_max"?: int,
                                         "extra_enforce_at"?: epoch, "clear_extra_enforce_at"?: bool,
                                         "net_free_accounts"?: int, "clone_networks"?: int}
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.admin import verify_admin
from app.core import device_link
from app.core.audit import log_admin_action

router = APIRouter()


class SettingsBody(BaseModel):
    mode: Optional[str] = None
    enforce_at: Optional[int] = None
    clear_enforce_at: bool = False
    free_max: Optional[int] = None
    paid_max: Optional[int] = None
    # 26/10 bypass guards (their own watch period)
    extra_enforce_at: Optional[int] = None
    clear_extra_enforce_at: bool = False
    net_free_accounts: Optional[int] = None
    clone_networks: Optional[int] = None


@router.get("/device-links")
async def device_links(_=Depends(verify_admin)) -> dict:
    import asyncio  # noqa: PLC0415
    try:
        return await asyncio.to_thread(device_link.stats)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Redis: {type(e).__name__}")


@router.get("/device-links/user/{user_id}")
async def device_links_user(user_id: str, _=Depends(verify_admin)) -> dict:
    try:
        return {"user_id": user_id, "devices": device_link.user_devices(user_id)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Redis: {type(e).__name__}")


@router.post("/device-links/settings")
async def device_links_settings(body: SettingsBody, request: Request, _=Depends(verify_admin)) -> dict:
    old = device_link.get_settings()
    kwargs = {"mode": body.mode, "free_max": body.free_max, "paid_max": body.paid_max,
              "net_free_accounts": body.net_free_accounts, "clone_networks": body.clone_networks}
    if body.clear_extra_enforce_at:
        kwargs["extra_enforce_at"] = None
    elif body.extra_enforce_at is not None:
        kwargs["extra_enforce_at"] = body.extra_enforce_at
    if body.clear_enforce_at:
        kwargs["enforce_at"] = None
    elif body.enforce_at is not None:
        kwargs["enforce_at"] = body.enforce_at
    try:
        new = device_link.save_settings(**kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_admin_action(request, "admin.device_links.settings", resource_type="device_links",
                     metadata={"old": old, "new": new})
    return new
