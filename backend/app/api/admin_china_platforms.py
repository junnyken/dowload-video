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

Phase 32B-2 Stage A (Douyin):
  GET  /admin/china-platforms/{platform}/benchmark/report   last run recomputed with current watermark reviews
  POST /admin/china-platforms/{platform}/watermark-review   {"provider","url_hash","state","note"} one benchmark item
  GET  /admin/china-platforms/{platform}/watermark          per-route review aggregate + gate

Apify token managed from the admin panel (secret_store; admin-stored > env > none):
  GET    /admin/china-platforms/apify/token        status: configured, source, last4, account, usage. NEVER the token
  POST   /admin/china-platforms/apify/token        {"token","reason"}: validated with a free Apify call, then stored
  DELETE /admin/china-platforms/apify/token        {"reason"}: env CHINA_ACCESS_APIFY_TOKEN (if set) applies again
  POST   /admin/china-platforms/apify/token/test   re-validate the current token (free call, no actor run)
  (Since task #6036 these four are back-compat wrappers: that token is the
  pool's "legacy slot" entry, mirrored by apify_pool.sync().)

Apify token pool (task #6036; rotation per paid call, see apify_pool.py):
  GET    /admin/china-platforms/apify/pool                 entries (never tokens) + pool summary
  POST   /admin/china-platforms/apify/pool                 {"token","label","priority"?,"monthly_ceiling_usd"?,"reason"}
                                                           validated with /users/me first; body parsed by hand
  POST   /admin/china-platforms/apify/pool/{id}            {"label"?,"priority"?,"monthly_ceiling_usd"?,"enabled"?,"reason"}
  DELETE /admin/china-platforms/apify/pool/{id}            {"reason"}
  POST   /admin/china-platforms/apify/pool/{id}/refresh    free: /users/me + /users/me/limits
  POST   /admin/china-platforms/apify/pool/refresh         same for every entry
  GET    /admin/china-platforms/apify/metrics              per platform today/7d/month + per entry + totals

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
    apify_pool,
    budget_guard,
    cost_metrics,
    provider_health,
    registry,
    request_cache,
    rollout_guard,
    secret_store,
    settings,
    watermark,
)
from app.services.china_platforms.benchmark_runner import (
    LAST_SUMMARY_KEY,
    LOCK_KEY,
    load_fixtures,
    report_from_last,
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
        "apify_configured": settings.apify_configured(),
        "apify_token_source": secret_store.resolve_apify_token()[1],
        "platforms": [_overview_row(p) for p in CHINA_PLATFORM_POLICIES],
    }


@router.get("/china-platforms/costs")
async def china_costs(_=Depends(verify_admin)) -> dict:
    snap = budget_guard.snapshot(list(CHINA_PLATFORM_POLICIES), _PROVIDER_BUDGET_CLASSES)
    snap["estimated_cost_per_call_usd"] = {
        "apify_douyin": settings.apify_douyin_est_cost_usd(),
        "apify_kuaishou": settings.apify_kuaishou_est_cost_usd(),
        "apify_xiaohongshu": settings.apify_xiaohongshu_est_cost_usd(),
    }
    snap["proxy_bytes"] = 0   # no metadata-proxy provider in wave 1
    snap["reconcile_today"] = {b: budget_guard.reconcile_snapshot(b) for b in _PROVIDER_BUDGET_CLASSES}
    return snap


# ── Apify token (admin-managed). Registered before /{platform} routes. ─────

class ReasonBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _apify_validator():
    """Seam for tests: returns secret_store.validate (free Apify calls only)."""
    return secret_store.validate


def _client_ip(request: Request):
    try:
        from app.core.client_ip import get_client_ip  # noqa: PLC0415
        return get_client_ip(request)
    except Exception:  # noqa: BLE001
        return None


@router.get("/china-platforms/apify/token")
async def china_apify_token_status(_=Depends(verify_admin)) -> dict:
    out = secret_store.status()
    out["deprecated"] = "use /admin/china-platforms/apify/pool"
    try:
        out["pool"] = apify_pool.summary()
    except Exception:  # noqa: BLE001
        out["pool"] = None
    return out


@router.post("/china-platforms/apify/token")
async def china_apify_token_set(request: Request, _=Depends(verify_admin)) -> dict:
    # Body parsed by hand: FastAPI's 422 response echoes the submitted input,
    # which here would be the token.
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"error": "bad_body", "message": "Cần JSON {token, reason}."})
    reason = str(payload.get("reason") or "").strip()
    if not (3 <= len(reason) <= 500):
        raise HTTPException(status_code=400, detail={"error": "reason_required",
                                                     "message": "Cần nhập lý do (3–500 ký tự)."})
    raw_token = payload.get("token")
    safe_reason = redact(reason)
    if isinstance(raw_token, str) and len(raw_token.strip()) >= 6:
        safe_reason = safe_reason.replace(raw_token.strip(), "<redacted>")
    try:
        token = secret_store.sanitize(raw_token if isinstance(raw_token, str) else "")
        info = await _apify_validator()(token)
    except secret_store.TokenRejected as exc:
        log_admin_action(request, "admin.china_access.apify_token_rejected", resource_type="china_secret",
                         resource_id="apify_token", metadata={"code": exc.code, "reason": safe_reason})
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "message": exc.message})
    prior = secret_store.status()
    try:
        secret_store.store(token, info, now_iso=budget_guard.utcnow().isoformat(), ip=_client_ip(request))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    new = secret_store.status()
    log_admin_action(request, "admin.china_access.apify_token_set", resource_type="china_secret",
                     resource_id="apify_token",
                     metadata={"prior": {"source": prior["source"], "last4": prior["last4"]},
                               "new": {"source": new["source"], "last4": new["last4"]},
                               "account": (new.get("account") or {}).get("username"),
                               "reason": safe_reason})
    return new


