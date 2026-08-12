# VidGrab Admin Dashboard — Control Plane Design Spec

**Version:** 1.0 | **Date:** 2026-06-30
**Status:** Design spec — ready for implementation

> **Scope:** Tất cả thiết kế trace trực tiếp về operational pain points được liệt kê trong brief.
> Không có feature nào được thêm vì "nice to have" nếu không map về một pain point thực tế.

---

## Baseline — Những gì đã có (không build lại)

Trước khi thiết kế, điều quan trọng là biết rõ cái gì **đã tồn tại** để tránh build lại:

| Đã có | File | Ghi chú |
|-------|------|---------|
| Admin auth (Bearer + Redis session, 8h TTL) | `admin.py` | Cần extend thêm RBAC role |
| IP allowlist + brute-force lockout (3 fails → 15m) | `admin.py` | Giữ nguyên |
| Login endpoint | `POST /admin/login` | Giữ nguyên |
| Cookie pool: status/list/add/remove/expiry | `admin.py` | Cần thêm health score |
| Proxy pool: status/add/remove | `admin.py` | Cần extend |
| Platform circuit breaker (Redis) | `platform_circuit.py` | Expose qua API mới |
| Metrics: job_metrics, queue_depths, stale_jobs | `core/metrics.py` | Expose qua API mới |
| Audit log infrastructure | `audit_api.py` + `log_admin_action()` | Extend với admin actions |
| System health endpoint | `GET /admin/system-health` | Extend |
| Platform analytics (basic) | `GET /admin/platform-analytics` | Extend |
| AdminDashboard.jsx, QueueHealthPanel, AnomalyPanel | `frontend/Admin/` | Refactor vào layout mới |
| OpsPanel.jsx, YouTubeGatePanel.jsx | `frontend/Admin/` | Absorb vào design mới |

**Nguyên tắc:** Extend trước, thêm mới sau.

---

## A. Page Layout & Navigation Structure

### A.1 Layout chính

```
┌─────────────────────────────────────────────────────────────────┐
│ HEADER: [VidGrab Admin] ····· [🔔 Alerts: 2] [trieunt ▾] [⬤]  │
├──────────┬──────────────────────────────────────────────────────┤
│          │                                                        │
│ SIDEBAR  │              MAIN CONTENT                             │
│  240px   │              (scrollable)                             │
│          │                                                        │
│ Nav      │                                                        │
│ items    │                                                        │
│ (icon +  │                                                        │
│  label)  │                                                        │
│          │                                                        │
│ Collapse │                                                        │
│ btn ◀    │                                                        │
└──────────┴──────────────────────────────────────────────────────┘
```

**Collapsed sidebar (tablet 768–1024px):** icon-only (60px), tooltip on hover.
**Mobile (<768px):** Bottom tab bar (5 tabs + "More" drawer).

### A.2 Sidebar Navigation

```
━━━━━━━━━━━━━━━
🟢 HEALTHY          ← System status chip, tự động update
━━━━━━━━━━━━━━━

  📊 Overview         /vid-admin/overview
  ─────── Monitor ────────────────────────
  🔌 Platforms        /vid-admin/platforms
  🍪 Cookies          /vid-admin/cookies
  🌐 Proxy            /vid-admin/proxy
  ⚙️  Queue           /vid-admin/queue
  ─────── Investigate ────────────────────
  🔍 Job Inspector    /vid-admin/jobs
  📈 Analytics        /vid-admin/analytics
  ─────── Manage ─────────────────────────
  👤 Users            /vid-admin/users
  ─────── System ──────────────────────── (admin+ only)
  🛠  Config          /vid-admin/config
  🔑 Access           /vid-admin/access
  📋 Audit Log        /vid-admin/audit
  ─────── ───────────────────────────────
  [▶ Logout]
```

### A.3 Route Structure

```
/vid-admin                          → redirect → /vid-admin/overview
/vid-admin/overview                 — Command Center
/vid-admin/platforms                — Platform Health Table
/vid-admin/platforms/:platform      — Platform Detail drawer (modal-style)
/vid-admin/cookies                  — Cookie Pool
/vid-admin/proxy                    — Proxy Health
/vid-admin/queue                    — Queue & Workers
/vid-admin/jobs                     — Job Inspector
/vid-admin/jobs/:job_id             — Job Detail (phase trace)
/vid-admin/analytics                — Analytics
/vid-admin/users                    — User Management
/vid-admin/config                   — System Config [admin+]
/vid-admin/access                   — Admin User RBAC [superadmin]
/vid-admin/audit                    — Audit Log [admin+]
/vid-admin/login                    — Login page (public)
```

### A.4 Role Visibility Matrix

| Route | viewer | operator | admin | superadmin |
|-------|:------:|:--------:|:-----:|:----------:|
| Overview | ✅ | ✅ | ✅ | ✅ |
| Platforms | ✅ read | ✅ + actions | ✅ + config | ✅ |
| Cookies | ✅ read | ✅ + rotate/test | ✅ + add/revoke | ✅ |
| Proxy | ✅ read | ✅ + test | ✅ + toggle/policy | ✅ |
| Queue | ✅ read | ✅ + retry/cancel | ✅ | ✅ |
| Job Inspector | ✅ read | ✅ + retry | ✅ | ✅ |
| Analytics | ✅ | ✅ | ✅ | ✅ |
| Users | ❌ | ✅ read | ✅ + edit | ✅ + ban/impersonate |
| Config | ❌ | ❌ | ✅ limited | ✅ full |
| Access | ❌ | ❌ | ❌ | ✅ |
| Audit Log | ❌ | ✅ read own | ✅ read all | ✅ |

### A.5 Bottom Tab Bar (Mobile)

```
[📊 Overview] [🔌 Platforms] [🍪 Cookies] [🔍 Jobs] [··· More]
```

"More" drawer opens remaining items.

---

## B. Component Inventory

