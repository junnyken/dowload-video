# Runbook: Stale Queue Buildup

> Scenario: Many jobs are in `stale` status. The recovery scanner (`scan_stale_jobs`, runs every 2 min via Celery beat) is not resolving them or is being overwhelmed.

---

## Symptoms

- Admin dashboard shows a growing count of jobs with `status = stale`.
- The `vidgrab:recovery_log` Redis list is not being updated (scanner stopped running).
- `celery-beat` logs do not show `scan_stale_jobs` firing every ~2 minutes.
- Jobs were in `processing` and transitioned to `stale` but never recovered or failed.
- Users are waiting on downloads that appear to be "in progress" but never finish.

---

## Quick Check

```bash
# 1. Check how many stale jobs exist
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.database import get_supabase_client
sb = get_supabase_client()
r = sb.table(\"download_jobs\").select(\"id\", count=\"exact\").eq(\"status\", \"stale\").execute()
print(r.count, \"stale jobs\")
"'

# 2. Confirm stale job scanner is firing (look for scan_stale_jobs in beat logs)
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery-beat --tail=50'

# 3. Check the recovery log (scanner writes here on each scan)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli lrange "vidgrab:recovery_log" 0 9'
```

---

## Likely Causes

1. **`celery-beat` container dead** — If the beat scheduler is not running, no periodic tasks fire at all. `scan_stale_jobs`, `cleanup-downloads-every-5-minutes`, and all other scheduled tasks will not run.

2. **`celery` default queue saturated** — `scan_stale_jobs` routes to the default `celery` queue (handled by `celery-light`). If the light worker is busy or dead, scanner tasks queue up but do not execute.

3. **Stale scanner overwhelmed** — If there are hundreds of stale jobs, each scan cycle takes longer than the 2-minute interval. Scanner tasks pile up in the queue faster than they are consumed, causing a feedback loop.

4. **Persistent heartbeat key anomaly** — Workers are writing heartbeats (`vidgrab:job_hb:{job_id}`) but the DB status is not being updated to `processing`. Scanner sees "alive" heartbeat and skips the job, but the DB remains in an intermediate state.

5. **`task_time_limit=720s` being hit** — Tasks that hit the 12-minute hard kill are marked by Celery as failed with a `SoftTimeLimitExceeded` or `TimeLimitExceeded` exception. If the exception handler fails to update the DB (e.g., Supabase timeout), the job row stays in whatever state it was last written.

---

## Recovery Steps

### Step 1 — Restart celery-beat if it is dead

```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose ps celery-beat'
# If not "Up":
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery-beat'

# Verify the PID file exists (healthcheck uses this)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-beat-1) ls -la /tmp/celerybeat.pid'
```

### Step 2 — Trigger the stale job scanner manually

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-light-1) \
  celery -A app.core.celery_app.celery_app call scan_stale_jobs'
```

Wait 30 seconds then recheck stale job count.

### Step 3 — Check if scanner is backlogged in the queue

```bash
# How many pending tasks are in the default celery queue?
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli llen celery'

# If >20, the scanner task itself may be queued but not executing
# Restart celery-light to drain
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery-light'
```

### Step 4 — Bulk-resolve stale jobs that have no active heartbeat

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.database import get_supabase_client
from app.core.job_lease import is_alive
sb = get_supabase_client()
jobs = sb.table(\"download_jobs\").select(\"id\").eq(\"status\", \"stale\").execute().data
requeued = 0
failed = 0
for j in jobs:
    jid = j[\"id\"]
    if not is_alive(jid):
        sb.table(\"download_jobs\").update({\"status\": \"failed\", \"last_error\": \"stale_no_heartbeat_manual_reset\"}).eq(\"id\", jid).execute()
        failed += 1
    else:
        requeued += 1  # still alive, leave for scanner
print(f\"Marked failed: {failed}, still alive: {requeued}\")
"'
```

### Step 5 — Clear orphaned heartbeat and lease keys for stale jobs

```bash
# If jobs are marked stale but heartbeat keys persist (causing scanner to skip them)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli --scan --pattern "vidgrab:job_hb:*" | \
  xargs -I{} docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli del {}'

ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli --scan --pattern "vidgrab:job_lease:*" | \
  xargs -I{} docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli del {}'
```

Warning: only do this after verifying no jobs are genuinely `processing` (Step 4 in `stuck-jobs.md`).

### Step 6 — Full worker + beat restart

```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery celery-light celery-beat'
```

---

## Rollback / Mitigation

- If stale buildup is systematic (recurring daily), lower the scanner interval in `celery_app.py` from 120s to 60s and redeploy.
- Monitor recovery log after restart: `redis-cli lrange "vidgrab:recovery_log" 0 -1 | tail -20`
- If beat keeps dying, check VPS memory: `ssh vidgrab 'free -h'`. OOM-kill of beat is silent — check `dmesg` for OOM events: `ssh vidgrab 'dmesg | grep -i oom | tail -10'`

---

## When to Escalate

- Stale jobs exceed 100 and the scanner is clearing them slower than they arrive — a systemic failure mode, not a transient burst.
- Scanner fires but stale count does not decrease — indicates a DB connectivity issue in the scanner task itself.
- `celery-beat` restarts loop with crash within <1 minute — check `celerybeat-schedule` file corruption: `ssh vidgrab 'ls -la /home/ubuntu/vidgrab/celerybeat-schedule*'`
