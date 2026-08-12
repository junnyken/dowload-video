# Runbook: Retry Storm

> Scenario: The same jobs are retrying repeatedly. Queue depth is growing. Workers are busy but no jobs complete. A permanent or unclassified error is being treated as retryable.

---

## Symptoms

- High retry activity visible in Celery worker logs: `Retry #2`, `Retry #3` appearing rapidly.
- Queue depth for `downloads` or `celery` queue grows instead of shrinking.
- Admin shows many jobs with `status = failed` and `auto_retry_status = auto_retry_scheduled`.
- Redis `vidgrab:pending_tasks` set has a large and growing membership.
- Error messages in logs are consistent across many jobs (same root cause).
- Worker CPU is high but downloads are not completing.

---

## Quick Check

```bash
# 1. Check queue depths (rising depth = storm in progress)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli llen downloads && redis-cli llen celery'

# 2. Check how many tasks are in the pending set
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli scard "vidgrab:pending_tasks"'

# 3. Check recent worker logs for repeated errors
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery --tail=80 | grep -E "Retry|ERROR|failed"'
```

---

## Likely Causes

1. **Permanent error misclassified as RETRYABLE** — The `failure_classifier` pattern-matches error messages. If a new error string does not match any `_PERMANENT_SIGNALS` pattern (e.g., a new "video unavailable" phrasing in a different language), it defaults to `RETRYABLE` (max 3 retries). This is intentional fail-safe, but causes wasted retries.

2. **RETRYABLE error that never resolves** — A 403/rate-limit error that the platform will not clear within the retry window. With exponential backoff capped at 30 minutes, 3 RETRYABLE attempts run over ~90 minutes total — this is by design but creates visible queue pressure.

3. **Task exception not caught properly** — An unhandled exception in `process_video_task` propagates as a Celery RETRY without setting `auto_retry_status = retry_limit_reached`. The task retries until it exhausts Celery's internal `max_retries`.

4. **`task_acks_late + task_reject_on_worker_lost` causing redelivery loop** — If a task is near its soft time limit (`660s`) and the worker is restarted, the task is redelivered. If the same URL always times out, this creates an infinite redelivery cycle.

5. **Celery beat flooding the queue** — A periodic task (e.g., `flush_event_buffer`, `run_anomaly_detection`) is failing and being requeued every cycle.

---

## Recovery Steps

### Step 1 — Identify the dominant error message

```bash
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery --since=10m | \
  grep -oP "(?<=last_error\": \")[^\"]*" | sort | uniq -c | sort -rn | head -10'
```

### Step 2 — Check failure classification for the error

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.failure_classifier import classify_failure
msg = \"YOUR ERROR MESSAGE HERE\"
print(classify_failure(msg))
"'
```

If the result is `RETRYABLE` but should be `PERMANENT`, a code fix is needed. As an immediate mitigation, purge the offending jobs (Step 4).

### Step 3 — Pause new task acceptance to stop the storm from growing

```bash
# Revoke all queued tasks for the downloads queue (drains the storm without deleting)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-1) \
  celery -A app.core.celery_app.celery_app control cancel_consumer downloads'
```

This stops the worker from picking up more tasks. Re-enable after fixing:
```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-1) \
  celery -A app.core.celery_app.celery_app control add_consumer downloads -d celery@HOSTNAME'
```

### Step 4 — Purge the storm queue

```bash
# Purge the downloads queue (all pending tasks discarded)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-1) \
  celery -A app.core.celery_app.celery_app purge -Q downloads -f'

# If the default celery queue is also flooded
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-light-1) \
  celery -A app.core.celery_app.celery_app purge -Q celery -f'
```

Warning: this discards all pending jobs in those queues. Mark affected jobs as failed via Supabase before purging.

### Step 5 — Mark retrying jobs as failed in Supabase

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.database import get_supabase_client
sb = get_supabase_client()
r = sb.table(\"download_jobs\") \
  .update({\"status\": \"failed\", \"auto_retry_status\": \"retry_suppressed\", \"last_error\": \"manual: retry storm abort\"}) \
  .eq(\"status\", \"processing\") \
  .eq(\"auto_retry_status\", \"auto_retry_scheduled\") \
  .execute()
print(\"Stopped retries for\", len(r.data), \"jobs\")
"'
```

### Step 6 — Restart workers after the storm is cleared

```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery celery-light'
```

---

## Rollback / Mitigation

- If a specific platform is causing the storm, open its circuit breaker manually to fail-fast instead of retrying: `redis-cli set pcircuit:youtube:state open && redis-cli set oracle_circuit:open_since $(date +%s)`
- Check `vidgrab:recovery_log` in Redis to see what the stale scanner has logged: `redis-cli lrange "vidgrab:recovery_log" 0 -1`
- Monitor queue depth after restart: `watch -n5 'docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli llen downloads'`

---

## When to Escalate

- Purging the queue does not stop the storm — tasks are being re-submitted immediately (check if a user or external integration is hammering the submit endpoint).
- The storm is caused by the `task_reject_on_worker_lost` redelivery on a task that always timeouts — indicates a video that can never be downloaded; add the URL to a blocklist or extend `task_time_limit`.
- Storm persists after two worker restarts — likely a corrupted Redis queue state; consider flushing Redis and restarting all services.