### B.1 Layout Shell (tất cả đều mới)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `AdminShell` | `layouts/AdminShell.jsx` | Root layout: sidebar + header + outlet | NEW |
| `AdminSidebar` | `layouts/AdminSidebar.jsx` | Nav links, role-gated, collapse | NEW |
| `AdminHeader` | `layouts/AdminHeader.jsx` | Logo, alerts bell, user menu | NEW |
| `AdminMobileNav` | `layouts/AdminMobileNav.jsx` | Bottom tab bar | NEW |
| `AlertBanner` | `components/AlertBanner.jsx` | Dismissible multi-alert strip | NEW |
| `RoleGate` | `components/RoleGate.jsx` | `<RoleGate min="operator">children</RoleGate>` | NEW |
| `useAdminAuth` | `hooks/useAdminAuth.js` | JWT store, role check, auto-refresh | NEW |

### B.2 Overview / Command Center (REUSE + EXTEND)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `OverviewPage` | `pages/Admin/OverviewPage.jsx` | Refactor `AdminDashboard.jsx` | REFACTOR |
| `SystemStatusBadge` | `components/SystemStatusBadge.jsx` | HEALTHY/DEGRADED/CRITICAL pill | NEW |
| `HealthPanelGrid` | `components/HealthPanelGrid.jsx` | 2×2 grid container | NEW |
| `HealthPanel` | `components/HealthPanel.jsx` | Reusable panel: title, metric, status, timestamp | NEW |
| `ActiveAlertsBanner` | `components/ActiveAlertsBanner.jsx` | Collapsible list of active alerts | EXTEND `AnomalyPanel` |
| `QuickStatsRow` | `components/QuickStatsRow.jsx` | 4 number cards: downloads today, success rate, active jobs, quota hits | NEW |

**Props — `HealthPanel`:**
```typescript
{
  title: string                    // "Platform Health"
  status: "ok" | "warn" | "error"  // drives color
  primaryMetric: string            // "18/20 OK"
  secondaryMetric?: string         // "2 circuit OPEN"
  lastUpdated: Date
  href: string                     // link to detail page
  loading?: boolean
}
```

### B.3 Auth (EXTEND existing)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `LoginPage` | `pages/Admin/LoginPage.jsx` | Email + password form (was hardcoded token) | REFACTOR |
| `AccessPage` | `pages/Admin/AccessPage.jsx` | Admin users list + invite + role change | NEW |
| `InviteAdminModal` | `components/InviteAdminModal.jsx` | Email + role selector | NEW |

### B.4 Platform Health (EXTEND `OpsPanel`)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `PlatformsPage` | `pages/Admin/PlatformsPage.jsx` | Replaces `OpsPanel.jsx` logic | REFACTOR |
| `PlatformTable` | `components/PlatformTable.jsx` | Sortable/filterable table, 20+ rows | NEW |
| `PlatformRow` | `components/PlatformRow.jsx` | Single row with inline actions | NEW |
| `CircuitStateChip` | `components/CircuitStateChip.jsx` | CLOSED (green) / OPEN (red) / HALF (amber) | NEW |
| `PlatformDrawer` | `components/PlatformDrawer.jsx` | Slide-in detail panel (right side, 480px) | NEW |
| `PhaseHeatmap` | `components/PhaseHeatmap.jsx` | 5×5 grid: phase × error_type, color by count | NEW |
| `ErrorBreakdownBar` | `components/ErrorBreakdownBar.jsx` | Stacked bar: 403 / 429 / timeout / login_req | NEW |
| `PlatformActionMenu` | `components/PlatformActionMenu.jsx` | Force close / force open / test / view logs | NEW |

**Props — `PlatformRow`:**
```typescript
{
  platform: string
  displayName: string
  icon: string               // emoji or svg path
  status: "ok" | "warn" | "down" | "disabled"
  circuitState: "closed" | "open" | "half" | "exempt"
  lastSuccessAt: Date | null
  failRate1h: number         // 0–1
  cookieRequired: boolean
  proxyRequired: boolean
  onForceClose: () => void   // operator+
  onForceOpen: () => void    // operator+
  onTest: () => void         // operator+
  onViewDetail: () => void
}
```

### B.5 Cookie Pool (EXTEND existing cookie endpoints)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `CookiesPage` | `pages/Admin/CookiesPage.jsx` | Full cookie management page | NEW |
| `CookieTable` | `components/CookieTable.jsx` | Table: all cookies, sortable | NEW |
| `CookieRow` | `components/CookieRow.jsx` | Single cookie row | NEW |
| `CookieHealthBar` | `components/CookieHealthBar.jsx` | Score 0–100 color bar | NEW |
| `CooldownTimer` | `components/CooldownTimer.jsx` | Live countdown for blocked cookies | NEW |
| `AddCookieModal` | `components/AddCookieModal.jsx` | Platform selector + Netscape paste | NEW |
| `RotationPolicyDrawer` | `components/RotationPolicyDrawer.jsx` | Per-platform: max_fail, soft, hard | NEW |

**Props — `CookieRow`:**
```typescript
{
  id: string
  platform: string
  accountLabel: string       // alias set on upload
  status: "active" | "soft_blocked" | "hard_blocked" | "expired"
  healthScore: number        // 0–100
  lastSuccessAt: Date | null
  lastFailAt: Date | null
  failCount: number
  cooldownRemainingMs: number | null
  expiryEstimate: Date | null   // estimated from cookie fields
  activeBatchCount: number
  onRevoke: () => void           // admin+
  onRotateNow: () => void        // operator+
  onTest: () => void             // operator+
}
```

### B.6 Proxy Health (EXTEND `/admin/proxies/status`)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `ProxyPage` | `pages/Admin/ProxyPage.jsx` | Proxy health full page | NEW |
| `ProxySourceCard` | `components/ProxySourceCard.jsx` | Per-source card | NEW |
| `ProxyPolicyMatrix` | `components/ProxyPolicyMatrix.jsx` | platform × phase → source grid (editor) | NEW |
| `ProxyCostTracker` | `components/ProxyCostTracker.jsx` | Daily/monthly cost estimate display | NEW |

