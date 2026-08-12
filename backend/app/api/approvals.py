"""
Approval Workflow API — Phase 11
=====================================
POST /workspaces/{id}/approvals              submit approval request (editor+)
GET  /workspaces/{id}/approvals              list requests (viewer+)
GET  /workspaces/{id}/approvals/pending      pending queue for approvers (admin+)
POST /workspaces/{id}/approvals/{req_id}/approve  approve (admin+)
POST /workspaces/{id}/approvals/{req_id}/reject   reject  (admin+)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.rbac import ROLE_HIERARCHY, get_workspace_role
from app.core.audit import log_event
from app.core.error_codes import make_error

router = APIRouter()


class CreateApprovalRequest(BaseModel):
    action_type:    str          # 'bulk_download' | 'schedule_create' | 'webhook_create' | 'archive_delete'
    action_payload: dict = {}
    note:           Optional[str] = None


class RejectRequest(BaseModel):
    reason: Optional[str] = None


@router.post("/workspaces/{workspace_id}/approvals", status_code=201)
async def submit_approval(
    workspace_id: str,
    body: CreateApprovalRequest,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role:
        raise HTTPException(403, detail=make_error("workspace_not_member"))

    if body.action_type not in ("bulk_download", "schedule_create", "webhook_create", "archive_delete"):
        raise HTTPException(400, "action_type không hợp lệ.")

    supabase = get_service_client()

    # Find eligible approvers: admins + owners in the workspace
    approver_res = (
        supabase.table("workspace_memberships")
        .select("user_id")
        .eq("workspace_id", workspace_id)
        .in_("role", ["admin", "owner"])
        .execute()
    )
    approvers = [m["user_id"] for m in (approver_res.data or []) if m["user_id"] != user_id]

    if not approvers:
        raise HTTPException(400, "Workspace không có Admin nào để phê duyệt.")

    payload = dict(body.action_payload)
    if body.note:
        payload["requester_note"] = body.note

    res = supabase.table("approval_requests").insert({
        "workspace_id":       workspace_id,
        "requester_user_id":  user_id,
        "action_type":        body.action_type,
        "action_payload":     payload,
        "status":             "awaiting_approval",
        "approvers":          approvers,
    }).execute()

    if not res.data:
        raise HTTPException(500, "Không thể tạo yêu cầu phê duyệt.")

    req = res.data[0]
    log_event("approval.requested", actor_user_id=user_id,
              workspace_id=workspace_id, resource_type="approval_request",
              resource_id=req["id"], metadata={"action_type": body.action_type})

    return {"approval_request": req}


@router.get("/workspaces/{workspace_id}/approvals")
async def list_approvals(
    workspace_id: str,
    status: Optional[str] = None,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role:
        raise HTTPException(403, detail=make_error("workspace_not_member"))

    supabase = get_service_client()
    q = (
        supabase.table("approval_requests")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
    )
    # Non-admins see only their own requests
    if ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        q = q.eq("requester_user_id", user_id)
    if status:
        q = q.eq("status", status)

    return {"approval_requests": q.execute().data or []}


@router.get("/workspaces/{workspace_id}/approvals/pending")
async def pending_approvals(workspace_id: str, user=Depends(get_required_user)):
    """Returns approvals awaiting action where the current user is an eligible approver."""
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để xem hàng chờ phê duyệt."})

    supabase = get_service_client()
    res = (
        supabase.table("approval_requests")
        .select("*")
        .eq("workspace_id", workspace_id)
        .eq("status", "awaiting_approval")
        .contains("approvers", [user_id])
        .order("created_at")
        .execute()
    )
    return {"pending": res.data or []}


@router.post("/workspaces/{workspace_id}/approvals/{req_id}/approve")
async def approve_request(
    workspace_id: str,
    req_id: str,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để phê duyệt."})

    supabase = get_service_client()
    req = (
        supabase.table("approval_requests")
        .select("*")
        .eq("id", req_id)
        .eq("workspace_id", workspace_id)
        .single()
        .execute()
    )
    if not req.data:
        raise HTTPException(404, "Yêu cầu không tồn tại.")
    if req.data["status"] != "awaiting_approval":
        raise HTTPException(409, f"Yêu cầu đã ở trạng thái '{req.data['status']}'.")

    now = datetime.now(timezone.utc).isoformat()
    supabase.table("approval_requests").update({
        "status":      "approved",
        "approved_by": user_id,
        "resolved_at": now,
        "updated_at":  now,
    }).eq("id", req_id).execute()

    log_event("approval.approved", actor_user_id=user_id,
              workspace_id=workspace_id, resource_type="approval_request", resource_id=req_id)

    return {"status": "approved", "approval_id": req_id}


@router.post("/workspaces/{workspace_id}/approvals/{req_id}/reject")
async def reject_request(
    workspace_id: str,
    req_id: str,
    body: RejectRequest,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để từ chối."})

    supabase = get_service_client()
    req = (
        supabase.table("approval_requests")
        .select("status")
        .eq("id", req_id)
        .eq("workspace_id", workspace_id)
        .single()
        .execute()
    )
    if not req.data:
        raise HTTPException(404, "Yêu cầu không tồn tại.")
    if req.data["status"] != "awaiting_approval":
        raise HTTPException(409, f"Yêu cầu đã ở trạng thái '{req.data['status']}'.")

    now = datetime.now(timezone.utc).isoformat()
    supabase.table("approval_requests").update({
        "status":           "rejected",
        "rejected_by":      user_id,
        "rejection_reason": body.reason or "",
        "resolved_at":      now,
        "updated_at":       now,
    }).eq("id", req_id).execute()

    log_event("approval.rejected", actor_user_id=user_id,
              workspace_id=workspace_id, resource_type="approval_request", resource_id=req_id,
              metadata={"reason": body.reason})

    return {"status": "rejected", "approval_id": req_id}
