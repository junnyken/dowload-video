"""
Workspace Invite API — Phase 11
===================================
POST   /workspaces/{id}/invites          create invite (admin/owner)
GET    /workspaces/{id}/invites          list invites (admin/owner)
DELETE /workspaces/{id}/invites/{inv_id} revoke invite (admin/owner)
GET    /invites/{token}                  inspect invite (public — any logged-in user)
POST   /invites/{token}/accept           accept invite (any logged-in user)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.rbac import ROLE_HIERARCHY, get_workspace_role
from app.core.audit import log_from_request

router = APIRouter()


class CreateInviteRequest(BaseModel):
    email: str
    role: str = "viewer"   # 'admin' | 'editor' | 'viewer'


@router.post("/workspaces/{workspace_id}/invites", status_code=201)
async def create_invite(
    request: Request,
    workspace_id: str,
    body: CreateInviteRequest,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    my_role = get_workspace_role(user_id, workspace_id)
    if not my_role or ROLE_HIERARCHY.get(my_role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role",
                                         "user_message": "Cần Admin để gửi lời mời."})

    if body.role not in ("admin", "editor", "viewer"):
        raise HTTPException(400, "role phải là 'admin', 'editor', hoặc 'viewer'.")

    email = body.email.strip().lower()
    if not email:
        raise HTTPException(400, "Email không hợp lệ.")

    supabase = get_service_client()

    # Check for existing pending invite for this email
    existing = (
        supabase.table("workspace_invites")
        .select("id, status")
        .eq("workspace_id", workspace_id)
        .eq("email", email)
        .eq("status", "pending")
        .execute()
    )
    if existing.data:
        raise HTTPException(409, "Đã có lời mời đang chờ cho email này.")

    # Check user is not already a member
    profile_res = (supabase.table("profiles").select("id")
                   .eq("email", email).single().execute())
    if profile_res.data:
        existing_member = get_workspace_role(profile_res.data["id"], workspace_id)
        if existing_member:
            raise HTTPException(409, "Người dùng này đã là thành viên của workspace.")

    res = supabase.table("workspace_invites").insert({
        "workspace_id": workspace_id,
        "email":        email,
        "role":         body.role,
        "invited_by":   user_id,
    }).execute()

    if not res.data:
        raise HTTPException(500, "Không thể tạo lời mời.")

    invite = res.data[0]
    log_from_request(request, "invite.created", user=user, workspace_id=workspace_id,
                     resource_type="invite", resource_id=invite["id"],
                     metadata={"email": email, "role": body.role})

    return {"invite": invite}


@router.get("/workspaces/{workspace_id}/invites")
async def list_invites(workspace_id: str, user=Depends(get_required_user)):
    user_id = str(user["id"])
    my_role = get_workspace_role(user_id, workspace_id)
    if not my_role or ROLE_HIERARCHY.get(my_role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role"})

    supabase = get_service_client()
    res = (
        supabase.table("workspace_invites")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    )

    invites = res.data or []
    now = datetime.now(timezone.utc)
    # Mark expired invites dynamically
    for inv in invites:
        if inv["status"] == "pending" and inv.get("expires_at"):
            if datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00")) < now:
                inv["status"] = "expired"

    return {"invites": invites}


@router.delete("/workspaces/{workspace_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    request: Request,
    workspace_id: str,
    invite_id: str,
    user=Depends(get_required_user),
):
    user_id = str(user["id"])
    my_role = get_workspace_role(user_id, workspace_id)
    if not my_role or ROLE_HIERARCHY.get(my_role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={"error_code": "insufficient_role"})

    supabase = get_service_client()
    supabase.table("workspace_invites") \
        .update({"status": "revoked"}) \
        .eq("id", invite_id) \
        .eq("workspace_id", workspace_id) \
        .execute()

    log_from_request(request, "invite.revoked", user=user, workspace_id=workspace_id,
                     resource_type="invite", resource_id=invite_id)


@router.get("/invites/{token}")
async def inspect_invite(token: str, user=Depends(get_required_user)):
    """Return invite metadata so the accept page can show workspace name + role."""
    supabase = get_service_client()
    res = (
        supabase.table("workspace_invites")
        .select("id, workspace_id, email, role, status, expires_at")
        .eq("token", token)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Lời mời không hợp lệ.")

    inv = res.data
    now = datetime.now(timezone.utc)
    if inv["status"] != "pending":
        raise HTTPException(410, f"Lời mời đã '{inv['status']}'.")
    if datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00")) < now:
        raise HTTPException(410, "Lời mời đã hết hạn.")

    # Fetch workspace name
    ws_res = (supabase.table("workspaces")
              .select("name, type, slug")
              .eq("id", inv["workspace_id"])
              .single()
              .execute())

    return {
        "invite":    inv,
        "workspace": ws_res.data or {},
    }


@router.post("/invites/{token}/accept", status_code=200)
async def accept_invite(
    request: Request,
    token: str,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])

    res = (
        supabase.table("workspace_invites")
        .select("*")
        .eq("token", token)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Lời mời không hợp lệ.")

    inv = res.data
    now = datetime.now(timezone.utc)

    if inv["status"] != "pending":
        raise HTTPException(410, f"Lời mời đã '{inv['status']}'.")
    if datetime.fromisoformat(inv["expires_at"].replace("Z", "+00:00")) < now:
        # Mark expired
        supabase.table("workspace_invites").update({"status": "expired"}).eq("id", inv["id"]).execute()
        raise HTTPException(410, "Lời mời đã hết hạn.")

    workspace_id = inv["workspace_id"]

    # Check already a member
    existing_role = get_workspace_role(user_id, workspace_id)
    if existing_role:
        # Mark invite accepted anyway
        supabase.table("workspace_invites").update({
            "status": "accepted", "accepted_at": now.isoformat(), "accepted_by": user_id,
        }).eq("id", inv["id"]).execute()
        return {"workspace_id": workspace_id, "role": existing_role, "already_member": True}

    # Create membership
    supabase.table("workspace_memberships").insert({
        "workspace_id": workspace_id,
        "user_id":      user_id,
        "role":         inv["role"],
        "invited_by":   inv["invited_by"],
    }).execute()

    # Mark invite as accepted
    supabase.table("workspace_invites").update({
        "status":      "accepted",
        "accepted_at": now.isoformat(),
        "accepted_by": user_id,
    }).eq("id", inv["id"]).execute()

    log_from_request(request, "invite.accepted", user=user, workspace_id=workspace_id,
                     resource_type="invite", resource_id=inv["id"],
                     metadata={"role": inv["role"]})

    return {"workspace_id": workspace_id, "role": inv["role"], "already_member": False}
