# VidGrab Production Runbook — Phase 17

> Last updated: 2026-06-29
> Stack: FastAPI + Celery + Redis + Supabase + Docker Compose
> VPS: Oracle VPS (dowloadvideo.io.vn) — SSH alias `vidgrab`
> Deploy dir: `/home/ubuntu/vidgrab`

---

## Incident Response — Quick Reference

| | |
|---|---|
| **Contact** | (placeholder — fill in on-call rotation) |
| **Alert channels** | Telegram: ops chat (bot alerts fire automatically); Email: (placeholder) |
| **RPO target** | < 1 hour (Supabase DB auto-backup), < 24h (Redis), < 0 (stateless app layer) |
| **RTO target** | < 15 minutes for app restart; < 1 hour for full recovery from backup |

**Severity levels:**
- **P0** — Site fully down, all downloads failing, disk full
- **P1** — Partial failure (one platform down, workers dead, slow degradation)
- **P2** — Single-user issue, non-critical feature broken

---

## Scenario 1: Backend Container Not Responding

**Symptoms:** `/health` returns 5xx or times out; users see "service unavailable".

```bash
# 1. Check all container states
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml ps'

# 2. Check recent error logs
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs backend --tail=50'

# 3. If OOM suspected — check memory usage
ssh vidgrab 'docker stats --no-stream'

# 4. Restart backend only (fast, no rebuild)
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart backend'

# 5. Verify recovery
ssh vidgrab 'curl -s localhost:8000/health'

# 6. If restart insufficient — rebuild backend image
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose up --build -d backend'
```

**If OOM recurs:** check `MEM_LIMIT` in docker-compose.yml; consider raising backend memory reservation or adding a swap file.

---

## Scenario 2: All Workers Dead (Queue Backing Up)

**Symptoms:** Downloads accepted but never complete; queue depth growing; `/health` shows 0 active workers.

```bash
# 1. Check queue depths (run for each queue: downloads, bulk, media, celery)
ssh vidgrab 'docker exec $(docker ps -qf name=redis) redis-cli llen downloads'
ssh vidgrab 'docker exec $(docker ps -qf name=redis) redis-cli llen bulk'
ssh vidgrab 'docker exec $(docker ps -qf name=redis) redis-cli llen media'

# 2. Check worker logs for crash reason
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery --tail=30'
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery-light --tail=30'
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery-media --tail=30'

# 3. Restart all worker services
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery celery-light celery-media'

# 4. Verify worker count via health endpoint
ssh vidgrab 'curl -s localhost:8000/health | python3 -m json.tool'

# 5. If queue depth > 500 and tasks are stale (older than 1h): drain the queue
#    WARNING: DESTRUCTIVE — all pending tasks are lost, users must retry
#    Confirm with team lead before running
ssh vidgrab 'docker exec $(docker ps -qf name=redis) redis-cli del downloads'
ssh vidgrab 'docker exec $(docker ps -qf name=redis) redis-cli del bulk'
```

**Prevention:** Monitor queue depth via Telegram alerts. If workers crash in loop, check for a poison-pill task and filter it out via the admin endpoint before restarting.

---

## Scenario 3: Redis Unavailable / Corrupt

**Symptoms:** Celery workers disconnect; quota checks fail; cookie storage unavailable; error logs show `ConnectionRefusedError` or `LOADING Redis is loading the dataset in memory`.

```bash
# 1. Check Redis logs
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs redis --tail=50'

# 2. Try a soft restart first
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart redis'

# 3. Verify Redis is accepting commands
ssh vidgrab 'docker exec $(docker ps -qf name=redis) redis-cli ping'
# Expected: PONG

# 4. If volume is corrupt — restore from backup
./scripts/restore.sh redis /path/to/redis-backup.rdb
# OR on VPS directly:
# ssh vidgrab 'cd /home/ubuntu/vidgrab && ./scripts/restore.sh redis'

# 5. If no backup available — create fresh Redis (data loss accepted)
#    Queue data, rate-limit counters, and cached cookies will be lost
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose stop redis && \
  docker volume rm vidgrab_redis-data 2>/dev/null || true && \
  docker compose up -d redis'

# 6. After Redis restart — trigger cookie reload via admin
ssh vidgrab 'curl -s -X POST localhost:8000/admin/cookies/reload'

# 7. Monitor queue refill as users retry
ssh vidgrab 'watch -n5 "docker exec $(docker ps -qf name=redis) redis-cli llen downloads"'
```

