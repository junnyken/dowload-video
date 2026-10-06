# Runbook — China platform rollout (Douyin, Kuaishou, Xiaohongshu)

Scope: **single public video only** — Douyin (Phase 32B-2 Stage A), and Kuaishou + Xiaohongshu (Phase 32B-3, section 8
below). Bilibili and Lemon8 are not routed. Profiles / channels are not in scope for any of them.
Modes are `off | benchmark | canary_admin | on`. There is no percentage canary: at about 110 downloads a week,
5 % would give almost no samples. There is no DB table. Apart from the Apify token pool and its cost page (section 9), everything below is env (Vibe Host)
or an admin API call.

Background: `docs/china-access/04-MIGRATION-ROLLBACK.md` (wave-1 rollout), `05-BENCHMARK-PLAN.md` (benchmark and
recommendation rules) and `06-FLAGS-ENV.md` (every variable and its default).

> **Never set `APIFY_TOKEN`.** It switches on the legacy, unbudgeted Apify path. The access layer uses
> `CHINA_ACCESS_APIFY_TOKEN` only.

## 0. Before you start

```bash
export API=https://<BACKEND_HOST>/api/v1           # backend base URL
# Session token (lasts as long as the admin session TTL). Type the password at the prompt; it is not saved in shell history.
read -rs ADMIN_PW
TOKEN=$(curl -s -X POST "$API/admin/login" -H 'Content-Type: application/json' \
  -d "{\"password\":\"$ADMIN_PW\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_token"])')
unset ADMIN_PW
H="Authorization: Bearer $TOKEN"
```

Every POST below needs a `reason` (or `note`) and is written to the audit log (`admin.china_access.*`).

Check that the env is set the same on **backend and Celery worker**:

| Variable | Value for this rollout |
|---|---|
| `CHINA_ACCESS_ENABLED` | `true` |
| `CHINA_ACCESS_DOUYIN_ENABLED` | `true` |
| `CHINA_ACCESS_DOUYIN_MANAGED_MODE` | ceiling: `benchmark` first, `canary_admin` after step 4, `on` only with owner approval |
| Apify token | saved in the admin panel (below); `CHINA_ACCESS_APIFY_TOKEN` env is only the fallback. Use a separate Apify account that has a spending limit set in the Apify console |
| `CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD` / `..._MONTHLY_...` | default `1.00` / `5.00` |
| `CHINA_ACCESS_BENCHMARK_DOUYIN_URLS` | 20–30 public fixture URLs supplied by the owner |
| `CHINA_ACCESS_SPEND_ALERTS_ENABLED`, `CHINA_ACCESS_AUTO_ROLLBACK_ENABLED` | `true` (default) |

Apify token (admin-stored > env > none). The token is never returned, only `last4`, the source and the account:

```bash
curl -s -H "$H" "$API/admin/china-platforms/apify/token"
read -rs APIFY_TOK    # paste the token at the prompt
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/apify/token" \
  -d "{\"token\":\"$APIFY_TOK\",\"reason\":\"set token\"}"; unset APIFY_TOK
curl -s -X POST -H "$H" "$API/admin/china-platforms/apify/token/test"        # free re-check (GET /v2/users/me)
curl -s -X DELETE -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/apify/token" \
  -d '{"reason":"rotate"}'                                                    # env fallback applies again, if set
```

Since task #6036 these four calls still work but only manage the pool's "legacy slot" entry (section 9). Use the
pool API or the admin page MANAGE → "Chi phí Apify" (also the "Apify" card in Config) for everything else.

Telegram alerts go through the existing `send_admin_alert`, so `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` must be set.

```bash
curl -s -H "$H" "$API/admin/china-platforms"          # master_enabled, apify_configured, modes, kill switches
curl -s -H "$H" "$API/admin/china-platforms/costs"    # spend vs ceilings + reconcile_today
```

## 1. Native-only baseline (free)

