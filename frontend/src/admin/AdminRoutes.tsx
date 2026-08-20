import { Routes, Route, Navigate } from 'react-router-dom'
import { AdminShell } from './layout/AdminShell'
import { AdminLoginPage } from './pages/AdminLoginPage'
import { AdminHomePage } from './pages/AdminHomePage'
import { PlatformsPage } from './pages/PlatformsPage'
import { CookiesPage } from './pages/CookiesPage'
import { JobsPage } from './pages/JobsPage'
import { ProxyPage } from './pages/ProxyPage'
import { QueuePage } from './pages/QueuePage'
import { AuditLogPage } from './pages/AuditLogPage'
import { AnalyticsPage } from './pages/AnalyticsPage'
import UsersPage from './pages/UsersPage'
import ConfigPage from './pages/ConfigPage'
import AccessPage from './pages/AccessPage'
import TenantsPage from './pages/TenantsPage'
import ApiKeysPage from './pages/ApiKeysPage'
import WebhooksPage from './pages/WebhooksPage'
import EnterprisUsagePage from './pages/EnterprisUsagePage'
import AiAnalysisPage from './pages/AiAnalysisPage'
import BillingAdminPage from './pages/BillingAdminPage'
import PresetsPage from './pages/PresetsPage'
// Ported back from src/pages/Admin/, where no route could reach them.
import QueueHealthPage from './pages/QueueHealthPage'
import PlaybooksPage from './pages/PlaybooksPage'
import AutomationHistoryPage from './pages/AutomationHistoryPage'
import YouTubeGatePage from './pages/YouTubeGatePage'

function Shell({ children }: { children: React.ReactNode }) {
  return <AdminShell>{children}</AdminShell>
}

export function AdminRoutes() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/vid-admin/login" element={<AdminLoginPage />} />

      {/* Protected — Phase 1 */}
      <Route path="/vid-admin"           element={<Shell><AdminHomePage /></Shell>} />
      <Route path="/vid-admin/platforms" element={<Shell><PlatformsPage /></Shell>} />
      <Route path="/vid-admin/cookies"   element={<Shell><CookiesPage /></Shell>} />
      <Route path="/vid-admin/jobs"      element={<Shell><JobsPage /></Shell>} />

      {/* Protected — Phase 2 (now wired) */}
      <Route path="/vid-admin/proxy"     element={<Shell><ProxyPage /></Shell>} />
      <Route path="/vid-admin/queue"     element={<Shell><QueuePage /></Shell>} />
      <Route path="/vid-admin/queue-health"       element={<Shell><QueueHealthPage /></Shell>} />
      <Route path="/vid-admin/playbooks"          element={<Shell><PlaybooksPage /></Shell>} />
      <Route path="/vid-admin/automation-history" element={<Shell><AutomationHistoryPage /></Shell>} />
      <Route path="/vid-admin/youtube-gate"       element={<Shell><YouTubeGatePage /></Shell>} />
      <Route path="/vid-admin/audit"     element={<Shell><AuditLogPage /></Shell>} />

      {/* Phase 3+ — Analytics now live */}
      <Route path="/vid-admin/analytics" element={<Shell><AnalyticsPage /></Shell>} />
      <Route path="/vid-admin/users"     element={<Shell><UsersPage /></Shell>} />
      <Route path="/vid-admin/config"    element={<Shell><ConfigPage /></Shell>} />
      <Route path="/vid-admin/access"    element={<Shell><AccessPage /></Shell>} />

      {/* Phase 4 — Enterprise */}
      <Route path="/vid-admin/tenants"   element={<Shell><TenantsPage /></Shell>} />
      <Route path="/vid-admin/api-keys"  element={<Shell><ApiKeysPage /></Shell>} />
      <Route path="/vid-admin/webhooks"  element={<Shell><WebhooksPage /></Shell>} />
      <Route path="/vid-admin/usage"     element={<Shell><EnterprisUsagePage /></Shell>} />

      {/* Phase 5 — AI + Billing */}
      <Route path="/vid-admin/ai-analysis" element={<Shell><AiAnalysisPage /></Shell>} />
      <Route path="/vid-admin/billing"     element={<Shell><BillingAdminPage /></Shell>} />

      {/* Phase 6 — Presets */}
      <Route path="/vid-admin/presets" element={<Shell><PresetsPage /></Shell>} />

      {/* Catch-all */}
      <Route path="/vid-admin/*" element={<Navigate to="/vid-admin" replace />} />
    </Routes>
  )
}
