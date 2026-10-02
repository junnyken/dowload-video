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
//
// One palette for the whole admin: success / warning / danger / neutral soft
// variants, all from the semantic tokens. `accent` is reserved for the
// in-progress dot so orange stays rare.

export type PillTone = 'success' | 'warning' | 'danger' | 'neutral'

export const PILL_TONE_STYLE: Record<PillTone, string> = {
  success: 'border-success/30 bg-success-soft text-success',
  warning: 'border-warning/30 bg-warning-soft text-warning',
  danger:  'border-danger/30 bg-danger-soft text-danger',
  neutral: 'border-line bg-surface-2 text-fg-2',
}

const PILL_TONE: Record<PillStatus, PillTone> = {
  healthy: 'success', success: 'success', closed: 'success', done: 'success',
  degraded: 'warning', warning: 'warning', half: 'warning', stuck: 'warning',
  critical: 'danger', error: 'danger', open: 'danger',
  info: 'neutral', pending: 'neutral', running: 'neutral',
  unknown: 'neutral', disabled: 'neutral', exempt: 'neutral', cancelled: 'neutral',
}

const DOT_TONE: Record<PillTone, string> = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger:  'bg-danger',
  neutral: 'bg-fg-muted',
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
  /** Pick a palette directly instead of a lifecycle status. */
  tone?:  PillTone
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
  tone,
  label,
  size      = 'sm',
  dot       = false,
  className,
}: StatusPillProps) {
  const resolved     = resolveStatus(status, type, value)
  const pillTone: PillTone = tone ?? PILL_TONE[resolved] ?? 'neutral'
  const displayLabel = label ?? DEFAULT_LABEL[resolved] ?? String(resolved).toUpperCase()
  const live         = !tone && (resolved === 'running' || resolved === 'pending')

  return (
    <span
      role="status"
      aria-label={displayLabel.toLowerCase()}
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border font-mono font-semibold uppercase tracking-wider',
        size === 'xs' && 'px-1.5 py-px text-[10px]',
        size === 'sm' && 'px-2 py-0.5 text-[10px]',
        size === 'md' && 'px-2.5 py-1 text-xs',
        PILL_TONE_STYLE[pillTone],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden
          className={cn(
            'flex-shrink-0 rounded-full',
            size === 'xs' ? 'h-1.5 w-1.5' : 'h-2 w-2',
            live ? 'bg-accent animate-pulse' : DOT_TONE[pillTone],
          )}
        />
      )}
      {displayLabel}
    </span>
  )
}
