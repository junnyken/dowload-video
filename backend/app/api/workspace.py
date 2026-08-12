"""
Workspace API — Phase 11
===========================
GET    /workspaces                  list my workspaces
POST   /workspaces                  create team workspace
GET    /workspaces/{id}             workspace detail + my role
PATCH  /workspaces/{id}             update workspace settings (admin/owner)
DELETE /workspaces/{id}             delete workspace (owner only)

GET    /workspaces/{id}/members     list members
PATCH  /workspaces/{id}/members/{uid}  update role (admin/owner)
DELETE /workspaces/{id}/members/{uid}  remove member (admin/owner)

GET    /workspaces/{id}/stats       usage stats (admin/owner)
POST   /workspaces/ensure-personal  ensure personal workspace exists
"""

import re
import unicodedata
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.rbac import (
    ROLE_HIERARCHY,
    ensure_personal_workspace,
    get_workspace_role,
    require_workspace_role,
)
from app.core.audit import log_from_request

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str, user_id: str) -> str:
    """Generate a URL-safe unique workspace slug."""
    s = unicodedata.normalize("NFKD", name.lower())
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    suffix = user_id.replace("-", "")[:8]
    return f"{s[:40]}-{suffix}"


# ── Models ────────────────────────────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    name: str
    type: str = "team"   # 'team' | 'enterprise'


class PatchWorkspaceRequest(BaseModel):
    name: Optional[str] = None
    settings: Optional[dict] = None
    approval_bulk_threshold: Optional[int] = None
    approval_schedules: Optional[bool] = None
    approval_webhooks: Optional[bool] = None
    approval_archive_delete: Optional[bool] = None
    features: Optional[dict] = None


