import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "video_downloader",
    broker=redis_url,
    backend=redis_url,
    include=[
        "app.tasks.video_tasks",
        "app.tasks.archive_tasks",
        "app.tasks.schedule_tasks",
        "app.tasks.intelligence_tasks",
        "app.tasks.analytics_tasks",
        "app.tasks.analysis_tasks",
        "app.tasks.transcript_translation_tasks",
        "app.tasks.transcript_asr_tasks",
        "app.tasks.keepalive_tasks",
        "app.tasks.container_tasks",
        "app.tasks.partner_tasks",
    ]
)

from celery.schedules import crontab

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_time_limit=720,        # hard kill after 12 min
    task_soft_time_limit=660,   # soft signal at 11 min so task can clean up
    worker_concurrency=8,
    # ── Durability (P7) ────────────────────────────────────────────────
    # Ack a task only AFTER it finishes, and re-queue it if the worker is lost
    # (deploy restart / OOM-kill) mid-flight — so an interrupted download is
    # recovered, not silently dropped. This only redelivers on genuine worker
    # death (NOT on per-task time-limit, which the parent acks as a failure),
    # so there is no poison-message loop. prefetch=1 bounds how many in-flight
    # messages a dying worker redelivers. Tasks are idempotent (same job row).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={
        'priority_steps': list(range(10)),
        'sep': ':',
        'queue_order_strategy': 'priority',
        # Redelivery window must exceed the longest task (hard limit 720s) so a
        # still-running acks_late task is never redelivered while alive.
        'visibility_timeout': 1800,
    },
    task_routes={
        # Heavy downloads run on a dedicated worker so they can't starve light
        # work (scrape dispatch, zip, periodic). Everything else → default queue.
        'process_video_task': {'queue': 'downloads'},
        # Partner jobs share the downloads worker (high-priority, same infra).
        'process_partner_job': {'queue': 'downloads'},
        'scrape_channel_task': {'queue': 'bulk'},
        'create_zip_task': {'queue': 'bulk'},
        # Phase 25 — container discovery (metadata only, no downloads)
        'discover_container_task': {'queue': 'bulk'},
        'expand_container_section_task': {'queue': 'bulk'},
        'cleanup_stale_container_jobs': {'queue': 'light'},
        # FFmpeg-heavy media processing — isolated on celery-media worker.
        'trim_video_task': {'queue': 'media'},
        'create_gif_task': {'queue': 'media'},
        'inpaint_logo_task': {'queue': 'media'},
        'watermark_task': {'queue': 'media'},
        'merge_audio_video_task': {'queue': 'media'},
        'render_video_task': {'queue': 'media'},
        # Phase 18 — AI analysis jobs → dedicated analysis worker.
        'analyze_media_task': {'queue': 'analysis'},
        'expire_analysis_jobs_task': {'queue': 'analysis'},
        # Transcript translation — reuses the 'analysis' worker (also
        # LLM-bound, bounded, moderate-concurrency work) rather than a new
        # 'translation' queue: no worker in docker-compose.yml consumes a
        # 'translation' queue, so tasks routed there would queue forever.
        # If translation volume grows enough to starve AI-analysis jobs on
        # the shared 2-concurrency worker, split this back out to its own
        # dedicated worker service.
        'translate_transcript_task': {'queue': 'analysis'},
        'expire_transcript_translation_jobs_task': {'queue': 'analysis'},
        # Transcript ASR — same reasoning as translate above.
        'transcribe_video_task': {'queue': 'analysis'},
        'expire_transcript_asr_jobs_task': {'queue': 'analysis'},
        # Webhook/partner delivery retries → light celery queue.
        'retry_webhook_delivery': {'queue': 'celery'},
        '*': {'queue': 'celery'},
    },
    beat_schedule={
        # Cleanup temp downloads every 5 minutes (+ enforce disk quota)
        'cleanup-downloads-every-5-minutes': {
            'task': 'periodic_cleanup_downloads',
            'schedule': 300.0,
        },
        # Phase 25 PR3 — clean stale discovery locks every 10 minutes
        'cleanup-stale-container-jobs-every-10min': {
            'task': 'app.tasks.container_tasks.cleanup_stale_container_jobs',
            'schedule': 600.0,
        },
        # Daily summary report at 23:00 UTC (6:00 AM UTC+7)
        'daily-summary-report': {
            'task': 'daily_summary_report',
            'schedule': crontab(hour=23, minute=0),
        },
        # Check API credits every 6 hours
        'check-api-credits-every-6h': {
            'task': 'check_api_credits',
            'schedule': crontab(hour='*/6', minute=15),
        },
        # Auto-update yt-dlp daily at 3 AM UTC (10 AM UTC+7)
        'ytdlp-auto-update-daily': {
            'task': 'ytdlp_auto_update',
            'schedule': crontab(hour=3, minute=0),
        },
        # Pre-warm YouTube PO token every 3 hours (token valid ~6h, TTL 3.5h)
        # Ensures no worker waits on bgutil-pot Chrome during real downloads
        'refresh-po-token-every-3h': {
            'task': 'refresh_po_token',
            'schedule': crontab(minute=5, hour='*/3'),
        },
        # Daily cookie expiry check at 9:30 AM UTC (4:30 PM UTC+7)
        'check-cookie-expiry-daily': {
            'task': 'check_cookie_expiry',
            'schedule': crontab(hour=9, minute=30),
        },
        # Stale job scanner — find and recover/abandon stuck processing jobs
        'scan-stale-jobs-every-2min': {
            'task': 'scan_stale_jobs',
            'schedule': 120.0,
        },
        # Scheduled download scanner — trigger due scheduled_jobs every 60s
        'scan-scheduled-jobs-every-1min': {
            'task': 'scan_scheduled_jobs',
            'schedule': 60.0,
        },
        # Phase 12 — Anomaly detection + auto-tune every 5 minutes
        'run-anomaly-detection-every-5min': {
            'task': 'run_anomaly_detection',
            'schedule': 300.0,
        },
        # Phase 12 — Dedicated auto-tune cycle every 10 minutes
        'auto-tune-cycle-every-10min': {
            'task': 'auto_tune_cycle',
            'schedule': 600.0,
        },
        # Phase 12 — Schedule optimization suggestions hourly
        'generate-schedule-suggestions-hourly': {
            'task': 'generate_schedule_suggestions',
            'schedule': crontab(minute=0),
        },
        # Phase 12 — Archive tag suggestions + duplicate detection daily at 2:30 AM
        'archive-intelligence-scan-daily': {
            'task': 'archive_intelligence_scan',
            'schedule': crontab(hour=2, minute=30),
        },
        # Phase 12 — Schedule drift detection every 5 minutes
        'detect-schedule-drift-every-5min': {
            'task': 'detect_schedule_drift',
            'schedule': 300.0,
        },
        # Fix 2 — bgutil-pot proactive health probe every 15 minutes
        'probe-bgutil-health-every-15min': {
            'task': 'probe_bgutil_health',
            'schedule': 900.0,
        },
        # Fix 6 — YouTube extraction health (android_vr + bgutil + Cobalt) every 10 min
        'youtube-extraction-health-every-10min': {
            'task': 'youtube_extraction_health_probe',
            'schedule': 600.0,
        },
        # Spotify Artist health watchdog — expand rate / SoundCloud resolve / disk
        'check-artist-health-every-5min': {
            'task': 'check_artist_health',
            'schedule': 300.0,
        },
        # YouTube proxy watchdog (Phase 8) — cost ceiling / success rate / circuit
        'check-youtube-health-every-5min': {
            'task': 'check_youtube_health',
            'schedule': 300.0,
        },
        # Phase 9 — Reset all users' downloads_today at 00:05 UTC each day
        'reset-daily-quota-at-midnight': {
            'task': 'reset_daily_quota',
            'schedule': crontab(hour=0, minute=5),
        },
        # Phase 9 — Downgrade expired canceling/past_due subscriptions at 00:30 UTC
        'expire-subscriptions-daily': {
            'task': 'expire_subscriptions',
            'schedule': crontab(hour=0, minute=30),
        },
        # Phase 11 — Worker heartbeat alert if no workers for >5 min
        'worker-heartbeat-every-2min': {
            'task': 'worker_heartbeat_check',
            'schedule': 120.0,
        },
        # Phase 14 — Flush Redis event buffer to Supabase every 60s
        'flush-event-buffer-every-60s': {
            'task': 'flush_event_buffer',
            'schedule': 60.0,
        },
        # Phase 14 — Health checks (fail rate + disk + queue) every 5 min
        'run-health-checks-every-5min': {
            'task': 'run_health_checks_task',
            'schedule': 300.0,
        },
        # Phase 14 — Aggregate analytics_events daily at 23:30 UTC
        'flush-analytics-daily-2330': {
            'task': 'flush_analytics_daily',
            'schedule': crontab(hour=23, minute=30),
        },
        # Phase 14 — Enhanced daily digest at 23:55 UTC
        'phase14-daily-digest-2355': {
            'task': 'send_phase14_daily_digest',
            'schedule': crontab(hour=23, minute=55),
        },
        # Phase 18 — Expire stale analysis jobs daily at 3:30 AM UTC
        'expire-analysis-jobs-daily': {
            'task': 'expire_analysis_jobs_task',
            'schedule': crontab(hour=3, minute=30),
        },
        # Transcript translation — expire jobs past their 7-day retention hourly
        'expire-transcript-translation-jobs-hourly': {
            'task': 'expire_transcript_translation_jobs_task',
            'schedule': crontab(minute=45),
        },
        # Transcript ASR — same hourly expiry cadence.
        'expire-transcript-asr-jobs-hourly': {
            'task': 'expire_transcript_asr_jobs_task',
            'schedule': crontab(minute=50),
        },
        # Phase 14 — Add bulk queue + bulk queue
        'flush-bulk-queue-buffer-every-2min': {
            'task': 'flush_event_buffer',
            'schedule': 120.0,
        },
        # Phase 4 Admin — Aggregate platform health metrics every hour
        'aggregate-platform-health-hourly': {
            'task': 'aggregate_platform_health',
            'schedule': crontab(minute=0),
        },
        # Supabase free-tier auto-pauses a project after 7 days of zero API
        # activity — this already silently wiped the production DB once.
        # A trivial read once a day keeps it comfortably active.
        'supabase-keepalive-daily': {
            'task': 'supabase_keepalive_ping',
            'schedule': 86400.0,
        },
    }
)