**Note:** Redis is configured with `appendonly yes` (AOF) and `volatile-lru 512MB`. AOF provides point-in-time recovery if the volume is intact but Redis crashed uncleanly.

---

## Scenario 4: Disk Full (100%)

**Severity: P0 — Act immediately. A full disk halts ALL writes including logs and downloads.**

```bash
# 1. Identify which partition is full
ssh vidgrab 'df -h'

# 2. Check largest consumers
ssh vidgrab 'du -sh /home/ubuntu/vidgrab/* 2>/dev/null | sort -rh | head -20'
ssh vidgrab 'docker system df'

# 3. Trigger emergency cleanup via backend guardrail
ssh vidgrab 'docker exec backend python3 -c "
from app.core.disk_guardrail import emergency_cleanup
import asyncio
asyncio.run(emergency_cleanup(target_free_pct=75))
"'

# 4. If emergency cleanup insufficient — manually remove oldest downloads
ssh vidgrab 'find /home/ubuntu/vidgrab/downloads -type f -mtime +1 | sort | head -50 | xargs rm -f'

# 5. Prune unused Docker layers
ssh vidgrab 'docker image prune -f && docker builder prune -f'

# 6. Check for log bloat
ssh vidgrab 'du -sh /var/log/* 2>/dev/null | sort -rh | head -10'
ssh vidgrab 'journalctl --disk-usage'
# Truncate old journals if needed:
ssh vidgrab 'journalctl --vacuum-time=3d'

# 7. After freeing space — restart Celery workers
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart celery celery-light celery-media'

# 8. Verify disk is below 80%
ssh vidgrab 'df -h /home/ubuntu'
```

**Root cause checklist:**
- Runaway batch job (check Celery logs for stuck long-running tasks)
- Large 4K video downloads accumulating
- Docker build cache from repeated deploys (`docker builder prune`)
- Log rotation not configured (check `/etc/logrotate.d/`)

---

## Scenario 5: Deploy Failed — App Won't Boot

**Symptoms:** New deploy pushed, containers restart, backend keeps crashing (OOMKilled, exit 1, or startup loop).

```bash
# 1. Check startup error in detail
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose logs backend --tail=100'

# 2. Common causes:
#    - Missing env var: grep for "KeyError" or "missing required"
ssh vidgrab 'cat /home/ubuntu/vidgrab/.env | grep -E "KEY_NAME|SECRET|TOKEN" | head -20'

#    - Import error: look for "ModuleNotFoundError" or "SyntaxError"
#    - DB connection: test directly
ssh vidgrab 'docker compose exec backend python3 -c "
from app.core.database import get_service_client
client = get_service_client()
print(\"DB OK:\", client.table(\"users\").select(\"id\").limit(1).execute())
"'

# 3. ROLLBACK — fastest path (from local machine)
./scripts/rollback.sh

# 4. OR rollback manually on VPS using saved image state
ssh vidgrab 'cat /tmp/vidgrab_last_images.json'
# Find previous image IDs, then:
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose down && \
  # Edit docker-compose.yml to pin previous image tag, then:
  docker compose up -d --no-build'

# 5. Alternative: re-deploy previous git SHA from local
git log --oneline -5
# git checkout <previous-sha>
# ./deploy-vps.sh
```

---

## Scenario 6: Supabase Cloud Downtime

**Supabase SLA: 99.9% uptime. This is a rare, external dependency failure.**

**During downtime:**
- App returns 503 for all authenticated endpoints (rate checks, quota, user data)
- Anonymous download paths degrade gracefully — quota check bypassed in fallback mode
- Redis-cached tokens may still allow some operations temporarily

**Actions:**
1. Check Supabase status page: https://status.supabase.com
2. Check app logs to confirm the root cause is Supabase (not our DB code):
   ```bash
   ssh vidgrab 'docker compose logs backend --tail=50 | grep -i "supabase\|postgrest\|connection"'
   ```
