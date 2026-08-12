"""
Recovery Service
=================
Finds jobs stuck in non-terminal states after a worker crash and either
re-queues them or marks them abandoned.

Called:
  - startup_recovery_scan()        — synchronously at app boot (lifespan)
  - scan_stale_jobs()              — Celery Beat task every 2 min
  - refresh_job_link()             — on-demand when a user requests link refresh

Heartbeat-aware staleness
─────────────────────────
Before declaring a job stale the scanner checks `vidgrab:job_hb:{job_id}` via
job_lease.is_alive().  If the key exists and is recent the worker is still
running and we must NOT re-queue.  This prevents false-positive recovery of
jobs that are legitimately taking 8–12 minutes (close to Celery hard limit).

Recovery log
─────────────
Every recovery action writes a compact entry to `vidgrab:recovery_log` (a Redis
list, capped at 200 entries, 7-day TTL) so the admin dashboard can surface
recent activity without a database query.
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional


def _track(event: str) -> None:
    """Fire-and-forget recovery counter into the existing container metrics store."""
    try:
        from app.services.container_discovery import track_container_metric
        track_container_metric("system", event)
    except Exception:
        pass

# Thresholds tightened so a stuck job frees up FAST (env-overridable).
# A download task is hard-killed by Celery at ~12 min (task_time_limit=720s).
# The heartbeat key solves the original dilemma: we can now safely lower
# STUCK_PROCESSING_MINUTES without fear of re-queuing live jobs — a live job
# always holds a fresh heartbeat key.
STUCK_PROCESSING_MINUTES  = int(os.getenv("RECOVERY_STUCK_MIN",              "8"))
STUCK_PENDING_MINUTES     = int(os.getenv("RECOVERY_PENDING_STUCK_MIN",      "2"))
ABANDONED_MINUTES         = int(os.getenv("RECOVERY_ABANDONED_MIN",          "25"))
PENDING_ABANDONED_MINUTES = int(os.getenv("RECOVERY_PENDING_ABANDONED_MIN",  "30"))
MAX_AUTO_RECOVERY         = int(os.getenv("RECOVERY_MAX_ATTEMPTS",           "3"))
# Container discovery: jobs stuck in `discovering` for > this are stale
STUCK_DISCOVERING_MINUTES = int(os.getenv("RECOVERY_DISCOVERING_STUCK_MIN",  "5"))

# Recovery log config
_RECOVERY_LOG_KEY = "vidgrab:recovery_log"
_RECOVERY_LOG_CAP = 200
_RECOVERY_LOG_TTL = 7 * 86400   # 7 days


def _log_recovery(action: str, job_id: str, reason: str = "") -> None:
    """Append a compact recovery event to the Redis recovery log. Never raises."""
    try:
        import json
        from app.core.redis_client import get_redis
        entry = json.dumps({
            "ts":     datetime.now(timezone.utc).isoformat(),
            "action": action,
            "job_id": job_id,
            "reason": reason,
        })
        rc = get_redis()
        rc.lpush(_RECOVERY_LOG_KEY, entry)
        rc.ltrim(_RECOVERY_LOG_KEY, 0, _RECOVERY_LOG_CAP - 1)
        rc.expire(_RECOVERY_LOG_KEY, _RECOVERY_LOG_TTL)
    except Exception:
        pass


def get_recovery_log(limit: int = 50) -> list:
    """Return recent recovery log entries (most recent first). Safe to call from admin API."""
    try:
        import json
        from app.core.redis_client import get_redis
        rc = get_redis()
        raw_entries = rc.lrange(_RECOVERY_LOG_KEY, 0, min(limit, _RECOVERY_LOG_CAP) - 1)
        result = []
        for raw in raw_entries:
            try:
                s = raw.decode() if isinstance(raw, bytes) else raw
                result.append(json.loads(s))
            except Exception:
                pass
        return result
    except Exception:
        return []


def _get_job_age_minutes(job: dict) -> float:
    """Return minutes since this job was last updated (or created)."""
    ts_str = job.get("updated_at") or job.get("created_at")
    if not ts_str:
        return 0.0
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - ts).total_seconds() / 60
    except Exception:
        return 0.0


def _is_job_alive(job_id: str) -> bool:
    """
    Check if the job has a recent heartbeat key (worker is still running).
    Returns False if job_lease module is unavailable (fail safe: treat as dead).
    """
    try:
        from app.core.job_lease import is_alive
        return is_alive(job_id)
    except Exception:
        return False


def _invalidate_job_lease(job_id: str) -> None:
    """Force-release the lease so a recovery re-queue can take ownership."""
    try:
        from app.core.job_lease import invalidate_lease, clear_heartbeat
        invalidate_lease(job_id)
        clear_heartbeat(job_id)
    except Exception:
        pass


def _recover_or_abandon(supabase, job: dict, source: str = "startup") -> str:
    """
    Classify a stuck processing job and act on it.

    Heartbeat guard: if the job has a live heartbeat key the worker is still
    running — skip without action.

    Returns: 'recovered' | 'abandoned' | 'skipped' | 'skip'
    """
    jid = job["id"]
    url = job.get("original_url", "")
    if not url or url == "batch_zip":
        return "skip"

    # ── Heartbeat guard: skip jobs that are still alive ──────────────────
    if _is_job_alive(jid):
        return "skip"   # worker is alive; don't interfere

    age_min   = _get_job_age_minutes(job)
    attempts  = job.get("recovery_attempts") or 0

    if age_min < STUCK_PROCESSING_MINUTES:
        return "skip"  # too young to be stale without heartbeat (brief processing)

    # Cooldown: skip if already recovered within the scan window
    last_rec = job.get("last_recovery_at")
    if last_rec:
        try:
            last_rec_dt = datetime.fromisoformat(last_rec.replace("Z", "+00:00"))
            cooldown_min = STUCK_PROCESSING_MINUTES
            if (datetime.now(timezone.utc) - last_rec_dt).total_seconds() < cooldown_min * 60:
                return "skip"
        except Exception:
            pass

    # ── Stage: mark stale before deciding ────────────────────────────────
    # Transition processing → stale first so the UI shows something meaningful
    # while we decide whether to retry or abandon.
    # NOTE: job_status enum only has pending/processing/success/failed — "stale"
    # is not a valid status value, so we only mark job_stage here and leave
    # status as "processing" (see _recover_stale_db_jobs / startup_recovery_scan,
    # which key off job_stage="stale" AND status="processing" instead).
    try:
        supabase.table("download_jobs").update({
            "job_stage": "stale",
        }).eq("id", jid).eq("status", "processing").execute()
    except Exception:
        pass  # if update fails (e.g. already moved), proceed anyway
    try:
        from app.core.metrics import emit_job_event
        emit_job_event("stale", job_id=jid, platform=job.get("platform") or "other")
    except Exception:
        pass

    if age_min > ABANDONED_MINUTES or attempts >= MAX_AUTO_RECOVERY:
        try:
            supabase.table("download_jobs").update({
                "status":             "failed",
                "job_stage":          "abandoned",
                "error_message":      "⚠ Job không phản hồi — đã dừng tự động. Nhấn Retry để thử lại.",
                "last_recovery_at":   datetime.now(timezone.utc).isoformat(),
            }).eq("id", jid).execute()
        except Exception as e:
            print(f"[Recovery:{source}] abandon failed for {jid}: {e}")
            return "skip"
        _invalidate_job_lease(jid)
        _track("job_abandoned")
        _log_recovery("abandoned", jid, reason=f"age={age_min:.1f}min attempts={attempts}")
        return "abandoned"

    # ── Re-queue ──────────────────────────────────────────────────────────
    quality = job.get("selected_quality") or "video"
    try:
        from app.tasks.video_tasks import process_video_task
        _invalidate_job_lease(jid)
        supabase.table("download_jobs").update({
            "status":             "pending",
            "job_stage":          "queued",
            "error_message":      "♻ Tự động khôi phục sau sự cố worker...",
            "recovery_attempts":  attempts + 1,
            "last_recovery_at":   datetime.now(timezone.utc).isoformat(),
        }).eq("id", jid).execute()
        process_video_task.delay(jid, url, None, quality)
    except Exception as e:
        print(f"[Recovery:{source}] re-queue failed for {jid}: {e}")
        return "skip"
    _track("job_recovered")
    _log_recovery("recovered", jid, reason=f"age={age_min:.1f}min attempts={attempts}")
    return "recovered"


def _recover_pending(supabase, job: dict, source: str = "startup") -> str:
    """
    Classify a stuck pending job and re-queue or abandon it.
    Returns: 'recovered' | 'succeeded' | 'abandoned' | 'skip'
    """
    jid = job["id"]
    url = job.get("original_url", "")
    if not url or url == "batch_zip":
        return "skip"

    age_min = _get_job_age_minutes(job)
    if age_min < STUCK_PENDING_MINUTES:
        return "skip"  # still plausibly queued

    # Already has a direct URL → mark success without re-downloading
    if job.get("direct_mp4_url"):
        try:
            supabase.table("download_jobs").update({
                "status":    "success",
                "job_stage": "done",
                "error_message": "✅ Link đã có sẵn — tiếp tục từ lần trước.",
            }).eq("id", jid).execute()
        except Exception as e:
            print(f"[Recovery:{source}] mark-success failed for {jid}: {e}")
            return "skip"
        _log_recovery("auto_succeeded", jid, reason="direct_mp4_url present")
        return "succeeded"

    attempts = job.get("recovery_attempts") or 0

    if age_min > PENDING_ABANDONED_MINUTES or attempts >= MAX_AUTO_RECOVERY:
        try:
            supabase.table("download_jobs").update({
                "status":        "failed",
                "job_stage":     "abandoned",
                "error_message": "⚠ Job chờ quá lâu không có worker. Nhấn Retry để thử lại.",
                "last_recovery_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", jid).execute()
        except Exception as e:
            print(f"[Recovery:{source}] abandon-pending failed for {jid}: {e}")
            return "skip"
        _log_recovery("abandoned", jid, reason=f"pending too long age={age_min:.1f}min")
        return "abandoned"

    # Re-queue
    quality = job.get("selected_quality") or "video"
    try:
        from app.tasks.video_tasks import process_video_task
        supabase.table("download_jobs").update({
            "status":            "pending",
            "job_stage":         "queued",
            "error_message":     "⏳ Đang tiếp tục xử lý...",
            "recovery_attempts": attempts + 1,
            "last_recovery_at":  datetime.now(timezone.utc).isoformat(),
        }).eq("id", jid).execute()
        process_video_task.delay(jid, url, None, quality)
    except Exception as e:
        print(f"[Recovery:{source}] re-queue-pending failed for {jid}: {e}")
        return "skip"
    _log_recovery("recovered", jid, reason=f"pending requeue age={age_min:.1f}min")
    return "recovered"


def _recover_stale_db_jobs(supabase, source: str = "scan") -> tuple:
    """
    Scan download_jobs marked stale (job_stage="stale", status stays "processing" —
    "stale" is not a valid job_status enum value) and decide: retry or abandon.
    Returns (recovered, abandoned) counts.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_PROCESSING_MINUTES)).isoformat()
        res = (
            supabase.table("download_jobs")
            .select("id, original_url, job_stage, recovery_attempts, selected_quality, updated_at, created_at, last_recovery_at")
            .eq("status", "processing")
            .eq("job_stage", "stale")
            .lt("updated_at", cutoff)
            .execute()
        )
        stale_jobs = res.data or []
    except Exception as e:
        print(f"[Recovery:{source}] stale-db scan failed: {e}")
        return 0, 0

    recovered = abandoned = 0
    for job in stale_jobs:
        jid = job["id"]
        url = job.get("original_url", "")
        if not url or url == "batch_zip":
            continue

        attempts = job.get("recovery_attempts") or 0
        age_min  = _get_job_age_minutes(job)

        # Double-check heartbeat: might have just come back alive
        if _is_job_alive(jid):
            try:
                supabase.table("download_jobs").update({
                    "status": "processing",
                    "job_stage": "extracting",
                }).eq("id", jid).execute()
            except Exception:
                pass
            continue

        if age_min > ABANDONED_MINUTES or attempts >= MAX_AUTO_RECOVERY:
            try:
                supabase.table("download_jobs").update({
                    "status":            "failed",
                    "job_stage":         "abandoned",
                    "error_message":     "⚠ Job không phản hồi — đã dừng tự động. Nhấn Retry để thử lại.",
                    "last_recovery_at":  datetime.now(timezone.utc).isoformat(),
                }).eq("id", jid).execute()
                _invalidate_job_lease(jid)
            except Exception:
                pass
            abandoned += 1
            _log_recovery("abandoned", jid, reason=f"stale→failed age={age_min:.1f}min")
        else:
            quality = job.get("selected_quality") or "video"
            try:
                from app.tasks.video_tasks import process_video_task
                _invalidate_job_lease(jid)
                supabase.table("download_jobs").update({
                    "status":            "pending",
                    "job_stage":         "queued",
                    "error_message":     "♻ Tự động khôi phục sau sự cố worker...",
                    "recovery_attempts": attempts + 1,
                    "last_recovery_at":  datetime.now(timezone.utc).isoformat(),
                }).eq("id", jid).execute()
                process_video_task.delay(jid, url, None, quality)
                recovered += 1
                _log_recovery("recovered", jid, reason=f"stale→requeue age={age_min:.1f}min")
            except Exception as e:
                print(f"[Recovery:{source}] stale requeue failed for {jid}: {e}")

    return recovered, abandoned


