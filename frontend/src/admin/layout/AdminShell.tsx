import { Component, useEffect, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { X } from 'lucide-react'
import { AdminSidebar } from './AdminSidebar'
import { AdminTopBar } from './AdminTopBar'
import { AdminMobileNav } from './AdminMobileNav'
import { useAdminAuth } from '../hooks/useAdminAuth'

interface AdminShellProps {
  children: ReactNode
}

class PageErrorBoundary extends Component<
  { children: ReactNode },
  { error: string | null }
> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(err: unknown) {
    return { error: err instanceof Error ? err.message : String(err) }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full items-center justify-center">
          <div className="max-w-lg space-y-3 rounded-card border border-danger/30 bg-danger-soft p-6 text-center">
            <p className="text-base font-semibold text-danger">Page crashed</p>
            <p className="break-all font-mono text-sm text-danger">{this.state.error}</p>
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="mt-2 rounded-control bg-danger px-4 py-1.5 text-sm font-medium text-danger-fg hover:opacity-90"
            >
              Retry
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export function AdminShell({ children }: AdminShellProps) {
  const { isAuthenticated } = useAdminAuth()
  const [menuOpen, setMenuOpen] = useState(false)
  const { pathname } = useLocation()

  // Close the drawer on navigation and on Escape.
  useEffect(() => { setMenuOpen(false) }, [pathname])
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuOpen])

  if (!isAuthenticated) {
    return <Navigate to="/vid-admin/login" replace />
  }

  return (
    <div className="flex h-screen overflow-hidden bg-canvas text-fg">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-surface lg:flex">
        <AdminSidebar />
      </aside>

      {/* Mobile drawer: the bottom bar only fits 5 tabs, this reaches every page. */}
      {menuOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-fg/40" onClick={() => setMenuOpen(false)} aria-hidden />
          <aside className="absolute inset-y-0 left-0 flex w-64 max-w-[85vw] flex-col border-r border-line bg-surface shadow-xl">
            <button
              type="button"
              onClick={() => setMenuOpen(false)}
              aria-label="Menu"
              className="absolute right-2 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg"
            >
              <X aria-hidden className="h-4 w-4" />
            </button>
            <AdminSidebar onNavigate={() => setMenuOpen(false)} />
          </aside>
        </div>
      )}

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <AdminTopBar onOpenMenu={() => setMenuOpen(true)} />

        {/* Scrollable content */}
        <main className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-5 pb-20 lg:px-6 lg:py-6 lg:pb-6">
          <div className="mx-auto w-full max-w-[1400px]">
            <PageErrorBoundary>{children}</PageErrorBoundary>
          </div>
        </main>
      </div>

      {/* Mobile bottom nav */}
      <AdminMobileNav />
    </div>
  )
}
