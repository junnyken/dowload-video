import { useState } from 'react'
import { useAdminSystemStatus } from '../hooks/useAdminSystemStatus'
import { buildAlerts } from '../utils/alerts'

const OVERALL_STATUS_STYLE = {
  ok:       'bg-emerald-950 text-emerald-400 border-emerald-900',
  warning:  'bg-amber-950  text-amber-400   border-amber-900',
  critical: 'bg-red-950    text-red-400     border-red-900',
}

export function AdminTopBar() {
  const [alertsOpen, setAlertsOpen] = useState(false)

  const { data: snapshot } = useAdminSystemStatus(60_000)
  const alerts = snapshot ? buildAlerts(snapshot) : []

  const hasCritical = alerts.some(a => a.severity === 'critical')
  const hasWarning  = alerts.some(a => a.severity === 'warning')
  const status = hasCritical
    ? { level: 'critical' as const, label: 'CRITICAL' }
    : hasWarning
    ? { level: 'warning'  as const, label: 'DEGRADED' }
    : { level: 'ok'       as const, label: 'HEALTHY'  }

  const activeAlerts = alerts.filter(a => a.severity !== 'info').length

  return (
    <header className="relative z-10 flex h-12 shrink-0 items-center justify-between border-b border-slate-800 bg-slate-950 px-4">
      <span className="hidden font-mono text-xs text-slate-500 sm:block">vid-admin</span>

      <div className="flex items-center gap-3">
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${OVERALL_STATUS_STYLE[status.level]}`}
        >
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
          {status.label}
        </span>

        <button
          onClick={() => setAlertsOpen(o => !o)}
          aria-label="Alerts"
          aria-expanded={alertsOpen}
          className="relative flex h-7 w-7 items-center justify-center rounded text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-200"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
            <path d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          {activeAlerts > 0 && (
            <span className="absolute right-0.5 top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-red-500 font-mono text-[8px] font-bold text-white">
              {activeAlerts}
            </span>
          )}
        </button>

        {alertsOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setAlertsOpen(false)} />
            <div className="absolute right-4 top-[52px] z-20 w-80 rounded-2xl border border-slate-800 bg-slate-900 shadow-xl">
              <div className="border-b border-slate-800 px-4 py-2.5">
                <p className="text-xs font-semibold text-slate-300">
                  Active Alerts {alerts.length === 0 && <span className="text-slate-600 font-normal">(none)</span>}
                </p>
              </div>
              <div className="max-h-64 overflow-y-auto">
                {alerts.length === 0 ? (
                  <p className="px-4 py-6 text-center text-[11px] text-slate-600">All systems healthy</p>
                ) : alerts.map(alert => {
                  const dotColor =
                    alert.severity === 'critical' ? 'bg-red-500' :
                    alert.severity === 'warning'  ? 'bg-amber-500' :
                    'bg-blue-500'
                  return (
                    <div key={alert.id} className="border-b border-slate-800/50 px-4 py-2.5 last:border-0">
                      <div className="flex items-start gap-2.5">
                        <span className={`mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full ${dotColor}`} />
                        <div className="min-w-0 flex-1">
                          {alert.platform && (
                            <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{alert.platform}</p>
                          )}
                          <p className="text-xs font-medium text-slate-200">{alert.message}</p>
                          <p className="mt-0.5 text-[11px] text-slate-500">{alert.time}</p>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          </>
        )}
      </div>
    </header>
  )
}
