"""
Celery Background Tasks
========================
process_video_task  — extract direct link for a single video job
scrape_channel_task — discover videos from a channel/playlist, create jobs, dispatch
"""

from app.core.celery_app import celery_app
from app.core.database import get_supabase_client
from app.core.cache import get_cached_result
from app.core.quotas import check_user_quota, increment_usage
from app.services.downloader import extract_video_info_sync, scrape_channel_entries_sync
from app.utils.helpers import slugify
from app.services.archive_service import create_batch_zip_sync
from typing import Optional
from datetime import datetime, timezone
import os
import shutil

@celery_app.task(name="process_video_task", bind=True, max_retries=2)
def process_video_task(self, job_id: str, url: str, user_id: Optional[str] = None, quality: str = "video", remove_watermark: bool = False):
    """Extract the direct MP4 link for a single video and update Supabase."""
    supabase = get_supabase_client()
    try:
        # Check quotas if user_id provided
        if user_id:
            c_info = check_user_quota(user_id)
            if not c_info["allowed"]:
                supabase.table("download_jobs").update({
                    "status": "failed",
                    "error_message": c_info["message"],
                }).eq("id", job_id).execute()
                return

        # 1. Check cache first
        cached = get_cached_result(url)
        if cached:
            print(f"[Cache Hit] Task - URL: {url}")
            if user_id: increment_usage(user_id)
            title = cached.get("title", "Unknown")
            slug = slugify(title)
            supabase.table("download_jobs").update({
                "status": "success",
                "title": title,
                "slugified_name": slug,
                "direct_mp4_url": cached.get("direct_mp4_url"),
                "error_message": "Tải từ bộ nhớ đệm thành công"
            }).eq("id", job_id).execute()
            return
            
        print(f"[Cache Miss] Task - URL: {url} - Proceeding to extraction")

        # 2. Mark as processing
        supabase.table("download_jobs").update(
            {"status": "processing"}
        ).eq("id", job_id).execute()

        # 3. Extract video info
        info = extract_video_info_sync(url, quality, remove_watermark)

        # Generate slugified name
        title = info.get("title", "Unknown")
        slug = slugify(title)

        # Increment quota usage on successful fetch
        if user_id: increment_usage(user_id)

        # 4. Mark as success
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # In bulk mode, if we downloaded the file locally (e.g. TikTok/Douyin or Audio),
        # we pass the local path via the direct_mp4_url column since Supabase schema lacks local path columns.
        best_url = info.get("local_file_path") or info.get("local_mp3_path") or info.get("direct_mp4_url")
        
        supabase.table("download_jobs").update({
            "status": "success",
            "title": title,
            "slugified_name": slug,
            "direct_mp4_url": best_url,
            "file_size_mb": info.get("file_size_mb", 0),
            "created_at": now_iso
        }).eq("id", job_id).execute()

    except Exception as e:
        error_msg = str(e)[:500]
        # Translate common errors to user-friendly Vietnamese
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
            error_msg = "⏱ Quá thời gian chờ. Video có thể bị chặn bởi captcha. Vui lòng thử lại."
        elif "private" in error_msg.lower() or "riêng tư" in error_msg.lower():
            error_msg = "🔒 Video ở chế độ riêng tư, không thể tải."
        elif "not found" in error_msg.lower() or "404" in error_msg:
            error_msg = "🚫 Video không tồn tại hoặc đã bị xóa."
        elif "403" in error_msg or "forbidden" in error_msg.lower():
            error_msg = "🛡 Server bị chặn tạm thời. Vui lòng thử lại sau 1 phút."
        supabase.table("download_jobs").update({
            "status": "failed",
            "error_message": error_msg,
        }).eq("id", job_id).execute()


