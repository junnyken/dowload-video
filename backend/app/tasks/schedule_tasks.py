"""
Celery tasks for Scheduled Downloads (Phase 8).

scan_scheduled_jobs — runs every 60s via celery-beat.
Finds due scheduled_jobs rows, triggers appropriate download tasks,
updates last_run_at / next_run_at, deactivates 'once' jobs after run.
"""

from datetime import datetime, timezone, timedelta

from app.core.celery_app import celery_app


def _compute_next_run(job: dict) -> str | None:
    """Compute next_run_at after a successful trigger. Returns ISO string or None."""
    schedule_type = job.get("schedule_type")
    if schedule_type == "once":
        return None  # caller should set is_active=False

    run_at = job.get("run_at")        # HH:MM:SS string or None
    run_on_weekday = job.get("run_on_weekday")  # 0-6 or None
    now_utc = datetime.now(timezone.utc)

    # Parse time-of-day
    if run_at:
        try:
            parts = str(run_at).split(":")
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
        except Exception:
            h, m, s = 0, 0, 0
    else:
        h, m, s = 0, 0, 0

    if schedule_type == "daily":
        # Next occurrence of HH:MM:SS tomorrow (or today if not yet passed)
        candidate = now_utc.replace(hour=h, minute=m, second=s, microsecond=0)
        if candidate <= now_utc:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    if schedule_type == "weekly":
        weekday = run_on_weekday if run_on_weekday is not None else 0
        days_ahead = (weekday - now_utc.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        candidate = (now_utc + timedelta(days=days_ahead)).replace(
            hour=h, minute=m, second=s, microsecond=0
        )
        return candidate.isoformat()

    return None


@celery_app.task(name="scan_scheduled_jobs", ignore_result=True)
def scan_scheduled_jobs():
    """Scan for due scheduled jobs and trigger downloads. Runs every 60s."""
    from app.core.database import get_service_client
    supabase = get_service_client()

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        res = (
            supabase.table("scheduled_jobs")
            .select("*")
            .eq("is_active", True)
            .lte("next_run_at", now_iso)
            .execute()
        )
        due_jobs = res.data or []
    except Exception as e:
        print(f"[Scheduler] Query failed: {e}")
        return

    for job in due_jobs:
        _trigger_job(supabase, job, now_iso)


def _trigger_job(supabase, job: dict, now_iso: str):
    """Trigger one scheduled job and update its state."""
    job_id = job["id"]
    user_id = job.get("user_id")
    job_type = job.get("job_type")
    payload = job.get("input_payload", {})
    schedule_type = job.get("schedule_type")
    auto_col_id = job.get("auto_collection_id")

    # Mark running
    try:
        supabase.table("scheduled_jobs").update({
            "last_run_at":     now_iso,
            "last_run_status": "running",
        }).eq("id", job_id).execute()
    except Exception:
        pass

    success = True
    try:
        if job_type == "single":
            _trigger_single(supabase, user_id, payload, auto_col_id)
        elif job_type == "channel":
            _trigger_channel(supabase, user_id, payload, auto_col_id)
        elif job_type == "keyword":
            _trigger_keyword(supabase, user_id, payload, auto_col_id)
    except Exception as e:
        success = False
        print(f"[Scheduler] Job {job_id} trigger failed: {e}")

    # Compute next run or deactivate
    if schedule_type == "once":
        try:
            supabase.table("scheduled_jobs").update({
                "is_active":       False,
                "last_run_status": "success" if success else "failed",
            }).eq("id", job_id).execute()
        except Exception:
            pass
        return

    next_run = _compute_next_run(job)
    update = {"last_run_status": "success" if success else "failed"}
    if next_run:
        update["next_run_at"] = next_run
    try:
        supabase.table("scheduled_jobs").update(update).eq("id", job_id).execute()
    except Exception:
        pass


def _trigger_single(supabase, user_id: str, payload: dict, auto_col_id: str | None):
    from app.tasks.video_tasks import process_video_task
    import uuid

    url = payload.get("url", "")
    quality = payload.get("quality", "video")

    job = supabase.table("download_jobs").insert({
        "original_url": url,
        "status": "queued",
        "user_id": user_id,
        "selected_quality": quality,
    }).execute()
    job_id = job.data[0]["id"] if job.data else None

    if job_id:
        process_video_task.delay(
            job_id=str(job_id),
            url=url,
            quality=quality,
            user_id=user_id,
            auto_collection_id=str(auto_col_id) if auto_col_id else None,
        )


def _trigger_channel(supabase, user_id: str, payload: dict, auto_col_id: str | None):
    from app.tasks.video_tasks import scrape_channel_task
    import uuid

    url = payload.get("url", "")
    max_videos = payload.get("max_videos", 10)
    quality = payload.get("quality", "video")
    batch_id = str(uuid.uuid4())

    channel_job = supabase.table("download_jobs").insert({
        "original_url": url,
        "batch_id": batch_id,
        "status": "queued",
        "user_id": user_id,
    }).execute()
    channel_job_id = channel_job.data[0]["id"] if channel_job.data else str(uuid.uuid4())

    scrape_channel_task.delay(
        channel_url=url,
        batch_id=batch_id,
        channel_job_id=str(channel_job_id),
        max_videos=max_videos,
        user_id=user_id,
        quality=quality,
    )


def _trigger_keyword(supabase, user_id: str, payload: dict, auto_col_id: str | None):
    keyword = payload.get("keyword", "")
    platform = payload.get("platform", "youtube")
    count = min(payload.get("count", 5), 20)

    from app.api.search_videos import _build_search_url, _extract_results
    import yt_dlp, uuid

    search_url = _build_search_url(platform, keyword, count)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_url, download=False)
        entries = info.get("entries", [])
        results = _extract_results(entries, None, None, "views")

    if not results:
        return

    from app.tasks.video_tasks import process_video_task
    batch_id = str(uuid.uuid4())

    for item in results[:count]:
        url = item.get("url", "")
        if not url:
            continue
        job = supabase.table("download_jobs").insert({
            "original_url": url,
            "batch_id": batch_id,
            "status": "queued",
            "user_id": user_id,
        }).execute()
        job_id = job.data[0]["id"] if job.data else None
        if job_id:
            process_video_task.delay(job_id=str(job_id), url=url, user_id=user_id)