@router.delete("/china-platforms/apify/token")
async def china_apify_token_delete(body: ReasonBody, request: Request, _=Depends(verify_admin)) -> dict:
    prior = secret_store.status()
    try:
        removed = secret_store.delete()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    new = secret_store.status()
    log_admin_action(request, "admin.china_access.apify_token_deleted", resource_type="china_secret",
                     resource_id="apify_token",
                     metadata={"prior": {"source": prior["source"], "last4": prior["last4"]},
                               "new": {"source": new["source"], "last4": new["last4"]},
                               "removed": removed, "reason": redact(body.reason)})
    note = ("Token trong admin đã xoá. Biến môi trường CHINA_ACCESS_APIFY_TOKEN vẫn được dùng."
            if new["source"] == "env" else "Token đã xoá. Hiện không có token Apify nào được cấu hình.")
    return {**new, "removed": removed, "note": note}


@router.post("/china-platforms/apify/token/test")
async def china_apify_token_test(request: Request, _=Depends(verify_admin)) -> dict:
    token, source = secret_store.resolve_apify_token()
    if not token:
        raise HTTPException(status_code=404, detail={"error": "not_configured", "message": "Chưa có token Apify."})
    try:
        info = await _apify_validator()(token)
    except secret_store.TokenRejected as exc:
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "message": exc.message})
    info = {**info, "last4": secret_store.last4(token)}
    secret_store.record_validation(info, now_iso=budget_guard.utcnow().isoformat(), source=source)
    _pool_record_legacy_refresh(source, info)
    log_admin_action(request, "admin.china_access.apify_token_tested", resource_type="china_secret",
                     resource_id="apify_token", metadata={"source": source, "last4": secret_store.last4(token)})
    return {**secret_store.status(), "valid": True}


def _pool_record_legacy_refresh(source: str, info: dict) -> None:
    """The old /apify/token/test also refreshes the matching pool entry."""
    try:
        for v in apify_pool.list_views():
            if (source == "admin" and v.get("legacy_slot")) or (source == "env" and v["id"] == apify_pool.ENV_ID):
                apify_pool.apply_refresh(v["id"], info)
                return
    except Exception as exc:  # noqa: BLE001
        logger.warning("apify pool legacy refresh failed: %s", type(exc).__name__)


# ── Apify token pool (task #6036) ───────────────────────────────────────────

