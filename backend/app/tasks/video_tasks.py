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
from app.core.failure_classifier import (
    classify_failure,
    backoff_seconds,
    max_retries_for_class,
    auto_retry_status,
    user_recovery_message,
    FailureClass,
)
from celery.exceptions import SoftTimeLimitExceeded
from typing import Optional
from datetime import datetime, timezone, timedelta
import os
import time
import random
import shutil

# Columns that may be absent before their migration has run.
# Strip them from updates if the DB reports a missing-column error.
_OPTIONAL_COLS = {
    "auto_retry_status", "retry_count", "last_error",
    "error_type", "completed_at", "job_type", "fallback_quality_used",
}
_MISSING_COLS: set = set()

# ── Quality auto-fallback ─────────────────────────────────────────────────────
# When a requested resolution isn't available, automatically try lower tiers.
_QUALITY_LADDER: dict = {
    "video_4k":  ["video_1080", "video_720", "video_480", "video"],
    "video_1080": ["video_720",  "video_480", "video_360", "video"],
    "video_720":  ["video_480",  "video_360", "video"],
    "video_480":  ["video_360",  "video"],
    "video_360":  ["video"],
}

_QUALITY_FALLBACK_SIGNALS = [
    "requested format is not available",
    "no video formats found",
    "no formats found",
    "format is not available",
    "unable to download video",
    "not available in",
    "this video is not available in your resolution",
]

def _is_quality_unavailable(error_msg: str) -> bool:
    msg = error_msg.lower()
    return any(sig in msg for sig in _QUALITY_FALLBACK_SIGNALS)

# ── Phase 11: flexible file expiry by job type ────────────────────────────
FILE_EXPIRY_SINGLE_MIN = int(os.getenv("FILE_EXPIRY_SINGLE_MIN", "20"))
FILE_EXPIRY_BULK_MIN   = int(os.getenv("FILE_EXPIRY_BULK_MIN",   "30"))
FILE_EXPIRY_ARTIST_MIN = int(os.getenv("FILE_EXPIRY_ARTIST_MIN", "45"))
FILE_EXPIRY_PRO_BONUS  = int(os.getenv("FILE_EXPIRY_PRO_BONUS_MIN", "30"))


def _compute_file_expires(job_type: Optional[str], tier: Optional[str]) -> str:
    """Return ISO timestamp for file_expires_at based on job type and user tier."""
    base_min = {
        "single":     FILE_EXPIRY_SINGLE_MIN,
        "bulk":       FILE_EXPIRY_BULK_MIN,
        "artist_all": FILE_EXPIRY_ARTIST_MIN,
    }.get(job_type or "single", FILE_EXPIRY_SINGLE_MIN)
    if tier == "pro":
        base_min += FILE_EXPIRY_PRO_BONUS
    return (datetime.now(timezone.utc) + timedelta(minutes=base_min)).isoformat()


