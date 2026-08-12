import { cn } from '../../utils/cn'
import type { PlatformStatus, CircuitState } from './platform.types'

// ─── Status badge (Status column) ─────────────────────────────────────────────

const STATUS_CONFIG: Record<
  PlatformStatus,
  { dot: string; text: string; label: string }
> = {
  healthy:  { dot: 'bg-emerald-400 animate-pulse', text: 'text-emerald-400', label: 'Operational' },
  warning:  { dot: 'bg-amber-400   animate-pulse', text: 'text-amber-400',   label: 'Degraded'    },
  critical: { dot: 'bg-red-400     animate-pulse', text: 'text-red-400',     label: 'Critical'    },
  disabled: { dot: 'bg-slate-600',                 text: 'text-slate-500',   label: 'Disabled'    },
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
  closed: { style: 'text-emerald-400 bg-emerald-950 border-emerald-900', label: 'CLOSED'    },
  open:   { style: 'text-red-400    bg-red-950     border-red-900',     label: 'OPEN'      },
  half:   { style: 'text-amber-400  bg-amber-950   border-amber-900',   label: 'HALF'      },
  exempt: { style: 'text-slate-400  bg-slate-800   border-slate-700',   label: 'EXEMPT'    },
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
  youtube:    { abbr: 'YT', abbr_color: 'text-red-300',    abbr_bg: 'bg-red-950/80'    },
  instagram:  { abbr: 'IG', abbr_color: 'text-pink-300',   abbr_bg: 'bg-pink-950/80'   },
  tiktok:     { abbr: 'TK', abbr_color: 'text-sky-300',    abbr_bg: 'bg-sky-950/80'    },
  twitter:    { abbr: 'TW', abbr_color: 'text-slate-300',  abbr_bg: 'bg-slate-800'     },
  facebook:   { abbr: 'FB', abbr_color: 'text-blue-300',   abbr_bg: 'bg-blue-950/80'   },
  bilibili:   { abbr: 'BB', abbr_color: 'text-cyan-300',   abbr_bg: 'bg-cyan-950/80'   },
  douyin:     { abbr: 'DY', abbr_color: 'text-slate-300',  abbr_bg: 'bg-slate-800'     },
  soundcloud: { abbr: 'SC', abbr_color: 'text-orange-300', abbr_bg: 'bg-orange-950/80' },
  pinterest:  { abbr: 'PT', abbr_color: 'text-rose-300',   abbr_bg: 'bg-rose-950/80'   },
  reddit:     { abbr: 'RD', abbr_color: 'text-orange-300', abbr_bg: 'bg-orange-950/70' },
  vimeo:      { abbr: 'VM', abbr_color: 'text-blue-300',   abbr_bg: 'bg-blue-950/70'   },
  threads:    { abbr: 'TH', abbr_color: 'text-slate-300',  abbr_bg: 'bg-slate-800'     },
}

function getFallbackMeta(platform: string) {
  const abbr = platform.slice(0, 2).toUpperCase()
  return { abbr, abbr_color: 'text-slate-400', abbr_bg: 'bg-slate-800' }
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
        <p className="text-xs font-semibold capitalize text-slate-200">{platform}</p>
        {activeJobs > 0 && (
          <p className="font-mono text-[9px] text-slate-600">
            {activeJobs} active
          </p>
        )}
      </div>
    </div>
  )
}
