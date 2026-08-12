import { useState } from 'react'
import { cn } from '../utils/cn'

export type AlertSeverity = 'critical' | 'warning' | 'info'

export interface AlertItem {
  id: string
  severity: AlertSeverity
  message: string
  time: string
  platform?: string
}

const SEV: Record<
  AlertSeverity,
  { bg: string; border: string; text: string; sub: string; iconPath: string; iconColor: string }
> = {
  critical: {
    bg:        'bg-red-950/40',
    border:    'border-red-900/50',
    text:      'text-red-300',
    sub:       'text-red-500/80',
    iconPath:  'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z',
    iconColor: 'text-red-400',
  },
  warning: {
    bg:        'bg-amber-950/30',
    border:    'border-amber-900/50',
    text:      'text-amber-300',
    sub:       'text-amber-600',
    iconPath:  'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    iconColor: 'text-amber-400',
  },
  info: {
    bg:        'bg-blue-950/25',
    border:    'border-blue-900/40',
    text:      'text-blue-300',
    sub:       'text-blue-500/80',
    iconPath:  'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    iconColor: 'text-blue-400',
  },
}

const SEV_ORDER: AlertSeverity[] = ['critical', 'warning', 'info']

interface ActiveAlertsBannerProps {
  alerts: AlertItem[]
  onDismiss?: (id: string) => void
}

export function ActiveAlertsBanner({ alerts, onDismiss }: ActiveAlertsBannerProps) {
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())

  const visible = [...alerts]
    .filter(a => !dismissed.has(a.id))
    .sort((a, b) => SEV_ORDER.indexOf(a.severity) - SEV_ORDER.indexOf(b.severity))

  if (visible.length === 0) return null

  function dismiss(id: string) {
    setDismissed(prev => new Set([...prev, id]))
    onDismiss?.(id)
  }

  return (
    <div className="flex flex-col gap-1.5">
      {visible.map(alert => {
        const s = SEV[alert.severity]
        return (
          <div
            key={alert.id}
            className={cn(
              'flex items-start gap-3 rounded-xl border px-3 py-2.5',
              s.bg,
              s.border,
            )}
          >
            {/* Icon */}
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.75}
              strokeLinecap="round"
              strokeLinejoin="round"
              className={cn('mt-0.5 h-4 w-4 flex-shrink-0', s.iconColor)}
            >
              <path d={s.iconPath} />
            </svg>

            {/* Content */}
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <span
                  className={cn(
                    'flex-shrink-0 font-mono text-[9px] font-bold uppercase tracking-widest',
                    s.iconColor,
                  )}
                >
                  {alert.severity}
                </span>
                {alert.platform && (
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-slate-400">
                    {alert.platform}
                  </span>
                )}
              </div>
              <p className={cn('mt-0.5 text-xs', s.text)}>{alert.message}</p>
            </div>

            {/* Time + dismiss */}
            <div className="flex flex-shrink-0 items-center gap-2">
              <span className={cn('font-mono text-[10px]', s.sub)}>{alert.time}</span>
              <button
                onClick={() => dismiss(alert.id)}
                className="flex h-5 w-5 items-center justify-center rounded text-slate-600 hover:text-slate-300"
                aria-label="Dismiss alert"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3 w-3">
                  <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