def _register_pending_task(task_id: str) -> None:
    """Track Celery task_id in Redis SET for crash-recovery visibility."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        rc.sadd("vidgrab:pending_tasks", task_id)
        rc.expire("vidgrab:pending_tasks", 3 * 86400)
    except Exception:
        pass


def _deregister_pending_task(task_id: str) -> None:
    """Remove task_id from Redis pending SET on completion or failure."""
    try:
        from app.core.redis_client import get_redis
        rc = get_redis()
        rc.srem("vidgrab:pending_tasks", task_id)
    except Exception:
        pass


def _sb_update(supabase, data: dict, job_id: str) -> None:
    """Update download_jobs, gracefully stripping columns absent before migration."""
    global _MISSING_COLS
    safe = {k: v for k, v in data.items() if k not in _MISSING_COLS}
    while True:
        try:
            supabase.table("download_jobs").update(safe).eq("id", job_id).execute()
            return
        except Exception as e:
            err = str(e)
            removed = False
            for col in _OPTIONAL_COLS:
                if col in err and col not in _MISSING_COLS:
                    _MISSING_COLS.add(col)
                    safe.pop(col, None)
                    removed = True
                    break
            if not removed:
                raise


def _get_platform(url: str) -> str:
    url = url.lower()
    if "youtube.com" in url or "youtu.be" in url: return "youtube"
    if "facebook.com" in url or "fb.watch" in url: return "facebook"
    if "tiktok.com" in url:  return "tiktok"
    if "instagram.com" in url: return "instagram"
    if "douyin.com" in url:  return "douyin"
    if "threads.net" in url or "threads.com" in url: return "threads"
    if "twitter.com" in url or "x.com" in url: return "twitter"
    if "reddit.com" in url: return "reddit"
    if "pinterest.com" in url or "pin.it" in url: return "pinterest"
    if "linkedin.com" in url: return "linkedin"
    return "other"

# Per-platform request delay range (seconds) — prevents triggering rate limits
_PLATFORM_DELAYS = {
    "youtube":   (3, 8),
    "facebook":  (5, 12),
    "tiktok":    (1, 4),
    "instagram": (3, 7),
    "douyin":    (2, 5),
    "threads":   (4, 9),
    "twitter":   (8, 15),
    "reddit":    (2, 5),
    "pinterest": (3, 8),
    "linkedin":  (5, 10),
    "other":     (1, 3),
}

# Per-platform wave delay (seconds between waves in bulk download)
_PLATFORM_WAVE_DELAYS = {
    "youtube":  5,
    "facebook": 8,
    "tiktok":   3,
    "instagram": 5,
    "douyin":   4,
    "threads":  6,
    "twitter":  10,
    "reddit":    4,
    "pinterest": 6,
    "linkedin":  7,
    "other":    3,
}

def _platform_sleep(url: str):
    """Sleep a random amount based on platform before making a request."""
    platform = _get_platform(url)
    lo, hi = _PLATFORM_DELAYS.get(platform, (1, 3))
    delay = random.uniform(lo, hi)
    time.sleep(delay)

def _track_platform(url: str, success: bool) -> None:
    """Record per-platform ok/err counter in Redis (same key as routes.py _track_download)."""
    try:
        from app.core.redis_client import get_redis
        import datetime as _dt
        rc = get_redis()
        date = _dt.date.today().isoformat()
        plat = _get_platform(url)
        field = f"{plat}:{'ok' if success else 'err'}"
        redis_key = f"vidgrab:stats:{date}"
        rc.hincrby(redis_key, field, 1)
        rc.expire(redis_key, 86400 * 8)
    except Exception:
        pass

@celery_app.task(
    name="process_video_task", bind=True, max_retries=3,
    # Per-task watchdog: a single download must not run forever. soft → clean
    # fail; hard → SIGKILL the prefork child (real process kill) so the worker
    # slot frees immediately for the next user instead of hanging 12 min.
    soft_time_limit=int(os.getenv("VIDEO_TASK_SOFT_LIMIT", "300")),   # 5 min
    time_limit=int(os.getenv("VIDEO_TASK_HARD_LIMIT", "360")),        # 6 min
)
def process_video_task(self, job_id: str, url: str, user_id: Optional[str] = None, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False, _lane_try: int = 0):
    """Extract the direct MP4 link for a single video and update Supabase."""
    supabase = get_supabase_client()
    task_start = time.time()
    platform = _get_platform(url)

    # ── Phase 11: register in pending SET for crash-recovery visibility ──
    _task_id = str(self.request.id or job_id)
    _register_pending_task(_task_id)

    # ── Observability: structured-log context + started metric ──────────
    try:
        from app.core.structured_log import set_task_context
        set_task_context(task_id=_task_id, job_id=job_id, platform=platform)
    except Exception:
        pass
    try:
        from app.core.metrics import emit_job_event as _emit
        _emit("started", job_id=job_id, platform=platform)
    except Exception:
        pass

    # ── Phase 11: idempotency — skip if job already succeeded ───────────
    try:
        _ex = supabase.table("download_jobs").select("status").eq("id", job_id).single().execute()
        if (_ex.data or {}).get("status") == "success":
            print(f"[Idempotency] job={job_id} already succeeded, skipping duplicate execution.")
            _deregister_pending_task(_task_id)
            return
    except Exception:
        pass

    # ── Stability: acquire per-job lease to prevent duplicate execution ──
    # If another worker already holds the lease (e.g., after a recovery
    # re-queue before the old task was killed) we exit immediately.
    # Fail-open: if job_lease is unavailable, proceed without the guard.
    _job_lease = None
    try:
        from app.core.job_lease import JobLease
        _job_lease = JobLease(job_id, worker_id=_task_id)
        if not _job_lease.acquire():
            print(f"[Lease] job={job_id} lease held by another worker — skipping duplicate.")
            _deregister_pending_task(_task_id)
            return
        _job_lease.start_heartbeat()
    except Exception as _le:
        print(f"[Lease] job={job_id} lease init failed (non-fatal): {_le}")
        _job_lease = None

    # ── Concurrency lanes: isolate per-platform + per-user ──────────
    # If this platform's lane (or the user's cap) is full, DEFER by re-queuing
    # with a short jittered delay — don't block a worker. After many tries,
    # proceed anyway (starvation guard).
    from app.core import platform_lanes as _lanes
    _MAX_LANE_REDISPATCH = 15
    _lane_held = _lanes.try_acquire_platform(platform)
    _user_held = False
    if _lane_held:
        _user_held = _lanes.try_acquire_user(user_id)
        if not _user_held:
            _lanes.release_platform(platform)
            _lane_held = False
    if not _lane_held and _lane_try < _MAX_LANE_REDISPATCH:
        process_video_task.apply_async(
            args=[job_id, url, user_id, quality, remove_watermark, download_subs],
            kwargs={"_lane_try": _lane_try + 1},
            countdown=4 + (_lane_try % 7),
        )
        return

    try:
        # Check quotas if user_id provided
        if user_id:
            c_info = check_user_quota(user_id)
            if not c_info["allowed"]:
                _sb_update(supabase, {
                    "status": "failed",
                    "job_stage": "failed",
                    "error_message": c_info["message"],
                    "auto_retry_status": "not_applicable",
                }, job_id)
                return

        # 1. Check cache first
        cached = get_cached_result(url)
        if cached:
            print(f"[Cache Hit] Task - URL: {url}")
            if user_id: increment_usage(user_id)
            title = cached.get("title", "Unknown")
            slug = slugify(title)
            _sb_update(supabase, {
                "status": "success",
                "job_stage": "completed",
                "title": title,
                "slugified_name": slug,
                "direct_mp4_url": cached.get("direct_mp4_url"),
                "error_message": "Tải từ bộ nhớ đệm thành công",
                "auto_retry_status": "not_applicable",
            }, job_id)
            return

        print(f"[Cache Miss] Task - URL: {url} - Proceeding to extraction")

        # 2. Mark as processing + checkpoint: extracting
        supabase.table("download_jobs").update({
            "status": "processing",
            "job_stage": "extracting",
        }).eq("id", job_id).execute()

        # 3. Per-platform delay to avoid rate limiting on bulk downloads
        _platform_sleep(url)

        # 4. Extract video info (with quality auto-fallback chain)
        _quality_chain = [quality] + _QUALITY_LADDER.get(quality, [])
        _last_exc: Exception = None
        info = None
        fallback_quality_used = None
        for _try_quality in _quality_chain:
            try:
                info = extract_video_info_sync(url, _try_quality, remove_watermark, download_subs)
                if _try_quality != quality:
                    fallback_quality_used = _try_quality
                    print(f"[QualityFallback] job={job_id} {quality}→{_try_quality} (original unavailable)")
                break
            except Exception as _qe:
                _last_exc = _qe
                if _is_quality_unavailable(str(_qe)):
                    print(f"[QualityFallback] job={job_id} {_try_quality} unavailable, trying next")
                    continue
                raise
        if info is None:
            raise _last_exc

        # Generate slugified name
        title = info.get("title", "Unknown")
        slug = slugify(title)

        # Increment quota usage on successful fetch
        if user_id: increment_usage(user_id)

        # 5. Mark as success with stage checkpoint and expiry
        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()

        # In bulk mode, local downloads (TikTok/Douyin/Audio) are stored locally.
        # We track the path and compute expiry (20-min cleanup window, 2-min buffer).
        local_path = info.get("local_file_path") or info.get("local_mp3_path")
        best_url   = local_path or info.get("direct_mp4_url")
        is_local   = bool(local_path)

        # link_expires_at: only set for local files (we control their TTL).
        # Remote CDN URLs have unknown expiry — leave NULL (frontend won't show countdown).
        link_expires_at = (now_utc + timedelta(minutes=58)).isoformat() if is_local else None

        elapsed_seconds = time.time() - task_start

        update_data = {
            "status":            "success",
            "job_stage":         "completed",
            "title":             title,
            "slugified_name":    slug,
            "direct_mp4_url":    best_url,
            "link_expires_at":   link_expires_at,
            **({"fallback_quality_used": fallback_quality_used} if fallback_quality_used else {}),
            "created_at":        now_iso,
            "auto_retry_status": "not_applicable",
        }
        if is_local:
            update_data["temp_artifact_path"] = local_path

        try:
            update_data["file_size_mb"] = info.get("file_size_mb", 0)
            _sb_update(supabase, update_data, job_id)
        except Exception:
            update_data.pop("file_size_mb", None)
            _sb_update(supabase, update_data, job_id)
        _track_platform(url, True)
        try:
            from app.core.metrics import emit_job_event as _emit
            _emit("succeeded", job_id=job_id, platform=platform)
        except Exception:
            pass
        try:
            from app.core import platform_circuit as _pcb
            _pcb.record_success(platform)
        except Exception:
            pass
        # Phase 8: bill YouTube proxy bytes from the bulk path too, so the daily
        # cost guard sees ALL proxy spend (single + bulk), not just /fetch-link.
        if platform == "youtube":
            try:
                from app.core import youtube_gate as _ytg
                _proxy_used = os.getenv("YOUTUBE_PROXY_DOWNLOAD", "0").strip().lower() in ("1", "true", "yes", "on")
                _ytg.record_result(True, info.get("file_size_mb", 0) if _proxy_used else 0)
            except Exception:
                pass

        # ── Queue Intelligence: record timing ────────────────────────────
        try:
            from app.core.queue_intelligence import update_avg_duration
            update_avg_duration(elapsed_seconds)
        except Exception:
            pass

        # ── Smart Defaults: record successful quality choice ─────────────
        try:
            from app.core.smart_defaults import record_choice
            record_choice(user_id, platform, quality, True)
        except Exception:
            pass

        # ── Storage governance: set file_expires_at + storage_bytes ─────
        try:
            _file_bytes = 0
            if local_path and os.path.exists(local_path):
                _file_bytes = os.path.getsize(local_path)
            # Phase 11: flexible expiry — compute from job_type × tier at COMPLETION
            _job_type = "single"
            _tier_for_expiry = None
            try:
                _jr = supabase.table("download_jobs").select("job_type").eq("id", job_id).single().execute()
                _job_type = (_jr.data or {}).get("job_type") or "single"
            except Exception:
                pass
            if user_id:
                try:
                    from app.core.quotas import _get_profile as _qp
                    _pr = _qp(user_id)
                    _tier_for_expiry = (_pr or {}).get("tier")
                except Exception:
                    pass
            _file_expires = _compute_file_expires(_job_type, _tier_for_expiry)
            _sb_update(supabase, {
                "file_expires_at":       _file_expires,
                "storage_bytes":         _file_bytes,
                "file_retention_status": "temporary",
                "completed_at":          now_iso,
            }, job_id)
        except Exception as _sg_err:
            print(f"[Storage] Governance update error: {_sg_err}")

        # ── Webhook: notify user webhook (Pro feature) ──────────
        try:
            from app.api.webhook import deliver_webhook_sync
            if user_id:
                deliver_webhook_sync(user_id, {
                    "job_id":       job_id,
                    "status":       "success",
                    "platform":     platform,
                    "title":        title,
                    "direct_url":   best_url,
                    "file_size_mb": info.get("file_size_mb", 0),
                    "completed_at": now_iso,
                })
        except Exception as wh_err:
            print(f"[Webhook] Dispatch error: {wh_err}")

        # ── Metadata: store yt-dlp metadata asynchronously ───────────────
        try:
            from app.tasks.metadata_tasks import store_job_metadata_task
            _meta = {
                "uploader_id":   info.get("uploader_id"),
                "channel_id":    info.get("channel_id"),
                "uploader":      info.get("uploader"),
                "channel":       info.get("channel"),
                "duration":      info.get("duration"),
                "view_count":    info.get("view_count"),
                "like_count":    info.get("like_count"),
                "upload_date":   info.get("upload_date"),
                "language":      info.get("language"),
                "tags":          info.get("tags"),
                "categories":    info.get("categories"),
                "description":   info.get("description"),
                "thumbnail":     info.get("thumbnail"),
            }
            store_job_metadata_task.delay(job_id, _meta)
        except Exception as _me:
            print(f"[Metadata] Dispatch error for job {job_id}: {_me}")

        # ── Archive: auto-create entry for logged-in users ────────────────
        try:
            if user_id:
                from app.tasks.archive_tasks import auto_archive_task
                auto_archive_task.delay(job_id, user_id, {
                    "url":            url,
                    "title":          title,
                    "platform":       platform,
                    "creator_handle": info.get("uploader_id"),
                    "creator_name":   info.get("uploader") or info.get("channel"),
                    "duration":       info.get("duration"),
                    "upload_date":    info.get("upload_date"),
                    "thumbnail":      info.get("thumbnail"),
                    "language":       info.get("language"),
                    "tags":           (info.get("tags") or [])[:20],
                    "description":    (info.get("description") or "")[:500],
                    "view_count":     info.get("view_count"),
                })
        except Exception as _ae:
            print(f"[Archive] Dispatch error for job {job_id}: {_ae}")

    except SoftTimeLimitExceeded:
        _sb_update(supabase, {
            "status": "failed",
            "job_stage": "abandoned",
            "error_message": "Qua thoi gian xu ly. Video qua lon hoac nguon phan hoi cham — vui long thu lai.",
            "auto_retry_status": "not_applicable",
        }, job_id)
        _track_platform(url, False)
        try:
            from app.core.metrics import emit_job_event as _emit
            _emit("failed", job_id=job_id, platform=platform)
        except Exception:
            pass

    except Exception as e:
        raw_error = str(e)[:500]
        fc = classify_failure(raw_error)
        retries = self.request.retries

        print(f"[process_video_task] job={job_id} fc={fc} retry={retries} err={raw_error[:120]}")

        # ── Adaptive Fallback: record the failed attempt ─────────────────
        try:
            from app.core.adaptive_fallback import record_attempt
            record_attempt(platform, "direct", False, 0, raw_error[:50])
        except Exception:
            pass

        _track_platform(url, False)
        try:
            from app.core.metrics import emit_job_event as _emit
            _emit("failed", job_id=job_id, platform=platform)
        except Exception:
            pass

        # ── Per-platform circuit breaker: count block/rate-limit signals ──
        try:
            from app.core import platform_circuit as _pcb
            _es = raw_error.lower()
            if any(kw in _es for kw in (
                "429", "rate limit", "too many", "sign in", "bot", "captcha",
                "challenge", "login required", "forbidden", "403", "blocked",
            )):
                _pcb.record_failure(platform)
        except Exception:
            pass

        # Phase 8: feed the YouTube proxy circuit breaker (separate from the
        # generic bot-block breaker — trips on proxy/download failures).
        if platform == "youtube":
            try:
                from app.core import youtube_gate as _ytg
                _ytg.record_result(False)
            except Exception:
                pass

        # ── Classify and decide: retry or fail permanently ───────────────
        _now_fail_iso = datetime.now(timezone.utc).isoformat()
        if fc in (FailureClass.PERMANENT, FailureClass.USER_ACTION):
            # No retry — these will not resolve on their own
            final_auto_status = auto_retry_status(fc, 0)
            final_msg = user_recovery_message(fc, retries, 0)
            _sb_update(supabase, {
                "status":            "failed",
                "job_stage":         "failed",
                "error_message":     final_msg,
                "auto_retry_status": final_auto_status,
                "retry_count":       retries,
                "last_error":        raw_error[:500],
                "error_type":        "permanent",
                "completed_at":      _now_fail_iso,
            }, job_id)

        elif fc in (FailureClass.TRANSIENT, FailureClass.RETRYABLE) and retries < max_retries_for_class(fc):
            # Schedule a retry with classifier-driven backoff
            backoff = backoff_seconds(retries, fc)
            retry_msg = user_recovery_message(fc, retries, backoff)
            print(f"[RateLimit/Transient] {platform} fc={fc} retry {retries} — backoff {backoff}s")
            _sb_update(supabase, {
                "error_message":     retry_msg,
                "auto_retry_status": "auto_retry_scheduled",
                "retry_count":       retries + 1,
                "last_error":        raw_error[:500],
                "error_type":        "retryable",
            }, job_id)
            try:
                from app.core.metrics import emit_job_event as _emit
                _emit("retrying", job_id=job_id, platform=platform)
            except Exception:
                pass
            raise self.retry(countdown=backoff, exc=e)

        else:
            # Retries exhausted (or unclassified exhausted)
            final_auto_status = "retry_limit_reached"
            final_msg = user_recovery_message(fc, retries, 0)
            _sb_update(supabase, {
                "status":            "failed",
                "job_stage":         "failed",
                "error_message":     final_msg,
                "auto_retry_status": final_auto_status,
                "retry_count":       retries,
                "last_error":        raw_error[:500],
                "error_type":        "retryable",
                "completed_at":      _now_fail_iso,
            }, job_id)

        # ── Telegram: Notify job failure ─────────────────
        try:
            from app.core.notifications import notify_job_failed_sync
            notify_job_failed_sync(job_id, url, raw_error)
        except Exception as tg_err:
            print(f"[Telegram] Failed to send job failure notification: {tg_err}")

    finally:
        # Always free the concurrency slots (also runs on self.retry / success).
        if _lane_held:
            _lanes.release_platform(platform)
        if _user_held:
            _lanes.release_user(user_id)
        # Phase 11: deregister from pending tasks SET (on retry this re-registers at next attempt start)
        _deregister_pending_task(_task_id)
        # Stability: stop heartbeat + release lease (on retry the next attempt re-acquires)
        if _job_lease is not None:
            try:
                _job_lease.stop()
            except Exception:
                pass


@celery_app.task(
    name="scrape_channel_task", bind=True,
    # Scrape phase is capped at ~120s internally; the rest is fast job inserts +
    # wave dispatch. Hard-kill a genuinely stuck scrape well before the 12-min
    # global limit so it can't pin a worker.
    soft_time_limit=int(os.getenv("SCRAPE_TASK_SOFT_LIMIT", "300")),  # 5 min
    time_limit=int(os.getenv("SCRAPE_TASK_HARD_LIMIT", "330")),       # 5.5 min
)
def scrape_channel_task(self, channel_url: str, batch_id: str, channel_job_id: str, max_videos: int = 100, min_views: int = 0, user_id: Optional[str] = None, quality: str = "video", remove_watermark: bool = False, download_subs: bool = False):
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
    # Per-platform wave delay — faster platforms get shorter delays
    platform = _get_platform(channel_url)
    # Phase 27D: use adaptive wave params (reads auto_tuner + lane state from 27A).
    # Falls back to static profile defaults when adaptive is disabled.
    try:
        from app.core.wave_scheduler import get_wave_params as _gwp
        _wp = _gwp(platform)
        WAVE_SIZE         = _wp.wave_size
        WAVE_DELAY_SECONDS = _wp.wave_delay
        _wave_mode        = _wp.mode
        _wave_reason      = _wp.reason
        # Disabled platform: cancel wave entirely, mark all as failed
        if WAVE_SIZE == 0:
            import logging as _log27d
            _log27d.getLogger(__name__).warning(
                "[WaveScheduler:%s] mode=disabled reason=%s — aborting wave dispatch",
                platform, _wave_reason,
            )
            supabase.table("download_jobs").update({
                "status":        "failed",
                "error_message": f"Nền tảng {platform} tạm thời không khả dụng. Thử lại sau.",
            }).eq("batch_id", batch_id).neq("original_url", "batch_zip").execute()
            return
    except Exception as _wp_err:
        import logging as _log27d
        _log27d.getLogger(__name__).debug("[WaveScheduler] fallback to static: %s", _wp_err)
        WAVE_SIZE          = 10  # pre-27D static default
        WAVE_DELAY_SECONDS = _PLATFORM_WAVE_DELAYS.get(platform, 3)
        _wave_mode         = "balanced"
        _wave_reason       = "fallback_static"

    supabase = get_supabase_client()

    try:
        # ── Phase 1: Discover videos ─────────────────────
        supabase.table("download_jobs").update({
            "error_message": f"Dang quet kenh — tim kiem toi da {max_videos} video...",
        }).eq("id", channel_job_id).execute()

        # Heartbeat: update error_message every 5s during the blocking yt-dlp call
        # so the frontend can show elapsed time and confirm the task is alive.
        import threading, time as _time
        _stop_hb = threading.Event()
        _hb_start = _time.time()

        def _heartbeat():
            while not _stop_hb.wait(5):
                elapsed = int(_time.time() - _hb_start)
                try:
                    supabase.table("download_jobs").update({
                        "error_message": f"Dang quet danh sach video... ({elapsed}s)",
                    }).eq("id", channel_job_id).execute()
                except Exception:
                    pass

        _hb_thread = threading.Thread(target=_heartbeat, daemon=True)
        _hb_thread.start()

        try:
            result = scrape_channel_entries_sync(channel_url, max_videos, min_views)
        finally:
            _stop_hb.set()
        entries = result.get("entries", [])
        total_found = result.get("total_found", 0)
        total_queued = result.get("total_queued", 0)

        if not entries:
            supabase.table("download_jobs").update({
                "status": "failed",
                "error_message": f"Da quet {total_found} videos nhung khong co video nao dat dieu kien loc.",
            }).eq("id", channel_job_id).execute()
            return

        # ── Phase 2: Insert ALL jobs as "pending" immediately ─
        # This lets the frontend show the full list with progress
        supabase.table("download_jobs").update({
            "error_message": f"Da tim thay {total_found} video. Dang tao {len(entries)} jobs...",
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
        # One apply_async() failing (e.g. a transient broker hiccup) must not
        # strand the rest of the batch in "pending" forever — scan_stale_jobs
        # recovers stuck-pending rows periodically as a second safety net, but
        # dispatch should still try every job before giving up on any of them.
        dispatch_failed = 0

        def _dispatch(job_id: str, video_url: str, countdown: int = 0):
            nonlocal dispatch_failed
            try:
                process_video_task.apply_async(
                    args=[job_id, video_url, user_id, quality, remove_watermark, download_subs],
                    countdown=countdown,
                )
            except Exception as dispatch_err:
                dispatch_failed += 1
                print(f"[scrape_channel_task] dispatch failed for job {job_id}: {dispatch_err}")

        # Tiny batch: skip wave overhead entirely — dispatch all immediately.
        if len(job_entries) <= WAVE_SIZE:
            for job_id, video_url in job_entries:
                _dispatch(job_id, video_url)
            total_waves = 1
        else:
            total_waves = (len(job_entries) + WAVE_SIZE - 1) // WAVE_SIZE

            # Phase 27D: wave_scheduler already factored in circuit + lane state.
            # adaptive_delay = final wave delay from wave_scheduler.
            adaptive_delay = WAVE_DELAY_SECONDS

            for wave_idx in range(total_waves):
                start = wave_idx * WAVE_SIZE
                end = min(start + WAVE_SIZE, len(job_entries))
                wave = job_entries[start:end]

                # Wave 0 = immediate, Wave 1 = adaptive_delay s, Wave 2 = 2×, etc.
                countdown = wave_idx * adaptive_delay

                for job_id, video_url in wave:
                    _dispatch(job_id, video_url, countdown=countdown)

        if dispatch_failed:
            print(f"[scrape_channel_task] {dispatch_failed}/{len(job_entries)} dispatch(es) failed — "
                  f"scan_stale_jobs will re-queue them within ~2 min")

        # ── Phase 4: Update summary ──────────────────────
        wave_info = f" ({total_waves} dot x {WAVE_SIZE} video, mode={_wave_mode})" if total_waves > 1 else ""
        summary_msg = (
            f"Da tim thay {total_found} video, "
            f"{total_queued} video dat dieu kien loc va dang xu ly{wave_info}."
        )
        supabase.table("download_jobs").update({
            "status": "success",
            "error_message": summary_msg,
        }).eq("id", channel_job_id).execute()

    except SoftTimeLimitExceeded:
        supabase.table("download_jobs").update({
            "status": "failed",
            "error_message": "Qua thoi gian quet kenh. Thu giam so video hoac chon kenh ngan hon.",
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
def create_zip_task(self, batch_id: str, zip_job_id: str, organize_by_channel: bool = False):
    supabase = get_supabase_client()
    try:
        supabase.table("download_jobs").update({
            "status": "processing",
            "job_stage": "zipping",
        }).eq("id", zip_job_id).execute()
        result = create_batch_zip_sync(batch_id, organize_by_channel=organize_by_channel)

        now_utc = datetime.now(timezone.utc)
        now_iso = now_utc.isoformat()
        if result.get("success"):
            zip_size = result.get("zip_size_mb", 0)
            total_files = result.get("total_files", 0)
            zip_expires_at = (now_utc + timedelta(minutes=13)).isoformat()  # batch cleanup at 15min
            zip_update = {
                "status": "success",
                "job_stage": "completed",
                "title": f"Batch {batch_id[:8]} — {total_files} files",
                "slugified_name": f"batch_{batch_id}",
                "direct_mp4_url": result.get("zip_path"),
                "link_expires_at": zip_expires_at,
                "created_at": now_iso,
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
                    .select("status, user_id")
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

            # ── Push: Notify user browser ─────────────────────────────────
            try:
                from app.api.push import send_push_notification
                user_ids = list(set(
                    j.get("user_id") for j in batch_jobs
                    if j.get("user_id")
                ))
                for uid in user_ids:
                    send_push_notification(
                        user_id=uid,
                        title="VidGrab — Batch hoan tat",
                        body=f"{success_count} video san sang tai xuong.",
                        url="/",
                    )
            except Exception as push_err:
                print(f"[Push] Failed to send batch push: {push_err}")

        else:
            error_msg = result.get("error", "Unknown zip error")
            supabase.table("download_jobs").update({
                "status": "failed",
                "job_stage": "failed",
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
    # NB: three dirname() — __file__ is app/tasks/video_tasks.py, so the real
    # downloads volume is at <repo>/downloads (/app/downloads), NOT app/downloads.
    # A two-dirname path silently no-ops cleanup and lets the disk fill up.
    DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
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

@celery_app.task(name="worker_heartbeat_check", bind=True)
def worker_heartbeat_check(self):
    """
    Phase 11: Periodic worker health check.
    Alerts via Telegram if no workers have been seen for >5 minutes.
    Updates a Redis key on every healthy check.
    """
    try:
        from app.core.queue_intelligence import _get_active_workers
        from app.core.redis_client import get_redis
        rc = get_redis()
        worker_count = _get_active_workers()
        if worker_count > 0:
            rc.set("vidgrab:last_worker_seen_at", datetime.now(timezone.utc).isoformat(), ex=3600)
        else:
            last_seen_raw = rc.get("vidgrab:last_worker_seen_at")
            if last_seen_raw:
                try:
                    last_seen = datetime.fromisoformat(last_seen_raw)
                    gap_min = (datetime.now(timezone.utc) - last_seen).total_seconds() / 60
                    if gap_min > 5:
                        alert_key = "vidgrab:alert:no_workers"
                        if not rc.get(alert_key):
                            rc.set(alert_key, "1", ex=1800)  # alert once per 30min
                            from app.core.notifications import send_telegram_message_sync
                            send_telegram_message_sync(
                                f"⚠️ <b>VidGrab — No Workers Detected</b>\n"
                                f"Last worker seen: {gap_min:.1f} min ago.\n"
                                f"Celery queue depth: {rc.llen('celery')} tasks pending.\n"
                                f"Action: check Celery containers.",
                                parse_mode="HTML",
                            )
                except Exception:
                    pass
    except Exception as e:
        print(f"[Heartbeat] worker check error: {e}")


@celery_app.task(name="periodic_cleanup_downloads", bind=True)
def periodic_cleanup_downloads(self):
    """
    Scan downloads dir: delete files respecting retention_status.
    - DB-driven: delete jobs where file_expires_at has passed (Phase 11)
    - Mtime-driven: delete orphaned files (no DB entry) older than LOCAL_FILE_TTL_SEC
    - Pinned: skip unless pin_expires_at passed
    - Emergency: if disk > 85%, aggressively evict oldest non-pinned
    After deleting, mark DB rows as expired_metadata_only.
    Also enforces DOWNLOADS_MAX_GB disk quota by LRU eviction.
    """
    import time
    DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "downloads")
    if not os.path.exists(DOWNLOAD_DIR):
        return

    MAX_GB    = float(os.getenv("DOWNLOADS_MAX_GB", "10"))
    MAX_BYTES = MAX_GB * 1024 ** 3
    now       = time.time()
    now_dt    = datetime.now(timezone.utc)
    # Keep files long enough for the shared YouTube cache (A2) to serve hot
    # videos for hours; disk still bounded by the MAX_GB LRU eviction below.
    expiry    = int(os.getenv("LOCAL_FILE_TTL_SEC", "7200"))  # 2h default

    # Record last cleanup time in Redis for health endpoint
    try:
        from app.core.redis_client import get_redis as _get_rc
        _get_rc().set("vidgrab:last_cleanup_at", now_dt.isoformat(), ex=86400)
    except Exception:
        pass

    # ── Emergency: check disk usage and alert / aggressively evict ──────
    try:
        _, _used, _free = __import__("shutil").disk_usage(DOWNLOAD_DIR)
        _total = _used + _free
        _disk_pct = _used / _total * 100 if _total else 0
        if _disk_pct > 70:
            try:
                from app.core.redis_client import get_redis as _get_rc2
                _rc2 = _get_rc2()
                _ak = "vidgrab:disk:cleanup_alert"
                if not _rc2.get(_ak):
                    _rc2.set(_ak, "1", ex=3600)
                    from app.core.notifications import send_telegram_message_sync as _tg
                    _tg(
                        f"⚠️ <b>VidGrab Disk Warning</b>\n"
                        f"Disk usage: {_disk_pct:.1f}% ({_free / 1e9:.1f} GB free)\n"
                        f"Cleanup running — may evict cached files.",
                        parse_mode="HTML",
                    )
            except Exception:
                pass
        if _disk_pct > 85:
            # Emergency: double the effective disk limit to force aggressive eviction
            MAX_BYTES = MAX_BYTES * 0.6
    except Exception:
        pass

    # ── Pass 0 (Phase 11): DB-driven expiry — delete files whose file_expires_at has passed ──
    try:
        from app.core.database import get_service_client as _get_svc0
        _sb0 = _get_svc0()
        _exp_res = (
            _sb0.table("download_jobs")
            .select("local_file_path, local_mp3_path, id")
            .eq("file_retention_status", "temporary")
            .lt("file_expires_at", now_dt.isoformat())
            .not_.is_("file_expires_at", "null")
            .execute()
        )
        _db_deleted = 0
        for _row in (_exp_res.data or []):
            for _col in ("local_file_path", "local_mp3_path"):
                _fp = _row.get(_col)
                if _fp and os.path.exists(_fp):
                    try:
                        os.remove(_fp)
                        _db_deleted += 1
                    except Exception:
                        pass
            try:
                _sb0.table("download_jobs").update({
                    "file_retention_status": "expired_metadata_only",
                    "local_file_path": None,
                    "local_mp3_path": None,
                }).eq("id", _row["id"]).execute()
            except Exception:
                pass
        if _db_deleted:
            print(f"[Cron] DB-driven expiry: removed {_db_deleted} expired files.")
    except Exception as _e0:
        print(f"[Cron] DB-driven expiry error: {_e0}")

    # Fetch pinned job paths from DB — we must NOT delete these unless pin expired
    pinned_paths: set = set()
    try:
        from app.core.database import get_service_client as _get_svc
        _sb = _get_svc()
        pinned_res = (
            _sb.table("download_jobs")
            .select("local_file_path, local_mp3_path, pin_expires_at")
            .eq("file_retention_status", "pinned")
            .execute()
        )
        for row in (pinned_res.data or []):
            pin_exp = row.get("pin_expires_at")
            if pin_exp:
                from datetime import datetime as _dt
                try:
                    exp_dt = _dt.fromisoformat(pin_exp.replace("Z", "+00:00"))
                    if exp_dt > now_dt:
                        # Still pinned — protect from deletion
                        if row.get("local_file_path"):
                            pinned_paths.add(os.path.normpath(row["local_file_path"]))
                        if row.get("local_mp3_path"):
                            pinned_paths.add(os.path.normpath(row["local_mp3_path"]))
                        continue
                except Exception:
                    pass
            # Pin expired — allow deletion below
    except Exception as e:
        print(f"[Cron] Could not fetch pinned paths: {e}")

    # Pass 1: delete expired files, skip pinned
    deleted = 0
    deleted_paths: list = []
    for item in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, item)
        norm = os.path.normpath(path)
        if norm in pinned_paths:
            continue
        try:
            if now - os.path.getmtime(path) > expiry:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
                deleted += 1
                deleted_paths.append(norm)
        except Exception as e:
            print(f"[Cron] cleanup error {path}: {e}")

    # Mark deleted files in DB as expired_metadata_only
    if deleted_paths:
        try:
            from app.core.database import get_service_client as _get_svc2
            _sb2 = _get_svc2()
            for dp in deleted_paths:
                try:
                    _sb2.table("download_jobs").update({
                        "file_retention_status": "expired_metadata_only",
                        "local_file_path": None,
                        "local_mp3_path": None,
                        "direct_mp4_url": None,
                    }).or_(
                        f"local_file_path.eq.{dp},local_mp3_path.eq.{dp}"
                    ).execute()
                except Exception:
                    pass
        except Exception as e:
            print(f"[Cron] DB status update error: {e}")

    # Pass 2: enforce disk quota — evict oldest non-pinned first
    all_files = []
    for item in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, item)
        if os.path.normpath(path) in pinned_paths:
            continue
        try:
            all_files.append((os.path.getmtime(path), os.path.getsize(path), path))
        except Exception:
            pass

    total_bytes = sum(s for _, s, _ in all_files)
    if total_bytes > MAX_BYTES:
        print(f"[Cron] Disk quota exceeded: {total_bytes/(1024**3):.2f}GB > {MAX_GB}GB — evicting oldest files")
        all_files.sort()  # oldest first
        for mtime, size, path in all_files:
            if total_bytes <= MAX_BYTES * 0.8:
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


# =════════════════════════════════════════════════════════════════════
# NEW: Daily Summary Report Task (Celery Beat)
# =════════════════════════════════════════════════════════════════════

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


# =════════════════════════════════════════════════════════════════════
# NEW: Credits Check Task (Celery Beat — every 6 hours)
# =════════════════════════════════════════════════════════════════════

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

        # Check ScraperAPI — all keys in pool
        try:
            from app.core.scraperapi_pool import fetch_all_credits
            key_stats = fetch_all_credits(use_cache=False)
            for ks in key_stats:
                credits = ks.get("credits")
                label = f"ScraperAPI [{ks['key_prefix']}]"
                if credits is None:
                    print(f"[Cron] {label}: fetch failed")
                    continue
                print(f"[Cron] {label}: {credits} credits remaining (active={ks['active']})")
                if credits < CREDITS_WARNING_THRESHOLD:
                    notify_credits_low_sync(label, credits)
        except Exception as e:
            print(f"[Cron] ScraperAPI check failed: {e}")

        print("[Cron] API credits check finished.")
    except Exception as e:
        print(f"[Cron] Credits check error: {e}")


# =════════════════════════════════════════════════════════════════════
# yt-dlp Auto-Update (daily at 3 AM UTC)
# =════════════════════════════════════════════════════════════════════

@celery_app.task(name="ytdlp_auto_update", bind=True)
def ytdlp_auto_update(self):
    """
    Upgrade yt-dlp to latest version daily.
    After successful upgrade: restart bgutil-pot containers (staggered),
    clear PO token cache, and trigger bgutil health probe.
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
            print(f"[ytdlp-update] Updated: {before} -> {after}")
        else:
            print(f"[ytdlp-update] Already latest: {before}")

        # ── Fix 4: Post-update — restart bgutil containers, clear PO cache ──
        import time as _time

        # Staggered restart: bgutil-pot-1 first, wait 10s, then bgutil-pot-2
        _bgutil_containers = ["vidgrab-bgutil-pot-1", "vidgrab-bgutil-pot-2-1"]
        for _cname in _bgutil_containers:
            try:
                _restart = subprocess.run(
                    ["docker", "restart", _cname],
                    capture_output=True, text=True, timeout=30
                )
                if _restart.returncode == 0:
                    print(f"[ytdlp-update] bgutil container {_cname} restarted")
                else:
                    print(f"[ytdlp-update] bgutil restart {_cname} failed: {_restart.stderr[:100]}")
            except Exception as _re:
                print(f"[ytdlp-update] bgutil restart {_cname} error: {_re}")
            _time.sleep(10)  # staggered — never both down simultaneously

        # Clear PO token cache (force fresh token on next request)
        try:
            from app.core.po_token_cache import invalidate_po_token
            invalidate_po_token()
            print("[ytdlp-update] PO token cache cleared")
        except Exception as _pe:
            print(f"[ytdlp-update] PO cache clear error: {_pe}")

        # Trigger immediate bgutil health probe
        try:
            probe_bgutil_health.delay()
            print("[ytdlp-update] bgutil health probe triggered")
        except Exception as _probe_e:
            print(f"[ytdlp-update] health probe trigger error: {_probe_e}")

        print("[ytdlp-update] yt-dlp updated → bgutil restarted → PO cache cleared → health probe triggered")

    except subprocess.TimeoutExpired:
        print("[ytdlp-update] pip upgrade timed out")
    except Exception as e:
        print(f"[ytdlp-update] Error: {e}")


