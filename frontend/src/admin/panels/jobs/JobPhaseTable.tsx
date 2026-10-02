import { cn } from '../../utils/cn'
import type { JobTracePhase, PhaseStatus } from './job.types'
import { PHASE_ORDER } from './job.types'

// ─── Status pill config ────────────────────────────────────────────────────────

const PILL: Record<PhaseStatus, string> = {
  success: 'border-success/30 bg-success-soft text-success',
  running: 'border-line   bg-surface-2   text-fg-2',
  failed:  'border-danger/30    bg-danger-soft    text-danger',
  skipped: 'border-line  bg-transparent text-fg-muted',
  pending: 'border-line  bg-transparent text-fg-muted',
}

const LABEL: Record<PhaseStatus, string> = {
  success: 'PASS',
  running: 'RUN',
  failed:  'FAIL',
  skipped: 'SKIP',
  pending: 'WAIT',
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function fmtMs(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000)  return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

// ─── Column defs ───────────────────────────────────────────────────────────────

const COLS = [
  { key: 'phase',  label: 'Phase',    w: 'min-w-[100px]' },
  { key: 'status', label: 'Status',   w: 'min-w-[70px]'  },
  { key: 'start',  label: 'Start',    w: 'min-w-[110px]' },
  { key: 'end',    label: 'End',      w: 'min-w-[110px]' },
  { key: 'dur',    label: 'Duration', w: 'min-w-[80px]'  },
  { key: 'proxy',  label: 'Proxy',    w: 'min-w-[160px]' },
  { key: 'cookie', label: 'Cookie',   w: 'min-w-[120px]' },
  { key: 'error',  label: 'Error',    w: 'min-w-[220px] max-w-[320px]' },
]

// ─── Main table ────────────────────────────────────────────────────────────────

interface JobPhaseTableProps {
  traces: JobTracePhase[]
}

export function JobPhaseTable({ traces }: JobPhaseTableProps) {
  // Fill all 7 phases
  const rows: JobTracePhase[] = PHASE_ORDER.map(phase =>
    traces.find(t => t.phase === phase) ?? {
      phase,
      status:       'pending',
      startedAt:    null,
      endedAt:      null,
      durationMs:   null,
      proxyUsed:    null,
      cookieUsed:   null,
      errorMessage: null,
    },
  )

  return (
    <div className="overflow-x-auto">
      <table className="min-w-[960px] w-full text-sm">
        <thead className="sticky top-0 z-10 bg-surface-2">
          <tr className="border-y border-line">
            {COLS.map(col => (
              <th
                key={col.key}
                className={cn(
                  'py-2 pr-3 text-left font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted first:pl-4',
                  col.w,
                )}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>

        <tbody>
          {rows.map((row, i) => {
            const isLast = i === rows.length - 1
            const dim    = row.status === 'pending' || row.status === 'skipped'
            return (
              <tr
                key={row.phase}
                className={cn(
                  'transition-colors hover:bg-surface-2',
                  !isLast && 'border-b border-line',
                  dim && 'opacity-35',
                )}
              >
                {/* Phase */}
                <td className="py-2.5 pl-4 pr-3">
                  <span className="font-mono text-[11px] font-semibold text-fg-2 capitalize">
                    {row.phase}
                  </span>
                </td>

                {/* Status */}
                <td className="py-2.5 pr-3">
                  <span
                    className={cn(
                      'inline-block rounded border px-1.5 py-px font-mono text-[10px] font-bold uppercase tracking-widest',
                      PILL[row.status],
                    )}
                  >
                    {LABEL[row.status]}
                  </span>
                </td>

                {/* Start */}
                <td className="py-2.5 pr-3">
                  <span className="font-mono text-[10px] text-fg-muted">
                    {row.startedAt ?? '—'}
                  </span>
                </td>

                {/* End */}
                <td className="py-2.5 pr-3">
                  <span className="font-mono text-[10px] text-fg-muted">
                    {row.endedAt ?? '—'}
                  </span>
                </td>

                {/* Duration */}
                <td className="py-2.5 pr-3">
                  <span
                    className={cn(
                      'font-mono text-[11px] tabular-nums',
                      row.status === 'failed'
                        ? 'text-danger'
                        : row.status === 'success'
                          ? 'text-fg-muted'
                          : 'text-fg-muted',
                    )}
                  >
                    {fmtMs(row.durationMs)}
                  </span>
                </td>

                {/* Proxy */}
                <td className="py-2.5 pr-3">
                  <span className="font-mono text-[10px] text-fg-2">
                    {row.proxyUsed ?? '—'}
                  </span>
                </td>

                {/* Cookie */}
                <td className="py-2.5 pr-3">
                  <span className="font-mono text-[10px] text-fg-2">
                    {row.cookieUsed ?? '—'}
                  </span>
                </td>

                {/* Error */}
                <td className="py-2.5 pr-3">
                  {row.errorMessage ? (
                    <span className="font-mono text-[10px] leading-relaxed text-danger line-clamp-2">
                      {row.errorMessage}
                    </span>
                  ) : (
                    <span className="text-[10px] text-fg">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
