# Runbook: Bulk Queue Not Progressing

> Scenario: A bulk download batch was accepted and returned a batch ID, but individual items remain in `pending` or `queued` status for >5 minutes. Queue depth for the `bulk` queue keeps rising.

---

## Symptoms

- Bulk download page shows items stuck at 0% with `pending` or `queued` status.
- Admin stats show `bulk` queue depth > 0 and not decreasing.
- Single-URL downloads may still work, but batch/channel scrapes do not complete.
- No progress on `scrape_channel_task` or `discover_container_task` Celery tasks.

---

## Quick Check

```bash
# 1. Check queue depths for all relevant queues
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli llen bulk && \
  redis-cli llen downloads && \
  redis-cli llen celery'

# 2. Check if celery-light worker (consumes "celery" default queue) is alive
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose ps celery-light'

# 3. Check what the celery-light worker is doing
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-light-1) \
  celery -A app.core.celery_app.celery_app inspect active'
```

---

## Likely Causes

1. **`celery-light` worker dead or unhealthy** — Bulk queue tasks (`scrape_channel_task`, `create_zip_task`, `discover_container_task`) route to the `bulk` queue and are consumed by the light worker. If this container is down, no bulk work runs.

2. **Disk guardrail blocking new jobs** — Disk at `high` (>80%) or `critical` (>90%) level causes `check_can_accept_job("bulk")` to return False. New bulk submissions are rejected at the middleware layer before even reaching the queue.

3. **`scrape_channel_task` hanging** — Channel scraping may be blocked on a platform (rate limit, bot-wall). The task has a 12-minute hard kill (`task_time_limit=720`), so it will eventually fail, but this delays all items in the batch.

4. **Supabase connection pool exhaustion** — If the backend is saturated with DB calls, batch item status writes fail and items remain in their initial state.

5. **Redis `bulk` queue key missing** — The queue has been flushed or Redis restarted without persistence replay.

---

## Recovery Steps

### Step 1 — Check disk pressure first

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.disk_guardrail import get_disk_state
s = get_disk_state()
print(s.threshold_level, s.used_pct, \"% used,\", round(s.free_gb, 1), \"GB free\")
"'
```

If the level is `high` or above, follow `file-expiry.md` to free disk space before continuing.

### Step 2 — Restart celery-light worker

```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery-light'
# Wait 10s then re-check queue depth
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli llen bulk'
```

### Step 3 — Check for stuck channel-scrape tasks and kill them

```bash
# List active tasks on all workers
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-light-1) \
  celery -A app.core.celery_app.celery_app inspect active'

# If scrape_channel_task is hanging, revoke it (replace TASK_ID)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-light-1) \
  celery -A app.core.celery_app.celery_app control revoke TASK_ID --terminate'
```

### Step 4 — Check Redis persistence and queue integrity

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli info persistence'
# "aof_enabled:1" should be present (appendonly yes is set in compose)

# Confirm broker queue list type (should be list, not other type)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli type bulk'
```

### Step 5 — Re-trigger failed batch items via admin API

```bash
# Fetch batch items that are stuck in queued/pending for >10 min
# Then use the admin retry endpoint for the batch
curl -X POST https://dowloadvideo.io.vn/api/v1/admin/retry-batch \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"batch_id": "BATCH_ID"}'
```

### Step 6 — Full worker restart if steps 1-5 do not resolve

```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery celery-light celery-beat'
```

---

## Rollback / Mitigation

- If the bulk queue is consistently saturated, consider reducing `CELERY_LIGHT_CONCURRENCY` to prevent memory pressure while still consuming the queue.
- Emergency redeploy (no rebuild, config-only): `bash ~/workspace/projects/Dowload-video/deploy-vps.sh --no-build`
- To drain and discard a poisoned bulk queue entirely (nuclear option):
  ```bash
  ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli del bulk'
  ```
  All pending items will need to be re-submitted by users.

---

## When to Escalate

- Queue depth exceeds 500 items and is still growing after worker restart.
- Disk guardrail is at `critical` or `emergency` and emergency cleanup is not freeing space.
- `scrape_channel_task` is repeatedly timing out for all platforms — indicates external connectivity issue from the VPS.