def _pool_503():
    return HTTPException(status_code=503, detail={"error": "redis_unavailable",
                                                  "message": "Không đọc được Redis. Thử lại sau."})


def _pool_error(exc: "apify_pool.PoolError"):
    return HTTPException(status_code=exc.status, detail={"error": exc.code, "message": exc.message})


def _pool_payload() -> dict:
    views = apify_pool.list_views()
    return {"entries": views, "summary": apify_pool.summary(views=views),
            "env_fallback_configured": bool(secret_store.env_token())}


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"error": "bad_body", "message": "Cần JSON hợp lệ."})
    return payload


def _reason_of(payload: dict, secret: str = "") -> str:
    reason = str(payload.get("reason") or "").strip()
    if not (3 <= len(reason) <= 500):
        raise HTTPException(status_code=400, detail={"error": "reason_required",
                                                     "message": "Cần nhập lý do (3–500 ký tự)."})
    safe = redact(reason)
    if secret and len(secret.strip()) >= 6:
        safe = safe.replace(secret.strip(), "<redacted>")
    return safe


def _clean_label(raw) -> str:
    label = " ".join(str(raw or "").split())[:60]
    if len(label) < 1:
        raise HTTPException(status_code=400, detail={"error": "label_required",
                                                     "message": "Cần đặt tên cho token (ví dụ: \"Tổ chức A\")."})
    return redact(label)


def _clean_priority(raw):
    if raw is None or raw == "":
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={"error": "bad_priority",
                                                     "message": "Thứ tự ưu tiên phải là số nguyên 0–1000."})
    if not 0 <= v <= 1000:
        raise HTTPException(status_code=400, detail={"error": "bad_priority",
                                                     "message": "Thứ tự ưu tiên phải là số nguyên 0–1000."})
    return v


def _clean_ceiling(raw):
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = -1
    if not 0 < v <= 10000:
        raise HTTPException(status_code=400, detail={"error": "bad_ceiling",
                                                     "message": "Trần chi tiêu tháng phải là số USD lớn hơn 0."})
    return round(v, 2)


def _audit_entry(v: dict) -> dict:
    return {"id": v.get("id"), "label": v.get("label"), "last4": v.get("last4")}


@router.get("/china-platforms/apify/pool")
async def china_apify_pool_list(_=Depends(verify_admin)) -> dict:
    try:
        return _pool_payload()
    except Exception:  # noqa: BLE001
        raise _pool_503()


@router.post("/china-platforms/apify/pool")
async def china_apify_pool_add(request: Request, _=Depends(verify_admin)) -> dict:
    # Parsed by hand: a FastAPI 422 would echo the submitted token.
    payload = await _json_body(request)
    raw_token = payload.get("token") if isinstance(payload.get("token"), str) else ""
    reason = _reason_of(payload, raw_token)
    label = _clean_label(payload.get("label"))
    priority = _clean_priority(payload.get("priority"))
    ceiling = _clean_ceiling(payload.get("monthly_ceiling_usd"))
    try:
        token = secret_store.sanitize(raw_token)
        info = await _apify_validator()(token)
    except secret_store.TokenRejected as exc:
        log_admin_action(request, "admin.china_access.apify_pool_rejected", resource_type="china_secret",
                         resource_id="apify_pool", metadata={"code": exc.code, "label": label, "reason": reason})
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "message": exc.message})
    try:
        view = apify_pool.add(token, info, label=label, priority=priority, ceiling=ceiling,
                              ip=_client_ip(request))
    except apify_pool.PoolError as exc:
        raise _pool_error(exc)
    except Exception:  # noqa: BLE001
        raise _pool_503()
    log_admin_action(request, "admin.china_access.apify_pool_added", resource_type="china_secret",
                     resource_id=f"apify_pool:{view['id']}",
                     metadata={"new": _audit_entry(view), "account": (view.get("account") or {}).get("username"),
                               "priority": view.get("priority"), "monthly_ceiling_usd": ceiling, "reason": reason})
    return {"entry": view, **_pool_payload()}