### B.7 Queue & Workers (EXTEND `QueueHealthPanel`)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `QueuePage` | `pages/Admin/QueuePage.jsx` | Full queue page | REFACTOR `QueueHealthPanel` |
| `WorkerGrid` | `components/WorkerGrid.jsx` | 8 worker tiles (busy/idle/error) | NEW |
| `WorkerTile` | `components/WorkerTile.jsx` | Single worker: ID, status, current job | NEW |
| `QueueDepthBar` | `components/QueueDepthBar.jsx` | pending/processing/success/failed counts | EXTEND existing |
| `ActiveJobsList` | `components/ActiveJobsList.jsx` | Real-time polling, per-job phase + elapsed | EXTEND |
| `CeleryTaskTable` | `components/CeleryTaskTable.jsx` | 7 periodic tasks: last/next/status | NEW |
| `DeadLetterPanel` | `components/DeadLetterPanel.jsx` | Stuck jobs >10min, retry/cancel | NEW |

### B.8 Job Inspector (MOSTLY NEW)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `JobsPage` | `pages/Admin/JobsPage.jsx` | Search + list | NEW |
| `JobSearch` | `components/JobSearch.jsx` | Filters: job_id, batch_id, url, platform, ip | NEW |
| `JobCard` | `components/JobCard.jsx` | Row in search results | NEW |
| `JobDetailPage` | `pages/Admin/JobDetailPage.jsx` | Full phase trace view at `/vid-admin/jobs/:id` | NEW |
| `PhaseTimeline` | `components/PhaseTimeline.jsx` | Visual vertical timeline of phases | NEW |
| `PhaseStep` | `components/PhaseStep.jsx` | Single phase: timestamp, duration, proxy, cookie, result | NEW |
| `ErrorTrace` | `components/ErrorTrace.jsx` | Collapsible raw error + stack | NEW |
| `BatchGrid` | `components/BatchGrid.jsx` | Item grid for batch jobs | NEW |
| `JobActions` | `components/JobActions.jsx` | Retry from phase / retry full / cancel | NEW |

**Props — `PhaseStep`:**
```typescript
{
  phase: "queued"|"resolve"|"metadata"|"auth"|"download"|"postprocess"|"done"|"failed"
  startedAt: Date
  endedAt: Date | null
  durationMs: number | null
  proxySource: string | null     // "proxying_io" | "direct" | "scraperapi"
  cookieId: string | null
  result: "ok" | "error" | "skipped" | "pending"
  errorType: string | null       // "429" | "403" | "timeout" | "login_required"
  errorMsg: string | null        // short
  rawError: object | null        // collapsible JSON
}
```

### B.9 Analytics (EXTEND existing)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `AnalyticsPage` | `pages/Admin/AnalyticsPage.jsx` | Refactor `AnalyticsPage.jsx` | REFACTOR |
| `TimeRangePicker` | `components/TimeRangePicker.jsx` | 1h/6h/24h/7d/30d pills | NEW |
| `DownloadTrendChart` | `components/DownloadTrendChart.jsx` | Line/area: success/failed/partial stacked | NEW |
| `PlatformBarChart` | `components/PlatformBarChart.jsx` | Horizontal bar per platform | NEW |
| `ErrorPieChart` | `components/ErrorPieChart.jsx` | Donut: error type breakdown | NEW |
| `HourlyHeatmap` | `components/HourlyHeatmap.jsx` | 24h × 7d grid, color by volume | NEW |
| `MetricsRow` | `components/MetricsRow.jsx` | 4 KPI cards: success rate, avg time, avg size, quota hits | REUSE `QuickStatsRow` |

> **Charting library:** Use plain SVG + inline calculations (no Recharts, no D3) — TailwindCSS 4 utility classes handle colors. Keeps bundle lean. For heatmap: CSS Grid 24×7 with `bg-opacity-*`.

### B.10 User Management (EXTEND existing)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `UsersPage` | `pages/Admin/UsersPage.jsx` | Refactor `/admin/users` | REFACTOR |
| `UserTable` | `components/UserTable.jsx` | Sortable: email, tier, downloads, quota | NEW |
| `UserActionMenu` | `components/UserActionMenu.jsx` | Upgrade/ban/reset-quota/impersonate | NEW |
| `IpQuotaTable` | `components/IpQuotaTable.jsx` | Top IPs by usage today | NEW |

### B.11 System Config (SUPERADMIN — mostly new)

| Component | File | Mô tả | New/Reuse |
|-----------|------|-------|-----------|
| `ConfigPage` | `pages/Admin/ConfigPage.jsx` | Tabbed config | NEW |
| `EnvVarEditor` | `components/EnvVarEditor.jsx` | Key-value rows, type-aware input, save per-row | NEW |
| `CircuitBreakerConfig` | `components/CircuitBreakerConfig.jsx` | Per-platform: threshold, window, cooldown | NEW |
| `RateLimitConfig` | `components/RateLimitConfig.jsx` | Per-endpoint slider: req/min | NEW |
| `CookieRotationConfig` | `components/CookieRotationConfig.jsx` | Per-platform: max_fail, soft, hard | NEW |

### B.12 Shared Primitives (used across all sections)

| Component | Mô tả |
|-----------|-------|
| `StatusDot` | Colored dot: green/amber/red/gray |
| `RelativeTime` | "2m ago" auto-refresh |
| `CopyButton` | Copy to clipboard with feedback |
| `ConfirmModal` | Generic confirm dialog (action, warning text, confirm button) |
| `EmptyState` | Icon + message for empty tables |
| `LoadingSkeleton` | Skeleton lines for loading states |
| `Pagination` | Page controls for tables |
| `DataTable` | Generic sortable, filterable table wrapper |
| `Badge` | Small label chip (color, text) |
| `Drawer` | Right-side slide-in panel (480px, dark backdrop) |

---

## C. API Contract — New & Extended Endpoints

### Convention

- Tất cả endpoints dưới `/api/v1/admin/`
- Auth: `Authorization: Bearer <session_token>` (Bearer header đã có)
- Role-check: thêm `role_required` param vào `verify_admin()`
- Write endpoints → tự động log vào audit log

### C.1 Auth — Extend Existing

```
POST /admin/login                    ← ĐÃ CÓ, chỉ thêm role trong response
GET  /admin/me                       ← MỚI
POST /admin/logout                   ← MỚI (revoke session)
POST /admin/refresh                  ← MỚI (extend TTL nếu active)
```

**GET `/admin/me` response:**
```json
{
  "user_id": "uuid",
  "email": "trieunt@matbao.com",
  "role": "admin",
  "expires_at": "2026-06-30T18:00:00Z",
  "permissions": ["cookie.add", "circuit.force", "user.edit"]
}
```

