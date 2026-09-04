#!/bin/sh
set -e

# ── Drop root ────────────────────────────────────────────────────────────────
# Celery announced this on every boot: "SecurityWarning: You're running the
# worker with superuser privileges". This container feeds attacker-supplied
# URLs to yt-dlp and their output to ffmpeg — the biggest RCE surface here — so
# the processes doing that should not own the filesystem they run on.
#
# Ownership is fixed HERE rather than only in the Dockerfile because the
# platform mounts a volume over /app/downloads, and a fresh mount arrives owned
# by root regardless of what the image did at build time. Doing it at start is
# the only ordering that works both with and without a mount.
#
# Everything degrades rather than fails: no root (already dropped by the
# platform), no setpriv, or a chown that cannot touch a read-only mount all
# fall through to running as whoever we already are. A container that will not
# start is a worse outcome than one running with more privilege than it needs.
APP_USER=appuser
RUN_AS=""
if [ "$(id -u)" = "0" ] && id "$APP_USER" >/dev/null 2>&1; then
    chown -R "$APP_USER":"$APP_USER" /app/downloads /app/ytdlp_cache /opt/bgutil-cache 2>/dev/null || \
        echo "[entrypoint] WARNING: could not chown working dirs — continuing"
    if command -v setpriv >/dev/null 2>&1; then
        RUN_AS="setpriv --reuid=$APP_USER --regid=$APP_USER --init-groups --inh-caps=-all"
    elif command -v su >/dev/null 2>&1; then
        RUN_AS="su -s /bin/sh $APP_USER -c"
    else
        echo "[entrypoint] WARNING: no setpriv or su — staying root"
    fi
fi

if [ -n "$RUN_AS" ]; then
    echo "[entrypoint] dropping privileges to $APP_USER"
else
    echo "[entrypoint] running as uid $(id -u) (no privilege drop)"
fi

# su takes one shell string, setpriv takes argv. Normalise so the commands
# below read the same either way.
run_bg() {
    case "$RUN_AS" in
        "")            sh -c "$1" & ;;
        *"-c")         $RUN_AS "$1" & ;;
        *)             $RUN_AS sh -c "$1" & ;;
    esac
}
run_fg() {
    case "$RUN_AS" in
        "")            exec sh -c "$1" ;;
        *"-c")         exec $RUN_AS "$1" ;;
        *)             exec $RUN_AS sh -c "$1" ;;
    esac
}

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
run_bg "celery -A app.core.celery_app worker \
    -Q downloads,bulk,light,media,analysis,celery \
    --concurrency=2 \
    --max-tasks-per-child=40 \
    --loglevel=info"

run_bg "celery -A app.core.celery_app beat --loglevel=info"

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
run_fg "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips '*'"
