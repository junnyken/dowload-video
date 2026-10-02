import { cn } from '../utils/cn'
import { LoadingSkeleton } from '../shared/LoadingSkeleton'
import { ErrorState } from '../shared/ErrorState'

export type SystemStatus = 'healthy' | 'degraded' | 'critical' | 'unknown'

export interface SystemStatusData {
  status: SystemStatus
  updatedAt: string
  activeAlerts: number
  queuedJobs: number
  failedJobs24h: number
  successRate24h: number  // 0–100
}

const STATUS_CONFIG: Record<
  SystemStatus,
  { label: string; dot: string; pill: string; border: string; bg: string }
> = {
  healthy: {
    label: 'All Systems Operational',
    dot:   'bg-success animate-pulse',
    pill:  'text-success bg-success-soft border-success/30',
    border:'border-success/30',
    bg:    'bg-success-soft',
  },
  degraded: {
    label: 'Partial Degradation Detected',
    dot:   'bg-accent animate-pulse',
    pill:  'text-warning bg-warning-soft border-warning/30',
    border:'border-warning/30',
    bg:    'bg-warning-soft',
  },
  critical: {
    label: 'Critical — Immediate Action Required',
    dot:   'bg-danger animate-pulse',
    pill:  'text-danger bg-danger-soft border-danger/30',
    border:'border-danger/30',
    bg:    'bg-danger-soft',
  },
  unknown: {
    label: 'Status Unknown',
    dot:   'bg-line-strong',
    pill:  'text-fg-muted bg-surface border-line',
    border:'border-line',
    bg:    '',
  },
}

interface QuickStatProps {
  label: string
  value: string | number
  tone?: 'neutral' | 'red' | 'amber' | 'emerald' | 'blue'
}

function QuickStat({ label, value, tone = 'neutral' }: QuickStatProps) {
  const textColor =
    tone === 'red'     ? 'text-danger' :
    tone === 'amber'   ? 'text-warning' :
    tone === 'emerald' ? 'text-success' :
    tone === 'blue'    ? 'text-fg-2' :
    'text-fg-2'

  return (
    <div className="flex flex-col gap-0.5 border-l border-line pl-4 first:border-l-0 first:pl-0">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-fg-muted">{label}</p>
      <p className={cn('font-mono text-lg font-bold leading-none tabular-nums', textColor)}>
        {value}
      </p>
    </div>
  )
}

interface SystemStatusOverviewProps extends SystemStatusData {
  loading?: boolean
  error?: string
  onRefresh?: () => void
}

export function SystemStatusOverview({
  status,
  updatedAt,
  activeAlerts,
  queuedJobs,
  failedJobs24h,
  successRate24h,
  loading,
  error,
  onRefresh,
}: SystemStatusOverviewProps) {
  const cfg = STATUS_CONFIG[status]

  return (
    <div
      className={cn(
        'rounded-card border p-4',
        cfg.border,
        cfg.bg,
      )}
    >
      {loading ? (
        <LoadingSkeleton lines={2} className="py-2" />
      ) : error ? (
        <ErrorState message={error} onRetry={onRefresh} />
      ) : (
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          {/* Status badge + label */}
          <div className="flex items-center gap-3">
            <span
              className={cn(
                'inline-flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-xs font-bold uppercase tracking-widest',
                cfg.pill,
              )}
            >
              <span className={cn('h-2 w-2 rounded-full flex-shrink-0', cfg.dot)} />
              {status}
            </span>
            <p className="text-sm font-medium text-fg-2">{cfg.label}</p>
          </div>

          {/* Quick stats row */}
          <div className="flex items-start gap-4 sm:gap-6">
            <QuickStat
              label="Active Alerts"
              value={activeAlerts}
              tone={activeAlerts > 0 ? (activeAlerts >= 3 ? 'red' : 'amber') : 'neutral'}
            />
            <QuickStat
              label="Queued Jobs"
              value={queuedJobs}
              tone="blue"
            />
            <QuickStat
              label="Failed 24h"
              value={failedJobs24h}
              tone={failedJobs24h === 0 ? 'emerald' : failedJobs24h > 10 ? 'red' : 'amber'}
            />
            <QuickStat
              label="Success Rate"
              value={`${successRate24h.toFixed(1)}%`}
              tone={successRate24h >= 99 ? 'emerald' : successRate24h >= 95 ? 'neutral' : 'amber'}
            />
          </div>

          {/* Refresh + timestamp */}
          <div className="flex flex-shrink-0 items-center gap-2">
            <p className="font-mono text-[10px] text-fg-muted">
              Updated {updatedAt}
            </p>
            {onRefresh && (
              <button
                onClick={onRefresh}
                className="flex h-6 w-6 items-center justify-center rounded text-fg-muted hover:bg-surface hover:text-fg-2"
                aria-label="Refresh status"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.75}
                  className="h-3.5 w-3.5"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                  />
                </svg>
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