def _recover_stale_discovery_jobs(source: str = "scan") -> int:
    """
    Scan Redis container:job:* snapshots in discovering/resolving/expanding status
    that have not been updated for > STUCK_DISCOVERING_MINUTES.
    Marks them as failed in Redis so the frontend stops polling.

    Returns count of jobs marked stale.
    """
    marked = 0
    try:
        from app.core.redis_client import get_redis
        from app.services.container_cache import get_job, patch_job, release_discovery_lock
        from app.schemas.container_discovery import DiscoveryJobStatus

        rc = get_redis()
        cursor = 0
        stuck_statuses = {
            DiscoveryJobStatus.queued.value,
            DiscoveryJobStatus.resolving.value,
            DiscoveryJobStatus.discovering.value,
            DiscoveryJobStatus.expanding.value,
        }
        threshold = STUCK_DISCOVERING_MINUTES * 60

        while True:
            cursor, keys = rc.scan(cursor, match="container:job:*", count=200)
            for key in keys:
                raw = rc.get(key)
                if not raw:
                    continue
                try:
                    from app.schemas.container_discovery import DiscoveryJobSnapshot
                    snap = DiscoveryJobSnapshot.model_validate_json(
                        raw.decode() if isinstance(raw, bytes) else raw
                    )
                except Exception:
                    continue

                if snap.status.value not in stuck_statuses:
                    continue

                now = __import__("time").time()
                age = now - (snap.updated_at or snap.created_at or now)
                if age < threshold:
                    continue

                # Job is stuck — mark failed
                try:
                    patch_job(
                        snap.job_id,
                        status=DiscoveryJobStatus.failed,
                        progress_pct=0,
                        message="⚠ Không hoàn thành — tự động dừng sau khi timeout.",
                        terminal_reason="stale_recovery",
                    )
                    # Release any lingering lock for this URL
                    if snap.canonical_url:
                        try:
                            release_discovery_lock(snap.canonical_url)
                        except Exception:
                            pass
                    marked += 1
                    _log_recovery("container_stale", snap.job_id,
                                  reason=f"age={age:.0f}s status={snap.status.value}")
                except Exception:
                    pass

            if cursor == 0:
                break
    except Exception as e:
        print(f"[Recovery:{source}] discovery scan failed: {e}")

    return marked