# =════════════════════════════════════════════════════════════════════
# bgutil-pot Health Probe (every 15 minutes) — Fix 2
# =════════════════════════════════════════════════════════════════════

@celery_app.task(name="probe_bgutil_health", bind=True)
def probe_bgutil_health(self):
    """
    Test bgutil-pot by requesting a PO token for a known public video.
    Sets bgutil_unhealthy Redis flag (TTL 15 min) on failure.
    Clears the flag and records last-healthy timestamp on success.
    """
    from app.core.po_token_cache import (
        _call_bgutil, _get_bgutil_urls,
        mark_bgutil_healthy, mark_bgutil_unhealthy,
    )

    urls = _get_bgutil_urls()
    if not urls:
        print("[bgutil-health] No BGUTIL_POT_URL configured — skipping probe")
        return

    any_ok = False
    for url in urls:
        token, _ = _call_bgutil(url)
        if token:
            any_ok = True
            break

    if any_ok:
        mark_bgutil_healthy()
        print("[bgutil-health] ✓ Healthy")
    else:
        mark_bgutil_unhealthy(ttl=900)
        print("[bgutil-health] ✗ Unhealthy — set flag TTL=900s")


@celery_app.task(name="refresh_po_token", bind=True)
def refresh_po_token(self):
    """
    Pre-warm YouTube PO token every 3 hours.
    Keeps the Redis cache hot so no worker ever waits on bgutil-pot's
    headless Chrome during an actual download request.
    """
    from app.core.po_token_cache import refresh_po_token as _refresh, get_cache_ttl

    ttl_before = get_cache_ttl()
    print(f"[POToken-prewarm] TTL before refresh: {ttl_before}s")

    token = _refresh()
    if token:
        ttl_after = get_cache_ttl()
        print(f"[POToken-prewarm] Token refreshed, new TTL: {ttl_after}s ({token[:16]}...)")
    else:
        print("[POToken-prewarm] Failed to refresh — bgutil-pot may be down")


