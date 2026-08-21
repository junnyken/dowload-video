"""
Intelligence API — VidGrab Phase 12
=====================================
Endpoints exposing anomaly detection, queue health, adaptive fallback,
playbook management, auto-tuner, schedule/archive suggestions, smart defaults,
and a unified automation history feed.

Mount at /api/v1/intelligence in main.py.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


# ── Pydantic request bodies ────────────────────────────────────────────────────

class PlaybookExecuteRequest(BaseModel):
    action: str
    params: Dict[str, Any] = {}


class SmartDefaultsRequest(BaseModel):
    quality: str
    download_subs: bool
    remove_watermark: bool


class AcceptTagsRequest(BaseModel):
    tags: List[str]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _redis():
    from app.core.redis_client import get_redis
    return get_redis()


async def _require_admin(request: Request) -> None:
    """
    Lazily import and call verify_admin to avoid circular imports at module load.

    Two bugs lived here. verify_admin takes `request` as a required positional
    parameter — it needs the caller IP for the allowlist and the denial log — and
    it was never passed, so every admin endpoint in this module raised TypeError
    and answered 500 instead of 401/200. They had never worked.

    And `bearer` was hardcoded to None, which left only the legacy X-Admin-Token
    path. The admin UI authenticates with the Bearer session token from
    POST /admin/login and has no way to produce that legacy header, so even once
    the TypeError is gone these routes would still have rejected it. The
    Authorization header is now parsed into the same credentials object FastAPI
    would have built.
    """
    from fastapi.security import HTTPAuthorizationCredentials
    from app.api.admin import verify_admin

    bearer = None
    auth = request.headers.get("Authorization") or ""
    scheme, _, credentials = auth.partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        bearer = HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials.strip())

    await verify_admin(
        request=request,
        legacy_token=request.headers.get("X-Admin-Token"),
        bearer=bearer,
    )


async def _require_user(request: Request) -> Dict[str, Any]:
    """
    Resolve Bearer token to a user dict; raise 401 if unauthenticated.
    Lazily imports to avoid circular dependencies.
    """
    from fastapi.security import HTTPBearer
    from app.core.auth_middleware import get_optional_user

    _bearer = HTTPBearer(auto_error=False)
    cred = await _bearer(request)

    # get_optional_user's signature is (request, credentials, x_api_key), and
    # the last two carry Depends(...) defaults. Called directly rather than
    # through FastAPI's injection, `get_optional_user(cred)` bound cred to
    # `request` and left x_api_key holding the raw Depends object — which is
    # truthy, so the first branch ran _lookup_new_api_key(<Depends>) and died on
    # .startswith. Nothing catches that, so EVERY endpoint here that requires a
    # user answered 500, signed in or not: archive suggestions, schedule
    # suggestions, smart defaults, all of them. Pass the arguments positionally
    # and explicitly.
    user = await get_optional_user(
        request, cred, request.headers.get("X-API-Key"),
    )
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


# ══════════════════════════════════════════════════════════════════════════════
# Anomaly endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/anomalies")
async def list_anomalies():
    """
    Public — returns active anomalies.
    No auth required; used by frontend dashboards.
    """
    from app.core.anomaly_detector import get_active_anomalies

    anomalies = get_active_anomalies()
    return {"anomalies": anomalies, "count": len(anomalies)}


@router.post("/anomalies/{anomaly_id}/resolve")
async def resolve_anomaly_route(anomaly_id: str, request: Request):
    """Admin only — mark an anomaly as resolved."""
    from app.core.anomaly_detector import resolve_anomaly

    await _require_admin(request)
    success = resolve_anomaly(anomaly_id)
    return {"success": success}


# ══════════════════════════════════════════════════════════════════════════════
# Queue health
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/queue-health")
async def queue_health():
    """
    Public — returns current queue health status.
    Used by frontend to show queue status indicators.
    """
    from app.core.queue_intelligence import get_queue_health

    return get_queue_health()


# ══════════════════════════════════════════════════════════════════════════════
# Fallback stats
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/fallback-stats")
async def fallback_stats(request: Request):
    """Admin only — adaptive fallback layer statistics."""
    from app.core.adaptive_fallback import get_fallback_stats

    await _require_admin(request)
    return get_fallback_stats()


# ══════════════════════════════════════════════════════════════════════════════
# Playbooks
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/playbooks")
async def list_playbooks(request: Request):
    """
    Admin only — list all playbooks with currently matching anomalies highlighted.
    """
    from app.core.anomaly_detector import get_active_anomalies
    from app.core import playbooks as _playbooks

    await _require_admin(request)

    all_playbooks = _playbooks.get_all_playbooks()
    anomalies = get_active_anomalies()
    matched_ids = {pb["id"] for pb in _playbooks.match_active_playbooks(anomalies)}

    # Annotate each playbook with whether it currently matches active anomalies
    for pb in all_playbooks:
        pb["currently_matched"] = pb.get("id") in matched_ids

    return {
        "playbooks": all_playbooks,
        "active_anomaly_count": len(anomalies),
        "matched_playbook_count": len(matched_ids),
    }


@router.post("/playbooks/execute")
async def execute_playbook(body: PlaybookExecuteRequest, request: Request):
    """Admin only — execute a safe playbook action and log to audit_logs."""
    from app.core import playbooks as _playbooks
    from app.core.audit import log_from_request

    await _require_admin(request)

    result = _playbooks.execute_safe_action(body.action, body.params)

    log_from_request(
        request,
        "playbook.executed",
        metadata={
            "action": body.action,
            "params": body.params,
            "result": result,
        },
    )

    success = result.get("success", False) if isinstance(result, dict) else bool(result)
    message = result.get("message", "") if isinstance(result, dict) else str(result)
    action_taken = result.get("action_taken", body.action) if isinstance(result, dict) else body.action

    return {
        "success": success,
        "message": message,
        "action_taken": action_taken,
    }


@router.get("/playbooks/history")
async def playbook_history(request: Request):
    """Admin only — execution history for playbooks (last 100)."""
    from app.core import playbooks as _playbooks

    await _require_admin(request)
    return {"history": _playbooks.get_execution_history(limit=100)}


# ══════════════════════════════════════════════════════════════════════════════
# Auto-tuner
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/auto-tune")
async def auto_tune_status(request: Request):
    """Admin only — current tuner params and recent tuning history (last 50)."""
    from app.core.auto_tuner import get_all_params, get_tune_history

    await _require_admin(request)
    return {
        "params": get_all_params(),
        "history": get_tune_history(limit=50),
    }


@router.post("/auto-tune/reset")
async def auto_tune_reset(request: Request):
    """Admin only — reset all tuner params to defaults."""
    from app.core.auto_tuner import reset_to_defaults

    await _require_admin(request)
    return reset_to_defaults()


# ══════════════════════════════════════════════════════════════════════════════
# Schedule suggestions
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/schedule-suggestions")
async def schedule_suggestions(request: Request):
    """
    Logged-in user — returns schedule suggestions from Redis.
    Admins see all suggestions; regular users see only their own.
    Suggestions are stored as a Redis hash: vidgrab:schedule:suggestions
    with field=job_id, value=JSON blob containing at least {user_id}.
    """
    user = await _require_user(request)
    user_id = str(user["id"])
    is_admin = user.get("tier") == "admin"

    r = _redis()
    raw = r.hgetall("vidgrab:schedule:suggestions")

    suggestions = []
    for job_id_raw, val_raw in raw.items():
        job_id = job_id_raw.decode() if isinstance(job_id_raw, bytes) else job_id_raw
        try:
            suggestion = json.loads(val_raw.decode() if isinstance(val_raw, bytes) else val_raw)
        except Exception:
            continue

        if not is_admin and suggestion.get("user_id") != user_id:
            continue

        suggestion["job_id"] = job_id
        suggestions.append(suggestion)

    return {"suggestions": suggestions, "count": len(suggestions)}


@router.post("/schedule-suggestions/{job_id}/dismiss")
async def dismiss_schedule_suggestion(job_id: str, request: Request):
    """Logged-in user — remove a schedule suggestion by job_id."""
    user = await _require_user(request)
    user_id = str(user["id"])
    is_admin = user.get("tier") == "admin"

    r = _redis()

    if not is_admin:
        raw = r.hget("vidgrab:schedule:suggestions", job_id)
        if raw:
            try:
                suggestion = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                if suggestion.get("user_id") != user_id:
                    raise HTTPException(status_code=403, detail="Not your suggestion")
            except HTTPException:
                raise
            except Exception:
                pass

    removed = r.hdel("vidgrab:schedule:suggestions", job_id)
    return {"success": bool(removed), "job_id": job_id}


# ══════════════════════════════════════════════════════════════════════════════
# Archive suggestions
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/archive-suggestions")
async def archive_suggestions(request: Request):
    """
    Logged-in user — tag suggestions and duplicate detections for archive items.
    Tag suggestions: vidgrab:archive:tag_suggestions (hash: item_id -> JSON)
    Duplicate suggestions: vidgrab:archive:duplicates (hash: item_id -> JSON)
    """
    user = await _require_user(request)
    user_id = str(user["id"])
    is_admin = user.get("tier") == "admin"

    r = _redis()

    # Tag suggestions
    raw_tags = r.hgetall("vidgrab:archive:tag_suggestions")
    tag_suggestions = []
    for item_id_raw, val_raw in raw_tags.items():
        item_id = item_id_raw.decode() if isinstance(item_id_raw, bytes) else item_id_raw
        try:
            suggestion = json.loads(val_raw.decode() if isinstance(val_raw, bytes) else val_raw)
        except Exception:
            continue
        if not is_admin and suggestion.get("user_id") != user_id:
            continue
        suggestion["item_id"] = item_id
        tag_suggestions.append(suggestion)

    # Duplicate suggestions
    raw_dupes = r.hgetall("vidgrab:archive:duplicates")
    duplicate_suggestions = []
    for item_id_raw, val_raw in raw_dupes.items():
        item_id = item_id_raw.decode() if isinstance(item_id_raw, bytes) else item_id_raw
        try:
            entry = json.loads(val_raw.decode() if isinstance(val_raw, bytes) else val_raw)
        except Exception:
            continue
        if not is_admin and entry.get("user_id") != user_id:
            continue
        entry["item_id"] = item_id
        duplicate_suggestions.append(entry)

    return {
        "tag_suggestions": tag_suggestions,
        "duplicate_suggestions": duplicate_suggestions,
    }


@router.post("/archive-suggestions/{item_id}/accept-tags")
async def accept_archive_tags(item_id: str, body: AcceptTagsRequest, request: Request):
    """
    Logged-in user — accept suggested tags for an archive item.
    Updates archive_items.tags in Supabase, then removes the Redis suggestion.
    """
    user = await _require_user(request)
    user_id = str(user["id"])
    is_admin = user.get("tier") == "admin"

    r = _redis()

    # Verify ownership before mutating
    if not is_admin:
        raw = r.hget("vidgrab:archive:tag_suggestions", item_id)
        if raw:
            try:
                suggestion = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
                if suggestion.get("user_id") != user_id:
                    raise HTTPException(status_code=403, detail="Not your item")
            except HTTPException:
                raise
            except Exception:
                pass

    # Persist tags to Supabase
    try:
        from app.core.database import get_supabase_client
        supabase = get_supabase_client()
        query = supabase.table("archive_items").update({"tags": body.tags}).eq("id", item_id)
        if not is_admin:
            query = query.eq("user_id", user_id)
        query.execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update tags: {exc}")

    # Remove suggestion from Redis
    r.hdel("vidgrab:archive:tag_suggestions", item_id)

    return {"success": True, "item_id": item_id, "tags": body.tags}


@router.post("/archive-suggestions/{item_id}/dismiss")
async def dismiss_archive_suggestion(item_id: str, request: Request):
    """
    Logged-in user — discard a tag suggestion without applying it.

    The ✕ button in ArchiveSuggestions has always POSTed here and this route did
    not exist. The component drops the row from local state and swallows the
    error, so dismissing looked like it worked and the same suggestion was back
    on the next page load — the kind of bug a user reports as "it keeps coming
    back" rather than as an error.

    Ownership is checked the same way accept-tags checks it: a suggestion
    belongs to whoever the archive item belongs to.
    """
    user = await _require_user(request)
    user_id = str(user["id"])
    is_admin = user.get("tier") == "admin"

    r = _redis()

    raw = r.hget("vidgrab:archive:tag_suggestions", item_id)
    if not raw:
        # Already gone — accepted, expired, or dismissed in another tab. The
        # caller wanted it absent and it is absent.
        return {"success": True, "item_id": item_id, "already_gone": True}

    if not is_admin:
        try:
            suggestion = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            suggestion = {}
        if suggestion.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Not your item")

    r.hdel("vidgrab:archive:tag_suggestions", item_id)
    return {"success": True, "item_id": item_id}


# ══════════════════════════════════════════════════════════════════════════════
# Automation history (aggregated feed)
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/automation-history")
async def automation_history(request: Request):
    """
    Admin only — unified feed of automation events from all intelligence subsystems.
    Sources:
      - vidgrab:tuning:history   (list, auto-tuner actions)
      - vidgrab:playbooks:history (list, playbook executions)
      - vidgrab:anomalies:active  (hash, anomaly events)
    Returns combined list sorted descending by timestamp.
    """
    await _require_admin(request)

    r = _redis()
    events: List[Dict[str, Any]] = []

    # Auto-tune history (Redis list, newest first)
    try:
        _tuning = r.lrange("vidgrab:tuning:history", 0, 199)
    except Exception as _e:
        print(f"[automation-history] tuning history unavailable: {_e}", flush=True)
        _tuning = []
    for item in _tuning:
        try:
            entry = json.loads(item.decode() if isinstance(item, bytes) else item)
            events.append({
                "timestamp": entry.get("timestamp") or entry.get("ts", ""),
                "source": "auto_tuner",
                "action": entry.get("action") or f"set {entry.get('param', '?')}",
                "reason": entry.get("reason", ""),
                "outcome": entry.get("outcome") or str(entry.get("new_value", "")),
            })
        except Exception:
            continue

    # Playbook execution history (Redis list)
    try:
        _pb = r.lrange("vidgrab:playbooks:history", 0, 199)
    except Exception as _e:
        print(f"[automation-history] playbook history unavailable: {_e}", flush=True)
        _pb = []
    for item in _pb:
        try:
            entry = json.loads(item.decode() if isinstance(item, bytes) else item)
            events.append({
                "timestamp": entry.get("timestamp") or entry.get("ts", ""),
                "source": "playbooks",
                "action": entry.get("action", ""),
                "reason": entry.get("reason") or entry.get("trigger", ""),
                "outcome": entry.get("outcome") or entry.get("result", ""),
            })
        except Exception:
            continue

    # Active anomaly events (Redis hash)
    try:
        _anom = r.hgetall("vidgrab:anomalies:active") or {}
    except Exception as _e:
        print(f"[automation-history] anomaly feed unavailable: {_e}", flush=True)
        _anom = {}
    for _key, val_raw in _anom.items():
        try:
            entry = json.loads(val_raw.decode() if isinstance(val_raw, bytes) else val_raw)
            events.append({
                "timestamp": entry.get("detected_at") or entry.get("timestamp", ""),
                "source": "anomaly_detector",
                "action": f"anomaly.{entry.get('state', 'detected')}",
                "reason": entry.get("reason") or entry.get("description", ""),
                "outcome": entry.get("state", ""),
            })
        except Exception:
            continue

    # Sort descending by ISO timestamp string
    events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    return {"events": events, "count": len(events)}


# ══════════════════════════════════════════════════════════════════════════════
# Smart defaults
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/smart-defaults/{platform}")
async def get_smart_defaults(platform: str, request: Request):
    """Logged-in user — retrieve smart download defaults for a platform."""
    from app.core.smart_defaults import get_defaults

    user = await _require_user(request)
    user_id = str(user["id"])
    return get_defaults(user_id, platform)


@router.post("/smart-defaults/{platform}")
async def save_smart_defaults(platform: str, body: SmartDefaultsRequest, request: Request):
    """Logged-in user — record a quality/setting choice for smart defaults."""
    from app.core.smart_defaults import record_choice

    user = await _require_user(request)
    user_id = str(user["id"])

    record_choice(
        user_id=user_id,
        platform=platform,
        quality=body.quality,
        success=True,
        options={"download_subs": body.download_subs, "remove_watermark": body.remove_watermark},
    )

    return {"success": True, "platform": platform}
