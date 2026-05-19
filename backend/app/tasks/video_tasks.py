"""
Celery Background Tasks
========================
process_video_task  — extract direct link for a single video job
scrape_channel_task — discover videos from a channel/playlist, create jobs, dispatch
create_zip_task     — create ZIP file from batch downloads + send Telegram notification
daily_summary_task  — send daily operations report via Telegram
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
        
        update_data = {
            "status": "success",
            "title": title,
            "slugified_name": slug,
            "direct_mp4_url": best_url,
            "created_at": now_iso
        }
        # Try with file_size_mb first (column may not exist yet)
        try:
            update_data["file_size_mb"] = info.get("file_size_mb", 0)
            supabase.table("download_jobs").update(update_data).eq("id", job_id).execute()
        except Exception:
            # Fallback: update without file_size_mb if column missing
            update_data.pop("file_size_mb", None)
            supabase.table("download_jobs").update(update_data).eq("id", job_id).execute()

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

        # ── Telegram: Notify job failure ─────────────────
        try:
            from app.core.notifications import notify_job_failed_sync
            notify_job_failed_sync(job_id, url, error_msg)
        except Exception as tg_err:
            print(f"[Telegram] Failed to send job failure notification: {tg_err}")


@celery_app.task(name="scrape_channel_task", bind=True)
def scrape_channel_task(self, channel_url: str, batch_id: str, channel_job_id: str, max_videos: int = 100, min_views: int = 0, user_id: Optional[str] = None, playlist_start: int = 1):
    """
    Scrape a channel/playlist URL with wave-based processing:
    
    1. Discover all video entries (yt-dlp handles pagination).
    2. Filter them according to limits.
    3. Insert ALL as pending jobs in Supabase immediately (frontend sees them).
    4. Dispatch process_video_task in WAVES of WAVE_SIZE.
       - Wave 1: videos 1-10 (immediate)
       - Wave 2: videos 11-20 (countdown=5s)
       - Wave 3: videos 21-30 (countdown=10s)
       This prevents flooding the Celery queue while keeping throughput high.
    5. Update the placeholder channel_job_id with summary.
    """
    WAVE_SIZE = 10          # Videos per wave
    WAVE_DELAY_SECONDS = 3  # Seconds between each wave's start

    supabase = get_supabase_client()

    try:
        # ── Phase 1: Discover videos ─────────────────────
        supabase.table("download_jobs").update({
            "error_message": f"Đang quét kênh — tìm kiếm tối đa {max_videos} video...",
        }).eq("id", channel_job_id).execute()

        result = scrape_channel_entries_sync(channel_url, max_videos, min_views, playlist_start)
        entries = result.get("entries", [])
        total_found = result.get("total_found", 0)
        total_queued = result.get("total_queued", 0)

        if not entries:
            supabase.table("download_jobs").update({
                "status": "failed",
                "error_message": f"Đã quét {total_found} videos nhưng không có video nào đạt điều kiện lọc.",
            }).eq("id", channel_job_id).execute()
            return

        # ── Phase 2: Insert ALL jobs as "pending" immediately ─
        # This lets the frontend show the full list with progress
        supabase.table("download_jobs").update({
            "error_message": f"Đã tìm thấy {total_found} video. Đang tạo {len(entries)} jobs...",
        }).eq("id", channel_job_id).execute()

        job_entries = []  # list of (job_id, video_url) pairs
        for entry in entries:
            video_url = entry.get("url", "")
            if not video_url:
                continue

            response = supabase.table("download_jobs").insert({
                "batch_id": batch_id,
                "original_url": video_url,
                "status": "pending",
            }).execute()

            job_id = response.data[0]["id"]
            job_entries.append((job_id, video_url))

        # ── Phase 3: Dispatch in waves ───────────────────
        total_waves = (len(job_entries) + WAVE_SIZE - 1) // WAVE_SIZE

        for wave_idx in range(total_waves):
            start = wave_idx * WAVE_SIZE
            end = min(start + WAVE_SIZE, len(job_entries))
            wave = job_entries[start:end]

            # Calculate countdown delay for this wave
            # Wave 0 = immediate, Wave 1 = 3s delay, Wave 2 = 6s, etc.
            countdown = wave_idx * WAVE_DELAY_SECONDS

            for job_id, video_url in wave:
                process_video_task.apply_async(
                    args=[job_id, video_url, user_id],
                    countdown=countdown,
                )

        # ── Phase 4: Update summary ──────────────────────
        wave_info = f" ({total_waves} đợt x {WAVE_SIZE} video)" if total_waves > 1 else ""
        summary_msg = (
            f"✅ Đã tìm thấy {total_found} video, "
            f"{total_queued} video đạt điều kiện lọc và đang xử lý{wave_info}."
        )
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

        # ── Telegram: Notify channel scrape failure ──────
        try:
            from app.core.notifications import notify_job_failed_sync
            notify_job_failed_sync(channel_job_id, channel_url, f"Channel scrape failed: {error_msg}")
        except Exception as tg_err:
            print(f"[Telegram] Failed to send scrape failure notification: {tg_err}")


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
            zip_update = {
                "status": "success",
                "title": f"Batch {batch_id[:8]} — {total_files} files",
                "slugified_name": f"batch_{batch_id}",
                "direct_mp4_url": result.get("zip_path"),
                "created_at": now_iso
            }
            try:
                zip_update["file_size_mb"] = zip_size
                supabase.table("download_jobs").update(zip_update).eq("id", zip_job_id).execute()
            except Exception:
                zip_update.pop("file_size_mb", None)
                supabase.table("download_jobs").update(zip_update).eq("id", zip_job_id).execute()

            # ── Telegram: Notify batch complete ──────────
            try:
                from app.core.notifications import notify_batch_complete_sync

                # Count success/failed in this batch for the notification
                batch_jobs_res = (
                    supabase.table("download_jobs")
                    .select("status")
                    .eq("batch_id", batch_id)
                    .neq("original_url", "batch_zip")
                    .execute()
                )
                batch_jobs = batch_jobs_res.data if batch_jobs_res.data else []
                success_count = sum(1 for j in batch_jobs if j.get("status") == "success")
                failed_count = sum(1 for j in batch_jobs if j.get("status") == "failed")

                notify_batch_complete_sync(
                    batch_id=batch_id,
                    total_files=total_files,
                    zip_size_mb=zip_size,
                    success_count=success_count,
                    failed_count=failed_count,
                )
            except Exception as tg_err:
                print(f"[Telegram] Failed to send batch complete notification: {tg_err}")

        else:
            error_msg = result.get("error", "Unknown zip error")
            supabase.table("download_jobs").update({
                "status": "failed",
                "error_message": error_msg,
            }).eq("id", zip_job_id).execute()

            # ── Telegram: Notify ZIP creation failure ────
            try:
                from app.core.notifications import notify_job_failed_sync
                notify_job_failed_sync(zip_job_id, f"batch_zip:{batch_id[:8]}", f"ZIP creation failed: {error_msg}")
            except Exception as tg_err:
                print(f"[Telegram] Failed to send zip failure notification: {tg_err}")

    except Exception as e:
        error_msg = str(e)[:500]
        supabase.table("download_jobs").update({
            "status": "failed",
            "error_message": f"Zip creation failed: {error_msg}",
        }).eq("id", zip_job_id).execute()

        # ── Telegram: Notify ZIP exception ───────────────
        try:
            from app.core.notifications import notify_job_failed_sync
            notify_job_failed_sync(zip_job_id, f"batch_zip:{batch_id[:8]}", f"Zip exception: {error_msg}")
        except Exception:
            pass


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
    """
    Scan downloads dir: delete files older than 20 min.
    Also enforce DOWNLOADS_MAX_GB disk quota by evicting oldest files first.
    """
    import time
    DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloads")
    if not os.path.exists(DOWNLOAD_DIR):
        return

    MAX_GB   = float(os.getenv("DOWNLOADS_MAX_GB", "10"))
    MAX_BYTES = MAX_GB * 1024 ** 3
    now      = time.time()
    expiry   = 20 * 60  # 20 minutes

    # Pass 1: delete expired files
    deleted = 0
    for item in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, item)
        try:
            if now - os.path.getmtime(path) > expiry:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                deleted += 1
        except Exception as e:
            print(f"[Cron] cleanup error {path}: {e}")

    # Pass 2: enforce disk quota — evict oldest first
    all_files = []
    for item in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, item)
        try:
            all_files.append((os.path.getmtime(path), os.path.getsize(path), path))
        except Exception:
            pass

    total_bytes = sum(s for _, s, _ in all_files)
    if total_bytes > MAX_BYTES:
        print(f"[Cron] Disk quota exceeded: {total_bytes/(1024**3):.2f}GB > {MAX_GB}GB — evicting oldest files")
        all_files.sort()  # oldest first
        for mtime, size, path in all_files:
            if total_bytes <= MAX_BYTES * 0.8:  # free to 80% of quota
                break
            try:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                total_bytes -= size
                deleted += 1
                print(f"[Cron] Quota evict: {path}")
            except Exception as e:
                print(f"[Cron] evict error {path}: {e}")

    remaining_gb = total_bytes / (1024 ** 3)
    print(f"[Cron] Cleanup done. Deleted {deleted} items. Disk: {remaining_gb:.2f}GB / {MAX_GB}GB")


# ═════════════════════════════════════════════════════════════════════
# NEW: Daily Summary Report Task (Celery Beat)
# ═════════════════════════════════════════════════════════════════════

@celery_app.task(name="daily_summary_report", bind=True)
def daily_summary_report(self):
    """
    Celery Beat task: Send a daily operations summary to Telegram.
    Runs every day at 23:00 UTC (6:00 AM UTC+7 next day).
    """
    print("[Cron] Generating daily summary report...")
    try:
        from app.core.notifications import send_daily_summary_sync
        result = send_daily_summary_sync()
        if result:
            print("[Cron] Daily summary sent to Telegram successfully.")
        else:
            print("[Cron] Daily summary failed to send.")
    except Exception as e:
        print(f"[Cron] Daily summary error: {e}")


# ═════════════════════════════════════════════════════════════════════
# NEW: Credits Check Task (Celery Beat — every 6 hours)
# ═════════════════════════════════════════════════════════════════════

@celery_app.task(name="check_api_credits", bind=True)
def check_api_credits(self):
    """
    Celery Beat task: Check API credits and send alerts if low.
    Runs every 6 hours.
    """
    print("[Cron] Checking API credits...")
    try:
        import httpx

        from app.core.notifications import (
            notify_credits_low_sync,
            CREDITS_WARNING_THRESHOLD,
        )

        # Check ScraperAPI
        scraper_key = os.getenv("SCRAPERAPI_API_KEY", os.getenv("SCRAPERAPI_KEY", ""))
        if scraper_key:
            try:
                resp = httpx.get(
                    f"http://api.scraperapi.com/account?api_key={scraper_key}",
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    remaining = data.get("requestLimit", 0) - data.get("requestCount", 0)
                    print(f"[Cron] ScraperAPI credits remaining: {remaining}")
                    if remaining < CREDITS_WARNING_THRESHOLD:
                        notify_credits_low_sync("ScraperAPI", remaining)
            except Exception as e:
                print(f"[Cron] ScraperAPI check failed: {e}")

        print("[Cron] API credits check finished.")
    except Exception as e:
        print(f"[Cron] Credits check error: {e}")


# ═════════════════════════════════════════════════════════════════════
# yt-dlp Auto-Update (daily at 3 AM UTC)
# ═════════════════════════════════════════════════════════════════════

@celery_app.task(name="ytdlp_auto_update", bind=True)
def ytdlp_auto_update(self):
    """
    Upgrade yt-dlp to latest version daily.
    yt-dlp breaks frequently when platforms update their APIs.
    Auto-updating prevents silent download failures.
    """
    import subprocess
    import importlib.metadata

    try:
        before = importlib.metadata.version("yt-dlp")
    except Exception:
        before = "unknown"

    print(f"[ytdlp-update] Current version: {before}")
    try:
        result = subprocess.run(
            ["pip", "install", "-q", "--upgrade", "yt-dlp"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            print(f"[ytdlp-update] pip upgrade failed: {result.stderr[:200]}")
            return

        # Force reload cached version info
        try:
            import importlib
            import yt_dlp as _ytdlp
            importlib.reload(_ytdlp)
        except Exception:
            pass

        try:
            after = importlib.metadata.version("yt-dlp")
        except Exception:
            after = "unknown"

        if before != after:
            print(f"[ytdlp-update] ✓ Updated: {before} → {after}")
        else:
            print(f"[ytdlp-update] Already latest: {before}")

    except subprocess.TimeoutExpired:
        print("[ytdlp-update] pip upgrade timed out")
    except Exception as e:
        print(f"[ytdlp-update] Error: {e}")
