# 03 — File-by-file impact (wave 1)

## New

| File | Purpose |
|---|---|
| `backend/app/services/china_platforms/__init__.py` | Package marker and public names |
| `…/settings.py` | All `CHINA_ACCESS_*` env readers with safe defaults, managed-mode ordering |
| `…/errors.py` | `FailureCategory`, `ProviderFailure`, `ProviderFailureError`, `ChinaAccessFailure`, `AlreadyProcessing`, category→existing error-code map, `redact()` |
| `…/normalized_models.py` | `ChinaResolveRequest`, `NormalizedMediaFormat`, `NormalizedMediaResult`, `RequestContext`, `ProviderHealthSnapshot` |
| `…/policy.py` | `ChinaPlatformPolicy` and defaults for douyin/kuaishou/xiaohongshu/bilibili/lemon8 |
| `…/registry.py` | URL → platform identification, policy lookup with env order override and Redis mode override |
| `…/budget_guard.py` | Read-only pre-checks (steps 5–7), atomic reserve/rollback, settle, kill switches, snapshot |
| `…/request_cache.py` | Result cache, dedupe lock, per-URL paid-attempt marker |
| `…/provider_health.py` | Per provider×platform health and derived state |
| `…/provider_router.py` | Guarded router (plan §8) and the attempt recorder |
| `…/probes.py` | Probe registration skeleton (`probe_origin()` context, managed probes off) |
| `…/benchmark_runner.py` | Benchmark harness (JSON + CSV), CLI `python -m app.services.china_platforms.benchmark_runner` |
| `…/integration.py` | Request context binding, Douyin download hook, `managed_route_open_for_current_request()` |
| `…/providers/base.py` | Provider protocol + base class |
| `…/providers/native_provider.py` | Generic native wrapper |
| `…/providers/managed_actor_provider.py` | Generic actor flow + validation |
| `…/providers/apify_provider.py` | Apify REST adapter (one run per call) |
| `…/providers/cookie_session_provider.py` | Scaffold only, not registered |
| `…/providers/metadata_proxy_provider.py` | Scaffold only, not registered |
| `…/adapters/base.py` | Platform adapter base (canonicalize, providers) |
| `…/adapters/douyin.py` | Douyin canonicalization, native provider over `douyin_extractor`, Apify actor spec |
| `…/adapters/kuaishou.py`, `xiaohongshu.py`, `bilibili.py`, `lemon8.py` | Skeletons: URL matching + canonicalization only, not routed |
| `backend/app/api/admin_china_platforms.py` | Admin endpoints (plan §16), `verify_admin` + `log_admin_action` |
| `backend/tests/_china_fakes.py` | Shared fakes (FakeProvider, fakeredis fixtures) |
| `backend/tests/test_china_access_router.py` | Guard order, budget-before-dispatch, kill switch, once-per-provider, fallback, dedupe |
| `backend/tests/test_china_access_apify.py` | Apify adapter: single run, validation, cost, redaction (MockTransport) |
| `backend/tests/test_china_access_integration.py` | Flags-off regression (Douyin, TikTok, Bilibili, generic), hook behaviour, admin endpoints, benchmark |
| `docs/china-access/01…08-*.md` | Deliverables 1–8 |

Not created (decisions): `adapters/weibo.py`, `app/schemas/china_platforms.py` (pydantic models live in `normalized_models.py`;
the admin request bodies sit next to their routes, as in `admin_asr.py`), `app/tasks/china_platform_tasks.py` (the benchmark runs
from the admin endpoint as a background task or the CLI; no new Celery queue), and any DB migration.

## Modified (small, flag-gated)

| File | Change | Behaviour with all flags off |
|---|---|---|
| `backend/app/services/downloader.py` (Douyin branch ~`:1546`) | Calls `integration.resolve_douyin_via_access_layer()` first. When it returns a dict, the legacy Apify and native calls are skipped. | Returns `None` after one env read, so the legacy path runs exactly as before. |
| `backend/app/services/downloader.py` `_run_with_timeout` (`:551`) | `executor.submit(contextvars.copy_context().run, func, …)` | Same function, same args; only the caller's context variables become visible in the worker thread. |
| `backend/app/services/douyin_extractor.py` `extract_douyin_video` | New keyword-only `skip_scraperapi: bool = False` | Default keeps the four-provider chain, including ScraperAPI. |
| `backend/app/services/douyin_extractor.py` `douyin_server_access_available` | First line: `managed_route_open_for_current_request("douyin")` | Returns False after one env read. |
| `backend/app/api/routes.py` `fetch_link` | `bind_request_context(request, user)` right after the SSRF check | No-op unless `CHINA_ACCESS_ENABLED`. |
| `backend/app/core/platform_probe.py` `probe_once` | Submits inside a copied context marked `origin="probe"` | Same extraction call; the marker is only read by the access layer. |
| `backend/app/main.py` | Mounts `admin_china_platforms` router at `/api/v1/admin` | Admin-only endpoints added. |

Explicitly **unchanged**: `apify_service.py` (legacy, still used by the legacy path), `failure_classifier.py`,
`error_codes.py`, `download_outcomes.py`, cookie pool and probe scheduling, every non-Douyin branch of the downloader.
