import { NavLink, useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { cn } from '../utils/cn'
import { useAdminAuth } from '../hooks/useAdminAuth'
import { NAV_SECTIONS } from './navConfig'

interface AdminSidebarProps {
  /** Called after a nav click — lets the mobile drawer close itself. */
  onNavigate?: () => void
}

export function AdminSidebar({ onNavigate }: AdminSidebarProps) {
  const navigate = useNavigate()
  const { user, logout, hasRole } = useAdminAuth()

  function handleLogout() {
    logout()
    navigate('/vid-admin/login')
  }

  return (
    <div className="flex h-full flex-col">
      {/* Brand */}
      <div className="flex h-14 shrink-0 items-center gap-2.5 border-b border-line px-4">
        <span
          aria-hidden
          className="flex h-6 w-6 items-center justify-center rounded-control bg-accent font-mono text-xs font-bold text-accent-fg"
        >
          V
        </span>
        <span className="font-mono text-sm font-semibold tracking-tight text-fg">
          VidGrab <span className="text-fg-muted">Admin</span>
        </span>
      </div>

      {/* Grouped nav */}
      <nav aria-label="Admin" className="flex-1 overflow-y-auto px-2 py-3">
        {NAV_SECTIONS.map((section, si) => {
          const visibleItems = section.items.filter(
            (item) => !item.hidden && (!item.minRole || hasRole(item.minRole)),
          )
          if (visibleItems.length === 0) return null
          return (
            <div key={si} className={cn(si > 0 && 'mt-4')}>
              {section.label && (
                <p className="mb-1 px-3 font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted">
                  {section.label}
                </p>
              )}
              <ul className="space-y-0.5">
                {visibleItems.map((item) => {
                  const Icon = item.icon
                  return (
                    <li key={item.href}>
                      <NavLink
                        to={item.href}
                        end={item.href === '/vid-admin'}
                        onClick={onNavigate}
                        className={({ isActive }) =>
                          cn(
                            'relative flex items-center gap-2.5 rounded-control px-3 py-2 text-sm transition-colors',
                            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent',
                            isActive
                              ? 'bg-surface-2 font-medium text-fg'
                              : 'text-fg-2 hover:bg-surface-2 hover:text-fg',
                          )
                        }
                      >
                        {({ isActive }) => (
                          <>
                            {isActive && (
                              <span aria-hidden className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent" />
                            )}
                            <Icon
                              aria-hidden
                              className={cn('h-4 w-4 shrink-0', isActive ? 'text-accent-text' : 'text-fg-muted')}
                            />
                            <span className="truncate">{item.label}</span>
                          </>
                        )}
                      </NavLink>
                    </li>
                  )
                })}
              </ul>
            </div>
          )
        })}
      </nav>

      {/* User footer */}
      {user && (
        <div className="shrink-0 border-t border-line p-3">
          <div className="flex items-center gap-2.5 px-1">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-surface-2 font-mono text-xs text-fg-2">
              {user.email.charAt(0).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-fg">{user.email}</p>
              <p className="font-mono text-[10px] uppercase tracking-wider text-fg-muted">{user.role}</p>
            </div>
            <button
              type="button"
              onClick={handleLogout}
              title="Logout"
              aria-label="Logout"
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control text-fg-muted transition-colors hover:bg-danger-soft hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <LogOut aria-hidden className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
