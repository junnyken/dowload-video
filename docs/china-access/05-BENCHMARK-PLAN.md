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

## Phase 32B-2 Stage A additions (Douyin)

### Usable-media validation
After each successful attempt, the harness checks the returned media URL with
`app/services/china_platforms/media_validation.py`:
- SSRF guard on every hop.
- One range request (2 MB default), plus one more only to complete an MP4 moov box.
- Status 200/206, a media content type and at least 64 KiB.
- ffprobe from stdin finds a video stream and a duration between 0 and 6 h.

The attempt row then carries these fields:
- `usable_media_url`: `true`, `false`, or `null` (not checked).
- `media_check`: `usable`, `unusable`, `not_checked`, or `not_run`.
- `media_check_reason`, `media_url_http_status`, `media_content_type`, `media_bytes_read`, `media_duration_sec`.
- `media_url_expiry_if_known`: from `x-expires` or the TikTok hex path.

`not_checked` means ffprobe is missing or the moov box sits after mdat. It is never counted as usable.
`media_url_present` keeps the wave-1 meaning (the provider returned a URL). The CSV appends the new columns after the
wave-1 ones, without reordering them.

### Report
`summary.<route>` adds:
- `sample_size`, `usable_media_rate`, `cost_per_usable_success_usd`.
- `failure_category_mix`, `media_reasons`, `skip_mix`, `cost_flags`.
- `watermark_distribution` (manual reviews of this run's items: `watermark_free`, `watermarked`, `unknown`, `not_reviewed`).
- `recommendation` and `recommendation_reasons`.

`overall.recommendation` is the platform verdict. `GET /admin/china-platforms/douyin/benchmark/report` (or the CLI with
`--report`) recomputes the last run with the current watermark reviews. Stored attempts carry the URL hash only and every
string is redacted.

### Recommendation rules
Rules are evaluated in order; the first match wins (`benchmark_runner.recommend`). Thresholds come from env (06).

| Rule | Route | Condition | Result |
|---|---|---|---|
| R1 | any | `sample_size` < `BENCH_MIN_SAMPLE` (20) | `keep_benchmark` |
| R2 | any | at least one success with media `not_checked` | `keep_benchmark` |
| N1 | native | usable ≥ `PROMOTE` (0.8) | `native_only` |
| N2 | native | usable < `REJECT` (0.5) | `reject` |
| N3 | native | otherwise | `keep_benchmark` |
| M1 | managed | usable < `REJECT` (0.5) | `reject` |
| M2 | managed | `provider_timeout` ≥ `MAX_TIMEOUT_SHARE` (0.2) of attempts | `reject` |
| M3 | managed | cost per usable success > `MAX_COST_PER_USABLE_USD` ($0.01) | `reject` |
| M4 | managed | a native route meets N1 (with R1/R2 passed) | `native_only` |
| M5 | managed | any `cost_flag` (`over_estimate` / `actual_missing`) | `keep_benchmark` |
| M6 | managed | usable ≥ `PROMOTE` (0.8) and ≥ best native usable + `NATIVE_MARGIN` (0.2) | `promote_canary_admin` |
| M7 | managed | otherwise | `keep_benchmark` |

Overall verdict:
1. `native_only` if any native route has that verdict.
2. Otherwise the best managed verdict, in the order `promote_canary_admin` > `keep_benchmark` > `reject`.
3. With no managed route: `keep_benchmark`.

Watermark does not affect the verdict. It only gates `watermark_state` (06: ≥ 10 reviews, ≥ 90 % `watermark_free`), and the UI wording is not changed.