```bash
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/douyin/benchmark/run" \
  -d '{"include_managed": false, "max_urls": 30, "reason": "baseline native"}'
curl -s -H "$H" "$API/admin/china-platforms/douyin/benchmark"          # wait until "running": false
curl -s -H "$H" "$API/admin/china-platforms/douyin/benchmark/report"   # per-route aggregates + overall
```

Check `media_validation.ffprobe_available` in the run output. It must be `true` on the server. If it is `false`,
every success comes back `not_checked`, and rule R2 keeps the recommendation at `keep_benchmark`.

## 2. Managed benchmark (paid, at most about $0.21 for 30 URLs at the free-tier price)

Requires `CHINA_ACCESS_DOUYIN_MANAGED_MODE` ≥ `benchmark` and the token.

```bash
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/douyin/benchmark/run" \
  -d '{"include_managed": true, "max_urls": 30, "reason": "managed benchmark"}'
curl -s -H "$H" "$API/admin/china-platforms/douyin/benchmark/report"
```

Read `summary.apify_douyin`:
- `sample_size`, `success_rate`, `usable_media_rate`, `p50_latency_ms`, `p95_latency_ms`
- `cost_per_attempt_usd`, `cost_per_usable_success_usd`, `cost_flags` (`over_estimate` / `actual_missing`)
- `failure_category_mix`, `media_reasons`, `watermark_distribution`
- `recommendation` + `recommendation_reasons` (rule IDs R1–R2, N1–N3, M1–M7 in 05)

Cross-check the money in the Apify console. Our counters are reconciled to Apify's `usageTotalUsd` after each run.

## 3. Watermark review (manual, optional for promotion)

For each successful managed item, open the downloaded video, look for a watermark, then record what you saw.
Use the `canonical_url_hash` from the run file (`backend/downloads/china_benchmark/*.json`, field `attempts[]`).

```bash
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/douyin/watermark-review" \
  -d '{"provider": "apify_douyin", "url_hash": "<canonical_url_hash>", "state": "watermark_free", "note": "checked full video"}'
# state: watermark_free | watermarked | unknown
curl -s -H "$H" "$API/admin/china-platforms/douyin/watermark"
```

A route's results show `watermark_state = "watermark_free"` only after **≥ 10 reviews with ≥ 90 % watermark_free**
(`CHINA_ACCESS_WATERMARK_MIN_REVIEWED`, `CHINA_ACCESS_WATERMARK_MIN_FREE_RATE`). This step does **not** change any
user-facing wording. Any "no watermark" copy needs a separate BA-approved change.

## 4. Promote to admin canary

Only when `overall.recommendation == "promote_canary_admin"` and the owner agrees:

1. On Vibe Host, set `CHINA_ACCESS_DOUYIN_MANAGED_MODE=canary_admin` for backend and worker, then restart.
2. If a runtime override is lower (for example after an auto-rollback), raise it. The override can never go above the env ceiling:
   ```bash
   curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/douyin/mode" \
     -d '{"mode": "canary_admin", "reason": "benchmark passed"}'
   ```
3. Send canary requests as admin. `POST $API/fetch-link` with the header `X-Admin-Token: $TOKEN` (the session token, not
   the password). Public users stay native-only.
4. Watch the results:
   ```bash
   curl -s -H "$H" "$API/admin/china-platforms/douyin"   # providers[].health, auto_rollback window, recent_attempts
   curl -s -H "$H" "$API/admin/china-platforms/costs"
   ```

Mode `on` (every requester, within quotas) needs explicit owner approval and an env change to `on`.

## 5. Automatic protection (no action needed, but know what it does)

| Trigger | What happens |
|---|---|
| Provider daily or monthly spend crosses 50 / 80 / 100 % of its ceiling | One Telegram alert per threshold per period (UTC day / UTC month). Redis keys `china:spend_alert:*`. |
| Ceiling reached | Every paid call is refused before dispatch (`budget_exceeded`). No fallback to another paid route. |
| Actual cost > estimate × 1.5, or Apify returned no `usageTotalUsd` | The run is flagged in the attempt (`cost_flag`) and in `costs.reconcile_today`. The counters hold the actual (or the estimate when the actual is missing). |
| 2 consecutive managed **probe** failures (`CHINA_ACCESS_AUTO_ROLLBACK_PROBE_FAILURES`) | Mode override set to **`benchmark`**, audited as `admin.china_access.mode` with reason `auto_rollback`, critical Telegram alert. |
| Usable-media success < 50 % over the last 10 managed attempts from users, workers or probes (`..._WINDOW`, `..._MIN_USABLE_RATE`; benchmark runs excluded) | Same as above. |

