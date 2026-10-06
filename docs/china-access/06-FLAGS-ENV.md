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
| `CHINA_ACCESS_APIFY_TOKEN` | *(empty)* | **Secret.** Empty → `apify_douyin` is never eligible |
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

## Do NOT set
- **`APIFY_TOKEN`**: it activates the **legacy** unbudgeted Apify path for every Douyin download
  (`downloader.py:1559`) and channel (`downloader.py:3474`). That path can bill two runs per request
  (`apify_service.py:161-168`) and opens the bulk/channel 422 gates (`douyin_extractor.py:145`). Use
  `CHINA_ACCESS_APIFY_TOKEN` only.

## Owner actions
1. Create a **separate** Apify account and set a monthly spending limit in the Apify console (hard stop independent of this code).
2. Put its API token in `CHINA_ACCESS_APIFY_TOKEN` on Vibe Host (backend + worker). Do not paste it anywhere else.
3. Supply 20–30 public Douyin fixture URLs (`CHINA_ACCESS_BENCHMARK_DOUYIN_URLS`).
4. Follow the rollout steps in 04.
