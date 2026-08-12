import { cn } from '../../utils/cn'
import { EmptyState } from '../../shared/EmptyState'
import { ErrorState } from '../../shared/ErrorState'
import { TableSkeleton } from '../../shared/LoadingSkeleton'
import type { CookieItem, CookieAction } from './cookie.types'
import { CookieHealthBadge, CookieStatusPill } from './CookieHealthBadge'
import { CookieActionsMenu } from './CookieActionsMenu'

// ─── Platform abbreviation badge (local, no cross-panel dep) ──────────────────

const PLAT_COLORS: Record<string, { text: string; bg: string }> = {
  youtube:    { text: 'text-red-300',    bg: 'bg-red-950/70'    },
  instagram:  { text: 'text-pink-300',   bg: 'bg-pink-950/70'   },
  tiktok:     { text: 'text-sky-300',    bg: 'bg-sky-950/70'    },
  twitter:    { text: 'text-slate-300',  bg: 'bg-slate-800'     },
  facebook:   { text: 'text-blue-300',   bg: 'bg-blue-950/70'   },
  bilibili:   { text: 'text-cyan-300',   bg: 'bg-cyan-950/70'   },
  douyin:     { text: 'text-slate-300',  bg: 'bg-slate-800'     },
  soundcloud: { text: 'text-orange-300', bg: 'bg-orange-950/70' },
  pinterest:  { text: 'text-rose-300',   bg: 'bg-rose-950/70'   },
  reddit:     { text: 'text-orange-300', bg: 'bg-orange-950/60' },
  vimeo:      { text: 'text-blue-300',   bg: 'bg-blue-950/60'   },
  threads:    { text: 'text-slate-300',  bg: 'bg-slate-800'     },
}

function PlatBadge({ platform }: { platform: string }) {
  const meta = PLAT_COLORS[platform] ?? { text: 'text-slate-400', bg: 'bg-slate-800' }
  const abbr = platform.slice(0, 2).toUpperCase()
  return (
    <div className="flex items-center gap-2">
      <span
        className={cn(
          'flex h-6 w-7 flex-shrink-0 items-center justify-center rounded font-mono text-[10px] font-bold',
          meta.text,
          meta.bg,
        )}
      >
        {abbr}
      </span>
      <span className="text-xs font-medium capitalize text-slate-300">{platform}</span>
    </div>
  )
}

// ─── Cooldown cell ─────────────────────────────────────────────────────────────

function formatCooldown(secs: number): string {
  if (secs <= 0) return '—'
  if (secs < 60) return `${secs}s`
  const m = Math.floor(secs / 60)
  const s = secs % 60
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return rem > 0 ? `${h}h ${rem}m` : `${h}h`
}

function CooldownCell({ secs, status }: { secs: number; status: CookieItem['status'] }) {
  if (secs <= 0) return <span className="text-[11px] text-slate-700">—</span>

  const isHard = status === 'hard_blocked'
  return (
    <span
      className={cn(
        'font-mono text-[11px] font-medium tabular-nums',
        isHard ? 'text-red-400' : 'text-amber-400',
      )}
    >
      {formatCooldown(secs)}
    </span>
  )
}

// ─── Column definitions ────────────────────────────────────────────────────────

const COLS = [
  { key: 'platform',      label: 'Platform',         w: 'min-w-[130px]' },
  { key: 'accountLabel',  label: 'Account Label',    w: 'min-w-[160px]' },
  { key: 'status',        label: 'Status',           w: 'min-w-[110px]' },
  { key: 'healthScore',   label: 'Health Score',     w: 'min-w-[130px]' },
  { key: 'lastSuccessAt', label: 'Last Success',     w: 'min-w-[100px]' },
  { key: 'lastFailAt',    label: 'Last Fail',        w: 'min-w-[100px]' },
  { key: 'failCount',     label: 'Fail Count',       w: 'min-w-[80px]'  },
  { key: 'cooldown',      label: 'Cooldown',         w: 'min-w-[100px]' },
  { key: 'expiry',        label: 'Expiry Est.',      w: 'min-w-[100px]' },
  { key: 'actions',       label: '',                 w: 'w-10'          },
]

// ─── Main table ────────────────────────────────────────────────────────────────

interface CookieTableProps {
  cookies: CookieItem[]
  loading?: boolean
  error?: string
  onRetry?: () => void
  onAction?: (id: string, action: CookieAction) => void
  onAddCookie?: () => void
}

