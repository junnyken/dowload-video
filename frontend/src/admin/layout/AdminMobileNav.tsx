import { NavLink } from 'react-router-dom'
import { cn } from '../utils/cn'
import { MOBILE_TABS } from './navConfig'

export function AdminMobileNav() {
  return (
    <nav
      aria-label="Admin"
      className="fixed inset-x-0 bottom-0 z-30 flex h-14 items-stretch border-t border-line bg-surface lg:hidden"
    >
      {MOBILE_TABS.map((tab) => {
        const Icon = tab.icon
        return (
          <NavLink
            key={tab.href}
            to={tab.href}
            end={tab.href === '/vid-admin'}
            className={({ isActive }) =>
              cn(
                'relative flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 text-[10px] transition-colors',
                isActive ? 'text-fg' : 'text-fg-muted hover:text-fg-2',
              )
            }
          >
            {({ isActive }) => (
              <>
                {isActive && <span aria-hidden className="absolute inset-x-4 top-0 h-0.5 rounded-full bg-accent" />}
                <Icon aria-hidden className={cn('h-5 w-5', isActive && 'text-accent-text')} />
                <span className="max-w-full truncate font-mono tracking-tight">{tab.label}</span>
              </>
            )}
          </NavLink>
        )
      })}
    </nav>
  )
}
