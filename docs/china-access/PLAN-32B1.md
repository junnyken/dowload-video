# Phase 32B-1 — China Platform Access Layer Foundation

## Status and intent

Build a reusable, production-safe access layer for VidGrab's China-origin platform group. This is **not** a Douyin-only Apify integration.

VidGrab currently or planned supports multiple China-origin platforms, including:

- Douyin
- Kuaishou
- Xiaohongshu (XHS / RedNote)
- Bilibili
- Weibo (future candidate; do not implement until confirmed)
- Lemon8 (existing related platform; treat according to its real capability)

The objective is to stop adding proxy, cookie, scraper, actor, fallback, and cost logic independently inside every platform extractor. Instead, build a shared policy, routing, budget, health, and observability layer that routes each platform request to the best available provider.

## Important business and technical rules

1. **Do not build a Douyin-only implementation.** Build reusable abstractions first.
2. **Apify is a provider candidate, not a universal implementation.** It may be used for a platform only after benchmark evidence supports it.
3. **Native extraction is preferred when healthy and cost-free.** Managed actors/APIs are fallbacks for anti-bot/signature failures, not the default for every request.
4. **Do not buy or use leaked/marketplace cookies.** They create security, account-abuse, and legal risk.
5. **A proxy changes network identity; it does not guarantee anti-bot signature or browser-challenge success.** Never treat a China/HK proxy as a universal fix.
6. **No unbounded retry loops.** A failed provider is attempted at most once per job unless an explicit bounded retry policy proves otherwise.
7. **No paid provider call without budget checks first.** Per-user/IP, per-platform, provider-wide daily, and provider-wide monthly guardrails must be checked before dispatch.
8. **All new functionality defaults OFF behind feature flags.** A platform can be paused or reverted without touching unrelated download code.
9. **Do not alter existing download behaviour while introducing this foundation.** Existing working native flows continue to run unchanged until an explicit policy is enabled.
10. **Do not claim a provider/platform works until it has passed the benchmark defined below.**
11. UI changes require BA review. Do not introduce i18n; current project strings are written inline in JSX.
12. The service is currently free and the pricing page is hidden. Do not add payment flows in this phase.

---

# 1. Current problem

Several China-origin platforms have different failure modes:

| Platform | Likely failure category | What proxy can help with | What proxy cannot reliably fix |
|---|---|---|---|
| Douyin | Dynamic signature, browser verification, anti-bot, sometimes region | Some geo/IP restrictions | Signature generation, browser verification, invalid session context |
| Kuaishou | Dynamic API/browser context, anti-bot | Regional response differences | Dynamic signatures, private/login-only content |
| Xiaohongshu | Public parsing plus profile/batch cookie/session needs | Regional access and some anti-bot pressure | Valid login session and fingerprint consistency |
| Bilibili | Mostly native extraction; some geo/licensing blocks | Mainland China/HK geo-locked catalog access | Paid/member-only or account-entitled content |
| Weibo | To be researched after owner approval | Potential regional/public access | Login/session/anti-bot restrictions |

A one-off approach creates these failures:

- Douyin uses a paid actor with no cost cap.
- Kuaishou gets a different proxy implementation.
- Xiaohongshu uses an incompatible cookie abstraction.
- Bilibili spends paid provider budget despite native extraction working.
- Admin cannot tell which provider failed or where money was spent.
- A refactor breaks imports and scheduled alerts, while production keeps silently failing.

Phase 32B-1 solves the shared infrastructure problem before enabling a large number of paid-provider calls.

---

# 2. Scope

## In scope

1. A shared China Platform Access Layer.
2. Platform capability registry and policy model.
3. Provider abstraction: native extractor, managed actor/API, cookie-session, direct/proxy metadata route.
4. Provider router with native-first and policy-controlled fallbacks.
5. Normalized media result and normalized failure model.
6. Budget, quota, caching, deduplication, and kill switches.
7. Probe, benchmark, health, and admin observability foundations.
8. A benchmark harness for Douyin, Kuaishou, Xiaohongshu, and Bilibili.
9. Shadow mode, canary rollout, and rollback controls.
10. Scaffold adapters for the listed platforms, but only enable those proven by benchmark.

## Explicitly out of scope

1. Do not implement all platform extractors from scratch in one change.
2. Do not promise support for private, login-gated, paid, member-only, DRM-protected, or access-controlled content.
3. Do not buy cookies, scrape accounts, or implement credential harvesting.
4. Do not route video bytes through rotating residential proxy by default.
5. Do not implement new payment/billing UI.
6. Do not build a Windows client in this phase; Phase 32C covers that.
7. Do not add Suno here; Suno belongs in a separate onboarding phase after this common layer exists.
8. Do not create a new generic “bypass” subsystem. Use bounded provider abstractions and explicit policy.