export function CookieTable({
  cookies,
  loading,
  error,
  onRetry,
  onAction,
  onAddCookie,
}: CookieTableProps) {
  function handleAction(id: string, action: CookieAction) {
    onAction?.(id, action)
  }

  if (loading) {
    return (
      <div className="px-4 py-3">
        <TableSkeleton rows={6} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="px-4 py-3">
        <ErrorState message={error} onRetry={onRetry} />
      </div>
    )
  }

  if (cookies.length === 0) {
    return (
      <EmptyState
        title="No cookies in pool"
        description="Add a session cookie to enable authenticated downloads for this platform."
        action={
          onAddCookie && (
            <button
              onClick={onAddCookie}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-500"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
              Add First Cookie
            </button>
          )
        }
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-[1080px] w-full text-sm">
        {/* Sticky header */}
        <thead className="sticky top-0 z-10 bg-slate-900">
          <tr className="border-y border-slate-800">
            {COLS.map(col => (
              <th
                key={col.key}
                className={cn(
                  'py-2 pr-3 text-left font-mono text-[9px] font-semibold uppercase tracking-widest text-slate-600 first:pl-4',
                  col.w,
                  col.key === 'actions' && 'text-center',
                )}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {cookies.map((cookie, i) => {
            const isLast = i === cookies.length - 1
            const isDim  = cookie.status === 'disabled' || cookie.status === 'expired'

            return (
              <tr
                key={cookie.id}
                className={cn(
                  'transition-colors hover:bg-slate-800/30',
                  !isLast && 'border-b border-slate-800/50',
                  isDim && 'opacity-60',
                )}
              >
                {/* Platform */}
                <td className="py-2.5 pl-4 pr-3">
                  <PlatBadge platform={cookie.platform} />
                </td>

                {/* Account Label */}
                <td className="py-2.5 pr-3">
                  <span className="font-mono text-[11px] text-slate-300">{cookie.accountLabel}</span>
                </td>

                {/* Status */}
                <td className="py-2.5 pr-3">
                  <CookieStatusPill status={cookie.status} dot />
                </td>

                {/* Health Score */}
                <td className="py-2.5 pr-3">
                  <CookieHealthBadge score={cookie.healthScore} />
                </td>

                {/* Last Success */}
                <td className="py-2.5 pr-3">
                  <span className="font-mono text-[11px] text-slate-500">
                    {cookie.lastSuccessAt}
                  </span>
                </td>

                {/* Last Fail */}
                <td className="py-2.5 pr-3">
                  <span className={cn('font-mono text-[11px]', cookie.lastFailAt ? 'text-red-500/80' : 'text-slate-700')}>
                    {cookie.lastFailAt ?? '—'}
                  </span>
                </td>

                {/* Fail Count */}
                <td className="py-2.5 pr-3">
                  <span
                    className={cn(
                      'font-mono text-xs font-semibold tabular-nums',
                      cookie.failCount === 0
                        ? 'text-slate-700'
                        : cookie.failCount >= 7
                          ? 'text-red-400'
                          : cookie.failCount >= 3
                            ? 'text-amber-400'
                            : 'text-slate-400',
                    )}
                  >
                    {cookie.failCount === 0 ? '—' : cookie.failCount}
                  </span>
                </td>

                {/* Cooldown */}
                <td className="py-2.5 pr-3">
                  <CooldownCell secs={cookie.cooldownRemainingSec} status={cookie.status} />
                </td>

                {/* Expiry Estimate */}
                <td className="py-2.5 pr-3">
                  <span
                    className={cn(
                      'font-mono text-[11px]',
                      cookie.expiryEstimate === 'Expired'
                        ? 'text-red-400'
                        : cookie.expiryEstimate === 'Unknown'
                          ? 'text-slate-600'
                          : cookie.expiryEstimate.startsWith('~1') || cookie.expiryEstimate.startsWith('~2')
                            ? 'text-amber-400'
                            : 'text-slate-500',
                    )}
                  >
                    {cookie.expiryEstimate}
                  </span>
                </td>

                {/* Actions */}
                <td
                  className="py-2.5 pr-4"
                  onClick={e => e.stopPropagation()}
                >
                  <CookieActionsMenu cookie={cookie} onAction={handleAction} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