Auto-rollback only lowers. It never raises a mode, and it does nothing when the mode is already `benchmark` or `off`.
Check the last rollback with `GET $API/admin/china-platforms/douyin` → `auto_rollback.last_rollback`.
To recover, find the cause first (`recent_attempts`, `failure_category_mix`), then raise the mode with step 4.2.

## 6. Emergency rollback

Pick the smallest action that stops the problem. None of these needs a code deploy:

```bash
# a) Stop ALL paid China calls now (Redis kill switch; Douyin falls back to the legacy path)
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/global/kill-switch" \
  -d '{"on": true, "reason": "incident <id>"}'

# b) Douyin only
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/douyin/kill-switch" \
  -d '{"on": true, "reason": "incident <id>"}'

# c) Keep the layer, no managed calls
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/douyin/mode" \
  -d '{"mode": "off", "reason": "incident <id>"}'
```

If Redis or the API is unreachable, use the env route on Vibe Host (backend + worker, then restart):
`CHINA_ACCESS_GLOBAL_KILL_SWITCH=true`, or `CHINA_ACCESS_ENABLED=false` to remove the layer.
Deleting the admin-stored token (`DELETE …/apify/token`) stops managed calls only when no env token is set. As a last
resort, revoke the token in the Apify console. The Apify account spending limit is the hard stop outside this code.

Undo a kill switch with the same call and `{"on": false, ...}`. Kill switches and the mode override have no TTL, so they survive restarts.

## 7. After an incident

- Export the attempts: `GET $API/admin/china-platforms/douyin` (scrubbed: URL hashes only, no signed URLs).
- Compare `costs` with the Apify console invoice for the same UTC day.
- Note the incident, the rollback time and the `auto_rollback` / kill-switch audit rows in the task log.

## 8. Kuaishou and Xiaohongshu (Phase 32B-3)

Routes (provider research and prices: `docs/china-access/09-XHS-KUAISHOU-PROVIDERS.md`):

| Platform | Order | Managed actor | Estimate/call |
|---|---|---|---|
| Kuaishou | `apify_kuaishou` only (www.kuaishou.com does not answer from our servers) | `natanielsantos~kuaishou-scraper` | $0.00405 |
| Xiaohongshu | `native_xiaohongshu` (existing extractor + cookie pool `xiaohongshu`), then `apify_xiaohongshu` | `blue_puppy~rednote-video-downloader` | $0.00255 |

Xiaohongshu goes to the paid actor only after a native failure that the actor can fix (no/stale cookie, API not
answering, timeout, parse failure). A private note never reaches it; an image-only note reaches it (and costs one
$0.0025 `not_video` row) only when the native extractor could not see the note, i.e. without a working XHS cookie. With the managed mode `off`
Xiaohongshu runs native only; Kuaishou then has no route and every link fails with `provider_unavailable`.

The old Kuaishou scaffold (`KUAISHOU_ENABLED`, docs/KUAISHOU.md) is separate. Leave it unset: when it is on, its
downloader hook runs first and the access layer never sees Kuaishou links.

### Env (backend **and** Celery worker), names and defaults