3. **No action needed on our side** — wait for Supabase recovery; our code has retry logic.
4. Post-recovery: verify `daily_quota_reset` Celery beat task ran on schedule:
   ```bash
   ssh vidgrab 'docker compose logs celery --tail=100 | grep "daily_quota_reset"'
   ```
5. If quota reset was missed, trigger manually:
   ```bash
   ssh vidgrab 'docker exec backend python3 -c "
   from app.tasks.maintenance import daily_quota_reset
   daily_quota_reset.delay()
   "'
   ```

---

## Scenario 7: Proxy Provider Exhausted (YouTube Down)

**Symptoms:** YouTube downloads fail with 403/407; Telegram ops alert fires `PROXY_EXHAUSTED`; `/health` shows YouTube circuit breaker OPEN.

**Immediate actions:**
1. Confirm via Telegram alert (bot sends `[VIDGRAB] YouTube circuit breaker OPEN — proxy 407 TRAFFIC_EXHAUSTED`)
2. Top up DataImpulse credit:
   - Dashboard: (placeholder — add DataImpulse dashboard URL)
   - Top up $10-$50 depending on usage pattern
3. After top-up — reset circuit breaker without redeploy:
   ```bash
   ssh vidgrab 'curl -s -X POST localhost:8000/admin/circuit-breaker/reset/youtube'
   ```
4. Alternative — switch to backup proxy provider:
   ```bash
   # Edit .env on VPS
   ssh vidgrab 'nano /home/ubuntu/vidgrab/.env'
   # Change: IPROYAL_PROXY=http://user:pass@new-proxy-host:port
   # Then hot-reload backend + workers (no rebuild needed)
   ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose up -d --no-build backend celery celery-light celery-media'
   ```
5. Frontend platform status indicator will automatically show YouTube as unavailable while circuit is OPEN, and recover when circuit resets.

**Note:** Other platforms (TikTok via TikWM, SoundCloud) are unaffected by DataImpulse proxy exhaustion.

---

## Scenario 8: VPS Completely Dead / Oracle Instance Terminated

**Severity: P0 — Full disaster recovery. Estimated RTO: 45-60 minutes.**

```bash
# Step 1: Provision new Oracle VPS
# - Ubuntu 22.04 LTS, same OCI region for low latency
# - Shape: VM.Standard.E4.Flex (2 OCPU, 16GB RAM minimum)
# - Add your SSH public key during provisioning

# Step 2: Install Docker on new VPS
ssh new-vps 'curl -fsSL https://get.docker.com | sh && \
  sudo usermod -aG docker ubuntu && \
  sudo systemctl enable docker'

# Step 3: Configure SSH alias on local machine
# Add to ~/.ssh/config:
# Host vidgrab
#   HostName <NEW_VPS_PUBLIC_IP>
#   User ubuntu
#   IdentityFile ~/.ssh/your-key.pem

# Step 4: Clone repo to VPS
ssh vidgrab 'git clone <GITLAB_REPO_URL> /home/ubuntu/vidgrab'

# Step 5: Restore .env from secure store
# Retrieve from password manager / team vault, then:
scp .env.production vidgrab:/home/ubuntu/vidgrab/.env

# Step 6: Restore Redis from backup (if available)
./scripts/restore.sh redis /path/to/redis-backup.rdb

# Step 7: Start all services
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose up -d'

# Step 8: Supabase DB is cloud-hosted — NO RESTORE NEEDED
# All user data, quotas, and metadata are safe in Supabase

# Step 9: Update DNS to new VPS IP
# - Log into DNS provider (Cloudflare / domain registrar)
# - Update A record for dowloadvideo.io.vn → new IP
# - TTL propagation: 5-30 minutes (Cloudflare proxied = near instant)

# Step 10: Verify recovery
curl -s https://dowloadvideo.io.vn/health | python3 -m json.tool

# Expected full RTO: 45-60 minutes
```

**What is permanently safe (no restore needed):**
- All user accounts, API keys, quota records (Supabase cloud)
- Download history, analytics (Supabase cloud)

**What may be lost (acceptable data loss per RPO):**
- In-flight download tasks in Redis queues (users retry)
- Rate-limit counters (reset to zero — acceptable)
- Cached platform cookies in Redis (auto-reloaded from env secrets on startup)

---

## Backup Verification Procedure

