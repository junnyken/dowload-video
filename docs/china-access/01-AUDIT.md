# 01 — Repository audit (Phase 32B-1, wave 1)

Base: branch `feat/china-access` from `github/main` 7e6856a (= production).
All paths are relative to `backend/` unless noted. Line numbers are from 7e6856a.
"Verified" = read in code. "Unverified" = not exercised against the live service.

## 1. Douyin extraction path (the one wave 1 hooks into)

| What | Where | Notes |
|---|---|---|
| Entry for every single-video download (`/fetch-link`, worker, probe) | `app/services/downloader.py:1494` `_extract_video_info_impl` | Douyin branch at `:1546-1645`. |
| Legacy Apify call (unbudgeted) | `downloader.py:1559-1566` | Runs `extract_douyin_apify_sync` whenever `APIFY_TOKEN` is set. No budget, no dedupe, no kill switch. |
| Native chain | `app/services/douyin_extractor.py:736-802` `extract_douyin_video` | Order: yt-dlp + cookie (`:774-779`), iesdouyin share page (`:783-787`), TikWM (`:790-793`), ScraperAPI SSR (`:796-800`), then `raise ValueError(DOUYIN_COOKIE_REQUIRED_MSG)` (`:802`). |
| yt-dlp signature-cookie dependency | `douyin_extractor.py:610-623`, `:689-729` | The aweme detail endpoint returns an empty body without signature cookies. yt-dlp's DouyinIE has the "TODO: Run verification challenge code to generate signature cookies" (quoted from DECISIONS-WAVE1 §3; the yt-dlp source itself was not re-read in this task). |
| CDN byte download after metadata | `downloader.py:1572-1640` | Direct first, then the CN residential proxy (`IPROYAL_PROXY_CN`). The access layer does not touch this. It only replaces the metadata step. |
| Cookie-required message + classifier | `douyin_extractor.py:129-134`; `app/core/failure_classifier.py:38-61` | Matched by `COOKIE_REQUIRED_SIGNALS` → USER_ACTION, no retry (7e6856a). |
| Server-access pre-check | `douyin_extractor.py:137-154` `douyin_server_access_available()` | True if `APIFY_TOKEN`, `DOUYIN_COOKIES_B64` or a selectable pooled cookie exists. |
| 422 `cookie_required` gates | `app/api/routes.py:54-79` (`_douyin_cookie_gate`, used at `:459` and `:1143`), `app/tasks/video_tasks.py:740-748`, `app/api/container.py:799-815` | All of them call `douyin_server_access_available()`. |
| Channel scrape | `downloader.py:3473-3483` (Apify profile, unbudgeted) then `:3485+` (ScraperAPI) | Out of scope for wave 1 (single media only). |

### 1.1 ScraperAPI inside the Douyin chain (correction #2)
`douyin_extractor.py:463-603` `_try_scraperapi_ssr` calls `http://api.scraperapi.com/` with `render=true`,
`country_code=cn` (`:477-485`), using the key pool `app/core/scraperapi_pool.py:118-140`. This is paid.
There is no per-call cost accounting anywhere in the repo. ScraperAPI's credit price for `render=true` + geo was **not
verified** in this task.
**Decision taken:** when the access layer handles a Douyin request, the native provider runs the chain with
`skip_scraperapi=True` (a new keyword-only parameter, default `False`, so legacy callers are unchanged). ScraperAPI is
therefore never called from the access layer and needs no budget class in wave 1. If the owner wants it back, it
goes in as its own paid provider with its own budget class (wave 2).

## 2. Existing Apify integration (`app/services/apify_service.py`)

Verified in code:
- Token is `APIFY_TOKEN` read at import (`:31`). Actor IDs are hardcoded (`:35`, `:38`).
- `extract_douyin_apify` (`:138-176`) tries a **sync run** (`:179-226`) and, if that returns nothing, starts a **second,
  async run** (`:229-320`). One failed request can therefore bill **two** actor runs. The access layer does not reuse
  this function for that reason.
- Input sent: `{"postUrls": [url], "maxItems": 1}` (`:186-189`, `:236-239`).
- Parser `_parse_apify_video_item` (`:323-423`) looks for `no_watermark_video_url`, `videoUrl`, `video_url`,
  `playAddr`, `play_url`, `videoPlayUrl`, `video.play_addr.url_list`, `video.playAddr|downloadAddr`.
