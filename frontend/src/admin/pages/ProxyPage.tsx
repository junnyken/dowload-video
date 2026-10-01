import { useState, useEffect, useCallback, useRef } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface ProxyPoolItem {
  redis_pool: number
  env_fallback: number
  total: number
}

interface ProxyStatusResponse {
  success: boolean
  pools: Record<string, ProxyPoolItem>
}

interface MaskedProxy {
  index: number
  masked_url: string
}

interface ProxyListResponse {
  success: boolean
  platform: string
  proxies: MaskedProxy[]
  count: number
}

interface ProxyAddPayload {
  platform: string
  proxy_url: string
}

const PLATFORM_ICONS: Record<string, string> = {
  youtube: '▶', tiktok: '◆', facebook: '◉', instagram: '◈',
  douyin: '◉', twitter: '✦', reddit: '◎', default: '◌',
}

const ENV_VAR_MAP: Record<string, string> = {
  youtube:   'PROXY_POOL_YT',
  tiktok:    'PROXY_POOL_TT',
  facebook:  'PROXY_POOL_FB',
  instagram: 'PROXY_POOL_IG',
  douyin:    'PROXY_POOL_CN',
  twitter:   'PROXY_POOL_TW',
  reddit:    'PROXY_POOL_REDDIT',
  default:   'PROXY_POOL_DEFAULT',
}

// Platforms where 0 proxies is genuinely a problem (bot-block without it).
// Optional platforms (TikTok, FB, IG, etc.) download fine without a dedicated proxy.
const PROXY_ESSENTIAL = new Set(['youtube'])

function rowColorClass(platform: string, total: number): string {
  if (total === 0 && PROXY_ESSENTIAL.has(platform)) return 'bg-danger-soft hover:bg-danger/20'
  if (total === 0) return 'hover:bg-surface-2'
  if (total <= 2)  return 'bg-accent-soft hover:bg-accent/20'
  return 'hover:bg-surface-2'
}

function totalColorClass(platform: string, total: number): string {
  if (total === 0 && PROXY_ESSENTIAL.has(platform)) return 'text-danger'
  if (total === 0) return 'text-fg-muted'
  if (total <= 2)  return 'text-accent-text'
  return 'text-success'
}

function StatusDot({ platform, total }: { platform: string; total: number }) {
  const cls =
    total > 2 ? 'bg-success'
    : total > 0 ? 'bg-accent'
    : PROXY_ESSENTIAL.has(platform) ? 'bg-danger'
    : 'bg-line'
  return <span className={cn('inline-block h-2 w-2 rounded-full flex-shrink-0', cls)} />
}

function SummaryCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface-2 p-4">
      <p className="font-mono text-[10px] uppercase tracking-widest text-fg-muted">{label}</p>
      <p className="mt-1 font-mono text-2xl font-bold text-fg">{value}</p>
      {sub && <p className="mt-0.5 text-[10px] text-fg-muted">{sub}</p>}
    </div>
  )
}