**POST `/admin/login` — extend response (thêm `role`):**
```json
{
  "token": "...",
  "expires_at": "2026-06-30T18:00:00Z",
  "role": "admin",          // ← ADD THIS
  "email": "..."            // ← ADD THIS
}
```

### C.2 Admin User Management (Access Page) — TẤT CẢ MỚI

```
GET    /admin/access/users                          ← superadmin
POST   /admin/access/users                          ← superadmin: tạo admin account
PATCH  /admin/access/users/{user_id}                ← superadmin: đổi role/active
DELETE /admin/access/users/{user_id}                ← superadmin: deactivate
POST   /admin/access/users/{user_id}/reset-password ← superadmin
```

**POST `/admin/access/users` body:**
```json
{
  "email": "trieunt@matbao.com",
  "password": "temporary_password",
  "role": "operator"
}
```

### C.3 Platform Health — EXTEND Existing

```
GET  /admin/platforms/health          ← MỚI: all platforms, circuit + fail_rate
GET  /admin/platforms/{p}/detail      ← MỚI: recent jobs + error breakdown + heatmap
POST /admin/platforms/{p}/circuit     ← MỚI: force_close / force_open / reset [operator+]
POST /admin/platforms/{p}/test        ← MỚI: fire test download [operator+]
PATCH /admin/platforms/{p}/config     ← MỚI: enable/disable, quota [admin+]
```

**GET `/admin/platforms/health` response:**
```json
{
  "updated_at": "2026-06-30T10:00:00Z",
  "platforms": [
    {
      "platform": "tiktok",
      "display_name": "TikTok",
      "status": "ok",              // ok | warn | down | disabled
      "circuit_state": "exempt",   // closed | open | half | exempt
      "last_success_at": "2026-06-30T09:58:12Z",
      "fail_rate_1h": 0.02,        // 0–1
      "total_jobs_1h": 143,
      "cookie_required": false,
      "proxy_required": false,
      "enabled": true
    }
  ]
}
```

**GET `/admin/platforms/{platform}/detail` response:**
```json
{
  "platform": "instagram",
  "recent_jobs": [...],           // last 20 download_jobs rows
  "error_breakdown": {
    "403": 12, "429": 5, "timeout": 3, "login_required": 8
  },
  "phase_fail_heatmap": {
    "metadata": {"403": 2, "timeout": 1},
    "auth": {"login_required": 8},
    "bytes": {"403": 10}
  },
  "config": {
    "enabled": true,
    "daily_quota": null,
    "cookie_pool_size": 2,
    "proxy_source": "direct"
  }
}
```

**POST `/admin/platforms/{platform}/circuit` body:**
```json
{
  "action": "force_close" | "force_open" | "reset"
}
```
Response: `{"ok": true, "new_state": "closed", "platform": "instagram"}`

### C.4 Cookie Pool — EXTEND Existing

```
GET  /admin/cookies               ← EXTEND /cookies/status: thêm health_score, expiry_estimate
POST /admin/cookies               ← alias /cookies/add (đổi tên cho REST consistency)
DELETE /admin/cookies/{id}        ← alias /cookies/remove
POST /admin/cookies/{id}/test     ← MỚI: kiểm tra cookie còn valid không [operator+]
POST /admin/cookies/{id}/rotate   ← MỚI: force ngay sang cookie tiếp theo [operator+]
GET  /admin/cookies/rotation-policy ← MỚI: per-platform rotation config
PATCH /admin/cookies/rotation-policy ← MỚI: update policy [admin+]
```

**GET `/admin/cookies` — extended response item:**
```json
{
  "id": "ig_main_2026",
  "platform": "instagram",
  "account_label": "ig_account_1",
  "status": "active",
  "health_score": 72,           // 0–100: 100 - (fail_count×10) + (success_count×1), floor 0
  "last_success_at": "...",
  "last_fail_at": "...",
  "fail_count": 2,
  "success_count_24h": 45,
  "cooldown_remaining_ms": 0,
  "expiry_estimate": "2026-09-15T00:00:00Z",  // parsed từ cookie expires field
  "active_batch_count": 1,
  "blocked_until": null
}
```

**Health score formula (backend compute):**
```python
def compute_health_score(fail_count: int, success_count: int) -> int:
    score = 100 - (fail_count * 10) + min(success_count, 20)
    return max(0, min(100, score))
# < 30 = red, 30–70 = amber, > 70 = green
```

**GET `/admin/cookies/rotation-policy` response:**
```json
{
  "instagram": {"max_fail_count": 5, "cooldown_soft_min": 15, "cooldown_hard_min": 360},
  "twitter": {"max_fail_count": 3, "cooldown_soft_min": 30, "cooldown_hard_min": 720},
  "youtube": {"max_fail_count": 5, "cooldown_soft_min": 15, "cooldown_hard_min": 360}
}
```

### C.5 Proxy Health — EXTEND Existing

```
GET  /admin/proxy/health         ← EXTEND /proxies/status: thêm cost_estimate, bytes_today
GET  /admin/proxy/policy         ← MỚI: platform × phase → source mapping
PATCH /admin/proxy/policy        ← MỚI: update mapping [admin+]
POST /admin/proxy/{source}/test  ← MỚI: test proxy source [operator+]
PATCH /admin/proxy/{source}/toggle ← MỚI: enable/disable source [admin+]
```

**GET `/admin/proxy/health` response:**
```json
{
  "sources": [
    {
      "source": "proxying_io",
      "label": "Proxying.io (Residential)",
      "enabled": true,
      "status": "ok",
      "requests_today": 1240,
      "bytes_today_mb": 180.5,
      "fail_rate_1h": 0.01,
      "cost_estimate_today_usd": 0.27,
      "cost_estimate_month_usd": 8.10,
      "last_good_response_at": "2026-06-30T09:59:45Z",
      "used_for": ["metadata"]   // phases this source is used for
    },
    {
      "source": "direct",
      "label": "Oracle VPS Direct",
      "enabled": true,
      "status": "partial",
      "requests_today": 4500,
      "fail_rate_1h": 0.0,
      "cost_estimate_today_usd": 0,
      "last_good_response_at": "2026-06-30T09:59:58Z",
      "used_for": ["bytes"],
      "notes": "YouTube bytes blocked (AS31898)"
    },
    {
      "source": "scraperapi",
      "label": "ScraperAPI",
      "enabled": true,
      "status": "ok",
      "requests_today": 120,
      "cost_estimate_today_usd": 0.00,  // credits-based
      "credits_remaining": 8340,
      "last_good_response_at": "..."
    },
    {
      "source": "ytdl_proxy",
      "label": "YTDL_PROXY (YouTube Channel)",
      "enabled": false,
      "status": "not_configured",
      "notes": "YTDL_PROXY env not set"
    }
  ]
}
```

