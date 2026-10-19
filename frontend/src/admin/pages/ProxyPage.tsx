import { useState, useEffect, useCallback, useRef } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'
import { ScraperApiPanel } from '../panels/ScraperApiPanel'
import { CheckCircle2, XCircle, HelpCircle, RefreshCw, Loader2 } from 'lucide-react'

interface ProxyPoolItem {
  redis_pool: number
  env_fallback: number
  total: number
}

interface ProxyStatusResponse {
  success: boolean
  pools: Record<string, ProxyPoolItem>
}

interface ProxyHealth {
  alive: boolean
  kind: string
  label: string
  ms: number | null
  ip: string | null
  country: string | null
  youtube_ok: boolean | null
  detail: string | null
  at: number
}

interface MaskedProxy {
  index: number
  masked_url: string
  priority?: 'main' | 'backup'
  health?: ProxyHealth | null
  cooling_s?: number
}

interface HealthSummary {
  at: number | null
  total: number
  alive: number
  results?: unknown[]
  skipped?: string
}

function relativeTime(epochSec: number): string {
  const diff = Math.max(0, Math.floor(Date.now() / 1000 - epochSec))
  if (diff < 60) return 'vừa xong' // wording: BA review
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước` // wording: BA review
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước` // wording: BA review
  return `${Math.floor(diff / 86400)} ngày trước` // wording: BA review
}

function HealthBadge({ h }: { h: ProxyHealth | null | undefined }) {
  const base = 'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold'
  if (!h) {
    return (
      <span className={cn(base, 'bg-surface-2 text-fg-muted border border-line')}>
        <HelpCircle className="h-3 w-3" aria-hidden="true" />
        {/* wording: BA review */}
        Chưa kiểm
      </span>
    )
  }
  if (h.alive) {
    const parts = ['Sống'] // wording: BA review
    if (h.ms != null) parts.push(`${h.ms} ms`)
    if (h.country) parts.push(h.country)
    return (
      <span className={cn(base, 'bg-success-soft text-success')}>
        <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
        {parts.join(' · ')}
      </span>
    )
  }
  return (
    <span className={cn(base, 'bg-danger-soft text-danger')}>
      <XCircle className="h-3 w-3" aria-hidden="true" />
      {h.label}
    </span>
  )
}

function HealthCard({ refreshKey, onRan }: { refreshKey: number; onRan: () => void }) {
  const [summary, setSummary] = useState<HealthSummary | null>(null)
  const [running, setRunning] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await adminFetch<HealthSummary>('/proxies/health')
      setSummary(data)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Không tải được tình trạng proxy') // wording: BA review
    }
  }, [])

  useEffect(() => { load() }, [load, refreshKey])

  async function run() {
    setRunning(true)
    setErr(null)
    setNote(null)
    try {
      const data = await adminPost<HealthSummary>('/proxies/health/run', {})
      if (data.skipped) setNote(data.skipped)
      else setSummary(data)
      onRan()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Kiểm tra thất bại') // wording: BA review
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="rounded-card border border-line bg-surface shadow-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          {/* wording: BA review */}
          <h3 className="font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted">Tình trạng proxy</h3>
          <p className="mt-1 text-sm text-fg" aria-live="polite">
            {/* wording: BA review */}
            {summary == null
              ? 'Đang tải…'
              : summary.at == null
                ? 'Chưa kiểm lần nào'
                : `${summary.alive}/${summary.total} proxy đang sống · kiểm lúc ${relativeTime(summary.at)}`}
          </p>
        </div>
        <button
          onClick={run}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-control border border-line bg-surface px-3 py-1.5 text-xs font-medium text-fg hover:border-line-strong hover:bg-surface-2 disabled:opacity-50 transition-colors"
        >
          {running
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            : <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />}
          {/* wording: BA review */}
          {running ? 'Đang kiểm tra…' : 'Kiểm tra ngay'}
        </button>
      </div>
      {err && <p className="mt-2 text-xs text-danger" role="alert">{err}</p>}
      {note && <p className="mt-2 text-xs text-warning">{note}</p>}
      {/* wording: BA review */}
      <p className="mt-2 text-[11px] text-fg-muted">
        Hệ thống tự kiểm mỗi 10 phút. Proxy chết bị tạm bỏ qua để proxy dự phòng phục vụ; sống lại thì tự dùng lại.
        Có báo Telegram khi đổi trạng thái.
      </p>
    </div>
  )
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
  douyin: '◉', twitter: '✦', reddit: '◎', default: '◌', tiktok_watch: '◇', all: '✱',
}

