import { NavLink } from 'react-router-dom'
import { cn } from '../utils/cn'

const MOBILE_TABS = [
  { href: '/vid-admin', label: 'Overview', icon: '◉', end: true },
  { href: '/vid-admin/platforms', label: 'Platforms', icon: '⬡' },
  { href: '/vid-admin/cookies', label: 'Cookies', icon: '⬢' },
  { href: '/vid-admin/jobs', label: 'Jobs', icon: '⊡' },
  { href: '/vid-admin/queue', label: 'Queue', icon: '◧' },
]

export function AdminMobileNav() {
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 flex h-14 items-stretch border-t border-line bg-canvas lg:hidden">
      {MOBILE_TABS.map((tab) => (
        <NavLink
          key={tab.href}
          to={tab.href}
          end={tab.end}
          className={({ isActive }) =>
            cn(
              'flex flex-1 flex-col items-center justify-center gap-0.5 text-[10px] transition-colors',
              isActive ? 'text-fg' : 'text-fg-muted hover:text-fg-2',
            )
          }
        >
          {({ isActive }) => (
            <>
              <span
                className={cn(
                  'flex h-7 w-10 items-center justify-center rounded-xl text-base transition-colors',
                  isActive ? 'bg-surface' : '',
                )}
              >
                {tab.icon}
              </span>
              <span className="font-mono tracking-tight">{tab.label}</span>
            </>
          )}
        </NavLink>
      ))}
    </nav>
  )
}
