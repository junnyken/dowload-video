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
  healthy:   'border-success/30 bg-success-soft text-success',
  success:   'border-success/30 bg-success-soft text-success',
  closed:    'border-success/30 bg-success-soft text-success',
  done:      'border-success/30 bg-success-soft text-success',
  // amber — warn
  degraded:  'border-warning/30  bg-warning-soft   text-warning',
  warning:   'border-warning/30  bg-warning-soft   text-warning',
  half:      'border-accent/30  bg-accent-soft   text-accent-text',
  stuck:     'border-accent/30  bg-accent-soft   text-accent-text',
  // red — bad
  critical:  'border-danger/30    bg-danger-soft     text-danger',
  error:     'border-danger/30    bg-danger-soft     text-danger',
  open:      'border-danger/30    bg-danger-soft     text-danger',
  // blue — info / in-progress
  info:      'border-line   bg-surface-2    text-fg-2',
  pending:   'border-line   bg-surface-2    text-fg-2',
  running:   'border-line   bg-surface-2    text-fg-2',
  // slate — neutral / off
  unknown:   'border-line  bg-surface   text-fg-muted',
  disabled:  'border-line  bg-surface   text-fg-muted',
  exempt:    'border-line  bg-surface   text-fg-muted',
  cancelled: 'border-line  bg-surface   text-fg-muted',
}

const DOT_STYLE: Record<PillStatus, string> = {
  healthy:   'bg-success animate-pulse',
  success:   'bg-success',
  closed:    'bg-success',
  done:      'bg-success',
  degraded:  'bg-warning animate-pulse',
  warning:   'bg-warning',
  half:      'bg-accent animate-pulse',
  stuck:     'bg-accent animate-pulse',
  critical:  'bg-danger animate-pulse',
  error:     'bg-danger',
  open:      'bg-danger',
  info:      'bg-accent',
  pending:   'bg-accent animate-pulse',
  running:   'bg-accent animate-pulse',
  unknown:   'bg-line',
  disabled:  'bg-surface-2',
  exempt:    'bg-line',
  cancelled: 'bg-surface-2',
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
