"""
Personal Video Archive API — Phase 8
======================================
Endpoints:
  GET    /api/v1/archive                               list (paginated, filtered)
  POST   /api/v1/archive                               add item manually
  GET    /api/v1/archive/export                        export JSON / CSV
  GET    /api/v1/archive/search                        full-text search
  GET    /api/v1/archive/{item_id}                     single item
  PATCH  /api/v1/archive/{item_id}                     update star/notes/tags
  DELETE /api/v1/archive/{item_id}                     remove
  POST   /api/v1/archive/{item_id}/re-download         re-trigger download
  GET    /api/v1/archive/collections                   list collections
  POST   /api/v1/archive/collections                   create
  PATCH  /api/v1/archive/collections/{coll_id}        edit
  DELETE /api/v1/archive/collections/{coll_id}        delete (not items)
  POST   /api/v1/archive/collections/{coll_id}/add    add item
  DELETE /api/v1/archive/collections/{coll_id}/remove/{item_id}
"""

import csv
import io
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth_middleware import get_required_user
from app.core.database import get_service_client
from app.core.quotas import get_user_tier

router = APIRouter()

_FREE_LIMIT = 100


# ── Models ────────────────────────────────────────────────────────────

class AddArchiveItemRequest(BaseModel):
    original_url: str
    platform: str = "other"
    title: str
    creator_handle: Optional[str] = None
    creator_name: Optional[str] = None
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    job_id: Optional[str] = None


class PatchArchiveItemRequest(BaseModel):
    is_starred: Optional[bool] = None
    user_notes: Optional[str] = None
    tags_user: Optional[List[str]] = None
    title: Optional[str] = None


class CreateCollectionRequest(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None


class PatchCollectionRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────

def _get_platform(url: str) -> str:
    url = url.lower()
    if "youtube.com" in url or "youtu.be" in url: return "youtube"
    if "facebook.com" in url or "fb.watch" in url: return "facebook"
    if "tiktok.com" in url:  return "tiktok"
    if "instagram.com" in url: return "instagram"
    if "twitter.com" in url or "x.com" in url: return "twitter"
    if "threads.net" in url or "threads.com" in url: return "threads"
    if "reddit.com" in url: return "reddit"
    if "pinterest.com" in url or "pin.it" in url: return "pinterest"
    return "other"


def _check_archive_limit(supabase, user_id: str, tier: str):
    if tier == "pro":
        return
    count_res = (
        supabase.table("archive_items")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .execute()
    )
    count = count_res.count or 0
    if count >= _FREE_LIMIT:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "archive_limit_reached",
                "message": f"Free plan giới hạn {_FREE_LIMIT} archive items. Nâng cấp Pro để lưu không giới hạn.",
                "limit": _FREE_LIMIT,
            },
        )


# ── Archive Item CRUD ─────────────────────────────────────────────────

@router.get("/archive")
async def list_archive(
    request: Request,
    platform: Optional[str] = None,
    is_starred: Optional[bool] = None,
    collection_id: Optional[str] = None,
    language: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_duration: Optional[int] = None,
    max_duration: Optional[int] = None,
    file_status: Optional[str] = None,   # available | pinned | expired
    sort: str = "archived_at",
    page: int = 1,
    limit: int = 20,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])
    offset = (page - 1) * max(1, min(limit, 100))

    q = (
        supabase.table("archive_items")
        .select("*", count="exact")
        .eq("user_id", user_id)
    )

    if platform:
        q = q.eq("platform", platform)
    if is_starred is not None:
        q = q.eq("is_starred", is_starred)
    if language:
        q = q.eq("language_detected", language)
    if date_from:
        q = q.gte("archived_at", date_from)
    if date_to:
        q = q.lte("archived_at", date_to)
    if min_duration is not None:
        q = q.gte("duration_seconds", min_duration)
    if max_duration is not None:
        q = q.lte("duration_seconds", max_duration)
    if collection_id:
        q = q.contains("collection_ids", [collection_id])
    if file_status == "available":
        q = q.in_("file_retention_status", ["temporary", "pinned"])
    elif file_status == "pinned":
        q = q.eq("file_retention_status", "pinned")
    elif file_status == "expired":
        q = q.eq("file_retention_status", "expired_metadata_only")

    sort_col = {
        "archived_at": "archived_at",
        "upload_date": "upload_date",
        "duration": "duration_seconds",
        "most_viewed": "view_count",
        "title": "title",
    }.get(sort, "archived_at")

    q = q.order(sort_col, desc=True).range(offset, offset + limit - 1)

    res = q.execute()
    return {
        "items": res.data or [],
        "total": res.count or 0,
        "page": page,
        "limit": limit,
    }