# =════════════════════════════════════════════════════════════════════
# YouTube Extraction Health Probe (every 10 minutes) — Fix 6
# =════════════════════════════════════════════════════════════════════

_YT_TEST_VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # known public, stable
_YT_HEALTH_SNAPSHOT_KEY = "yt:health:snapshot"
_YT_HEALTH_SNAPSHOT_TTL = 3600  # 1 hour


@celery_app.task(name="youtube_extraction_health_probe", bind=True)
def youtube_extraction_health_probe(self):
    """
    Every 10 minutes: probe android_vr, bgutil-pot, and Cobalt API.
    Writes a health snapshot to Redis. Sends Telegram CRITICAL alert
    when ALL layers are simultaneously unhealthy.
    """
    import time as _time
    import json as _json
    from app.core.redis_client import get_redis
    from app.core.po_token_cache import get_bgutil_health_status, mark_bgutil_healthy
    from app.core.oracle_circuit_breaker import oracle_circuit
    from app.core.notifications import send_telegram_message_sync

    rc = get_redis()
    now = _time.time()

    # ── Probe android_vr ──────────────────────────────────────────────
    avr_healthy = False
    try:
        import yt_dlp as _ydlp
        _avr_probe_opts = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "retries": 0,
            "extractor_retries": 0,
            "extract_flat": False,
            "socket_timeout": 15,
            "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
        }
        with _ydlp.YoutubeDL(_avr_probe_opts) as ydl:
            _probe_info = ydl.extract_info(_YT_TEST_VIDEO, download=False)
        avr_healthy = bool(_probe_info and _probe_info.get("formats"))
        rc.set("yt_android_vr_healthy", "1" if avr_healthy else "0", ex=_YT_HEALTH_SNAPSHOT_TTL)
    except Exception as _avr_err:
        rc.set("yt_android_vr_healthy", "0", ex=_YT_HEALTH_SNAPSHOT_TTL)
        print(f"[yt-health] android_vr probe failed: {str(_avr_err)[:80]}")

    # ── Probe bgutil-pot (reuse probe_bgutil_health logic) ────────────
    bgutil_status = get_bgutil_health_status()
    bgutil_last_checked = bgutil_status.get("last_healthy_at", 0) or 0
    if (now - bgutil_last_checked) > 600:
        # hasn't been probed in the last 10 min → trigger fresh probe
        probe_bgutil_health.delay()
        bgutil_healthy = bgutil_status.get("healthy", True)  # use last known
    else:
        bgutil_healthy = bgutil_status.get("healthy", True)

    # ── Probe Cobalt API ──────────────────────────────────────────────
    cobalt_healthy = False
    try:
        import httpx as _httpx
        _cobalt_base = "http://cobalt-api:9000"
        _resp = _httpx.get(f"{_cobalt_base}/api/serverInfo", timeout=5.0)
        cobalt_healthy = _resp.status_code == 200
        rc.set("cobalt_healthy", "1" if cobalt_healthy else "0", ex=_YT_HEALTH_SNAPSHOT_TTL)
    except Exception:
        rc.set("cobalt_healthy", "0", ex=_YT_HEALTH_SNAPSHOT_TTL)

    # ── Oracle circuit state ──────────────────────────────────────────
    circuit_status = oracle_circuit.get_status()

    # ── Build health snapshot ─────────────────────────────────────────
    snapshot = {
        "timestamp": now,
        "android_vr": avr_healthy,
        "bgutil": bgutil_healthy,
        "cobalt": cobalt_healthy,
        "oracle_circuit": circuit_status.get("state", "unknown"),
        "oracle_fail_count": circuit_status.get("fail_count", 0),
    }
    try:
        rc.set(_YT_HEALTH_SNAPSHOT_KEY, _json.dumps(snapshot), ex=_YT_HEALTH_SNAPSHOT_TTL)
    except Exception:
        pass

    print(f"[yt-health] android_vr={avr_healthy} bgutil={bgutil_healthy} cobalt={cobalt_healthy} circuit={circuit_status.get('state')}")

    # ── CRITICAL alert when ALL layers unhealthy ──────────────────────
    if not avr_healthy and not bgutil_healthy and not cobalt_healthy:
        try:
            send_telegram_message_sync(
                "🚨 *CRITICAL: YouTube toàn bộ layers đều unhealthy*\n"
                "• android\\_vr: ✗\n• bgutil-pot: ✗\n• Cobalt API: ✗\n"
                f"• Oracle circuit: {circuit_status.get('state', '?')}\n"
                "VidGrab YouTube downloads hiện tại đang TOÀN BỘ fail. Kiểm tra ngay!"
            )
        except Exception as _alert_err:
            print(f"[yt-health] Telegram CRITICAL alert failed: {_alert_err}")
    elif not avr_healthy or not bgutil_healthy or not cobalt_healthy:
        unhealthy = [k for k, v in [("android_vr", avr_healthy), ("bgutil", bgutil_healthy), ("cobalt", cobalt_healthy)] if not v]
        print(f"[yt-health] WARNING: {unhealthy} layer(s) unhealthy (non-critical — other layers available)")


