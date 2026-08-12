"""
Role-Based Access Control for VidGrab workspaces.

Role hierarchy (ascending privilege):
  viewer < editor < admin < owner

All checks are enforced server-side via FastAPI dependencies.
Frontend may hide UI but backend is the source of truth.
"""

from fastapi import Depends, HTTPException

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client

# ── Role ordering ──────────────────────────────────────────────────────────────
ROLE_HIERARCHY = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}

ROLE_CAPABILITIES: dict[str, list[str]] = {
    "viewer": [
        "archive.read", "schedule.read", "analytics.read",
        "collections.read", "comments.read",
    ],
    "editor": [
        "archive.read", "archive.write", "archive.comment",
        "schedule.read", "schedule.write",
        "collections.read", "collections.write",
        "download.create", "notes.write", "storage.pin",
        "comments.read", "comments.write",
    ],
    "admin": [
        "archive.read", "archive.write", "archive.delete", "archive.comment",
        "schedule.read", "schedule.write", "schedule.delete",
        "collections.read", "collections.write", "collections.delete",
        "download.create", "notes.write", "storage.pin",
        "members.read", "members.invite", "members.update_role",
        "webhooks.manage", "notifications.manage",
        "audit.read", "approvals.manage",
        "comments.read", "comments.write", "comments.delete",
        "exports.create",
    ],
    "owner": [
        # Inherits everything from admin plus:
        "workspace.settings", "workspace.delete", "workspace.transfer",
        "members.remove", "members.suspend", "billing.manage",
        "approvals.configure", "exports.create",
        # All admin caps
        "archive.read", "archive.write", "archive.delete", "archive.comment",
        "schedule.read", "schedule.write", "schedule.delete",
        "collections.read", "collections.write", "collections.delete",
        "download.create", "notes.write", "storage.pin",
        "members.read", "members.invite", "members.update_role",
        "webhooks.manage", "notifications.manage",
        "audit.read", "approvals.manage",
        "comments.read", "comments.write", "comments.delete",
    ],
}


def has_role(user_role: str | None, required_role: str) -> bool:
    """Return True if user_role is >= required_role in the hierarchy."""
    return ROLE_HIERARCHY.get(user_role or "", 0) >= ROLE_HIERARCHY.get(required_role, 99)


def can(user_role: str | None, capability: str) -> bool:
    """Return True if the given role grants the capability."""
    return capability in ROLE_CAPABILITIES.get(user_role or "", [])


# ── Workspace membership resolver ─────────────────────────────────────────────

def get_workspace_role(user_id: str, workspace_id: str) -> str | None:
    """Return the user's role in a workspace, or None if not a member."""
    supabase = get_service_client()
    res = (
        supabase.table("workspace_memberships")
        .select("role, status")
        .eq("workspace_id", workspace_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not res.data:
        return None
    if res.data.get("status") != "active":
        return None
    return res.data.get("role")


def get_user_personal_workspace(user_id: str) -> dict | None:
    """Return the user's personal workspace record, or None if not created yet.

    Uses maybe_single(), not single(): single() raises on zero rows, which
    would crash ensure_personal_workspace's "lazily create on first use" path
    below for any user whose workspace wasn't backfilled at migration time
    (e.g. an account created after the one-time migration 009 backfill ran).
    """
    supabase = get_service_client()
    res = (
        supabase.table("workspaces")
        .select("*")
        .eq("owner_user_id", user_id)
        .eq("type", "personal")
        .maybe_single()
        .execute()
    )
    return res.data if res and res.data else None


def ensure_personal_workspace(user_id: str, email: str = "") -> dict:
    """
    Idempotently create and return a personal workspace for the user.
    Called lazily on first workspace API use.
    """
    existing = get_user_personal_workspace(user_id)
    if existing:
        return existing

    supabase = get_service_client()
    slug = "personal-" + user_id.replace("-", "")
    name = (email.split("@")[0] if email else "My") + "'s Workspace"

    ws_res = supabase.table("workspaces").insert({
        "name":          name,
        "slug":          slug,
        "type":          "personal",
        "owner_user_id": user_id,
        "plan":          "free",
    }).execute()

    workspace = ws_res.data[0] if ws_res.data else None
    if workspace:
        supabase.table("workspace_memberships").insert({
            "workspace_id": workspace["id"],
            "user_id":      user_id,
            "role":         "owner",
        }).execute()

    return workspace or {}


# ── FastAPI dependency factories ───────────────────────────────────────────────

def require_workspace_role(minimum_role: str):
    """
    Returns a FastAPI dependency that:
      1. Authenticates the user.
      2. Resolves the workspace from X-Workspace-ID header or workspace_id query.
      3. Checks the user has >= minimum_role.
      4. Returns (user, workspace_id, role) dict.

    Usage:
        @router.post("/workspaces/{wid}/foo")
        async def foo(wid: str, ctx=Depends(require_workspace_role("editor"))):
            user, workspace_id, role = ctx["user"], ctx["workspace_id"], ctx["role"]
    """
    async def _dep(workspace_id: str, user=Depends(get_required_user)):
        user_id = str(user["id"])
        role = get_workspace_role(user_id, workspace_id)

        if role is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code":       "workspace_not_member",
                    "user_message":     "Bạn không phải thành viên của workspace này.",
                    "retryable":        False,
                    "suggested_action": "Yêu cầu owner workspace mời bạn tham gia.",
                },
            )

        if not has_role(role, minimum_role):
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code":       "insufficient_role",
                    "user_message":     f"Hành động này yêu cầu vai trò '{minimum_role}' trở lên.",
                    "required_role":    minimum_role,
                    "current_role":     role,
                    "retryable":        False,
                    "suggested_action": "Yêu cầu admin workspace cấp quyền cao hơn.",
                },
            )

        return {"user": user, "workspace_id": workspace_id, "role": role}

    return _dep