| Variable | Default | Notes |
|---|---|---|
| `CHINA_ACCESS_ENABLED` | `false` | Master switch, shared with Douyin |
| `CHINA_ACCESS_KUAISHOU_ENABLED` | `false` | Routes Kuaishou single video through the layer; also makes `/resolve-input` and the classifier recognise Kuaishou links |
| `CHINA_ACCESS_KUAISHOU_MANAGED_MODE` | `off` | `off` \| `benchmark` \| `canary_admin` \| `on` (env = ceiling) |
| `CHINA_ACCESS_KUAISHOU_MANAGED_DAILY_CALL_LIMIT` | `20` | Platform level |
| `CHINA_ACCESS_KUAISHOU_MANAGED_DAILY_SPEND_CEILING_USD` | `0.10` | Platform level |
| `CHINA_ACCESS_APIFY_KUAISHOU_ACTOR_ID` | `natanielsantos~kuaishou-scraper` | |
| `CHINA_ACCESS_APIFY_KUAISHOU_EST_COST_USD` | `0.00405` | Effective price in our counters (cost floor) |
| `CHINA_ACCESS_XIAOHONGSHU_ENABLED` | `false` | Routes Xiaohongshu single video notes (explore / discovery/item / xhslink.com / xhslink.cn) through the layer |
| `CHINA_ACCESS_XIAOHONGSHU_MANAGED_MODE` | `off` | as above |
| `CHINA_ACCESS_XIAOHONGSHU_PROVIDER_ORDER` | `native_xiaohongshu,apify_xiaohongshu` | `apify_xiaohongshu` alone = managed only |
| `CHINA_ACCESS_XIAOHONGSHU_MANAGED_DAILY_CALL_LIMIT` | `20` | Platform level |
| `CHINA_ACCESS_XIAOHONGSHU_MANAGED_DAILY_SPEND_CEILING_USD` | `0.10` | Platform level |
| `CHINA_ACCESS_APIFY_XIAOHONGSHU_ACTOR_ID` | `blue_puppy~rednote-video-downloader` | `agentflow~xiaohongshu-video-downloader` is the documented alternative (set its estimate to `0.007`) |
| `CHINA_ACCESS_APIFY_XIAOHONGSHU_EST_COST_USD` | `0.00255` | |
| `CHINA_ACCESS_SHORT_LINK_TIMEOUT_SEC` | `6` | One free 302 lookup for `v.kuaishou.com` / `xhslink.com` links |
| `CHINA_ACCESS_MANAGED_SERVER_DOWNLOAD_BUDGET_SEC` | `45` | Shared with Douyin: a managed result's server copy stops after this, then the user's browser gets the CDN URL. No CN proxy is ever used for these two platforms |

Unchanged and shared by all three platforms: `CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT` (50),
`CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD` (1.00), `CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD` (5.00), the Apify
token (admin panel / `CHINA_ACCESS_APIFY_TOKEN`), the anonymous / signed-in / admin managed quotas and the kill switches.
Auto-rollback (section 5) now covers both platforms too.

### Enable, one platform at a time

1. Native-only first (Xiaohongshu): `CHINA_ACCESS_ENABLED=true`, `CHINA_ACCESS_XIAOHONGSHU_ENABLED=true`, mode `off`.
   Restart backend + worker. Make sure the cookie pool has a fresh `xiaohongshu` cookie. Try 5 public video notes on
   `/fetch-link`; `GET $API/admin/china-platforms/xiaohongshu` → `recent_attempts` shows `native_xiaohongshu` outcomes.
2. Benchmark (paid, about $0.08 for 30 URLs): put the owner's public URLs in
   `CHINA_ACCESS_BENCHMARK_KUAISHOU_URLS` / `CHINA_ACCESS_BENCHMARK_XIAOHONGSHU_URLS`, set the platform mode to
   `benchmark`, restart, then:
   ```bash
   curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/kuaishou/benchmark/run" \
     -d '{"include_managed": true, "max_urls": 20, "reason": "managed benchmark"}'
   curl -s -H "$H" "$API/admin/china-platforms/kuaishou/benchmark/report"
   ```
   Same with `xiaohongshu`. Compare `cost_per_attempt_usd` with the Apify console for the same runs.
3. Admin canary: mode `canary_admin`, restart, send `/fetch-link` with `X-Admin-Token: $TOKEN` (section 4.3).
   Check the media plays and that the server copy or the hand-off to the browser works (`provider` in the response,
   backend log line `[Downloader] <platform> via access layer (...)`).
