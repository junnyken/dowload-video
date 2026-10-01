import { cn } from '../utils/cn'
import { CardSkeleton } from '../shared/LoadingSkeleton'
import { ErrorState } from '../shared/ErrorState'

export type StatTone = 'green' | 'yellow' | 'red' | 'blue' | 'neutral'

const TONE: Record<StatTone, { border: string; value: string; iconBg: string; iconText: string }> = {
  green:   { border: 'border-success/50', value: 'text-success', iconBg: 'bg-success-soft', iconText: 'text-success' },
  yellow:  { border: 'border-accent/50',   value: 'text-accent-text',   iconBg: 'bg-accent-soft',   iconText: 'text-accent-text'   },
  red:     { border: 'border-danger/50',     value: 'text-danger',     iconBg: 'bg-danger-soft',     iconText: 'text-danger'     },
  blue:    { border: 'border-line',    value: 'text-fg-2',    iconBg: 'bg-surface-2',    iconText: 'text-fg-2'    },
  neutral: { border: 'border-line',      value: 'text-fg-2',   iconBg: 'bg-surface',   iconText: 'text-fg-muted'   },
}

export interface HealthStatCardProps {
  label: string
  value: string | number
  subvalue?: string
  tone?: StatTone
  /** Direction the metric is moving */
  trend?: 'up' | 'down'
  trendLabel?: string
  /** True if the trend direction is a positive signal (e.g. up = more success = good) */
  trendPositive?: boolean
  /** SVG path d= for the corner icon */
  iconPath?: string
  loading?: boolean
  error?: string
  className?: string
}

function TrendIndicator({
  trend,
  label,
  trendPositive = true,
}: {
  trend: 'up' | 'down'
  label?: string
  trendPositive?: boolean
}) {
  const isGood = (trend === 'up') === trendPositive
  const color = isGood ? 'text-success' : 'text-danger'
  const arrowPath = trend === 'up' ? 'M5 15l7-7 7 7' : 'M19 9l-7 7-7-7'

  return (
    <span className={cn('flex items-center gap-0.5 text-[10px] font-medium', color)}>
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.5}
        strokeLinecap="round"
        className="h-3 w-3 flex-shrink-0"
      >
        <path d={arrowPath} />
      </svg>
      {label}
    </span>
  )
}

export function HealthStatCard({
  label,
  value,
  subvalue,
  tone = 'neutral',
  trend,
  trendLabel,
  trendPositive,
  iconPath,
  loading,
  error,
  className,
}: HealthStatCardProps) {
  const t = TONE[tone]

  if (loading) return <CardSkeleton className={className} />
  if (error) {
    return (
      <div className={cn('rounded-2xl border border-line bg-surface-2 p-4', className)}>
        <ErrorState message={error} />
      </div>
    )
  }

  return (
    <div className={cn('rounded-2xl border bg-surface-2 p-4', t.border, className)}>
      {/* Label row */}
      <div className="mb-3 flex items-start justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-fg-muted">
          {label}
        </p>
        {iconPath && (
          <span
            className={cn(
              'flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg',
              t.iconBg,
              t.iconText,
            )}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.75}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="h-4 w-4"
            >
              <path d={iconPath} />
            </svg>
          </span>
        )}
      </div>

      {/* Value */}
      <p className={cn('font-mono text-2xl font-bold leading-none tabular-nums', t.value)}>
        {value}
      </p>

      {/* Footer row */}
      <div className="mt-2 flex items-center gap-2">
        {subvalue && (
          <p className="text-[11px] text-fg-muted">{subvalue}</p>
        )}
        {trend && (
          <TrendIndicator
            trend={trend}
            label={trendLabel}
            trendPositive={trendPositive}
          />
        )}
      </div>
    </div>
  )
}