# =════════════════════════════════════════════════════════════════════
# Cookie Expiry Monitor (daily at 9:30 AM UTC = 4:30 PM UTC+7)
# =════════════════════════════════════════════════════════════════════

@celery_app.task(name="check_cookie_expiry", bind=True)
def check_cookie_expiry(self):
    """
    Daily check: scan all cookie pools for expiring/expired cookies.
    Sends Telegram alert grouped by severity when found.
    """
    from app.core.cookie_pool import get_expiry_report
    from app.core.notifications import send_telegram_message_sync

    platforms = ["youtube", "tiktok", "facebook", "instagram"]
    expired_lines = []
    critical_lines = []
    soon_lines = []

    for platform in platforms:
        try:
            entries = get_expiry_report(platform)
        except Exception as e:
            print(f"[CookieMonitor] {platform} report error: {e}")
            continue

        for e in entries:
            status = e.get("expiry_status", "")
            if status not in ("expired", "critical", "expiring_soon"):
                continue
            label = e.get("label") or e.get("account_hint") or e["hash"][:8]
            days = e.get("days_left", "?")
            date_str = e.get("expires_at_str") or "?"
            line = f"  - <b>[{platform.upper()}]</b> {label} — {days} ngay con lai ({date_str})"
            if status == "expired":
                expired_lines.append(line)
            elif status == "critical":
                critical_lines.append(line)
            else:
                soon_lines.append(line)

    if not expired_lines and not critical_lines and not soon_lines:
        print("[CookieMonitor] All cookies healthy — no alerts needed")
        return

    sections = []
    if expired_lines:
        sections.append("<b>DA HET HAN (can thay ngay):</b>\n" + "\n".join(expired_lines))
    if critical_lines:
        sections.append("<b>SAP HET (con &lt;7 ngay):</b>\n" + "\n".join(critical_lines))
    if soon_lines:
        sections.append("<b>CANH BAO (con &lt;30 ngay):</b>\n" + "\n".join(soon_lines))

    message = (
        "<b>Cookie Expiry Alert — VidGrab</b>\n"
        "------------------------\n\n"
        + "\n\n".join(sections)
        + "\n\n-> Upload cookie moi: <code>POST /admin/cookies/upload</code>"
    )
    send_telegram_message_sync(message)
    print(f"[CookieMonitor] Alert sent: {len(expired_lines)} expired, {len(critical_lines)} critical, {len(soon_lines)} expiring soon")


