# 07 — Admin UI mapping

Wave 1 decision: **API only, no new admin page.** A section on an existing page is not trivial here. Every candidate page
(`frontend/src/admin/pages/AsrPage.tsx`, `ProbesPage.tsx`, `PlatformsPage.tsx`) would need a new API client file, a
hook, a table and confirm dialogs for kill switches. That is a UI change needing BA review (plan rule 11). The endpoints
below are shaped so a wave-2 section can bind to them directly.

| Plan §15.1 area | Endpoint (all `verify_admin`) | Fields | Future home (wave 2) |
|---|---|---|---|
| Platform overview | `GET /api/v1/admin/china-platforms` | per platform: `routable_in_wave1`, `env_enabled`, `killswitch`, `managed_mode{env,override,effective}`, `provider_order`, `cache_ttl_sec` | new section on `PlatformsPage.tsx` |
| Provider health | `GET …/{platform}` | `providers[].health{state,consecutive_failures,last_failure_category,last_success_ts}`, `success_rate`, `p50/p95_latency_ms` (from the last 200 attempts) | same |
| Cost | `GET …/costs` | per provider: today calls/spend, month spend, ceilings; per platform: calls/spend vs limits; `cost_source` mix | `AsrPage.tsx`-style cost card |
| Proxy | — | wave 1 has no metadata proxy provider; returns `proxy_bytes: 0` | — |
| Sessions | — | aggregate cookie state is already on `CookiesPage.tsx`; cookie-session provider not built | — |
| Benchmark | `GET …/{platform}/benchmark`, `POST …/{platform}/benchmark/run` | last summary (05 JSON `summary` + file names), running flag | `ProbesPage.tsx` |
| Controls | `POST …/{platform}/kill-switch`, `POST …/global/kill-switch`, `POST …/{platform}/mode` | `{on|mode, reason}` → `{prior, new}`; audited | `PlatformActionMenu.tsx` |
| Operations | `POST …/{platform}/cache/reset` `{url_hash, reason}` | deletes one cache entry; audited | same |

Audit: every POST calls `log_admin_action(request, "admin.china_access.<action>", resource_type="china_platform",
resource_id=platform, metadata={prior, new, reason})`. The actor is recorded as `"admin"` (single admin identity, correction #7).
Responses never include tokens, cookies, run IDs' full URLs or signed media URLs. Attempt records carry the URL hash only.
The `reason` field is required (≥ 3 chars) on every POST.