Run monthly to confirm backups are valid and restorable:

```bash
# Verify Redis backup integrity without restoring
./scripts/restore.sh --verify-only

# Check backup file ages on VPS
ssh vidgrab 'ls -lh /home/ubuntu/vidgrab/backups/ 2>/dev/null || echo "No local backups dir"'

# Confirm Supabase auto-backup is enabled
# Log in to Supabase dashboard → Settings → Backups
# Verify: daily backups enabled, retention >= 7 days
```

**Backup schedule:**
| Data | Method | Frequency | Retention |
|---|---|---|---|
| Supabase DB | Supabase auto-backup | Daily | 7 days (free tier) / 30 days (Pro) |
| Redis dump | `scripts/backup.sh` (cron) | Daily 02:00 VN | 7 days |
| `.env` secrets | Team vault / password manager | On change | Indefinite |
| Code | GitLab repo | Every push | Indefinite |

---

## Contacts & Escalation

| Role | Name | Contact |
|---|---|---|
| On-call engineer | (placeholder) | (placeholder) |
| Team lead | (placeholder) | (placeholder) |
| Supabase support | — | support.supabase.com |
| Oracle Cloud support | — | cloud.oracle.com/support |
| DataImpulse proxy | — | (placeholder dashboard URL) |

**Escalation path:** On-call → Team lead → CTO

---

## Post-Incident Template

```
## Incident Report YYYY-MM-DD

**Severity:** P0 / P1 / P2
**Duration:** HH:MM (HH:MM UTC+7 — HH:MM UTC+7)
**Detected by:** (Telegram alert / user report / monitoring)

**Root Cause:**
(Concise 1-2 sentence technical root cause)

**Impact:**
- Users affected: (estimated count or "unknown")
- Downloads failed: (estimated count or "unknown")
- Revenue impact: (if applicable)

**Timeline:**
- HH:MM — Alert fired / issue detected
- HH:MM — Engineer paged / started investigation
- HH:MM — Root cause identified
- HH:MM — Fix applied
- HH:MM — Recovery confirmed

**Resolution:**
(What was done to fix it)

**Prevention:**
(What will prevent recurrence)

**Action Items:**
- [ ] (specific, assigned, time-bounded)
- [ ] (specific, assigned, time-bounded)
```

---

## Scenario: Signup / password-reset emails land on localhost (ERR_CONNECTION_REFUSED)

**Symptom.** A user registers, clicks the confirmation link in the email, and
the browser shows `localhost refused to connect` at
`localhost:3000/#access_token=...`.

**What actually happened.** Nothing is wrong with the account — Supabase
confirms the email server-side when the link is opened, so the user *is*
registered and confirmed. Only the page they were sent to afterwards does not
exist. They are dropped on a dead page with no way to tell it worked, and will
usually assume registration failed.

**Cause.** The Supabase project's **Site URL** is still the default
`http://localhost:3000`, and `emailRedirectTo` is only honoured for URLs that
appear in the redirect allowlist. The frontend now passes
`emailRedirectTo: <origin>/` (AuthContext.signUp), but Supabase silently
reverts to the Site URL when that origin is not allow-listed, so the code
change alone does not fix it.

**Fix — Supabase dashboard, cannot be done from this repo.** There is no
service-key API for auth config; it needs dashboard access or a Management API
personal access token (`sbp_...`), neither of which is stored here.

1. Supabase dashboard → **Authentication → URL Configuration**
2. **Site URL**: `https://dvid.cmc-1.vibenode.matbao.ai`
3. **Redirect URLs**: add
   - `https://dvid.cmc-1.vibenode.matbao.ai/**`
   - `http://localhost:5173/**` (Vite dev, keep for local work)
4. Save, then register a throwaway address and confirm the link lands on the
   real site.

**Users already stuck.** No action needed — their account exists and is
confirmed. They can sign in normally at the production site; the dead redirect
page has no bearing on the account.

**Related gap (still open).** `AuthContext.resetPassword` sends users to
`<origin>/reset-password`, but that path is not in `PATH_MAP` in App.jsx, so it
falls through to the landing page and there is no UI to enter a new password.
Fixing the Site URL makes the reset link reach the site but not a working reset
screen; that page still needs to be built.