@router.post("/china-platforms/apify/pool/refresh")
async def china_apify_pool_refresh_all(request: Request, _=Depends(verify_admin)) -> dict:
    try:
        ids = [v["id"] for v in apify_pool.list_views()]
    except Exception:  # noqa: BLE001
        raise _pool_503()
    results = {}
    for eid in ids:
        results[eid] = await _refresh_one(eid)
    log_admin_action(request, "admin.china_access.apify_pool_refreshed", resource_type="china_secret",
                     resource_id="apify_pool", metadata={"entries": results})
    return {"results": results, **_pool_payload()}


async def _refresh_one(entry_id: str) -> str:
    """'ok' | 'invalid_token' | 'apify_unreachable'. Free Apify calls only."""
    token, _meta = apify_pool.token_of(entry_id)
    if not token:
        return "not_configured"
    try:
        info = await _apify_validator()(token)
    except secret_store.TokenRejected as exc:
        apify_pool.apply_refresh(entry_id, None, rejected_code=exc.code)
        return exc.code
    apify_pool.apply_refresh(entry_id, info)
    return "ok"


@router.post("/china-platforms/apify/pool/{entry_id}/refresh")
async def china_apify_pool_refresh(entry_id: str, request: Request, _=Depends(verify_admin)) -> dict:
    try:
        outcome = await _refresh_one(entry_id)
        view = apify_pool.get_view(entry_id)
    except apify_pool.PoolError as exc:
        raise _pool_error(exc)
    log_admin_action(request, "admin.china_access.apify_pool_refreshed", resource_type="china_secret",
                     resource_id=f"apify_pool:{entry_id}", metadata={**_audit_entry(view), "outcome": outcome})
    return {"outcome": outcome, "entry": view}


@router.post("/china-platforms/apify/pool/{entry_id}")
async def china_apify_pool_update(entry_id: str, request: Request, _=Depends(verify_admin)) -> dict:
    payload = await _json_body(request)
    if "token" in payload:
        raise HTTPException(status_code=400, detail={
            "error": "token_not_editable",
            "message": "Không sửa được token. Thêm token mới rồi xoá token cũ."})
    reason = _reason_of(payload)
    fields: dict = {}
    if "label" in payload:
        fields["label"] = _clean_label(payload.get("label"))
    if "priority" in payload:
        p = _clean_priority(payload.get("priority"))
        fields["priority"] = apify_pool.DEFAULT_PRIORITY if p is None else p
    if "monthly_ceiling_usd" in payload:
        fields["monthly_ceiling_usd"] = _clean_ceiling(payload.get("monthly_ceiling_usd"))
    if "enabled" in payload:
        if not isinstance(payload.get("enabled"), bool):
            raise HTTPException(status_code=400, detail={"error": "bad_enabled", "message": "Giá trị bật/tắt không hợp lệ."})
        fields["enabled"] = payload["enabled"]
    if not fields:
        raise HTTPException(status_code=400, detail={"error": "nothing_to_update", "message": "Không có gì để đổi."})
    try:
        prior, new = apify_pool.update(entry_id, fields)
    except apify_pool.PoolError as exc:
        raise _pool_error(exc)
    except Exception:  # noqa: BLE001
        raise _pool_503()
    keys = ("label", "priority", "monthly_ceiling_usd", "enabled")
    log_admin_action(request, "admin.china_access.apify_pool_updated", resource_type="china_secret",
                     resource_id=f"apify_pool:{entry_id}",
                     metadata={"entry": _audit_entry(new), "prior": {k: prior.get(k) for k in keys},
                               "new": {k: new.get(k) for k in keys}, "reason": reason})
    return {"entry": new, **_pool_payload()}


@router.delete("/china-platforms/apify/pool/{entry_id}")
async def china_apify_pool_delete(entry_id: str, request: Request, _=Depends(verify_admin)) -> dict:
    payload = await _json_body(request)
    reason = _reason_of(payload)
    try:
        view = apify_pool.delete(entry_id)
    except apify_pool.PoolError as exc:
        raise _pool_error(exc)
    except Exception:  # noqa: BLE001
        raise _pool_503()
    log_admin_action(request, "admin.china_access.apify_pool_deleted", resource_type="china_secret",
                     resource_id=f"apify_pool:{entry_id}",
                     metadata={"prior": _audit_entry(view), "reason": reason})
    return {"removed": _audit_entry(view), **_pool_payload()}


