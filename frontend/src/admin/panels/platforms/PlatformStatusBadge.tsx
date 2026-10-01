import { cn } from '../../utils/cn'
import type { PlatformStatus, CircuitState } from './platform.types'

// ─── Status badge (Status column) ─────────────────────────────────────────────

const STATUS_CONFIG: Record<
  PlatformStatus,
  { dot: string; text: string; label: string }
> = {
  healthy:  { dot: 'bg-success animate-pulse', text: 'text-success', label: 'Operational' },
  warning:  { dot: 'bg-warning   animate-pulse', text: 'text-warning',   label: 'Degraded'    },
  critical: { dot: 'bg-danger     animate-pulse', text: 'text-danger',     label: 'Critical'    },
  disabled: { dot: 'bg-line',                 text: 'text-fg-muted',   label: 'Disabled'    },
}

interface PlatformStatusBadgeProps {
  status: PlatformStatus
  className?: string
}

export function PlatformStatusBadge({ status, className }: PlatformStatusBadgeProps) {
  const cfg = STATUS_CONFIG[status]
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className={cn('h-2 w-2 flex-shrink-0 rounded-full', cfg.dot)} />
      <span className={cn('text-xs font-medium', cfg.text)}>{cfg.label}</span>
    </span>
  )
}

// ─── Circuit state pill (Circuit column) ──────────────────────────────────────

const CIRCUIT_CONFIG: Record<
  CircuitState,
  { style: string; label: string }
> = {
  closed: { style: 'text-success bg-success-soft border-success/30', label: 'CLOSED'    },
  open:   { style: 'text-danger    bg-danger-soft     border-danger/30',     label: 'OPEN'      },
  half:   { style: 'text-accent-text  bg-accent-soft   border-accent/30',   label: 'HALF'      },
  exempt: { style: 'text-fg-muted  bg-surface   border-line',   label: 'EXEMPT'    },
}

interface CircuitStatePillProps {
  state: CircuitState
  size?: 'xs' | 'sm'
  className?: string
}

export function CircuitStatePill({ state, size = 'sm', className }: CircuitStatePillProps) {
  const cfg = CIRCUIT_CONFIG[state]
  return (
    <span
      className={cn(
        'inline-flex items-center rounded border font-mono font-semibold uppercase tracking-widest',
        size === 'xs' ? 'px-1.5 py-0.5 text-[9px]' : 'px-2 py-0.5 text-[10px]',
        cfg.style,
        className,
      )}
    >
      {cfg.label}
    </span>
  )
}

// ─── Platform name cell (Platform column) ─────────────────────────────────────

const PLATFORM_META: Record<string, { abbr: string; abbr_color: string; abbr_bg: string }> = {
  youtube:    { abbr: 'YT', abbr_color: 'text-danger',    abbr_bg: 'bg-danger-soft'    },
  instagram:  { abbr: 'IG', abbr_color: 'text-fg-2',   abbr_bg: 'bg-surface-2'   },
  tiktok:     { abbr: 'TK', abbr_color: 'text-fg-2',    abbr_bg: 'bg-surface-2'    },
  twitter:    { abbr: 'TW', abbr_color: 'text-fg-2',  abbr_bg: 'bg-surface'     },
  facebook:   { abbr: 'FB', abbr_color: 'text-fg-2',   abbr_bg: 'bg-surface-2'   },
  bilibili:   { abbr: 'BB', abbr_color: 'text-fg-2',   abbr_bg: 'bg-surface-2'   },
  douyin:     { abbr: 'DY', abbr_color: 'text-fg-2',  abbr_bg: 'bg-surface'     },
  soundcloud: { abbr: 'SC', abbr_color: 'text-accent-text', abbr_bg: 'bg-accent-soft' },
  pinterest:  { abbr: 'PT', abbr_color: 'text-danger',   abbr_bg: 'bg-danger-soft'   },
  reddit:     { abbr: 'RD', abbr_color: 'text-accent-text', abbr_bg: 'bg-accent-soft' },
  vimeo:      { abbr: 'VM', abbr_color: 'text-fg-2',   abbr_bg: 'bg-surface-2'   },
  threads:    { abbr: 'TH', abbr_color: 'text-fg-2',  abbr_bg: 'bg-surface'     },
}

function getFallbackMeta(platform: string) {
  const abbr = platform.slice(0, 2).toUpperCase()
  return { abbr, abbr_color: 'text-fg-muted', abbr_bg: 'bg-surface' }
}

interface PlatformNameCellProps {
  platform: string
  activeJobs?: number
}

export function PlatformNameCell({ platform, activeJobs = 0 }: PlatformNameCellProps) {
  const meta = PLATFORM_META[platform] ?? getFallbackMeta(platform)
  return (
    <div className="flex items-center gap-2.5">
      <span
        className={cn(
          'flex h-6 w-7 flex-shrink-0 items-center justify-center rounded font-mono text-[10px] font-bold',
          meta.abbr_color,
          meta.abbr_bg,
        )}
      >
        {meta.abbr}
      </span>
      <div>
        <p className="text-xs font-semibold capitalize text-fg-2">{platform}</p>
        {activeJobs > 0 && (
          <p className="font-mono text-[9px] text-fg-muted">
            {activeJobs} active
          </p>
        )}
      </div>
    </div>
  )
}
