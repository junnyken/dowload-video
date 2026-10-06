# 02 — Architecture (wave 1)

Package: `backend/app/services/china_platforms/`. Everything is behind `CHINA_ACCESS_ENABLED` (default off). With it off,
no code in the package runs on any request path: the two hooks check one environment variable and return
before touching Redis.

```
/fetch-link ──bind_request_context()──┐   (contextvars: requester key, is_admin, origin)
                                      v
downloader._extract_video_info_impl ─ Douyin branch ─ integration.resolve_douyin_via_access_layer()
        │  returns None when inactive → legacy path, byte-for-byte unchanged
        v
ProviderRouter.resolve(ChinaResolveRequest, RequestContext)
   ├─ registry / ChinaPlatformPolicy           (policy.py, registry.py)
   ├─ BudgetGuard (Redis, micro-USD)           (budget_guard.py)
   ├─ RequestCache + Dedupe (SET NX EX)        (request_cache.py)
   ├─ ProviderHealth                           (provider_health.py)
   └─ providers: native_douyin (adapters/douyin.py → douyin_extractor, ScraperAPI skipped)
                 apify_douyin  (providers/apify_provider.py → ManagedActorProvider)
        v
NormalizedMediaResult  → legacy dict (title, thumbnail_url, direct_mp4_url, …) → existing CDN download code
NormalizedFailure      → ValueError(message) that the existing classifier/outcome store already understand
```

## 1. Registry and policy (`registry.py`, `policy.py`)
`ChinaPlatformPolicy` (dataclass, frozen): `platform`, `routable_in_wave1`, `supported_operations`,
`provider_order{operation: [provider names]}`, `allowed_providers`, `managed_provider_allowed`,
`metadata_proxy_profile`, `requires_cookie_for`, `cache_ttl_sec`, `max_container_items`, `delivery_mode`,
`fallback_categories` (failure categories after which the next provider may run), `budget_class`.