def startup_recovery_scan() -> None:
    """
    Run synchronously at application startup.
    Finds any jobs left 'processing', 'pending', or 'stale' from the previous
    deployment/crash and re-queues or abandons them.
    """
    from app.core.database import get_service_client
    supabase = get_service_client()

    # Scan stuck 'processing' jobs
    try:
        res = (
            supabase.table("download_jobs")
            .select("id, original_url, job_stage, recovery_attempts, selected_quality, direct_mp4_url, updated_at, created_at, last_recovery_at")
            .eq("status", "processing")
            .execute()
        )
        stuck_processing = res.data or []
    except Exception as e:
        print(f"[Recovery:startup] processing scan failed: {e}")
        stuck_processing = []

    # Scan stuck 'pending' jobs (older than STUCK_PENDING_MINUTES)
    try:
        cutoff_pending = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_PENDING_MINUTES)).isoformat()
        res2 = (
            supabase.table("download_jobs")
            .select("id, original_url, job_stage, recovery_attempts, selected_quality, direct_mp4_url, updated_at, created_at, last_recovery_at")
            .eq("status", "pending")
            .lt("updated_at", cutoff_pending)
            .execute()
        )
        stuck_pending = res2.data or []
    except Exception as e:
        print(f"[Recovery:startup] pending scan failed: {e}")
        stuck_pending = []

    # Scan leftover 'stale' jobs from previous run (job_stage="stale", status stays "processing")
    try:
        res3 = (
            supabase.table("download_jobs")
            .select("id, original_url, job_stage, recovery_attempts, selected_quality, updated_at, created_at, last_recovery_at")
            .eq("status", "processing")
            .eq("job_stage", "stale")
            .execute()
        )
        leftover_stale = res3.data or []
    except Exception as e:
        print(f"[Recovery:startup] stale scan failed: {e}")
        leftover_stale = []

    recovered = abandoned = succeeded = skipped = 0
    for job in stuck_processing:
        outcome = _recover_or_abandon(supabase, job, source="startup")
        if outcome == "recovered":   recovered  += 1
        elif outcome == "abandoned": abandoned  += 1
        else:                        skipped    += 1

    for job in stuck_pending:
        outcome = _recover_pending(supabase, job, source="startup")
        if outcome == "recovered":   recovered  += 1
        elif outcome == "succeeded": succeeded  += 1
        elif outcome == "abandoned": abandoned  += 1
        else:                        skipped    += 1

    # Reprocess leftover stale rows from the previous boot
    for job in leftover_stale:
        jid = job["id"]
        url = job.get("original_url", "")
        if not url or url == "batch_zip":
            continue
        attempts = job.get("recovery_attempts") or 0
        quality  = job.get("selected_quality") or "video"
        try:
            from app.tasks.video_tasks import process_video_task
            _invalidate_job_lease(jid)
            supabase.table("download_jobs").update({
                "status":            "pending",
                "job_stage":         "queued",
                "error_message":     "♻ Khởi động lại — tiếp tục xử lý...",
                "recovery_attempts": attempts + 1,
                "last_recovery_at":  datetime.now(timezone.utc).isoformat(),
            }).eq("id", jid).execute()
            process_video_task.delay(jid, url, None, quality)
            recovered += 1
            _log_recovery("startup_stale_requeue", jid)
        except Exception as e:
            print(f"[Recovery:startup] stale requeue failed for {jid}: {e}")
            skipped += 1

    total = recovered + abandoned + succeeded
    if total:
        print(f"[Recovery:startup] {recovered} re-queued, {succeeded} auto-succeeded, {abandoned} abandoned ({skipped} skipped)")
    else:
        print(f"[Recovery:startup] clean — no stuck jobs")