**GET `/admin/proxy/policy` response:**
```json
{
  "policy": {
    "youtube":   {"metadata": "proxying_io", "bytes": "disabled"},
    "instagram": {"metadata": "direct",      "bytes": "direct"},
    "tiktok":    {"metadata": "direct",      "bytes": "direct"},
    "douyin":    {"metadata": "scraperapi",  "bytes": "direct"}
  }
}
```

### C.6 Queue & Workers — MOSTLY NEW

```
GET /admin/queue/workers          ← MỚI: per-worker status
GET /admin/queue/depth            ← EXTEND /admin/stats: breakdown by type
GET /admin/queue/active-jobs      ← EXTEND: thêm worker_id, phase, elapsed_ms
GET /admin/queue/dead-letter      ← MỚI: jobs stuck >10min
POST /admin/queue/jobs/{id}/retry ← MỚI [operator+]
POST /admin/queue/jobs/{id}/cancel ← MỚI [operator+]
GET /admin/queue/tasks            ← MỚI: Celery periodic tasks status
```

**GET `/admin/queue/workers` response:**
```json
{
  "workers": [
    {
      "worker_id": "celery@vidgrab-w1",
      "status": "busy",          // busy | idle | error | offline
      "current_job_id": "uuid",
      "current_platform": "instagram",
      "current_phase": "bytes",
      "started_at": "2026-06-30T09:58:00Z",
      "tasks_completed_today": 234
    }
  ],
  "summary": {"busy": 3, "idle": 5, "error": 0, "offline": 0}
}
```

**GET `/admin/queue/dead-letter` response:**
```json
{
  "jobs": [
    {
      "job_id": "uuid",
      "platform": "instagram",
      "url": "https://...",
      "stuck_since": "2026-06-30T09:45:00Z",
      "stuck_minutes": 14,
      "last_phase": "auth",
      "error_type": "login_required"
    }
  ]
}
```

**GET `/admin/queue/tasks` — Celery periodic tasks:**
```json
{
  "tasks": [
    {
      "name": "cleanup-downloads",
      "schedule": "every 5 minutes",
      "last_run_at": "2026-06-30T09:55:00Z",
      "last_status": "ok",
      "last_duration_ms": 340,
      "next_run_at": "2026-06-30T10:00:00Z"
    }
  ]
}
```

### C.7 Job Inspector — NEW

```
GET  /admin/jobs                      ← search: job_id, batch_id, url, platform, ip, page
GET  /admin/jobs/{job_id}             ← job detail
GET  /admin/jobs/{job_id}/trace       ← phase trace (requires job_phase_trace table)
GET  /admin/jobs/{batch_id}/batch     ← batch overview: per-item grid
POST /admin/jobs/{job_id}/retry       ← {from_phase: "metadata"|"full"} [operator+]
POST /admin/jobs/{job_id}/cancel      ← [operator+]
```

**GET `/admin/jobs` params:** `?q=&platform=&status=&ip=&page=1&limit=50`

**GET `/admin/jobs/{id}/trace` response:**
```json
{
  "job_id": "uuid",
  "platform": "instagram",
  "url": "https://...",
  "created_at": "...",
  "phases": [
    {
      "phase": "queued",
      "started_at": "2026-06-30T09:58:00.000Z",
      "ended_at":   "2026-06-30T09:58:00.120Z",
      "duration_ms": 120,
      "proxy_source": null,
      "cookie_id": null,
      "result": "ok",
      "error_type": null,
      "error_msg": null,
      "raw_error": null
    },
    {
      "phase": "metadata",
      "started_at": "2026-06-30T09:58:00.120Z",
      "ended_at":   "2026-06-30T09:58:02.445Z",
      "duration_ms": 2325,
      "proxy_source": "proxying_io",
      "cookie_id": null,
      "result": "ok"
    },
    {
      "phase": "auth",
      "started_at": "2026-06-30T09:58:02.450Z",
      "ended_at":   "2026-06-30T09:58:02.900Z",
      "duration_ms": 450,
      "proxy_source": null,
      "cookie_id": "ig_main_2026",
      "result": "error",
      "error_type": "login_required",
      "error_msg": "Cookie expired or account logged out",
      "raw_error": {"http_status": 401, "body": "..."}
    }
  ],
  "final_status": "failed",
  "total_duration_ms": 900
}
```

### C.8 Analytics — EXTEND Existing

```
GET /admin/analytics                  ← ĐÃ CÓ — thêm range param, per-platform
GET /admin/analytics/errors           ← EXTEND /admin/errors: thêm range + breakdown
GET /admin/analytics/heatmap          ← MỚI: hour × day matrix
GET /admin/analytics/metrics          ← MỚI: summary KPIs
```

**GET `/admin/analytics?range=24h&group_by=hour` response:**
```json
{
  "range": "24h",
  "group_by": "hour",
  "series": [
    {
      "timestamp": "2026-06-30T09:00:00Z",
      "total": 145,
      "success": 138,
      "failed": 7,
      "partial": 0
    }
  ],
  "by_platform": {
    "tiktok": {"total": 80, "success": 79, "failed": 1},
    "spotify": {"total": 35, "success": 33, "failed": 2}
  }
}
```

**GET `/admin/analytics/heatmap?range=7d` response:**
```json
{
  "matrix": [
    {"hour": 0, "day": "2026-06-24", "count": 12},
    {"hour": 1, "day": "2026-06-24", "count": 5}
  ],
  "max_count": 214
}
```