| platform | wave-1 routable | single_media order (default) | managed allowed |
|---|---|---|---|
| douyin | yes | `native_douyin`, `apify_douyin` (order overridable by `CHINA_ACCESS_DOUYIN_PROVIDER_ORDER`, correction #3) | yes |
| kuaishou | no | `native_kuaishou` | no (wave 2) |
| xiaohongshu | no | `native_xiaohongshu` | no |
| bilibili | no (correction #5) | `native_bilibili` | no |
| lemon8 | no | `native_lemon8` | no |
| weibo | not registered (decisions) | — | — |

The order override may only re-order or drop providers in `allowed_providers`. Unknown names are ignored and logged.
Runtime overrides (Redis) can only **reduce**: a lower managed mode, a kill switch, or a health pause. Expanding a mode
beyond the deployment env is refused (plan §7.4).

Managed mode (`CHINA_ACCESS_DOUYIN_MANAGED_MODE`, ordered `off < benchmark < canary_admin < on`):
- `off`: the paid provider is never eligible.
- `benchmark`: eligible only in benchmark runs that explicitly request managed calls.
- `canary_admin`: also eligible for `/fetch-link` requests carrying a valid admin session in `X-Admin-Token`.
- `on`: eligible for every request.

The effective mode is `min(env, redis override)`. Probe-origin requests never get a paid provider unless
`CHINA_ACCESS_MANAGED_PROBES_ENABLED=true`.

## 2. Normalized contracts (`normalized_models.py`, `errors.py`)
As in plan §5: `ChinaResolveRequest`, `NormalizedMediaFormat`, `NormalizedMediaResult`, plus `ProviderFailure`.
`FailureCategory` is the plan's 13 values. Each maps onto an **existing** error code (no parallel codes):

| category | existing code (`app/core/error_codes.py`) |
|---|---|
| unsupported_url | `unsupported_url` |
| private_or_login_required | `private_or_login_required` |
| geo_restricted | `geo_blocked` |
| signature_or_verification_failed | `cookie_required` (the 2026-10-05 Douyin failure is a signature-cookie failure and production already reports it as `cookie_required`) |
| cookie_required, cookie_invalid | `cookie_required` |
| upstream_rate_limited | `rate_limited` |
| provider_timeout, provider_unavailable | `provider_unavailable` |
| parse_failed | `no_media_found` |
| budget_exceeded, platform_disabled | `provider_unavailable` (the user is not told about internal budgets) |
| unknown | `processing_failed` |

On the Douyin path, the user-facing message for any aggregate that contains `cookie_required` stays exactly
`DOUYIN_COOKIE_REQUIRED_MSG`. That keeps `failure_classifier` (USER_ACTION, no retry) and the outcome store behaving
as they did after 7e6856a.

## 3. Provider interface (`providers/base.py`)
`ChinaPlatformProvider` Protocol (plan §6): `name`, `mode`, `paid`, `budget_class`, `can_handle`, `resolve`,
`estimate_cost_usd`, `health`. A provider raises `ProviderFailureError(ProviderFailure)` and never a bare exception: the router
wraps anything else as `unknown` and redacts it. Managed providers attach `last_run` (run id, actual cost or None,
elapsed) for the cost record.

- `NativeProvider`: wraps a callable. Cost is 0.
- `ManagedActorProvider`: generic actor flow (build input → run once → fetch items → validate → normalize). Actor
  id, input mapping, output mapping, timeout, max charge and estimate come from an `ActorSpec` built from settings,
  not from the router.
- `ApifyProvider(ManagedActorProvider)`: Apify REST only (`POST /acts/{id}/runs?waitForFinish&maxItems=1&maxTotalChargeUsd`,
  `GET /actor-runs/{id}?waitForFinish`, `GET /datasets/{id}/items`, abort on deadline). **Exactly one run per call.**
  It reuses `apify_service.APIFY_BASE`, `ACTOR_ID_VIDEO` and `_parse_apify_video_item` (as a fallback parser).
  The token is `CHINA_ACCESS_APIFY_TOKEN`.
- `CookieSessionProvider`, `MetadataProxyProvider`: interface only and never registered (decisions).

## 4. Router (`provider_router.py`) — plan §8.1 order, exactly
1. Identify the platform (`registry.identify_platform`) and canonicalize via the adapter → else `unsupported_url`.
2. Platform in registry and `routable_in_wave1` → else `platform_disabled`.
3. Master flag `CHINA_ACCESS_ENABLED` and global kill switch (env or `china:killswitch:global`) → `platform_disabled`.
4. Platform flag `CHINA_ACCESS_<P>_ENABLED` and platform kill switch → `platform_disabled`.
5. User/IP quota (paid path only, correction #8). Evaluated only if the eligible order contains a paid provider.
6. Platform managed calls/spend budget.
7. Provider daily/monthly spend and daily calls.
   Steps 5–7 are **read-only pre-checks** here. A denial marks the paid path `budget_exceeded`.
8. Cache lookup, then dedupe `SET NX EX`. Both are skipped for a private context (the user's own cookie): that result is user-specific. If another identical request
   holds the lock, wait up to `CHINA_ACCESS_DEDUPE_WAIT_SEC` for its cached result, else `AlreadyProcessing`.
9. Health + effective order (providers paused/tripped are skipped and recorded).
10. Providers in order, **once each**. Before each paid dispatch: re-check the kill switch, **atomically reserve** all
    budget levels (rollback on any denial; Redis error = fail closed), then claim the per-URL paid marker
    `china:paid:{provider}:{hash}` (`SET NX EX dedupe_ttl`). If the marker already exists, the reservation is rolled
    back and the provider is skipped. The same paid provider is therefore never called twice for the same canonical
    URL inside the TTL, across jobs and Celery retries. If the paid path is denied, the router **stops
    immediately** with `budget_exceeded` and no further fallback. After a failure, the next provider runs only if the
    failure category is in `policy.fallback_categories`.
11. Success → `NormalizedMediaResult`. Failure → `ChinaAccessFailure` (all attempts, stop reason).
12. Settle actual cost (delta vs. estimate), cache the success (public context only), write the scrubbed attempt record and
    counters, release the dedupe lock.

`CHINA_ACCESS_MANAGED_MAX_RETRIES` is read and **clamped to 0** in wave 1 (plan §13.4 "must be 0").

## 5. Budget model (`budget_guard.py`)
Integer micro-USD, UTC day/month, same pattern as `asr/budget.py` (kill switch first, reserve-then-check-then-rollback,
fail closed, TTL'd keys, once-per-day alert). Redis down → the router fails closed at step 3 (kill switch unreadable →
`platform_disabled`). The download hook treats an unreadable kill switch as "layer off" and runs the legacy path.

| key (plan §9.2) | TTL | limit env |
|---|---|---|
| `china:quota:user:{day}:{requester}:{platform}` | 3 d | anon/free/admin daily resolve limit |
| `china:platform:calls:{day}:{platform}` | 3 d | `CHINA_ACCESS_DOUYIN_MANAGED_DAILY_CALL_LIMIT` |
| `china:platform:spend_usd_micros:{day}:{platform}` | 3 d | `CHINA_ACCESS_DOUYIN_MANAGED_DAILY_SPEND_CEILING_USD` |
| `china:provider:calls:{day}:{provider}` | 3 d | `CHINA_ACCESS_APIFY_DAILY_CALL_LIMIT` |
| `china:provider:spend_usd_micros:{day}:{provider}` | 3 d | `CHINA_ACCESS_APIFY_DAILY_SPEND_CEILING_USD` |
| `china:provider:spend_month_usd_micros:{yyyymm}:{provider}` | 40 d | `CHINA_ACCESS_APIFY_MONTHLY_SPEND_CEILING_USD` |
| `china:killswitch:global`, `china:killswitch:platform:{p}` | none | admin |

Requester keys: `user:{id}` → free limit, `ip:{ip}` → anonymous limit, `admin` → admin limit, `unknown` (worker
jobs, no context) → anonymous limit, shared. The estimate per Douyin managed call is `CHINA_ACCESS_APIFY_DOUYIN_EST_COST_USD`
(default 0.0071 = $0.007 result + $0.00005 start, FREE tier). Each run is also capped server-side by Apify with
`maxTotalChargeUsd = CHINA_ACCESS_APIFY_RUN_MAX_CHARGE_USD` (default 0.02).

## 6. Managed output validation (Apify, Douyin)
Success requires: the run status is `SUCCEEDED`, there is at least one dataset item, the item has no `error`, and it has a
non-empty title (`text`/`desc`/`title`). It also needs an `http(s)` media URL (`videoMeta.playUrl`, then the legacy
parser's fields). If a duration is present it must be a positive number ≤ 6 h. A missing duration is accepted
unless `CHINA_ACCESS_APIFY_REQUIRE_DURATION=true`: the documented output has no video duration (01 §2). Missing
duration is recorded in the attempt as `duration_missing=true`. `watermark_state` is always `unknown` (no
trustworthy signal; plan §12.2).

## 7. Health model (`provider_health.py`)
Per `(provider, platform)` hash `china:health:{provider}:{platform}` holds `consecutive_failures`, `last_failure_category`,
`last_failure_ts`, `last_success_ts`, and `paused` (admin). Derived state:
`paused` (admin) → `degraded` (≥ `CHINA_ACCESS_HEALTH_FAIL_THRESHOLD`=5 consecutive failures, skipped until
`CHINA_ACCESS_HEALTH_COOLDOWN_SEC`=600 has passed) → `recovery` (cooldown passed, one attempt allowed; success resets
it) → `constrained` (≥ 2 consecutive failures, still used) → `healthy`. Categories `budget_exceeded`,
`platform_disabled`, `private_or_login_required` and `unsupported_url` do not count as provider failures, so no flapping
on user errors. The cooldown is the anti-flap window.

## 8. Cache and dedupe (`request_cache.py`)
- `china:cache:{platform}:{operation}:{sha256(canonical)[:32]}`: the JSON of the normalized result, TTL =
  `min(policy.cache_ttl_sec, CHINA_ACCESS_CACHE_TTL_SEC)`. It is internal only. Media URLs are signed and short-lived, so the TTL stays
  at 30 min or less.
- `china:dedupe:{platform}:{operation}:{hash}`: holds a random owner token. `SET NX EX CHINA_ACCESS_DEDUPE_TTL_SEC`,
  released by compare-and-delete.
- `china:paid:{provider}:{hash}`: the once-per-paid-provider-per-URL marker (`SET NX EX` dedupe TTL). It is **not**
  released on failure. That is what stops a retry storm from billing again.

## 9. Observability
- `china:attempts:{platform}`: list of the last 200 scrubbed attempt records (plan §13.4 fields, url hash only), TTL 7 d.
- `china:stats:{day}`: hash with `{platform}|{provider}|ok` and `{platform}|{provider}|err|{category}`, TTL 35 d.
- Logger `app.china_access`: one JSON line per attempt (plan §18 fields), passed through `errors.redact`.
- The existing outcome store keeps counting per job, unchanged (`routes._track_download`, the worker).

## 10. Redaction (`errors.redact`)
It strips every URL query string (`?…` → `?<redacted>`), applies `structured_log.redact_secrets`, and replaces the
literal values of `CHINA_ACCESS_APIFY_TOKEN` / `APIFY_TOKEN` and of the user's cookie file contents (never read into
the layer) with `<redacted>`. It runs on every `internal_detail`, log line and admin response field that can hold
upstream text.