---

# 3. Target architecture

```text
Incoming URL
   |
   v
URL normalization / resolve-input
   |
   v
China Platform Access Layer
   |
   +-- Platform Capability Registry
   +-- Policy Resolver
   +-- Provider Router
   +-- Provider Health Registry
   +-- Budget + Quota Guard
   +-- Result Cache + Request Deduplication
   +-- Probe + Benchmark Recorder
   |
   v
Selected Provider
   |
   +-- Native extractor
   +-- Managed provider adapter (Apify or future provider)
   +-- Cookie-session adapter (only owner-managed lawful sessions)
   +-- Regional metadata proxy profile
   |
   v
Normalized Media Result / Normalized Provider Error
   |
   v
Existing VidGrab download, container preview, batch, or job pipeline
```

The access layer decides **how to resolve metadata and obtain usable output**, but it must not rewrite the existing download engine. It plugs into it.

## Design principle

Separate four concerns:

1. **Platform adapter** — knows URLs and platform-specific response parsing.
2. **Provider adapter** — knows how to call native code, an actor, a managed API, or a session flow.
3. **Policy/router** — chooses provider order, only after guard checks.
4. **Operations layer** — costs, health, probes, cache, audit records, kill switches.

Do not mix all four concerns in a single `douyin.py` file.

---

# 4. Required module structure

Verify current repository paths first and adapt names if needed. Preserve existing imports until migrations are complete.

```text
backend/app/services/china_platforms/
├── __init__.py
├── registry.py
├── policy.py
├── provider_router.py
├── normalized_models.py
├── budget_guard.py
├── request_cache.py
├── provider_health.py
├── probes.py
├── benchmark_runner.py
├── errors.py
├── providers/
│   ├── base.py
│   ├── native_provider.py
│   ├── managed_actor_provider.py
│   ├── apify_provider.py
│   ├── cookie_session_provider.py
│   └── metadata_proxy_provider.py
└── adapters/
    ├── base.py
    ├── douyin.py
    ├── kuaishou.py
    ├── xiaohongshu.py
    ├── bilibili.py
    ├── lemon8.py
    └── weibo.py                # scaffold only until owner approves

backend/app/api/admin/china_platforms.py
backend/app/schemas/china_platforms.py
backend/app/tasks/china_platform_tasks.py
backend/app/core/china_platform_flags.py
backend/app/core/china_platform_settings.py
```

Keep direct adapters to existing extractors as compatibility wrappers. Do not duplicate mature extractor logic just to fit a new folder layout.

---

# 5. Normalized contracts

## 5.1 Request model

```python
class ChinaResolveRequest(BaseModel):
    url: HttpUrl
    operation: Literal[
        "single_media",
        "container_discovery",
        "container_expand",
        "metadata_only",
    ] = "single_media"
    user_id: str | None = None
    anonymous_key: str | None = None
    requested_quality: str | None = None
    request_id: str
```

## 5.2 Normalized media result

```python
class NormalizedMediaFormat(BaseModel):
    format_id: str
    ext: str
    label: str
    height: int | None = None
    width: int | None = None
    codec: str | None = None
    has_audio: bool = False
    has_video: bool = False
    estimated_size_bytes: int | None = None
    requires_merge: bool = False
    source_url: str | None = None
    expires_at: datetime | None = None

class NormalizedMediaResult(BaseModel):
    platform: str
    canonical_url: str
    media_id: str | None = None
    title: str
    uploader: str | None = None
    thumbnail_url: str | None = None
    duration_sec: float | None = None
    formats: list[NormalizedMediaFormat] = []
    subtitles: list[dict] = []
    container: dict | None = None
    provider_name: str
    provider_mode: Literal["native", "managed", "cookie_session", "proxy_metadata"]
    watermark_state: Literal["watermark_free", "watermarked", "unknown"] = "unknown"
    resolution_time_ms: int
    cache_hit: bool = False
```

## 5.3 Normalized provider failure

```python
class ProviderFailure(BaseModel):
    platform: str
    provider_name: str
    category: Literal[
        "unsupported_url",
        "private_or_login_required",
        "geo_restricted",
        "signature_or_verification_failed",
        "cookie_required",
        "cookie_invalid",
        "upstream_rate_limited",
        "provider_timeout",
        "provider_unavailable",
        "parse_failed",
        "budget_exceeded",
        "platform_disabled",
        "unknown",
    ]
    retryable: bool
    user_message: str
    internal_detail: str | None = None
```

