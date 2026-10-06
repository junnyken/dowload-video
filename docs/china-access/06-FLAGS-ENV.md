# 06 — Feature flags and Vibe Host environment checklist

Set these in **Vibe Host → project env** for the backend **and** the Celery worker. Both run the Douyin branch.
Never put a value in source, chat, or a commit. Names only below, with the safe defaults that apply when a variable is unset.

## Master
| Name | Default | Notes |
|---|---|---|
| `CHINA_ACCESS_ENABLED` | `false` | Master switch. Off → the layer is never entered. |
| `CHINA_ACCESS_GLOBAL_KILL_SWITCH` | `false` | Env kill switch. The Redis one is set from the admin endpoint. |

## Guardrails
| Name | Default | Notes |
|---|---|---|
| `CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT` | `5` | Paid path only, per IP (and the shared `unknown` worker bucket) |
| `CHINA_ACCESS_FREE_DAILY_RESOLVE_LIMIT` | `20` | Paid path only, per signed-in user |
| `CHINA_ACCESS_ADMIN_DAILY_RESOLVE_LIMIT` | `50` | Paid path only, admin canary |
| `CHINA_ACCESS_MANAGED_TIMEOUT_SEC` | `90` | Whole managed call, including polling; on deadline the run is aborted |
| `CHINA_ACCESS_MANAGED_MAX_RETRIES` | `0` | Clamped to 0 in wave 1 |
| `CHINA_ACCESS_CACHE_TTL_SEC` | `1800` | Upper bound; the policy may be lower |
| `CHINA_ACCESS_DEDUPE_TTL_SEC` | `1800` | In-flight lock and once-per-paid-provider-per-URL window |
| `CHINA_ACCESS_DEDUPE_WAIT_SEC` | `20` | How long a duplicate request waits for the first one's result |
| `CHINA_ACCESS_MANAGED_PROBES_ENABLED` | `false` | Lets probe-origin requests use a paid provider |
| `CHINA_ACCESS_HEALTH_FAIL_THRESHOLD` | `5` | Consecutive failures → degraded (skipped) |
| `CHINA_ACCESS_HEALTH_COOLDOWN_SEC` | `600` | Degraded → one recovery attempt after this |

## Douyin
| Name | Default | Notes |
|---|---|---|
| `CHINA_ACCESS_DOUYIN_ENABLED` | `false` | Routes Douyin single media through the layer (native without ScraperAPI, then managed if the mode allows) |
| `CHINA_ACCESS_DOUYIN_MANAGED_MODE` | `off` | `off` \| `benchmark` \| `canary_admin` \| `on`. This is the ceiling; the admin can lower it at runtime |
| `CHINA_ACCESS_DOUYIN_PROVIDER_ORDER` | `native_douyin,apify_douyin` | Correction #3: `apify_douyin,native_douyin` = managed-first, after the benchmark only |
| `CHINA_ACCESS_DOUYIN_MANAGED_DAILY_CALL_LIMIT` | `50` | Platform level |
| `CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD` | `1.00` | Platform level |

## Apify (provider level)
| Name | Default | Notes |
|---|---|---|
| `CHINA_ACCESS_APIFY_TOKEN` | *(empty)* | **Secret.** Fallback only: a token saved in the admin panel (Config → "Apify (lấy video Douyin)", Redis `china:secret:apify_token`) takes precedence. Neither set → `apify_douyin` is never eligible |
| `CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT` | `50` | |
| `CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD` | `1.00` | |
| `CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD` | `5.00` | Calendar month, UTC |
| `CHINA_ACCESS_APIFY_DOUYIN_ACTOR_ID` | `natanielsantos~douyin-scraper` | |
| `CHINA_ACCESS_APIFY_DOUYIN_EST_COST_USD` | `0.0071` | FREE-tier list price per result + start event (01 §2) |
| `CHINA_ACCESS_APIFY_RUN_MAX_CHARGE_USD` | `0.02` | Sent as `maxTotalChargeUsd` per run |
| `CHINA_ACCESS_APIFY_REQUIRE_DURATION` | `false` | The documented output has no video duration |

## Benchmark
| Name | Default | Notes |
|---|---|---|
| `CHINA_ACCESS_BENCHMARK_DOUYIN_URLS` | *(empty)* | Comma/newline list of public fixture URLs (owner-supplied) |
| `CHINA_ACCESS_BENCHMARK_FIXTURES_FILE` | *(empty)* | JSON file alternative (05) |
| `CHINA_ACCESS_BENCHMARK_DIR` | `backend/downloads/china_benchmark` | Output directory |

