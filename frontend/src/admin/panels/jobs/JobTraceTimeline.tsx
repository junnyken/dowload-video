import { cn } from '../../utils/cn'
import type { JobTracePhase, PhaseStatus } from './job.types'
import { PHASE_ORDER } from './job.types'

// ─── Status config ─────────────────────────────────────────────────────────────

interface StatusMeta {
  dot:    string
  ring:   string
  line:   string
  badge:  string
  label:  string
}

const STATUS_META: Record<PhaseStatus, StatusMeta> = {
  success: {
    dot:   'bg-success',
    ring:  'border-success/30',
    line:  'bg-success-soft',
    badge: 'border-success/30 bg-success-soft text-success',
    label: 'PASS',
  },
  running: {
    dot:   'bg-accent animate-pulse',
    ring:  'border-line',
    line:  'bg-surface',
    badge: 'border-line bg-surface-2 text-fg-2',
    label: 'RUN',
  },
  failed: {
    dot:   'bg-danger',
    ring:  'border-danger/30',
    line:  'bg-surface',
    badge: 'border-danger/30 bg-danger-soft text-danger',
    label: 'FAIL',
  },
  skipped: {
    dot:   'bg-surface',
    ring:  'border-line',
    line:  'bg-canvas',
    badge: 'border-line bg-surface-2 text-fg-muted',
    label: 'SKIP',
  },
  pending: {
    dot:   'bg-transparent',
    ring:  'border-line border-dashed',
    line:  'bg-canvas',
    badge: 'border-line bg-surface-2 text-fg-muted',
    label: 'WAIT',
  },
}

// ─── Phase dot icon ────────────────────────────────────────────────────────────

function DotIcon({ status }: { status: PhaseStatus }) {
  if (status === 'success') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} className="h-2.5 w-2.5 text-fg">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    )
  }
  if (status === 'failed') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} className="h-2.5 w-2.5 text-fg">
        <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    )
  }
  if (status === 'running') {
    return <span className="h-2 w-2 rounded-full bg-surface opacity-90" />
  }
  return null
}

// ─── Format helpers ────────────────────────────────────────────────────────────

function fmtMs(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000)  return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

// ─── Single phase row ──────────────────────────────────────────────────────────

interface TraceRowProps {
  trace:  JobTracePhase
  isLast: boolean
}

function TraceRow({ trace, isLast }: TraceRowProps) {
  const m = STATUS_META[trace.status]
  const hasDetail = trace.status !== 'pending' && trace.status !== 'skipped'

  return (
    <div className="flex">
      {/* ── Left spine: dot + line ── */}
      <div className="flex w-9 flex-shrink-0 flex-col items-center">
        <div
          className={cn(
            'z-10 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full border-2',
            m.dot, m.ring,
          )}
        >
          <DotIcon status={trace.status} />
        </div>
        {!isLast && (
          <div className={cn('w-px flex-1 my-1 min-h-[28px]', m.line)} />
        )}
      </div>

      {/* ── Right: content ── */}
      <div className={cn('min-w-0 flex-1', isLast ? 'pb-0' : 'pb-5')}>
        {/* Phase name + status badge + duration */}
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <span
            className={cn(
              'font-mono text-[11px] font-bold uppercase tracking-wide',
              hasDetail ? 'text-fg-2' : 'text-fg-muted',
            )}
          >
            {trace.phase}
          </span>

          <span
            className={cn(
              'rounded border px-1.5 py-px font-mono text-[9px] font-bold uppercase tracking-widest',
              m.badge,
            )}
          >
            {m.label}
          </span>

          {trace.durationMs !== null && (
            <span
              className={cn(
                'font-mono text-[10px] tabular-nums',
                trace.status === 'failed' ? 'text-danger' : 'text-fg-muted',
              )}
            >
              {fmtMs(trace.durationMs)}
            </span>
          )}
        </div>

        {/* Metadata row: timestamps, proxy, cookie */}
        {hasDetail && (trace.startedAt || trace.proxyUsed || trace.cookieUsed) && (
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5">
            {trace.startedAt && (
              <span className="text-[10px] text-fg-muted">
                <span className="text-[9px] uppercase tracking-widest text-fg-muted mr-1">start</span>
                <span className="font-mono">{trace.startedAt}</span>
              </span>
            )}
            {trace.endedAt && (
              <span className="text-[10px] text-fg-muted">
                <span className="text-[9px] uppercase tracking-widest text-fg-muted mr-1">end</span>
                <span className="font-mono">{trace.endedAt}</span>
              </span>
            )}
            {trace.proxyUsed && (
              <span className="text-[10px]">
                <span className="text-[9px] uppercase tracking-widest text-fg-muted mr-1">proxy</span>
                <span className="font-mono text-fg-2">{trace.proxyUsed}</span>
              </span>
            )}
            {trace.cookieUsed && (
              <span className="text-[10px]">
                <span className="text-[9px] uppercase tracking-widest text-fg-muted mr-1">cookie</span>
                <span className="font-mono text-fg-2">{trace.cookieUsed}</span>
              </span>
            )}
          </div>
        )}

        {/* Error message */}
        {trace.errorMessage && (
          <div className="mt-2 flex items-start gap-2 rounded-lg border border-danger/60 bg-danger-soft px-3 py-2">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.75}
              strokeLinecap="round"
              className="mt-0.5 h-3 w-3 flex-shrink-0 text-danger"
            >
              <path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <p className="font-mono text-[10px] leading-relaxed text-danger">
              {trace.errorMessage}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Main component ────────────────────────────────────────────────────────────

interface JobTraceTimelineProps {
  traces: JobTracePhase[]
}

export function JobTraceTimeline({ traces }: JobTraceTimelineProps) {
  // Fill in all 7 phases; any phase missing from the trace gets 'pending'
  const filled: JobTracePhase[] = PHASE_ORDER.map(phase => {
    return (
      traces.find(t => t.phase === phase) ?? {
        phase,
        status:       'pending',
        startedAt:    null,
        endedAt:      null,
        durationMs:   null,
        proxyUsed:    null,
        cookieUsed:   null,
        errorMessage: null,
      }
    )
  })

  return (
    <div className="px-5 py-4">
      {filled.map((trace, i) => (
        <TraceRow
          key={trace.phase}
          trace={trace}
          isLast={i === filled.length - 1}
        />
      ))}
    </div>
  )
}
