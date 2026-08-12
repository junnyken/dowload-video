import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { cn } from '../../utils/cn'
import { EmptyState } from '../../shared/EmptyState'
import { ErrorState } from '../../shared/ErrorState'
import { TableSkeleton } from '../../shared/LoadingSkeleton'
import type { PlatformHealthRow, PlatformFilter } from './platform.types'
import { DEFAULT_FILTER } from './platform.types'
import { PlatformNameCell, PlatformStatusBadge, CircuitStatePill } from './PlatformStatusBadge'
import { PlatformFilters } from './PlatformFilters'
import { PlatformActionMenu } from './PlatformActionMenu'

// ─── Inline cell components ────────────────────────────────────────────────────

function FailRateCell({ rate }: { rate: number }) {
  const color =
    rate >= 20 ? 'bg-red-500' :
    rate >= 5  ? 'bg-amber-500' :
    'bg-emerald-500'
  const textColor =
    rate >= 20 ? 'text-red-400' :
    rate >= 5  ? 'text-amber-400' :
    'text-emerald-400'

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-14 flex-shrink-0 overflow-hidden rounded-full bg-slate-800">
        <div
          className={cn('h-full rounded-full transition-all', color)}
          style={{ width: `${Math.min(rate, 100)}%` }}
        />
      </div>
      <span className={cn('w-8 font-mono text-[11px] tabular-nums', textColor)}>
        {rate}%
      </span>
    </div>
  )
}

function BoolCell({ value, trueLabel, falseLabel }: { value: boolean; trueLabel: string; falseLabel: string }) {
  return value ? (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} className="h-3 w-3 text-emerald-500">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
      {trueLabel}
    </span>
  ) : (
    <span className="text-[11px] text-slate-700">{falseLabel}</span>
  )
}

// ─── Column definitions ────────────────────────────────────────────────────────

const COLUMNS = [
  { key: 'platform',       label: 'Platform',       minW: 'min-w-[150px]' },
  { key: 'status',         label: 'Status',         minW: 'min-w-[120px]' },
  { key: 'circuit',        label: 'Circuit State',  minW: 'min-w-[110px]' },
  { key: 'lastSuccess',    label: 'Last Success',   minW: 'min-w-[100px]' },
  { key: 'failRate',       label: 'Fail Rate 1h',   minW: 'min-w-[140px]' },
  { key: 'cookie',         label: 'Cookie',         minW: 'min-w-[80px]'  },
  { key: 'proxy',          label: 'Proxy',          minW: 'min-w-[80px]'  },
  { key: 'actions',        label: '',               minW: 'w-10'          },
]

// ─── Filter logic ──────────────────────────────────────────────────────────────

function applyFilter(rows: PlatformHealthRow[], filter: PlatformFilter): PlatformHealthRow[] {
  return rows.filter(row => {
    if (filter.search && !row.platform.includes(filter.search.toLowerCase())) return false
    if (filter.status !== 'all' && row.status !== filter.status) return false
    if (filter.cookieRequired !== null && row.cookieRequired !== filter.cookieRequired) return false
    if (filter.proxyRequired !== null && row.proxyRequired !== filter.proxyRequired) return false
    return true
  })
}

// ─── Main component ────────────────────────────────────────────────────────────

interface PlatformHealthTableProps {
  rows: PlatformHealthRow[]
  loading?: boolean
  error?: string
  onRetry?: () => void
  onRowClick?: (row: PlatformHealthRow) => void
  onAction?: (platform: string, action: string) => void
  selectedPlatform?: string | null
}