Never expose provider tokens, raw cookies, proxy URLs, or upstream sensitive response data in `user_message`.

---

# 6. Provider interface

```python
class ChinaPlatformProvider(Protocol):
    name: str
    mode: Literal["native", "managed", "cookie_session", "proxy_metadata"]

    async def can_handle(
        self,
        request: ChinaResolveRequest,
        policy: "PlatformPolicy",
    ) -> bool: ...

    async def resolve(
        self,
        request: ChinaResolveRequest,
        context: "ProviderContext",
    ) -> NormalizedMediaResult: ...

    async def estimate_cost_usd(
        self,
        request: ChinaResolveRequest,
    ) -> Decimal: ...

    async def health(self) -> "ProviderHealthSnapshot": ...
```

Provider rules:

- Native provider cost is normally zero, but may record proxy metadata usage separately.
- Managed provider must return an estimated cost before dispatch and actual cost after completion when available.
- Cookie-session provider accepts only internal cookie IDs, never raw cookie strings from frontend requests.
- Metadata proxy provider may be used for metadata requests only unless a platform policy explicitly permits bytes. Default is metadata-only.

---

# 7. Platform policy registry

This is a **policy skeleton**, not a declaration that each route currently works. All settings must be adjustable via config and runtime override.

```python
CHINA_PLATFORM_POLICIES: dict[str, PlatformPolicy] = {
    "douyin": PlatformPolicy(
        platform="douyin",
        enabled=False,
        supported_operations={"single_media", "container_discovery"},
        provider_order={
            "single_media": ["native_douyin", "apify_douyin"],
            "container_discovery": ["native_douyin", "apify_douyin_analytics"],
        },
        metadata_proxy_profile="china_or_hk_optional",
        requires_cookie_for={"container_discovery": "conditional"},
        managed_provider_allowed=True,
        cache_ttl_sec=1800,
        max_container_items=50,
        delivery_mode="server_proxy_default",
    ),
    "kuaishou": PlatformPolicy(
        platform="kuaishou",
        enabled=False,
        supported_operations={"single_media", "container_discovery"},
        provider_order={
            "single_media": ["native_kuaishou", "managed_kuaishou"],
            "container_discovery": ["managed_kuaishou"],
        },
        metadata_proxy_profile="china_or_hk_optional",
        requires_cookie_for={"container_discovery": "conditional"},
        managed_provider_allowed=True,
        cache_ttl_sec=1800,
        max_container_items=30,
        delivery_mode="server_proxy_default",
    ),
    "xiaohongshu": PlatformPolicy(
        platform="xiaohongshu",
        enabled=False,
        supported_operations={"single_media", "container_discovery"},
        provider_order={
            "single_media": ["native_xiaohongshu"],
            "container_discovery": ["owner_managed_xiaohongshu_session", "managed_xiaohongshu"],
        },
        metadata_proxy_profile="china_or_hk_optional",
        requires_cookie_for={"container_discovery": True},
        managed_provider_allowed=True,
        cache_ttl_sec=900,
        max_container_items=20,
        delivery_mode="server_proxy_default",
    ),
    "bilibili": PlatformPolicy(
        platform="bilibili",
        enabled=False,
        supported_operations={"single_media", "container_discovery", "container_expand"},
        provider_order={
            "single_media": ["native_bilibili"],
            "container_discovery": ["native_bilibili"],
            "container_expand": ["native_bilibili"],
        },
        metadata_proxy_profile="direct_then_china_or_hk_for_geo_locked",
        requires_cookie_for={"single_media": "conditional"},
        managed_provider_allowed=False,
        cache_ttl_sec=1800,
        max_container_items=100,
        delivery_mode="server_proxy_default",
    ),
}
```

Requirements:

1. Policy must define operations, provider order, budget class, proxy profile, cache TTL, batch cap, and kill switch.
2. The router does not call a provider not explicitly allowed by policy.
3. Default policy is disabled.
4. Runtime override may reduce capacity or pause a platform, but may never expand privileges beyond deployment config without an audit event.
5. Do not treat `managed_provider_allowed=True` as “call managed provider by default.” Native is still first unless a benchmark promotes managed provider.

---

# 8. Router and fallback algorithm

## 8.1 Required order of checks

For every request:

```text
1. Normalize URL and identify platform
2. Confirm platform is in China-platform registry
3. Master feature flag check
4. Per-platform enabled/kill-switch check
5. User/IP quota check
6. Platform call and spend budget check
7. Provider global daily/monthly budget check
8. Cache lookup / same URL deduplication check
9. Read current health state and policy provider order
10. Try providers in allowed order, once each
11. Normalize success or normalize aggregated failure
12. Store audit, metrics, cache, and cost record
```