def scan_stale_jobs() -> None:
    """
    Periodic scan (Celery Beat, every 2 min).

    Checks:
      1. `processing` jobs older than STUCK_PROCESSING_MINUTES AND no live heartbeat
      2. `pending` jobs older than STUCK_PENDING_MINUTES with no task ever dispatched
         (e.g. a wave-dispatch loop that raised partway through a channel-scrape
         batch, or any other apply_async that silently never reached the broker) —
         previously this class was only recovered once, at process startup, so a
         job stuck here between deploys was stuck forever with no self-healing.
      3. `stale` rows that were marked by a previous cycle
      4. Redis container discovery snapshots stuck in discovering/resolving/expanding
    """
    from app.core.database import get_service_client
    supabase = get_service_client()

    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_PROCESSING_MINUTES)).isoformat()

    # 1. Scan stuck 'processing' jobs
    try:
        res = (
            supabase.table("download_jobs")
            .select("id, original_url, job_stage, recovery_attempts, selected_quality, updated_at, created_at, last_recovery_at")
            .eq("status", "processing")
            .lt("updated_at", cutoff)
            .execute()
        )
        stuck = res.data or []
    except Exception as e:
        print(f"[Recovery:scan] query failed (migration pending?): {e}")
        stuck = []

    recovered = abandoned = succeeded = 0
    for job in stuck:
        outcome = _recover_or_abandon(supabase, job, source="scan")
        if outcome == "recovered":   recovered += 1
        elif outcome == "abandoned": abandoned += 1

    # 2. Scan stuck 'pending' jobs (never dispatched, or dispatch was lost)
    try:
        cutoff_pending = (datetime.now(timezone.utc) - timedelta(minutes=STUCK_PENDING_MINUTES)).isoformat()
        res_pending = (
            supabase.table("download_jobs")
            .select("id, original_url, job_stage, recovery_attempts, selected_quality, direct_mp4_url, updated_at, created_at, last_recovery_at")
            .eq("status", "pending")
            .lt("updated_at", cutoff_pending)
            .execute()
        )
        stuck_pending = res_pending.data or []
    except Exception as e:
        print(f"[Recovery:scan] pending query failed: {e}")
        stuck_pending = []

    for job in stuck_pending:
        outcome = _recover_pending(supabase, job, source="scan")
        if outcome == "recovered":   recovered += 1
        elif outcome == "succeeded": succeeded += 1
        elif outcome == "abandoned": abandoned += 1

    # 3. Scan leftover 'stale' DB rows
    stale_rec, stale_aban = _recover_stale_db_jobs(supabase, source="scan")
    recovered += stale_rec
    abandoned += stale_aban

    # 4. Scan stuck Redis discovery jobs
    discovery_marked = _recover_stale_discovery_jobs(source="scan")

    if recovered or abandoned or succeeded or discovery_marked:
        print(f"[Recovery:scan] {recovered} recovered, {succeeded} auto-succeeded, {abandoned} abandoned, {discovery_marked} discovery-stale")


