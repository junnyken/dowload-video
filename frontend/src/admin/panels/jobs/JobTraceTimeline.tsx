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
    dot:   'bg-emerald-500',
    ring:  'border-emerald-700',
    line:  'bg-emerald-900',
    badge: 'border-emerald-900 bg-emerald-950 text-emerald-400',
    label: 'PASS',
  },
  running: {
    dot:   'bg-blue-500 animate-pulse',
    ring:  'border-blue-600',
    line:  'bg-slate-800',
    badge: 'border-blue-900 bg-blue-950 text-blue-400',
    label: 'RUN',
  },
  failed: {
    dot:   'bg-red-600',
    ring:  'border-red-800',
    line:  'bg-slate-800',
    badge: 'border-red-900 bg-red-950 text-red-400',
    label: 'FAIL',
  },
  skipped: {
    dot:   'bg-slate-800',
    ring:  'border-slate-700',
    line:  'bg-slate-900',
    badge: 'border-slate-800 bg-slate-900/50 text-slate-600',
    label: 'SKIP',
  },
  pending: {
    dot:   'bg-transparent',
    ring:  'border-slate-700 border-dashed',
    line:  'bg-slate-900',
    badge: 'border-slate-800 bg-slate-900/30 text-slate-700',
    label: 'WAIT',
  },
}

// ─── Phase dot icon ────────────────────────────────────────────────────────────

function DotIcon({ status }: { status: PhaseStatus }) {
  if (status === 'success') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} className="h-2.5 w-2.5 text-white">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    )
  }
  if (status === 'failed') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={3} className="h-2.5 w-2.5 text-white">
        <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    )
  }
  if (status === 'running') {
    return <span className="h-2 w-2 rounded-full bg-white opacity-90" />
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
              hasDetail ? 'text-slate-200' : 'text-slate-600',
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
                trace.status === 'failed' ? 'text-red-500/80' : 'text-slate-600',
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
              <span className="text-[10px] text-slate-600">
                <span className="text-[9px] uppercase tracking-widest text-slate-700 mr-1">start</span>
                <span className="font-mono">{trace.startedAt}</span>
              </span>
            )}
            {trace.endedAt && (
              <span className="text-[10px] text-slate-600">
                <span className="text-[9px] uppercase tracking-widest text-slate-700 mr-1">end</span>
                <span className="font-mono">{trace.endedAt}</span>
              </span>
            )}
            {trace.proxyUsed && (
              <span className="text-[10px]">
                <span className="text-[9px] uppercase tracking-widest text-slate-700 mr-1">proxy</span>
                <span className="font-mono text-blue-500/70">{trace.proxyUsed}</span>
              </span>
            )}
            {trace.cookieUsed && (
              <span className="text-[10px]">
                <span className="text-[9px] uppercase tracking-widest text-slate-700 mr-1">cookie</span>
                <span className="font-mono text-purple-400/70">{trace.cookieUsed}</span>
              </span>
            )}
          </div>
        )}

        {/* Error message */}
        {trace.errorMessage && (
          <div className="mt-2 flex items-start gap-2 rounded-lg border border-red-900/60 bg-red-950/25 px-3 py-2">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.75}
              strokeLinecap="round"
              className="mt-0.5 h-3 w-3 flex-shrink-0 text-red-600"
            >
              <path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
            </svg>
            <p className="font-mono text-[10px] leading-relaxed text-red-400/90">
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
