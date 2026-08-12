import { useEffect, useState, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface TenantSettings {
  custom_domain: string | null
  features: Record<string, boolean>
}

interface TodayUsage {
  api_calls?: number
  downloads?: number
  webhook_deliveries?: number
  storage_bytes_used?: number
}

interface Tenant {
  id: string
  name: string
  slug: string
  plan: string
  plan_seats: number
  plan_api_calls_per_month: number
  plan_storage_gb: number
  status: string
  trial_ends_at: string | null
  subscription_id: string | null
  license_expires_at: string | null
  created_at: string
  updated_at: string
  tenant_settings: TenantSettings | null
  today_usage: TodayUsage
}

interface TenantsResponse {
  tenants: Tenant[]
  total: number
  plan_counts: Record<string, number>
  status_counts: Record<string, number>
}

const PLAN_COLORS: Record<string, string> = {
  starter: 'bg-slate-700 text-slate-200',
  growth: 'bg-blue-700 text-blue-100',
  enterprise: 'bg-purple-700 text-purple-100',
}

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-emerald-800 text-emerald-200',
  suspended: 'bg-amber-800 text-amber-200',
  canceled: 'bg-red-900 text-red-300',
}

function PlanBadge({ plan }: { plan: string }) {
  return (
    <span className={cn('inline-block rounded px-2 py-0.5 text-xs font-semibold uppercase', PLAN_COLORS[plan] ?? 'bg-slate-700 text-slate-200')}>
      {plan}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn('inline-block rounded px-2 py-0.5 text-xs font-semibold uppercase', STATUS_COLORS[status] ?? 'bg-slate-700 text-slate-200')}>
      {status}
    </span>
  )
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function TenantsPage() {
  const [data, setData] = useState<TenantsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [planFilter, setPlanFilter] = useState('')
  const [actionInProgress, setActionInProgress] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (statusFilter) params.set('status', statusFilter)
      if (planFilter) params.set('plan', planFilter)
      const result = await adminFetch<TenantsResponse>(`/enterprise/tenants?${params}`)
      setData(result)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, planFilter])

  useEffect(() => { fetchData() }, [fetchData])

  async function handleStatusChange(tenantId: string, newStatus: string) {
    setActionInProgress(p => ({ ...p, [tenantId]: true }))
    try {
      await adminPost(`/enterprise/tenants/${tenantId}/status`, { status: newStatus })
      setMsg(m => ({ ...m, [tenantId]: `Status → ${newStatus}` }))
      setTimeout(() => setMsg(m => { const n = { ...m }; delete n[tenantId]; return n }), 2000)
      fetchData()
    } catch (e) {
      setMsg(m => ({ ...m, [tenantId]: `Error: ${(e as Error).message}` }))
    } finally {
      setActionInProgress(p => ({ ...p, [tenantId]: false }))
    }
  }

  async function handlePlanChange(tenantId: string, newPlan: string) {
    setActionInProgress(p => ({ ...p, [tenantId + '_plan']: true }))
    try {
      await adminPost(`/enterprise/tenants/${tenantId}/plan`, { plan: newPlan })
      setMsg(m => ({ ...m, [tenantId + '_plan']: `Plan → ${newPlan}` }))
      setTimeout(() => setMsg(m => { const n = { ...m }; delete n[tenantId + '_plan']; return n }), 2000)
      fetchData()
    } catch (e) {
      setMsg(m => ({ ...m, [tenantId + '_plan']: `Error: ${(e as Error).message}` }))
    } finally {
      setActionInProgress(p => ({ ...p, [tenantId + '_plan']: false }))
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-bold text-slate-100">Tenants</h1>
          <p className="mt-0.5 text-xs text-slate-500">Multi-tenant enterprise accounts</p>
        </div>
        <button onClick={fetchData} className="rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600">
          ↺ Refresh
        </button>
      </div>

      {/* Summary cards */}
      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'Total', value: data.total },
            { label: 'Active', value: data.status_counts['active'] ?? 0 },
            { label: 'Suspended', value: data.status_counts['suspended'] ?? 0 },
            { label: 'Enterprise', value: data.plan_counts['enterprise'] ?? 0 },
          ].map(card => (
            <div key={card.label} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <p className="font-mono text-xs text-slate-500">{card.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-slate-100">{card.value}</p>
            </div>
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
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
          <option value="canceled">Canceled</option>
        </select>
        <select
          value={planFilter}
          onChange={e => setPlanFilter(e.target.value)}
          className="rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-xs text-slate-300"
        >
          <option value="">All plans</option>
          <option value="starter">Starter</option>
          <option value="growth">Growth</option>
          <option value="enterprise">Enterprise</option>
        </select>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {/* Table */}
      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-slate-600">Loading tenants…</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900">
                {['Tenant', 'Plan', 'Status', "Today's Usage", 'Seats', 'Custom Domain', 'Created', 'Actions'].map(h => (
                  <th key={h} className="px-3 py-2.5 font-mono font-semibold text-slate-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.tenants ?? []).map(t => (
                <tr key={t.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                  <td className="px-3 py-2.5">
                    <p className="font-medium text-slate-200">{t.name}</p>
                    <p className="font-mono text-[10px] text-slate-600">{t.slug}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <PlanBadge plan={t.plan} />
                    <p className="mt-0.5 font-mono text-[10px] text-slate-600">{t.plan_api_calls_per_month?.toLocaleString()}/mo</p>
                  </td>
                  <td className="px-3 py-2.5"><StatusBadge status={t.status} /></td>
                  <td className="px-3 py-2.5 font-mono text-slate-400">
                    <span title="API calls">⚡{t.today_usage?.api_calls ?? 0}</span>
                    {' · '}
                    <span title="Downloads">↓{t.today_usage?.downloads ?? 0}</span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-400">{t.plan_seats}</td>
                  <td className="px-3 py-2.5 font-mono text-slate-500 text-[10px]">
                    {t.tenant_settings?.custom_domain ?? '—'}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-500">{formatDate(t.created_at)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5">
                      {/* Plan change */}
                      <select
                        disabled={actionInProgress[t.id + '_plan']}
                        value={t.plan}
                        onChange={e => handlePlanChange(t.id, e.target.value)}
                        className="rounded border border-slate-700 bg-slate-800 px-1.5 py-1 text-[10px] text-slate-300"
                      >
                        <option value="starter">Starter</option>
                        <option value="growth">Growth</option>
                        <option value="enterprise">Enterprise</option>
                      </select>
                      {/* Status toggle */}
                      {t.status === 'active' ? (
                        <button
                          disabled={actionInProgress[t.id]}
                          onClick={() => handleStatusChange(t.id, 'suspended')}
                          className="rounded bg-amber-800/40 px-2 py-1 text-[10px] text-amber-300 hover:bg-amber-700/40 disabled:opacity-40"
                        >
                          Suspend
                        </button>
                      ) : (
                        <button
                          disabled={actionInProgress[t.id]}
                          onClick={() => handleStatusChange(t.id, 'active')}
                          className="rounded bg-emerald-800/40 px-2 py-1 text-[10px] text-emerald-300 hover:bg-emerald-700/40 disabled:opacity-40"
                        >
                          Activate
                        </button>
                      )}
                    </div>
                    {(msg[t.id] || msg[t.id + '_plan']) && (
                      <p className="mt-1 font-mono text-[10px] text-emerald-400">
                        {msg[t.id] || msg[t.id + '_plan']}
                      </p>
                    )}
                  </td>
                </tr>
              ))}
              {(data?.tenants ?? []).length === 0 && (
                <tr>
                  <td colSpan={8} className="py-10 text-center font-mono text-xs text-slate-600">
                    No tenants found
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