export function PlatformHealthTable({
  rows,
  loading,
  error,
  onRetry,
  onRowClick,
  onAction,
  selectedPlatform,
}: PlatformHealthTableProps) {
  const navigate = useNavigate()
  const [filter, setFilter] = useState<PlatformFilter>(DEFAULT_FILTER)

  const filtered = useMemo(() => applyFilter(rows, filter), [rows, filter])

  const healthyCount = rows.filter(r => r.status === 'healthy').length

  return (
    <div className="flex flex-col">
      {/* ── Toolbar ── */}
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h1 className="text-sm font-semibold text-slate-100">Platform Health</h1>
          <p className="mt-0.5 text-[11px] text-slate-600">
            {healthyCount} / {rows.length} operational · circuit breakers + cookie pool status
          </p>
        </div>
        <span className="font-mono text-[10px] text-slate-700">
          auto-refresh 30s
        </span>
      </div>

      <div className="rounded-2xl border border-slate-800 bg-slate-900">
        {/* ── Filters ── */}
        <div className="px-4 pt-4 pb-3">
          <PlatformFilters
            value={filter}
            onChange={setFilter}
            totalCount={rows.length}
            filteredCount={filtered.length}
          />
        </div>

        {/* ── Table ── */}
        {loading ? (
          <div className="px-4 pb-4">
            <TableSkeleton rows={8} />
          </div>
        ) : error ? (
          <div className="px-4 pb-4">
            <ErrorState message={error} onRetry={onRetry} />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            title="No platforms match"
            description="Adjust filters or search to see results"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[780px] w-full text-sm">
              {/* Sticky header */}
              <thead className="sticky top-0 z-10 bg-slate-900">
                <tr className="border-y border-slate-800">
                  {COLUMNS.map(col => (
                    <th
                      key={col.key}
                      className={cn(
                        'py-2 pr-3 text-left font-mono text-[9px] font-semibold uppercase tracking-widest text-slate-600 first:pl-4',
                        col.minW,
                        col.key === 'actions' && 'text-center',
                      )}
                    >
                      {col.label}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {filtered.map((row, i) => {
                  const isSelected = row.platform === selectedPlatform
                  const isLast = i === filtered.length - 1

                  return (
                    <tr
                      key={row.platform}
                      onClick={() => onRowClick?.(row)}
                      className={cn(
                        'cursor-pointer transition-colors',
                        !isLast && 'border-b border-slate-800/50',
                        isSelected
                          ? 'bg-blue-950/20 hover:bg-blue-950/30'
                          : 'hover:bg-slate-800/40',
                      )}
                    >
                      {/* Platform */}
                      <td className="py-2.5 pl-4 pr-3">
                        <PlatformNameCell
                          platform={row.platform}
                          activeJobs={row.activeJobs}
                        />
                      </td>

                      {/* Status */}
                      <td className="py-2.5 pr-3">
                        <PlatformStatusBadge status={row.status} />
                      </td>

                      {/* Circuit State */}
                      <td className="py-2.5 pr-3">
                        <CircuitStatePill state={row.circuitState} />
                      </td>

                      {/* Last Success */}
                      <td className="py-2.5 pr-3">
                        <span className="font-mono text-[11px] text-slate-500">
                          {row.lastSuccessAt}
                        </span>
                      </td>

                      {/* Fail Rate */}
                      <td className="py-2.5 pr-3">
                        <FailRateCell rate={row.failRate1h} />
                      </td>

                      {/* Cookie Required */}
                      <td className="py-2.5 pr-3">
                        <BoolCell
                          value={row.cookieRequired}
                          trueLabel="Req"
                          falseLabel="—"
                        />
                      </td>

                      {/* Proxy Required */}
                      <td className="py-2.5 pr-3">
                        <BoolCell
                          value={row.proxyRequired}
                          trueLabel="Req"
                          falseLabel="—"
                        />
                      </td>

                      {/* Actions */}
                      <td
                        className="py-2.5 pr-4"
                        onClick={e => e.stopPropagation()}
                      >
                        <PlatformActionMenu
                          row={row}
                          onAction={onAction}
                          onViewJobs={p => navigate(`/vid-admin/jobs?platform=${p}`)}
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* ── Footer ── */}
        {!loading && !error && filtered.length > 0 && (
          <div className="flex items-center justify-between border-t border-slate-800/60 px-4 py-2">
            <span className="font-mono text-[10px] text-slate-700">
              Showing {filtered.length} of {rows.length} platforms
            </span>
            <div className="flex items-center gap-3">
              {[
                { status: 'healthy',  label: 'Healthy',  color: 'bg-emerald-500' },
                { status: 'warning',  label: 'Warning',  color: 'bg-amber-500'   },
                { status: 'critical', label: 'Critical', color: 'bg-red-500'     },
                { status: 'disabled', label: 'Disabled', color: 'bg-slate-600'   },
              ].map(({ status, label, color }) => {
                const count = rows.filter(r => r.status === status).length
                if (count === 0) return null
                return (
                  <span key={status} className="flex items-center gap-1 font-mono text-[10px] text-slate-600">
                    <span className={cn('h-1.5 w-1.5 rounded-full', color)} />
                    {count} {label}
                  </span>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
