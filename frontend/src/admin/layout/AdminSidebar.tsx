import { NavLink, useNavigate } from 'react-router-dom'
import { cn } from '../utils/cn'
import { useAdminAuth } from '../hooks/useAdminAuth'

interface NavItem {
  href: string
  label: string
  icon: string
  minRole?: 'viewer' | 'operator' | 'admin' | 'superadmin'
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
  { href: '/vid-admin/queue-health', label: 'Queue Health', icon: '◍', minRole: 'viewer' },
  { href: '/vid-admin/youtube-gate', label: 'YouTube Gate', icon: '⊙', minRole: 'operator' },
]

const NAV_MANAGE: NavItem[] = [
  { href: '/vid-admin/users', label: 'Users', icon: '⊛', minRole: 'operator' },
  { href: '/vid-admin/config', label: 'Config', icon: '⊜', minRole: 'admin' },
  { href: '/vid-admin/playbooks', label: 'Playbooks', icon: '⊟', minRole: 'operator' },
  { href: '/vid-admin/automation-history', label: 'Automation', icon: '⊠', minRole: 'viewer' },
]

const NAV_ENTERPRISE: NavItem[] = [
  { href: '/vid-admin/tenants',     label: 'Tenants',    icon: '◨', minRole: 'admin' },
  { href: '/vid-admin/api-keys',    label: 'API Keys',   icon: '◪', minRole: 'admin' },
  { href: '/vid-admin/webhooks',    label: 'Webhooks',   icon: '◩', minRole: 'admin' },
  { href: '/vid-admin/usage',       label: 'Usage',      icon: '◬', minRole: 'admin' },
  { href: '/vid-admin/ai-analysis', label: 'AI Analysis', icon: '◭', minRole: 'admin' },
  { href: '/vid-admin/billing',     label: 'Billing',    icon: '◮', minRole: 'admin' },
  { href: '/vid-admin/presets',     label: 'Presets',    icon: '◯', minRole: 'admin' },
]

const NAV_SYSTEM: NavItem[] = [
  { href: '/vid-admin/access', label: 'Access', icon: '⊝', minRole: 'superadmin' },
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
            (item) => !item.minRole || hasRole(item.minRole),
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