// Pools that are not a download platform: shown with a plain label.
const POOL_LABELS: Record<string, string> = {
  tiktok_watch: 'Theo dõi kênh TikTok', // wording: BA review
  all: 'Tất cả nền tảng (dự phòng khi bị chặn)', // wording: BA review
}

const ENV_VAR_MAP: Record<string, string> = {
  youtube:   'PROXY_POOL_YT',
  tiktok:    'PROXY_POOL_TT',
  facebook:  'PROXY_POOL_FB',
  instagram: 'PROXY_POOL_IG',
  douyin:    'PROXY_POOL_CN',
  twitter:   'PROXY_POOL_TW',
  reddit:    'PROXY_POOL_REDDIT',
  tiktok_watch: 'PROXY_POOL_TT_WATCH',
  all:       'PROXY_POOL_ALL',
  default:   'PROXY_POOL_DEFAULT',
}

// Platforms where 0 proxies is genuinely a problem (bot-block without it).
// Optional platforms (TikTok, FB, IG, etc.) download fine without a dedicated proxy.
const PROXY_ESSENTIAL = new Set(['youtube'])

function rowColorClass(platform: string, total: number): string {
  if (total === 0 && PROXY_ESSENTIAL.has(platform)) return 'bg-danger-soft hover:bg-danger/20'
  if (total === 0) return 'hover:bg-surface-2'
  if (total <= 2)  return 'bg-warning-soft hover:bg-warning-soft'
  return 'hover:bg-surface-2'
}

function totalColorClass(platform: string, total: number): string {
  if (total === 0 && PROXY_ESSENTIAL.has(platform)) return 'text-danger'
  if (total === 0) return 'text-fg-muted'
  if (total <= 2)  return 'text-warning'
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
    <div className="rounded-card border border-line bg-surface shadow-card p-4">
      <p className="font-mono text-[10px] uppercase tracking-widest text-fg-muted">{label}</p>
      <p className="mt-1 font-mono text-2xl font-bold text-fg">{value}</p>
      {sub && <p className="mt-0.5 text-[10px] text-fg-muted">{sub}</p>}
    </div>
  )
}

