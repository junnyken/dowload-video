#!/bin/sh
set -e

# Single-container deploy (no separate celery/celery-beat services available):
# run one Celery worker + celery beat (periodic maintenance tasks: job expiry,
# analytics flush, subscription expiry, etc.) in the background, then run the
# API server in the foreground so the container's main process is uvicorn.
# --max-tasks-per-child: recycle a worker process after N tasks. yt-dlp and the
# ffmpeg subprocesses it spawns leave memory behind, so without recycling RSS
# only ever grows and the container eventually OOMs. docker-compose.yml sets
# this per worker type (20-100); it was lost when the four workers were folded
# into this single one. 40 is the middle of that range, chosen because this one
# worker serves every queue — media (compose used 20) through light (100).
celery -A app.core.celery_app worker \
    -Q downloads,bulk,light,media,analysis,celery \
    --concurrency=2 \
    --max-tasks-per-child=40 \
    --loglevel=info &

celery -A app.core.celery_app beat \
    --loglevel=info &

# Retuned from the emergency --workers 1 diagnostic (2026-08-12). The comment
# here used to claim 2 CPU/2GB; the platform actually reported 1 CPU/2GB, so
# the tuning was based on twice the CPU that existed. Raised to 1.6 CPU/4GB on
# 2026-08-31 (the plan already included that headroom, unused).
#
# Measured before the raise: one TikTok fetch took 1.6s on its own, but six in
# parallel took 1.5-7.5s — and TikTok is the cheapest path, where the server
# streams no bytes at all. CPU was the bottleneck, not bandwidth.
#
# 2 uvicorn workers + concurrency=2 celery balances throughput against this
# single container also loading yt-dlp/ffmpeg per process. Re-tune again if RAM
# pressure or crash-looping (repeated "Child process died" in logs) returns.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips '*'
