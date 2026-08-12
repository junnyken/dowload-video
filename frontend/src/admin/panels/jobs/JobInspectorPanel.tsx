import { useState } from 'react'
import { cn } from '../../utils/cn'
import { Card } from '../../shared/Card'
import { SectionHeader } from '../../shared/SectionHeader'
import { EmptyState } from '../../shared/EmptyState'
import { CardSkeleton } from '../../shared/LoadingSkeleton'
import type { JobInspectorData, JobStatus, PhaseName } from './job.types'
import { JobSearchBar } from './JobSearchBar'
import { JobTraceTimeline } from './JobTraceTimeline'
import { JobPhaseTable } from './JobPhaseTable'
import { JobRetryActions } from './JobRetryActions'
import { findJob } from './job.mock'

// ─── Job status pill ───────────────────────────────────────────────────────────

const JOB_STATUS: Record<JobStatus, { pill: string; dot: string; label: string }> = {
  queued:    { pill: 'border-blue-900   bg-blue-950   text-blue-400',    dot: 'bg-blue-500',            label: 'QUEUED'    },
  running:   { pill: 'border-blue-700   bg-blue-900   text-blue-300',    dot: 'bg-blue-400 animate-pulse', label: 'RUNNING' },
  done:      { pill: 'border-emerald-900 bg-emerald-950 text-emerald-400', dot: 'bg-emerald-500',       label: 'DONE'      },
  failed:    { pill: 'border-red-900    bg-red-950    text-red-400',     dot: 'bg-red-500',             label: 'FAILED'    },
  cancelled: { pill: 'border-slate-700  bg-slate-800  text-slate-500',   dot: 'bg-slate-600',           label: 'CANCELLED' },
  stuck:     { pill: 'border-amber-900  bg-amber-950  text-amber-400',   dot: 'bg-amber-400 animate-pulse', label: 'STUCK' },
}

function JobStatusPill({ status }: { status: JobStatus }) {
  const cfg = JOB_STATUS[status]
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded border px-2 py-0.5',
        'font-mono text-[10px] font-bold uppercase tracking-widest',
        cfg.pill,
      )}
    >
      <span className={cn('h-1.5 w-1.5 flex-shrink-0 rounded-full', cfg.dot)} />
      {cfg.label}
    </span>
  )
}

// ─── Job header ────────────────────────────────────────────────────────────────