@router.get("/china-platforms/apify/metrics")
async def china_apify_metrics(_=Depends(verify_admin)) -> dict:
    try:
        views = apify_pool.list_views()
        report = cost_metrics.platform_report()
    except Exception:  # noqa: BLE001
        raise _pool_503()
    snap = budget_guard.snapshot(list(cost_metrics.PLATFORMS), _PROVIDER_BUDGET_CLASSES)
    return {
        "day_utc": budget_guard.day_key(),
        "month_utc": budget_guard.month_key(),
        **report,
        "entries": views,
        "pool": apify_pool.summary(views=views),
        "ceilings": snap,
        "estimated_cost_per_call_usd": {p: cost_metrics.est_micros_for(f"apify_{p}") / 1e6
                                        for p in cost_metrics.PLATFORMS},
        "cache": {"ttl_sec": settings.cache_ttl_sec(), "extended_ttl_sec": settings.cache_extended_ttl_sec(),
                  "expiry_margin_sec": settings.cache_expiry_margin_sec()},
        "notes": {
            "spend": "recorded by us per call, never below the estimate (Apify's usageTotalUsd leaves out "
                     "pay-per-result charges)",
            "saved": "estimate: cache hits on a paid result x that provider's estimated cost per call",
            "apify_usage": "Apify's monthlyUsageUsd at the last refresh + our spend recorded since",
        },
    }


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
            "auto_rollback": rollout_guard.window_snapshot(platform),
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


@router.get("/china-platforms/{platform}/benchmark/report")
async def china_benchmark_report(platform: str, _=Depends(verify_admin)) -> dict:
    _platform_or_404(platform)
    try:
        rep = report_from_last(platform)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    if rep is None:
        raise HTTPException(status_code=404, detail="no benchmark run recorded")
    return rep


@router.get("/china-platforms/{platform}/watermark")
async def china_watermark(platform: str, _=Depends(verify_admin)) -> dict:
    policy = _platform_or_404(platform)
    return {"platform": platform,
            "routes": [watermark.aggregate(platform, p) for p in sorted(policy.allowed_providers)],
            "ui_wording_changed": False}


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


class WatermarkReviewBody(BaseModel):
    provider: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    url_hash: str = Field(min_length=8, max_length=64, pattern=r"^[0-9a-f]+$")
    state: str
    note: str = Field(min_length=3, max_length=500)


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


@router.post("/china-platforms/{platform}/watermark-review")
async def china_watermark_review(platform: str, body: WatermarkReviewBody, request: Request,
                                 _=Depends(verify_admin)) -> dict:
    """Record a manual review of ONE benchmark item (provider route + canonical
    URL hash). Only items a benchmark run resolved successfully are accepted."""
    policy = _platform_or_404(platform)
    if body.state not in watermark.REVIEW_STATES:
        raise HTTPException(status_code=400, detail=f"state must be one of {list(watermark.REVIEW_STATES)}")
    if body.provider not in policy.allowed_providers:
        raise HTTPException(status_code=404, detail="unknown provider route for this platform")
    try:
        if not watermark.is_benchmark_item(platform, body.provider, body.url_hash):
            raise HTTPException(status_code=404, detail="not a successful benchmark item for this route")
        prior = watermark.record_review(platform, body.provider, body.url_hash, body.state, body.note,
                                        budget_guard.utcnow().isoformat())
        agg = watermark.aggregate(platform, body.provider)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    log_admin_action(request, "admin.china_access.watermark_review", resource_type="china_platform",
                     resource_id=platform,
                     metadata={"provider": body.provider, "url_hash": body.url_hash,
                               "prior": (prior or {}).get("state"), "new": body.state,
                               "reason": redact(body.note)[:500]})
    return {"platform": platform, "provider": body.provider, "url_hash": body.url_hash,
            "state": body.state, "prior": (prior or {}).get("state"), "route": agg}
