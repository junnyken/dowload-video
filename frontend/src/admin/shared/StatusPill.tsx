import { cn } from '../utils/cn'

// ─── Unified status vocabulary ────────────────────────────────────────────────
//
// Covers:  health, circuit-breaker, job, severity, and cookie states.
// Legacy panels that used type="status"|"circuit"|"severity" + value= still work
// via the LEGACY_MAP fallback.

export type PillStatus =
  // health / system
  | 'healthy'  | 'degraded' | 'critical' | 'unknown'
  // circuit breaker
  | 'closed'   | 'open'     | 'half'     | 'exempt'
  // generic severity
  | 'success'  | 'warning'  | 'error'    | 'info'
  // lifecycle
  | 'pending'  | 'disabled' | 'running'  | 'done'
  | 'cancelled'| 'stuck'

// ─── Style maps ───────────────────────────────────────────────────────────────

const PILL_STYLE: Record<PillStatus, string> = {
  // emerald — good
  healthy:   'border-emerald-900 bg-emerald-950 text-emerald-400',
  success:   'border-emerald-900 bg-emerald-950 text-emerald-400',
  closed:    'border-emerald-900 bg-emerald-950 text-emerald-400',
  done:      'border-emerald-900 bg-emerald-950 text-emerald-400',
  // amber — warn
  degraded:  'border-amber-900  bg-amber-950   text-amber-400',
  warning:   'border-amber-900  bg-amber-950   text-amber-400',
  half:      'border-amber-900  bg-amber-950   text-amber-400',
  stuck:     'border-amber-900  bg-amber-950   text-amber-400',
  // red — bad
  critical:  'border-red-900    bg-red-950     text-red-400',
  error:     'border-red-900    bg-red-950     text-red-400',
  open:      'border-red-900    bg-red-950     text-red-400',
  // blue — info / in-progress
  info:      'border-blue-900   bg-blue-950    text-blue-400',
  pending:   'border-blue-900   bg-blue-950    text-blue-400',
  running:   'border-blue-800   bg-blue-900    text-blue-300',
  // slate — neutral / off
  unknown:   'border-slate-700  bg-slate-800   text-slate-500',
  disabled:  'border-slate-700  bg-slate-800   text-slate-500',
  exempt:    'border-slate-700  bg-slate-800   text-slate-500',
  cancelled: 'border-slate-700  bg-slate-800   text-slate-500',
}

const DOT_STYLE: Record<PillStatus, string> = {
  healthy:   'bg-emerald-400 animate-pulse',
  success:   'bg-emerald-400',
  closed:    'bg-emerald-400',
  done:      'bg-emerald-400',
  degraded:  'bg-amber-400 animate-pulse',
  warning:   'bg-amber-400',
  half:      'bg-amber-400 animate-pulse',
  stuck:     'bg-amber-400 animate-pulse',
  critical:  'bg-red-400 animate-pulse',
  error:     'bg-red-400',
  open:      'bg-red-400',
  info:      'bg-blue-400',
  pending:   'bg-blue-400 animate-pulse',
  running:   'bg-blue-400 animate-pulse',
  unknown:   'bg-slate-600',
  disabled:  'bg-slate-700',
  exempt:    'bg-slate-600',
  cancelled: 'bg-slate-700',
}

const DEFAULT_LABEL: Partial<Record<PillStatus, string>> = {
  closed: 'CLOSED', open: 'OPEN', half: 'HALF', exempt: 'EXEMPT',
  done:   'DONE',   cancelled: 'CANCELLED',
}

// ─── Legacy value mapping (old panels: type="status"|"circuit" + value=) ──────

const LEGACY_MAP: Record<string, PillStatus> = {
  ok:   'healthy',
  warn: 'degraded',
}

function resolveStatus(
  status?:      PillStatus,
  legacyType?:  string,
  legacyValue?: string,
): PillStatus {
  if (status) return status
  if (legacyValue) return (LEGACY_MAP[legacyValue] ?? legacyValue) as PillStatus
  return 'unknown'
}

// ─── Component ────────────────────────────────────────────────────────────────

interface StatusPillProps {
  /** Preferred API */
  status?: PillStatus
  /** Legacy API — paired with `value` */
  type?:  string
  /** Legacy API — paired with `type` */
  value?: string
  /** Override display label */
  label?:     string
  size?:      'xs' | 'sm' | 'md'
  dot?:       boolean
  className?: string
}

export function StatusPill({
  status,
  type,
  value,
  label,
  size      = 'sm',
  dot       = false,
  className,
}: StatusPillProps) {
  const resolved     = resolveStatus(status, type, value)
  const displayLabel = label ?? DEFAULT_LABEL[resolved] ?? resolved.toUpperCase()

  return (
    <span
      role="status"
      aria-label={displayLabel.toLowerCase()}
      className={cn(
        'inline-flex items-center gap-1 rounded border font-mono font-semibold uppercase tracking-widest',
        size === 'xs' && 'px-1.5 py-px  text-[9px]',
        size === 'sm' && 'px-2   py-0.5 text-[10px]',
        size === 'md' && 'px-2.5 py-1   text-xs',
        PILL_STYLE[resolved],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden
          className={cn(
            'flex-shrink-0 rounded-full',
            size === 'xs' ? 'h-1.5 w-1.5' : 'h-2 w-2',
            DOT_STYLE[resolved],
          )}
        />
      )}
      {displayLabel}
    </span>
  )
}
