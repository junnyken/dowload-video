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
  if (total === 0 && PROXY_ESSENTIAL.has(platform)) return 'bg-red-900/10 hover:bg-red-900/20'
  if (total === 0) return 'hover:bg-slate-800/30'
  if (total <= 2)  return 'bg-amber-900/10 hover:bg-amber-900/20'
  return 'hover:bg-slate-800/30'
}

function totalColorClass(platform: string, total: number): string {
  if (total === 0 && PROXY_ESSENTIAL.has(platform)) return 'text-red-400'
  if (total === 0) return 'text-slate-500'
  if (total <= 2)  return 'text-amber-400'
  return 'text-emerald-400'
}

function StatusDot({ platform, total }: { platform: string; total: number }) {
  const cls =
    total > 2 ? 'bg-emerald-500'
    : total > 0 ? 'bg-amber-400'
    : PROXY_ESSENTIAL.has(platform) ? 'bg-red-500'
    : 'bg-slate-600'
  return <span className={cn('inline-block h-2 w-2 rounded-full flex-shrink-0', cls)} />
}

function SummaryCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-4">
      <p className="font-mono text-[10px] uppercase tracking-widest text-slate-500">{label}</p>
      <p className="mt-1 font-mono text-2xl font-bold text-slate-100">{value}</p>
      {sub && <p className="mt-0.5 text-[10px] text-slate-600">{sub}</p>}
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
      <div className="px-6 py-3 text-[11px] text-slate-500 animate-pulse">
        Loading proxies…
      </div>
    )
  }

  if (error) {
    return (
      <div className="px-6 py-3 text-[11px] text-red-400">{error}</div>
    )
  }

  if (proxies.length === 0) {
    return (
      <div className="px-6 py-3 text-[11px] text-slate-600 italic">
        No Redis proxies for this platform (env-fallback only, read-only).
      </div>
    )
  }

  return (
    <div className="px-6 py-3 space-y-1.5">
      {proxies.map(px => (
        <div key={px.index} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="font-mono text-[10px] text-slate-600 flex-shrink-0">#{px.index}</span>
            <span className="font-mono text-[11px] text-slate-400 truncate">{px.masked_url}</span>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {removeMsg[px.index] && (
              <span className={cn(
                'font-mono text-[10px]',
                removeMsg[px.index].startsWith('Error') ? 'text-red-400' : 'text-emerald-400'
              )}>
                {removeMsg[px.index]}
              </span>
            )}
            <button
              onClick={() => handleRemove(px.index)}
              disabled={removing[px.index]}
              className="rounded bg-red-900/40 px-2 py-0.5 text-[10px] font-semibold text-red-300 hover:bg-red-800/50 disabled:opacity-40 transition-colors"
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
        <div className="text-sm text-slate-500 animate-pulse">Loading proxy pools…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3">
        <p className="text-sm text-red-400">{error}</p>
        <button onClick={fetchPools} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-800">Retry</button>
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
    healthStatus === 'Healthy'  ? 'text-emerald-400' :
    healthStatus === 'Degraded' ? 'text-red-400'     : 'text-amber-400'

  const formattedTime = lastRefreshed.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm font-semibold text-slate-100">Proxy Health</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Auto-refresh every 30s — last updated {formattedTime}
          </p>
        </div>
        <button onClick={fetchPools} className="text-[10px] text-slate-500 hover:text-slate-300 transition-colors">
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
      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/60">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-800">
              <th className="py-2.5 pl-4 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">Platform</th>
              <th className="py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-slate-500">Redis</th>
              <th className="py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-slate-500">ENV</th>
              <th className="py-2.5 text-right font-mono text-[10px] uppercase tracking-widest text-slate-500">Total</th>
              <th className="py-2.5 pr-4 text-right font-mono text-[10px] uppercase tracking-widest text-slate-500">Details</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60">
            {platforms.map(p => (
              <>
                <tr
                  key={p}
                  className={cn('transition-colors', rowColorClass(p, pools[p].total))}
                >
                  <td className="py-3 pl-4">
                    <div className="flex items-center gap-2.5">
                      <StatusDot platform={p} total={pools[p].total} />
                      <span className="font-mono text-[11px] text-slate-300">{PLATFORM_ICONS[p] ?? '◌'}</span>
                      <span className="text-sm font-medium capitalize text-slate-200">{p}</span>
                    </div>
                  </td>
                  <td className="py-3 text-right font-mono text-sm text-slate-400">{pools[p].redis_pool}</td>
                  <td className="py-3 text-right font-mono text-sm text-slate-400">{pools[p].env_fallback}</td>
                  <td className="py-3 text-right">
                    <span className={cn('font-mono text-sm font-semibold', totalColorClass(p, pools[p].total))}>
                      {pools[p].total}
                    </span>
                  </td>
                  <td className="py-3 pr-4 text-right">
                    <button
                      onClick={() => toggleExpand(p)}
                      className="rounded border border-slate-700 px-2 py-1 font-mono text-[10px] text-slate-400 hover:border-slate-500 hover:text-slate-200 transition-colors"
                    >
                      {expanded[p] ? '▾ Hide' : '▸ Details'}
                    </button>
                  </td>
                </tr>
                {expanded[p] && (
                  <tr key={`${p}-detail`} className="border-b border-slate-800/40 bg-slate-950/40">
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
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
        <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">Add Proxy to Redis Pool</h3>
        <form onSubmit={handleAdd} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex flex-col gap-1.5">
            <label className="font-mono text-[9px] uppercase tracking-widest text-slate-600">Platform</label>
            <select
              value={addPlatform}
              onChange={e => setAddPlatform(e.target.value)}
              className="rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 outline-none focus:border-slate-500"
            >
              <option value="">Select…</option>
              {['youtube','tiktok','facebook','instagram','douyin','twitter','reddit','default'].map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-1 flex-col gap-1.5">
            <label className="font-mono text-[9px] uppercase tracking-widest text-slate-600">Proxy URL</label>
            <input
              type="text"
              placeholder="http://user:pass@host:port"
              value={addUrl}
              onChange={e => setAddUrl(e.target.value)}
              className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-slate-500"
            />
          </div>
          <button
            type="submit"
            disabled={adding}
            className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50 transition-colors"
          >
            {adding ? 'Adding…' : 'Add'}
          </button>
        </form>
        {addError && <p className="mt-2 text-xs text-red-400">{addError}</p>}
      </div>

      {/* ENV var reference */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
        <h3 className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">ENV Var Reference</h3>
        <p className="mb-3 text-[11px] text-slate-600">
          ENV-fallback proxies are read-only. Set these on Coolify to provision env proxies.
        </p>
        <div className="overflow-hidden rounded-xl border border-slate-800">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900">
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">Platform</th>
                <th className="px-3 py-2 text-left font-mono text-[10px] uppercase tracking-widest text-slate-500">ENV Variable</th>
                <th className="px-3 py-2 text-right font-mono text-[10px] uppercase tracking-widest text-slate-500">Current ENV Count</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/50">
              {Object.entries(ENV_VAR_MAP).map(([plat, envVar]) => (
                <tr key={plat} className="hover:bg-slate-800/20">
                  <td className="px-3 py-2 font-medium capitalize text-slate-300">{plat}</td>
                  <td className="px-3 py-2">
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[11px] text-amber-300">{envVar}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-slate-500">
                    {pools[plat]?.env_fallback ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-center text-[10px] text-slate-700">
        Redis proxies survive restarts — ENV-fallback proxies are read-only from PROXY_POOL_* env vars.
      </p>
    </div>
  )
}