@router.post("/archive", status_code=201)
async def add_archive_item(
    body: AddArchiveItemRequest,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])
    tier = get_user_tier(user_id)
    _check_archive_limit(supabase, user_id, tier)

    platform = body.platform if body.platform != "other" else _get_platform(body.original_url)

    row = {
        "user_id":        user_id,
        "original_url":   body.original_url,
        "platform":       platform,
        "title":          body.title,
        "creator_handle": body.creator_handle,
        "creator_name":   body.creator_name,
        "duration_seconds": body.duration_seconds,
        "thumbnail_url":  body.thumbnail_url,
        "is_downloaded":  False,
    }
    if body.job_id:
        row["job_id"] = body.job_id

    res = supabase.table("archive_items").insert(row).execute()
    if not res.data:
        raise HTTPException(500, "Không thể lưu vào archive.")
    return res.data[0]


@router.get("/archive/export")
async def export_archive(
    format: str = Query("json", regex="^(json|csv)$"),
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])

    res = (
        supabase.table("archive_items")
        .select("*")
        .eq("user_id", user_id)
        .order("archived_at", desc=True)
        .execute()
    )
    items = res.data or []

    # Attach collection names
    col_res = (
        supabase.table("archive_collections")
        .select("id,name")
        .eq("user_id", user_id)
        .execute()
    )
    col_map = {c["id"]: c["name"] for c in (col_res.data or [])}

    if format == "json":
        for item in items:
            ids = item.get("collection_ids") or []
            item["collections"] = [col_map.get(cid, cid) for cid in ids]

        content = json.dumps({"archive": items}, ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=vidgrab-archive.json"},
        )

    # CSV
    output = io.StringIO()
    fields = [
        "title", "platform", "creator_name", "creator_handle",
        "duration_seconds", "upload_date", "archived_at",
        "collections", "tags_user", "user_notes", "original_url",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        ids = item.get("collection_ids") or []
        item["collections"] = "|".join(col_map.get(cid, cid) for cid in ids)
        tags = item.get("tags_user") or []
        item["tags_user"] = "|".join(tags)
        writer.writerow({k: item.get(k, "") for k in fields})

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vidgrab-archive.csv"},
    )


@router.get("/archive/search")
async def search_archive(
    q: str = Query(..., min_length=2),
    limit: int = 20,
    user=Depends(get_required_user),
):
    """Full-text search across title, creator, hashtags, user_notes, tags_user."""
    supabase = get_service_client()
    user_id = str(user["id"])
    query = q.strip().lower()

    res = (
        supabase.table("archive_items")
        .select("*")
        .eq("user_id", user_id)
        .order("archived_at", desc=True)
        .limit(300)
        .execute()
    )
    items = res.data or []

    def _matches(item: dict) -> bool:
        haystack = " ".join([
            item.get("title") or "",
            item.get("creator_handle") or "",
            item.get("creator_name") or "",
            item.get("user_notes") or "",
            " ".join(item.get("hashtags") or []),
            " ".join(item.get("tags_user") or []),
        ]).lower()
        return query in haystack

    matched = [i for i in items if _matches(i)][:limit]
    return {"items": matched, "total": len(matched)}


