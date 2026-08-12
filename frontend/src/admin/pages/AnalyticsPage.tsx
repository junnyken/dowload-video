import { useState, useEffect, useCallback } from 'react'
import { adminFetch } from '../utils/adminFetch'
import { cn } from '../utils/cn'

// ─── Types ────────────────────────────────────────────────────────────────────

interface DailyStat {
  date: string
  total: number
  success: number
  failed: number
}

interface PlatformStat {
  platform: string
  count: number
}

interface AnalyticsSummary {
  total_jobs: number
  success_rate: number
  total_failed: number
  avg_daily: number
}

interface AnalyticsResponse {
  daily_stats: DailyStat[]
  platform_stats: PlatformStat[]
  summary: AnalyticsSummary
}

interface ErrorPattern {
  pattern: string
  count: number
}

interface PlatformFailRate {
  platform: string
  total: number
  failed: number
  fail_rate: number
}

interface ErrorsResponse {
  error_patterns: ErrorPattern[]
  summary_24h: { total: number; failed: number; fail_rate: number } | number
  platform_fail_rates: PlatformFailRate[]
}

// ─── Constants ────────────────────────────────────────────────────────────────

const PLATFORM_COLORS: Record<string, string> = {
  youtube: '#ef4444',
  tiktok: '#f97316',
  instagram: '#a855f7',
  twitter: '#3b82f6',
  facebook: '#60a5fa',
  soundcloud: '#f59e0b',
  threads: '#818cf8',
  spotify: '#22c55e',
  vimeo: '#06b6d4',
  reddit: '#fb923c',
}

function platformColor(name: string): string {
  return PLATFORM_COLORS[name.toLowerCase()] ?? '#94a3b8'
}

// ─── Utility ──────────────────────────────────────────────────────────────────

function fmtRate(v: number) {
  return `${v.toFixed(1)}%`
}

function shortDate(iso: string) {
  // iso: "2026-06-30" → "6/30"
  const parts = iso.split('-')
  if (parts.length < 3) return iso
  return `${parseInt(parts[1])}/${parseInt(parts[2])}`
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  sub,
  tone = 'default',
}: {
  label: string
  value: string | number
  sub?: string
  tone?: 'green' | 'red' | 'blue' | 'default'
}) {
  const toneClass = {
    green: 'text-emerald-400',
    red: 'text-red-400',
    blue: 'text-blue-400',
    default: 'text-slate-100',
  }[tone]

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 flex flex-col gap-1">
      <span className="font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">{label}</span>
      <span className={cn('text-2xl font-bold font-mono', toneClass)}>{value}</span>
      {sub && <span className="text-[10px] text-slate-600">{sub}</span>}
    </div>
  )
}

// ─── SVG Polyline Trend Chart ─────────────────────────────────────────────────

