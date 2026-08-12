"""
Celery tasks for Personal Video Archive (Phase 8).
"""

from app.core.celery_app import celery_app


@celery_app.task(name="auto_archive_task", ignore_result=True)
def auto_archive_task(job_id: str, user_id: str, meta: dict):
    """
    Auto-create an archive_items entry for every successful logged-in download.
    Idempotent: skips if the job is already archived.
    """
    from app.core.database import get_service_client
    supabase = get_service_client()

    try:
        existing = (
            supabase.table("archive_items")
            .select("id")
            .eq("job_id", job_id)
            .execute()
        )
        if existing.data:
            return
    except Exception:
        pass

    # Parse YYYYMMDD → ISO date
    upload_date = None
    raw_date = meta.get("upload_date", "")
    if raw_date and len(str(raw_date)) == 8:
        try:
            d = str(raw_date)
            upload_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        except Exception:
            pass

    tags = meta.get("tags") or []
    if isinstance(tags, list):
        tags = [str(t) for t in tags[:20]]
    else:
        tags = []

    description = meta.get("description") or ""
    if len(description) > 500:
        description = description[:497] + "..."

    try:
        supabase.table("archive_items").insert({
            "user_id":            user_id,
            "job_id":             job_id,
            "original_url":       meta.get("url", ""),
            "platform":           meta.get("platform", "other"),
            "title":              meta.get("title") or meta.get("url") or "Untitled",
            "creator_handle":     meta.get("creator_handle"),
            "creator_name":       meta.get("creator_name"),
            "duration_seconds":   meta.get("duration"),
            "upload_date":        upload_date,
            "thumbnail_url":      meta.get("thumbnail"),
            "language_detected":  meta.get("language"),
            "hashtags":           tags,
            "description_snippet": description or None,
            "view_count":         meta.get("view_count"),
            "is_downloaded":      True,
        }).execute()
    except Exception as e:
        print(f"[Archive] Auto-archive insert failed for job {job_id}: {e}")