## 8.2 Router pseudocode

```python
async def resolve_with_china_access_layer(request: ChinaResolveRequest):
    platform = identify_platform(request.url)
    policy = registry.get(platform)

    guards.assert_master_enabled()
    guards.assert_platform_enabled(policy)
    guards.assert_user_quota(request, policy)
    guards.assert_platform_budget(platform)
    guards.assert_provider_budget(policy)

    cached = cache.get(platform, canonicalize(request.url), request.operation)
    if cached:
        return cached.with_cache_hit(True)

    request_lock = dedupe.acquire(platform, canonicalize(request.url))
    async with request_lock:
        cached = cache.get(...)
        if cached:
            return cached.with_cache_hit(True)

        failures = []
        for provider_name in router.eligible_provider_order(policy, request):
            provider = providers.get(provider_name)
            if not provider_health.is_eligible(provider_name, platform):
                continue

            estimated = await provider.estimate_cost_usd(request)
            guards.assert_cost_before_dispatch(provider_name, platform, estimated)

            try:
                result = await provider.resolve(request, context)
                audit.record_success(...)
                budget.record_actual_cost(...)
                cache.set(result, ttl=policy.cache_ttl_sec)
                return result
            except ProviderFailureException as exc:
                failures.append(exc.normalized)
                audit.record_failure(...)
                provider_health.record_failure(...)
                # Exactly one try for this provider in this job.

        raise aggregate_failures(platform, failures)
```

Requirements:

- No retry loop may call the same paid provider twice for the same canonical URL within the dedupe TTL.
- A fallback may run only when the previous failure category is eligible for fallback. For example, `private_or_login_required` should not trigger an expensive managed retry unless policy says it can solve that category.
- `budget_exceeded` and `platform_disabled` must fail immediately; no fallback to paid providers.

---

# 9. Budget, quota, cache, and deduplication

## 9.1 Multi-level guardrails

Every paid/managed provider path must pass all four levels:

| Level | Purpose | Example |
|---|---|---|
| User/IP | Stop abuse by a single requester | 5 China-platform resolutions/day for anonymous user |
| Platform | Cap one platform | Douyin managed calls max 50/day |
| Provider | Limit one vendor spend | Apify daily 1 USD, monthly 5 USD during test |
| Global feature | Emergency shutoff | China-access master kill switch |

## 9.2 Redis key conventions

All keys use explicit TTLs and a separate prefix. Do not create unbounded keys.

```text
china:quota:user:{date_utc}:{user_or_ip}:{platform}
china:platform:calls:{date_utc}:{platform}
china:platform:spend_usd_micros:{date_utc}:{platform}
china:provider:calls:{date_utc}:{provider}
china:provider:spend_usd_micros:{date_utc}:{provider}
china:provider:spend_month_usd_micros:{yyyymm}:{provider}
china:cache:{platform}:{operation}:{canonical_url_hash}
china:dedupe:{platform}:{operation}:{canonical_url_hash}
china:health:{provider}:{platform}
china:killswitch:global
china:killswitch:platform:{platform}
```

## 9.3 Cache and dedupe rules

- Cache public metadata/result only, not raw cookies, auth headers, user-specific session output, or private content.
- Canonicalize URL before hashing: remove tracking query params but retain IDs that change media identity.
- Use 15–30 minutes for volatile social metadata unless platform policy specifies otherwise.
- Use a distributed lock or Redis `SET NX EX` so concurrent identical requests create one managed actor run, not 20.
- Return a clear `already_processing` state to duplicate callers, with the shared job ID when possible.

## 9.4 Conservative initial limits

These are starting values only; make all env-configurable:

```env
CHINA_ACCESS_ENABLED=false
CHINA_ACCESS_GLOBAL_KILL_SWITCH=false
CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT=5
CHINA_ACCESS_FREE_DAILY_RESOLVE_LIMIT=20
CHINA_ACCESS_PROVIDER_DAILY_SPEND_CEILING_USD=1.00
CHINA_ACCESS_PROVIDER_MONTHLY_SPEND_CEILING_USD=5.00
CHINA_ACCESS_MANAGED_TIMEOUT_SEC=90
CHINA_ACCESS_MANAGED_MAX_RETRIES=0
CHINA_ACCESS_CACHE_TTL_SEC=1800
CHINA_ACCESS_DEDUPE_TTL_SEC=1800
```

