import { NavLink, useNavigate } from 'react-router-dom'
import { cn } from '../utils/cn'
import { useAdminAuth } from '../hooks/useAdminAuth'

interface NavItem {
  href: string
  label: string
  icon: string
  minRole?: 'viewer' | 'operator' | 'admin' | 'superadmin'
  /**
   * Kept out of the sidebar, still routed and reachable by URL.
   *
   * The menu had 24 entries and most of them could not show anything on this
   * deployment: the whole Enterprise group is backed by tables holding either
   * zero rows or leftovers from one "R27 Pilot Tenant" trial in August, and
   * Billing in particular cannot ever populate because no STRIPE_* variable
   * exists in the environment. A menu that lists mostly dead ends trains you
   * to ignore it, which is how a real signal gets missed.
   *
   * Nothing is deleted — the routes stay in AdminRoutes.tsx, so bookmarks keep
   * working, and re-listing one is deleting a single line.
   */
  hidden?: boolean
}

const NAV_OVERVIEW: NavItem[] = [
  { href: '/vid-admin', label: 'Overview', icon: '◉', minRole: 'viewer' },
]

const NAV_MONITOR: NavItem[] = [
  { href: '/vid-admin/platforms', label: 'Platforms', icon: '⬡', minRole: 'viewer' },
  { href: '/vid-admin/cookies', label: 'Cookies', icon: '⬢', minRole: 'viewer' },
  { href: '/vid-admin/proxy', label: 'Proxy', icon: '◈', minRole: 'viewer' },
  { href: '/vid-admin/queue', label: 'Queue', icon: '◧', minRole: 'viewer' },
  { href: '/vid-admin/jobs', label: 'Jobs', icon: '⊡', minRole: 'viewer' },
  { href: '/vid-admin/analytics', label: 'Analytics', icon: '◫', minRole: 'viewer' },
  { href: '/vid-admin/youtube-gate', label: 'YouTube Gate', icon: '⊙', minRole: 'operator' },
  // Ops Signals is the aggregated "is anything wrong right now" view. Queue
  // Health and Anomalies answer the same question from the same Redis state in
  // a different arrangement, so they are folded behind it rather than listed
  // three times. Queue Health also owns the auto-tune controls — reachable at
  // /vid-admin/queue-health when those are needed.
  { href: '/vid-admin/ops-signals', label: 'Ops Signals', icon: '◆', minRole: 'viewer' },
  { href: '/vid-admin/queue-health', label: 'Queue Health', icon: '◍', minRole: 'viewer', hidden: true },
  { href: '/vid-admin/anomalies', label: 'Anomalies', icon: '◇', minRole: 'viewer', hidden: true },
]

const NAV_MANAGE: NavItem[] = [
  { href: '/vid-admin/users', label: 'Users', icon: '⊛', minRole: 'operator' },
  { href: '/vid-admin/config', label: 'Config', icon: '⊜', minRole: 'admin' },
  { href: '/vid-admin/playbooks', label: 'Playbooks', icon: '⊟', minRole: 'operator' },
  { href: '/vid-admin/automation-history', label: 'Automation', icon: '⊠', minRole: 'viewer' },
]

// The partner/multi-tenant surface. Every page here reads a table that is
// empty or holds only the August "R27 Pilot Tenant" trial data — api_keys,
// webhook_endpoints, analysis_jobs, payment_events, user_credits and
// user_presets are all at zero rows. Re-list a line here the day that feature
// has real customers.
const NAV_ENTERPRISE: NavItem[] = [
  { href: '/vid-admin/tenants',     label: 'Tenants',    icon: '◨', minRole: 'admin', hidden: true },
  { href: '/vid-admin/api-keys',    label: 'API Keys',   icon: '◪', minRole: 'admin', hidden: true },
  { href: '/vid-admin/webhooks',    label: 'Webhooks',   icon: '◩', minRole: 'admin', hidden: true },
  { href: '/vid-admin/usage',       label: 'Usage',      icon: '◬', minRole: 'admin', hidden: true },
  { href: '/vid-admin/ai-analysis', label: 'AI Analysis', icon: '◭', minRole: 'admin', hidden: true },
  { href: '/vid-admin/billing',     label: 'Billing',    icon: '◮', minRole: 'admin', hidden: true },
  { href: '/vid-admin/presets',     label: 'Presets',    icon: '◯', minRole: 'admin', hidden: true },
]

const NAV_SYSTEM: NavItem[] = [
  // Admin session management, superadmin-only, with a single admin account.
  { href: '/vid-admin/access', label: 'Access', icon: '⊝', minRole: 'superadmin', hidden: true },
  { href: '/vid-admin/audit', label: 'Audit Log', icon: '⊞', minRole: 'admin' },
]

const SECTIONS = [
  { label: null, items: NAV_OVERVIEW },
  { label: 'Monitor', items: NAV_MONITOR },
  { label: 'Manage', items: NAV_MANAGE },
  { label: 'Enterprise', items: NAV_ENTERPRISE },
  { label: 'System', items: NAV_SYSTEM },
]

export function AdminSidebar() {
  const navigate = useNavigate()
  const { user, logout, hasRole } = useAdminAuth()

  function handleLogout() {
    logout()
    navigate('/vid-admin/login')
  }

  return (
    <div className="flex h-full flex-col">
      {/* Logo */}
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-slate-800 px-4">
        <span className="text-lg">▼</span>
        <span className="font-mono text-sm font-bold tracking-tight text-slate-100">
          VidGrab <span className="text-slate-500">Admin</span>
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto py-3">
        {SECTIONS.map((section, si) => {
          const visibleItems = section.items.filter(
            (item) => !item.hidden && (!item.minRole || hasRole(item.minRole)),
          )
          if (visibleItems.length === 0) return null
          return (
            <div key={si} className="mb-1">
              {section.label && (
                <p className="mb-0.5 px-4 pt-3 font-mono text-[9px] font-semibold uppercase tracking-widest text-slate-600">
                  {section.label}
                </p>
              )}
              {visibleItems.map((item) => (
                <NavLink
                  key={item.href}
                  to={item.href}
                  end={item.href === '/vid-admin'}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2.5 rounded-lg mx-2 px-3 py-1.5 text-sm transition-colors',
                      isActive
                        ? 'bg-slate-800 text-slate-100'
                        : 'text-slate-400 hover:bg-slate-800/50 hover:text-slate-200',
                    )
                  }
                >
                  <span className="font-mono text-base leading-none text-slate-500">
                    {item.icon}
                  </span>
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          )
        })}
      </nav>

      {/* User footer */}
      <div className="shrink-0 border-t border-slate-800 p-3">
        {user && (
          <div className="flex items-center gap-2 rounded-lg px-2 py-1.5">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-700 font-mono text-xs text-slate-300">
              {user.email.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-slate-300">{user.email}</p>
              <p className="font-mono text-[10px] uppercase text-slate-600">{user.role}</p>
            </div>
            <button
              onClick={handleLogout}
              title="Logout"
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-slate-500 transition-colors hover:text-red-400"
            >
              ⏻
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
