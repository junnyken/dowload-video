import { cn } from '../../utils/cn'
import type { CookieStatus } from './cookie.types'

// ─── Health score badge ────────────────────────────────────────────────────────

interface HealthTone {
  text: string
  bg: string
  border: string
  bar: string
  label: string
}

function getHealthTone(score: number): HealthTone {
  if (score >= 80) return { text: 'text-success', bg: 'bg-success-soft', border: 'border-success/30', bar: 'bg-success', label: 'Healthy'  }
  if (score >= 60) return { text: 'text-accent-text',   bg: 'bg-accent-soft',   border: 'border-accent/30',   bar: 'bg-accent',   label: 'Good'     }
  if (score >= 40) return { text: 'text-accent-text',   bg: 'bg-accent-soft',border: 'border-accent/50',bar: 'bg-accent',   label: 'Fair'     }
  if (score >= 20) return { text: 'text-accent-text',  bg: 'bg-accent-soft',  border: 'border-accent/30',  bar: 'bg-accent',  label: 'Poor'     }
  return            { text: 'text-danger',    bg: 'bg-danger-soft',     border: 'border-danger/30',     bar: 'bg-danger',     label: 'Critical' }
}

interface CookieHealthBadgeProps {
  score: number
  showBar?: boolean
  className?: string
}

export function CookieHealthBadge({ score, showBar = false, className }: CookieHealthBadgeProps) {
  const t = getHealthTone(score)

  if (showBar) {
    return (
      <div className={cn('flex items-center gap-2', className)}>
        <div className="h-1.5 w-14 flex-shrink-0 overflow-hidden rounded-full bg-surface">
          <div className={cn('h-full rounded-full transition-all', t.bar)} style={{ width: `${score}%` }} />
        </div>
        <span className={cn('w-7 font-mono text-[11px] tabular-nums', t.text)}>{score}</span>
      </div>
    )
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded border font-mono text-[10px] font-bold',
        'px-2 py-0.5',
        t.bg, t.border, className,
      )}
    >
      <span className={t.text}>{score}</span>
      <span className="text-fg-muted">·</span>
      <span className={cn('text-[9px] uppercase tracking-wide', t.text)}>{t.label}</span>
    </span>
  )
}

// ─── Cookie status pill ────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<CookieStatus, { style: string; label: string; dot: string }> = {
  active:       { style: 'text-success bg-success-soft border-success/30', label: 'Active',     dot: 'bg-success animate-pulse' },
  soft_blocked: { style: 'text-accent-text   bg-accent-soft   border-accent/30',   label: 'Soft Block', dot: 'bg-accent animate-pulse'   },
  hard_blocked: { style: 'text-danger     bg-danger-soft     border-danger/30',     label: 'Hard Block', dot: 'bg-danger animate-pulse'     },
  expired:      { style: 'text-fg-muted   bg-surface   border-line',   label: 'Expired',    dot: 'bg-line'                 },
  disabled:     { style: 'text-fg-muted   bg-surface   border-line',   label: 'Disabled',   dot: 'bg-surface-2'                 },
  untested:     { style: 'text-fg-2    bg-surface-2    border-line',    label: 'Untested',   dot: 'bg-accent'                  },
}

interface CookieStatusPillProps {
  status: CookieStatus
  dot?: boolean
  size?: 'xs' | 'sm'
  className?: string
}

export function CookieStatusPill({ status, dot = false, size = 'sm', className }: CookieStatusPillProps) {
  const cfg = STATUS_CONFIG[status]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded border font-mono font-semibold uppercase tracking-widest',
        size === 'xs' ? 'px-1.5 py-0.5 text-[9px]' : 'px-2 py-0.5 text-[10px]',
        cfg.style,
        className,
      )}
    >
      {dot && <span className={cn('flex-shrink-0 rounded-full', size === 'xs' ? 'h-1.5 w-1.5' : 'h-2 w-2', cfg.dot)} />}
      {cfg.label}
    </span>
  )
}
