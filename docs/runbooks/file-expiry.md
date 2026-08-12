# Runbook: File Expiry / Download Links Expire Too Quickly

> Scenario: Users report that download links are already expired when they try to access them. Files are being cleaned up before users have enough time to download. Alternatively, disk usage is at critical/emergency level and files need to be freed urgently.

---

## Symptoms

- Users report "link expired" or 404 errors on download URLs shortly after a job completes.
- Admin logs show `periodic_cleanup_downloads` running more aggressively than expected.
- Disk guardrail is firing Telegram alerts: `disk_critical` or `disk_emergency` level.
- Download volume is high, leading to rapid disk fill-up.
- Environment variables `FILE_EXPIRY_SINGLE_MIN`, `FILE_EXPIRY_BULK_MIN`, or `FILE_EXPIRY_ARTIST_MIN` are set to very low values.

---

## Quick Check

```bash
# 1. Check current disk usage and threshold level
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.disk_guardrail import get_disk_state
s = get_disk_state()
print(f\"Level: {s.threshold_level} | Used: {s.used_pct:.1f}% | Free: {s.free_gb:.1f} GB / {s.total_gb:.0f} GB total\")
"'

# 2. Check the effective file expiry values in the backend container
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
import os
print(\"SINGLE:\", os.getenv(\"FILE_EXPIRY_SINGLE_MIN\", \"20\"), \"min\")
print(\"BULK:\", os.getenv(\"FILE_EXPIRY_BULK_MIN\", \"30\"), \"min\")
print(\"ARTIST:\", os.getenv(\"FILE_EXPIRY_ARTIST_MIN\", \"45\"), \"min\")
print(\"PRO BONUS:\", os.getenv(\"FILE_EXPIRY_PRO_BONUS_MIN\", \"30\"), \"min\")
"'

# 3. Check how many files are in the downloads directory and total size
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  sh -c "find /app/downloads -type f | wc -l && du -sh /app/downloads"'
```

---

## Likely Causes

1. **`FILE_EXPIRY_SINGLE_MIN` set too low** — Default is 20 minutes for free-tier single downloads. If this was reduced (e.g., to 5 minutes) to free disk space, users with slow connections cannot finish downloading in time.

2. **Disk at `critical` or `emergency` level triggering aggressive cleanup** — `emergency_cleanup()` in `disk_guardrail.py` deletes the oldest files until usage drops below 82%. Files untouched for >60 seconds are candidates. A long download on a slow connection may get its file deleted mid-stream.

3. **`periodic_cleanup_downloads` running with a tight TTL** — This Celery beat task runs every 5 minutes and removes files whose `file_expires_at` timestamp has passed. If the expiry was set too soon, files are deleted.

4. **`DOWNLOADS_MAX_GB` env set too low** — If `DOWNLOADS_MAX_GB=10` (the default) but the VPS volume is larger, downloads are artificially throttled and files are cleaned up more frequently than necessary.

5. **Race condition between completion and cleanup** — If cleanup fires between the time a file is written and when `file_expires_at` is set in the DB, the file may be deleted before it's ever accessible.

---

## Recovery Steps

### Step 1 — Check and raise file expiry windows if set too low

```bash
# Check current values
ssh vidgrab 'grep -E "FILE_EXPIRY|DOWNLOADS_MAX" /home/ubuntu/vidgrab/.env'

# Edit .env to raise expiry windows
ssh vidgrab 'nano /home/ubuntu/vidgrab/.env'
# Recommended minimum values:
# FILE_EXPIRY_SINGLE_MIN=20
# FILE_EXPIRY_BULK_MIN=30
# FILE_EXPIRY_ARTIST_MIN=45
# FILE_EXPIRY_PRO_BONUS_MIN=30
# DOWNLOADS_MAX_GB=20  (if VPS disk supports it)

# Redeploy with new env (no rebuild needed)
bash ~/workspace/projects/Dowload-video/deploy-vps.sh --no-build
```

### Step 2 — Free disk space if at critical/emergency level

```bash
# Manually trigger emergency cleanup (deletes oldest files, spares last-60s active downloads)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.disk_guardrail import emergency_cleanup
deleted = emergency_cleanup(target_free_pct=82.0)
print(f\"Deleted {deleted} files\")
"'

# Recheck disk state after cleanup
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.disk_guardrail import get_disk_state
s = get_disk_state()
print(s.threshold_level, s.used_pct, \"% used,\", round(s.free_gb, 1), \"GB free\")
"'
```

### Step 3 — Find and remove large orphaned files (not referenced in DB)

```bash
# List the 20 largest files in downloads
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  sh -c "find /app/downloads -type f -exec du -sh {} \; | sort -rh | head -20"'

# Find files older than 2 hours
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  sh -c "find /app/downloads -type f -mmin +120 | wc -l"'

# Delete files older than 2 hours (safe if expiry windows are ≤45 min)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  sh -c "find /app/downloads -type f -mmin +120 -delete && echo done"'
```

### Step 4 — Expand disk volume if needed (Oracle Cloud)

If disk is consistently at >70% usage, the Oracle Cloud block volume needs to be expanded. This is an infrastructure change:

1. Expand the block volume in Oracle Cloud Console (no downtime for online resize).
2. On the VPS: `sudo growpart /dev/sda 1 && sudo resize2fs /dev/sda1`
3. Verify: `df -h /`

### Step 5 — Monitor disk alert cadence

Disk alerts are rate-limited to once per hour per level (`_ALERT_RATE_LIMIT_TTL = 3600`). To check if an alert was suppressed:

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli keys "disk_alert:*"'
```

---

## Rollback / Mitigation

- Disk guardrail thresholds are configurable via env: `DISK_WARN_PCT=70`, `DISK_HIGH_PCT=80`, `DISK_CRITICAL_PCT=90`, `DISK_EMERGENCY_PCT=95`. Lowering these triggers alerts earlier, giving more reaction time.
- If emergency cleanup deleted a file a user is actively downloading, there is no recovery — the user must re-request the download.
- Set up an external disk monitoring alert (e.g., via the `run_health_checks_task` Celery task which fires every 5 min and includes disk state in the health payload).

---

## When to Escalate

- Disk is at emergency level and `emergency_cleanup()` is not freeing enough space — indicates files >1GB are not being deleted (active downloads or stuck temp files).
- Disk has filled up completely (`used_pct = 100%`) — Docker itself may fail to write logs. Requires SSH emergency cleanup: `ssh vidgrab 'find /home/ubuntu/vidgrab/downloads -type f -mmin +5 -delete'`
- Oracle Cloud block volume cannot be expanded (billing/quota issue) — contact Oracle Cloud support.
