"""
Admin — China Platform Access (Phase 32B-1, wave 1)
====================================================
All routes require verify_admin (Bearer session or X-Admin-Token), mounted
under /api/v1/admin. Every POST is audited via log_admin_action with prior
value, new value and reason (single admin identity — owner correction #7).

  GET  /admin/china-platforms                        overview: flags, modes, kill switches, order
  GET  /admin/china-platforms/costs                  today/month spend + calls vs ceilings
  GET  /admin/china-platforms/{platform}             detail: provider health, recent scrubbed attempts
  GET  /admin/china-platforms/{platform}/benchmark   last benchmark summary
  POST /admin/china-platforms/{platform}/mode        {"mode","reason"} — may only go DOWN from the env ceiling
  POST /admin/china-platforms/{platform}/kill-switch {"on","reason"}; platform "global" = master kill switch
  POST /admin/china-platforms/{platform}/benchmark/run {"include_managed","max_urls","reason"}
  POST /admin/china-platforms/{platform}/cache/reset {"url_hash","reason"}

Responses never contain tokens, cookies or signed media URLs: attempts carry
the canonical-URL hash only, and every free-text field passes errors.redact.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.admin import verify_admin
from app.core.audit import log_admin_action
from app.services.china_platforms import (
    budget_guard,
    provider_health,
    registry,
    request_cache,
    settings,
)
from app.services.china_platforms.benchmark_runner import (
    LAST_SUMMARY_KEY,
    LOCK_KEY,
    load_fixtures,
    run_benchmark,
)
from app.services.china_platforms.errors import redact
from app.services.china_platforms.policy import CHINA_PLATFORM_POLICIES
from app.services.china_platforms.provider_router import recent_attempts

logger = logging.getLogger(__name__)

router = APIRouter()

_PROVIDER_BUDGET_CLASSES = ["apify"]


def _r():
    from app.core.redis_client import get_redis  # noqa: PLC0415
    return get_redis()


def _platform_or_404(platform: str):
    policy = registry.get_policy(platform)
    if policy is None:
        raise HTTPException(status_code=404, detail="unknown China platform")
    return policy


def _killswitch(platform: str) -> Any:
    try:
        return budget_guard.platform_killswitch_on(platform)
    except Exception:  # noqa: BLE001
        return None


def _mode_view(platform: str) -> dict:
    try:
        override = registry.mode_override(platform)
    except Exception:  # noqa: BLE001
        override = "unreadable"
    return {"env": settings.env_managed_mode(platform), "override": override,
            "effective": registry.effective_managed_mode(platform)}


def _overview_row(platform: str) -> dict:
    policy = registry.get_policy(platform)
    return {
        **policy.to_public_dict(),
        "env_enabled": settings.platform_env_enabled(platform),
        "killswitch": _killswitch(platform),
        "managed_mode": _mode_view(platform),
        "effective_provider_order": registry.effective_order(policy, "single_media"),
    }


def _pct(vals: list[int], p: float):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))]


def _scrub_attempt(a: dict) -> dict:
    return {k: (redact(v) if isinstance(v, str) else v) for k, v in a.items()}


@router.get("/china-platforms")
async def china_overview(_=Depends(verify_admin)) -> dict:
    try:
        global_kill = budget_guard.global_killswitch_on()
    except Exception:  # noqa: BLE001
        global_kill = None
    return {
        "master_enabled": settings.master_enabled(),
        "global_killswitch": global_kill,
        "apify_configured": bool(settings.apify_token()),
        "platforms": [_overview_row(p) for p in CHINA_PLATFORM_POLICIES],
    }


@router.get("/china-platforms/costs")
async def china_costs(_=Depends(verify_admin)) -> dict:
    snap = budget_guard.snapshot(list(CHINA_PLATFORM_POLICIES), _PROVIDER_BUDGET_CLASSES)
    snap["estimated_cost_per_call_usd"] = {"apify_douyin": settings.apify_douyin_est_cost_usd()}
    snap["proxy_bytes"] = 0   # no metadata-proxy provider in wave 1
    return snap


@router.get("/china-platforms/{platform}")
async def china_platform_detail(platform: str, _=Depends(verify_admin)) -> dict:
    policy = _platform_or_404(platform)
    adapter = registry.get_adapter(platform)
    providers = adapter.build_providers() if adapter else {}
    attempts = recent_attempts(platform)
    per: dict[str, dict] = {}
    for a in attempts:
        if a.get("outcome") == "skipped":
            continue
        s = per.setdefault(a.get("provider"), {"n": 0, "ok": 0, "lat": [], "last_failure_category": None})
        s["n"] += 1
        s["lat"].append(int(a.get("latency_ms") or 0))
        if a.get("outcome") == "success":
            s["ok"] += 1
        elif s["last_failure_category"] is None:
            s["last_failure_category"] = a.get("normalized_failure_category")
    prov_rows = []
    for name in sorted(set(policy.allowed_providers) | set(providers)):
        p = providers.get(name)
        s = per.get(name, {"n": 0, "ok": 0, "lat": [], "last_failure_category": None})
        prov_rows.append({
            "name": name,
            "mode": getattr(p, "mode", None),
            "paid": getattr(p, "paid", None),
            "configured": p.is_configured() if p is not None else False,
            "health": provider_health.snapshot(name, platform).model_dump(),
            "recent_attempts": s["n"],
            "success_rate": round(s["ok"] / s["n"], 4) if s["n"] else None,
            "p50_latency_ms": _pct(s["lat"], 0.5),
            "p95_latency_ms": _pct(s["lat"], 0.95),
            "last_failure_category": s["last_failure_category"],
        })
    return {**_overview_row(platform), "providers": prov_rows,
            "recent_attempts": [_scrub_attempt(a) for a in attempts[:50]]}


@router.get("/china-platforms/{platform}/benchmark")
async def china_benchmark_last(platform: str, _=Depends(verify_admin)) -> dict:
    _platform_or_404(platform)
    try:
        raw = _r().get(LAST_SUMMARY_KEY.format(platform=platform))
        running = bool(_r().get(LOCK_KEY.format(platform=platform)))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    return {"platform": platform, "running": running,
            "last": json.loads(raw) if raw else None,
            "fixture_count_configured": len(load_fixtures(platform))}


class ModeBody(BaseModel):
    mode: str
    reason: str = Field(min_length=3, max_length=500)


class KillBody(BaseModel):
    on: bool
    reason: str = Field(min_length=3, max_length=500)


class BenchBody(BaseModel):
    include_managed: bool = False
    max_urls: int = Field(default=30, ge=1, le=100)
    reason: str = Field(min_length=3, max_length=500)


class CacheResetBody(BaseModel):
    url_hash: str = Field(min_length=8, max_length=64, pattern=r"^[0-9a-f]+$")
    reason: str = Field(min_length=3, max_length=500)


@router.post("/china-platforms/{platform}/mode")
async def china_set_mode(platform: str, body: ModeBody, request: Request, _=Depends(verify_admin)) -> dict:
    policy = _platform_or_404(platform)
    if body.mode not in settings.MANAGED_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {list(settings.MANAGED_MODES)}")
    env_mode = settings.env_managed_mode(platform)
    if not policy.managed_provider_allowed and body.mode != "off":
        raise HTTPException(status_code=409, detail="managed provider not allowed for this platform")
    if registry.rank(body.mode) > registry.rank(env_mode):
        # Runtime override may only reduce (plan §7.4); expanding = env change.
        raise HTTPException(status_code=409, detail=(
            f"mode '{body.mode}' exceeds the deployment ceiling '{env_mode}' "
            f"(CHINA_ACCESS_{platform.upper()}_MANAGED_MODE)"))
    prior = _mode_view(platform)
    try:
        _r().set(registry.MODE_OVERRIDE_KEY.format(platform=platform), body.mode)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    new = _mode_view(platform)
    log_admin_action(request, "admin.china_access.mode", resource_type="china_platform", resource_id=platform,
                     metadata={"prior": prior, "new": new, "reason": body.reason})
    return {"platform": platform, "prior": prior, "new": new}


@router.post("/china-platforms/{platform}/kill-switch")
async def china_kill_switch(platform: str, body: KillBody, request: Request, _=Depends(verify_admin)) -> dict:
    if platform != "global":
        _platform_or_404(platform)
    try:
        prior = budget_guard.global_killswitch_on() if platform == "global" else budget_guard.platform_killswitch_on(platform)
        budget_guard.set_killswitch(platform, body.on)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    log_admin_action(request, "admin.china_access.killswitch", resource_type="china_platform", resource_id=platform,
                     metadata={"prior": prior, "new": body.on, "reason": body.reason})
    return {"scope": platform, "prior": prior, "new": body.on}


@router.post("/china-platforms/{platform}/benchmark/run")
async def china_benchmark_run(platform: str, body: BenchBody, request: Request,
                              background: BackgroundTasks, _=Depends(verify_admin)) -> dict:
    _platform_or_404(platform)
    try:
        if not _r().set(LOCK_KEY.format(platform=platform), "1", nx=True, ex=7200):
            raise HTTPException(status_code=409, detail="a benchmark is already running for this platform")
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")

    def _job() -> None:
        try:
            asyncio.run(run_benchmark(platform, include_managed=body.include_managed, max_urls=body.max_urls))
        except Exception as exc:  # noqa: BLE001
            logger.error("china benchmark failed: %s", redact(f"{type(exc).__name__}: {exc}"))
        finally:
            try:
                _r().delete(LOCK_KEY.format(platform=platform))
            except Exception:  # noqa: BLE001
                pass

    background.add_task(_job)
    log_admin_action(request, "admin.china_access.benchmark_run", resource_type="china_platform", resource_id=platform,
                     metadata={"include_managed": body.include_managed, "max_urls": body.max_urls,
                               "reason": body.reason})
    return {"platform": platform, "started": True, "include_managed": body.include_managed}


@router.post("/china-platforms/{platform}/cache/reset")
async def china_cache_reset(platform: str, body: CacheResetBody, request: Request, _=Depends(verify_admin)) -> dict:
    _platform_or_404(platform)
    try:
        deleted = request_cache.delete_cached(platform, "single_media", body.url_hash)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    log_admin_action(request, "admin.china_access.cache_reset", resource_type="china_platform", resource_id=platform,
                     metadata={"url_hash": body.url_hash, "deleted": deleted, "reason": body.reason})
    return {"platform": platform, "deleted": deleted}
