"""
Audit event logger for VidGrab.

All significant actions are recorded as immutable append-only rows
in the audit_logs table. Logging is fire-and-forget — failures are
swallowed so that log errors never break user-facing requests.

Standard action namespacing: <resource>.<verb>
  e.g. download.created, archive_item.deleted, member.invited,
       role.changed, invite.accepted, schedule.created, webhook.revoked,
       api_key.generated, file.pinned, workspace.settings_changed
"""

from __future__ import annotations

import threading
from typing import Any

_AUDIT_ENABLED = True  # Can be flipped via env at startup


def log_event(
    action: str,
    *,
    actor_user_id: str | None = None,
    actor_email: str | None = None,
    workspace_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """
    Record an audit event. Always non-blocking (runs in a daemon thread).
    Never raises — failure to log must not break the caller.
    """
    if not _AUDIT_ENABLED:
        return

    row = {
        "action":        action,
        "actor_user_id": actor_user_id,
        "actor_email":   actor_email,
        "workspace_id":  workspace_id,
        "resource_type": resource_type,
        "resource_id":   str(resource_id) if resource_id else None,
        "metadata":      metadata or {},
        "ip_address":    ip_address,
        "user_agent":    user_agent,
    }
    # Strip None values to keep rows clean
    row = {k: v for k, v in row.items() if v is not None}

    def _write():
        try:
            from app.core.database import get_service_client
            get_service_client().table("audit_logs").insert(row).execute()
        except Exception as exc:  # noqa: BLE001
            print(f"[Audit] Failed to write audit log: {exc}")

    t = threading.Thread(target=_write, daemon=True)
    t.start()


# ── Convenience helpers ────────────────────────────────────────────────────────

def _extract_ip(request) -> str | None:
    if request is None:
        return None
    from app.core.client_ip import get_client_ip
    return get_client_ip(request)


def log_from_request(request, action: str, user=None, **kwargs) -> None:
    """
    Extract IP + user-agent from a FastAPI Request and call log_event.
    `user` is the dict returned by get_optional_user / get_required_user.
    """
    ip = _extract_ip(request)
    ua = request.headers.get("User-Agent") if request else None
    uid = str(user["id"]) if user else None
    email = user.get("email") if user else None
    log_event(
        action,
        actor_user_id=uid,
        actor_email=email,
        ip_address=ip,
        user_agent=ua,
        **kwargs,
    )


def log_admin_action(
    request,
    action: str,
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Log a privileged admin action.
    actor_email is recorded as 'admin' (no per-admin identity yet).
    Never raises.
    """
    ip = _extract_ip(request)
    ua = request.headers.get("User-Agent") if request else None
    log_event(
        action,
        actor_email="admin",
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata or {},
        ip_address=ip,
        user_agent=ua,
    )


def log_access_denied(
    request,
    endpoint: str,
    *,
    reason: str = "unauthorized",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log a denied access attempt to a sensitive endpoint."""
    ip = _extract_ip(request)
    ua = request.headers.get("User-Agent") if request else None
    log_event(
        "access.denied",
        resource_type="endpoint",
        resource_id=endpoint,
        metadata={"reason": reason, **(metadata or {})},
        ip_address=ip,
        user_agent=ua,
    )


def get_recent_admin_actions(limit: int = 50) -> list:
    """Retrieve recent admin audit entries (synchronous, for the admin dashboard)."""
    try:
        from app.core.database import get_service_client
        res = (
            get_service_client()
            .table("audit_logs")
            .select("*")
            .ilike("action", "admin.%")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        print(f"[Audit] Failed to fetch admin actions: {exc}")
        return []


def get_recent_access_denials(limit: int = 50) -> list:
    """Retrieve recent access.denied audit entries (synchronous, for security visibility)."""
    try:
        from app.core.database import get_service_client
        res = (
            get_service_client()
            .table("audit_logs")
            .select("*")
            .eq("action", "access.denied")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        print(f"[Audit] Failed to fetch access denials: {exc}")
        return []
