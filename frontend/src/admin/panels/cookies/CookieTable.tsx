import { cn } from '../../utils/cn'
import { EmptyState } from '../../shared/EmptyState'
import { ErrorState } from '../../shared/ErrorState'
import { TableSkeleton } from '../../shared/LoadingSkeleton'
import type { CookieItem, CookieAction } from './cookie.types'
import { CookieHealthBadge, CookieStatusPill } from './CookieHealthBadge'
import { CookieActionsMenu } from './CookieActionsMenu'
import { describeLifetime, formatAddedDate, formatStamp } from './cookieLifetime'
import type { LiveTest } from './cookie.types'

const LIVE_STYLE: Record<LiveTest['state'], { text: string; label: string }> = {
  queued:           { text: 'text-fg-muted', label: 'Đang chờ đến lượt' },
  running:          { text: 'text-fg-2',     label: 'Đang kiểm tra…' },
  ok:               { text: 'text-success',  label: 'Còn dùng được' },
  rejected:         { text: 'text-danger',   label: 'Bị từ chối' },
  inconclusive:     { text: 'text-warning',  label: 'Chưa kết luận' },
  unsupported:      { text: 'text-fg-muted', label: 'Chưa hỗ trợ kiểm tra' },
  skipped_disabled: { text: 'text-fg-muted', label: 'Đang tắt, bỏ qua' },
  rate_limited:     { text: 'text-warning',  label: 'Chờ giãn cách' },
  error:            { text: 'text-danger',   label: 'Lỗi khi kiểm tra' },
}

function LiveTestCell({ test }: { test?: LiveTest }) {
  if (!test) return <span className="text-[11px] text-fg-muted">Chưa kiểm tra</span>
  const st = LIVE_STYLE[test.state]
  return (
    <div className="max-w-[220px]" data-testid="live-test">
      <p className={cn('text-[11px] font-medium', st.text)}>{st.label}</p>
      {test.message && test.state !== 'ok' && (
        <p className="text-[10px] leading-snug text-fg-muted">{test.message}</p>
      )}
      {test.at ? <p className="font-mono text-[10px] text-fg-muted">{formatStamp(test.at)}</p> : null}
    </div>
  )
}

// ─── Platform abbreviation badge (local, no cross-panel dep) ──────────────────

const PLAT_COLORS: Record<string, { text: string; bg: string }> = {
  youtube:    { text: 'text-fg-2', bg: 'bg-surface-2' },
  instagram:  { text: 'text-fg-2',   bg: 'bg-surface-2'   },
  tiktok:     { text: 'text-fg-2',    bg: 'bg-surface-2'    },
  twitter:    { text: 'text-fg-2',  bg: 'bg-surface'     },
  facebook:   { text: 'text-fg-2',   bg: 'bg-surface-2'   },
  bilibili:   { text: 'text-fg-2',   bg: 'bg-surface-2'   },
  douyin:     { text: 'text-fg-2',  bg: 'bg-surface'     },
  soundcloud: { text: 'text-fg-2', bg: 'bg-surface-2' },
  pinterest:  { text: 'text-fg-2', bg: 'bg-surface-2' },
  reddit:     { text: 'text-fg-2', bg: 'bg-surface-2' },
  vimeo:      { text: 'text-fg-2',   bg: 'bg-surface-2'   },
  threads:    { text: 'text-fg-2',  bg: 'bg-surface'     },
}

function PlatBadge({ platform }: { platform: string }) {
  const meta = PLAT_COLORS[platform] ?? { text: 'text-fg-muted', bg: 'bg-surface' }
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
      <span className="text-xs font-medium capitalize text-fg-2">{platform}</span>
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
  if (secs <= 0) return <span className="text-[11px] text-fg-muted">—</span>

  const isHard = status === 'hard_blocked'
  return (
    <span
      className={cn(
        'font-mono text-[11px] font-medium tabular-nums',
        isHard ? 'text-danger' : 'text-warning',
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
  { key: 'expiry',        label: 'Hạn · ngày thêm',  w: 'min-w-[170px]' },
  { key: 'livetest',      label: 'Kiểm tra thật',    w: 'min-w-[190px]' },
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
              className="inline-flex items-center gap-1.5 rounded-control bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-hover"
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
    <>
    <div className="overflow-x-auto">
      <table className="min-w-[1280px] w-full text-sm">
        {/* Sticky header */}
        <thead className="sticky top-0 z-10 bg-surface-2">
          <tr className="border-y border-line">
            {COLS.map(col => (
              <th
                key={col.key}
                className={cn(
                  'py-2 pr-3 text-left font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted first:pl-4',
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
                  'transition-colors hover:bg-surface-2',
                  !isLast && 'border-b border-line',
                  isDim && 'opacity-60',
                )}
              >
                {/* Platform */}
                <td className="py-2.5 pl-4 pr-3">
                  <PlatBadge platform={cookie.platform} />
                </td>

                {/* Account Label */}
                <td className="py-2.5 pr-3">
                  <span className="font-mono text-[11px] text-fg-2">{cookie.accountLabel}</span>
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
                  <span className="font-mono text-[11px] text-fg-muted">
                    {cookie.lastSuccessAt}
                  </span>
                </td>

                {/* Last Fail */}
                <td className="py-2.5 pr-3">
                  <span className={cn('font-mono text-[11px]', cookie.lastFailAt ? 'text-danger' : 'text-fg-muted')}>
                    {cookie.lastFailAt ?? '—'}
                  </span>
                </td>

                {/* Fail Count */}
                <td className="py-2.5 pr-3">
                  <span
                    className={cn(
                      'font-mono text-xs font-semibold tabular-nums',
                      cookie.failCount === 0
                        ? 'text-fg-muted'
                        : cookie.failCount >= 7
                          ? 'text-danger'
                          : cookie.failCount >= 3
                            ? 'text-warning'
                            : 'text-fg-muted',
                    )}
                  >
                    {cookie.failCount === 0 ? '—' : cookie.failCount}
                  </span>
                </td>

                {/* Cooldown */}
                <td className="py-2.5 pr-3">
                  <CooldownCell secs={cookie.cooldownRemainingSec} status={cookie.status} />
                </td>

                {/* Hạn · ngày thêm */}
                <td className="py-2.5 pr-3">
                  {(() => {
                    const life = describeLifetime(cookie.expiresAt, Math.floor(Date.now() / 1000))
                    const added = formatAddedDate(cookie.addedAt)
                    return (
                      <div>
                        <p
                          className={cn(
                            'text-[11px] font-medium',
                            life.tone === 'past' ? 'text-danger'
                              : life.tone === 'soon' ? 'text-warning'
                                : life.tone === 'ok' ? 'text-fg-2' : 'text-fg-muted',
                          )}
                        >
                          {life.text}
                        </p>
                        <p className="text-[10px] text-fg-muted">
                          {added ? `Thêm ngày ${added}` : 'Không rõ ngày thêm'}
                        </p>
                      </div>
                    )
                  })()}
                </td>

                {/* Kiểm tra thật */}
                <td className="py-2.5 pr-3">
                  <LiveTestCell test={cookie.liveTest} />
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
    <p className="border-t border-line px-4 py-2 text-[11px] text-fg-muted">
      Hạn dùng là hạn do chính cookie tự khai; nền tảng có thể vô hiệu hoá sớm hơn
      hoặc gia hạn phiên lâu hơn. Dùng &quot;Kiểm tra lại&quot; để hỏi thẳng nền tảng.
    </p>
    </>
  )
}
