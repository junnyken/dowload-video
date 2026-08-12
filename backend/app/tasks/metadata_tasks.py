"""
Metadata extraction storage task.
Triggered after every successful download — does NOT block the response.
"""
from app.core.celery_app import celery_app
from app.core.database import get_service_client


@celery_app.task(name="store_job_metadata_task", ignore_result=True)
def store_job_metadata_task(job_id: str, meta: dict):
    """
    Upsert yt-dlp metadata fields into job_metadata table.
    Failures are silently logged — never raise.
    """
    try:
        supabase = get_service_client()

        # Parse upload_date YYYYMMDD → ISO date string (e.g. "20240115" → "2024-01-15")
        ud = str(meta.get("upload_date") or "")
        upload_date = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}" if len(ud) == 8 else None

        desc = (meta.get("description") or "")[:300] or None

        hashtags = [str(t) for t in (meta.get("tags") or []) if t][:20] or None
        categories = [str(c) for c in (meta.get("categories") or []) if c][:10] or None

        row = {
            "job_id":              job_id,
            "creator_handle":      meta.get("uploader_id") or meta.get("channel_id"),
            "creator_name":        meta.get("uploader") or meta.get("channel"),
            "duration_seconds":    meta.get("duration"),
            "view_count":          meta.get("view_count"),
            "like_count":          meta.get("like_count"),
            "upload_date":         upload_date,
            "language_detected":   meta.get("language"),
            "hashtags":            hashtags,
            "categories":          categories,
            "description_snippet": desc,
            "thumbnail_url":       meta.get("thumbnail"),
        }

        # Remove None values to avoid overwriting existing data on partial updates
        row = {k: v for k, v in row.items() if v is not None}
        row["job_id"] = job_id  # always include

        supabase.table("job_metadata").upsert(row, on_conflict="job_id").execute()
    except Exception as e:
        print(f"[Metadata] Failed to store metadata for job {job_id}: {e}")
