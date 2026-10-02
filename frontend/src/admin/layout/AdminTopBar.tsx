import { useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Bell, Menu } from 'lucide-react'
import ThemeToggle from '../../components/ThemeToggle'
import { useAdminSystemStatus } from '../hooks/useAdminSystemStatus'
import { buildAlerts } from '../utils/alerts'
import { AlertRepeat, AlertTime } from '../panels/ActiveAlertsBanner'
import { cn } from '../utils/cn'
import { StatusPill, type PillStatus } from '../shared/StatusPill'
import { titleForPath } from './navConfig'

interface AdminTopBarProps {
  /** Opens the mobile navigation drawer. */
  onOpenMenu?: () => void
}

export function AdminTopBar({ onOpenMenu }: AdminTopBarProps) {
  const [alertsOpen, setAlertsOpen] = useState(false)
  const { pathname } = useLocation()

  const { data: snapshot } = useAdminSystemStatus(60_000)
  const alerts = snapshot ? buildAlerts(snapshot).fresh : []

  const hasCritical = alerts.some(a => a.severity === 'critical')
  const hasWarning  = alerts.some(a => a.severity === 'warning')
  const status: { pill: PillStatus; label: string } = hasCritical
    ? { pill: 'critical', label: 'CRITICAL' }
    : hasWarning
    ? { pill: 'degraded', label: 'DEGRADED' }
    : !snapshot || snapshot.failed.length > 0
    // Missing data is not a clean bill of health.
    ? { pill: 'unknown', label: 'KHÔNG RÕ' }
    : { pill: 'healthy', label: 'HEALTHY' }

  const activeAlerts = alerts.filter(a => a.severity !== 'info').length

  return (
    <header className="relative z-20 flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-surface px-4 lg:px-6">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={onOpenMenu}
          aria-label="Menu"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control border border-line text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent lg:hidden"
        >
          <Menu aria-hidden className="h-4 w-4" />
        </button>
        <p className="truncate text-sm font-semibold tracking-tight text-fg">{titleForPath(pathname)}</p>
      </div>

      <div className="flex items-center gap-2">
        <StatusPill status={status.pill} label={status.label} dot size="sm" />

        <div className="relative">
          <button
            type="button"
            onClick={() => setAlertsOpen(o => !o)}
            aria-label="Cảnh báo"
            aria-expanded={alertsOpen}
            className="relative flex h-8 w-8 items-center justify-center rounded-control border border-line text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Bell aria-hidden className="h-4 w-4" />
            {activeAlerts > 0 && (
              <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 font-mono text-[9px] font-bold text-danger-fg">
                {activeAlerts}
              </span>
            )}
          </button>

          {alertsOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setAlertsOpen(false)} />
              <div className="absolute right-0 top-10 z-20 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-card border border-line bg-surface shadow-lg">
                <div className="border-b border-line px-4 py-2.5">
                  <p className="font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted">
                    Cảnh báo đang hoạt động {alerts.length === 0 && <span className="font-normal normal-case">(không có)</span>}
                  </p>
                </div>
                <div className="max-h-72 overflow-y-auto">
                  {alerts.length === 0 ? (
                    <p className="px-4 py-6 text-center text-xs text-fg-muted">Hệ thống ổn định</p>
                  ) : alerts.map(alert => (
                    <div key={alert.id} className="border-b border-line px-4 py-2.5 last:border-0">
                      <div className="flex items-start gap-2.5">
                        <span
                          className={cn(
                            'mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full',
                            alert.severity === 'critical' ? 'bg-danger' :
                            alert.severity === 'warning'  ? 'bg-warning' : 'bg-fg-muted',
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <p className="text-xs font-semibold text-fg">{alert.title ?? alert.message}</p>
                            {alert.platform && (
                              <span className="rounded border border-line bg-surface-2 px-1 py-px font-mono text-[10px] uppercase tracking-wider text-fg-2">{alert.platform}</span>
                            )}
                          </div>
                          {alert.title && <p className="mt-0.5 text-xs text-fg-2">{alert.message}</p>}
                          <p className="mt-0.5 text-[11px] text-fg-muted">
                            <AlertRepeat alert={alert} fallback={<AlertTime alert={alert} className="font-mono" />} />
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        <ThemeToggle />
      </div>
    </header>
  )
}
