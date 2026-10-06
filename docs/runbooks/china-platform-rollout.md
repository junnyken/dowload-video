# Runbook — China platform rollout (Douyin, Phase 32B-2 Stage A)

Scope: **Douyin single public video only.** Kuaishou, Xiaohongshu, Bilibili and Lemon8 are not routed.
Modes are `off | benchmark | canary_admin | on`. There is no percentage canary: at about 110 downloads a week,
5 % would give almost no samples. There is no admin UI page and no DB table. Everything below is env (Vibe Host)
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

The same controls are in the admin panel under Config → "Apify (lấy video Douyin)".

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
