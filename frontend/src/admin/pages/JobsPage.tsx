import { useEffect, useState } from 'react'
import { useAdminActiveJobs } from '../hooks/useAdminActiveJobs'
import { cn } from '../utils/cn'
import type { ActiveJob } from '../api/jobs'

// ─── Helpers ─────────────────────────────────────────────────────────────────

function truncate(str: string, max: number): string {
  return str.length <= max ? str : str.slice(0, max) + '…'
}

function shortId(id: string): string {
  return id.replace(/-/g, '').slice(0, 8).toUpperCase()
}

function detectPlatform(url: string, given?: string): string {
  if (given) return given
  const lower = url.toLowerCase()
  if (lower.includes('youtube.com') || lower.includes('youtu.be')) return 'YouTube'
  if (lower.includes('tiktok.com'))    return 'TikTok'
  if (lower.includes('instagram.com')) return 'Instagram'
  if (lower.includes('facebook.com') || lower.includes('fb.watch')) return 'Facebook'
  if (lower.includes('twitter.com') || lower.includes('x.com'))  return 'Twitter/X'
  if (lower.includes('soundcloud.com')) return 'SoundCloud'
  if (lower.includes('threads.net'))  return 'Threads'
  return 'Unknown'
}

function useElapsedSeconds(createdAt: string): number {
  const [elapsed, setElapsed] = useState(() =>
    Math.floor((Date.now() - new Date(createdAt).getTime()) / 1000),
  )
  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - new Date(createdAt).getTime()) / 1000))
    }, 1000)
    return () => clearInterval(id)
  }, [createdAt])
  return elapsed
}

function fmtElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  if (m < 60) return `${m}m ${s}s`
  const h = Math.floor(m / 60)
  const rm = m % 60
  return `${h}h ${rm}m`
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso
  }
}

// ─── Status pill ─────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, { pill: string; dot: string; label: string }> = {
  processing: {
    pill: 'border-blue-700 bg-blue-900 text-blue-300',
    dot: 'bg-blue-400 animate-pulse',
    label: 'RUNNING',
  },
  pending: {
    pill: 'border-amber-900 bg-amber-950 text-amber-400',
    dot: 'bg-amber-400',
    label: 'PENDING',
  },
}

function StatusPill({ status }: { status: string }) {
  const cfg = STATUS_STYLE[status] ?? {
    pill: 'border-slate-700 bg-slate-800 text-slate-400',
    dot: 'bg-slate-500',
    label: status.toUpperCase(),
  }
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

// ─── Platform badge ───────────────────────────────────────────────────────────

const PLATFORM_COLOR: Record<string, string> = {
  youtube:     'bg-red-950 text-red-400 border-red-900',
  tiktok:      'bg-slate-800 text-slate-300 border-slate-700',
  instagram:   'bg-purple-950 text-purple-400 border-purple-900',
  facebook:    'bg-blue-950 text-blue-400 border-blue-900',
  'twitter/x': 'bg-slate-800 text-slate-300 border-slate-700',
  soundcloud:  'bg-orange-950 text-orange-400 border-orange-900',
  threads:     'bg-slate-800 text-slate-300 border-slate-700',
}

function PlatformBadge({ platform }: { platform: string }) {
  const cls = PLATFORM_COLOR[platform.toLowerCase()] ?? 'bg-slate-800 text-slate-400 border-slate-700'
  return (
    <span className={cn('rounded border px-1.5 py-0.5 text-[10px] font-semibold', cls)}>
      {platform}
    </span>
  )
}

// ─── Elapsed cell ─────────────────────────────────────────────────────────────

function ElapsedCell({ createdAt }: { createdAt: string }) {
  const s = useElapsedSeconds(createdAt)
  return <span className="font-mono text-xs text-slate-300">{fmtElapsed(s)}</span>
}

// ─── Detail panel ─────────────────────────────────────────────────────────────

function DetailRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <>
      <span className="text-slate-500">{label}</span>
      <span className={cn('text-slate-200', mono && 'font-mono')}>{value}</span>
    </>
  )
}

function JobDetailPanel({ job, onClose }: { job: ActiveJob; onClose: () => void }) {
  const platform = detectPlatform(job.original_url, job.platform)
  const elapsed  = useElapsedSeconds(job.created_at)

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900 p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-bold text-slate-100">{job.id}</span>
          <StatusPill status={job.status} />
          <PlatformBadge platform={platform} />
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-300 transition-colors"
          aria-label="Close detail"
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-4 w-4">
            <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-lg border border-slate-800 bg-slate-950/60 px-4 py-3 text-xs">
        <DetailRow label="Job ID"   value={job.id} mono />
        <DetailRow label="Batch ID" value={job.batch_id ?? '—'} mono />
        <DetailRow label="Status"   value={job.status} />
        <DetailRow label="Platform" value={platform} />
        <DetailRow label="Created"  value={fmtTime(job.created_at)} mono />
        <DetailRow label="Elapsed"  value={fmtElapsed(elapsed)} mono />
      </div>

      <div className="rounded-lg border border-slate-800 bg-slate-950/50 px-3 py-2">
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-slate-600">URL</p>
        <a
          href={job.original_url}
          target="_blank"
          rel="noopener noreferrer"
          className="break-all font-mono text-xs text-blue-400 hover:text-blue-300 transition-colors"
        >
          {job.original_url}
        </a>
      </div>
    </div>
  )
}

