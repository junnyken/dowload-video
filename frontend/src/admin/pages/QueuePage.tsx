import { useState, useEffect, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface ActiveJob {
  job_id?: string
  id?: string
  platform: string
  url?: string
  status?: string
  started_at?: string
  elapsed_ms?: number
  worker_id?: string
  current_phase?: string
}

interface DeadLetterJob {
  id: string
  platform: string
  url: string
  error: string
  retries: number
  failedAt: string
}

interface QueueWorkersResponse {
  success: boolean
  queues: {
    default: number
    priority: number
    archive: number
    analysis: number
    total: number
    error?: string
  }
  active_jobs: ActiveJob[]
  active_count: number
  stale_jobs: ActiveJob[]
  dead_letter: DeadLetterJob[]
  dead_letter_count: number
  queue_health?: {
    ok?: boolean
    throughput_per_min?: number
    error?: string
  }
}

function QueueDepthBar({ name, count, max }: { name: string; count: number; max: number }) {
  const pct = max > 0 ? Math.min((count / max) * 100, 100) : 0
  const color = count > 50 ? 'bg-red-500' : count > 10 ? 'bg-amber-500' : 'bg-emerald-500'
  return (
    <div className="flex items-center gap-3">
      <span className="w-20 font-mono text-[10px] text-slate-500">{name}</span>
      <div className="flex-1 overflow-hidden rounded-full bg-slate-800 h-1.5">
        <div className={cn('h-1.5 rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right font-mono text-xs font-semibold text-slate-300">{count}</span>
    </div>
  )
}

export function QueuePage() {
  const [data, setData] = useState<QueueWorkersResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState<string | null>(null)

  const fetchWorkers = useCallback(async () => {
    try {
      const res = await adminFetch<QueueWorkersResponse>('/queue/workers')
      setData(res)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load queue data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchWorkers()
    const id = setInterval(fetchWorkers, 15_000)
    return () => clearInterval(id)
  }, [fetchWorkers])

  async function handleRetry(jobId: string) {
    setRetrying(jobId)
    try {
      await adminPost(`/queue/jobs/${jobId}/retry`)
      await fetchWorkers()
    } catch (e) {
      console.error('[QueuePage] retry failed:', e)
    } finally {
      setRetrying(null)
    }
  }

  async function handleCancel(jobId: string) {
    setCancelling(jobId)
    try {
      await adminPost(`/queue/jobs/${jobId}/cancel`)
      await fetchWorkers()
    } catch (e) {
      console.error('[QueuePage] cancel failed:', e)
    } finally {
      setCancelling(null)
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-sm text-slate-500 animate-pulse">Loading queue state…</div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <p className="text-sm text-red-400">{error ?? 'No data'}</p>
        <button onClick={fetchWorkers} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">Retry</button>
      </div>
    )
  }

  const { queues, active_jobs, dead_letter, queue_health } = data
  const maxQ = Math.max(queues.default, queues.priority, queues.archive, queues.analysis, 1)

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm font-semibold text-slate-100">Queue & Workers</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            {data.active_count} active · {data.dead_letter_count} dead-letter · auto-refresh 15s
          </p>
        </div>
        <button onClick={fetchWorkers} className="text-[10px] text-slate-500 hover:text-slate-300">↺ Refresh</button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {/* Queue depths */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
          <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">Queue Depths</h3>
          <div className="flex flex-col gap-3">
            <QueueDepthBar name="default" count={queues.default ?? 0} max={maxQ} />
            <QueueDepthBar name="priority" count={queues.priority ?? 0} max={maxQ} />
            <QueueDepthBar name="archive"  count={queues.archive  ?? 0} max={maxQ} />
            <QueueDepthBar name="analysis" count={queues.analysis ?? 0} max={maxQ} />
          </div>
          <div className="mt-3 border-t border-slate-800 pt-3 flex justify-between text-xs">
            <span className="text-slate-500">Total pending</span>
            <span className="font-mono font-semibold text-slate-200">{queues.total ?? 0}</span>
          </div>
          {queue_health && (
            <div className="mt-1 flex justify-between text-xs">
              <span className="text-slate-500">Throughput</span>
              <span className="font-mono text-slate-400">
                {queue_health.throughput_per_min != null
                  ? `${queue_health.throughput_per_min}/min`
                  : queue_health.error ?? '—'}
              </span>
            </div>
          )}
        </div>

        {/* Active jobs */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
          <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Active Jobs ({active_jobs.length})
          </h3>
          {active_jobs.length === 0 ? (
            <p className="text-xs text-slate-600">No active jobs right now.</p>
          ) : (
            <div className="flex flex-col gap-2 max-h-48 overflow-y-auto pr-1">
              {active_jobs.map((j, i) => {
                const id = j.job_id ?? j.id ?? String(i)
                return (
                  <div key={id} className="rounded-xl border border-slate-800 bg-slate-900 p-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[10px] text-blue-400 uppercase">{j.platform}</span>
                      {j.current_phase && (
                        <span className="text-[9px] text-slate-500">{j.current_phase}</span>
                      )}
                    </div>
                    {j.url && (
                      <p className="mt-1 truncate text-[10px] text-slate-600">{j.url}</p>
                    )}
                    <div className="mt-1 flex items-center justify-between">
                      {j.elapsed_ms != null && (
                        <span className="text-[9px] text-slate-600">{Math.round(j.elapsed_ms / 1000)}s</span>
                      )}
                      <button
                        onClick={() => handleCancel(id)}
                        disabled={cancelling === id}
                        className="text-[9px] text-red-500 hover:text-red-400 disabled:opacity-40"
                      >
                        {cancelling === id ? 'cancelling…' : 'cancel'}
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Dead letter */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
        <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Dead Letter — Failed Last 1h ({dead_letter.length})
        </h3>
        {dead_letter.length === 0 ? (
          <p className="text-xs text-slate-600">No failed jobs in the last hour.</p>
        ) : (
          <div className="overflow-hidden rounded-xl border border-slate-800">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-800 bg-slate-900/80">
                  <th className="py-2 pl-3 text-left font-mono text-[9px] uppercase tracking-widest text-slate-600">Platform</th>
                  <th className="py-2 text-left font-mono text-[9px] uppercase tracking-widest text-slate-600">Error</th>
                  <th className="py-2 text-right font-mono text-[9px] uppercase tracking-widest text-slate-600">Retries</th>
                  <th className="py-2 text-right font-mono text-[9px] uppercase tracking-widest text-slate-600">Failed</th>
                  <th className="py-2 pr-3 text-right font-mono text-[9px] uppercase tracking-widest text-slate-600">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/50">
                {dead_letter.map(j => (
                  <tr key={j.id} className="hover:bg-slate-800/20">
                    <td className="py-2 pl-3 font-mono text-[10px] uppercase text-slate-400">{j.platform}</td>
                    <td className="py-2 max-w-[200px] truncate text-slate-500">{j.error || '—'}</td>
                    <td className="py-2 text-right font-mono text-slate-500">{j.retries}</td>
                    <td className="py-2 text-right text-slate-600">{j.failedAt}</td>
                    <td className="py-2 pr-3 text-right">
                      <button
                        onClick={() => handleRetry(j.id)}
                        disabled={retrying === j.id}
                        className="rounded-lg border border-slate-700 px-2 py-0.5 text-[9px] text-slate-300 hover:bg-slate-700 disabled:opacity-40"
                      >
                        {retrying === j.id ? '…' : 'retry'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
