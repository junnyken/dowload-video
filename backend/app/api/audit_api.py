"""
Audit Log API — Phase 11
============================
GET /workspaces/{id}/audit   paginated audit log with filters
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.rbac import ROLE_HIERARCHY, get_workspace_role

router = APIRouter()

# Human-readable action labels for the UI
ACTION_LABELS: dict[str, str] = {
    "download.created":       "Tạo lượt tải",
    "archive_item.created":   "Lưu vào Archive",
    "archive_item.deleted":   "Xóa Archive item",
    "archive_item.starred":   "Gắn sao Archive item",
    "collection.created":     "Tạo Collection",
    "collection.deleted":     "Xóa Collection",
    "schedule.created":       "Tạo lịch tải",
    "schedule.deleted":       "Xóa lịch tải",
    "schedule.toggled":       "Bật/Tắt lịch tải",
    "invite.created":         "Gửi lời mời",
    "invite.accepted":        "Chấp nhận lời mời",
    "invite.revoked":         "Thu hồi lời mời",
    "member.removed":         "Xóa thành viên",
    "role.changed":           "Thay đổi vai trò",
    "api_key.generated":      "Tạo API Key",
    "api_key.revoked":        "Thu hồi API Key",
    "webhook.created":        "Đăng ký Webhook",
    "webhook.revoked":        "Thu hồi Webhook",
    "file.pinned":            "Ghim file",
    "file.unpinned":          "Bỏ ghim file",
    "file.deleted":           "Xóa file",
    "workspace.created":      "Tạo Workspace",
    "workspace.settings_changed": "Cập nhật cài đặt Workspace",
    "workspace.deleted":      "Xóa Workspace",
    "approval.requested":     "Yêu cầu phê duyệt",
    "approval.approved":      "Phê duyệt yêu cầu",
    "approval.rejected":      "Từ chối yêu cầu",
}


@router.get("/workspaces/{workspace_id}/audit")
async def get_audit_log(
    workspace_id: str,
    user=Depends(get_required_user),
    actor_user_id: Optional[str] = Query(None),
    action:        Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    date_from:     Optional[str] = Query(None),   # ISO date
    date_to:       Optional[str] = Query(None),   # ISO date
    page:          int = Query(1, ge=1),
    page_size:     int = Query(50, ge=1, le=200),
):
    user_id = str(user["id"])
    role = get_workspace_role(user_id, workspace_id)
    if not role or ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["admin"]:
        raise HTTPException(403, detail={
            "error_code": "insufficient_role",
            "user_message": "Cần Admin để xem audit log.",
        })

    supabase = get_service_client()
    offset = (page - 1) * page_size

    q = (
        supabase.table("audit_logs")
        .select("*", count="exact")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
    )

    if actor_user_id:
        q = q.eq("actor_user_id", actor_user_id)
    if action:
        q = q.eq("action", action)
    if resource_type:
        q = q.eq("resource_type", resource_type)
    if date_from:
        q = q.gte("created_at", date_from)
    if date_to:
        q = q.lte("created_at", date_to + "T23:59:59Z")

    res = q.execute()
    logs = res.data or []

    # Enrich with human-readable labels
    for log in logs:
        log["action_label"] = ACTION_LABELS.get(log.get("action", ""), log.get("action", ""))

    return {
        "logs":      logs,
        "total":     res.count or 0,
        "page":      page,
        "page_size": page_size,
        "action_labels": ACTION_LABELS,
    }