# =════════════════════════════════════════════════════════════════════
# Stale Job Scanner (Celery Beat — every 2 minutes)
# =════════════════════════════════════════════════════════════════════

@celery_app.task(name="scan_stale_jobs", bind=True)
def scan_stale_jobs(self):
    """
    Periodic task: find jobs stuck in 'processing' and auto-recover or abandon.
    Runs every 2 minutes via Celery Beat.
    """
    try:
        from app.core.recovery import scan_stale_jobs as _scan
        _scan()
    except Exception as e:
        print(f"[scan_stale_jobs] error: {e}")


@celery_app.task(name="check_artist_health", bind=True)
def check_artist_health(self):
    """Spotify Artist health watchdog (P5b). Evaluates today's Redis counters
    and fires Telegram alerts (with SETNX cooldown) when:
      • artist expand success rate < 70%, or
      • SoundCloud resolve fail rate > 30% (Spotify's primary source), or
      • disk usage > 70%.
    Runs every 5 minutes via Celery Beat. Cheap, best-effort, never raises."""
    try:
        from app.core.spotify_artist_ops import (
            check_artist_health_and_alert, check_disk_and_alert,
        )
        report = check_artist_health_and_alert()
        check_disk_and_alert()
        if report.get("alerts_fired"):
            print(f"[check_artist_health] fired: {report['alerts_fired']}")
    except Exception as e:
        print(f"[check_artist_health] error: {e}")


