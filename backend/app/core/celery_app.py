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
    include=["app.tasks.video_tasks"]
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
    worker_concurrency=4,  # Prevent CPU overload from FFmpeg
    broker_transport_options={
        'priority_steps': list(range(10)),
        'sep': ':',
        'queue_order_strategy': 'priority',
    },
    task_routes={
        'create_zip_task': {'queue': 'celery'},
        'process_video_task': {'queue': 'celery'},
        'scrape_channel_task': {'queue': 'celery'},
        '*': {'queue': 'celery'},
    },
    beat_schedule={
        # Cleanup temp downloads every 30 minutes (+ enforce disk quota)
        'cleanup-downloads-every-30-minutes': {
            'task': 'periodic_cleanup_downloads',
            'schedule': 1800.0,
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
    }
)
