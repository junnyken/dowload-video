import { useEffect, useState, useCallback, useRef } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface TenantRef {
  name: string
  slug: string
  plan: string
}

interface ApiKey {
  id: string
  tenant_id: string
  key_prefix: string
  label: string
  scopes: string[]
  rate_limit_per_min: number
  rate_limit_per_day: number
  is_active: boolean
  created_by: string | null
  last_used_at: string | null
  requests_today: number
  requests_this_month: number
  requests_total: number
  ip_allowlist: string[] | null
  expires_at: string | null
  created_at: string
  tenants: TenantRef | null
}

interface ApiKeysResponse {
  api_keys: ApiKey[]
  total: number
  total_active: number
  total_requests_today: number
  total_requests_month: number
}

function formatRelative(iso: string | null): string {
  if (!iso) return 'Never'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation()
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <button
      onClick={handleCopy}
      title="Copy key prefix"
      className={cn(
        'ml-1 rounded px-1 py-0.5 font-mono text-[9px] transition-colors',
        copied
          ? 'bg-emerald-900/60 text-emerald-300'
          : 'bg-slate-800 text-slate-500 hover:bg-slate-700 hover:text-slate-300',
      )}
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

const SCOPE_COLORS: Record<string, string> = {
  read: 'bg-blue-900/60 text-blue-300',
  write: 'bg-emerald-900/60 text-emerald-300',
  batch: 'bg-purple-900/60 text-purple-300',
  webhook: 'bg-amber-900/60 text-amber-300',
  admin: 'bg-red-900/60 text-red-300',
}

export default function ApiKeysPage() {
  const [data, setData] = useState<ApiKeysResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeOnly, setActiveOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [toggling, setToggling] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (activeOnly) params.set('active_only', 'true')
      const result = await adminFetch<ApiKeysResponse>(`/enterprise/api-keys?${params}`)
      setData(result)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [activeOnly])

  useEffect(() => { fetchData() }, [fetchData])

  async function handleToggle(keyId: string, currentState: boolean) {
    setToggling(p => ({ ...p, [keyId]: true }))
    try {
      await adminPost(`/enterprise/api-keys/${keyId}/toggle`)
      const label = currentState ? 'Deactivated' : 'Activated'
      setMsg(m => ({ ...m, [keyId]: label }))
      setTimeout(() => setMsg(m => { const n = { ...m }; delete n[keyId]; return n }), 2000)
      fetchData()
    } catch (e) {
      setMsg(m => ({ ...m, [keyId]: `Error: ${(e as Error).message}` }))
    } finally {
      setToggling(p => ({ ...p, [keyId]: false }))
    }
  }

  const keys = (data?.api_keys ?? []).filter(k =>
    !search || k.key_prefix.includes(search) || k.label.toLowerCase().includes(search.toLowerCase())
      || k.tenants?.name.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-bold text-slate-100">Partner API Keys</h1>
          <p className="mt-0.5 text-xs text-slate-500">vgp_ prefixed keys for tenant partner access</p>
        </div>
        <button onClick={fetchData} className="rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600">
          ↺ Refresh
        </button>
      </div>

      {/* Summary */}
      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'Total Keys', value: data.total },
            { label: 'Active', value: data.total_active },
            { label: 'Req Today', value: data.total_requests_today.toLocaleString() },
            { label: 'Req Month', value: data.total_requests_month.toLocaleString() },
          ].map(c => (
            <div key={c.label} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <p className="font-mono text-xs text-slate-500">{c.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-slate-100">{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          placeholder="Search key, label, tenant…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="rounded border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 placeholder-slate-600 w-52"
        />
        <label className="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer">
          <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
          Active only
        </label>
      </div>

      {error && (
        <div className="rounded border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-slate-600">Loading API keys…</div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900">
                {['Key', 'Tenant', 'Scopes', 'IP Allowlist', 'Created By', 'Requests', 'Rate Limits', 'Last Used', 'Expires', 'Status'].map(h => (
                  <th key={h} className="px-3 py-2.5 font-mono font-semibold text-slate-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-0.5">
                      <p className="font-mono font-medium text-slate-200">{k.key_prefix}…</p>
                      <CopyButton text={k.key_prefix} />
                    </div>
                    <p className="text-[10px] text-slate-600">{k.label}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <p className="text-slate-300">{k.tenants?.name ?? '—'}</p>
                    <span className={cn('inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase mt-0.5',
                      k.tenants?.plan === 'enterprise' ? 'bg-purple-700 text-purple-100'
                      : k.tenants?.plan === 'growth' ? 'bg-blue-700 text-blue-100'
                      : 'bg-slate-700 text-slate-200')}>
                      {k.tenants?.plan ?? '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {(k.scopes ?? []).map(s => (
                        <span key={s} className={cn('rounded px-1.5 py-0.5 text-[10px] font-semibold', SCOPE_COLORS[s] ?? 'bg-slate-700 text-slate-300')}>
                          {s}
                        </span>
                      ))}
                    </div>
                  </td>
                  {/* IP Allowlist */}
                  <td className="px-3 py-2.5">
                    {(!k.ip_allowlist || k.ip_allowlist.length === 0) ? (
                      <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">Any</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {k.ip_allowlist.map(ip => (
                          <span key={ip} className="rounded bg-blue-900/40 px-1.5 py-0.5 font-mono text-[10px] text-blue-300">
                            {ip}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  {/* Created By */}
                  <td className="px-3 py-2.5 font-mono text-[11px] text-slate-500">
                    {k.created_by
                      ? <span title={k.created_by}>{k.created_by.slice(0, 8)}…</span>
                      : <span className="text-slate-700">—</span>
                    }
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-400">
                    <div>Today: {k.requests_today.toLocaleString()}</div>
                    <div className="text-slate-600">Month: {k.requests_this_month.toLocaleString()}</div>
                    <div className="text-slate-700">Total: {k.requests_total.toLocaleString()}</div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-500">
                    <div>{k.rate_limit_per_min}/min</div>
                    <div>{k.rate_limit_per_day.toLocaleString()}/day</div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-slate-500">{formatRelative(k.last_used_at)}</td>
                  <td className="px-3 py-2.5 font-mono text-slate-500">{formatDate(k.expires_at)}</td>
                  <td className="px-3 py-2.5">
                    <button
                      disabled={toggling[k.id]}
                      onClick={() => handleToggle(k.id, k.is_active)}
                      className={cn(
                        'rounded px-2 py-1 text-[10px] font-semibold transition-colors disabled:opacity-40',
                        k.is_active
                          ? 'bg-red-900/40 text-red-300 hover:bg-red-800/40'
                          : 'bg-emerald-900/40 text-emerald-300 hover:bg-emerald-800/40',
                      )}
                    >
                      {toggling[k.id] ? '…' : k.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                    {msg[k.id] && (
                      <p className="mt-1 font-mono text-[10px] text-emerald-400">{msg[k.id]}</p>
                    )}
                  </td>
                </tr>
              ))}
              {keys.length === 0 && (
                <tr>
                  <td colSpan={10} className="py-10 text-center font-mono text-xs text-slate-600">
                    No API keys found
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
