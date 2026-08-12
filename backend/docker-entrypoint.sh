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

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips '*'
