# Runbook: Cookie Pool Depleted or Blocked

> Scenario: Downloads from a platform requiring authentication (Instagram, YouTube, Facebook, TikTok) are returning auth errors (`LOGIN_REQUIRED`, `sessionid invalid`, `checkpoint required`). The cookie pool may be fully depleted or all cookies are hard-blocked.

---

## Symptoms

- Download errors for Instagram, Facebook, TikTok, or YouTube contain: `sign in`, `login required`, `authentication required`, `sessionid`, `checkpoint`, or `account has been restricted`.
- `failure_classifier` returns `USER_ACTION` for these jobs (no auto-retry).
- The `check_cookie_expiry` daily task (runs at 09:30 UTC) may have fired a Telegram alert.
- Admin cookie pool status shows all cookies as `hard` blocked or expired.

---

## Quick Check

```bash
# 1. Check pool health for all auth platforms
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.cookie_pool import get_pool_status
import json
print(json.dumps(get_pool_status(), indent=2))
"'

# 2. Check health status of each cookie (instagram example)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli keys "cookie_health:instagram:*"'

# 3. Check expiry report for a specific platform
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.cookie_pool import get_expiry_report
import json
report = get_expiry_report(\"instagram\")
for c in report: print(c[\"index\"], c[\"health_status\"], c[\"expiry_status\"], \"days_left:\", c[\"days_left\"])
"'
```

---

## Likely Causes

1. **All cookies hard-blocked (6h TTL)** — Platform detected bot activity and triggered account checkpoints. The cookie pool rotates away from blocked cookies, but if all are blocked simultaneously, `get_cookie_from_pool` falls back to the least-recently-blocked cookie (last-resort mode).

2. **Cookies expired** — Session cookies (`sessionid`, `SID`, `c_user`) have a finite lifetime. The daily expiry check fires Telegram alerts at `expiry_status = critical` (≤7 days left) or `expired`. Downloads will fail with auth errors if cookies are past expiry.

3. **Platform aggressive anti-bot** — Some platforms (Instagram, X.com/Twitter) apply aggressive per-IP rate limits independent of cookie identity. Even valid cookies get challenged from an Oracle Cloud datacenter IP.

4. **Only one cookie in pool** — A single cookie being hammered by concurrent workers will always be in cooldown, and if it gets blocked there is zero fallback.

5. **`YOUTUBE_COOKIES_B64` / `INSTAGRAM_COOKIES_B64` env not set** — Cookies were never loaded into the pool at startup.

---

## Making cookies last (task #6127, 2026-10-07)

What the server does now, and the knobs:

| Mechanism | What it does | Knob |
|---|---|---|
| Cookie last | Facebook / Instagram / X try the post anonymously first; a pool cookie is spent only when that fails, and never on "video unavailable / not found / removed". | `COOKIE_LAST_PLATFORMS=facebook,instagram,twitter` (default empty = cookie first, old behaviour). Remove a platform to roll back. |
| Per-request pick | Each download picks a cookie from the pool (cooldown, LRU, daily cap); the layers of one download keep that pick. Before: one cookie per worker process until it got blocked. | — |
| Soft daily cap | Past its cap an account is only used when every other one is past its cap too. Never refuses a download. Defaults: instagram 40, facebook 60, twitter 60, tiktok 150, youtube 150. | `COOKIE_DAILY_CAP="instagram=30,facebook=0"` (0 = no cap) |
| Session write-back | yt-dlp re-saves the jar after each use; the next pick of that cookie saves it to the pool when every login cookie is still present (a logged-out jar is never saved). At most once per 10 min per account. Label, expiry, health and usage move with it (`cookie_meta.refresh_count`). | — |
| Cobalt before cookie | For a cookie-last platform, after the anonymous yt-dlp attempt fails, our own Cobalt (`services/cobalt/`, API-key protected) is tried before a pool cookie. An answer that is not a real video (Cobalt returns a JPEG for some posts) is rejected by ffprobe. Anonymous metadata attempts fail fast (no retries, ≤10 s). | backend `COBALT_API_URLS`, `COBALT_API_KEY` (secret); Cobalt service `API_URL`, `COBALT_API_KEY` |
| Outcome counts | Redis `cookie_last:stats:<UTC day>` → `<platform>\|anon_ok / cobalt_ok / cookie_ok / all_fail / gone`, 40 days. If `anon_ok` stays 0 for a platform while `cookie_ok` grows, take it out of `COOKIE_LAST_PLATFORMS` (it only adds latency). | — |
| Daily re-test | `retest_cookie_pool_daily` 09:00 UTC (queue `light`): youtube, tiktok, instagram, reddit, bilibili; up to 40 cookies; a cookie the platform rejects is marked expired + Telegram. | probe limits in `cookie_probe.py` |
| Expiry alert | `check_cookie_expiry` 09:30 UTC now covers every platform holding cookies. | — |