Provider/platform overrides:

```env
DOUYIN_ENABLED=false
DOUYIN_APIFY_ENABLED=false
DOUYIN_APIFY_SHADOW_MODE=true
DOUYIN_APIFY_DAILY_CALL_LIMIT=50
DOUYIN_APIFY_DAILY_SPEND_CEILING_USD=1.00

KUAISHOU_ENABLED=false
KUAISHOU_MANAGED_ENABLED=false
XIAOHONGSHU_ENABLED=false
BILIBILI_CHINA_ACCESS_ENABLED=false
```

Do not put `APIFY_TOKEN`, proxy credentials, or cookie values in source code, frontend code, logs, exceptions, admin responses, or chat messages.

---

# 10. Proxy policy

## 10.1 Metadata-only rule

Rotating residential proxy is for metadata resolution only by default:

- URL redirect resolution
- title, thumbnail, duration
- format discovery
- subtitle list discovery
- container/profile discovery
- limited structured page data

It is not for:

- downloading full video/audio bytes
- HLS/DASH segment download
- FFmpeg merge
- bulk ZIP generation
- repeated retry traffic

## 10.2 Proxy profiles

```python
ProxyProfile = Literal[
    "direct",
    "residential_global_metadata",
    "residential_cn_metadata",
    "residential_hk_metadata",
    "fallback_metadata",
]
```

Rules:

1. The frontend never chooses a proxy country/profile.
2. Provider context uses platform policy only.
3. Metadata proxy use is metered and recorded separately from managed provider cost.
4. A China/HK proxy is tried only after benchmark evidence proves it helps a specific platform/failure category.
5. A proxy does not replace cookie/session or signature requirements.

---

# 11. Cookie-session policy

1. Only owner-managed, lawful sessions added through the existing protected admin cookie pool are allowed.
2. Never accept raw cookie text from regular frontend users.
3. Never buy, import, or test marketplace/leaked cookies.
4. Session records must have state: `healthy`, `warming`, `cooling`, `soft_blocked`, `hard_blocked`, `expired`, `invalid`, `manual_disabled`.
5. Session IDs may appear in internal audit records, but raw values must never be returned through any API or logs.
6. Xiaohongshu profile/container work must return a clear `cookie_required` / `private_or_login_required` outcome when no valid owner-managed session exists. Do not burn managed-provider budget repeatedly on a known session-required failure.

---

# 12. Managed provider / Apify adapter

## 12.1 Requirements

1. Implement generic `ManagedActorProvider`, then a thin `ApifyProvider` adapter. Do not bind router logic directly to one actor ID.
2. Actor ID, input mapping, output mapping, timeout, max items, and estimated cost live in deployment config/policy, not hardcoded in generic router code.
3. Actor result parsing must validate title, media URL, duration, and output status before treating a run as success.
4. Record provider run ID, platform, canonical URL hash, elapsed time, estimated cost, actual cost if API provides it, and normalized result status.
5. Never log the full output if it contains signed URLs or sensitive headers; store scrubbed diagnostics.
6. Start in shadow mode: call provider for benchmark only, do not return its result to normal users.

## 12.2 Douyin candidate integration

Existing project notes indicate code may already reference:

- `natanielsantos/douyin-scraper` for video
- `automation-lab/douyin-analytics-scraper` for channels

Verify these actor IDs, terms, input schemas, output schemas, active maintenance, price, and output rights at implementation time. Do not assume any actor gives no-watermark media.

Required output field:

```python
watermark_state: Literal["watermark_free", "watermarked", "unknown"]
```

Only label output “no watermark” in UI when a provider returns a trustworthy explicit state or a reliable validation procedure confirms it. Otherwise use neutral copy such as “Nguon video tra ve tu nen tang” and show the state as unknown internally.

---

# 13. Benchmark phase (mandatory before production enablement)

## 13.1 Platforms

Run benchmark separately for:

- Douyin
- Kuaishou
- Xiaohongshu
- Bilibili

Use only public, lawful test URLs. Keep test URL IDs in secure configuration or fixture files; do not expose them to users.

## 13.2 Test matrix

For each platform, include if lawful/publicly accessible:

| Case | Required measure |
|---|---|
| Public single media | Metadata success, usable media result |
| Short/share URL | Redirect/normalization success |
| Long media | Timeout and format behaviour |
| Container/profile/channel | Only if platform supports it and test is public |
| Geo-sensitive item | Only where authorized to test; capture geo restriction state |
| Cookie-required item | Do not bypass; verify truthful error result |
| No-watermark claim (if provider makes one) | Visual/manual validation and state classification |