// ─── Table row ────────────────────────────────────────────────────────────────

function JobRow({
  job,
  selected,
  onClick,
}: {
  job: ActiveJob
  selected: boolean
  onClick: () => void
}) {
  const platform = detectPlatform(job.original_url, job.platform)

  return (
    <tr
      onClick={onClick}
      className={cn(
        'cursor-pointer border-b border-slate-800 transition-colors',
        selected ? 'bg-blue-950/30' : 'hover:bg-slate-800/50',
      )}
    >
      <td className="px-4 py-2.5">
        <span className="font-mono text-xs font-bold text-slate-200">{shortId(job.id)}</span>
      </td>
      <td className="px-3 py-2.5"><PlatformBadge platform={platform} /></td>
      <td className="px-3 py-2.5"><StatusPill status={job.status} /></td>
      <td className="max-w-[240px] px-3 py-2.5">
        <span className="block truncate font-mono text-xs text-slate-400" title={job.original_url}>
          {truncate(job.original_url, 40)}
        </span>
      </td>
      <td className="px-3 py-2.5 text-right">
        <ElapsedCell createdAt={job.created_at} />
      </td>
    </tr>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export function JobsPage() {
  const [selected, setSelected] = useState<ActiveJob | null>(null)

  const { data, isLoading, isFetching, error, refetch, dataUpdatedAt } =
    useAdminActiveJobs(15_000)

  const processing = data?.processing ?? []
  const pending    = data?.pending ?? []
  const allJobs    = [...processing, ...pending]
  const totalCount = allJobs.length

  const errorMsg = error instanceof Error ? error.message : error ? 'Unknown error' : null
  const lastCheckedStr = dataUpdatedAt ? new Date(dataUpdatedAt).toISOString() : null

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-base font-semibold text-slate-100">Active Jobs</h1>
            {!isLoading && (
              <span
                className={cn(
                  'inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full px-1.5',
                  'font-mono text-[11px] font-bold tabular-nums',
                  totalCount > 0 ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-500',
                )}
              >
                {totalCount}
              </span>
            )}
            {isFetching && (
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-700 border-t-blue-500" />
            )}
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            Live view — refreshes every 15 s · click a row for details
          </p>
        </div>

        <div className="flex items-center gap-2">
          {lastCheckedStr && (
            <span className="font-mono text-[10px] text-slate-600">
              checked {fmtTime(lastCheckedStr)}
            </span>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className={cn(
              'flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800',
              'px-3 py-1.5 text-xs text-slate-300 transition-colors',
              'hover:border-slate-600 hover:text-slate-100 disabled:opacity-40',
            )}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5">
              <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="rounded-lg border border-red-900 bg-red-950/50 px-4 py-3 text-sm text-red-400">
          Failed to fetch jobs: {errorMsg}
        </div>
      )}

      {!isLoading && totalCount > 0 && (
        <div className="flex gap-3 text-xs text-slate-500">
          <span><span className="font-bold text-blue-400">{processing.length}</span> running</span>
          <span><span className="font-bold text-amber-400">{pending.length}</span> pending</span>
        </div>
      )}

      {!isLoading && totalCount > 0 && (
        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800 bg-slate-800/50">
                  <th className="px-4 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">ID</th>
                  <th className="px-3 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">Platform</th>
                  <th className="px-3 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">Status</th>
                  <th className="px-3 py-2.5 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">URL</th>
                  <th className="px-3 py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-slate-500">Elapsed</th>
                </tr>
              </thead>
              <tbody>
                {allJobs.map(job => (
                  <JobRow
                    key={job.id}
                    job={job}
                    selected={selected?.id === job.id}
                    onClick={() => setSelected(selected?.id === job.id ? null : job)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selected && (
        <JobDetailPanel job={selected} onClose={() => setSelected(null)} />
      )}

      {!isLoading && !errorMsg && totalCount === 0 && (
        <div className="flex flex-col items-center justify-center rounded-xl border border-slate-800 bg-slate-900 py-16 gap-3">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="h-10 w-10 text-slate-700">
            <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <p className="text-sm font-medium text-slate-400">No active jobs right now</p>
          {lastCheckedStr && (
            <p className="font-mono text-xs text-slate-600">
              Last checked at {fmtTime(lastCheckedStr)}
            </p>
          )}
        </div>
      )}

      {isLoading && (
        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
          {[...Array(5)].map((_, i) => (
            <div key={i} className={cn('flex gap-4 px-4 py-3 border-b border-slate-800', i === 4 && 'border-b-0')}>
              <div className="h-4 w-20 animate-pulse rounded bg-slate-800" />
              <div className="h-4 w-16 animate-pulse rounded bg-slate-800" />
              <div className="h-4 w-14 animate-pulse rounded bg-slate-800" />
              <div className="h-4 w-48 animate-pulse rounded bg-slate-800" />
              <div className="ml-auto h-4 w-10 animate-pulse rounded bg-slate-800" />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