- It labels the result `"provider": "apify"` and does not return a duration.
- It logs `resp.text[:500]` on errors (`:211`) and the item keys (`:363`).

Checked against the actor's public metadata (fetched 2026-10-06 from `https://api.apify.com/v2/acts/natanielsantos~douyin-scraper`
and its latest build `0.5.93`, the same data shown on https://apify.com/natanielsantos/douyin-scraper):
- **Input schema** properties: `postUrls` (array), `profileUrls`, `searchTermsOrHashtags`, `maxItemsPerUrl` (int, default 0),
  `scrapeAdditionalUserInfo`, `scrapePlayCount`, `shouldDownloadVideos`, `shouldDownloadCovers`, `maxDownloadSize`,
  `storageName`, sort/date filters. **`maxItems` is not an input field.** The existing code puts it in the body, where
  the actor most likely ignores it (unverified). The platform-level cap is the `maxItems` *query parameter* of
  `POST /v2/acts/{id}/runs` (https://docs.apify.com/api/v2/act-runs-post).
- **Output** (README example): `id`, `text` (caption), `url`, `createTime`, `thumb`, `authorMeta{name,…}`,
  `musicMeta{…, duration}`, `videoMeta{cover, originCover, width, playUrl}`, `statistics{…}`, `hashtags`, `mentions`,
  plus an `error` column in the dataset view.
- **Mismatch (important):** the media URL is at `videoMeta.playUrl`, and `_parse_apify_video_item` never reads it.
  If the README example is accurate, the existing parser returns `None` for every item, and the legacy path pays for
  both runs and still fails. This is unverified live (no token, no paid call made).
- **No video duration** in the documented output. `musicMeta.duration` is the *music* length. The access-layer parser
  accepts `videoMeta.duration` if present. Otherwise duration is `None` and the result is flagged (see 02 §6).
- **Pricing** (pay per event, `pricingInfos` current since 2026-09-03): `result` $0.007 (FREE tier) / $0.005 (BRONZE) /
  $0.004 (SILVER+), `apify-actor-start` $0.00005 per GB of memory, add-ons $0.0005–0.001 (all off by default).
  This matches DECISIONS-WAVE1 ($7/$5/$4 per 1000). Public 30-day run stats: 10 872 SUCCEEDED / 10 950 total.
- **Actual cost** is exposed on the run object as `usageTotalUsd` ("Total cost in USD for this run. Represents what you
  actually pay.") and `chargedEventCounts` (https://docs.apify.com/api/v2/actor-run-get). The access layer records
  `usageTotalUsd` when it is present. Otherwise it records the estimate and marks the cost `estimated`.
- `waitForFinish` on the run-start endpoint is at most 60 s. `maxTotalChargeUsd` caps the charge of one run (same doc).

## 3. Probe architecture

- `app/core/platform_probe.py:140-208` `probe_once`: metadata-only, through `extract_video_info_sync`, in its own
  `ThreadPoolExecutor`. Targets come from Redis `probe:targets` (`:76`) and Douyin has no default target (`:54-74`).
- Scheduler: Celery beat `probe-platforms-30m` (`app/core/celery_app.py:262-265`) → `app/tasks/probe_tasks.py:39-60`.
  It alerts only on transitions (`:63-90`).
- Risk: once the access layer is wired into the Douyin branch, a Douyin probe would go through it. The probe must
  never trigger a managed call (plan §14.1). Wave 1 marks probe calls with a context flag (`origin="probe"`). The
  router refuses paid providers for that origin unless `CHINA_ACCESS_MANAGED_PROBES_ENABLED` is on (default off).
- Context propagation caveat: `_run_with_timeout` (`downloader.py:538-562`) and `probe_once` submit to bare
  `ThreadPoolExecutor`s, which do **not** copy `contextvars`. Wave 1 changes both to `submit(copy_context().run, …)`
  so request context reaches the Douyin branch. This changes nothing else.

## 4. Cookie manager APIs

- `app/core/cookie_pool.py:13-24`: states are `soft` / `hard` / `expired` / `disabled` / absent (=healthy) plus a
  cooldown key. `has_selectable_cookie` (`:263-283`) is read-only.
- `app/core/cookie_manager.py:25,36`: `has_cookies_for`, `get_cookie_file`.
- `app/core/cookie_probe.py:94-180`: live retest for bilibili, reddit, instagram, youtube and tiktok. **No Douyin probe.**
- The user feature "Dùng cookie của tôi" sends `user_cookies_b64` on `/fetch-link` (`routes.py:722-737`). Per correction
  #1 it stays. The access layer passes the resulting temp file only to the native provider, never caches that
  result, and never sends it to a managed provider.
- `cookie_session_provider` is scaffold-only in wave 1 (interface, not registered).

## 5. Admin route patterns

- Auth: `app/api/admin.py:139-198` `verify_admin`. It accepts a Bearer session or `X-Admin-Token` (session or the raw
  password, with lockout) and enforces `ADMIN_ALLOWED_IPS` when set.
- Audit: `app/core/audit.py:94-118` `log_admin_action(request, action, resource_type=, resource_id=, metadata=)`. It never
  raises and records actor `"admin"` (single identity, correction #7).
- Closest pattern to copy: `app/api/admin_asr.py` (summary + kill switch, mounted at `app/main.py:483-484`).
- Admin UI: `frontend/src/admin/pages/*` (AsrPage, ProbesPage, PlatformsPage…). See 07.

## 6. Budget pattern to reuse (`app/services/asr/budget.py`)

Verified: day keys are UTC `yyyymmdd` (`:45-46`, a pinned clock at `:40-42`). Order is kill switch, then ceiling (`:73-98`).
It reserves, then checks, then releases on overflow. It fails **closed** when Redis is down (`:87-91`), sends a once-per-day
alert via `SET NX` (`:152-167`), and keeps TTL'd ledger keys (`:36`, `:85-86`).
**Correction to the task brief:** this module stores USD as **floats** (`INCRBYFLOAT`, `:83`), not micro-USD
integers. The access layer keeps the same *pattern* but stores integer micro-USD (`INCRBY`), as plan §9.2 key names
(`spend_usd_micros`) ask. This avoids float drift in the ceilings.

## 7. Conflicts with the plan

| # | Conflict | Resolution |
|---|---|---|
| C1 | `app/core/platform_policy.py:38` already defines `PlatformPolicy` (Phase 28 throughput policy) | New class is `ChinaPlatformPolicy` in `app/services/china_platforms/policy.py`. |
| C2 | `app/core/runtime_overrides.py:72` already has platform modes `healthy/constrained/degraded/reduced/paused` | China health states are separate (`china:health:*`). Wave 1 does not write Phase 28 overrides. |
| C3 | `KUAISHOU_ENABLED` exists (`kuaishou_extractor.py:66`) | All new flags are `CHINA_ACCESS_*` (correction #6). |
| C4 | Plan files `app/core/china_platform_flags.py` + `china_platform_settings.py` | One module, `app/services/china_platforms/settings.py`, keeps everything in the package. |
| C5 | Plan `app/api/admin/china_platforms.py` (package) | `app/api/admin` is a module (`admin.py`), not a package. The new file is `app/api/admin_china_platforms.py`, the same style as `admin_asr.py`. |
| C6 | Setting `APIFY_TOKEN` in production turns on the legacy unbudgeted path (`downloader.py:1559`, `:3474`), the double run (`apify_service.py:161-168`) and opens the 422 gates (`douyin_extractor.py:145`) | The access layer reads its **own** token, `CHINA_ACCESS_APIFY_TOKEN`. **Owner must NOT set `APIFY_TOKEN`.** See 06. |
| C7 | DB tables (§17) | Not in wave 1 (decisions). Redis plus the existing outcome store only. The migration plan is in 04. |
| C8 | `user_id`/IP are not passed into `extract_video_info` | Request context (`contextvars`) is bound in `/fetch-link` only when `CHINA_ACCESS_ENABLED`. Worker jobs have no requester context, so they use the shared `unknown` bucket at the anonymous limit (only matters in mode `on`). |
| C9 | No video duration in the actor output | Duration is validated when present and not required by default (`CHINA_ACCESS_APIFY_REQUIRE_DURATION=false`). |
| C10 | Existing redactor `redact_secrets` (`app/core/structured_log.py:71-95`) does not catch Douyin's `x-signature=` | The access layer strips the whole query string of any URL it logs or returns to admin. |
