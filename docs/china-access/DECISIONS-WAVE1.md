# Phase 32B-1 — Wave 1 decisions (owner, 2026-10-06)

Source plan: `PLAN-32B1.md` (owner's document, verbatim). This file records
what the owner decided after review and **overrides the plan where they
differ**. Task: AI Factory #5998, due 2026-10-09.

## Scope: wave 1 only (owner chose "Đợt 1 gọn trước")

Build:
1. Registry + `PlatformPolicy` for douyin, kuaishou, xiaohongshu, bilibili,
   lemon8 (weibo: not at all). All disabled by default.
2. Normalized models + errors (plan §5), provider protocol (§6).
3. Guarded router (§8) with the exact check order, once-per-provider, no
   fallback after `budget_exceeded` / `platform_disabled`, fallback only for
   eligible failure categories.
4. Budget/quota/cache/dedupe on Redis (§9) — reuse the pattern of
   `backend/app/services/asr/budget.py` (micro-USD integer counters, TTL'd
   daily keys) instead of inventing a new one.
5. `ManagedActorProvider` + thin `ApifyProvider`, wired for Douyin single
   media first (reuse `backend/app/services/apify_service.py`, which already
   calls `natanielsantos~douyin-scraper`; verify input/output there).
6. Douyin adapter that wraps the existing native chain
   (`douyin_extractor.py`).
7. Benchmark harness (§13) — CLI/admin-triggered, fixture URLs from config,
   JSON/CSV output, mocks in tests, no paid calls in tests.
8. Admin read endpoints + kill switch / mode endpoints (§16), audited via the
   existing `log_admin_action`. No new admin page in wave 1 (a section on an
   existing page is acceptable only if trivial; otherwise API only).
9. Canary: managed provider route enabled for **admin requests only** first.

Not in wave 1: new DB tables (use Redis + existing outcome store; tables in
wave 2), new admin page, metadata proxy provider (scaffold interface only),
Kuaishou/XHS managed providers, Bilibili routing.

## Corrections to the plan

| # | Plan says | Wave 1 rule |
|---|---|---|
| 1 | §11.2 never accept raw cookie text from frontend users | Applies to the NEW access layer's cookie-session provider only. The existing web feature "Dùng cookie của tôi" (per-request user cookie) stays unchanged. |
| 2 | Native cost is normally zero | The existing Douyin chain calls ScraperAPI (paid). Treat ScraperAPI as a paid provider with its own budget class, or skip it when the access layer is active. |
| 3 | Native first | Douyin native is measured broken (0/1245 on prod, 2026-10-05/06; yt-dlp DouyinIE has "TODO: Run verification challenge code to generate signature cookies"). Policy must allow per-platform order so Douyin can be managed-first once the benchmark confirms; default order stays native-first until then. |
| 4 | §12.1 start in shadow mode on real traffic | Shadow on real traffic pays without helping users. Wave 1: benchmark harness on fixture URLs + canary for admin requests. Keep a shadow flag but default it to benchmark-only. |
| 5 | Bilibili in the router | Registry entry only; NOT routed in wave 1 (native works: probe ok). Plan §21.12 — must not affect Bilibili native flow. |
| 6 | Flags `DOUYIN_ENABLED`, `KUAISHOU_ENABLED`… | `KUAISHOU_ENABLED` already exists (Kuaishou extractor scaffold). Use `CHINA_ACCESS_<PLATFORM>_*` names for everything new, so a default-OFF flag never disables today's Douyin path. |
| 7 | §15.2 operator/admin/superadmin roles | Backend has a single admin session (`verify_admin`). Use it + `log_admin_action`. |
| 8 | Anonymous 5 resolutions/day | Applies to the PAID/managed path only. The general download quota stays as the owner set it (`DAILY_QUOTA_PER_IP=0`, unlimited). |
| 9 | New probe scheduler? | Reuse `backend/app/core/platform_probe.py` and `app/tasks/probe_tasks.py`; managed probes off by default. |

## Already in production that wave 1 must build on (not duplicate)

- `failure_classifier.py`: cookie-gated messages → USER_ACTION, max 3
  attempts/job, one outcome per job (commit 7e6856a).
- `error_codes.py`: `cookie_required` exists.
- `douyin_extractor.douyin_server_access_available()` and the 422
  `cookie_required` gates in routes/container/channel scrape.
- `download_outcomes.py`: per-platform + per-domain ("other") hourly counts.
- `cookie_pool.py` states: healthy / cooldown / soft_blocked / hard_blocked /
  expired / disabled; `cookie_probe.py` live retest (no Douyin probe yet).
- Production env has NO `APIFY_TOKEN` yet (owner to add, separate Apify
  account with a spending limit). Apify price read 2026-10-06 on
  apify.com/natanielsantos/douyin-scraper: $7 / 1000 posts (Free plan),
  $5 Starter, $4 Scale; Free plan $5 credit/month; 99.6% runs succeeded.