@celery_app.task(name="check_youtube_health", bind=True)
def check_youtube_health(self):
    """YouTube proxy watchdog (Phase 8 / P9). Fires Telegram alerts on:
      • proxy bytes > 80% of the daily ceiling,
      • success rate < 60% (min sample),
      • circuit OPEN.
    Runs every 5 minutes via Celery Beat. Best-effort, never raises."""
    try:
        from app.core.youtube_gate import check_health_and_alert
        snap = check_health_and_alert()
        if snap.get("status_color") != "green":
            print(f"[check_youtube_health] {snap['status_color']}: "
                  f"{snap['bytes_gb']:.2f}/{snap['limit_gb']}GB "
                  f"(${snap['cost_today']}), cb={snap['circuit_state']}, "
                  f"rate={snap['success_rate']}%")
    except Exception as e:
        print(f"[check_youtube_health] error: {e}")


# ═════════════════════════════════════════════════════════════════════
# Daily Quota Reset (Celery Beat — 00:05 UTC)
# ═════════════════════════════════════════════════════════════════════

@celery_app.task(name="reset_daily_quota", bind=True)
def reset_daily_quota(self):
    """
    Reset downloads_today = 0 for ALL users in user_usage table.
    Runs at 00:05 UTC each day (crontab entry in celery_app.py).
    Also purges anonymous IP quota keys from Redis (they auto-expire anyway,
    but explicit purge makes reset instant after a restart at midnight).
    """
    try:
        supabase = get_supabase_client()
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()

        res = supabase.table("user_usage").update({
            "downloads_today": 0,
            "last_reset_at":   today,
        }).neq("user_id", "").execute()  # update all rows

        count = len(res.data) if res.data else 0
        print(f"[reset_daily_quota] Reset {count} users.")

        # Purge anon Redis keys for previous day (best-effort)
        try:
            from app.core.redis_client import get_redis
            r = get_redis()
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            for key in r.scan_iter(f"vidgrab:quota:anon:*:{yesterday}"):
                r.delete(key)
        except Exception as _re:
            print(f"[reset_daily_quota] Redis cleanup failed (non-fatal): {_re}")

    except Exception as e:
        print(f"[reset_daily_quota] error: {e}")