@router.get("/archive/{item_id}")
async def get_archive_item(item_id: str, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id = str(user["id"])
    res = (
        supabase.table("archive_items")
        .select("*")
        .eq("id", item_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Archive item không tồn tại.")
    # Touch last_accessed_at
    try:
        from datetime import datetime, timezone
        supabase.table("archive_items").update({
            "last_accessed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", item_id).execute()
    except Exception:
        pass
    return res.data


@router.patch("/archive/{item_id}")
async def patch_archive_item(
    item_id: str,
    body: PatchArchiveItemRequest,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])

    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Không có trường nào để cập nhật.")
    if "tags_user" in updates:
        updates["tags_user"] = [str(t)[:50] for t in updates["tags_user"][:20]]

    res = (
        supabase.table("archive_items")
        .update(updates)
        .eq("id", item_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Archive item không tồn tại.")
    return res.data[0]


@router.delete("/archive/{item_id}", status_code=204)
async def delete_archive_item(
    item_id: str,
    mode: str = "archive_only",   # archive_only | file_only | everything
    user=Depends(get_required_user),
):
    """
    Three deletion modes:
    - file_only     : delete binary from disk, keep metadata/notes/collections
    - archive_only  : delete archive entry only, keep job execution record
    - everything    : delete archive + notes + collection memberships + attempt file delete
    """
    supabase = get_service_client()
    user_id = str(user["id"])

    item_res = (
        supabase.table("archive_items")
        .select("collection_ids, file_job_id")
        .eq("id", item_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not item_res.data:
        raise HTTPException(404, "Archive item không tồn tại.")
    item = item_res.data[0]

    if mode == "file_only":
        # Delete binary only — delegate to storage endpoint logic
        job_id = item.get("file_job_id")
        if job_id:
            import os as _os
            job_res = supabase.table("download_jobs").select(
                "local_file_path, local_mp3_path, file_retention_status"
            ).eq("id", job_id).single().execute()
            if job_res.data:
                for field in ("local_file_path", "local_mp3_path"):
                    fp = job_res.data.get(field)
                    if fp and _os.path.exists(fp):
                        try: _os.remove(fp)
                        except Exception: pass
                supabase.table("download_jobs").update({
                    "file_retention_status": "expired_metadata_only",
                    "local_file_path": None, "local_mp3_path": None, "direct_mp4_url": None,
                }).eq("id", job_id).execute()
        supabase.table("archive_items").update({
            "file_retention_status": "expired_metadata_only",
        }).eq("id", item_id).execute()
        return

    if mode == "everything":
        # Delete notes, collection memberships, then archive item
        supabase.table("clip_notes").delete().eq("job_id", item.get("file_job_id", "00000000-0000-0000-0000-000000000000")).execute()
        # Try to delete binary too
        job_id = item.get("file_job_id")
        if job_id:
            import os as _os
            job_res = supabase.table("download_jobs").select(
                "local_file_path, local_mp3_path"
            ).eq("id", job_id).single().execute()
            if job_res.data:
                for field in ("local_file_path", "local_mp3_path"):
                    fp = job_res.data.get(field)
                    if fp and _os.path.exists(fp):
                        try: _os.remove(fp)
                        except Exception: pass
                supabase.table("download_jobs").update({
                    "file_retention_status": "expired_metadata_only",
                    "local_file_path": None, "local_mp3_path": None, "direct_mp4_url": None,
                }).eq("id", job_id).execute()

    # archive_only + everything: remove archive row (cascades collection_ids array)
    col_ids = item.get("collection_ids") or []
    supabase.table("archive_items").delete().eq("id", item_id).eq("user_id", user_id).execute()

    # Refresh collection item counts
    for cid in col_ids:
        try:
            count_res = supabase.table("archive_items").select("id", count="exact").eq(
                "user_id", user_id).contains("collection_ids", [cid]).execute()
            supabase.table("archive_collections").update({
                "item_count": count_res.count or 0
            }).eq("id", cid).execute()
        except Exception:
            pass


@router.post("/archive/{item_id}/re-download", status_code=202)
async def re_download_archive_item(item_id: str, user=Depends(get_required_user)):
    """Re-trigger download for an archived URL."""
    supabase = get_service_client()
    user_id = str(user["id"])

    item_res = (
        supabase.table("archive_items")
        .select("original_url, platform, title")
        .eq("id", item_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not item_res.data:
        raise HTTPException(404, "Archive item không tồn tại.")

    item = item_res.data
    url = item["original_url"]

    # Create a new download job
    from app.tasks.video_tasks import process_video_task
    import uuid

    job_res = supabase.table("download_jobs").insert({
        "original_url": url,
        "status": "queued",
        "user_id": user_id,
    }).execute()
    if not job_res.data:
        raise HTTPException(500, "Không thể tạo download job.")

    new_job_id = str(job_res.data[0]["id"])
    process_video_task.delay(job_id=new_job_id, url=url, user_id=user_id)

    return {"job_id": new_job_id, "message": "Đang tải lại. Kiểm tra tiến trình trong lịch sử."}


# ── Collections ───────────────────────────────────────────────────────

@router.get("/archive/collections")
async def list_collections(user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id = str(user["id"])
    res = (
        supabase.table("archive_collections")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=False)
        .execute()
    )
    return {"collections": res.data or []}


@router.post("/archive/collections", status_code=201)
async def create_collection(body: CreateCollectionRequest, user=Depends(get_required_user)):
    supabase = get_service_client()
    user_id = str(user["id"])

    # Limit: Free = 10, Pro = unlimited
    tier = get_user_tier(user_id)
    if tier == "free":
        count_res = (
            supabase.table("archive_collections")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )
        if (count_res.count or 0) >= 10:
            raise HTTPException(403, detail={
                "code": "collection_limit",
                "message": "Free plan giới hạn 10 collections. Nâng cấp Pro để tạo thêm.",
            })

    res = supabase.table("archive_collections").insert({
        "user_id":     user_id,
        "name":        body.name,
        "description": body.description,
        "color":       body.color,
        "icon":        body.icon,
    }).execute()
    if not res.data:
        raise HTTPException(500, "Không thể tạo collection.")
    return res.data[0]


@router.patch("/archive/collections/{coll_id}")
async def patch_collection(
    coll_id: str,
    body: PatchCollectionRequest,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        raise HTTPException(400, "Không có trường nào để cập nhật.")
    from datetime import datetime, timezone
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    res = (
        supabase.table("archive_collections")
        .update(updates)
        .eq("id", coll_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not res.data:
        raise HTTPException(404, "Collection không tồn tại.")
    return res.data[0]


@router.delete("/archive/collections/{coll_id}", status_code=204)
async def delete_collection(coll_id: str, user=Depends(get_required_user)):
    """Delete collection but NOT its items. Items remain in archive."""
    supabase = get_service_client()
    user_id = str(user["id"])

    # Remove collection_id from all items that reference this collection
    items_res = (
        supabase.table("archive_items")
        .select("id, collection_ids")
        .eq("user_id", user_id)
        .contains("collection_ids", [coll_id])
        .execute()
    )
    for item in (items_res.data or []):
        new_ids = [c for c in (item.get("collection_ids") or []) if c != coll_id]
        try:
            supabase.table("archive_items").update({
                "collection_ids": new_ids
            }).eq("id", item["id"]).execute()
        except Exception:
            pass

    supabase.table("archive_collections").delete().eq("id", coll_id).eq("user_id", user_id).execute()


@router.post("/archive/collections/{coll_id}/add")
async def add_to_collection(
    coll_id: str,
    item_id: str,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])

    # Verify collection belongs to user
    col_res = (
        supabase.table("archive_collections")
        .select("id")
        .eq("id", coll_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not col_res.data:
        raise HTTPException(404, "Collection không tồn tại.")

    # Fetch current collection_ids of the item
    item_res = (
        supabase.table("archive_items")
        .select("collection_ids")
        .eq("id", item_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not item_res.data:
        raise HTTPException(404, "Archive item không tồn tại.")

    current_ids = item_res.data.get("collection_ids") or []
    if coll_id not in current_ids:
        current_ids.append(coll_id)
        supabase.table("archive_items").update({
            "collection_ids": current_ids
        }).eq("id", item_id).execute()

        # Increment collection item_count
        count_res = (
            supabase.table("archive_items")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .contains("collection_ids", [coll_id])
            .execute()
        )
        supabase.table("archive_collections").update({
            "item_count": count_res.count or 0
        }).eq("id", coll_id).execute()

    return {"message": "Đã thêm vào collection."}


@router.delete("/archive/collections/{coll_id}/remove/{item_id}", status_code=204)
async def remove_from_collection(
    coll_id: str,
    item_id: str,
    user=Depends(get_required_user),
):
    supabase = get_service_client()
    user_id = str(user["id"])

    item_res = (
        supabase.table("archive_items")
        .select("collection_ids")
        .eq("id", item_id)
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    if not item_res.data:
        raise HTTPException(404, "Archive item không tồn tại.")

    current_ids = item_res.data.get("collection_ids") or []
    new_ids = [c for c in current_ids if c != coll_id]
    supabase.table("archive_items").update({
        "collection_ids": new_ids
    }).eq("id", item_id).execute()

    # Update count
    count_res = (
        supabase.table("archive_items")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .contains("collection_ids", [coll_id])
        .execute()
    )
    try:
        supabase.table("archive_collections").update({
            "item_count": count_res.count or 0
        }).eq("id", coll_id).execute()
    except Exception:
        pass
