# Runbook: Jobs Stuck in `processing` Status

> Scenario: One or more jobs remain in `processing` for >10 minutes. Workers appear running but jobs never reach `completed` or `failed`.

---

## Symptoms

- Users report downloads that spin forever with no result.
- Admin dashboard shows jobs with `status = processing` for more than 10 minutes.
- Worker containers (`vidgrab-celery-1`) show as healthy, but no progress in logs.
- The stale job scanner (`scan_stale_jobs`, runs every 2 min) is not clearing the jobs.

---

## Quick Check

```bash
# 1. Check how many jobs are currently in processing status
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.database import get_supabase_client
sb = get_supabase_client()
r = sb.table(\"download_jobs\").select(\"id,created_at,url\").eq(\"status\",\"processing\").execute()
print(len(r.data), \"stuck jobs\")
for j in r.data[:5]: print(j[\"id\"][:8], j[\"created_at\"], j.get(\"url\",\"\")[:60])
"'

# 2. Check if workers are actually processing anything
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-1) \
  celery -A app.core.celery_app.celery_app inspect active'

# 3. Check heartbeat liveness for a specific job_id
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli keys "vidgrab:job_hb:*"'
```

---

## Likely Causes

1. **Worker OOM-killed mid-task** — heartbeat key expired, lease still held. The task was not acked back to the broker. With `task_acks_late=True` + `task_reject_on_worker_lost=True`, redelivery is automatic on worker restart, but only if the worker truly died. If the container restarted it may have lost in-flight task context.

2. **Lease not released** — `vidgrab:job_lease:{job_id}` key exists with TTL ~15 min. Another worker will not pick it up until the lease expires, even if the original worker is dead.

3. **Heartbeat thread killed without cleanup** — The `JobLease.stop()` was not called cleanly. The heartbeat key (`vidgrab:job_hb:{job_id}`) is missing (TTL 90s), but the DB row is still `processing`.

4. **Stale job scanner (`scan_stale_jobs`) is not running** — celery-beat container dead or beat schedule not publishing to the `celery` queue.

5. **Supabase connectivity issue** — task completed internally but the `status = completed` DB write failed silently.

---

## Recovery Steps

### Step 1 — Verify worker liveness

```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose ps'
# If any celery container is not "Up", restart it:
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery'
```

### Step 2 — Confirm beat is running and publishing stale-scan tasks

```bash
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery-beat --tail=30'
# You should see: "scan_stale_jobs" appearing every ~2 minutes
```

### Step 3 — Check and clear stale leases for specific jobs

```bash
# List all active leases
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli keys "vidgrab:job_lease:*"'

# Force-delete lease for a specific job to allow requeue (replace JOB_ID)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli del "vidgrab:job_lease:JOB_ID"'
```

### Step 4 — Mark stuck jobs as failed via admin API

```bash
# Reset a stuck job to failed so the user can retry
curl -X POST https://dowloadvideo.io.vn/api/v1/admin/reset-job \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "JOB_ID", "new_status": "failed", "reason": "stuck_processing_manual_reset"}'
```

### Step 5 — Force-restart all workers if multiple jobs are stuck

```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery celery-light celery-beat'
# With task_acks_late + task_reject_on_worker_lost, incomplete tasks are re-queued automatically.
```

### Step 6 — Bulk-reset all jobs stuck >15 min

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.database import get_supabase_client
from datetime import datetime, timezone, timedelta
sb = get_supabase_client()
cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
r = sb.table(\"download_jobs\") \
  .update({\"status\": \"failed\", \"last_error\": \"manual_reset: stuck >15min\"}) \
  .eq(\"status\", \"processing\") \
  .lt(\"created_at\", cutoff) \
  .execute()
print(\"Reset\", len(r.data), \"jobs\")
"'
```

---

## Rollback / Mitigation

- If worker restart does not help, perform a full redeploy: `bash ~/workspace/projects/Dowload-video/deploy-vps.sh --no-build`
- Check the recovery log in Redis for scanner history: `redis-cli lrange "vidgrab:recovery_log" 0 20`
- If the stale scanner is consistently missing jobs, check `CELERY_LIGHT_CONCURRENCY` — if the `celery` queue is saturated, scanner tasks may be delayed.

---

## When to Escalate

- More than 20 jobs stuck across multiple platforms simultaneously — indicates systemic worker failure.
- Heartbeat keys are present (workers alive) but DB writes are consistently failing — Supabase outage.
- Leases are expiring and re-acquired but jobs still do not complete — likely a poison-message loop (see `retry-storm.md`).
