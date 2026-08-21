"""
Bulk-delete alerting
====================

A public endpoint deleted every row in download_jobs, and nothing anywhere
recorded that it had happened. The table was found empty weeks later, with no
way to tell whether it was a bug, a cleanup task, or someone hitting the URL —
and no way to tell WHEN, which is what makes a restore-from-backup decision
possible.

This is the missing half of that fix: a large deletion now leaves a trail.

  record_bulk_delete("download_jobs", deleted=1240, actor=..., request=...)

writes an audit_logs row unconditionally (so the count and the actor survive
even for routine, correct deletions) and fires a Telegram alert once the row
count crosses BULK_DELETE_ALERT_THRESHOLD.

Never raises. A deletion that already happened must not turn into a 500 just
because the alert could not be sent.
"""

from __future__ import annotations

import os
import threading
from typing import Any

# A user clearing their own history is normally a handful of rows. Anything at
# this scale is either an unusually heavy account or something going wrong, and
# both are worth a message.
ALERT_THRESHOLD = int(os.getenv("BULK_DELETE_ALERT_THRESHOLD", "100"))


def record_bulk_delete(
    table: str,
    *,
    deleted: int,
    scope: str,
    request: Any = None,
    user: dict[str, Any] | None = None,
    actor: str | None = None,
) -> None:
    """
    Record a multi-row deletion.

    table   — the table rows were removed from
    deleted — how many rows the database reported deleting
    scope   — what the delete was filtered to, e.g. "user_id=abc123". Written
              verbatim into the audit metadata: "which rows" is the first
              question anyone asks afterwards.
    """
    if deleted <= 0:
        return

    try:
        from app.core.audit import log_from_request, log_event

        meta = {"table": table, "deleted": deleted, "scope": scope}
        if request is not None:
            log_from_request(
                request, "data.bulk_delete", user=user,
                resource_type=table, metadata=meta,
            )
        else:
            log_event(
                "data.bulk_delete", actor_email=actor,
                resource_type=table, metadata=meta,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[BulkDelete] audit write failed: {exc}")

    if deleted < ALERT_THRESHOLD:
        return

    who = actor or (user or {}).get("email") or (user or {}).get("id") or "ẩn danh"

    def _alert() -> None:
        try:
            from app.core.notifications import send_telegram_message_sync

            send_telegram_message_sync(
                "🗑️ <b>Xóa hàng loạt</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"📋 Bảng: <code>{table}</code>\n"
                f"🔢 Số dòng: <b>{deleted}</b>\n"
                f"🎯 Phạm vi: <code>{scope}</code>\n"
                f"👤 Người thực hiện: {who}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[BulkDelete] alert failed: {exc}")

    # Daemon thread, matching app.core.audit: the caller has already committed
    # the delete and is on its way to returning a response.
    threading.Thread(target=_alert, daemon=True).start()