@celery_app.task(name="detect_schedule_drift", ignore_result=True)
def detect_schedule_drift():
    """Detect scheduled jobs whose next_run_at has drifted significantly from expected.
    Runs every 5 minutes (Phase 12 beat schedule).
    Writes drift alerts to Redis key vidgrab:schedule:drift_alerts (max 50).
    """
    try:
        import json
        from datetime import datetime, timezone, timedelta
        from app.core.database import get_supabase_client
        from app.core.redis_client import get_redis

        rc = get_redis()
        supabase = get_supabase_client()
        now_utc = datetime.now(timezone.utc)

        # Fetch active recurring jobs with a next_run_at
        # NOTE: scheduled_jobs has no top-level "url" column — the target URL
        # lives inside input_payload (JSONB), same as schedule_tasks._trigger_job.
        resp = supabase.table("scheduled_jobs").select(
            "id, input_payload, schedule_type, next_run_at, run_at, run_on_weekday"
        ).eq("is_active", True).neq("schedule_type", "once").execute()

        jobs = resp.data or []
        alerts = []

        for job in jobs:
            next_run_raw = job.get("next_run_at")
            if not next_run_raw:
                continue
            try:
                next_run = datetime.fromisoformat(next_run_raw.replace("Z", "+00:00"))
            except Exception:
                continue

            # Flag jobs overdue by more than 30 minutes
            drift_seconds = (now_utc - next_run).total_seconds()
            if drift_seconds > 1800:
                url = (job.get("input_payload") or {}).get("url", "")
                alerts.append({
                    "job_id": job["id"],
                    "url": url[:80],
                    "schedule_type": job.get("schedule_type"),
                    "next_run_at": next_run_raw,
                    "drift_minutes": round(drift_seconds / 60, 1),
                    "detected_at": now_utc.isoformat(),
                })

        if alerts:
            DRIFT_KEY = "vidgrab:schedule:drift_alerts"
            for alert in alerts:
                rc.lpush(DRIFT_KEY, json.dumps(alert))
            rc.ltrim(DRIFT_KEY, 0, 49)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[detect_schedule_drift] {e}")
