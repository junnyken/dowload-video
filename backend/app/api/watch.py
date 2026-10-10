"""
Channel Watch API ("Theo dõi kênh") — Phase 33A, task #6257.

User routes (prefix /api/v1/watch), signed-in accounts only:
  GET    /status                    {enabled, limits, used} — the frontend shows
                                    the page only when enabled is true
  POST   /sources                   {url, mode?} → subscription (+ baseline scan)
  GET    /subscriptions
  PATCH  /subscriptions/{id}        {status: active|paused, mode: notify_only|one_tap}
  DELETE /subscriptions/{id}        soft remove (last watcher → source idle)
  GET    /items?since=ISO           recent new items of own subscriptions

Admin routes (prefix /api/v1/admin, verify_admin):
  GET    /watch/overview
  POST   /watch/sources/{id}/pause | /resume   (audit-logged)

Errors are JSON {error_code, message, detail} (WatchError handler in main.py).
With WATCH_ENABLED=false every user route answers 404 watch_disabled.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.admin import verify_admin
from app.core import watch_config as cfg
from app.core.auth_middleware import get_optional_user
from app.services import channel_watch as cw
from app.services.channel_watch import WatchError

logger = logging.getLogger(__name__)

router = APIRouter()
admin_router = APIRouter()


async def watch_error_handler(_request: Request, exc: WatchError):
    return JSONResponse(status_code=exc.status, content=exc.body())


# ── Guards ───────────────────────────────────────────────────────────

def _require_enabled() -> None:
    if not cfg.enabled():
        raise WatchError("watch_disabled", 404)


def _require_user(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    # WATCH_REQUIRE_ACCOUNT: 33A has no guest mode — a guest is refused even
    # if the flag is set to false (nothing could own the subscription).
    if not user or not user.get("id"):
        raise WatchError("login_required", 401)
    return user


def _is_admin(request: Request) -> bool:
    from app.core.quotas import is_admin_request
    return is_admin_request(request)


def _tier(user_id: str) -> str:
    from app.core.quotas import get_user_tier
    try:
        return get_user_tier(user_id) or "free"
    except Exception:
        return "free"


def _email_verified(user: Dict[str, Any]) -> Optional[bool]:
    """True/False when known, None when it cannot be determined.

    A Supabase session (JWT) carries email_confirmed_at (auth_middleware).
    API-key callers do not; for them the Supabase admin API is asked. If that
    fails the answer is None and the caller is NOT blocked (logged).
    TODO(33B): decide whether API-key callers should be refused instead."""
    if "email_confirmed_at" in user:
        return bool(user.get("email_confirmed_at"))
    try:
        from app.core.database import get_service_client
        res = get_service_client().auth.admin.get_user_by_id(user["id"])
        u = getattr(res, "user", None)
        if u is not None:
            return bool(getattr(u, "email_confirmed_at", None))
    except Exception as e:
        logger.info("[Watch] email verification unknown for %s: %s", user.get("id"), type(e).__name__)
    return None


def _client_ip(request: Request) -> str:
    from app.core.client_ip import get_client_ip
    try:
        return get_client_ip(request)
    except Exception:
        return ""


def _limits(tier: str) -> dict:
    return {
        "max_sources": cfg.max_sources(tier),
        "min_interval_sec": cfg.min_interval_sec(tier),
        "replace_cooldown_sec": cfg.replace_cooldown_sec(tier),
    }


# ── User routes ──────────────────────────────────────────────────────

@router.get("/watch/status")
def watch_status(request: Request, user=Depends(get_optional_user)):
    _require_enabled()
    user = _require_user(user)
    admin = _is_admin(request)
    tier = "pro" if admin else _tier(user["id"])
    can_add = (not cfg.kill_switch()) and (admin or not cfg.admin_only())
    sb = cw._sb()
    return {
        "enabled": can_add,
        "kill_switch": cfg.kill_switch(),
        "admin_only": cfg.admin_only(),
        "platforms": sorted(p for p in cfg.platforms_enabled() if p in cfg.IMPLEMENTED_PLATFORMS),
        "tier": tier,
        "limits": _limits(tier),
        "used": cw.user_used(sb, user["id"]),
    }


class AddSourceRequest(BaseModel):
    url: str
    mode: Optional[str] = None


@router.post("/watch/sources", status_code=201)
def add_watch_source(payload: AddSourceRequest, request: Request, user=Depends(get_optional_user)):
    _require_enabled()
    user = _require_user(user)
    if cfg.kill_switch():
        raise WatchError("watch_disabled", 503)
    admin = _is_admin(request)
    if cfg.admin_only() and not admin:
        raise WatchError("watch_disabled", 403, message_key="admin_only")
    tier = _tier(user["id"])
    eff = "pro" if admin else tier
    email_ok = None
    if not cfg.is_paid(eff) and cfg.free_requires_verified_email():
        email_ok = _email_verified(user)
    res = cw.add_source(
        user_id=user["id"], tier=tier, is_admin=admin, email_verified=email_ok,
        url=payload.url, mode=payload.mode or "one_tap", ip=_client_ip(request),
    )
    body = {
        "subscription": cw.subscription_view(res["subscription"], res["source"] or {}),
        "already_subscribed": res["already"],
        "baseline": res["baseline"],
    }
    return JSONResponse(status_code=200 if res["already"] else 201, content=body)


@router.get("/watch/subscriptions")
def get_watch_subscriptions(user=Depends(get_optional_user)):
    _require_enabled()
    user = _require_user(user)
    return {"subscriptions": cw.list_subscriptions(cw._sb(), user["id"])}


class PatchSubscriptionRequest(BaseModel):
    status: Optional[str] = None
    mode: Optional[str] = None


@router.patch("/watch/subscriptions/{sub_id}")
def patch_watch_subscription(sub_id: str, payload: PatchSubscriptionRequest, user=Depends(get_optional_user)):
    _require_enabled()
    user = _require_user(user)
    sb = cw._sb()
    sub = cw.update_subscription(sb, user["id"], sub_id, status=payload.status, mode=payload.mode)
    return {"subscription": cw.subscription_view(sub, cw.get_source(sb, sub["source_id"]) or {})}


@router.delete("/watch/subscriptions/{sub_id}")
def delete_watch_subscription(sub_id: str, user=Depends(get_optional_user)):
    _require_enabled()
    user = _require_user(user)
    cw.remove_subscription(cw._sb(), user["id"], sub_id)
    return {"removed": True}


@router.get("/watch/items")
def get_watch_items(since: Optional[str] = None, user=Depends(get_optional_user)):
    _require_enabled()
    user = _require_user(user)
    since_dt = None
    if since:
        since_dt = cw._parse(since)
        if since_dt is None:
            raise WatchError("invalid_since", 400)
        # Never more than 30 days back.
        since_dt = max(since_dt, datetime.now(timezone.utc) - timedelta(days=30))
    return {"items": cw.list_items(cw._sb(), user["id"], since=since_dt)}


# ── Admin ────────────────────────────────────────────────────────────

@admin_router.get("/watch/overview")
def watch_overview(_=Depends(verify_admin)):
    sb = cw._sb()
    now = datetime.now(timezone.utc)
    sources = (sb.table("watch_sources").select("id, platform, status, next_scan_at, subscriber_count")
               .execute()).data or []
    by: dict = {}
    due = 0
    for s in sources:
        key = f"{s.get('platform')}:{s.get('status')}"
        by[key] = by.get(key, 0) + 1
        nxt = cw._parse(s.get("next_scan_at"))
        if s.get("status") in cw.SCANNABLE and nxt and nxt <= now:
            due += 1
    subs = (sb.table("watch_subscriptions").select("status, tier").execute()).data or []
    sub_counts: dict = {}
    for s in subs:
        sub_counts[s.get("status")] = sub_counts.get(s.get("status"), 0) + 1
    runs = (sb.table("watch_scan_runs").select("*").order("created_at", desc=True).limit(20).execute()).data or []
    pending = cw._count(sb, "watch_deliveries", lambda q: q.eq("status", "pending"))
    return {
        "flags": {
            "enabled": cfg.enabled(), "kill_switch": cfg.kill_switch(), "admin_only": cfg.admin_only(),
            "platforms_enabled": sorted(cfg.platforms_enabled()),
        },
        "sources_by_platform_status": by,
        "sources_total": len(sources),
        "subscriptions_by_status": sub_counts,
        "free_sources": {"used": cw.free_sources_total(sb), "cap": cfg.global_free_cap()},
        "due_now": due,
        "deliveries_pending": pending,
        "last_scan_runs": runs,
    }


def _admin_set_source(request: Request, source_id: str, status: str, action: str) -> dict:
    from app.core.audit import log_admin_action
    sb = cw._sb()
    src = cw.get_source(sb, source_id)
    if not src:
        raise WatchError("not_found", 404)
    upd: dict = {"status": status}
    if status == "active":
        upd["next_scan_at"] = datetime.now(timezone.utc).isoformat()
    sb.table("watch_sources").update(upd).eq("id", source_id).execute()
    log_admin_action(request, action, resource_type="watch_source", resource_id=source_id,
                     metadata={"from": src.get("status"), "to": status,
                               "platform": src.get("platform"), "channel": src.get("external_channel_id")})
    return {"id": source_id, "status": status}


@admin_router.post("/watch/sources/{source_id}/pause")
def admin_pause_source(source_id: str, request: Request, _=Depends(verify_admin)):
    return _admin_set_source(request, source_id, "paused_admin", "admin.watch.source.pause")


@admin_router.post("/watch/sources/{source_id}/resume")
def admin_resume_source(source_id: str, request: Request, _=Depends(verify_admin)):
    return _admin_set_source(request, source_id, "active", "admin.watch.source.resume")
