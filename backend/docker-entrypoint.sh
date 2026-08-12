#!/bin/sh
set -e

# Single-container deploy (no separate celery/celery-beat services available):
# run one Celery worker + celery beat (periodic maintenance tasks: job expiry,
# analytics flush, subscription expiry, etc.) in the background, then run the
# API server in the foreground so the container's main process is uvicorn.
celery -A app.core.celery_app worker \
    -Q downloads,bulk,light,media,analysis,celery \
    --concurrency=2 \
    --loglevel=info &

celery -A app.core.celery_app beat \
    --loglevel=info &

# Retuned from the emergency --workers 1 diagnostic (2026-08-12) now that the
# container has 2 CPU/2GB RAM and has run stable for a while. 2 uvicorn
# workers + concurrency=2 celery balances throughput against this single
# container also loading yt-dlp/ffmpeg per process. Re-tune again if RAM
# pressure or crash-looping (repeated "Child process died" in logs) returns.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips '*'
