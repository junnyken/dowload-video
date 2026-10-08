import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ActiveAlertsBanner, type AlertItem } from '../panels/ActiveAlertsBanner'
import { useAdminSystemStatus } from '../hooks/useAdminSystemStatus'
import { useAdminActiveJobs } from '../hooks/useAdminActiveJobs'
import type { SystemSnapshot, PlatformStatsTotal, DailyStatEntry, SnapshotSource, RecentAttempt } from '../api/system'
import { buildAlerts } from '../utils/alerts'
import { absoluteTime, relativeTimeVi } from '../utils/anomalies'
import { NO_TRAFFIC_HINT, fmtRate, safeRate } from '../utils/rate'
import { coverageHint } from '../utils/coverage'
import { APPROX_DAY_HINT, VN_TIME_HINT, vnDayLabel } from '../utils/vnTime'

const UNAVAILABLE = 'Không tải được dữ liệu'

function fmt(v: number | undefined | null, decimals = 0): string {
  if (v === undefined || v === null) return '—'
  return v.toLocaleString('en-US', { maximumFractionDigits: decimals })
}

// ─── Tone system ──────────────────────────────────────────────────────────────

type Tone = 'green' | 'blue' | 'amber' | 'red' | 'purple' | 'slate' | 'cyan'

const TONE: Record<Tone, { val: string; border: string; dot: string }> = {
  green:  { val: 'text-success', border: 'border-line', dot: 'bg-success' },
  blue:   { val: 'text-fg-2',    border: 'border-line',    dot: 'bg-accent'    },
  amber:  { val: 'text-warning',   border: 'border-line',   dot: 'bg-warning'  },
  red:    { val: 'text-danger',     border: 'border-line',     dot: 'bg-danger'     },
  purple: { val: 'text-fg-2',  border: 'border-line',  dot: 'bg-accent'  },
  slate:  { val: 'text-fg-2',   border: 'border-line',      dot: 'bg-line-strong'   },
  cyan:   { val: 'text-fg-2',    border: 'border-line',    dot: 'bg-accent'    },
}

// ─── Metric card ──────────────────────────────────────────────────────────────

function MetricCard({
  label, value, sub, tone = 'slate', link, pulse, unavailable,
}: {
  label: string; value: string; sub?: string
  tone?: Tone; link?: string; pulse?: boolean
  /** The request behind this card failed: show "—", never a made-up zero. */
  unavailable?: boolean
}) {
  if (unavailable) { value = '—'; sub = UNAVAILABLE; tone = 'slate'; pulse = false }
  const t = TONE[tone]
  const body = (
    <div className={`rounded-card border bg-surface px-4 py-3.5 space-y-1 h-full shadow-card transition-colors hover:border-line-strong ${t.border}`}>
      <div className="text-[10px] text-fg-muted uppercase tracking-widest font-mono">{label}</div>
      <div className={`text-xl font-semibold font-mono tabular-nums flex items-center gap-2 ${t.val}`}>
        {pulse && <span className={`inline-block h-2 w-2 rounded-full animate-pulse ${t.dot}`} />}
        {value}
      </div>
      {sub && <div className={`text-[11px] ${unavailable ? 'text-warning' : 'text-fg-muted'}`}>{sub}</div>}
    </div>
  )
  return link ? <Link to={link} className="block">{body}</Link> : body
}

// ─── Section header ───────────────────────────────────────────────────────────

function SectionTitle({ title, hint, linkTo, linkLabel }: { title: string; hint?: string; linkTo?: string; linkLabel?: string }) {
  return (
    <div className="flex items-center justify-between mb-2.5">
      <h2 className="font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted">
        {title}
        {hint && <span className="ml-1.5 font-sans normal-case tracking-normal text-fg-muted opacity-70">({hint})</span>}
      </h2>
      {linkTo && (
        <Link to={linkTo} className="text-[11px] text-fg-muted hover:text-fg-2 transition-colors">{linkLabel ?? 'View all →'}</Link>
      )}
    </div>
  )
}

// ─── P2: Mini sparkline ───────────────────────────────────────────────────────

