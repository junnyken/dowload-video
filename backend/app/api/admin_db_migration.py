"""
Admin — moving the database to the Vibe Host Postgres (21/10/2026)
=================================================================

docs/MIGRATION-DB-VIBEHOST.md. Read-only for now: the last probe of both
databases (app.core.db_migration) and a button to probe again. Never returns
a connection string or password.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.admin import verify_admin
from app.core.audit import log_admin_action

router = APIRouter()


@router.get("/db-migration")
async def db_migration_status(_=Depends(verify_admin)) -> dict:
    from app.core import db_migration as M  # noqa: PLC0415
    from app.core.redis_client import get_redis  # noqa: PLC0415
    try:
        raw = get_redis().get(M.PROBE_KEY)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="redis unavailable")
    return {
        "source_set": bool(M.source_dsn()),
        "target_set": bool(M.target_dsn()),
        "probe": json.loads(raw) if raw else None,
    }


@router.post("/db-migration/probe")
async def db_migration_probe(request: Request, _=Depends(verify_admin)) -> dict:
    from app.core import db_migration as M  # noqa: PLC0415
    report = await asyncio.wait_for(asyncio.to_thread(M.probe), timeout=90)
    log_admin_action(request, "admin.db_migration.probe", resource_type="database", resource_id="probe",
                     metadata={"source_ok": report["source"].get("ok"), "target_ok": report["target"].get("ok")})
    return {"source_set": bool(M.source_dsn()), "target_set": bool(M.target_dsn()), "probe": report}


@router.get("/db-migration/copy")
async def db_migration_copy_status(_=Depends(verify_admin)) -> dict:
    from app.core import db_migration as M  # noqa: PLC0415
    from app.core.redis_client import get_redis  # noqa: PLC0415
    rc = get_redis()
    raw = rc.get(M.COPY_KEY)
    return {"running": bool(rc.get(M.COPY_LOCK)), "last": json.loads(raw) if raw else None}


@router.post("/db-migration/copy")
async def db_migration_copy(request: Request, _=Depends(verify_admin)) -> dict:
    """Copy Supabase -> Vibe Host Postgres. Body {"confirm": "CHEP", "replace": bool}.
    Only writes to the target; the app keeps using Supabase."""
    from app.core import db_migration as M  # noqa: PLC0415
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    if not isinstance(payload, dict) or payload.get("confirm") != "CHEP":
        raise HTTPException(status_code=400, detail={"error": "confirm", "message": 'Gõ "CHEP" để xác nhận.'})
    if not M.source_dsn() or not M.target_dsn():
        raise HTTPException(status_code=400, detail={"error": "not_configured",
                                                      "message": "Thiếu SUPABASE_DB_URL hoặc DATABASE_URL."})
    replace = bool(payload.get("replace"))
    if not M.copy_in_background(replace=replace):
        raise HTTPException(status_code=409, detail={"error": "running", "message": "Đang chép, chờ xong đã."})
    log_admin_action(request, "admin.db_migration.copy", resource_type="database", resource_id="copy",
                     metadata={"replace": replace})
    return {"started": True}