## 13.3 Providers/routes to compare

| Platform | Baseline | Candidate routes |
|---|---|---|
| Douyin | Existing native/iesdouyin/TikWM chain | Native, managed Douyin actor, metadata proxy if policy allows |
| Kuaishou | Native extractor if present | Native, managed candidate only after actor research |
| Xiaohongshu | Native public extraction | Native, owner-managed session for profile/container, managed candidate only after research |
| Bilibili | Native extractor | Native direct, native with regional metadata profile only for authorized geo test |

## 13.4 Required measurements

For every attempt record:

```text
platform
operation
provider
route_mode
request_id
canonical_url_hash
outcome (success/failure)
normalized_failure_category
latency_ms
metadata_success
usable_media_url
media_url_expiry_if_known
watermark_state
proxy_bytes_if_used
estimated_cost_usd
actual_cost_usd
retry_count (must be 0 for managed provider in this phase)
```

Calculate:

- success rate
- P50/P95 latency
- cost per successful result
- cost per attempted result
- provider error mix
- proxy byte usage per request
- no-watermark validation rate where applicable

## 13.5 Promotion rule

A provider/platform route can move from shadow to canary only if all are true:

1. At least 20–30 valid attempts per relevant operation, unless platform access is too constrained and owner explicitly accepts less.
2. Success rate is materially better than native baseline or native is proven broken for that operation.
3. Actual cost is inside configured budget.
4. Output is usable and any watermark claim is validated.
5. No secrets or raw sessions appeared in logs.
6. Probe works and platform-specific kill switch works.

## 13.6 Non-promotion rule

Do not promote if output is private/login-only, if actor output does not contain usable media, if unknown watermark state is marketed as watermark-free, if per-success cost is above owner-approved limit, or if the provider creates repeated timeouts.

---

# 14. Probe and health model

## 14.1 Probe schedule

Integrate with existing periodic probe framework. Do not create a second uncontrolled scheduler.

- Probe public stable URLs every 30 minutes or use existing cadence.
- Probe performs metadata resolution only; it must not download full bytes or invoke expensive managed providers by default.
- Managed-provider probe is optional and budgeted, no more than a configured number per day.

## 14.2 Health states

```text
healthy -> constrained -> degraded -> paused -> recovery
```

| State | Entry rule | Router behaviour |
|---|---|---|
| healthy | Recent success and probe success | Normal policy order |
| constrained | Elevated latency/failure but still working | Reduce container caps; avoid nonessential managed calls |
| degraded | Two consecutive probe failures or failure threshold | Native/managed route only if explicitly allowed; UI truthful |
| paused | Manual kill switch or budget ceiling | Immediate `platform_disabled` / `budget_exceeded` |
| recovery | Sustained probe success after degraded | Canary traffic only, then return to healthy |

Do not allow state flapping. Require a cooldown and successive success window before recovery.

---

# 15. Admin observability

Extend existing admin patterns. Do not redesign the dashboard.

## 15.1 New admin page/section: China Platform Access

Display:

| Area | Data |
|---|---|
| Platform overview | current health state, enabled flag, policy profile, active provider order |
| Provider health | success rate, P50/P95 latency, last failure category, last success |
| Cost | daily/provider/platform spend, call count, estimated vs actual cost |
| Proxy | metadata proxy bytes, profile used, proxy failure rate |
| Sessions | aggregate state only: healthy/cooling/blocked counts; never raw cookies |
| Benchmark | sample count, baseline comparison, promotion state |
| Controls | master kill switch, platform kill switch, managed-provider enable/shadow/canary mode |
| Operations | max container item limit, cache TTL, daily caps, current runtime overrides |

## 15.2 Admin actions

Actions require an existing appropriate role (operator/admin/superadmin according to current RBAC) and an audit log entry:

- Pause/re-enable one platform.
- Set managed provider to shadow, canary, or disabled.
- Freeze provider routing to native-only.
- Reset a bad cache entry by canonical URL hash.
- Grant temporary quota only if such an admin pattern already exists; no raw-cookie management changes in this phase.

Every action must record actor, timestamp, prior value, new value, reason, and expiry if applicable.

---

# 16. API contracts

Add admin endpoints only after matching existing admin auth and RBAC patterns.

```text
GET  /api/v1/admin/china-platforms
GET  /api/v1/admin/china-platforms/{platform}
GET  /api/v1/admin/china-platforms/{platform}/benchmark
POST /api/v1/admin/china-platforms/{platform}/mode
POST /api/v1/admin/china-platforms/{platform}/kill-switch
POST /api/v1/admin/china-platforms/{platform}/benchmark/run
GET  /api/v1/admin/china-platforms/costs
```

