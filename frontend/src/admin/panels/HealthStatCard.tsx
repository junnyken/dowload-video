import { cn } from '../utils/cn'
import { CardSkeleton } from '../shared/LoadingSkeleton'
import { ErrorState } from '../shared/ErrorState'

export type StatTone = 'green' | 'yellow' | 'red' | 'blue' | 'neutral'

const TONE: Record<StatTone, { border: string; value: string; iconBg: string; iconText: string }> = {
  green:   { border: 'border-emerald-900/50', value: 'text-emerald-400', iconBg: 'bg-emerald-950', iconText: 'text-emerald-400' },
  yellow:  { border: 'border-amber-900/50',   value: 'text-amber-400',   iconBg: 'bg-amber-950',   iconText: 'text-amber-400'   },
  red:     { border: 'border-red-900/50',     value: 'text-red-400',     iconBg: 'bg-red-950',     iconText: 'text-red-400'     },
  blue:    { border: 'border-blue-900/50',    value: 'text-blue-400',    iconBg: 'bg-blue-950',    iconText: 'text-blue-400'    },
  neutral: { border: 'border-slate-800',      value: 'text-slate-200',   iconBg: 'bg-slate-800',   iconText: 'text-slate-400'   },
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
  const color = isGood ? 'text-emerald-400' : 'text-red-400'
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
      <div className={cn('rounded-2xl border border-slate-800 bg-slate-900/70 p-4', className)}>
        <ErrorState message={error} />
      </div>
    )
  }

  return (
    <div className={cn('rounded-2xl border bg-slate-900/70 p-4', t.border, className)}>
      {/* Label row */}
      <div className="mb-3 flex items-start justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">
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
          <p className="text-[11px] text-slate-500">{subvalue}</p>
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