class UpdateMemberRoleRequest(BaseModel):
    role: str  # 'admin' | 'editor' | 'viewer'


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/workspaces")
async def list_my_workspaces(user=Depends(get_required_user)):
    """Return all workspaces the current user belongs to."""
    supabase = get_service_client()
    user_id = str(user["id"])

    # Ensure personal workspace exists lazily
    ensure_personal_workspace(user_id, email=user.get("email", ""))

    res = (
        supabase.table("user_workspaces")  # view created in migration
        .select("*")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return {"workspaces": res.data or []}


@router.post("/workspaces/ensure-personal", status_code=200)
async def ensure_personal(user=Depends(get_required_user)):
    """Idempotently create the user's personal workspace and return it."""
    user_id = str(user["id"])
    ws = ensure_personal_workspace(user_id, email=user.get("email", ""))
    return {"workspace": ws}


@router.post("/workspaces", status_code=201)
async def create_workspace(
    request: Request,
    body: CreateWorkspaceRequest,
    user=Depends(get_required_user),
):
    if body.type not in ("team", "enterprise"):
        raise HTTPException(400, "workspace type must be 'team' or 'enterprise'")

    name = body.name.strip()
    if not name or len(name) > 60:
        raise HTTPException(400, "Tên workspace phải từ 1–60 ký tự.")

    supabase = get_service_client()
    user_id = str(user["id"])
    slug = _slugify(name, user_id)

    ws_res = supabase.table("workspaces").insert({
        "name":          name,
        "slug":          slug,
        "type":          body.type,
        "owner_user_id": user_id,
        "plan":          "free",
    }).execute()

    if not ws_res.data:
        raise HTTPException(500, "Không thể tạo workspace.")

    workspace = ws_res.data[0]

    # Add owner membership
    supabase.table("workspace_memberships").insert({
        "workspace_id": workspace["id"],
        "user_id":      user_id,
        "role":         "owner",
    }).execute()

    log_from_request(request, "workspace.created",
                     user=user, workspace_id=workspace["id"],
                     resource_type="workspace", resource_id=workspace["id"])

    return {"workspace": workspace}


@router.get("/workspaces/{workspace_id}")
async def get_workspace(workspace_id: str, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role:
        raise HTTPException(403, detail={
            "error_code": "workspace_not_member",
            "user_message": "Bạn không phải thành viên của workspace này.",
        })

    res = supabase.table("workspaces").select("*").eq("id", workspace_id).single().execute()
    if not res.data:
        raise HTTPException(404, "Workspace không tồn tại.")

    return {"workspace": res.data, "my_role": role}


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    request: Request,
    workspace_id: str,
    body: PatchWorkspaceRequest,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần quyền Admin để thay đổi cài đặt workspace."})

    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Không có trường nào để cập nhật.")

    supabase = get_service_client()
    res = (supabase.table("workspaces").update(updates)
           .eq("id", workspace_id).execute())
    if not res.data:
        raise HTTPException(404, "Workspace không tồn tại.")

    log_from_request(request, "workspace.settings_changed", user=user,
                     workspace_id=workspace_id, metadata={"fields": list(updates.keys())})

    return {"workspace": res.data[0]}


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    request: Request,
    workspace_id: str,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if role != "owner":
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Chỉ Owner mới có thể xóa workspace."})

    supabase = get_service_client()
    ws = supabase.table("workspaces").select("type").eq("id", workspace_id).single().execute()
    if ws.data and ws.data.get("type") == "personal":
        raise HTTPException(400, "Không thể xóa personal workspace.")

    supabase.table("workspaces").delete().eq("id", workspace_id).execute()
    log_from_request(request, "workspace.deleted", user=user,
                     workspace_id=workspace_id, resource_type="workspace", resource_id=workspace_id)


# ── Members ───────────────────────────────────────────────────────────────────

@router.get("/workspaces/{workspace_id}/members")
async def list_members(workspace_id: str, user=Depends(get_required_user)):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role:
        raise HTTPException(403, detail={"error_code": "workspace_not_member"})

    supabase = get_service_client()
    res = (
        supabase.table("workspace_memberships")
        .select("id, user_id, role, joined_at, status, invited_by")
        .eq("workspace_id", workspace_id)
        .order("joined_at")
        .execute()
    )
    # Enrich with profile data
    members = res.data or []
    if members:
        uids = [m["user_id"] for m in members]
        profiles_res = (
            supabase.table("profiles")
            .select("id, email, display_name, avatar_url")
            .in_("id", uids)
            .execute()
        )
        profile_map = {p["id"]: p for p in (profiles_res.data or [])}
        for m in members:
            m["profile"] = profile_map.get(m["user_id"], {})

    return {"members": members, "my_role": role}


@router.patch("/workspaces/{workspace_id}/members/{target_user_id}")
async def update_member_role(
    request: Request,
    workspace_id: str,
    target_user_id: str,
    body: UpdateMemberRoleRequest,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    my_role = get_workspace_role(user_id, workspace_id)

    if not my_role or ROLE_HIERARCHY.get(my_role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để thay đổi vai trò thành viên."})

    new_role = body.role
    if new_role not in ("admin", "editor", "viewer"):
        raise HTTPException(400, "role phải là 'admin', 'editor', hoặc 'viewer'.")

    # Admins cannot promote/demote owners
    supabase = get_service_client()
    target_role = get_workspace_role(target_user_id, workspace_id)
    if target_role == "owner":
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Không thể thay đổi vai trò của Owner."})

    res = (
        supabase.table("workspace_memberships")
        .update({"role": new_role})
        .eq("workspace_id", workspace_id)
        .eq("user_id", target_user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Thành viên không tồn tại.")

    log_from_request(request, "role.changed", user=user, workspace_id=workspace_id,
                     resource_type="member", resource_id=target_user_id,
                     metadata={"new_role": new_role, "previous_role": target_role})

    return {"user_id": target_user_id, "role": new_role}


@router.delete("/workspaces/{workspace_id}/members/{target_user_id}", status_code=204)
async def remove_member(
    request: Request,
    workspace_id: str,
    target_user_id: str,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    my_role = get_workspace_role(user_id, workspace_id)

    if not my_role or ROLE_HIERARCHY.get(my_role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để xóa thành viên."})

    # Cannot remove workspace owner
    target_role = get_workspace_role(target_user_id, workspace_id)
    if target_role == "owner":
        raise HTTPException(403, "Không thể xóa Owner khỏi workspace.")

    # Users can always leave their own workspace
    supabase = get_service_client()
    supabase.table("workspace_memberships").delete() \
        .eq("workspace_id", workspace_id) \
        .eq("user_id", target_user_id) \
        .execute()

    log_from_request(request, "member.removed", user=user, workspace_id=workspace_id,
                     resource_type="member", resource_id=target_user_id)


@router.get("/workspaces/{workspace_id}/stats")
async def workspace_stats(workspace_id: str, user=Depends(get_required_user)):
    """Return usage snapshot for quota display."""
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để xem thống kê workspace."})

    supabase = get_service_client()

    member_count = (supabase.table("workspace_memberships")
                    .select("id", count="exact")
                    .eq("workspace_id", workspace_id)
                    .execute()).count or 0

    archive_count = (supabase.table("archive_items")
                     .select("id", count="exact")
                     .eq("workspace_id", workspace_id)
                     .execute()).count or 0

    schedule_count = (supabase.table("scheduled_jobs")
                      .select("id", count="exact")
                      .eq("workspace_id", workspace_id)
                      .execute()).count or 0

    pending_approvals = (supabase.table("approval_requests")
                         .select("id", count="exact")
                         .eq("workspace_id", workspace_id)
                         .eq("status", "awaiting_approval")
                         .execute()).count or 0

    return {
        "member_count":       member_count,
        "archive_item_count": archive_count,
        "schedule_count":     schedule_count,
        "pending_approvals":  pending_approvals,
    }
