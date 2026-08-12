"""
Data Export API — Phase 11
============================
POST /workspaces/{id}/exports                 trigger async export
GET  /workspaces/{id}/exports                 list export jobs
GET  /workspaces/{id}/exports/{eid}/download  inline download when ready

Supported export_types: archive | audit_logs | schedules | collections | full
Supported formats:      json | csv
"""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.rbac import ROLE_HIERARCHY, get_workspace_role
from app.core.audit import log_event

router = APIRouter()


class CreateExportRequest(BaseModel):
    export_type: str = "archive"   # archive | audit_logs | schedules | collections | full
    format: str = "json"           # json | csv
    date_from: Optional[str] = None
    date_to: Optional[str] = None


def _to_csv(rows: list[dict], fieldnames: list[str] | None = None) -> str:
    if not rows:
        return ""
    fieldnames = fieldnames or list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        # Flatten nested dicts/lists for CSV
        flat = {}
        for k, v in row.items():
            if isinstance(v, (dict, list)):
                flat[k] = json.dumps(v, ensure_ascii=False)
            else:
                flat[k] = v
        writer.writerow(flat)
    return buf.getvalue()


def _fetch_archive(supabase, workspace_id: str, date_from: str, date_to: str) -> list[dict]:
    q = (supabase.table("archive_items")
         .select("id,original_url,platform,title,creator_handle,creator_name,"
                 "duration_seconds,upload_date,thumbnail_url,hashtags,description_snippet,"
                 "view_count,user_notes,is_starred,is_downloaded,tags_user,item_status,"
                 "archived_at,last_accessed_at")
         .eq("workspace_id", workspace_id)
         .order("archived_at", desc=True))
    if date_from:
        q = q.gte("archived_at", date_from)
    if date_to:
        q = q.lte("archived_at", date_to + "T23:59:59Z")
    return q.execute().data or []


def _fetch_audit(supabase, workspace_id: str, date_from: str, date_to: str) -> list[dict]:
    q = (supabase.table("audit_logs")
         .select("id,action,actor_user_id,actor_email,resource_type,resource_id,metadata,ip_address,created_at")
         .eq("workspace_id", workspace_id)
         .order("created_at", desc=True))
    if date_from:
        q = q.gte("created_at", date_from)
    if date_to:
        q = q.lte("created_at", date_to + "T23:59:59Z")
    return q.execute().data or []


def _fetch_schedules(supabase, workspace_id: str) -> list[dict]:
    return (supabase.table("scheduled_jobs")
            .select("id,job_type,input_payload,schedule_type,run_at,run_on_weekday,"
                    "next_run_at,last_run_at,last_run_status,is_active,run_count,created_at")
            .eq("workspace_id", workspace_id)
            .order("created_at", desc=True)
            .execute().data or [])


def _fetch_collections(supabase, workspace_id: str) -> list[dict]:
    return (supabase.table("archive_collections")
            .select("id,name,description,color,icon,item_count,is_shared,created_at")
            .eq("workspace_id", workspace_id)
            .order("created_at")
            .execute().data or [])


