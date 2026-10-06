# 05 — Benchmark plan

## Fixture URLs
- Source: env `CHINA_ACCESS_BENCHMARK_DOUYIN_URLS` (comma or newline separated) and/or a JSON file at
  `CHINA_ACCESS_BENCHMARK_FIXTURES_FILE`, shaped `{"douyin": [{"url": "...", "case": "public_single"}, ...]}`.
  The code holds no URLs (tests use fake ones with mocks).
- Requirements per platform (plan §13.2): 20–30 **public** items. Mix `public_single`, `short_url` (v.douyin.com),
  `long_media` (> 5 min) and, where lawfully available, one `cookie_required`/private item, used only to verify a truthful error.
  Do not include anything login-gated that we would then try to bypass.
- The owner supplies the list. Agents must not invent URLs (a monitor with invented targets reports false failures;
  see `platform_probe.py:20-28`).

## How to run
- CLI (on the server, inside the backend container):
  `python -m app.services.china_platforms.benchmark_runner --platform douyin [--include-managed] [--max-urls 30] [--out DIR]`
- Admin: `POST /api/v1/admin/china-platforms/douyin/benchmark/run` with
  `{"include_managed": false, "max_urls": 30, "reason": "..."}`. It runs as a background task, one at a time per platform
  (Redis lock `china:benchmark:lock:{platform}`, 2 h; released when the run ends). Read the result with `GET …/douyin/benchmark`.
- Managed calls happen only if **all** of these hold: `include_managed=true`, effective managed mode ≥ `benchmark`, the
  token is set, and the budget reserve succeeds for that call. A denied budget stops further managed calls in the run
  (`budget_exceeded`). Native runs always (it is free; ScraperAPI is skipped).
- Benchmark calls use the `admin` requester bucket (`CHINA_ACCESS_ADMIN_DAILY_RESOLVE_LIMIT`).
- Each URL is tried once per provider. Provider calls go through the router's guarded single-provider path, so the
  kill switch, budget, paid-URL marker and health apply exactly as in production. The cache is bypassed so each
  attempt is a real measurement.

## Output (one file pair per run)
`{out}/china_benchmark_{platform}_{UTC yyyymmddTHHMMSSZ}.json` and `.csv`. Default `out` =
`CHINA_ACCESS_BENCHMARK_DIR`, else `backend/downloads/china_benchmark/` (already git-ignored via `backend/downloads/`).

CSV columns = JSON `attempts[]` keys (plan §13.4, in this order):
```
platform,operation,provider,route_mode,request_id,case,canonical_url_hash,outcome,normalized_failure_category,
latency_ms,metadata_success,usable_media_url,media_url_expiry_if_known,watermark_state,proxy_bytes_if_used,
estimated_cost_usd,actual_cost_usd,cost_source,retry_count,duration_missing
```
- `outcome` ∈ `success|failure|skipped`. `skipped` covers a paid marker that already exists, a denied budget or a mode
  that is not allowed; the reason is in `normalized_failure_category`.
- `usable_media_url` is a boolean. The URL itself is never written.
- `media_url_expiry_if_known` comes from the `x-expires` query param when present (Douyin CDN), as ISO UTC.
- `proxy_bytes_if_used` is always `0`: wave 1 has no metadata-proxy provider. The native chain's CN proxy byte usage is not
  metered anywhere in the repo (unverified).
- `cost_source` ∈ `actual` (Apify `usageTotalUsd`) | `estimated` | `none`.
- `retry_count` is always `0` (managed retries are clamped to 0).

JSON top level:
```json
{"platform": "douyin", "started_at": "...", "finished_at": "...", "include_managed": false,
 "fixture_count": 30, "attempts": [...],
 "summary": {"<provider>": {"attempts": n, "successes": n, "success_rate": 0.0, "p50_latency_ms": n,
             "p95_latency_ms": n, "cost_total_usd": 0.0, "cost_per_success_usd": null,
             "cost_per_attempt_usd": 0.0, "error_mix": {"cookie_required": n}, "duration_missing": n,
             "watermark_validated": 0}}}
```

## Promotion decision (owner, plan §13.5)
At least 20 valid managed attempts, success rate clearly above native (native is measured 0/1245 in prod), and
cost per success under the owner's limit (FREE-tier list price ≈ $0.0071/result). Every result must have a
usable media URL, with no token, cookie or signed URL in logs (check `GET …/douyin` attempts). The kill switch must be
verified once. Watermark stays `unknown` unless someone validates it by hand. The harness does not judge watermarks.
