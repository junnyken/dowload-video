import { Link } from 'react-router-dom'
import { cn } from '../utils/cn'
import { EmptyState } from '../shared/EmptyState'
import { ErrorState } from '../shared/ErrorState'
import { TableSkeleton } from '../shared/LoadingSkeleton'

export interface FailureRow {
  id: string
  platform: string
  error: string
  time: string
  phase: string
  action?: string   // href to view the job
}

// Platform badge colors (best-effort, falls back to neutral)
const PLATFORM_COLORS: Record<string, string> = {
  youtube:   'text-red-400    bg-red-950/60    border-red-900/60',
  instagram: 'text-pink-400   bg-pink-950/60   border-pink-900/60',
  tiktok:    'text-sky-400    bg-sky-950/60    border-sky-900/60',
  twitter:   'text-slate-300  bg-slate-800     border-slate-700',
  facebook:  'text-blue-400   bg-blue-950/60   border-blue-900/60',
  bilibili:  'text-cyan-400   bg-cyan-950/60   border-cyan-900/60',
  soundcloud:'text-orange-400 bg-orange-950/60 border-orange-900/60',
}

function platformStyle(name: string) {
  return (
    PLATFORM_COLORS[name.toLowerCase()] ??
    'text-slate-400 bg-slate-800 border-slate-700'
  )
}

function PhaseBadge({ phase }: { phase: string }) {
  return (
    <span className="inline-flex items-center rounded border border-slate-700 bg-slate-800 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-slate-400">
      {phase}
    </span>
  )
}

const COL_HEADERS = ['Platform', 'Phase', 'Error', 'When', '']

interface RecentFailuresTableProps {
  rows: FailureRow[]
  loading?: boolean
  error?: string
  onRetry?: () => void
}

export function RecentFailuresTable({
  rows,
  loading,
  error,
  onRetry,
}: RecentFailuresTableProps) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/70">
      {/* Table header */}
      {loading ? (
        <div className="p-4">
          <TableSkeleton rows={5} />
        </div>
      ) : error ? (
        <div className="p-4">
          <ErrorState message={error} onRetry={onRetry} />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No recent failures"
          description="All jobs completing successfully across all platforms"
          icon={
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.25}
              className="h-10 w-10 text-emerald-800"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          }
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-[540px] w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800">
                {COL_HEADERS.map(h => (
                  <th
                    key={h}
                    className={cn(
                      'py-2 pr-3 text-left font-mono text-[9px] font-semibold uppercase tracking-widest text-slate-600 first:pl-4',
                      h === '' && 'w-12 text-right last:pr-4',
                    )}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={row.id}
                  className={cn(
                    'transition-colors hover:bg-slate-800/30',
                    i < rows.length - 1 && 'border-b border-slate-800/50',
                  )}
                >
                  {/* Platform */}
                  <td className="py-2.5 pl-4 pr-3">
                    <span
                      className={cn(
                        'inline-flex items-center rounded border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider',
                        platformStyle(row.platform),
                      )}
                    >
                      {row.platform}
                    </span>
                  </td>

                  {/* Phase */}
                  <td className="py-2.5 pr-3">
                    <PhaseBadge phase={row.phase} />
                  </td>

                  {/* Error */}
                  <td className="py-2.5 pr-3">
                    <p
                      className="max-w-[280px] truncate text-xs text-slate-400"
                      title={row.error}
                    >
                      {row.error}
                    </p>
                  </td>

                  {/* Time */}
                  <td className="py-2.5 pr-3">
                    <span className="font-mono text-[11px] text-slate-600">{row.time}</span>
                  </td>

                  {/* Action */}
                  <td className="py-2.5 pr-4 text-right">
                    {row.action ? (
                      <Link
                        to={row.action}
                        className="inline-flex items-center rounded border border-slate-700 px-2 py-0.5 text-[10px] text-slate-500 transition-colors hover:border-slate-600 hover:text-slate-300"
                      >
                        View
                      </Link>
                    ) : (
                      <span className="text-[10px] text-slate-700">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