4. `on` only with the owner's approval.

Kuaishou has no free route, so step 1 does not apply: start at step 2.

### Roll back

Same commands as section 6 with `kuaishou` or `xiaohongshu` instead of `douyin`. A platform kill switch (or
`CHINA_ACCESS_<P>_ENABLED=false`) sends that platform's links back to the old path: Xiaohongshu to the generic yt-dlp
path, Kuaishou to the generic path (which fails as before unless `KUAISHOU_ENABLED` is set).


## 9. Apify token pool (task #6036)

Several Apify accounts are rotated per paid call. **Each token must belong to an Apify organization account or to a
different legal owner with its own paid balance.** Apify Terms §4.3 forbid several personal accounts for one person;
organizations are allowed (up to 10 per person) and billed separately
(docs.apify.com/platform/collaboration/organization-account). `GET /v2/users/me` has no "is organization" field, so the
pool cannot check this. The label you give each token is the only record.

Admin page: MANAGE → **Chi phí Apify** (`/vid-admin/apify-costs`): per-platform numbers (today / 7 days / this
month), per-token usage and projections, pool totals, add / edit / disable / delete / refresh. The Config page
shows the same token list.

### What happens on each paid call

1. Entries that are `active`, enabled and under their own monthly ceiling are eligible.
2. Pick order: **priority** (lower number first; default 100, env fallback 1000), then **most remaining credit**.
   Remaining = min(Apify limit − usage, own ceiling − our month spend). Usage = Apify's `monthlyUsageUsd` at the last
   refresh + what we recorded on that entry since, or our own month figure if that is higher. Entries with unknown
   remaining go after known ones.
3. The call's estimate is reserved on the entry, then settled to the same figure the provider budget records (never
   below the estimate, as before). Platform / provider daily and monthly ceilings, kill switches and quotas are
   unchanged and still apply first.
4. If Apify **refuses to start** the run, the entry changes state:

| Apify answer to `POST /v2/acts/{id}/runs` | Entry state | Same video retried? |
|---|---|---|
| 402 (any type), or `not-enough-usage-to-run-paid-actor`, `platform-feature-disabled`, `x402-payment-required` | `exhausted` | Yes, once, on the next entry |
| 401 `invalid-token` / `token-not-provided`, 403 `insufficient-permissions`, `user-disabled`, `apify-plan-required-to-use-paid-actor` | `invalid` | Yes, once, on the next entry |
| 429 `rate-limit-exceeded`, `concurrent-runs-limit-exceeded`, `actor-memory-limit-exceeded`, any 5xx | `cooldown` (5 min) | No |
| Run started (201), or the start request timed out | — | **Never** (the run may be billed) |

The retry is safe because nothing was billed on the first account and the second is a different billing account.
The router's single budget reservation covers that one run. At most two entries are tried per request. Set
`CHINA_ACCESS_APIFY_POOL_RETRY_NEXT_TOKEN=false` to switch the retry off.

Sources: Apify OpenAPI (`https://docs.apify.com/api/openapi.json`), *Run Actor*
(`https://docs.apify.com/api/v2/act-runs-post`): 401 `invalid-token`; 402 "the user has exceeded their usage limit,
does not have enough credits…"; 403 `insufficient-permissions`; 429 `rate-limit-exceeded`
(`https://docs.apify.com/api/v2` → Rate limiting). The other type names come from the spec's `ErrorType` enum. The
exact type Apify sends for an organization that is out of credit was **not** observed live. Any 402 counts as
exhausted, whatever the type.

### What "exhausted" means and how it recovers

`exhausted` = Apify said this account cannot pay for a run now, or a refresh showed `monthlyUsageUsd ≥
maxMonthlyUsageUsd`, or `effectivePlatformFeatures.ACTORS.isEnabled = false`. It comes back to `active` without anyone
doing anything at `exhausted_until`. That is the end of Apify's `monthlyUsageCycle` from `/users/me/limits`, or the
start of the next UTC month when the cycle is unknown. It also comes back when "Làm mới" (refresh) shows remaining
credit. `invalid` only comes back through a successful refresh. `cooldown` ends after
`CHINA_ACCESS_APIFY_POOL_COOLDOWN_SEC`. `disabled` = switched off by the admin.

