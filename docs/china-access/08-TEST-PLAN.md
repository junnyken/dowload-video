# 08 — Test plan

All tests use `fakeredis` and mocks. **No network, no paid call.** The Apify adapter is tested with `httpx.MockTransport`.
Run the full backend suite twice, once under `TZ=UTC` and once under `TZ=Asia/Ho_Chi_Minh` (day keys are UTC and must not depend on the host TZ).

Shared fakes: `tests/_china_fakes.py`.

## Unit: router (`tests/test_china_access_router.py`)
- Guard order: each guard fails in turn, and the test asserts the earlier ones were consulted and the later ones were not (a call log
  across master → platform → quota → platform budget → provider budget → cache → dedupe → health → providers).
- Master off / global kill switch / platform kill switch / platform not routable → `platform_disabled`, no provider called.
- Budget before dispatch: the reserve is called before `provider.resolve`. A denial means `resolve` is never called, the failure is
  `budget_exceeded`, and later providers are not tried (no fallback).
- Each budget level independently denies: user quota, platform calls, platform spend, provider daily calls,
  provider daily spend, provider monthly spend. Rollback leaves the counters unchanged.
- Redis down → the budget pre-check and reserve both deny (`budget_unavailable`), and the router fails closed (`platform_disabled`). The download hook then falls back to the legacy path.
- Once per provider: an order with a duplicate name calls the provider once, and a provider failure is not retried.
- Paid marker: two sequential jobs for the same canonical URL → the second does not call the paid provider.
- Dedupe: 10 concurrent identical requests → exactly one paid `resolve` and one budget reservation; the others get
  the cached result or `AlreadyProcessing`.
- Fallback eligibility: `cookie_required` from native → managed runs; `private_or_login_required` / `unsupported_url`
  → managed does not run.
- Mode gating: `off` never calls managed; `canary_admin` only with an admin context; `benchmark` only from the harness;
  probe origin never gets managed.
- Settle: the actual cost replaces the estimate in daily, monthly and platform counters. No run → estimate refunded.
- Cache: success cached and second call `cache_hit=True` with no provider call; private context (user cookie) is not cached.
- Health: N consecutive failures → provider skipped until cooldown.

## Unit: Apify adapter (`tests/test_china_access_apify.py`)
- Exactly one `POST …/runs` per resolve, even when the run fails or times out (then one abort call).
- Query params include `maxItems=1`, `maxTotalChargeUsd`, `waitForFinish`. The body contains `postUrls` and no `maxItems`.
- Documented output (`videoMeta.playUrl`, `text`) → success. Missing title/media URL, an `error` item, an empty dataset or
  a bad duration → `parse_failed`. Missing duration → success with `duration_missing`, or failure when required.
- `usageTotalUsd` → `actual`; absent → `estimated`.
- 401/402/429/5xx/timeout map to categories.
- Redaction: the token, `x-signature` and any query string never appear in failure details, log records or attempt records.

## Integration / regression (`tests/test_china_access_integration.py`)
- **Flags off (default):** Douyin `_extract_video_info_impl` calls `extract_douyin_video_sync` with the same args as before
  (ScraperAPI not skipped). The hook reads no Redis. `douyin_server_access_available()` and `_douyin_cookie_gate` behave
  as before. TikTok, Bilibili and a generic yt-dlp URL never enter the access layer, even with flags **on**.
- Flags on: Douyin uses the layer. Native success → legacy-shaped dict → existing CDN download code. Aggregated
  `cookie_required` → `ValueError` containing `DOUYIN_COOKIE_REQUIRED_MSG` (classifier: USER_ACTION). The legacy `APIFY_TOKEN`
  branch is not called.
- `bind_request_context`: admin detection only with a valid session token, never the raw password.
- `_run_with_timeout` propagates context variables.
- Admin endpoints: auth required, kill switch/mode audited with prior/new/reason, mode cannot exceed the env ceiling,
  responses contain no token or signed URL.
- Benchmark harness: writes JSON and CSV with the §13.4 columns, makes no managed call unless requested, and skips managed on a denied budget.

## Not covered (needs the owner / live)
- Real Apify output shape and actual cost (needs a token and a paid call).
- Real Douyin native success rate (needs fixtures and production network).
- Celery worker context (no requester context in worker jobs, by design in wave 1).