function fmtMs(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000)  return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function JobHeader({ job }: { job: JobInspectorData }) {
  return (
    <div className="border-b border-slate-800 px-4 py-3 space-y-2">
      {/* Top row: IDs + status + timestamps */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-bold text-slate-100">{job.jobId}</span>
            <JobStatusPill status={job.currentStatus} />
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5">
            {job.batchId && (
              <MetaField label="Batch" value={job.batchId} mono />
            )}
            <MetaField label="Platform" value={job.platform} capitalize />
            <MetaField label="IP" value={job.userIp} mono />
          </div>
        </div>

        <div className="flex flex-col items-end gap-0.5">
          <MetaField label="Created" value={job.createdAt} mono right />
          <MetaField label="Updated" value={job.updatedAt} mono right />
          {job.summary.totalDurationMs !== null && (
            <MetaField label="Total" value={fmtMs(job.summary.totalDurationMs)} mono right bold />
          )}
        </div>
      </div>

      {/* URL */}
      <div className="flex items-start gap-1.5 rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-1.5">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" className="mt-0.5 h-3 w-3 flex-shrink-0 text-slate-700">
          <path d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
        </svg>
        <span className="break-all font-mono text-[10px] text-slate-500">{job.url}</span>
      </div>

      {/* Recent errors */}
      {job.recentErrors.length > 0 && (
        <div>
          <p className="mb-1 text-[9px] uppercase tracking-widest text-slate-700">Recent errors</p>
          <div className="flex flex-col gap-0.5">
            {job.recentErrors.map((err, i) => (
              <div key={i} className="flex items-start gap-1.5">
                <span className="mt-1 h-1 w-1 flex-shrink-0 rounded-full bg-red-700" />
                <span className="font-mono text-[10px] text-red-500/80">{err}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MetaField({
  label, value, mono, capitalize, bold, right,
}: {
  label: string
  value: string
  mono?:       boolean
  capitalize?: boolean
  bold?:       boolean
  right?:      boolean
}) {
  return (
    <div className={cn('flex items-center gap-1', right && 'justify-end')}>
      <span className="text-[9px] uppercase tracking-widest text-slate-700">{label}</span>
      <span
        className={cn(
          'text-[10px] text-slate-500',
          mono       && 'font-mono',
          capitalize && 'capitalize',
          bold       && 'font-semibold text-slate-400',
        )}
      >
        {value}
      </span>
    </div>
  )
}

// ─── View tabs ─────────────────────────────────────────────────────────────────

type ViewTab = 'timeline' | 'table'

interface TabBarProps {
  active:   ViewTab
  onChange: (t: ViewTab) => void
  job:      JobInspectorData
}

function TabBar({ active, onChange, job }: TabBarProps) {
  const pct = Math.round((job.summary.completedPhases / job.summary.totalPhases) * 100)
  const hasFail = job.summary.failedPhases > 0

  return (
    <div className="flex items-center border-b border-slate-800 px-4">
      {(['timeline', 'table'] as ViewTab[]).map(t => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={cn(
            'flex items-center gap-1.5 border-b-2 px-4 py-2.5 text-xs font-medium transition-colors',
            active === t
              ? 'border-blue-600 text-blue-400'
              : 'border-transparent text-slate-600 hover:text-slate-400',
          )}
        >
          {t === 'timeline' ? (
            <>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className="h-3.5 w-3.5">
                <path strokeLinecap="round" d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
              </svg>
              Timeline
            </>
          ) : (
            <>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className="h-3.5 w-3.5">
                <path strokeLinecap="round" d="M3 10h18M3 14h18M10 6h4M10 18h4M6 6v12M18 6v12" />
              </svg>
              Phase Table
            </>
          )}
        </button>
      ))}

      {/* Progress bar */}
      <div className="ml-auto flex items-center gap-2 py-2">
        <span className="font-mono text-[10px] text-slate-600">
          {job.summary.completedPhases}/{job.summary.totalPhases}
        </span>
        <div className="h-1 w-20 overflow-hidden rounded-full bg-slate-800">
          <div
            className={cn('h-full rounded-full transition-all', hasFail ? 'bg-red-600' : 'bg-emerald-500')}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="font-mono text-[10px] text-slate-600">{pct}%</span>
      </div>
    </div>
  )
}

// ─── Main panel ────────────────────────────────────────────────────────────────

interface JobInspectorPanelProps {
  initialJob?: JobInspectorData | null
}

export function JobInspectorPanel({ initialJob }: JobInspectorPanelProps) {
  const [query,   setQuery]   = useState('')
  const [job,     setJob]     = useState<JobInspectorData | null>(initialJob ?? null)
  const [loading, setLoading] = useState(false)
  const [tab,     setTab]     = useState<ViewTab>('timeline')

  async function handleSearch(q: string) {
    if (!q.trim()) { setJob(null); return }
    setLoading(true)
    await new Promise(r => setTimeout(r, 280)) // Phase 2: GET /admin/jobs/inspect?q=
    setJob(findJob(q))
    setLoading(false)
  }

  function handleRetryFull(jobId: string) {
    console.log('[JobInspector] retry full:', jobId)
    // Phase 2: POST /admin/jobs/{jobId}/retry
  }

  function handleRetryFromPhase(jobId: string, phase: PhaseName) {
    console.log('[JobInspector] retry from phase:', jobId, phase)
    // Phase 2: POST /admin/jobs/{jobId}/retry?from={phase}
  }

  function handleCancel(jobId: string) {
    console.log('[JobInspector] cancel:', jobId)
    // Phase 2: POST /admin/jobs/{jobId}/cancel
  }

  return (
    <Card className="overflow-hidden">
      <SectionHeader
        title="Job Inspector"
        subtitle="Phase-by-phase trace · error root-cause · retry and cancel controls"
      />

      <JobSearchBar
        value={query}
        onChange={setQuery}
        onSearch={handleSearch}
        loading={loading}
      />

      {loading && (
        <div className="px-4 py-4">
          <CardSkeleton />
        </div>
      )}

      {!loading && !job && (
        <EmptyState
          title={query ? `No job found for "${query}"` : 'No job selected'}
          description={
            query
              ? 'Try a different job ID, batch ID, full URL, platform name, or user IP.'
              : 'Search a job ID, batch ID, URL, platform, or IP to inspect its phase trace.'
          }
          icon={
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.25} className="h-10 w-10 text-slate-700">
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M16.65 10a6.65 6.65 0 11-13.3 0 6.65 6.65 0 0113.3 0z" />
            </svg>
          }
        />
      )}

      {!loading && job && (
        <>
          <JobHeader job={job} />

          <TabBar active={tab} onChange={setTab} job={job} />

          {tab === 'timeline' ? (
            <JobTraceTimeline traces={job.traces} />
          ) : (
            <JobPhaseTable traces={job.traces} />
          )}

          <JobRetryActions
            job={job}
            onRetryFull={handleRetryFull}
            onRetryFromPhase={handleRetryFromPhase}
            onCancel={handleCancel}
          />
        </>
      )}
    </Card>
  )
}