Operating rules (no code):
- Use aged secondary accounts, not new ones. 2–3 accounts per platform.
- Export from a private/incognito window, then **close the window — do not log out**. Logging out kills the exported session immediately.
- Do not use the same account in a browser elsewhere while it is in the pool.
- Telegram alerts need a working `TELEGRAM_BOT_TOKEN` — a `[Telegram] Sync API error: 403` in the log means alerts are not arriving.

## Recovery Steps

### Step 1 — View current pool state and identify the worst platform

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.cookie_pool import get_pool_status
s = get_pool_status()
for p, d in s.items():
    print(f\"{p}: total={d[\"total\"]} healthy={d[\"healthy\"]} hard_blocked={d[\"hard_blocked\"]} soft_blocked={d[\"soft_blocked\"]} expiry_warn={d[\"expiry_warnings\"]}\")
"'
```

### Step 2 — Clear soft blocks to allow immediate re-use (if rate-limit has passed)

```bash
# Clear all soft blocks for instagram (replace with target platform)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-redis-1) \
  redis-cli --scan --pattern "cookie_health:instagram:*" | \
  xargs -r docker exec $(docker ps -qf name=vidgrab-redis-1) redis-cli del'
```

Note: this only helps if the underlying cause (rate limit) has actually cleared. For hard blocks (6h), the platform may still reject the cookie — the block TTL is intentional.

### Step 3 — Add a fresh cookie via admin API

Export a fresh cookie from a logged-in browser session (use the `scripts/extract-cookie.py` helper or a browser extension such as "Get cookies.txt LOCALLY"), then base64-encode it:

```bash
# On your local machine
base64 -w 0 fresh_instagram_cookies.txt > /tmp/cookie_b64.txt

# Upload and add via admin API
curl -X POST https://dowloadvideo.io.vn/api/v1/admin/cookies/add \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"platform\": \"instagram\", \"cookie_b64\": \"$(cat /tmp/cookie_b64.txt)\", \"label\": \"account-2-$(date +%F)\"}"
```

### Step 4 — Refresh the env-backed cookie (for YOUTUBE_COOKIES_B64)

If YouTube cookies are loaded from the environment variable at startup:

```bash
# 1. Update the .env on VPS
ssh vidgrab 'nano /home/ubuntu/vidgrab/.env'
# Set YOUTUBE_COOKIES_B64 to the new base64-encoded cookies.txt

# 2. Redeploy without rebuilding (env reload)
bash ~/workspace/projects/Dowload-video/deploy-vps.sh --no-build
```

### Step 5 — Trigger the cookie expiry check manually

```bash
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-celery-light-1) \
  celery -A app.core.celery_app.celery_app call check_cookie_expiry'
```

This re-evaluates all cookies and fires Telegram alerts for any `critical` or `expired` states.

### Step 6 — Remove genuinely expired or banned cookies

```bash
# Remove cookie at index 0 for instagram (check index from expiry report first)
ssh vidgrab 'docker exec $(docker ps -qf name=vidgrab-backend) \
  python3 -c "
from app.core.cookie_pool import remove_cookie
remaining = remove_cookie(\"instagram\", 0)
print(\"Remaining cookies:\", remaining)
"'
```

---

## Rollback / Mitigation

- If no fresh cookies are available, disable auth-gated platform downloads temporarily and surface a clear user message.
- Per-platform cooldown windows are set in `cookie_pool.py` (`instagram: 20s`, `facebook: 15s`, `youtube: 5s`). These can be tuned via env if traffic increases.
- Soft-block TTL: 15 minutes (`SOFT_BLOCK_TTL`). Hard-block TTL: 6 hours (`HARD_BLOCK_TTL`). Both are hardcoded constants — changing them requires code deploy.

---

## When to Escalate

- All cookies across all accounts are hard-blocked simultaneously — indicates platform-level IP ban on the Oracle VPS IP.
- Cookie expiry alerts are firing daily and fresh cookies cannot be obtained — the managed accounts may have been permanently suspended.
- Hard blocks are clearing but cookies get re-blocked within minutes of re-use — the platform has flagged the VPS IP independently of cookie identity.
