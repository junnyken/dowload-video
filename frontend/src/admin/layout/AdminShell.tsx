import { Component, type ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
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
          <div className="max-w-lg rounded-xl border border-danger/30 bg-danger-soft p-6 text-center space-y-3">
            <p className="text-lg font-semibold text-danger">Page crashed</p>
            <p className="text-sm text-danger font-mono break-all">{this.state.error}</p>
            <button
              onClick={() => this.setState({ error: null })}
              className="mt-2 px-4 py-1.5 rounded bg-danger hover:opacity-90 text-danger text-sm"
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

  if (!isAuthenticated) {
    return <Navigate to="/vid-admin/login" replace />
  }

  return (
    <div className="flex h-screen overflow-hidden bg-canvas text-fg">
      {/* Desktop sidebar */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-surface-2 lg:flex">
        <AdminSidebar />
      </aside>

      {/* Main column */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <AdminTopBar />

        {/* Scrollable content */}
        <main className="flex-1 overflow-y-auto px-4 py-4 pb-20 lg:px-6 lg:py-5 lg:pb-5">
          <PageErrorBoundary>{children}</PageErrorBoundary>
        </main>
      </div>

      {/* Mobile bottom nav */}
      <AdminMobileNav />
    </div>
  )
}