@router.post("/workspaces/{workspace_id}/exports", status_code=201)
async def create_export(
    workspace_id: str,
    body: CreateExportRequest,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để xuất dữ liệu."})

    if body.export_type not in ("archive", "audit_logs", "schedules", "collections", "full"):
        raise HTTPException(400, "export_type không hợp lệ.")
    if body.format not in ("json", "csv"):
        raise HTTPException(400, "format phải là 'json' hoặc 'csv'.")

    supabase = get_service_client()

    res = supabase.table("workspace_export_jobs").insert({
        "workspace_id":  workspace_id,
        "requested_by":  user_id,
        "export_type":   body.export_type,
        "format":        body.format,
        "filters": {
            "date_from": body.date_from,
            "date_to":   body.date_to,
        },
        "status":        "processing",
    }).execute()

    if not res.data:
        raise HTTPException(500, "Không thể tạo export job.")

    export_job = res.data[0]
    eid = export_job["id"]

    # Run export synchronously (data is typically small)
    try:
        date_from = body.date_from or ""
        date_to = body.date_to or ""

        bundles: dict[str, list] = {}
        if body.export_type in ("archive", "full"):
            bundles["archive_items"] = _fetch_archive(supabase, workspace_id, date_from, date_to)
        if body.export_type in ("audit_logs", "full"):
            bundles["audit_logs"] = _fetch_audit(supabase, workspace_id, date_from, date_to)
        if body.export_type in ("schedules", "full"):
            bundles["scheduled_jobs"] = _fetch_schedules(supabase, workspace_id)
        if body.export_type in ("collections", "full"):
            bundles["collections"] = _fetch_collections(supabase, workspace_id)

        total_rows = sum(len(v) for v in bundles.values())
        now_iso = datetime.now(timezone.utc).isoformat()

        supabase.table("workspace_export_jobs").update({
            "status":       "ready",
            "row_count":    total_rows,
            "completed_at": now_iso,
            "expires_at":   now_iso[:10] + "T23:59:59Z",  # expires end of today
        }).eq("id", eid).execute()

        log_event("exports.created", actor_user_id=user_id, workspace_id=workspace_id,
                  resource_type="export_job", resource_id=eid,
                  metadata={"type": body.export_type, "format": body.format, "rows": total_rows})

    except Exception as exc:
        supabase.table("workspace_export_jobs").update({
            "status": "failed", "error_message": str(exc),
        }).eq("id", eid).execute()

    return {"export_job": supabase.table("workspace_export_jobs")
            .select("*").eq("id", eid).single().execute().data}


@router.get("/workspaces/{workspace_id}/exports")
async def list_exports(workspace_id: str, user=Depends(get_required_user)):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role"})

    supabase = get_service_client()
    res = (supabase.table("workspace_export_jobs")
           .select("*")
           .eq("workspace_id", workspace_id)
           .order("created_at", desc=True)
           .limit(20)
           .execute())
    return {"exports": res.data or []}


@router.get("/workspaces/{workspace_id}/exports/{export_id}/download")
async def download_export(
    workspace_id: str,
    export_id: str,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role"})

    supabase = get_service_client()
    job_res = (supabase.table("workspace_export_jobs")
               .select("*")
               .eq("id", export_id)
               .eq("workspace_id", workspace_id)
               .single()
               .execute())
    if not job_res.data:
        raise HTTPException(404, "Export job không tồn tại.")

    job = job_res.data
    if job["status"] != "ready":
        raise HTTPException(425, f"Export chưa sẵn sàng (status: {job['status']}).")

    # Rebuild the data inline for download (no file storage needed)
    date_from = (job.get("filters") or {}).get("date_from", "")
    date_to   = (job.get("filters") or {}).get("date_to", "")
    fmt = job["format"]
    etype = job["export_type"]

    bundles: dict[str, list] = {}
    if etype in ("archive", "full"):
        bundles["archive_items"] = _fetch_archive(supabase, workspace_id, date_from, date_to)
    if etype in ("audit_logs", "full"):
        bundles["audit_logs"] = _fetch_audit(supabase, workspace_id, date_from, date_to)
    if etype in ("schedules", "full"):
        bundles["scheduled_jobs"] = _fetch_schedules(supabase, workspace_id)
    if etype in ("collections", "full"):
        bundles["collections"] = _fetch_collections(supabase, workspace_id)

    filename = f"vidgrab-{etype}-{export_id[:8]}.{fmt}"

    if fmt == "json":
        content = json.dumps(
            {"export_type": etype, "workspace_id": workspace_id,
             "exported_at": datetime.now(timezone.utc).isoformat(),
             "data": bundles if len(bundles) > 1 else list(bundles.values())[0]},
            ensure_ascii=False, indent=2,
        )
        return Response(
            content=content.encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:  # csv — flatten first bundle or combine
        all_rows = []
        for rows in bundles.values():
            all_rows.extend(rows)
        csv_text = _to_csv(all_rows)
        return Response(
            content=csv_text.encode("utf-8"),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