# ═════════════════════════════════════════════════════════════════════
# Subscription Expiry Enforcement (Celery Beat — 00:30 UTC)
# ═════════════════════════════════════════════════════════════════════

@celery_app.task(name="expire_subscriptions", bind=True)
def expire_subscriptions(self):
    """
    Find profiles with billing_status='canceling' and subscription_expiry < now.
    Downgrade them to tier='free' / billing_status='canceled'.
    Runs at 00:30 UTC each day so users who cancelled keep Pro until midnight
    after their billing period ends.
    """
    try:
        supabase = get_supabase_client()
        now_iso = datetime.now(timezone.utc).isoformat()

        res = (
            supabase.table("profiles")
            .select("id, tier, billing_status, subscription_expiry")
            .in_("billing_status", ["canceling", "past_due"])
            .lt("subscription_expiry", now_iso)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return

        for row in rows:
            uid = row["id"]
            try:
                supabase.table("profiles").update({
                    "tier":                    "free",
                    "billing_status":          "canceled",
                    "subscription_expiry":     None,
                    "stripe_subscription_id":  None,
                }).eq("id", uid).execute()
                # Keep user_usage.plan in sync
                supabase.table("user_usage").update({"plan": "free"}).eq("user_id", uid).execute()
                print(f"[expire_subscriptions] Downgraded {uid}")
            except Exception as _de:
                print(f"[expire_subscriptions] Failed to downgrade {uid}: {_de}")

        print(f"[expire_subscriptions] Processed {len(rows)} expired subscriptions.")
    except Exception as e:
        print(f"[expire_subscriptions] error: {e}")


# ── Phase 25: Container Discovery Task ───────────────────────────────────────

@celery_app.task(
    name="discover_container_task",
    bind=True,
    max_retries=2,
    soft_time_limit=120,
    time_limit=150,
)
def discover_container_task(
    self,
    url: str,
    container_id: str,
    max_items: int = 100,
    include_sections=None,
):
    """
    Async-in-Celery container discovery.
    Runs platform-specific expander, stores result in Redis via cache_container().
    Routes to: bulk queue (shared with scrape_channel_task).
    """
    from app.services.container_discovery import (
        get_cached_container, cache_container, update_container_fields,
        track_container_metric,
    )
    from app.services.container_expanders import get_expander

    try:
        # Refresh status to "discovering" (may be stale "pending" from API seed)
        update_container_fields(url, status="discovering")

        # Get cached meta to read platform/container_type
        meta = get_cached_container(url)
        platform = meta.platform if meta else "unknown"

        expander = get_expander(platform)
        if expander is None:
            update_container_fields(
                url,
                status="failed",
                error_code="C030",
                error_message=f"Platform '{platform}' chưa hỗ trợ container trong Phase 25.",
            )
            track_container_metric(platform, "discover_fail")
            return

        print(f"[discover_container] {platform} url={url[:80]} max={max_items}")
        result = expander.discover(url, max_items=max_items, include_sections=include_sections)

        # Preserve container_id from the seeded meta (expander creates its own)
        result.container_id = container_id
        import time as _t
        result.discovered_at = _t.time()

        cache_container(result)
        track_container_metric(platform, "discover_ok")
        print(f"[discover_container] done status={result.status} items={result.item_count}")

    except SoftTimeLimitExceeded:
        update_container_fields(
            url,
            status="partial",
            error_code="C051",
            error_message="Quét chưa hoàn thành do timeout. Kết quả hiện có vẫn dùng được.",
        )
        track_container_metric("unknown", "discover_timeout")

    except Exception as exc:
        print(f"[discover_container] error: {exc}")
        try:
            update_container_fields(
                url,
                status="failed",
                error_code="C001",
                error_message=str(exc)[:200],
            )
            track_container_metric("unknown", "discover_fail")
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=5)


@celery_app.task(name="aggregate_platform_health", bind=True, max_retries=2)
def aggregate_platform_health_task(self):
    """
    Hourly Celery task: aggregate download_jobs from the last hour
    per platform and write to platform_health_metrics.
    """
    import json as _json
    from datetime import datetime, timezone, timedelta
    from app.core.database import get_supabase_client
    from app.core.platform_circuit import _k as circuit_key
    from app.core.redis_client import get_redis

    try:
        sb = get_supabase_client()
        r = get_redis()
        now = datetime.now(timezone.utc)
        hour_bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
        since = (now - timedelta(hours=1)).isoformat()

        # Fetch all jobs from the past hour
        res = sb.table("download_jobs").select(
            "platform, status, duration_ms, error_message, updated_at"
        ).gt("updated_at", since).execute()
        jobs = res.data or []

        # Group by platform
        from collections import defaultdict
        by_platform = defaultdict(list)
        for j in jobs:
            p = j.get("platform") or "unknown"
            by_platform[p].append(j)

        for platform, pjobs in by_platform.items():
            total = len(pjobs)
            success = sum(1 for j in pjobs if j.get("status") == "completed")
            failed = sum(1 for j in pjobs if j.get("status") == "failed")
            durations = [j["duration_ms"] for j in pjobs if j.get("duration_ms")]
            avg_ms = int(sum(durations) / len(durations)) if durations else None
            sorted_d = sorted(durations)
            p95_ms = sorted_d[int(len(sorted_d) * 0.95)] if sorted_d else None

            error_counts = defaultdict(int)
            for j in pjobs:
                if j.get("error_message"):
                    key = j["error_message"][:80]
                    error_counts[key] += 1

            # Circuit open events in the hour
            circuit_state = r.get(circuit_key(platform, "state"))
            circuit_opens = 1 if circuit_state and circuit_state.decode() == "open" else 0

            try:
                sb.table("platform_health_metrics").upsert({
                    "platform": platform,
                    "hour_bucket": hour_bucket,
                    "total_jobs": total,
                    "success_jobs": success,
                    "failed_jobs": failed,
                    "avg_duration_ms": avg_ms,
                    "p95_duration_ms": p95_ms,
                    "error_counts": dict(error_counts),
                    "circuit_opens": circuit_opens,
                }, on_conflict="platform,hour_bucket").execute()
            except Exception as upsert_err:
                print(f"[aggregate_platform_health] upsert {platform}: {upsert_err}")

        print(f"[aggregate_platform_health] Done: {len(by_platform)} platforms, {len(jobs)} jobs")
    except Exception as e:
        print(f"[aggregate_platform_health] Error: {e}")
        raise self.retry(exc=e, countdown=300)