def refresh_job_link(job_id: str, user_id: Optional[str] = None) -> dict:
    """
    Re-extract a job whose download link has expired or is broken.
    Safe to call on success or failed jobs.
    Returns {"success": bool, "message": str}
    """
    from app.core.database import get_service_client
    from app.tasks.video_tasks import process_video_task
    supabase = get_service_client()

    try:
        res = (
            supabase.table("download_jobs")
            .select("id, original_url, status, job_stage, recovery_attempts, selected_quality")
            .eq("id", job_id)
            .single()
            .execute()
        )
        job = res.data
    except Exception:
        return {"success": False, "message": "Job không tìm thấy"}

    if not job:
        return {"success": False, "message": "Job không tìm thấy"}

    url = job.get("original_url", "")
    if not url or url == "batch_zip":
        return {"success": False, "message": "Job này không thể làm mới"}

    if job.get("status") not in ("success", "failed", "stale", "abandoned"):
        return {"success": False, "message": "Job đang xử lý, vui lòng đợi"}

    attempts = job.get("recovery_attempts") or 0
    quality  = job.get("selected_quality") or "video"

    try:
        _invalidate_job_lease(job_id)
        supabase.table("download_jobs").update({
            "status":            "pending",
            "job_stage":         "queued",
            "direct_mp4_url":    None,
            "link_expires_at":   None,
            "error_message":     "🔄 Đang làm mới link tải...",
            "recovery_attempts": attempts + 1,
            "last_recovery_at":  datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).execute()

        process_video_task.delay(job_id, url, user_id, quality)
        _log_recovery("manual_refresh", job_id)
        return {"success": True, "message": "Đang làm mới link, vui lòng chờ..."}
    except Exception as e:
        return {"success": False, "message": str(e)[:200]}