function MiniSparkline({
  data, color = 'var(--vg-accent)', label, valueKey,
}: {
  data: DailyStatEntry[]
  color?: string
  label: string
  valueKey: 'total' | 'success' | 'failed'
}) {
  if (!data || data.length < 2) {
    return <div className="h-16 flex items-center justify-center text-[10px] text-fg-muted">No data</div>
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
  const today = vals[vals.length - 1] ?? 0
  const approxIdx = data.map((d, i) => (d.approximate ? i : -1)).filter(i => i >= 0)

  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="text-[10px] text-fg-muted">{label}</span>
        <span className="font-mono text-xs font-semibold" style={{ color }}>{fmt(today)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
        <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
        {/* Approximate days (UTC-day fallback): hollow dashed marker */}
        {approxIdx.map(i => (
          <circle key={`a${i}`} cx={x(i)} cy={y(vals[i])} r={2.5} fill="var(--vg-surface)"
            stroke={color} strokeWidth={1} strokeDasharray="1.5 1.5">
            <title>{`${vnDayLabel(data[i].date)} · ${APPROX_DAY_HINT}`}</title>
          </circle>
        ))}
        {/* Last point dot */}
        <circle cx={x(data.length - 1)} cy={y(today)} r={2.5}
          fill={data[data.length - 1]?.approximate ? 'var(--vg-surface)' : color} stroke={color} strokeWidth={1} />
        {/* X labels — first, mid, last */}
        {[0, Math.floor(data.length / 2), data.length - 1].map(i => (
          <text key={i} x={x(i)} y={H} textAnchor={i === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle'} fontSize={7} fill="var(--vg-fg-muted)">
            {vnDayLabel(data[i]?.date)}
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
  const rate = safeRate(p.success_rate, p.total)
  const barColor = rate === null ? 'bg-line-strong' : p.success_rate >= 95 ? 'bg-success' : p.success_rate >= 80 ? 'bg-warning' : 'bg-danger'
  const rateColor = rate === null ? 'text-fg-muted' : p.success_rate >= 95 ? 'text-success' : p.success_rate >= 80 ? 'text-warning' : 'text-danger'
  const abbr = PLAT_ABBR[p.platform.toLowerCase()] ?? p.platform.slice(0, 2).toUpperCase()

  return (
    <div className="flex items-center gap-3">
      <div className="w-7 h-7 shrink-0 rounded-md border border-line bg-surface-2 flex items-center justify-center font-mono text-[10px] font-bold text-fg-muted">
        {abbr}
      </div>
      <div className="flex-1 space-y-1">
        <div className="flex items-center justify-between text-[11px]">
          <span className="text-fg-2 capitalize">{p.platform}</span>
          <span className="font-mono text-fg-muted">{fmt(p.total)} req</span>
        </div>
        <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden">
          <div className="h-full bg-line rounded-full relative" style={{ width: `${pct}%` }}>
            <div className={`absolute inset-0 ${barColor} rounded-full`} style={{ width: `${okPct}%` }} />
          </div>
        </div>
      </div>
      <span className={`w-10 shrink-0 text-right font-mono text-[10px] ${rateColor}`}>
        {fmtRate(rate, 0)}
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
    <div className={`rounded-control border bg-surface px-3 py-2.5 ${t.border}`}>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[10px] text-fg-muted uppercase font-mono">{abbr}</span>
        <span className={`text-xs font-bold font-mono ${t.val}`}>{total}</span>
      </div>
      <div className="text-[10px] text-fg-muted space-y-0.5">
        {redis > 0 && <div>Redis: {redis}</div>}
        {env > 0 && <div>Env: {env}</div>}
        {total === 0 && <div className="text-danger">No proxies</div>}
      </div>
    </div>
  )
}

// ─── Failed attempt row (task #6148) ──────────────────────────────────────────

const SOURCE_LABEL: Record<string, string> = { web: 'Web', extension: 'Tiện ích', app: 'App', api: 'API' }

function AttemptRow({ a }: { a: RecentAttempt }) {
  const iso = new Date(a.ts * 1000).toISOString()
  const meta = [
    a.status ? `HTTP ${a.status}` : null,
    a.error_code || null,
    SOURCE_LABEL[a.source] ?? null,
    a.kind === 'user' ? 'Tài khoản' : a.kind === 'guest' ? 'Khách' : null,
    a.quality || null,
    a.user_cookies ? 'cookie người dùng' : null,
  ].filter(Boolean).join(' · ')
  return (
    <div className="flex items-start gap-3 py-2 border-b border-line last:border-0 text-xs">
      <span className="shrink-0 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] uppercase text-fg-2">
        {a.platform || '?'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-fg-2 break-words line-clamp-2" title={a.reason}>{a.reason || 'Không ghi lý do'}</p>
        <p className="text-fg-muted text-[10px] break-words">{meta}{a.url ? ` · ${a.url}` : ''}{a.path && a.path !== '/api/v1/fetch-link' ? ` · ${a.path}` : ''}</p>
      </div>
      <span className="shrink-0 text-fg-muted text-[10px] font-mono" title={absoluteTime(iso)}>{relativeTimeVi(iso)}</span>
    </div>
  )
}

// ─── Failure row ──────────────────────────────────────────────────────────────

// download_jobs has error_message / error_type / created_at. The row read
// error / phase / time, which no job carries, so every failure showed
// "Unknown error" and "—" (08/10, task #6148).
function FailureRow({ job }: { job: NonNullable<SystemSnapshot['stats']['failed_jobs']>[number] }) {
  return (
    <div className="flex items-start gap-3 py-2 border-b border-line last:border-0 text-xs">
      <span className="shrink-0 rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] uppercase text-fg-2">
        {job.platform ?? '?'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-fg-2 truncate" title={job.error_message ?? undefined}>{job.error_message || 'Không ghi lý do'}</p>
        <p className="text-fg-muted text-[10px]">{job.error_type || job.job_stage || '—'}</p>
      </div>
      <span className="shrink-0 text-fg-muted text-[10px] font-mono" title={absoluteTime(job.created_at)}>{relativeTimeVi(job.created_at)}</span>
    </div>
  )
}

// ─── Provider credit ──────────────────────────────────────────────────────────

function ProviderPill({ name, credits }: { name: string; credits: number }) {
  const tone: Tone = credits > 5000 ? 'green' : credits > 1000 ? 'amber' : 'red'
  const t = TONE[tone]
  return (
    <div className={`rounded-card border bg-surface px-3 py-3 shadow-card ${t.border}`}>
      <div className="text-[10px] text-fg-muted uppercase tracking-wider font-mono mb-1">{name}</div>
      <div className={`text-xl font-semibold font-mono tabular-nums ${t.val}`}>{credits.toLocaleString()}</div>
      <div className="text-[10px] text-fg-muted mt-0.5">
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
        <svg className="h-8 w-8 animate-spin text-fg-muted" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
      </div>
    )
  }

  const s = snapshot
  const built = s ? buildAlerts(s) : { fresh: [], stale: [], anomalyCount: 0 }
  const alerts = built.fresh
  const down = new Set<SnapshotSource>(s?.failed ?? [])
  const bad = (...k: SnapshotSource[]) => k.some(x => down.has(x))

  // Derived metrics
  const downloadsToday = s?.stats.total_downloads_today ?? 0
  const todayApprox    = s?.stats.today?.approximate === true
  const totalUsers     = s?.stats.total_users ?? 0
  const signupsToday   = s?.signups.today ?? 0
  const totalJobs24h   = s?.analytics.summary?.total_jobs ?? 0
  // One window, one source: /stats downloads_24h and /errors summary_24h are
  // both built from the outcome store's hourly counters.
  const d24            = s?.stats.downloads_24h
  const failed24h      = d24?.failed ?? 0
  const attempts24h    = d24?.attempts ?? 0
  const successRate    = safeRate(d24?.success_rate, d24?.attempts)
  const hint24h        = coverageHint(d24) ?? coverageHint(s?.errors)
  const queueDepth     = s?.ops.queue_health?.depth ?? 0
  const queueOk        = s?.ops.queue_health?.ok !== false
  const anomalyCount   = built.anomalyCount
  const providers      = s?.stats.providers ?? {}
  const platformTotals = (s?.platformStats.totals ?? []).filter(p => p.total > 0)
  const maxPlatTotal   = Math.max(...platformTotals.map(p => p.total), 1)
  const failedJobs     = (s?.stats.failed_jobs ?? []).slice(0, 5)
  // undefined (older server) reads as empty; null means Redis was unreadable
  const recentAttempts = s?.errors.recent_attempts === null ? null : (s?.errors.recent_attempts ?? [])
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

  const errSummary = s?.errors.summary_24h
  const errors24h = errSummary?.failed ?? 0
  const errorsOf24h = errSummary?.total ?? 0

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
          <h1 className="text-lg font-semibold tracking-tight text-fg">Overview</h1>
          <p className="text-[11px] text-fg-muted mt-0.5">
            {isFetching ? 'Refreshing…' : `Auto-refresh in ${countdown}s`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* P4: Live active jobs badge */}
          {liveJobTotal > 0 && (
            <div className="flex items-center gap-1.5 rounded-control border border-line bg-surface px-2.5 py-1.5 text-[11px] text-fg-2">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
              <span className="font-mono font-semibold">{liveJobTotal}</span>
              <span className="text-fg-2">
                {processingCount > 0 && `${processingCount} running`}
                {processingCount > 0 && pendingCount > 0 && ' / '}
                {pendingCount > 0 && `${pendingCount} pending`}
              </span>
            </div>
          )}
          {liveJobTotal === 0 && (
            <div className="flex items-center gap-1.5 text-[11px] text-fg-muted">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-success" />
              No active jobs
            </div>
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 rounded-control border border-line px-3 py-1.5 text-xs text-fg-muted hover:text-fg-2 hover:border-line-strong transition-colors disabled:opacity-40"
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75} className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      {errorMsg && (
        <div className="rounded-control border border-danger/30 bg-danger-soft px-4 py-3 text-xs text-danger">{errorMsg}</div>
      )}

      {/* ── Active alerts ── */}
      {alerts.length > 0 && <ActiveAlertsBanner alerts={alerts} />}
      {built.stale.length > 0 && (
        <details className="rounded-card border border-line bg-surface px-3.5 py-2.5">
          <summary className="cursor-pointer text-xs text-fg-muted hover:text-fg-2">
            Cảnh báo cũ hơn 24h ({built.stale.length}) ·{' '}
            <Link to="/vid-admin/anomalies" className="underline hover:text-fg-2" onClick={e => e.stopPropagation()}>
              Xem trang Anomalies
            </Link>
          </summary>
          <div className="mt-2.5"><ActiveAlertsBanner alerts={built.stale} /></div>
        </details>
      )}

      {/* ── Key metrics row 1 ── */}
      <section>
        <SectionTitle title="Hoạt động hôm nay" hint={VN_TIME_HINT} />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricCard
            label="Lượt tải hôm nay"
            value={fmt(downloadsToday)}
            sub={todayApprox ? APPROX_DAY_HINT : totalJobs24h > 0 ? `${fmt(totalJobs24h)} jobs ghi nhận` : undefined}
            tone="blue"
            unavailable={bad('stats')}
            link="/vid-admin/analytics"
          />
          <MetricCard
            label="Success rate 24h"
            value={fmtRate(successRate)}
            sub={hint24h ?? (successRate === null ? NO_TRAFFIC_HINT : failed24h > 0 ? `${failed24h} failed (/ ${fmt(attempts24h)} total)` : 'Không có lỗi')}
            tone={successRate === null ? 'slate' : successRate >= 99 ? 'green' : successRate >= 95 ? 'amber' : 'red'}
            unavailable={bad('stats')}
            link="/vid-admin/analytics"
          />
          <MetricCard
            label="Users (tổng)"
            value={fmt(totalUsers)}
            sub={signupsToday > 0 ? `+${signupsToday} đăng ký hôm nay` : 'Chưa có đăng ký mới'}
            tone="purple"
            unavailable={bad('stats')}
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
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricCard
            label="YouTube"
            value={ytValue}
            sub={ytSub}
            tone={ytTone}
            unavailable={bad('youtubeStatus')}
            link="/vid-admin/platforms"
          />
          <MetricCard
            label="Lỗi 24h"
            value={fmt(errors24h)}
            sub={hint24h ?? (errorsOf24h > 0 ? `/ ${fmt(errorsOf24h)} total` : undefined)}
            tone={errors24h === 0 ? 'green' : errors24h > 10 ? 'red' : 'amber'}
            unavailable={bad('errors')}
            link="/vid-admin/analytics"
          />
          <MetricCard
            label="Anomaly alerts"
            value={fmt(anomalyCount)}
            sub={anomalyCount === 0 ? 'All clear' : `${anomalyCount} cần xem`}
            tone={anomalyCount === 0 ? 'green' : 'red'}
            unavailable={bad('ops')}
          />
          <MetricCard
            label="Queue depth"
            value={fmt(queueDepth)}
            sub={queueOk ? 'Queue healthy' : 'Queue degraded'}
            tone={queueOk ? 'green' : 'red'}
            unavailable={bad('ops')}
            link="/vid-admin/queue"
          />
        </div>
      </section>

      {/* ── P2: 7-day sparklines ── */}
      {daily7d.length > 1 && (
        <section>
          <SectionTitle title="Trend 7 ngày" linkTo="/vid-admin/analytics" linkLabel="Full analytics →" />
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="rounded-card border border-line bg-surface shadow-card px-4 py-3">
              <MiniSparkline data={daily7d} color="var(--vg-accent)" label="Downloads / ngày" valueKey="total" />
            </div>
            <div className="rounded-card border border-line bg-surface shadow-card px-4 py-3">
              <MiniSparkline data={daily7d} color="var(--vg-success)" label="Success / ngày" valueKey="success" />
            </div>
            <div className="rounded-card border border-line bg-surface shadow-card px-4 py-3">
              <MiniSparkline data={daily7d} color="var(--vg-danger)" label="Failed / ngày" valueKey="failed" />
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
            <div className="rounded-card border border-line bg-surface shadow-card p-4 space-y-3">
              {platformTotals.slice(0, 8).map(p => (
                <PlatformBar key={p.platform} p={p} maxTotal={maxPlatTotal} />
              ))}
              {platformTotals.length === 0 && (
                <p className="text-xs text-fg-muted text-center py-4">Chưa có dữ liệu hôm nay</p>
              )}
            </div>
          </section>
        )}

        {/* Recent failures */}
        {failedJobs.length > 0 && (
          <section>
            <SectionTitle title="Job lỗi gần đây" linkTo="/vid-admin/jobs" linkLabel="All jobs →" />
            <p className="mb-2 text-[11px] text-fg-muted">Từ bảng job — chỉ gồm job hàng loạt / đặt lịch, không phải mọi lượt tải lỗi.</p>
            <div className="rounded-card border border-line bg-surface shadow-card px-4 py-2">
              {failedJobs.map((f, i) => <FailureRow key={f.id ?? i} job={f} />)}
            </div>
          </section>
        )}
      </div>

      {/* ── Every failed download, with its raw reason (task #6148) ── */}
      {s && !down.has('errors') && (
        <section>
          <SectionTitle title="Lượt tải lỗi gần đây" />
          <p className="mb-2 text-[11px] text-fg-muted">
            Mọi lượt tải lỗi (web, tiện ích, app) và lỗi máy chủ chưa xử lý — lý do gốc, đã che khoá/mật khẩu. Giữ 7 ngày.
          </p>
          <div className="rounded-card border border-line bg-surface shadow-card px-4 py-2">
            {recentAttempts === null ? (
              <p className="py-3 text-xs text-fg-muted">Không đọc được sổ lỗi (Redis) — không có nghĩa là không có lỗi.</p>
            ) : recentAttempts.length === 0 ? (
              <p className="py-3 text-xs text-fg-muted">Chưa có lượt tải lỗi nào được ghi.</p>
            ) : (
              recentAttempts.slice(0, 10).map((a, i) => <AttemptRow key={`${a.ts}-${i}`} a={a} />)
            )}
          </div>
        </section>
      )}

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
            <p className="mt-2 text-[11px] text-warning">
              ⚠ Một số platform không có proxy — tải có thể bị chặn.{' '}
              <Link to="/vid-admin/proxy" className="underline hover:text-warning">Thêm proxy →</Link>
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
              const rate = safeRate(info.success_rate, info.total)
              const tone: Tone = rate === null ? 'slate' : rate >= 95 ? 'green' : rate >= 80 ? 'amber' : 'red'
              return (
                <div key={platform} className={`rounded-control border bg-surface px-3 py-2.5 ${TONE[tone].border}`}>
                  <div className="text-[10px] text-fg-muted uppercase font-mono mb-1">
                    {PLAT_ABBR[platform.toLowerCase()] ?? platform}
                  </div>
                  <div className={`text-base font-bold font-mono ${TONE[tone].val}`}>
                    {fmtRate(rate, 0)}
                  </div>
                  {info.top_layer && (
                    <div className="text-[10px] text-fg-muted mt-0.5 truncate">{info.top_layer}</div>
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
              className="rounded-control border border-line bg-surface px-3 py-2.5 text-center text-xs text-fg-muted hover:text-fg-2 hover:border-line transition-colors"
            >
              {l.label}
            </Link>
          ))}
        </div>
      </section>

    </div>
  )
}