@celery_app.task(name="scrape_channel_task", bind=True)
def scrape_channel_task(self, channel_url: str, batch_id: str, channel_job_id: str, max_videos: int = 20, min_views: int = 0, user_id: Optional[str] = None):
    """
    Scrape a channel/playlist URL:
    1. Discover all video entries.
    2. Filter them according to limits.
    3. Insert each as a pending job in Supabase.
    4. Dispatch process_video_task for each.
    5. Update the placeholder channel_job_id with results.
    """
    supabase = get_supabase_client()

    try:
        result = scrape_channel_entries_sync(channel_url, max_videos, min_views)
        entries = result.get("entries", [])
        total_found = result.get("total_found", 0)
        total_queued = result.get("total_queued", 0)

        if not entries:
            # Insert a single failed record to notify the user
            supabase.table("download_jobs").update({
                "status": "failed",
                "error_message": f"Đã quét 0/0 videos. Không tìm thấy video hợp lệ nào.",
            }).eq("id", channel_job_id).execute()
            return

        for entry in entries:
            video_url = entry.get("url", "")
            if not video_url:
                continue

            # Create pending job
            response = supabase.table("download_jobs").insert({
                "batch_id": batch_id,
                "original_url": video_url,
                "status": "pending",
            }).execute()

            job_id = response.data[0]["id"]

            # Dispatch extraction task
            process_video_task.delay(job_id, video_url, user_id)

        # Update the placeholder job with the summary! We mark it as success but no direct_url.
        summary_msg = f"Đã tìm thấy {total_found} video, {total_queued} video đạt điều kiện lọc và đang được đưa vào hàng chờ."
        supabase.table("download_jobs").update({
            "status": "success",
            "error_message": summary_msg, 
        }).eq("id", channel_job_id).execute()

    except Exception as e:
        error_msg = str(e)[:500]
        supabase.table("download_jobs").update({
            "status": "failed",
            "error_message": f"Channel scrape failed: {error_msg}",
        }).eq("id", channel_job_id).execute()

@celery_app.task(name="create_zip_task", bind=True)
def create_zip_task(self, batch_id: str, zip_job_id: str):
    supabase = get_supabase_client()
    try:
        supabase.table("download_jobs").update({"status": "processing"}).eq("id", zip_job_id).execute()
        result = create_batch_zip_sync(batch_id)
        
        now_iso = datetime.now(timezone.utc).isoformat()
        if result.get("success"):
            zip_size = result.get("zip_size_mb", 0)
            total_files = result.get("total_files", 0)
            supabase.table("download_jobs").update({
                "status": "success",
                "title": f"Batch {batch_id[:8]} — {total_files} files",
                "slugified_name": f"batch_{batch_id}",
                "direct_mp4_url": result.get("zip_path"),
                "file_size_mb": zip_size,
                "created_at": now_iso
            }).eq("id", zip_job_id).execute()
        else:
            supabase.table("download_jobs").update({
                "status": "failed",
                "error_message": result.get("error", "Unknown zip error"),
            }).eq("id", zip_job_id).execute()
    except Exception as e:
        error_msg = str(e)[:500]
        supabase.table("download_jobs").update({
            "status": "failed",
            "error_message": f"Zip creation failed: {error_msg}",
        }).eq("id", zip_job_id).execute()

@celery_app.task(name="delete_batch_resources", bind=True)
def delete_batch_resources(self, batch_id: str):
    """Deletes the batch temp folder and the batch zip file."""
    DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloads")
    batch_dir = os.path.join(DOWNLOAD_DIR, batch_id)
    zip_file = os.path.join(DOWNLOAD_DIR, f"batch_{batch_id}.zip")
    
    if os.path.exists(batch_dir):
        try:
            shutil.rmtree(batch_dir)
        except Exception as e:
            print(f"Failed to delete batch dir {batch_id}: {e}")
            
    if os.path.exists(zip_file):
        try:
            os.remove(zip_file)
        except Exception as e:
            print(f"Failed to delete zip file {zip_file}: {e}")

@celery_app.task(name="delete_local_file", bind=True)
def delete_local_file(self, filepath: str):
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception as e:
            print(f"Failed to delete {filepath}: {e}")

@celery_app.task(name="periodic_cleanup_downloads", bind=True)
def periodic_cleanup_downloads(self):
    """Scan the downloads directory and delete files/folders older than 20 minutes."""
    print("[Cron] Starting periodic cleanup of downloads directory...")
    DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloads")
    if not os.path.exists(DOWNLOAD_DIR):
        return

    import time
    current_time = time.time()
    expiry_seconds = 20 * 60  # 20 minutes
    
    deleted_count = 0
    
    for item in os.listdir(DOWNLOAD_DIR):
        item_path = os.path.join(DOWNLOAD_DIR, item)
        try:
            # Get modified time
            mtime = os.path.getmtime(item_path)
            
            if current_time - mtime > expiry_seconds:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
                deleted_count += 1
                print(f"[Cron] Deleted expired resource: {item_path}")
        except Exception as e:
            print(f"[Cron] Failed to process {item_path}: {e}")
            
    print(f"[Cron] Periodic cleanup finished. Deleted {deleted_count} items.")
