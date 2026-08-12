import { useEffect, useState, useCallback } from 'react'
import { adminFetch } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface AnalysisJob {
  id: string
  user_id: string | null
  tenant_id: string | null
  media_url: string | null
  media_fingerprint: string | null
  duration_seconds: number | null
  analyses_requested: string[]
  status: string
  analyzer_version: string
  error_message: string | null
  created_at: string
  updated_at: string
  expires_at: string
}

interface JobsResponse {
  jobs: AnalysisJob[]
  total: number
  status_counts: Record<string, number>
  analysis_type_counts: Record<string, number>
}

interface StatsResponse {
  total_jobs: number
  status_counts: Record<string, number>
  analysis_type_counts: Record<string, number>
  total_analyses_metered: number
  total_media_minutes: number
  days: number
}

const STATUS_COLORS: Record<string, string> = {
  done: 'bg-emerald-800 text-emerald-200',
  processing: 'bg-blue-800 text-blue-200',
  queued: 'bg-amber-800 text-amber-200',
  failed: 'bg-red-900 text-red-300',
  cached: 'bg-purple-800 text-purple-200',
}

const TYPE_ICONS: Record<string, string> = {
  trim: '✂',
  clip: '◆',
  gif: '▶',
  metadata: '≡',
  summary: '∑',
}

function formatRelative(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function maskId(id: string | null): string {
  if (!id) return '—'
  return id.length > 12 ? `${id.slice(0, 8)}…` : id
}

export default function AiAnalysisPage() {
  const [jobs, setJobs] = useState<JobsResponse | null>(null)
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [days, setDays] = useState(30)

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (statusFilter) params.set('status', statusFilter)
      const [j, s] = await Promise.all([
        adminFetch<JobsResponse>(`/enterprise/analysis/jobs?${params}`),
        adminFetch<StatsResponse>(`/enterprise/analysis/stats?days=${days}`),
      ])
      setJobs(j)
      setStats(s)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, days])

  useEffect(() => { fetchAll() }, [fetchAll])

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-bold text-slate-100">AI Analysis</h1>
          <p className="mt-0.5 text-xs text-slate-500">Smart trim · clip · GIF · metadata jobs</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={e => setDays(Number(e.target.value))}
            className="rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-xs text-slate-300"
          >
            <option value={7}>7d</option>
            <option value={30}>30d</option>
            <option value={90}>90d</option>
          </select>
          <button onClick={fetchAll} className="rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600">
            ↺ Refresh
          </button>
        </div>
      </div>

      {/* Stats row */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[
            { label: 'Total Jobs', value: stats.total_jobs },
            { label: 'Done', value: stats.status_counts['done'] ?? 0 },
            { label: 'Failed', value: stats.status_counts['failed'] ?? 0 },
            { label: 'Analyses Metered', value: stats.total_analyses_metered.toLocaleString() },
            { label: 'Media Minutes', value: `${stats.total_media_minutes}m` },
          ].map(c => (
            <div key={c.label} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <p className="font-mono text-xs text-slate-500">{c.label}</p>
              <p className="mt-1 font-mono text-xl font-bold text-slate-100">{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Analysis type breakdown */}
      {stats && Object.keys(stats.analysis_type_counts).length > 0 && (
        <div className="flex flex-wrap gap-2">
          <span className="font-mono text-xs text-slate-500 self-center">Types:</span>
          {Object.entries(stats.analysis_type_counts).map(([type, count]) => (
            <span key={type} className="flex items-center gap-1 rounded bg-slate-800 px-2.5 py-1 font-mono text-xs text-slate-300">
              <span>{TYPE_ICONS[type] ?? '○'}</span>
              <span>{type}</span>
              <span className="text-slate-500">×{count}</span>
            </span>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-2">
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-xs text-slate-300"
        >
          <option value="">All status</option>
          <option value="queued">Queued</option>
          <option value="processing">Processing</option>
          <option value="done">Done</option>
          <option value="failed">Failed</option>
          <option value="cached">Cached</option>
        </select>
      </div>

      {error && (
        <div className="rounded border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-slate-600">Loading analysis jobs…</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900">
                {['Job ID', 'User', 'Analyses', 'Duration', 'Status', 'Version', 'Created', 'Error'].map(h => (
                  <th key={h} className="px-3 py-2.5 font-mono font-semibold text-slate-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(jobs?.jobs ?? []).map(j => (
                <tr key={j.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                  <td className="px-3 py-2.5 font-mono text-[10px] text-slate-500">{j.id.slice(0, 8)}…</td>
                  <td className="px-3 py-2.5 font-mono text-slate-500 text-[10px]">{maskId(j.user_id)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {(j.analyses_requested ?? []).map(t => (
                        <span key={t} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                          {TYPE_ICONS[t] ?? '○'} {t}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-500">
                    {j.duration_seconds ? `${j.duration_seconds.toFixed(1)}s` : '—'}
                  </td>
                  <td className="px-3 py-2.5">
                    <span className={cn('inline-block rounded px-2 py-0.5 text-[10px] font-semibold uppercase', STATUS_COLORS[j.status] ?? 'bg-slate-700 text-slate-300')}>
                      {j.status}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[10px] text-slate-600">{j.analyzer_version}</td>
                  <td className="px-3 py-2.5 font-mono text-slate-500">{formatRelative(j.created_at)}</td>
                  <td className="px-3 py-2.5 max-w-[180px]">
                    {j.error_message ? (
                      <span className="truncate block font-mono text-[10px] text-red-400" title={j.error_message}>
                        {j.error_message.slice(0, 60)}{j.error_message.length > 60 ? '…' : ''}
                      </span>
                    ) : <span className="text-slate-700">—</span>}
                  </td>
                </tr>
              ))}
              {(jobs?.jobs ?? []).length === 0 && (
                <tr>
                  <td colSpan={8} className="py-10 text-center font-mono text-xs text-slate-600">
                    No analysis jobs found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
