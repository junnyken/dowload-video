# 04 — Migration and rollback plan

## Wave 1: no database migration
Decisions: "new DB tables (use Redis + existing outcome store; tables in wave 2)". Wave 1 state is Redis only, all keys
under the `china:` prefix with explicit TTLs (02 §5, §8, §9), except the two kill-switch keys and the mode override,
which are deliberately persistent until an admin clears them.

Losing Redis loses at most: today's/this month's spend counters, cached results, the last 200 attempts per platform
and the health counters. The budget **fails closed** when Redis is unreachable (no paid call), so loss of Redis cannot
cause spend. The Apify console remains the source of truth for money actually spent.

## Wave 2 (planned, not built): additive tables
Migration files go in `database/` following the existing numbering. They only add tables, with no `ALTER` on
`download_jobs` or user tables:
- `china_platform_provider_runs` (plan §17; `canonical_url_hash` only, no URL; no signed URLs, no tokens)
- `china_platform_benchmark_runs` (`metrics_json` = the JSON row from 05)
- `china_platform_runtime_overrides` (who/when/prior/new/reason/expires)
Retention: provider runs 90 d, benchmark rows kept, overrides kept. Rollback of the wave-2 migration means dropping the three
new tables. Nothing else depends on them.

## Rollout (wave 1)
1. Deploy with every flag at its default (all OFF). Expected change: none. Proof: the regression tests in 08.
2. Owner adds `CHINA_ACCESS_APIFY_TOKEN` (separate Apify account with a spending limit). **Do not set `APIFY_TOKEN`** (01 C6).
3. Set `CHINA_ACCESS_ENABLED=true`, `CHINA_ACCESS_DOUYIN_ENABLED=true`, `CHINA_ACCESS_DOUYIN_MANAGED_MODE=benchmark`.
   User traffic now goes native-only through the access layer with ScraperAPI skipped. That is equal to production today
   (0/1245 Douyin successes), minus one paid ScraperAPI attempt per request.
4. Run the benchmark (05), first native-only, then with `include_managed=true` on ≤ 30 fixture URLs (≈ $0.21 at FREE tier).
5. If §13.5 is met: `CHINA_ACCESS_DOUYIN_MANAGED_MODE=canary_admin`. Canary requests are `POST /api/v1/fetch-link` carrying the header
   `X-Admin-Token: <session token from POST /api/v1/admin/login>`. Only a live session token counts, never the raw password.
   The public site does not send this header, so normal users stay native-only.
6. Mode `on` and managed-first order (`CHINA_ACCESS_DOUYIN_PROVIDER_ORDER=apify_douyin,native_douyin`) only after the owner approves.

## Rollback (any stage, no code deploy)
| Severity | Action | Effect |
|---|---|---|
| Stop paid calls now | `POST /api/v1/admin/china-platforms/global/kill-switch {"on":true}` (Redis) or env `CHINA_ACCESS_GLOBAL_KILL_SWITCH=true` | The hook returns `None`, so Douyin goes back to the legacy path. With `APIFY_TOKEN` unset, the legacy path never calls Apify. |
| Stop Douyin only | `POST …/douyin/kill-switch {"on":true}` | Same, Douyin only. |
| Keep the layer, no managed | `POST …/douyin/mode {"mode":"off"}` | Native only through the layer. |
| Remove the layer | env `CHINA_ACCESS_ENABLED=false` (Vibe Host restart, no code change) | Exactly the pre-wave-1 behaviour. |
| Code rollback | redeploy 7e6856a | All `china:*` Redis keys become inert (TTL'd); nothing reads them. |

Rollback never deletes data. Kill switches are Redis keys without TTL, so they survive restarts. Clear them with the same endpoint and `{"on": false}`.