**GET `/admin/analytics/metrics?range=24h` response:**
```json
{
  "range": "24h",
  "success_rate": 0.953,
  "avg_download_ms": 4200,
  "avg_file_size_mb": 28.4,
  "quota_hits": 12,
  "total_downloads": 1240,
  "unique_ips": 89
}
```

### C.9 System Config — NEW (superadmin only)

```
GET   /admin/config                   ← all DB-backed overrides
PATCH /admin/config                   ← update one or many
GET   /admin/config/circuit-breakers  ← per-platform CB config
PATCH /admin/config/circuit-breakers  ← update CB config
GET   /admin/config/rate-limits       ← per-endpoint config
PATCH /admin/config/rate-limits       ← update rate limits
```

**GET `/admin/config` response:**
```json
{
  "config": [
    {
      "key": "YOUTUBE_ENABLED",
      "value": "false",
      "value_type": "bool",
      "description": "Enable YouTube single video download",
      "updated_by": "trieunt@matbao.com",
      "updated_at": "2026-06-30T08:00:00Z",
      "is_sensitive": false
    }
  ]
}
```

---

## D. Data Model Additions (Supabase)

### D.1 `admin_users` — RBAC (NEW)

```sql
CREATE TABLE admin_users (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email           TEXT UNIQUE NOT NULL,
  password_hash   TEXT NOT NULL,          -- bcrypt
  role            TEXT NOT NULL CHECK (role IN ('viewer','operator','admin','superadmin')),
  is_active       BOOLEAN NOT NULL DEFAULT true,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_login_at   TIMESTAMPTZ,
  created_by      UUID REFERENCES admin_users(id),
  force_password_change BOOLEAN DEFAULT false
);
CREATE INDEX ON admin_users (email);
```

> **Migration từ hardcoded password:** Seed 1 superadmin row khi deploy. Existing Redis sessions vẫn valid (backward compat vì role check chỉ cần cho new endpoints).

### D.2 `admin_audit_log` — EXTEND Existing

```sql
-- Existing log_admin_action() writes to Redis list. 
-- Promote sang Postgres cho searchability.
CREATE TABLE admin_audit_log (
  id              BIGSERIAL PRIMARY KEY,
  actor_id        UUID REFERENCES admin_users(id),
  actor_email     TEXT NOT NULL,           -- denormalized
  action          TEXT NOT NULL,           -- "cookie.add" | "circuit.force_close"
  target_type     TEXT,                    -- "cookie" | "platform" | "job" | "user"
  target_id       TEXT,
  payload         JSONB,
  result          TEXT NOT NULL CHECK (result IN ('ok','error')),
  error_msg       TEXT,
  ip_address      INET,
  user_agent      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON admin_audit_log (actor_id, created_at DESC);
CREATE INDEX ON admin_audit_log (target_type, target_id);
CREATE INDEX ON admin_audit_log (created_at DESC);
```

> **Backward compat:** Existing `log_admin_action()` tiếp tục write vào Redis list. Thêm async task write sang Postgres. Không block request path.

### D.3 `job_phase_trace` — NEW (required for Job Inspector)

```sql
CREATE TABLE job_phase_trace (
  id              BIGSERIAL PRIMARY KEY,
  job_id          UUID NOT NULL REFERENCES download_jobs(id) ON DELETE CASCADE,
  phase           TEXT NOT NULL,           -- queued|resolve|metadata|auth|download|postprocess|done|failed
  started_at      TIMESTAMPTZ NOT NULL,
  ended_at        TIMESTAMPTZ,
  duration_ms     INT,
  proxy_source    TEXT,                    -- "proxying_io"|"scraperapi"|"direct"|null
  cookie_id       TEXT,
  result          TEXT CHECK (result IN ('ok','error','skipped','pending')),
  error_type      TEXT,                    -- "429"|"403"|"timeout"|"login_required"
  error_msg       TEXT,
  raw_error       JSONB
);
CREATE INDEX ON job_phase_trace (job_id);
CREATE INDEX ON job_phase_trace (started_at DESC);
-- Partition by month if volume is high (optional)
```

> **Instrumentation:** Backend gọi `trace_phase_start(job_id, phase)` và `trace_phase_end(job_id, phase, result, ...)` trong `downloader.py`. Viết vào Postgres async (không block download). Nếu DB down → log to Redis buffer → drain khi DB up.

### D.4 `admin_config` — DB-backed env overrides (NEW)

```sql
CREATE TABLE admin_config (
  key             TEXT PRIMARY KEY,
  value           TEXT NOT NULL,
  value_type      TEXT NOT NULL CHECK (value_type IN ('bool','int','string','json')),
  description     TEXT,
  is_sensitive    BOOLEAN DEFAULT false,
  updated_by      UUID REFERENCES admin_users(id),
  updated_at      TIMESTAMPTZ DEFAULT now()
);

-- Seed rows
INSERT INTO admin_config (key, value, value_type, description) VALUES
  ('YOUTUBE_ENABLED', 'false', 'bool', 'Enable YouTube single video download'),
  ('YOUTUBE_PROXY_DOWNLOAD', '0', 'bool', 'Allow bytes download via proxy'),
  ('MAX_CONCURRENT_DOWNLOADS', '10', 'int', 'Global concurrent download cap'),
  ('DAILY_QUOTA_PER_IP', '50', 'int', 'Downloads per IP per day'),
  ('FILE_EXPIRY_SINGLE_MIN', '20', 'int', 'Minutes before single download link expires'),
  ('PLATFORM_CB_THRESHOLD', '5', 'int', 'Circuit breaker: fails before OPEN'),
  ('PLATFORM_CB_COOLDOWN', '300', 'int', 'Circuit breaker: seconds OPEN stays');
```

> **How it works:** Backend reads from DB on startup → Redis cache (5min TTL). Config page changes → PATCH endpoint → update DB + invalidate Redis. Sensitive keys (passwords, proxy URLs) are `is_sensitive=true` → value masked in GET response.

### D.5 `platform_health_metrics` — NEW (Celery beat writes hourly)