### Alerts (Telegram, via `send_admin_alert`)

| Alert | When | De-duplication |
|---|---|---|
| "Apify pool: token hết tiền / chạm giới hạn" (warning) | an entry turns `exhausted` | once per transition |
| "Apify pool: token không dùng được" (critical) | an entry turns `invalid` | once per transition |
| "Apify pool: không còn token dùng được" (critical) | a paid call finds no eligible entry | once, re-armed when an entry is picked again (max 1/day) |
| "Apify pool: sắp hết tiền" (warning) | known remaining < `CHINA_ACCESS_APIFY_POOL_LOW_PCT` % of known capacity | once per UTC month |
| "Apify pool: token chạm trần chi tiêu riêng" (warning) | an entry's own monthly ceiling would be exceeded | once per entry per month |

Alerts name the label and `••••last4` only.

### API

```bash
curl -s -H "$H" "$API/admin/china-platforms/apify/pool"            # entries (no tokens) + summary
read -rs APIFY_TOK
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/apify/pool" \
  -d "{\"token\":\"$APIFY_TOK\",\"label\":\"Tổ chức A\",\"priority\":10,\"monthly_ceiling_usd\":5,\"reason\":\"add org A\"}"; unset APIFY_TOK
curl -s -X POST -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/apify/pool/2" \
  -d '{"enabled": false, "reason": "pause org B"}'                   # label / priority / monthly_ceiling_usd / enabled
curl -s -X POST -H "$H" "$API/admin/china-platforms/apify/pool/2/refresh"   # free: /users/me + /users/me/limits
curl -s -X POST -H "$H" "$API/admin/china-platforms/apify/pool/refresh"     # all entries
curl -s -X DELETE -H "$H" -H 'Content-Type: application/json' "$API/admin/china-platforms/apify/pool/2" \
  -d '{"reason": "account closed"}'
curl -s -H "$H" "$API/admin/china-platforms/apify/metrics"         # platforms today/7d/month + entries + pool
```

A token cannot be edited. Add the new one, then delete the old one. The env entry (`id` `env`) cannot be deleted
here. Remove `CHINA_ACCESS_APIFY_TOKEN` on Vibe Host, or disable the entry. Every POST/DELETE except refresh needs
`reason` and is audited (`admin.china_access.apify_pool_*`, with id + label + last4 only).

### Migration

No action. On the first read after deploy, the token saved by the old card (`china:secret:apify_token`) becomes entry
`1` ("Token chính (đã lưu trước đây)") with its saved account and usage. The old key is kept as a mirror, so rolling the
code back still finds it. Deleting that entry also deletes the old key.

### Measurement and cost reductions

* Rollups: `china:metrics:{yyyymmdd}:{platform}` and `china:apify_pool:spend|calls:{id}:day:{d}`, 40-day TTL, UTC days.
* "Saved" = cache hits whose result came from a paid provider × that provider's estimate. It is an estimate.
* The cache never serves a result whose media URL expires within `CHINA_ACCESS_CACHE_EXPIRY_MARGIN_SEC` (600 s), and
  never stores one past its expiry. `CHINA_ACCESS_CACHE_EXTENDED_TTL_SEC` (off by default) lets results whose URLs
  carry a known expiry stay cached longer, still bounded by that expiry. What is cached is the whole normalized result,
  including the signed CDN URL. No metadata-only cache exists, because there is no cheap way to re-resolve a URL
  (Douyin native is broken).
* Order is unchanged: free route first (Douyin, Xiaohongshu). Managed-first only via `CHINA_ACCESS_<P>_PROVIDER_ORDER`.
  The anonymous paid quota stays at `CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT` (5) unless the owner lowers it.
