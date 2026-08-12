"""
Storage Governance API — Phase 9
==================================
Endpoints:
  GET   /api/v1/storage/info                  user's storage usage summary
  POST  /api/v1/storage/pin/{job_id}          pin a file (extend retention)
  DELETE /api/v1/storage/pin/{job_id}         unpin a file
  GET   /api/v1/storage/expiring              list files expiring within 48h
  DELETE /api/v1/storage/jobs/{job_id}/file   delete file only (keep metadata)
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.quotas import get_user_tier

router = APIRouter()

# Pin limits per tier
_PIN_LIMITS: Dict[str, int] = {"free": 3, "pro": 50}
# Pin retention window in days per tier
_PIN_DAYS: Dict[str, int] = {"free": 7, "pro": 30}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── Storage info ──────────────────────────────────────────────────────

@router.get("/storage/info")
async def get_storage_info(user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id  = str(user["id"])
    tier     = get_user_tier(user_id)
    pin_limit = _PIN_LIMITS.get(tier, 3)
    pin_days  = _PIN_DAYS.get(tier, 7)

    # Count pinned files
    pinned_res = (
        supabase.table("download_jobs")
        .select("id, storage_bytes, pin_expires_at", count="exact")
        .eq("user_id", user_id)
        .eq("file_retention_status", "pinned")
        .execute()
    )
    pinned_count = pinned_res.count or 0
    pinned_bytes = sum((r.get("storage_bytes") or 0) for r in (pinned_res.data or []))
    next_expiring = min(
        (r["pin_expires_at"] for r in (pinned_res.data or []) if r.get("pin_expires_at")),
        default=None,
    )

    # Count temporary (active) files
    active_res = (
        supabase.table("download_jobs")
        .select("id, storage_bytes", count="exact")
        .eq("user_id", user_id)
        .eq("file_retention_status", "temporary")
        .execute()
    )
    active_count = active_res.count or 0
    active_bytes = sum((r.get("storage_bytes") or 0) for r in (active_res.data or []))

    # Archive item count
    archive_res = (
        supabase.table("archive_items")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )

    return {
        "tier": tier,
        "pinned": {
            "count":       pinned_count,
            "limit":       pin_limit,
            "bytes":       pinned_bytes,
            "bytes_mb":    round(pinned_bytes / (1024 ** 2), 2),
            "next_expiring": next_expiring,
            "retention_days": pin_days,
        },
        "active_temporary": {
            "count": active_count,
            "bytes": active_bytes,
            "bytes_mb": round(active_bytes / (1024 ** 2), 2),
        },
        "archive_items_total": archive_res.count or 0,
        "policy": {
            "default_retention_hours": 24,
            "pin_retention_days": pin_days,
            "pin_limit": pin_limit,
        },
    }


# ── Pin / Unpin ───────────────────────────────────────────────────────

@router.post("/storage/pin/{job_id}")
async def pin_file(job_id: str, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id  = str(user["id"])
    tier     = get_user_tier(user_id)
    pin_limit = _PIN_LIMITS.get(tier, 3)
    pin_days  = _PIN_DAYS.get(tier, 7)

    # Verify job belongs to user
    job_res = (
        supabase.table("download_jobs")
        .select("id, file_retention_status, status, local_file_path")
        .eq("id", job_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not job_res.data:
        raise HTTPException(404, "Job không tồn tại.")

    job = job_res.data
    if job["file_retention_status"] == "expired_metadata_only":
        raise HTTPException(409, detail={
            "code": "file_already_expired",
            "message": "File đã hết hạn. Tải lại để ghim phiên bản mới.",
        })

    if job["file_retention_status"] == "pinned":
        # Already pinned — just refresh expiry
        pin_expires = _iso(_now() + timedelta(days=pin_days))
        supabase.table("download_jobs").update({
            "pin_expires_at": pin_expires,
        }).eq("id", job_id).execute()
        return {"message": f"Đã gia hạn ghim đến {pin_expires}.", "pin_expires_at": pin_expires}

    # Check pin limit
    pinned_res = (
        supabase.table("download_jobs")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .eq("file_retention_status", "pinned")
        .execute()
    )
    if (pinned_res.count or 0) >= pin_limit:
        raise HTTPException(403, detail={
            "code": "pin_limit_reached",
            "message": f"Bạn đã ghim {pinned_res.count}/{pin_limit} file. Bỏ ghim một file trước.",
            "limit": pin_limit,
        })

    pin_now     = _iso(_now())
    pin_expires = _iso(_now() + timedelta(days=pin_days))

    supabase.table("download_jobs").update({
        "file_retention_status": "pinned",
        "pinned_at":     pin_now,
        "pin_expires_at": pin_expires,
        "file_expires_at": pin_expires,  # extend file expiry
    }).eq("id", job_id).execute()

    # Sync to archive_items if exists
    try:
        supabase.table("archive_items").update({
            "file_retention_status": "pinned",
            "pin_expires_at": pin_expires,
        }).eq("file_job_id", job_id).execute()
    except Exception:
        pass

    return {
        "message": f"Đã ghim. File sẽ được giữ đến {pin_expires}.",
        "pin_expires_at": pin_expires,
        "retention_days": pin_days,
    }


@router.delete("/storage/pin/{job_id}")
async def unpin_file(job_id: str, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id  = str(user["id"])

    job_res = (
        supabase.table("download_jobs")
        .select("id, file_retention_status")
        .eq("id", job_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not job_res.data:
        raise HTTPException(404, "Job không tồn tại.")

    new_expires = _iso(_now() + timedelta(hours=24))
    supabase.table("download_jobs").update({
        "file_retention_status": "temporary",
        "pinned_at":     None,
        "pin_expires_at": None,
        "file_expires_at": new_expires,
    }).eq("id", job_id).execute()

    try:
        supabase.table("archive_items").update({
            "file_retention_status": "temporary",
            "pin_expires_at": None,
        }).eq("file_job_id", job_id).execute()
    except Exception:
        pass

    return {"message": "Đã bỏ ghim. File sẽ hết hạn sau 24 giờ.", "file_expires_at": new_expires}


# ── Expiring files ────────────────────────────────────────────────────

@router.get("/storage/expiring")
async def list_expiring(hours: int = 48, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id  = str(user["id"])
    cutoff   = _iso(_now() + timedelta(hours=max(1, min(hours, 168))))

    # Pinned files expiring soon
    pinned_res = (
        supabase.table("download_jobs")
        .select("id, title, platform, pin_expires_at, storage_bytes, original_url")
        .eq("user_id", user_id)
        .eq("file_retention_status", "pinned")
        .lte("pin_expires_at", cutoff)
        .order("pin_expires_at")
        .limit(20)
        .execute()
    )

    # Temporary files expiring soon
    temp_res = (
        supabase.table("download_jobs")
        .select("id, title, platform, file_expires_at, storage_bytes, original_url")
        .eq("user_id", user_id)
        .eq("file_retention_status", "temporary")
        .lte("file_expires_at", cutoff)
        .order("file_expires_at")
        .limit(20)
        .execute()
    )

    return {
        "pinned_expiring":    pinned_res.data or [],
        "temporary_expiring": temp_res.data or [],
        "cutoff_hours": hours,
    }


# ── Delete file only ──────────────────────────────────────────────────

@router.delete("/storage/jobs/{job_id}/file", status_code=204)
async def delete_file_only(job_id: str, user=Depends(get_required_user)):
    """
    Delete binary file from disk but keep all metadata, notes, archive entry.
    This is 'Remove file only' mode.
    """
    supabase = get_service_client()
    user_id  = str(user["id"])

    job_res = (
        supabase.table("download_jobs")
        .select("id, local_file_path, local_mp3_path, file_retention_status")
        .eq("id", job_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not job_res.data:
        raise HTTPException(404, "Job không tồn tại.")

    job = job_res.data
    if job["file_retention_status"] == "expired_metadata_only":
        raise HTTPException(409, detail={
            "code": "file_already_expired",
            "message": "File đã được xóa trước đó.",
        })

    # Delete from disk
    import os
    for field in ("local_file_path", "local_mp3_path"):
        fpath = job.get(field)
        if fpath and os.path.exists(fpath):
            try:
                os.remove(fpath)
            except Exception as e:
                print(f"[Storage] File delete error {fpath}: {e}")

    # Update status — keep metadata
    supabase.table("download_jobs").update({
        "file_retention_status": "expired_metadata_only",
        "local_file_path": None,
        "local_mp3_path": None,
        "direct_mp4_url": None,
    }).eq("id", job_id).execute()

    # Sync archive_items
    try:
        supabase.table("archive_items").update({
            "file_retention_status": "expired_metadata_only",
        }).eq("file_job_id", job_id).execute()
    except Exception:
        pass