```sql
CREATE TABLE platform_health_metrics (
  id              BIGSERIAL PRIMARY KEY,
  platform        TEXT NOT NULL,
  recorded_at     TIMESTAMPTZ NOT NULL,   -- truncated to hour
  success_count   INT DEFAULT 0,
  fail_count      INT DEFAULT 0,
  p95_duration_ms INT,
  error_breakdown JSONB,                  -- {"403":5,"429":2,"timeout":1}
  phase_fail_counts JSONB                 -- {"metadata":3,"bytes":2}
);
CREATE UNIQUE INDEX ON platform_health_metrics (platform, recorded_at);
CREATE INDEX ON platform_health_metrics (recorded_at DESC);
-- Retention: DELETE WHERE recorded_at < now() - INTERVAL '30 days'
```

> **Writer:** New Celery periodic task `aggregate-platform-health` runs every 1h, reads from `download_jobs` WHERE `created_at > now() - 1h`, groups by platform + error type. Upsert into `platform_health_metrics`.

### D.6 `proxy_usage_log` — NEW (optional Phase 3)

```sql
CREATE TABLE proxy_usage_log (
  source          TEXT NOT NULL,
  platform        TEXT,
  phase           TEXT,
  requests        INT DEFAULT 0,
  failures        INT DEFAULT 0,
  bytes_used      BIGINT DEFAULT 0,
  cost_usd        NUMERIC(10,6) DEFAULT 0,
  recorded_at     TIMESTAMPTZ NOT NULL    -- hourly bucket
);
CREATE UNIQUE INDEX ON proxy_usage_log (source, platform, phase, recorded_at);
```

> **Writer:** Backend increments Redis counters (`proxy:usage:{source}:{hour}`). Celery task drains hourly into Postgres.

### D.7 `cookie_health_log` — NEW (simple event log)

```sql
CREATE TABLE cookie_health_log (
  id          BIGSERIAL PRIMARY KEY,
  cookie_id   TEXT NOT NULL,
  platform    TEXT NOT NULL,
  event       TEXT NOT NULL CHECK (event IN ('success','soft_block','hard_block','added','revoked','tested','rotated')),
  job_id      UUID,
  error_type  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON cookie_health_log (cookie_id, created_at DESC);
CREATE INDEX ON cookie_health_log (platform, created_at DESC);
-- Retention: 7 days
```

### D.8 Migration Plan

```sql
-- Run in this order (no breaking changes to existing tables):
-- 1.
CREATE TABLE admin_users (...);         -- seed 1 superadmin
-- 2.
CREATE TABLE admin_audit_log (...);     -- existing Redis log → async drain
-- 3.
CREATE TABLE job_phase_trace (...);     -- backend starts writing new jobs only
-- 4.
CREATE TABLE admin_config (...);        -- seed defaults, backend reads DB next deploy
-- 5.
CREATE TABLE platform_health_metrics (...); -- Celery task starts filling
-- 6. (Phase 3 only)
CREATE TABLE proxy_usage_log (...);
CREATE TABLE cookie_health_log (...);
```

---

## E. Implementation Order

### Phase 1 — Must Have (Operational Safety)

**Priority:** Fixes the most painful daily pain points. No new tables except `admin_users`.

| # | Việc cần làm | Files | DB change |
|---|-------------|-------|-----------|
| 1.1 | Create `admin_users` table, seed 1 superadmin | Migration | `admin_users` |
| 1.2 | Extend `verify_admin()` → check role from Redis session (role stored at login) | `admin.py` | — |
| 1.3 | `RoleGate` component + `useAdminAuth` hook | Frontend | — |
| 1.4 | **Login page refactor** — show email field (was token-only) | `LoginPage.jsx` | — |
| 1.5 | **Cookie Pool Panel** — extend `/admin/cookies` response với `health_score`, `expiry_estimate`, `fail_count`. Thêm test + rotate endpoints | `admin.py` | — (Redis only) |
| 1.6 | `CookiesPage` + `CookieTable` + `CookieHealthBar` + `AddCookieModal` | Frontend | — |
| 1.7 | Create `job_phase_trace` table. Instrument `downloader.py` với `trace_phase_start/end` | Backend | `job_phase_trace` |
| 1.8 | **Job Inspector** — `GET /admin/jobs` search + `GET /admin/jobs/{id}/trace` | `admin.py` | reads `job_phase_trace` |
| 1.9 | `JobsPage` + `PhaseTimeline` + `PhaseStep` + `ErrorTrace` | Frontend | — |
| 1.10 | **Overview refactor** — `SystemStatusBadge`, 4 `HealthPanel`, `AlertBanner` | Frontend | — (existing endpoints) |

**Estimated effort:** 3–5 ngày

---

### Phase 2 — High Value (Operational Visibility)

| # | Việc cần làm | Files | DB change |
|---|-------------|-------|-----------|
| 2.1 | `GET /admin/platforms/health` — đọc circuit state từ Redis + fail_rate từ `download_jobs` | `admin.py` | — |
| 2.2 | `POST /admin/platforms/{p}/circuit` — force_close/force_open qua `platform_circuit.py` | `admin.py` | — |
| 2.3 | `PlatformsPage` + `PlatformTable` + `CircuitStateChip` + `PlatformDrawer` | Frontend | — |
| 2.4 | Create `platform_health_metrics` table. Add `aggregate-platform-health` Celery task (1h) | Backend | `platform_health_metrics` |
| 2.5 | `GET /admin/platforms/{p}/detail` — error breakdown + phase heatmap | `admin.py` | reads `platform_health_metrics` |
| 2.6 | `PhaseHeatmap` + `ErrorBreakdownBar` components | Frontend | — |
| 2.7 | **Proxy Health Panel** — extend `/proxies/status` + policy endpoints | `admin.py` | — |
| 2.8 | `ProxyPage` + `ProxySourceCard` + `ProxyPolicyMatrix` | Frontend | — |
| 2.9 | **Queue & Workers** — `GET /admin/queue/workers` (Redis worker keys) + dead-letter | `admin.py` | — |
| 2.10 | `QueuePage` + `WorkerGrid` + `DeadLetterPanel` + `CeleryTaskTable` | Frontend | — |
| 2.11 | `POST /admin/queue/jobs/{id}/retry` + `cancel` | `admin.py` | — |
| 2.12 | **Audit Log** — promote Redis log → Postgres (create `admin_audit_log`, async drain) | Backend | `admin_audit_log` |

