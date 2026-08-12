import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ActiveAlertsBanner, type AlertItem } from '../panels/ActiveAlertsBanner'
import { useAdminSystemStatus } from '../hooks/useAdminSystemStatus'
import { useAdminActiveJobs } from '../hooks/useAdminActiveJobs'
import type { SystemSnapshot, PlatformStatsTotal, DailyStatEntry } from '../api/system'
import { buildAlerts } from '../utils/alerts'

function fmt(v: number | undefined | null, decimals = 0): string {
  if (v === undefined || v === null) return '—'
  return v.toLocaleString('en-US', { maximumFractionDigits: decimals })
}

// ─── Tone system ──────────────────────────────────────────────────────────────

type Tone = 'green' | 'blue' | 'amber' | 'red' | 'purple' | 'slate' | 'cyan'

const TONE: Record<Tone, { val: string; border: string; dot: string }> = {
  green:  { val: 'text-emerald-400', border: 'border-emerald-800/40', dot: 'bg-emerald-400' },
  blue:   { val: 'text-blue-400',    border: 'border-blue-800/40',    dot: 'bg-blue-400'    },
  amber:  { val: 'text-amber-400',   border: 'border-amber-800/40',   dot: 'bg-amber-400'   },
  red:    { val: 'text-red-400',     border: 'border-red-800/40',     dot: 'bg-red-400'     },
  purple: { val: 'text-purple-400',  border: 'border-purple-800/40',  dot: 'bg-purple-400'  },
  slate:  { val: 'text-slate-300',   border: 'border-slate-800',      dot: 'bg-slate-500'   },
  cyan:   { val: 'text-cyan-400',    border: 'border-cyan-800/40',    dot: 'bg-cyan-400'    },
}

// ─── Metric card ──────────────────────────────────────────────────────────────

function MetricCard({
  label, value, sub, tone = 'slate', link, pulse,
}: {
  label: string; value: string; sub?: string
  tone?: Tone; link?: string; pulse?: boolean
}) {
  const t = TONE[tone]
  const body = (
    <div className={`rounded-xl border bg-slate-900 px-4 py-3.5 space-y-1 h-full transition-colors hover:border-slate-700 ${t.border}`}>
      <div className="text-[10px] text-slate-500 uppercase tracking-widest font-mono">{label}</div>
      <div className={`text-xl font-bold font-mono flex items-center gap-2 ${t.val}`}>
        {pulse && <span className={`inline-block h-2 w-2 rounded-full animate-pulse ${t.dot}`} />}
        {value}
      </div>
      {sub && <div className="text-[11px] text-slate-500">{sub}</div>}
    </div>
  )
  return link ? <Link to={link} className="block">{body}</Link> : body
}

// ─── Section header ───────────────────────────────────────────────────────────

function SectionTitle({ title, linkTo, linkLabel }: { title: string; linkTo?: string; linkLabel?: string }) {
  return (
    <div className="flex items-center justify-between mb-2.5">
      <h2 className="text-[10px] font-semibold uppercase tracking-widest text-slate-500">{title}</h2>
      {linkTo && (
        <Link to={linkTo} className="text-[11px] text-slate-600 hover:text-slate-300 transition-colors">{linkLabel ?? 'View all →'}</Link>
      )}
    </div>
  )
}

// ─── P2: Mini sparkline ───────────────────────────────────────────────────────

