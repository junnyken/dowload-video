import { useEffect, useState, useCallback } from 'react'
import { adminFetch } from '../utils/adminFetch'

interface TenantRef {
  name: string
  slug: string
  plan: string
}

interface UsageRow {
  tenant_id: string
  date: string
  api_calls: number
  downloads: number
  batch_jobs: number
  webhook_deliveries: number
  storage_bytes_used: number
  active_seats: number
  tenants: TenantRef | null
}

interface DailyAgg {
  date: string
  api_calls: number
  downloads: number
  batch_jobs: number
  webhook_deliveries: number
}

interface UsageTotals {
  api_calls: number
  downloads: number
  batch_jobs: number
  webhook_deliveries: number
  storage_bytes_used: number
}

interface UsageResponse {
  usage_rows: UsageRow[]
  daily_aggregated: DailyAgg[]
  totals: UsageTotals
  days: number
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

function MiniBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div className="mt-1 h-1 w-full rounded-full bg-surface">
      <div className="h-1 rounded-full bg-accent" style={{ width: `${pct}%` }} />
    </div>
  )
}

export default function EnterprisUsagePage() {
  const [data, setData] = useState<UsageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(30)
  const [view, setView] = useState<'chart' | 'table'>('chart')

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const result = await adminFetch<UsageResponse>(`/enterprise/usage?days=${days}`)
      setData(result)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [days])

  useEffect(() => { fetchData() }, [fetchData])

  // Per-tenant aggregation from usage_rows
  const tenantSummary: Record<string, { name: string; plan: string; api_calls: number; downloads: number; batch_jobs: number }> = {}
  for (const row of (data?.usage_rows ?? [])) {
    const tid = row.tenant_id
    if (!tenantSummary[tid]) {
      tenantSummary[tid] = { name: row.tenants?.name ?? tid.slice(0, 8), plan: row.tenants?.plan ?? '—', api_calls: 0, downloads: 0, batch_jobs: 0 }
    }
    tenantSummary[tid].api_calls += row.api_calls
    tenantSummary[tid].downloads += row.downloads
    tenantSummary[tid].batch_jobs += row.batch_jobs
  }
  const tenantList = Object.entries(tenantSummary).sort((a, b) => b[1].api_calls - a[1].api_calls)
  const maxApiCalls = Math.max(...tenantList.map(([, v]) => v.api_calls), 1)

  const dailyData = data?.daily_aggregated ?? []
  const maxDailyDownloads = Math.max(...dailyData.map(d => d.downloads), 1)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-bold text-fg">Enterprise Usage</h1>
          <p className="mt-0.5 text-xs text-fg-muted">API calls, downloads, batch jobs across all tenants</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={e => setDays(Number(e.target.value))}
            className="rounded border border-line bg-surface px-2 py-1.5 text-xs text-fg-2"
          >
            <option value={7}>Last 7d</option>
            <option value={30}>Last 30d</option>
            <option value={90}>Last 90d</option>
          </select>
          <div className="flex rounded border border-line overflow-hidden">
            {(['chart', 'table'] as const).map(v => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`px-3 py-1.5 text-xs ${view === v ? 'bg-surface-2 text-fg' : 'bg-surface text-fg-muted hover:text-fg-2'}`}
              >
                {v === 'chart' ? '▦' : '☰'} {v}
              </button>
            ))}
          </div>
          <button onClick={fetchData} className="rounded bg-surface-2 px-3 py-1.5 text-xs text-fg-2 hover:bg-line">
            ↺
          </button>
        </div>
      </div>

      {/* Totals */}
      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[
            { label: 'API Calls', value: data.totals.api_calls.toLocaleString() },
            { label: 'Downloads', value: data.totals.downloads.toLocaleString() },
            { label: 'Batch Jobs', value: data.totals.batch_jobs.toLocaleString() },
            { label: 'Webhook Deliveries', value: data.totals.webhook_deliveries.toLocaleString() },
            { label: 'Storage Used', value: formatBytes(data.totals.storage_bytes_used) },
          ].map(c => (
            <div key={c.label} className="rounded-lg border border-line bg-canvas p-4">
              <p className="font-mono text-xs text-fg-muted">{c.label}</p>
              <p className="mt-1 font-mono text-xl font-bold text-fg">{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>
      )}

      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-fg-muted">Loading usage data…</div>
      ) : (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* Daily chart (bar sparkline) */}
          <div className="rounded-lg border border-line bg-canvas p-4">
            <h2 className="mb-4 font-mono text-xs font-semibold uppercase text-fg-muted">Daily Downloads — {days}d</h2>
            {dailyData.length === 0 ? (
              <p className="font-mono text-xs text-fg-muted">No data in range</p>
            ) : (
              <div className="flex h-24 items-end gap-0.5">
                {dailyData.map(d => {
                  const h = maxDailyDownloads > 0 ? Math.max(2, (d.downloads / maxDailyDownloads) * 96) : 2
                  return (
                    <div
                      key={d.date}
                      title={`${d.date}: ${d.downloads} downloads`}
                      className="flex-1 rounded-sm bg-surface-2 hover:bg-accent-hover cursor-default transition-colors"
                      style={{ height: `${h}px` }}
                    />
                  )
                })}
              </div>
            )}
            <div className="mt-2 flex justify-between font-mono text-[10px] text-fg-muted">
              <span>{dailyData[0]?.date ?? ''}</span>
              <span>{dailyData[dailyData.length - 1]?.date ?? ''}</span>
            </div>
          </div>

          {/* Per-tenant breakdown */}
          <div className="rounded-lg border border-line bg-canvas p-4">
            <h2 className="mb-4 font-mono text-xs font-semibold uppercase text-fg-muted">Per-Tenant API Calls</h2>
            {tenantList.length === 0 ? (
              <p className="font-mono text-xs text-fg-muted">No tenant usage data</p>
            ) : (
              <div className="space-y-3">
                {tenantList.slice(0, 10).map(([, v]) => (
                  <div key={v.name}>
                    <div className="flex items-center justify-between font-mono text-xs">
                      <span className="text-fg-2">{v.name}</span>
                      <span className="text-fg-muted">{v.api_calls.toLocaleString()} calls</span>
                    </div>
                    <MiniBar value={v.api_calls} max={maxApiCalls} />
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Detailed table */}
          {view === 'table' && (
            <div className="lg:col-span-2 overflow-x-auto rounded-lg border border-line">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-line bg-canvas">
                    {['Date', 'Tenant', 'Plan', 'API Calls', 'Downloads', 'Batch Jobs', 'Webhooks', 'Storage'].map(h => (
                      <th key={h} className="px-3 py-2.5 font-mono font-semibold text-fg-muted">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(data?.usage_rows ?? []).map(row => (
                    <tr key={`${row.tenant_id}-${row.date}`} className="border-b border-line hover:bg-surface-2">
                      <td className="px-3 py-2 font-mono text-fg-muted">{row.date}</td>
                      <td className="px-3 py-2 text-fg-2">{row.tenants?.name ?? row.tenant_id.slice(0, 8)}</td>
                      <td className="px-3 py-2 font-mono text-[10px] text-fg-muted uppercase">{row.tenants?.plan ?? '—'}</td>
                      <td className="px-3 py-2 font-mono text-fg-muted">{row.api_calls.toLocaleString()}</td>
                      <td className="px-3 py-2 font-mono text-fg-muted">{row.downloads.toLocaleString()}</td>
                      <td className="px-3 py-2 font-mono text-fg-muted">{row.batch_jobs.toLocaleString()}</td>
                      <td className="px-3 py-2 font-mono text-fg-muted">{row.webhook_deliveries.toLocaleString()}</td>
                      <td className="px-3 py-2 font-mono text-fg-muted">{formatBytes(row.storage_bytes_used)}</td>
                    </tr>
                  ))}
                  {(data?.usage_rows ?? []).length === 0 && (
                    <tr>
                      <td colSpan={8} className="py-10 text-center font-mono text-xs text-fg-muted">No usage data in range</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