**Estimated effort:** 5–7 ngày

---

### Phase 3 — Nice to Have

| # | Việc cần làm | Files | DB change |
|---|-------------|-------|-----------|
| 3.1 | **Analytics depth** — heatmap + per-platform time series + KPI metrics | `admin.py` | reads `download_jobs` aggregate |
| 3.2 | `HourlyHeatmap` + `DownloadTrendChart` + `PlatformBarChart` + `ErrorPieChart` | Frontend | — |
| 3.3 | **System Config panel** — `admin_config` table + CRUD endpoints | `admin.py` | `admin_config` |
| 3.4 | Backend reads config from DB (with Redis 5min cache) instead of only env vars | `core/config_loader.py` | — |
| 3.5 | **Access management page** — admin user list, invite, role change | `admin.py` + Frontend | `admin_users` |
| 3.6 | **User Management** — extend existing `/admin/users` + `/admin/ips/top` | `admin.py` | — |
| 3.7 | Proxy usage tracking — `proxy_usage_log` + `ProxyCostTracker` | Backend | `proxy_usage_log` |
| 3.8 | Cookie health log — `cookie_health_log` + backend instrumentation | Backend | `cookie_health_log` |
| 3.9 | `RotationPolicyDrawer` — per-platform cookie rotation config | Frontend | reads `cookie_health_log` |

**Estimated effort:** 5–8 ngày

---

## F. Key Design Decisions & Trade-offs

### F.1 Tại sao không dùng external charting library?

Charts trong Analytics panel dùng plain SVG + CSS Grid — không có Recharts/D3.
**Lý do:** Bundle size (Recharts ~180KB gzip), không cần animation, operator-facing UI
ưu tiên tải nhanh hơn đẹp. Heatmap dùng CSS Grid 24 cột, `bg-opacity-*` TailwindCSS 4.

### F.2 Tại sao không thêm real-time WebSocket cho active jobs?

Polling 3s đủ cho operational visibility. WebSocket yêu cầu thêm infra (connection state,
reconnect logic). Với 8 workers và không có SLA real-time, polling 3s là pragmatic.

### F.3 Tại sao `admin_users` trong Supabase thay vì auth.users?

Admin users là một population riêng, không phải end-users của VidGrab.
`auth.users` = user tải video. `admin_users` = operator/engineer truy cập control plane.
Mixing 2 groups trong cùng 1 auth table làm phức tạp RLS policies.

### F.4 job_phase_trace — chỉ cho jobs MỚI, không backfill

Backfill historical jobs là tốn công và không có giá trị. Trace chỉ bắt đầu từ khi
deploy backend với instrumentation. Historical jobs hiển thị "trace not available".

### F.5 DB-backed config — không replace env vars hoàn toàn

`admin_config` chỉ override giá trị runtime (không sensitive). Secrets (API keys,
proxy credentials) vẫn ở `.env`. Config page không expose sensitive keys.
Hierarchy: `admin_config DB > env var default`.

### F.6 Cookie Health Score — tại sao simple formula thay vì ML?

Formula `100 - (fail×10) + min(success, 20)` là deterministic, predictable, và
operator có thể giải thích được. ML scoring cho cookie pool ở scale này (2-5 accounts)
là over-engineering không có ROI.

---

## G. UI Color System (Dark Mode Default)

```
Background:
  surface-0: #0f1117   (main bg)
  surface-1: #1a1d27   (card bg)
  surface-2: #252836   (elevated, table row hover)
  border:    #2d3148   (dividers)

Status colors:
  ok:       #22c55e   (green-500)  + bg: #052e16
  warn:     #f59e0b   (amber-500)  + bg: #1c1505
  error:    #ef4444   (red-500)    + bg: #1f0808
  disabled: #6b7280   (gray-500)   + bg: #111
  info:     #3b82f6   (blue-500)   + bg: #0c1a3a

Circuit state:
  CLOSED:   text-green-400  bg-green-950
  OPEN:     text-red-400    bg-red-950
  HALF:     text-amber-400  bg-amber-950
  EXEMPT:   text-gray-400   bg-gray-800

Health score bars:
  >70:      bg-green-500
  30–70:    bg-amber-500
  <30:      bg-red-500

Typography:
  font-family: ui-monospace, 'Cascadia Code', monospace  (for metrics, IDs)
  font-family: system-ui (for labels, descriptions)
  font-size: base 14px (information-dense)
```

---

## H. Mobile / Tablet Adaptations

| Component | Desktop | Tablet (768–1024px) | Mobile (<768px) |
|-----------|---------|---------------------|-----------------|
| Sidebar | 240px | 60px icon-only | Hidden |
| Navigation | Sidebar | Icon sidebar | Bottom tab bar |
| Health panels | 2×2 grid | 2×2 grid | 1×4 stack |
| Platform table | Full columns | Hide: fail_rate, proxy_required | Only: name, status, circuit |
| Cookie table | Full columns | Hide: expiry, batch_count | Only: platform, score, status |
| Job trace | Side-by-side | Side-by-side | Vertical stack |
| Charts | Full width | Full width | Horizontal scroll container |
| Drawers | 480px right panel | Full-screen modal | Full-screen modal |

---

## I. Security Checklist (Pre-deploy)

- [ ] `admin_users` table: row-level security ON, no public access
- [ ] `/api/v1/admin/*` endpoints: `verify_admin()` on every route (no exception)
- [ ] Role check on write operations: `require_role("operator")` pattern
- [ ] Audit log: every write endpoint calls `log_admin_action()` in finally block
- [ ] Session tokens: store hash in Redis (not plaintext), delete on logout
- [ ] IP allowlist: `ADMIN_ALLOWED_IPS` env var (optional, recommended for prod)
- [ ] Rate limit login endpoint: already has lockout (3 fails → 15m), keep it
- [ ] Config endpoint: `is_sensitive` keys return `"***"` in GET response
- [ ] Impersonate (superadmin): creates audit log entry, time-limited session (30m)
- [ ] CORS: `/api/v1/admin/*` restricted to admin frontend origin only
- [ ] `force_password_change` flag: set for newly-created admin accounts

---

*VidGrab Admin Control Plane Design Spec v1.0 — 2026-06-30*
