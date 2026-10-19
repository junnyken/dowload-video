"""
Admin — platform health & yt-dlp channel (2026-10-19)
=====================================================
One place for "is every platform working, and what is the system doing about
the ones that are not": probe results, the last ~2 h of real traffic split
into user / system / extractor_broken failures, platforms marked degraded,
Cobalt-first overrides, and the yt-dlp channel (running version, pin, latest
stable/nightly, history). Actions: try a newer yt-dlp now, roll back, run the
daily stable check, toggle Cobalt-first for a platform.

Mounted under /api/v1/admin, behind verify_admin.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.admin import verify_admin
from app.core.audit import log_admin_action

router = APIRouter()


class RemediateBody(BaseModel):
    platforms: List[str] = Field(default_factory=list, max_length=10)


class CobaltBody(BaseModel):
    platform: str
    on: bool


class PlatformBody(BaseModel):
    platform: str


@router.get("/platform-health")
async def platform_health_overview(_=Depends(verify_admin)):
    import asyncio
    from app.core import platform_health as ph
    from app.core import platform_probe as pp
    from app.core import ytdlp_channel as yc

    counts = ph.recent_counts()
    probes = pp.get_probe_states()
    rows = []
    for p in sorted(set(pp.PROBE_PLATFORMS) | set(k for k in counts if k not in ("other", "unknown"))):
        c = counts.get(p) or {}
        rows.append({
            "platform": p,
            "probe": probes.get(p, {}),
            "traffic_2h": c,
            "verdict": ph.verdict(c) if c else None,
            "degraded": ph.degraded().get(p),
            "prefer_cobalt": ph.prefer_cobalt(p),
            "cobalt_capable": p in ph.COBALT_ROUTE_PLATFORMS,
        })
    # status() hits PyPI and spawns one subprocess: off the event loop.
    channel = await asyncio.to_thread(yc.status)
    return {
        "platforms": rows,
        "ytdlp": channel,
        "thresholds": {
            "min_attempts": ph.MIN_ATTEMPTS, "system_warn": ph.SYSTEM_WARN,
            "system_critical": ph.SYSTEM_CRITICAL, "broken_min": ph.BROKEN_MIN,
            "broken_rate": ph.BROKEN_RATE,
        },
    }


@router.post("/ytdlp/remediate")
async def ytdlp_remediate_now(body: RemediateBody, request: Request, _=Depends(verify_admin)):
    """Try a newer yt-dlp now (bypasses the per-platform 6 h cooldown).
    Empty `platforms` = the platforms whose probe is failing right now."""
    from app.core import platform_probe as pp
    from app.tasks.video_tasks import ytdlp_remediate
    plats = [p for p in body.platforms if p in pp.PROBE_PLATFORMS]
    if not plats:
        plats = [p for p, s in pp.get_probe_states().items() if s.get("status") == pp.FAILED]
    if not plats:
        raise HTTPException(status_code=422, detail="Không có nền tảng nào đang lỗi để khắc phục")
    ytdlp_remediate.apply_async(args=[plats, "admin"], queue="light")
    log_admin_action(request, "admin.ytdlp.remediate", resource_type="ytdlp", resource_id=",".join(plats))
    return {"queued": True, "platforms": plats}


@router.post("/ytdlp/daily-check")
async def ytdlp_daily_check_now(request: Request, _=Depends(verify_admin)):
    from app.tasks.video_tasks import ytdlp_auto_update
    ytdlp_auto_update.apply_async(queue="light")
    log_admin_action(request, "admin.ytdlp.daily_check", resource_type="ytdlp", resource_id="stable")
    return {"queued": True}


@router.post("/ytdlp/rollback")
async def ytdlp_rollback_now(request: Request, _=Depends(verify_admin)):
    """Back to the previous yt-dlp (or the image copy). Runs in the API
    process: it only swaps a directory and signals the services."""
    import asyncio
    from app.core import ytdlp_channel as yc
    entry = await asyncio.to_thread(yc.rollback, "admin")
    log_admin_action(request, "admin.ytdlp.rollback", resource_type="ytdlp",
                     resource_id=str(entry.get("to")))
    return entry


@router.post("/platform-health/cobalt")
async def set_cobalt_first(body: CobaltBody, request: Request, _=Depends(verify_admin)):
    from app.core import platform_health as ph
    if body.platform not in ph.COBALT_ROUTE_PLATFORMS:
        raise HTTPException(status_code=422, detail="Chỉ Facebook, Instagram, X tải được qua Cobalt")
    ph.set_prefer_cobalt(body.platform, body.on, "admin")
    log_admin_action(request, f"admin.health.cobalt_{'on' if body.on else 'off'}",
                     resource_type="platform", resource_id=body.platform)
    return {"platform": body.platform, "prefer_cobalt": ph.prefer_cobalt(body.platform)}


@router.post("/platform-health/recovered")
async def mark_platform_recovered(body: PlatformBody, request: Request, _=Depends(verify_admin)):
    """Clear a platform's degraded mark (and its Cobalt override) by hand."""
    from app.core import platform_health as ph
    changed = ph.mark_recovered(body.platform)
    log_admin_action(request, "admin.health.recovered", resource_type="platform", resource_id=body.platform)
    return {"platform": body.platform, "was_degraded": changed}


# ── Capacity (concurrent downloads) ──────────────────────────────────────────

class CapacitySettingsBody(BaseModel):
    # None = back to the env value (MAX_CONCURRENT_DOWNLOADS)
    max_concurrent_downloads: int | None = Field(None, ge=1, le=50)


@router.get("/capacity")
async def capacity_overview(range: str = "24h", _=Depends(verify_admin)):  # noqa: A002
    from app.core import capacity
    rng = "7d" if range == "7d" else "24h"
    return {
        **capacity.series(rng),
        "latency": capacity.latency_by_platform(7 if rng == "7d" else 1),
        "setting": {
            "max_concurrent_downloads": capacity.get_max_concurrent(),
            "source": capacity.setting_source(),
            "env_default": capacity.env_default(),
            "min": capacity.HARD_MIN, "max": capacity.HARD_MAX,
        },
        "resources": {"cpu_cores": capacity.cpu_cores(), "memory_pct_now": capacity.memory_pct()},
    }


@router.post("/capacity/settings")
async def capacity_settings(body: CapacitySettingsBody, request: Request, _=Depends(verify_admin)):
    from app.core import capacity
    try:
        value = capacity.set_max_concurrent(body.max_concurrent_downloads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_admin_action(request, "admin.capacity.max_concurrent", resource_type="setting",
                     resource_id=str(value))
    return {"max_concurrent_downloads": value, "source": capacity.setting_source()}
