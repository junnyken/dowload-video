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
        'ml-1 rounded px-1 py-0.5 font-mono text-[10px] transition-colors',
        copied
          ? 'bg-success-soft text-success'
          : 'bg-surface text-fg-muted hover:bg-surface-2 hover:text-fg-2',
      )}
    >
      {copied ? 'Copied!' : 'Copy'}
    </button>
  )
}

const SCOPE_COLORS: Record<string, string> = {
  read: 'bg-surface-2 text-fg-2',
  write: 'bg-success-soft text-success',
  batch: 'bg-surface-2 text-fg-2',
  webhook: 'bg-warning-soft text-warning',
  admin: 'bg-danger-soft text-danger',
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Partner API Keys</h1>
          <p className="mt-0.5 text-xs text-fg-muted">vgp_ prefixed keys for tenant partner access</p>
        </div>
        <button onClick={fetchData} className="px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2">
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
            <div key={c.label} className="rounded-card border border-line bg-surface shadow-card p-4">
              <p className="font-mono text-xs text-fg-muted">{c.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-fg">{c.value}</p>
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
          className="rounded border border-line bg-surface px-3 py-1.5 text-xs text-fg-2 placeholder:text-fg-muted w-52"
        />
        <label className="flex items-center gap-1.5 text-xs text-fg-muted cursor-pointer">
          <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
          Active only
        </label>
      </div>

      {error && (
        <div className="rounded border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>
      )}

      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-fg-muted">Loading API keys…</div>
      ) : (
        <div className="overflow-x-auto rounded-control border border-line">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-line bg-surface-2">
                {['Key', 'Tenant', 'Scopes', 'IP Allowlist', 'Created By', 'Requests', 'Rate Limits', 'Last Used', 'Expires', 'Status'].map(h => (
                  <th key={h} className="px-3 py-2.5 font-mono font-semibold text-fg-muted">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id} className="border-b border-line hover:bg-surface-2">
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-0.5">
                      <p className="font-mono font-medium text-fg-2">{k.key_prefix}…</p>
                      <CopyButton text={k.key_prefix} />
                    </div>
                    <p className="text-[10px] text-fg-muted">{k.label}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <p className="text-fg-2">{k.tenants?.name ?? '—'}</p>
                    <span className={cn('inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase mt-0.5',
                      k.tenants?.plan === 'enterprise' ? 'bg-accent-hover text-fg-2'
                      : k.tenants?.plan === 'growth' ? 'bg-accent-hover text-fg-2'
                      : 'bg-surface-2 text-fg-2')}>
                      {k.tenants?.plan ?? '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {(k.scopes ?? []).map(s => (
                        <span key={s} className={cn('rounded px-1.5 py-0.5 text-[10px] font-semibold', SCOPE_COLORS[s] ?? 'bg-surface-2 text-fg-2')}>
                          {s}
                        </span>
                      ))}
                    </div>
                  </td>
                  {/* IP Allowlist */}
                  <td className="px-3 py-2.5">
                    {(!k.ip_allowlist || k.ip_allowlist.length === 0) ? (
                      <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">Any</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {k.ip_allowlist.map(ip => (
                          <span key={ip} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-fg-2">
                            {ip}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  {/* Created By */}
                  <td className="px-3 py-2.5 font-mono text-[11px] text-fg-muted">
                    {k.created_by
                      ? <span title={k.created_by}>{k.created_by.slice(0, 8)}…</span>
                      : <span className="text-fg-muted">—</span>
                    }
                  </td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">
                    <div>Today: {k.requests_today.toLocaleString()}</div>
                    <div className="text-fg-muted">Month: {k.requests_this_month.toLocaleString()}</div>
                    <div className="text-fg-muted">Total: {k.requests_total.toLocaleString()}</div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">
                    <div>{k.rate_limit_per_min}/min</div>
                    <div>{k.rate_limit_per_day.toLocaleString()}/day</div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">{formatRelative(k.last_used_at)}</td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">{formatDate(k.expires_at)}</td>
                  <td className="px-3 py-2.5">
                    <button
                      disabled={toggling[k.id]}
                      onClick={() => handleToggle(k.id, k.is_active)}
                      className={cn(
                        'rounded px-2 py-1 text-[10px] font-semibold transition-colors disabled:opacity-40',
                        k.is_active
                          ? 'bg-danger-soft text-danger hover:bg-danger/20'
                          : 'bg-success-soft text-success hover:bg-success/20',
                      )}
                    >
                      {toggling[k.id] ? '…' : k.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                    {msg[k.id] && (
                      <p className="mt-1 font-mono text-[10px] text-success">{msg[k.id]}</p>
                    )}
                  </td>
                </tr>
              ))}
              {keys.length === 0 && (
                <tr>
                  <td colSpan={10} className="py-10 text-center font-mono text-xs text-fg-muted">
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