Public/existing resolver responses may add safe fields only:

```ts
type ChinaPlatformStatus = {
  platform: string;
  health: "healthy" | "constrained" | "degraded" | "paused" | "recovery";
  providerMode?: "native" | "managed" | "cookie_session" | "proxy_metadata";
  userVisibleAvailability: "available" | "limited" | "temporarily_unavailable";
};
```

Never expose exact proxy location, provider token, session/cookie state per account, actor run ID, signed media URL, internal cost values, or policy order to unauthenticated users.

---

# 17. Database and migrations

Use additive migrations only. Never modify existing `download_jobs`, user profile, or core download tables destructively.

Suggested tables (adapt to existing schema naming):

```sql
china_platform_provider_runs
- id
- request_id
- platform
- operation
- provider_name
- provider_mode
- canonical_url_hash
- status
- normalized_failure_category
- estimated_cost_usd
- actual_cost_usd
- proxy_bytes
- latency_ms
- watermark_state
- created_at

china_platform_benchmark_runs
- id
- platform
- provider_name
- route_mode
- test_case
- outcome
- metrics_json
- created_at

china_platform_runtime_overrides
- id
- platform
- key
- value_json
- enabled
- reason
- expires_at
- changed_by
- created_at
```

Retention:

- Keep aggregate cost/health metrics according to existing analytics retention.
- Delete raw or sensitive diagnostic payloads quickly.
- Store only URL hash in routine provider run records unless retaining canonical URL is necessary and allowed by the current privacy policy.

---

# 18. Logging and security

Structured logs must include:

```text
request_id
platform
operation
provider_name
provider_mode
canonical_url_hash
health_state
cache_hit
budget_check_result
estimated_cost_usd
actual_cost_usd
latency_ms
outcome
normalized_failure_category
```

Never log:

- `APIFY_TOKEN` or any provider API token
- Proxy username/password or full proxy URL
- Raw cookie/session values
- Full signed media URL query strings
- User file paths or private source URLs beyond what existing privacy policy permits

Redaction must happen before exceptions are sent to Sentry or admin diagnostics.

---

# 19. Feature flags and configuration

All values must be environment-driven with safe defaults.

```env
# Master
CHINA_ACCESS_ENABLED=false
CHINA_ACCESS_GLOBAL_KILL_SWITCH=false

# Guardrails
CHINA_ACCESS_ANON_DAILY_RESOLVE_LIMIT=5
CHINA_ACCESS_FREE_DAILY_RESOLVE_LIMIT=20
CHINA_ACCESS_MANAGED_TIMEOUT_SEC=90
CHINA_ACCESS_MANAGED_MAX_RETRIES=0
CHINA_ACCESS_CACHE_TTL_SEC=1800
CHINA_ACCESS_DEDUPE_TTL_SEC=1800

# Provider budgets
APIFY_ENABLED=false
APIFY_TOKEN=
APIFY_DAILY_SPEND_CEILING_USD=1.00
APIFY_MONTHLY_SPEND_CEILING_USD=5.00
APIFY_DAILY_CALL_LIMIT=50

# Platforms
DOUYIN_ENABLED=false
DOUYIN_APIFY_ENABLED=false
DOUYIN_APIFY_SHADOW_MODE=true
KUAISHOU_ENABLED=false
KUAISHOU_MANAGED_ENABLED=false
XIAOHONGSHU_ENABLED=false
BILIBILI_CHINA_ACCESS_ENABLED=false
WEIBO_ENABLED=false

# Proxy profiles (metadata only; credentials stored securely)
CHINA_METADATA_PROXY_ENABLED=false
CHINA_METADATA_PROXY_PROFILE=
```

The coding agent must not ask users to paste secrets into source files. It must document where to set them in Vibe Host environment variables.

---

# 20. Rollout plan

## Stage 0 — Foundation, no behaviour change

- Add contracts, registry, router, guardrails, normalized errors, metrics, database migrations, and admin read-only views.
- All flags OFF.
- Existing extractors work as before.

**Exit:** unit tests pass, migrations are additive, and no existing platform behaviour changes.

## Stage 1 — Native shadow instrumentation

- Enable `CHINA_ACCESS_ENABLED` only in shadow mode for native results.
- Router records what it would choose but continues existing path.
- Compare normalized results with current extractor output.

**Exit:** logs and metrics match observed current behaviour; no new errors in working platforms.

## Stage 2 — Benchmark