function MiniSparkline({
  data, color = '#60a5fa', label, valueKey,
}: {
  data: DailyStatEntry[]
  color?: string
  label: string
  valueKey: 'total' | 'success' | 'failed'
}) {
  if (!data || data.length < 2) {
    return <div className="h-16 flex items-center justify-center text-[10px] text-slate-600">No data</div>
  }

  const W = 200, H = 56, PL = 0, PR = 0, PT = 4, PB = 16
  const vals = data.map(d => d[valueKey] ?? 0)
  const maxV = Math.max(...vals, 1)
  const iW = W - PL - PR
  const iH = H - PT - PB
  const step = iW / Math.max(data.length - 1, 1)

  function x(i: number) { return PL + i * step }
  function y(v: number) { return PT + iH - (v / maxV) * iH }

  const pts = data.map((d, i) => `${x(i)},${y(d[valueKey] ?? 0)}`).join(' ')
  const fillPts = `${x(0)},${PT + iH} ${pts} ${x(data.length - 1)},${PT + iH}`
  const today = vals[vals.length - 1] ?? 0

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-[10px] text-slate-500">{label}</span>
        <span className="font-mono text-xs font-semibold" style={{ color }}>{fmt(today)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
        <defs>
          <linearGradient id={`sg-${valueKey}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.3" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <polygon points={fillPts} fill={`url(#sg-${valueKey})`} />
        <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
        {/* Last point dot */}
        <circle cx={x(data.length - 1)} cy={y(today)} r={2.5} fill={color} />
        {/* X labels — first, mid, last */}
        {[0, Math.floor(data.length / 2), data.length - 1].map(i => (
          <text key={i} x={x(i)} y={H} textAnchor={i === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle'} fontSize={7} fill="#475569">
            {data[i]?.date?.slice(5) ?? ''}
          </text>
        ))}
      </svg>
    </div>
  )
}

// ─── P3: Platform bar ──────────────────────────────────────────────────────────

const PLAT_ABBR: Record<string, string> = {
  youtube: 'YT', tiktok: 'TK', facebook: 'FB', instagram: 'IG',
  twitter: 'TW', bilibili: 'BB', douyin: 'DY', reddit: 'RD',
  threads: 'TH', vimeo: 'VM', soundcloud: 'SC', spotify: 'SP',
  pinterest: 'PT', rumble: 'RU', xiaohongshu: 'XH', lemon8: 'L8',
  odysee: 'OD', vk: 'VK', dailymotion: 'DM',
}

function PlatformBar({ p, maxTotal }: { p: PlatformStatsTotal; maxTotal: number }) {
  const pct = maxTotal > 0 ? (p.total / maxTotal) * 100 : 0
  const okPct = p.total > 0 ? (p.ok / p.total) * 100 : 100
  const barColor = p.success_rate >= 95 ? 'bg-emerald-500' : p.success_rate >= 80 ? 'bg-amber-500' : 'bg-red-500'
  const rateColor = p.success_rate >= 95 ? 'text-emerald-400' : p.success_rate >= 80 ? 'text-amber-400' : 'text-red-400'
  const abbr = PLAT_ABBR[p.platform.toLowerCase()] ?? p.platform.slice(0, 2).toUpperCase()

  return (
    <div className="flex items-center gap-3">
      <div className="w-7 h-7 shrink-0 rounded-md bg-slate-800 flex items-center justify-center font-mono text-[9px] font-bold text-slate-400">
        {abbr}
      </div>
      <div className="flex-1 space-y-1">
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-slate-300 capitalize">{p.platform}</span>
          <span className="font-mono text-slate-500">{fmt(p.total)} req</span>
        </div>
        <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div className="h-full bg-slate-700 rounded-full relative" style={{ width: `${pct}%` }}>
            <div className={`absolute inset-0 ${barColor} rounded-full`} style={{ width: `${okPct}%` }} />
          </div>
        </div>
      </div>
      <span className={`w-10 shrink-0 text-right font-mono text-[10px] ${rateColor}`}>
        {p.success_rate.toFixed(0)}%
      </span>
    </div>
  )
}

// ─── P3: Proxy pool card ──────────────────────────────────────────────────────

function ProxyPoolCard({ platform, redis, env, total }: { platform: string; redis: number; env: number; total: number }) {
  const tone: Tone = total > 0 ? 'green' : 'red'
  const t = TONE[tone]
  const abbr = PLAT_ABBR[platform.toLowerCase()] ?? platform.slice(0, 2).toUpperCase()
  return (
    <div className={`rounded-lg border bg-slate-900 px-3 py-2.5 ${t.border}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-slate-500 uppercase font-mono">{abbr}</span>
        <span className={`text-xs font-bold font-mono ${t.val}`}>{total}</span>
      </div>
      <div className="text-[9px] text-slate-600 space-y-0.5">
        {redis > 0 && <div>Redis: {redis}</div>}
        {env > 0 && <div>Env: {env}</div>}
        {total === 0 && <div className="text-red-500">No proxies</div>}
      </div>
    </div>
  )
}

// ─── Failure row ──────────────────────────────────────────────────────────────

function FailureRow({ job }: { job: { platform?: string; phase?: string; error?: string; time?: string } }) {
  return (
    <div className="flex items-start gap-3 py-2 border-b border-slate-800/70 last:border-0 text-xs">
      <span className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[9px] uppercase text-slate-400">
        {job.platform ?? '?'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-slate-300 truncate">{job.error ?? 'Unknown error'}</p>
        <p className="text-slate-600 text-[10px]">{job.phase ?? '—'}</p>
      </div>
      <span className="shrink-0 text-slate-600 text-[10px] font-mono">{job.time ?? '—'}</span>
    </div>
  )
}

// ─── Provider credit ──────────────────────────────────────────────────────────

function ProviderPill({ name, credits }: { name: string; credits: number }) {
  const tone: Tone = credits > 5000 ? 'green' : credits > 1000 ? 'amber' : 'red'
  const t = TONE[tone]
  return (
    <div className={`rounded-xl border bg-slate-900 px-3 py-3 ${t.border}`}>
      <div className="text-[10px] text-slate-500 uppercase tracking-wider font-mono mb-1">{name}</div>
      <div className={`text-xl font-bold font-mono ${t.val}`}>{credits.toLocaleString()}</div>
      <div className="text-[10px] text-slate-600 mt-0.5">
        {credits > 5000 ? 'Credits OK' : credits > 1000 ? 'Low credits' : '⚠ Critical'}
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

const REFETCH_INTERVAL = 60_000

export function AdminHomePage() {
  const {
    data: snapshot, isFetching, isLoading, error, refetch, dataUpdatedAt,
  } = useAdminSystemStatus(REFETCH_INTERVAL)

  // P4: Live active jobs — polls every 15s independently
  const { data: jobsData } = useAdminActiveJobs(15_000)
  const processingCount = jobsData?.processing_count ?? 0
  const pendingCount    = jobsData?.pending_count ?? 0
  const liveJobTotal    = processingCount + pendingCount

  const [countdown, setCountdown] = useState(REFETCH_INTERVAL / 1000)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    setCountdown(REFETCH_INTERVAL / 1000)
    timerRef.current && clearInterval(timerRef.current)
    timerRef.current = setInterval(
      () => setCountdown(c => (c <= 1 ? REFETCH_INTERVAL / 1000 : c - 1)),
      1_000,
    )
    return () => { timerRef.current && clearInterval(timerRef.current) }
  }, [dataUpdatedAt])

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <svg className="h-8 w-8 animate-spin text-slate-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      </div>
    )
  }

  const s = snapshot
  const alerts = s ? buildAlerts(s) : []

  // Derived metrics
  const downloadsToday = s?.stats.total_downloads_today ?? 0
  const totalUsers     = s?.stats.total_users ?? 0
  const signupsToday   = s?.signups.today ?? 0
  const totalJobs24h   = s?.analytics.summary?.total_jobs ?? 0
  const failedJobs24h  = s?.analytics.summary?.total_failed ?? 0
  const successRate    = s?.analytics.summary?.success_rate ?? 100
  const queueDepth     = s?.ops.queue_health?.depth ?? 0
  const queueOk        = s?.ops.queue_health?.ok !== false
  const anomalyCount   = s?.ops.anomaly_count ?? 0
  const providers      = s?.stats.providers ?? {}
  const platformTotals = (s?.platformStats.totals ?? []).filter(p => p.total > 0)
  const maxPlatTotal   = Math.max(...platformTotals.map(p => p.total), 1)
  const failedJobs     = (s?.stats.failed_jobs ?? []).slice(0, 5)
  const daily7d        = s?.analytics7d.daily_stats ?? []

  // Proxy pools — only platforms with >0 total or that are "required"
  const PROXY_REQUIRED = ['youtube', 'tiktok', 'facebook', 'instagram', 'twitter', 'xiaohongshu']
  const pools = s?.proxyPools.pools ?? {}
  const proxyEntries = Object.entries(pools)
    .filter(([p]) => PROXY_REQUIRED.includes(p) || (pools[p]?.total ?? 0) > 0)
    .sort(([a], [b]) => {
      const ai = PROXY_REQUIRED.indexOf(a), bi = PROXY_REQUIRED.indexOf(b)
      return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi)
    })

  const errObj = s?.errors.summary_24h
  const errors24h = typeof errObj === 'object' && errObj !== null
    ? (errObj as Record<string, number>).total ?? 0
    : (errObj as number | undefined) ?? 0

  const errorMsg = error instanceof Error ? error.message : error ? 'Failed to load' : null

  // YouTube status card derived values
  const yt = s?.youtubeStatus ?? {}
  const ytEnabled = yt.enabled !== false
  const ytCircuit = yt.circuit_state ?? 'unknown'
  const ytValue = !ytEnabled ? 'Off'
    : ytCircuit === 'open' ? 'Down'
    : ytCircuit === 'half' ? 'Degraded'
    : ytCircuit === 'closed' ? 'Live'
    : 'Unknown'
  const ytTone: Tone = !ytEnabled ? 'slate'
    : ytCircuit === 'open' ? 'red'
    : ytCircuit === 'half' ? 'amber'
    : ytCircuit === 'closed' ? 'green'
    : 'slate'
  const ytSub = yt.proxy_bytes_today_gb != null && yt.proxy_limit_gb != null
    ? `Proxy: ${yt.proxy_bytes_today_gb.toFixed(1)}GB / ${yt.proxy_limit_gb}GB`
    : ytCircuit !== 'unknown' ? `Circuit: ${ytCircuit}` : 'Status unknown'

  return (
    <div className="flex flex-col gap-6 pb-8">

      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">Overview</h1>
          <p className="text-[11px] text-slate-500 mt-0.5">
            {isFetching ? 'Refreshing…' : `Auto-refresh in ${countdown}s`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* P4: Live active jobs badge */}
          {liveJobTotal > 0 && (
            <div className="flex items-center gap-1.5 rounded-lg border border-blue-800/50 bg-blue-950/40 px-2.5 py-1.5 text-[11px] text-blue-300">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
              <span className="font-mono font-semibold">{liveJobTotal}</span>
              <span className="text-blue-400/80">
                {processingCount > 0 && `${processingCount} running`}
                {processingCount > 0 && pendingCount > 0 && ' / '}
                {pendingCount > 0 && `${pendingCount} pending`}
              </span>
            </div>
          )}
          {liveJobTotal === 0 && (
            <div className="flex items-center gap-1.5 text-[11px] text-slate-600">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-emerald-500" />
              No active jobs
            </div>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200 hover:border-slate-600 transition-colors disabled:opacity-40"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="rounded-lg border border-red-800 bg-red-900/20 px-4 py-3 text-xs text-red-300">{errorMsg}</div>
      )}

      {/* ── Active alerts ── */}
      {alerts.length > 0 && <ActiveAlertsBanner alerts={alerts} />}

      {/* ── Key metrics row 1 ── */}
      <section>
        <SectionTitle title="Hoạt động hôm nay" />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricCard
            label="Lượt tải hôm nay"
            value={fmt(downloadsToday)}
            sub={totalJobs24h > 0 ? `${fmt(totalJobs24h)} jobs ghi nhận` : undefined}
            tone="blue"
            link="/vid-admin/analytics"
          />
          <MetricCard
            label="Success rate 24h"
            value={successRate !== undefined ? `${successRate.toFixed(1)}%` : '—'}
            sub={failedJobs24h > 0 ? `${failedJobs24h} failed` : 'Không có lỗi'}
            tone={successRate >= 99 ? 'green' : successRate >= 95 ? 'amber' : 'red'}
            link="/vid-admin/analytics"
          />
          <MetricCard
            label="Users (tổng)"
            value={fmt(totalUsers)}
            sub={signupsToday > 0 ? `+${signupsToday} đăng ký hôm nay` : 'Chưa có đăng ký mới'}
            tone="purple"
            link="/vid-admin/users"
          />
          <MetricCard
            label="Jobs đang chạy"
            value={fmt(liveJobTotal)}
            sub={processingCount > 0 ? `${processingCount} processing, ${pendingCount} pending` : 'Queue trống'}
            tone={liveJobTotal > 50 ? 'amber' : liveJobTotal > 0 ? 'cyan' : 'green'}
            pulse={liveJobTotal > 0}
            link="/vid-admin/queue"
          />
        </div>
      </section>

      {/* ── Key metrics row 2 ── */}
      <section>
        <SectionTitle title="Hệ thống" />
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <MetricCard
            label="YouTube"
            value={ytValue}
            sub={ytSub}
            tone={ytTone}
            link="/vid-admin/platforms"
          />
          <MetricCard
            label="Lỗi 24h"
            value={fmt(errors24h)}
            tone={errors24h === 0 ? 'green' : errors24h > 10 ? 'red' : 'amber'}
            link="/vid-admin/analytics"
          />
          <MetricCard
            label="Anomaly alerts"
            value={fmt(anomalyCount)}
            sub={anomalyCount === 0 ? 'All clear' : `${anomalyCount} cần xem`}
            tone={anomalyCount === 0 ? 'green' : 'red'}
          />
          <MetricCard
            label="Queue depth"
            value={fmt(queueDepth)}
            sub={queueOk ? 'Queue healthy' : 'Queue degraded'}
            tone={queueOk ? 'green' : 'red'}
            link="/vid-admin/queue"
          />
          <MetricCard
            label="Failed jobs 24h"
            value={fmt(failedJobs24h)}
            sub={totalJobs24h > 0 ? `/ ${fmt(totalJobs24h)} total` : undefined}
            tone={failedJobs24h === 0 ? 'green' : failedJobs24h > 10 ? 'red' : 'amber'}
            link="/vid-admin/jobs"
          />
        </div>
      </section>

      {/* ── P2: 7-day sparklines ── */}
      {daily7d.length > 1 && (
        <section>
          <SectionTitle title="Trend 7 ngày" linkTo="/vid-admin/analytics" linkLabel="Full analytics →" />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="rounded-xl border border-slate-800 bg-slate-900 px-4 py-3">
              <MiniSparkline data={daily7d} color="#60a5fa" label="Downloads / ngày" valueKey="total" />
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900 px-4 py-3">
              <MiniSparkline data={daily7d} color="#34d399" label="Success / ngày" valueKey="success" />
            </div>
            <div className="rounded-xl border border-slate-800 bg-slate-900 px-4 py-3">
              <MiniSparkline data={daily7d} color="#f87171" label="Failed / ngày" valueKey="failed" />
            </div>
          </div>
        </section>
      )}

      {/* ── Two-column: platforms + failures ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Platform breakdown */}
        {platformTotals.length > 0 && (
          <section>
            <SectionTitle title="Kênh tải hôm nay" linkTo="/vid-admin/analytics" linkLabel="Analytics →" />
            <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
              {platformTotals.slice(0, 8).map(p => (
                <PlatformBar key={p.platform} p={p} maxTotal={maxPlatTotal} />
              ))}
              {platformTotals.length === 0 && (
                <p className="text-xs text-slate-600 text-center py-4">Chưa có dữ liệu hôm nay</p>
              )}
            </div>
          </section>
        )}

        {/* Recent failures */}
        {failedJobs.length > 0 && (
          <section>
            <SectionTitle title="Recent Failures" linkTo="/vid-admin/jobs" linkLabel="All jobs →" />
            <div className="rounded-xl border border-slate-800 bg-slate-900 px-4 py-2">
              {failedJobs.map((f, i) => <FailureRow key={f.id ?? i} job={f} />)}
            </div>
          </section>
        )}
      </div>

      {/* ── P3: Proxy pool status ── */}
      {proxyEntries.length > 0 && (
        <section>
          <SectionTitle title="Proxy Pool" linkTo="/vid-admin/proxy" linkLabel="Manage proxies →" />
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
            {proxyEntries.map(([platform, pool]) => (
              <ProxyPoolCard
                key={platform}
                platform={platform}
                redis={pool.redis}
                env={pool.env}
                total={pool.total}
              />
            ))}
          </div>
          {proxyEntries.some(([, p]) => p.total === 0) && (
            <p className="mt-2 text-[11px] text-amber-500/80">
              ⚠ Một số platform không có proxy — tải có thể bị chặn.{' '}
              <Link to="/vid-admin/proxy" className="underline hover:text-amber-300">Thêm proxy →</Link>
            </p>
          )}
        </section>
      )}

      {/* ── Provider credits ── */}
      {Object.keys(providers).length > 0 && (
        <section>
          <SectionTitle title="API Credits" linkTo="/vid-admin/proxy" linkLabel="Proxy →" />
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Object.entries(providers).map(([name, credits]) => (
              <ProviderPill key={name} name={name} credits={credits} />
            ))}
          </div>
        </section>
      )}

      {/* ── Fallback summary ── */}
      {s?.ops.fallback_summary && Object.keys(s.ops.fallback_summary).length > 0 && (
        <section>
          <SectionTitle title="Proxy Fallback Success Rate" linkTo="/vid-admin/proxy" linkLabel="Proxy →" />
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2">
            {Object.entries(s.ops.fallback_summary).map(([platform, info]) => {
              const rate = info.success_rate ?? 100
              const tone: Tone = rate >= 95 ? 'green' : rate >= 80 ? 'amber' : 'red'
              return (
                <div key={platform} className={`rounded-lg border bg-slate-900 px-3 py-2.5 ${TONE[tone].border}`}>
                  <div className="text-[10px] text-slate-500 uppercase font-mono mb-1">
                    {PLAT_ABBR[platform.toLowerCase()] ?? platform}
                  </div>
                  <div className={`text-base font-bold font-mono ${TONE[tone].val}`}>
                    {rate.toFixed(0)}%
                  </div>
                  {info.top_layer && (
                    <div className="text-[9px] text-slate-600 mt-0.5 truncate">{info.top_layer}</div>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* ── Quick links ── */}
      <section>
        <SectionTitle title="Quick links" />
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
          {[
            { label: 'Platforms', to: '/vid-admin/platforms' },
            { label: 'Cookies',   to: '/vid-admin/cookies'   },
            { label: 'Proxy',     to: '/vid-admin/proxy'     },
            { label: 'Jobs',      to: '/vid-admin/jobs'      },
            { label: 'Users',     to: '/vid-admin/users'     },
            { label: 'Analytics', to: '/vid-admin/analytics' },
          ].map(l => (
            <Link
              key={l.to}
              to={l.to}
              className="rounded-lg border border-slate-800 bg-slate-900 px-3 py-2.5 text-center text-xs text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-colors"
            >
              {l.label}
            </Link>
          ))}
        </div>
      </section>

    </div>
  )
}