function ExpandedProxies({
  platform,
  onRemoved,
}: {
  platform: string
  onRemoved: () => void
}) {
  const [proxies, setProxies] = useState<MaskedProxy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [removing, setRemoving] = useState<Record<number, boolean>>({})
  const [removeMsg, setRemoveMsg] = useState<Record<number, string>>({})

  const fetchProxies = useCallback(async () => {
    setLoading(true)
    try {
      const data = await adminFetch<ProxyListResponse>(`/proxies/list/${platform}`)
      setProxies(data.proxies ?? [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load proxies')
    } finally {
      setLoading(false)
    }
  }, [platform])

  useEffect(() => { fetchProxies() }, [fetchProxies])

  async function handleRemove(index: number) {
    setRemoving(r => ({ ...r, [index]: true }))
    try {
      await adminFetch('/proxies/remove', {
        method: 'DELETE',
        body: JSON.stringify({ platform, index }),
      })
      setRemoveMsg(m => ({ ...m, [index]: 'Removed' }))
      setTimeout(() => {
        fetchProxies()
        onRemoved()
      }, 600)
    } catch (e) {
      setRemoveMsg(m => ({ ...m, [index]: `Error: ${e instanceof Error ? e.message : 'Failed'}` }))
    } finally {
      setRemoving(r => ({ ...r, [index]: false }))
    }
  }

  if (loading) {
    return (
      <div className="px-6 py-3 text-[11px] text-fg-muted animate-pulse">
        Loading proxies…
      </div>
    )
  }

  if (error) {
    return (
      <div className="px-6 py-3 text-[11px] text-danger">{error}</div>
    )
  }

  if (proxies.length === 0) {
    return (
      <div className="px-6 py-3 text-[11px] text-fg-muted italic">
        No Redis proxies for this platform (env-fallback only, read-only).
      </div>
    )
  }

  return (
    <div className="px-6 py-3 space-y-1.5">
      {proxies.map(px => (
        <div key={px.index} className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface-2 px-3 py-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="font-mono text-[10px] text-fg-muted flex-shrink-0">#{px.index}</span>
            <span className="font-mono text-[11px] text-fg-muted truncate">{px.masked_url}</span>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {removeMsg[px.index] && (
              <span className={cn(
                'font-mono text-[10px]',
                removeMsg[px.index].startsWith('Error') ? 'text-danger' : 'text-success'
              )}>
                {removeMsg[px.index]}
              </span>
            )}
            <button
              onClick={() => handleRemove(px.index)}
              disabled={removing[px.index]}
              className="rounded bg-danger-soft px-2 py-0.5 text-[10px] font-semibold text-danger hover:bg-danger/20 disabled:opacity-40 transition-colors"
            >
              {removing[px.index] ? '…' : 'Remove'}
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

export function ProxyPage() {
  const [pools, setPools] = useState<Record<string, ProxyPoolItem>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addPlatform, setAddPlatform] = useState('')
  const [addUrl, setAddUrl] = useState('')
  const [addError, setAddError] = useState('')
  const [adding, setAdding] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [lastRefreshed, setLastRefreshed] = useState<Date>(new Date())
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchPools = useCallback(async () => {
    try {
      const data = await adminFetch<ProxyStatusResponse>('/proxies/status')
      setPools(data.pools ?? {})
      setLastRefreshed(new Date())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load proxy pools')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchPools()
    intervalRef.current = setInterval(fetchPools, 30_000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [fetchPools])

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    setAddError('')
    if (!addPlatform || !addUrl) { setAddError('Platform and proxy URL are required.'); return }
    if (!addUrl.match(/^(https?|socks5):\/\//)) { setAddError('URL must start with http://, https://, or socks5://'); return }
    setAdding(true)
    try {
      await adminPost<unknown>('/proxies/add', { platform: addPlatform, proxy_url: addUrl } as ProxyAddPayload)
      setAddUrl('')
      await fetchPools()
    } catch (e) {
      setAddError(e instanceof Error ? e.message : 'Failed to add proxy')
    } finally {
      setAdding(false)
    }
  }

  function toggleExpand(platform: string) {
    setExpanded(prev => ({ ...prev, [platform]: !prev[platform] }))
  }

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-sm text-fg-muted animate-pulse">Loading proxy pools…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <p className="text-sm text-danger">{error}</p>
        <button onClick={fetchPools} className="rounded-lg border border-line px-3 py-1.5 text-xs text-fg-2 hover:bg-surface">Retry</button>
      </div>
    )
  }

  const platforms = Object.keys(pools)
  const totalRedis = platforms.reduce((s, p) => s + pools[p].redis_pool, 0)
  const totalEnv   = platforms.reduce((s, p) => s + pools[p].env_fallback, 0)
  const totalAll   = platforms.reduce((s, p) => s + pools[p].total, 0)

  // Health is only "Degraded" when an essential platform (YouTube) has 0 proxies.
  // Optional platforms with 0 proxies are normal — they download direct.
  const essentialMissing = platforms.some(p => PROXY_ESSENTIAL.has(p) && pools[p].total === 0)
  const anyMissing       = platforms.some(p => pools[p].total === 0)
  const healthStatus = essentialMissing ? 'Degraded' : anyMissing ? 'Partial' : 'Healthy'
  const healthColor =
    healthStatus === 'Healthy'  ? 'text-success' :
    healthStatus === 'Degraded' ? 'text-danger'     : 'text-warning'

  const formattedTime = lastRefreshed.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm font-semibold text-fg">Proxy Health</h2>
          <p className="mt-0.5 text-xs text-fg-muted">
            Auto-refresh every 30s — last updated {formattedTime}
          </p>
        </div>
        <button onClick={fetchPools} className="text-[10px] text-fg-muted hover:text-fg-2 transition-colors">
          ↺ Refresh
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryCard label="Platforms" value={platforms.length} />
        <SummaryCard label="Total (Redis)" value={totalRedis} sub="editable via API" />
        <SummaryCard label="Total (ENV)" value={totalEnv} sub="read-only fallback" />
        <SummaryCard
          label="Health"
          value={<span className={healthColor}>{healthStatus}</span> as unknown as string}
          sub={`${totalAll} proxies total`}
        />
      </div>

      {/* Pool table */}
      <div className="overflow-hidden rounded-2xl border border-line bg-surface-2">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line">
              <th className="py-2.5 pl-4 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted">Platform</th>
              <th className="py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-fg-muted">Redis</th>
              <th className="py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-fg-muted">ENV</th>
              <th className="py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-fg-muted">Total</th>
              <th className="py-2.5 pr-4 text-right font-mono text-[10px] uppercase tracking-widest text-fg-muted">Details</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {platforms.map(p => (
              <>
                <tr
                  key={p}
                  className={cn('transition-colors', rowColorClass(p, pools[p].total))}
                >
                  <td className="py-3 pl-4">
                    <div className="flex items-center gap-2.5">
                      <StatusDot platform={p} total={pools[p].total} />
                      <span className="font-mono text-[11px] text-fg-2">{PLATFORM_ICONS[p] ?? '◌'}</span>
                      <span className="text-sm font-medium capitalize text-fg-2">{p}</span>
                    </div>
                  </td>
                  <td className="py-3 text-right font-mono text-sm text-fg-muted">{pools[p].redis_pool}</td>
                  <td className="py-3 text-right font-mono text-sm text-fg-muted">{pools[p].env_fallback}</td>
                  <td className="py-3 text-right">
                    <span className={cn('font-mono text-sm font-semibold', totalColorClass(p, pools[p].total))}>
                      {pools[p].total}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-right">
                    <button
                      onClick={() => toggleExpand(p)}
                      className="rounded border border-line px-2 py-1 font-mono text-[10px] text-fg-muted hover:border-line-strong hover:text-fg-2 transition-colors"
                    >
                      {expanded[p] ? '▾ Hide' : '▸ Details'}
                    </button>
                  </td>
                </tr>
                {expanded[p] && (
                  <tr key={`${p}-detail`} className="border-b border-line bg-surface-2">
                    <td colSpan={5} className="py-1">
                      <ExpandedProxies platform={p} onRemoved={fetchPools} />
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {/* Add proxy form */}
      <div className="rounded-2xl border border-line bg-surface-2 p-5">
        <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted">Add Proxy to Redis Pool</h3>
        <form onSubmit={handleAdd} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex flex-col gap-1.5">
            <label className="font-mono text-[9px] uppercase tracking-widest text-fg-muted">Platform</label>
            <select
              value={addPlatform}
              onChange={e => setAddPlatform(e.target.value)}
              className="rounded-xl border border-line bg-surface px-3 py-2 text-sm text-fg-2 outline-none focus:border-line-strong"
            >
              <option value="">Select…</option>
              {['youtube','tiktok','facebook','instagram','douyin','twitter','reddit','default'].map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-1 flex-col gap-1.5">
            <label className="font-mono text-[9px] uppercase tracking-widest text-fg-muted">Proxy URL</label>
            <input
              type="text"
              placeholder="http://user:pass@host:port"
              value={addUrl}
              onChange={e => setAddUrl(e.target.value)}
              className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-sm text-fg-2 placeholder:text-fg-muted outline-none focus:border-line-strong"
            />
          </div>
          <button
            type="submit"
            disabled={adding}
            className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50 transition-colors"
          >
            {adding ? 'Adding…' : 'Add'}
          </button>
        </form>
        {addError && <p className="mt-2 text-xs text-danger">{addError}</p>}
      </div>

      {/* ENV var reference */}
      <div className="rounded-2xl border border-line bg-surface-2 p-5">
        <h3 className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted">ENV Var Reference</h3>
        <p className="mb-3 text-[11px] text-fg-muted">
          ENV-fallback proxies are read-only. Set these on Coolify to provision env proxies.
        </p>
        <div className="overflow-hidden rounded-xl border border-line">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line bg-canvas">
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted">Platform</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted">ENV Variable</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-widest text-fg-muted">Current ENV Count</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {Object.entries(ENV_VAR_MAP).map(([plat, envVar]) => (
                <tr key={plat} className="hover:bg-surface-2">
                  <td className="px-3 py-2 font-medium capitalize text-fg-2">{plat}</td>
                  <td className="px-3 py-2">
                    <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[11px] text-accent-text">{envVar}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-fg-muted">
                    {pools[plat]?.env_fallback ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-center text-[10px] text-fg-muted">
        Redis proxies survive restarts — ENV-fallback proxies are read-only from PROXY_POOL_* env vars.
      </p>
    </div>
  )
}