function ExpandedProxies({
  platform,
  onRemoved,
  refreshKey = 0,
}: {
  platform: string
  onRemoved: () => void
  refreshKey?: number
}) {
  const [proxies, setProxies] = useState<MaskedProxy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [removing, setRemoving] = useState<Record<number, boolean>>({})
  const [removeMsg, setRemoveMsg] = useState<Record<number, string>>({})

  const fetchProxies = useCallback(async () => {
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

  useEffect(() => { fetchProxies() }, [fetchProxies, refreshKey])

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
        <div key={px.index} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-control border border-line bg-surface px-3 py-2">
          <div className="flex min-w-0 flex-1 basis-full flex-col gap-1.5 sm:basis-0">
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="font-mono text-[10px] text-fg-muted flex-shrink-0">#{px.index}</span>
              <span className="font-mono text-[11px] text-fg-muted truncate">{px.masked_url}</span>
            </div>
            <div
              className="flex flex-wrap items-center gap-1.5"
              title={[px.health?.detail, px.health?.ip ? `IP: ${px.health.ip}` : null].filter(Boolean).join(' — ') || undefined}
            >
              {/* wording: BA review */}
              <span className={cn(
                'rounded border px-1.5 py-0.5 text-[10px] font-semibold',
                px.priority === 'main' ? 'border-accent text-accent' : 'border-line text-fg-muted'
              )}>
                {px.priority === 'main' ? 'Chính' : 'Dự phòng'}
              </span>
              <HealthBadge h={px.health} />
              {(px.cooling_s ?? 0) > 0 && (
                <span className="text-[10px] text-fg-muted">
                  {/* wording: BA review */}
                  đang tạm bỏ qua ~{Math.max(1, Math.ceil((px.cooling_s ?? 0) / 60))} phút
                </span>
              )}
            </div>
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
  const [healthKey, setHealthKey] = useState(0)
  const [liveSummary, setLiveSummary] = useState<HealthSummary | null>(null)
  useEffect(() => {
    adminFetch<HealthSummary>('/proxies/health').then(setLiveSummary).catch(() => setLiveSummary(null))
  }, [healthKey])
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
        <button onClick={fetchPools} className="px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2">Retry</button>
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
  // A dead proxy (last live check) outranks the count-based states: the card
  // read "Healthy" while the main proxy was out of traffic.
  const deadCount = liveSummary && liveSummary.at != null ? liveSummary.total - liveSummary.alive : 0
  // wording: BA review
  const healthStatus = deadCount > 0 ? `${deadCount} proxy chết`
    : essentialMissing ? 'Degraded' : anyMissing ? 'Partial' : 'Healthy'
  const healthColor =
    healthStatus === 'Healthy'  ? 'text-success' :
    healthStatus === 'Degraded' || deadCount > 0 ? 'text-danger'     : 'text-warning'

  const formattedTime = lastRefreshed.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Proxy Health</h1>
          <p className="mt-0.5 text-xs text-fg-muted">
            Auto-refresh every 30s — last updated {formattedTime}
          </p>
        </div>
        <button onClick={fetchPools} className="px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2">
          ↺ Refresh
        </button>
      </div>

      <HealthCard refreshKey={healthKey} onRan={() => setHealthKey(k => k + 1)} />

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
      <div className="overflow-x-auto rounded-card border border-line bg-surface shadow-card">
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
                      <span className="text-sm font-medium capitalize text-fg-2">{POOL_LABELS[p] ?? p}</span>
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
                      <ExpandedProxies platform={p} onRemoved={fetchPools} refreshKey={healthKey} />
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {/* Add proxy form */}
      <div className="rounded-card border border-line bg-surface shadow-card p-5">
        <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted">Add Proxy to Redis Pool</h3>
        <form onSubmit={handleAdd} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex flex-col gap-1.5">
            <label className="font-mono text-[10px] uppercase tracking-widest text-fg-muted">Platform</label>
            <select
              value={addPlatform}
              onChange={e => setAddPlatform(e.target.value)}
              className="rounded-card border border-line bg-surface shadow-card px-3 py-2 text-sm text-fg-2 outline-none focus:border-line-strong"
            >
              <option value="">Select…</option>
              {['all','youtube','tiktok','facebook','instagram','douyin','twitter','reddit','tiktok_watch','default'].map(p => (
                <option key={p} value={p}>{POOL_LABELS[p] ?? p}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-1 flex-col gap-1.5">
            <label className="font-mono text-[10px] uppercase tracking-widest text-fg-muted">Proxy URL</label>
            <input
              type="text"
              placeholder="http://user:pass@host:port"
              value={addUrl}
              onChange={e => setAddUrl(e.target.value)}
              className="w-full rounded-card border border-line bg-surface shadow-card px-3 py-2 text-sm text-fg-2 placeholder:text-fg-muted outline-none focus:border-line-strong"
            />
          </div>
          <button
            type="submit"
            disabled={adding}
            className="rounded-card bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50 transition-colors"
          >
            {adding ? 'Adding…' : 'Add'}
          </button>
        </form>
        {addError && <p className="mt-2 text-xs text-danger">{addError}</p>}
        {/* wording: BA review */}
        <p className="mt-3 text-[11px] text-fg-muted">
          Thứ tự là thứ tự ưu tiên: proxy thêm trước là proxy chính, proxy thêm sau là dự phòng — chỉ dùng khi proxy
          trước lỗi hoặc hết dung lượng (nghỉ 1 giờ rồi thử lại proxy chính; có báo Telegram khi chuyển).
        </p>
      </div>

      <ScraperApiPanel />

      {/* ENV var reference */}
      <div className="rounded-card border border-line bg-surface shadow-card p-5">
        <h3 className="mb-3 font-mono text-[10px] font-semibold uppercase tracking-widest text-fg-muted">ENV Var Reference</h3>
        <p className="mb-3 text-[11px] text-fg-muted">
          ENV-fallback proxies are read-only. Set these on Coolify to provision env proxies.
        </p>
        <div className="overflow-x-auto rounded-card border border-line">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line bg-surface-2">
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
                    <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[11px] text-warning">{envVar}</span>
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