## Phase 32B-2 Stage A (Douyin): validation, cost, alerts, rollback, watermark, report
| Name | Default | Notes |
|---|---|---|
| `CHINA_ACCESS_BENCHMARK_VALIDATE_MEDIA` | `true` | Benchmark checks every successful media URL (range fetch + ffprobe) |
| `CHINA_ACCESS_ROUTER_VALIDATE_MEDIA` | `false` | Same check inside the router on live traffic; an `unusable` URL becomes `parse_failed` (fallback-eligible). Costs up to a few MB of CDN traffic per request |
| `CHINA_ACCESS_MEDIA_VALIDATE_MAX_BYTES` | `2097152` | First range request (clamped to 64 KiB–4 MiB); one more request only to complete a moov box (4 MiB cap) |
| `CHINA_ACCESS_MEDIA_VALIDATE_MIN_BYTES` | `65536` | Smaller body = `body_too_small` |
| `CHINA_ACCESS_MEDIA_VALIDATE_TIMEOUT_SEC` | `20` | httpx timeout |
| `CHINA_ACCESS_COST_OVERRUN_FLAG_PCT` | `50` | Flag a run when actual > estimate × (1 + pct/100) |
| `CHINA_ACCESS_SPEND_ALERTS_ENABLED` | `true` | Telegram alert at 50/80/100 % of the provider daily and monthly ceilings, once per threshold per period |
| `CHINA_ACCESS_AUTO_ROLLBACK_ENABLED` | `true` | Douyin only; lowers the runtime mode to `benchmark`, never raises |
| `CHINA_ACCESS_AUTO_ROLLBACK_PROBE_FAILURES` | `2` | Consecutive managed probe failures |
| `CHINA_ACCESS_AUTO_ROLLBACK_WINDOW` | `10` | Last N managed attempts (users, workers, probes; benchmark excluded) |
| `CHINA_ACCESS_AUTO_ROLLBACK_MIN_USABLE_RATE` | `0.5` | Rollback when the usable-media success rate over the full window is below this |
| `CHINA_ACCESS_WATERMARK_MIN_REVIEWED` | `10` | Reviews needed before a route may report `watermark_free` |
| `CHINA_ACCESS_WATERMARK_MIN_FREE_RATE` | `0.9` | Share of reviews that must be `watermark_free` |
| `CHINA_ACCESS_BENCH_MIN_SAMPLE` | `20` | Report rule R1 |
| `CHINA_ACCESS_BENCH_PROMOTE_USABLE_RATE` | `0.8` | Rules N1, M4, M6 |
| `CHINA_ACCESS_BENCH_REJECT_USABLE_RATE` | `0.5` | Rules N2, M1 |
| `CHINA_ACCESS_BENCH_NATIVE_MARGIN` | `0.2` | Rule M6: managed must beat the best native by this margin |
| `CHINA_ACCESS_BENCH_MAX_COST_PER_USABLE_USD` | `0.01` | Rule M3 |
| `CHINA_ACCESS_BENCH_MAX_TIMEOUT_SHARE` | `0.2` | Rule M2 |

## Do NOT set
- **`APIFY_TOKEN`**: it activates the **legacy** unbudgeted Apify path for every Douyin download
  (`downloader.py:1559`) and channel (`downloader.py:3474`). That path can bill two runs per request
  (`apify_service.py:161-168`) and opens the bulk/channel 422 gates (`douyin_extractor.py:145`). Use
  `CHINA_ACCESS_APIFY_TOKEN` only.

## Owner actions
1. Create a **separate** Apify account and set a monthly spending limit in the Apify console (hard stop independent of this code).
2. Save its API token in the admin panel (Config → "Apify (lấy video Douyin)"). It is validated with a free Apify call
   before it is stored, and it can be replaced later without a redeploy. Alternative: `CHINA_ACCESS_APIFY_TOKEN` on Vibe Host
   (backend + worker). Do not paste it anywhere else.
3. Supply 20–30 public Douyin fixture URLs (`CHINA_ACCESS_BENCHMARK_DOUYIN_URLS`).
4. Follow the rollout steps in 04.