- Run the mandatory 20–30 URL benchmark per platform where lawful/publicly available.
- Managed provider starts in shadow mode only.
- Cost ceiling starts at 1 USD/day and 5 USD/month for test.

**Exit:** benchmark report completed; promotion rules satisfied for at least one route.

## Stage 3 — Canary

- Enable a single approved route, likely Douyin single-video managed fallback if benchmark proves it.
- Canary 5–10% of eligible requests, or admin/test accounts first.
- Keep native-first policy unless native is objectively broken for that operation.

**Exit:** success rate, latency, cost, and output quality remain within owner-approved bounds for seven days.

## Stage 4 — Controlled production

- Enable approved routes per platform.
- Enable profile/container only after single media is stable.
- Expand only one platform or operation at a time.

## Rollback

At any stage:

1. Set global kill switch, provider kill switch, or platform kill switch.
2. Router immediately stops new managed calls.
3. Existing core download routes remain untouched.
4. Preserve aggregate audit records for incident review.
5. Do not delete data or change existing extractor code during emergency rollback.

---

# 21. Acceptance criteria

The phase is complete only when all criteria below are met.

1. A reusable China Platform Access Layer exists; no Douyin-only routing is hardcoded into generic download logic.
2. Platform policies exist for Douyin, Kuaishou, Xiaohongshu, and Bilibili, but are default-disabled.
3. Every managed provider call is blocked unless user/IP quota, platform budget, provider daily/monthly budget, platform flag, and kill switch checks pass first.
4. Identical concurrent canonical URLs create one managed provider run via deduplication, not multiple billable runs.
5. The same managed provider is never retried more than once for the same URL/job in this phase.
6. Native extraction remains the default first route unless a benchmark-based runtime policy explicitly promotes another provider.
7. Proxy routes are metadata-only by default; full byte download through rotating residential proxy is not enabled.
8. Raw cookies, provider tokens, proxy credentials, and signed URLs never appear in public APIs, logs, Sentry payloads, or admin UI.
9. A benchmark harness records success, latency, failure category, cost, proxy bytes, and watermark state for each test run.
10. A provider/platform cannot move from shadow to canary until the stated promotion rule is met.
11. Admin can view platform health, provider performance, cost, benchmark results, and toggle kill switches with audit logging.
12. Disabling a platform or all China access requires no code deployment and does not affect TikTok, YouTube, Bilibili native flow, or other unrelated platforms.
13. Existing working download routes have regression tests and pass after rollout.
14. All database migrations are additive and do not alter existing download job schema destructively.
15. No private/login-only content is falsely presented as publicly downloadable.
16. Any “no watermark” claim is made only with validated provider output; otherwise state is `unknown`.

---

# 22. Required deliverables from the coding agent

Return work in this exact order:

1. Repository audit: actual current extractor paths, existing Apify integration, current probe architecture, current cookie manager APIs, current admin route patterns, and any conflicts with this plan.
2. Architecture design: registry, policy, provider interface, router, normalized contracts, budget model, health model.
3. File-by-file impact list marking new vs modified files.
4. Migration plan and rollback plan.
5. Benchmark plan with URL fixture requirements and a CSV/JSON result format.
6. Feature-flag and Vibe Host environment-variable checklist. Never print real secrets.
7. Admin UI mapping: existing component/route -> new fields/behaviour.
8. Test plan: unit, integration, benchmark, regression, and failure tests.
9. Only then generate scaffold code for:
   - normalized models and errors;
   - base provider protocol;
   - policy registry;
   - guarded provider router;
   - Redis budget/cache/dedupe helper;
   - managed provider/Apify adapter skeleton;
   - Douyin, Kuaishou, Xiaohongshu, Bilibili adapter skeletons;
   - probe registration skeleton;
   - admin read endpoints;
   - migration skeletons;
   - tests for guard order, no duplicate paid calls, kill switch, redaction, and fallback rules.

## Code constraints

- Scaffold only, not a giant rewrite.
- Reuse FastAPI, Celery, Redis, Supabase/PostgreSQL, existing admin patterns, and existing extractor code.
- Do not implement every managed actor at once.
- Do not make paid provider calls in tests; use fixtures/mocks.
- Do not add a new global state library in frontend.
- Keep all code production-like, typed, paste-ready, and conservative.
- Preserve existing public endpoint contracts unless a backward-compatible field addition is necessary.

## Final instruction

Build the foundation that allows VidGrab to support multiple China-origin platforms reliably and cost-safely. The objective is not to force every request through Apify, a proxy, or a cookie. The objective is to select a tested, observable, bounded route per platform and operation, while keeping the rest of VidGrab stable.