function TrendChart({ stats }: { stats: DailyStat[] }) {
  const W = 600
  const H = 200
  const PAD = 40

  const inner_w = W - PAD * 2
  const inner_h = H - PAD * 2

  const maxVal = Math.max(...stats.map(s => s.total), 1)

  function xOf(i: number) {
    return PAD + (i / Math.max(stats.length - 1, 1)) * inner_w
  }
  function yOf(v: number) {
    return PAD + inner_h - (v / maxVal) * inner_h
  }

  function toPoints(getter: (s: DailyStat) => number) {
    return stats.map((s, i) => `${xOf(i)},${yOf(getter(s))}`).join(' ')
  }

  // Y-axis: 4 grid lines at 0, 25, 50, 75, 100% of max
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(pct => ({
    y: PAD + inner_h - pct * inner_h,
    label: Math.round(pct * maxVal).toString(),
  }))

  // X-axis: show label every N days to avoid crowding
  const step = stats.length > 14 ? Math.ceil(stats.length / 7) : 1

  const series = [
    { name: 'Total', getter: (s: DailyStat) => s.total, color: '#94a3b8' },
    { name: 'Success', getter: (s: DailyStat) => s.success, color: '#34d399' },
    { name: 'Failed', getter: (s: DailyStat) => s.failed, color: '#f87171' },
  ]

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full max-w-full"
        style={{ minWidth: 320, height: 200 }}
        aria-label="Daily trend chart"
      >
        {/* Grid lines */}
        {yTicks.map(({ y, label }) => (
          <g key={y}>
            <line
              x1={PAD} y1={y} x2={W - PAD} y2={y}
              stroke="#1e293b" strokeWidth={1}
            />
            <text
              x={PAD - 5} y={y + 4}
              textAnchor="end"
              fontSize={9}
              fill="#475569"
            >
              {label}
            </text>
          </g>
        ))}

        {/* X axis labels */}
        {stats.map((s, i) => {
          if (i % step !== 0 && i !== stats.length - 1) return null
          return (
            <text
              key={i}
              x={xOf(i)}
              y={H - PAD + 14}
              textAnchor="middle"
              fontSize={8}
              fill="#475569"
            >
              {shortDate(s.date)}
            </text>
          )
        })}

        {/* Polylines */}
        {series.map(({ name, getter, color }) => (
          <polyline
            key={name}
            points={toPoints(getter)}
            fill="none"
            stroke={color}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}

        {/* Dots */}
        {series.map(({ name, getter, color }) =>
          stats.map((s, i) => (
            <circle
              key={`${name}-${i}`}
              cx={xOf(i)}
              cy={yOf(getter(s))}
              r={3}
              fill={color}
              stroke="#0f172a"
              strokeWidth={1.5}
            >
              <title>{`${s.date} · ${name}: ${getter(s)}`}</title>
            </circle>
          ))
        )}

        {/* X axis baseline */}
        <line
          x1={PAD} y1={PAD + inner_h} x2={W - PAD} y2={PAD + inner_h}
          stroke="#334155" strokeWidth={1}
        />
      </svg>

      {/* Legend */}
      <div className="mt-2 flex gap-5 px-2">
        {series.map(({ name, color }) => (
          <div key={name} className="flex items-center gap-1.5">
            <span className="inline-block h-2 w-6 rounded-full" style={{ background: color }} />
            <span className="text-[10px] text-slate-500">{name}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Platform breakdown horizontal bars ───────────────────────────────────────

function PlatformBars({ stats }: { stats: PlatformStat[] }) {
  const total = stats.reduce((s, p) => s + p.count, 0) || 1
  const sorted = [...stats].sort((a, b) => b.count - a.count)

  return (
    <div className="flex flex-col gap-2.5">
      {sorted.map(p => {
        const pct = (p.count / total) * 100
        const color = platformColor(p.platform)
        return (
          <div key={p.platform} className="flex items-center gap-3">
            <span className="w-24 font-mono text-[10px] capitalize text-slate-400 truncate">{p.platform}</span>
            <div className="flex-1 overflow-hidden rounded-full bg-slate-800 h-2">
              <div
                className="h-2 rounded-full transition-all duration-500"
                style={{ width: `${pct}%`, background: color }}
              />
            </div>
            <span className="w-16 text-right font-mono text-[10px] text-slate-400">
              {p.count.toLocaleString()} <span className="text-slate-600">({pct.toFixed(1)}%)</span>
            </span>
          </div>
        )
      })}
      {sorted.length === 0 && (
        <p className="text-xs text-slate-600">No platform data.</p>
      )}
    </div>
  )
}

// ─── Error patterns small chart ───────────────────────────────────────────────

function ErrorPatternBars({ patterns }: { patterns: ErrorPattern[] }) {
  const max = Math.max(...patterns.map(p => p.count), 1)
  const sorted = [...patterns].sort((a, b) => b.count - a.count).slice(0, 10)

  return (
    <div className="flex flex-col gap-2.5">
      {sorted.map((p, i) => {
        const pct = (p.count / max) * 100
        return (
          <div key={i} className="flex items-center gap-3">
            <span
              className="w-40 truncate text-[10px] text-slate-400"
              title={p.pattern}
            >
              {p.pattern}
            </span>
            <div className="flex-1 overflow-hidden rounded-full bg-slate-800 h-1.5">
              <div
                className="h-1.5 rounded-full bg-red-500/70 transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="w-8 text-right font-mono text-[10px] font-semibold text-red-400">{p.count}</span>
          </div>
        )
      })}
      {sorted.length === 0 && (
        <p className="text-xs text-slate-600">No error patterns found.</p>
      )}
    </div>
  )
}

// ─── Platform fail rate table ─────────────────────────────────────────────────

type SortKey = 'platform' | 'total' | 'failed' | 'fail_rate'

function PlatformFailTable({ rows }: { rows: PlatformFailRate[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('fail_rate')
  const [sortAsc, setSortAsc] = useState(false)

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortAsc(a => !a)
    } else {
      setSortKey(key)
      setSortAsc(false)
    }
  }

  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey]
    const bv = b[sortKey]
    if (typeof av === 'string' && typeof bv === 'string') {
      return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av)
    }
    return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number)
  })

  function ColHead({ col, label }: { col: SortKey; label: string }) {
    const active = sortKey === col
    return (
      <th
        className={cn(
          'cursor-pointer select-none py-2 text-right font-mono text-[9px] uppercase tracking-widest',
          active ? 'text-slate-300' : 'text-slate-600',
          'hover:text-slate-400',
        )}
        onClick={() => handleSort(col)}
      >
        {label}
        {active && <span className="ml-0.5">{sortAsc ? '↑' : '↓'}</span>}
      </th>
    )
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-800">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-slate-800 bg-slate-900/80">
            <th
              className={cn(
                'cursor-pointer select-none py-2 pl-3 text-left font-mono text-[9px] uppercase tracking-widest',
                sortKey === 'platform' ? 'text-slate-300' : 'text-slate-600',
                'hover:text-slate-400',
              )}
              onClick={() => handleSort('platform')}
            >
              Platform{sortKey === 'platform' && <span className="ml-0.5">{sortAsc ? '↑' : '↓'}</span>}
            </th>
            <ColHead col="total" label="Total" />
            <ColHead col="failed" label="Failed" />
            <ColHead col="fail_rate" label="Fail %" />
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/50">
          {sorted.map(r => {
            const rate = r.fail_rate ?? 0
            const rateTone =
              rate >= 50 ? 'text-red-400' : rate >= 20 ? 'text-amber-400' : 'text-emerald-400'
            return (
              <tr key={r.platform} className="hover:bg-slate-800/20">
                <td className="py-2 pl-3 font-mono text-[10px] capitalize text-slate-300">{r.platform}</td>
                <td className="py-2 pr-3 text-right font-mono text-slate-400">{r.total.toLocaleString()}</td>
                <td className="py-2 pr-3 text-right font-mono text-red-400/80">{r.failed.toLocaleString()}</td>
                <td className={cn('py-2 pr-3 text-right font-mono font-semibold', rateTone)}>
                  {fmtRate(rate)}
                </td>
              </tr>
            )
          })}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={4} className="py-4 text-center text-xs text-slate-600">No data.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export function AnalyticsPage() {
  const [days, setDays] = useState<7 | 30>(7)
  const [analytics, setAnalytics] = useState<AnalyticsResponse | null>(null)
  const [errors, setErrors] = useState<ErrorsResponse | null>(null)
  const [loadingA, setLoadingA] = useState(true)
  const [loadingE, setLoadingE] = useState(true)
  const [errorA, setErrorA] = useState<string | null>(null)
  const [errorE, setErrorE] = useState<string | null>(null)

  const fetchAnalytics = useCallback(async (d: 7 | 30) => {
    setLoadingA(true)
    setErrorA(null)
    try {
      const res = await adminFetch<AnalyticsResponse>(`/analytics?days=${d}`)
      setAnalytics(res)
    } catch (e) {
      setErrorA(e instanceof Error ? e.message : 'Failed to load analytics')
    } finally {
      setLoadingA(false)
    }
  }, [])

  const fetchErrors = useCallback(async () => {
    setLoadingE(true)
    setErrorE(null)
    try {
      const res = await adminFetch<ErrorsResponse>('/errors')
      setErrors(res)
    } catch (e) {
      setErrorE(e instanceof Error ? e.message : 'Failed to load error data')
    } finally {
      setLoadingE(false)
    }
  }, [])

  useEffect(() => {
    fetchAnalytics(days)
  }, [days, fetchAnalytics])

  useEffect(() => {
    fetchErrors()
  }, [fetchErrors])

  const summary = analytics?.summary
  const isLoading = loadingA || loadingE

  return (
    <div className="flex flex-col gap-6">
      {/* Header + days toggle */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm font-semibold text-slate-100">Analytics</h2>
          <p className="mt-0.5 text-xs text-slate-500">
            Download job stats · last {days} days
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { fetchAnalytics(days); fetchErrors() }}
            className="text-[10px] text-slate-500 hover:text-slate-300 mr-2"
          >
            ↺ Refresh
          </button>
          <div className="flex rounded-lg overflow-hidden border border-slate-800">
            {([7, 30] as const).map(d => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={cn(
                  'px-3 py-1 font-mono text-xs transition-colors',
                  days === d
                    ? 'bg-slate-700 text-slate-100'
                    : 'bg-slate-900 text-slate-500 hover:text-slate-300',
                )}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* KPI row */}
      {loadingA && !analytics ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="h-24 rounded-2xl border border-slate-800 bg-slate-900/60 animate-pulse" />
          ))}
        </div>
      ) : errorA && !analytics ? (
        <div className="rounded-2xl border border-red-900/40 bg-red-950/20 p-4 text-sm text-red-400">
          {errorA}
          <button
            onClick={() => fetchAnalytics(days)}
            className="ml-3 text-xs underline hover:text-red-300"
          >
            Retry
          </button>
        </div>
      ) : summary ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard
            label="Total Jobs"
            value={summary.total_jobs.toLocaleString()}
            sub={`last ${days} days`}
            tone="blue"
          />
          <StatCard
            label="Success Rate"
            value={fmtRate(summary.success_rate)}
            sub="succeeded / attempted"
            tone={summary.success_rate >= 90 ? 'green' : summary.success_rate >= 70 ? 'default' : 'red'}
          />
          <StatCard
            label="Avg Daily"
            value={Math.round(summary.avg_daily).toLocaleString()}
            sub="jobs per day"
          />
          <StatCard
            label="Total Failed"
            value={summary.total_failed.toLocaleString()}
            sub={`${days}d window`}
            tone={summary.total_failed > 0 ? 'red' : 'green'}
          />
        </div>
      ) : null}

      {/* Daily trend chart */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
        <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Daily Trend — {days}d
        </h3>
        {loadingA && !analytics ? (
          <div className="h-48 animate-pulse rounded-xl bg-slate-800/40" />
        ) : analytics?.daily_stats && analytics.daily_stats.length > 0 ? (
          <TrendChart stats={analytics.daily_stats} />
        ) : (
          <p className="text-xs text-slate-600">No daily data available.</p>
        )}
      </div>

      {/* Platform breakdown + error patterns side by side */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Platform breakdown */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
          <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">
            Platform Breakdown
          </h3>
          {loadingA && !analytics ? (
            <div className="space-y-2">
              {[0, 1, 2, 3].map(i => (
                <div key={i} className="h-4 animate-pulse rounded-full bg-slate-800" />
              ))}
            </div>
          ) : analytics?.platform_stats ? (
            <PlatformBars stats={analytics.platform_stats} />
          ) : (
            <p className="text-xs text-slate-600">No platform data.</p>
          )}
        </div>

        {/* Error patterns */}
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">
              Top Error Patterns
            </h3>
            {errors?.summary_24h != null && (
              <span className="font-mono text-[10px] text-red-400">
                {typeof errors.summary_24h === 'object'
                  ? errors.summary_24h.total
                  : errors.summary_24h} errors (24h)
              </span>
            )}
          </div>
          {loadingE && !errors ? (
            <div className="space-y-2">
              {[0, 1, 2, 3].map(i => (
                <div key={i} className="h-3 animate-pulse rounded-full bg-slate-800" />
              ))}
            </div>
          ) : errorE && !errors ? (
            <p className="text-xs text-red-400">{errorE}</p>
          ) : errors?.error_patterns ? (
            <ErrorPatternBars patterns={errors.error_patterns} />
          ) : (
            <p className="text-xs text-slate-600">No error data.</p>
          )}
        </div>
      </div>

      {/* Platform fail rates table */}
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
        <h3 className="mb-4 font-mono text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Platform Fail Rates
          <span className="ml-2 normal-case font-normal text-slate-600 tracking-normal">(click header to sort)</span>
        </h3>
        {loadingE && !errors ? (
          <div className="h-32 animate-pulse rounded-xl bg-slate-800/40" />
        ) : errors?.platform_fail_rates ? (
          <PlatformFailTable rows={errors.platform_fail_rates} />
        ) : (
          <p className="text-xs text-slate-600">No fail rate data.</p>
        )}
      </div>

      {isLoading && (analytics || errors) && (
        <div className="text-center text-[10px] text-slate-700 animate-pulse">Refreshing…</div>
      )}
    </div>
  )
}
