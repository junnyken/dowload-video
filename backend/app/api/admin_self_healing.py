"""
Admin Self-Healing API
========================
Admin-only routes to monitor and control the Self-Healing Extractor.

Routes:
  GET  /admin/self-healing/status       — platform health overview
  POST /admin/self-healing/reroute      — manually activate/clear a reroute
  GET  /admin/self-healing/config       — current configuration
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.admin import verify_admin

router = APIRouter()


@router.get("/self-healing/status")
async def self_healing_status(admin=Depends(verify_admin)):
    """Return health status for all monitored platforms."""
    from app.services.self_healing import monitor, ENABLED
    return {
        "enabled": ENABLED,
        "platforms": monitor.get_status(),
    }


class RerouteRequest(BaseModel):
    platform: str
    method: Optional[str] = None      # None = clear reroute
    duration_minutes: int = 60


@router.post("/self-healing/reroute")
async def self_healing_reroute(payload: RerouteRequest, admin=Depends(verify_admin)):
    """Manually activate or clear a reroute for a platform."""
    from app.services.self_healing import monitor, FALLBACK_ROUTES

    if payload.method:
        valid_methods = FALLBACK_ROUTES.get(payload.platform, []) + ["cobalt", "browser_direct", "proxy_pool", "proxy_cn"]
        if payload.method not in valid_methods:
            raise HTTPException(
                status_code=400,
                detail=f"Method '{payload.method}' không hợp lệ cho {payload.platform}. "
                       f"Có thể dùng: {', '.join(valid_methods)}"
            )
        monitor.set_reroute(payload.platform, payload.method, payload.duration_minutes)
        return {
            "success": True,
            "action": "rerouted",
            "platform": payload.platform,
            "method": payload.method,
            "duration_minutes": payload.duration_minutes,
        }
    else:
        monitor.clear_reroute(payload.platform)
        return {
            "success": True,
            "action": "cleared",
            "platform": payload.platform,
        }


@router.get("/self-healing/config")
async def self_healing_config(admin=Depends(verify_admin)):
    """Return current self-healing configuration."""
    from app.services.self_healing import (
        ENABLED, WINDOW_MINUTES, ERROR_THRESHOLD, MIN_SAMPLES,
        COOLDOWN_MINUTES, TELEGRAM_ENABLED, FALLBACK_ROUTES,
    )
    return {
        "enabled": ENABLED,
        "window_minutes": WINDOW_MINUTES,
        "error_threshold_pct": ERROR_THRESHOLD,
        "min_samples": MIN_SAMPLES,
        "cooldown_minutes": COOLDOWN_MINUTES,
        "telegram_enabled": TELEGRAM_ENABLED,
        "fallback_routes": FALLBACK_ROUTES,
    }
