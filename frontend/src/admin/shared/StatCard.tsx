import type { ReactNode } from 'react'
import { cn } from '../utils/cn'

export type StatValueTone = 'default' | 'success' | 'warning' | 'danger' | 'accent'

const VALUE_TONE: Record<StatValueTone, string> = {
  default: 'text-fg',
  success: 'text-success',
  warning: 'text-warning',
  danger:  'text-danger',
  accent:  'text-accent-text',
}

interface StatCardProps {
  label:      string
  value:      ReactNode
  hint?:      ReactNode
  tone?:      StatValueTone
  className?: string
}

/** Neutral stat: small mono label, large mono number, optional hint. */
export function StatCard({ label, value, hint, tone = 'default', className }: StatCardProps) {
  return (
    <div className={cn('rounded-card border border-line bg-surface p-4 shadow-card', className)}>
      <p className="font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted">{label}</p>
      <p className={cn('mt-2 font-mono text-2xl font-semibold leading-none tabular-nums', VALUE_TONE[tone])}>{value}</p>
      {hint && <p className="mt-2 text-[11px] text-fg-muted">{hint}</p>}
    </div>
  )
}
