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
  if (score >= 80) return { text: 'text-emerald-400', bg: 'bg-emerald-950', border: 'border-emerald-900', bar: 'bg-emerald-500', label: 'Healthy'  }
  if (score >= 60) return { text: 'text-amber-400',   bg: 'bg-amber-950',   border: 'border-amber-900',   bar: 'bg-amber-500',   label: 'Good'     }
  if (score >= 40) return { text: 'text-amber-400',   bg: 'bg-amber-950/50',border: 'border-amber-900/50',bar: 'bg-amber-400',   label: 'Fair'     }
  if (score >= 20) return { text: 'text-orange-400',  bg: 'bg-orange-950',  border: 'border-orange-900',  bar: 'bg-orange-500',  label: 'Poor'     }
  return            { text: 'text-red-400',    bg: 'bg-red-950',     border: 'border-red-900',     bar: 'bg-red-500',     label: 'Critical' }
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
        <div className="h-1.5 w-14 flex-shrink-0 overflow-hidden rounded-full bg-slate-800">
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
      <span className="text-slate-700">·</span>
      <span className={cn('text-[9px] uppercase tracking-wide', t.text)}>{t.label}</span>
    </span>
  )
}

// ─── Cookie status pill ────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<CookieStatus, { style: string; label: string; dot: string }> = {
  active:       { style: 'text-emerald-400 bg-emerald-950 border-emerald-900', label: 'Active',     dot: 'bg-emerald-400 animate-pulse' },
  soft_blocked: { style: 'text-amber-400   bg-amber-950   border-amber-900',   label: 'Soft Block', dot: 'bg-amber-400 animate-pulse'   },
  hard_blocked: { style: 'text-red-400     bg-red-950     border-red-900',     label: 'Hard Block', dot: 'bg-red-400 animate-pulse'     },
  expired:      { style: 'text-slate-500   bg-slate-800   border-slate-700',   label: 'Expired',    dot: 'bg-slate-600'                 },
  disabled:     { style: 'text-slate-500   bg-slate-800   border-slate-700',   label: 'Disabled',   dot: 'bg-slate-700'                 },
  untested:     { style: 'text-blue-400    bg-blue-950    border-blue-900',    label: 'Untested',   dot: 'bg-blue-400'                  },
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
