# Runbook: Platform / Provider Down

> Scenario: All downloads for a specific platform (e.g., YouTube, Instagram, TikTok) are failing with 403, bot-wall errors, or timeouts. The platform circuit breaker (`pcircuit:{platform}:state`) may be open.

---

## Symptoms

- All jobs for one platform fail quickly with errors containing `403`, `bot`, `forbidden`, `access denied`, or `temporarily unavailable`.
- The `failure_classifier` is classifying errors as `RETRYABLE` — causing repeated retries that exhaust the retry budget (3 attempts for RETRYABLE).
- Admin dashboard shows a spike in failed jobs from one platform.
- For YouTube specifically: errors may reference `LOGIN_REQUIRED`, `Sign in to confirm your age`, or PO token issues.

---

## Quick Check

```bash
# 1. Check the platform circuit breaker state (replace PLATFORM with youtube/instagram/tiktok etc.)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli get "pcircuit:youtube:state"'
# Expected: "closed" (normal), "open" (blocking), "half" (probing)

# 2. Check oracle circuit breaker (YouTube direct-VPS extraction path)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli get "oracle_circuit:state" && \
  redis-cli get "oracle_circuit:fail_count"'

# 3. Check recent error messages for this platform
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs celery --tail=100 | grep -i "youtube\|403\|forbidden\|bot"'
```

---

## Likely Causes

1. **Oracle/datacenter IP bot-blocked** — YouTube (and some other platforms) block datacenter IPs. The Oracle Cloud VPS IP is detected as non-residential. The `oracle_circuit` state transitions CLOSED → OPEN after 3 failures in 5 minutes. This is the most common YouTube failure mode.

2. **DataImpulse residential proxy exhausted (YouTube)** — VidGrab hard-depends on the residential proxy (`IPROYAL_PROXY` / DataImpulse) for YouTube downloads when `YOUTUBE_PROXY_DOWNLOAD=1`. A `407 TRAFFIC_EXHAUSTED` or `407 Proxy Authentication Required` error means the proxy credit is depleted.

3. **Platform-wide rate limiting** — The platform has temporarily banned the cookie pool's IP range. `pcircuit:{platform}:state = open` confirms the circuit breaker has tripped.

4. **yt-dlp extractor broken** — A platform changed its web client API; yt-dlp extraction fails. Usually follows a platform change, not gradual degradation.

5. **bgutil-pot service unavailable** (YouTube only) — PO token provider (`vidgrab-bgutil-pot-1`) is down. Without a valid PoToken, YouTube returns `LOGIN_REQUIRED` even with valid cookies.

---

## Recovery Steps

### Step 1 — Identify the platform and check circuit state

```bash
for platform in youtube instagram tiktok facebook twitter; do
  state=$(ssh vidgrab "docker exec \$(docker ps -qf name=vidgrab-redis-1) redis-cli get pcircuit:${platform}:state")
  echo "${platform}: ${state:-closed}"
done
```

### Step 2 (YouTube) — Check proxy credit and bgutil-pot health

```bash
# Check bgutil-pot containers
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose ps bgutil-pot bgutil-pot-2'

# Tail bgutil-pot logs for errors
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml logs bgutil-pot --tail=30'

# Check YouTube health probe result
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli get "yt_health:last_check"'
```

If bgutil-pot is down, restart it:
```bash
ssh vidgrab 'cd /home/ubuntu/vidgrab && docker compose restart bgutil-pot bgutil-pot-2'
```

### Step 3 — Manually reset the circuit breaker to attempt recovery

```bash
# Force circuit to half-open (triggers one probe attempt)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli set oracle_circuit:state half'

# Or for the platform circuit breaker
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli del pcircuit:youtube:state'
```

Wait 2-5 minutes and watch logs. If the probe succeeds, the circuit closes automatically (`HALF → CLOSED`).

### Step 4 — Enable proxy for YouTube if oracle IP is blocked

```bash
# Check current proxy setting
ssh vidgrab 'docker compose -f /home/ubuntu/vidgrab/docker-compose.yml exec backend env | grep YOUTUBE_PROXY'

# To enable proxy, update .env on VPS and redeploy (no-build)
ssh vidgrab 'echo "YOUTUBE_PROXY_DOWNLOAD=1" >> /home/ubuntu/vidgrab/.env'
bash ~/workspace/projects/Dowload-video/deploy-vps.sh --no-build
```

### Step 5 — Force yt-dlp auto-update (if extractor is broken)

```bash
# Trigger the ytdlp_auto_update beat task immediately
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-light-1) \
  celery -A app.core.celery_app.celery_app call ytdlp_auto_update'
```

### Step 6 — Test a single download manually to confirm recovery

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-1) \
  python3 -c "
from app.services.downloader import extract_video_info_sync
result = extract_video_info_sync(\"https://www.youtube.com/watch?v=jNQXAC9IVRw\")
print(result.get(\"title\", \"ERROR\"), result.get(\"url\", \"\")[:80])
"'
```

---

## Rollback / Mitigation

- If the proxy is exhausted: top up DataImpulse credit at the dashboard, then clear the oracle circuit: `redis-cli del oracle_circuit:state oracle_circuit:fail_count oracle_circuit:open_since`
- If yt-dlp is broken and the update does not fix it: pin a known-good version by updating the `Dockerfile` and redeploying.
- Enable Cobalt API as fallback: confirm `COBALT_API_URL` is set and `cobalt-api` container is running.

---

## When to Escalate

- Proxy `407 TRAFFIC_EXHAUSTED` and you cannot top up immediately — YouTube is fully down until credit is restored.
- All 3 extraction paths (direct, proxy, cobalt) fail for YouTube simultaneously.
- Platform outage is confirmed as platform-side (check `downdetector.com` or platform status pages).
- yt-dlp update does not fix the extractor — requires a code patch.
