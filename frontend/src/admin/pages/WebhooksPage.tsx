import { useEffect, useState, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface TenantRef {
  name: string
  slug: string
  plan: string
}

interface WebhookEndpoint {
  id: string
  tenant_id: string
  url: string
  events: string[]
  is_active: boolean
  created_by: string | null
  last_triggered_at: string | null
  total_deliveries: number
  successful_deliveries: number
  failed_deliveries: number
  created_at: string
  tenants: TenantRef | null
}

interface Delivery {
  id: string
  event_type: string
  status: string
  attempt_count: number
  last_attempt_at: string
  response_status: number | null
  error_message: string | null
  created_at: string
}

interface WebhooksResponse {
  endpoints: WebhookEndpoint[]
  total: number
  total_active: number
  total_deliveries: number
  total_failed: number
}

interface DeliveriesResponse {
  deliveries: Delivery[]
}

const DELIVERY_STATUS_COLORS: Record<string, string> = {
  delivered: 'text-emerald-400',
  pending: 'text-amber-400',
  failed: 'text-red-400',
  abandoned: 'text-slate-500',
}

function formatRelative(iso: string | null): string {
  if (!iso) return 'Never'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function SuccessRate({ ok, total }: { ok: number; total: number }) {
  const rate = total ? Math.round((ok / total) * 100) : 0
  const color = rate >= 90 ? 'text-emerald-400' : rate >= 70 ? 'text-amber-400' : 'text-red-400'
  return <span className={cn('font-mono font-bold', color)}>{rate}%</span>
}

export default function WebhooksPage() {
  const [data, setData] = useState<WebhooksResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [deliveries, setDeliveries] = useState<Record<string, Delivery[]>>({})
  const [loadingDeliveries, setLoadingDeliveries] = useState<Record<string, boolean>>({})
  const [toggling, setToggling] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const result = await adminFetch<WebhooksResponse>('/enterprise/webhooks')
      setData(result)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  async function loadDeliveries(endpointId: string) {
    if (deliveries[endpointId]) return
    setLoadingDeliveries(p => ({ ...p, [endpointId]: true }))
    try {
      const result = await adminFetch<DeliveriesResponse>(`/enterprise/webhooks/${endpointId}/deliveries`)
      setDeliveries(d => ({ ...d, [endpointId]: result.deliveries }))
    } finally {
      setLoadingDeliveries(p => ({ ...p, [endpointId]: false }))
    }
  }

  function toggleExpanded(id: string) {
    if (expanded === id) {
      setExpanded(null)
    } else {
      setExpanded(id)
      loadDeliveries(id)
    }
  }

  async function handleToggle(endpointId: string, currentState: boolean) {
    setToggling(p => ({ ...p, [endpointId]: true }))
    try {
      await adminPost(`/enterprise/webhooks/${endpointId}/toggle`)
      const label = currentState ? 'Disabled' : 'Enabled'
      setMsg(m => ({ ...m, [endpointId]: label }))
      setTimeout(() => setMsg(m => { const n = { ...m }; delete n[endpointId]; return n }), 2000)
      fetchData()
    } catch (e) {
      setMsg(m => ({ ...m, [endpointId]: `Error: ${(e as Error).message}` }))
    } finally {
      setToggling(p => ({ ...p, [endpointId]: false }))
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-bold text-slate-100">Webhooks</h1>
          <p className="mt-0.5 text-xs text-slate-500">HMAC-signed webhook endpoints per tenant</p>
        </div>
        <button onClick={fetchData} className="rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600">
          ↺ Refresh
        </button>
      </div>

      {/* Summary */}
      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'Endpoints', value: data.total },
            { label: 'Active', value: data.total_active },
            { label: 'Total Delivered', value: data.total_deliveries.toLocaleString() },
            { label: 'Failed', value: data.total_failed.toLocaleString() },
          ].map(c => (
            <div key={c.label} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <p className="font-mono text-xs text-slate-500">{c.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-slate-100">{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-slate-600">Loading webhooks…</div>
      ) : (
        <div className="space-y-2">
          {(data?.endpoints ?? []).map(ep => (
            <div key={ep.id} className="rounded-lg border border-slate-800 bg-slate-900">
              {/* Endpoint row */}
              <div className="flex items-start gap-3 p-4">
                <div className="flex-1 min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={cn(
                      'inline-block h-2 w-2 rounded-full',
                      ep.is_active ? 'bg-emerald-400' : 'bg-slate-600',
                    )} />
                    <span className="font-mono text-sm font-medium text-slate-200 truncate max-w-md">{ep.url}</span>
                    <span className="font-mono text-[10px] text-slate-500">{ep.tenants?.name ?? '—'}</span>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {(ep.events ?? []).map(ev => (
                      <span key={ev} className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                        {ev}
                      </span>
                    ))}
                  </div>
                  <div className="flex gap-4 font-mono text-[10px] text-slate-600">
                    <span>✓ {ep.successful_deliveries} delivered</span>
                    <span>✗ {ep.failed_deliveries} failed</span>
                    <span>Last: {formatRelative(ep.last_triggered_at)}</span>
                    {ep.total_deliveries > 0 && (
                      <span>Rate: <SuccessRate ok={ep.successful_deliveries} total={ep.total_deliveries} /></span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    disabled={toggling[ep.id]}
                    onClick={() => handleToggle(ep.id, ep.is_active)}
                    className={cn(
                      'rounded px-2.5 py-1 text-[10px] font-semibold transition-colors disabled:opacity-40',
                      ep.is_active
                        ? 'bg-red-900/40 text-red-300 hover:bg-red-800/40'
                        : 'bg-emerald-900/40 text-emerald-300 hover:bg-emerald-800/40',
                    )}
                  >
                    {toggling[ep.id] ? '…' : ep.is_active ? 'Disable' : 'Enable'}
                  </button>
                  <button
                    onClick={() => toggleExpanded(ep.id)}
                    className="rounded px-2.5 py-1 text-[10px] text-slate-400 hover:bg-slate-800"
                  >
                    {expanded === ep.id ? '▲ Hide' : '▼ Deliveries'}
                  </button>
                </div>
              </div>
              {msg[ep.id] && (
                <p className="px-4 pb-2 font-mono text-[10px] text-emerald-400">{msg[ep.id]}</p>
              )}

              {/* Delivery history */}
              {expanded === ep.id && (
                <div className="border-t border-slate-800 px-4 pb-4 pt-3">
                  {loadingDeliveries[ep.id] ? (
                    <p className="font-mono text-xs text-slate-600">Loading deliveries…</p>
                  ) : (deliveries[ep.id] ?? []).length === 0 ? (
                    <p className="font-mono text-xs text-slate-600">No deliveries yet</p>
                  ) : (
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr className="text-slate-500 font-mono">
                          <th className="pb-2 pr-4">Event</th>
                          <th className="pb-2 pr-4">Status</th>
                          <th className="pb-2 pr-4">HTTP</th>
                          <th className="pb-2 pr-4">Attempts</th>
                          <th className="pb-2">Time</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(deliveries[ep.id] ?? []).map(d => (
                          <tr key={d.id} className="border-t border-slate-800/50">
                            <td className="py-1.5 pr-4 font-mono text-slate-400">{d.event_type}</td>
                            <td className={cn('py-1.5 pr-4 font-mono font-semibold', DELIVERY_STATUS_COLORS[d.status] ?? 'text-slate-400')}>
                              {d.status}
                            </td>
                            <td className="py-1.5 pr-4 font-mono text-slate-500">{d.response_status ?? '—'}</td>
                            <td className="py-1.5 pr-4 font-mono text-slate-500">{d.attempt_count}</td>
                            <td className="py-1.5 font-mono text-slate-600">{formatRelative(d.last_attempt_at)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          ))}
          {(data?.endpoints ?? []).length === 0 && (
            <div className="py-12 text-center font-mono text-xs text-slate-600">No webhook endpoints found</div>
          )}
        </div>
      )}
    </div>
  )
}
