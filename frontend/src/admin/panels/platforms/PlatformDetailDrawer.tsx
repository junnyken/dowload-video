import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '../../utils/cn'
import type { PlatformDetail, RecentJob, ErrorBreakdownItem, PhaseStatItem } from './platform.types'
import { PlatformStatusBadge, CircuitStatePill } from './PlatformStatusBadge'

// ─── Tab definitions ───────────────────────────────────────────────────────────

const TABS = ['Overview', 'Jobs', 'Errors', 'Phases', 'Config'] as const
type DrawerTab = (typeof TABS)[number]

// ─── Sub-components ────────────────────────────────────────────────────────────

function InfoGrid({ items }: { items: Array<{ label: string; value: string }> }) {
  return (
    <div className="grid grid-cols-2 gap-2">
      {items.map(({ label, value }) => (
        <div key={label} className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <p className="mb-1 text-[9px] font-semibold uppercase tracking-widest text-slate-600">
            {label}
          </p>
          <p className="font-mono text-sm font-semibold text-slate-200">{value}</p>
        </div>
      ))}
    </div>
  )
}

function JobResultIcon({ result }: { result: RecentJob['result'] }) {
  if (result === 'success') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 text-emerald-400">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    )
  }
  if (result === 'running') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 animate-spin text-blue-400">
        <path strokeLinecap="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 text-red-400">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  )
}

function JobsTab({ jobs }: { jobs: RecentJob[] }) {
  if (jobs.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-slate-600">
        No recent jobs
      </div>
    )
  }
  return (
    <div className="flex flex-col divide-y divide-slate-800/60">
      {jobs.map(job => (
        <div key={job.id} className="flex items-start gap-3 py-3">
          <div className="mt-0.5 flex-shrink-0">
            <JobResultIcon result={job.result} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate font-mono text-[11px] text-slate-400" title={job.url}>
              {job.url}
            </p>
            {job.error && (
              <p className="mt-0.5 text-[11px] text-red-500">{job.error}</p>
            )}
            <div className="mt-1 flex items-center gap-2 font-mono text-[9px] text-slate-700">
              {job.phase && <span>phase:{job.phase}</span>}
              <span>{(job.durationMs / 1000).toFixed(1)}s</span>
              <span>{job.startedAt}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function ErrorsTab({ errors }: { errors: ErrorBreakdownItem[] }) {
  if (errors.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-slate-600">No errors recorded</div>
    )
  }
  const total = errors.reduce((s, e) => s + e.count, 0)
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-[10px] text-slate-600">
        {total} total errors · last 1h
      </p>
      {errors.map(item => (
        <div key={item.errorType}>
          <div className="mb-1 flex items-center justify-between">
            <span className="font-mono text-[11px] text-slate-300">{item.errorType}</span>
            <span className="font-mono text-[10px] text-slate-500">
              {item.count} ({item.pct}%)
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
            <div
              className="h-full rounded-full bg-red-500 transition-all"
              style={{ width: `${item.pct}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

function SuccessRateBar({ rate }: { rate: number }) {
  const color =
    rate >= 90 ? 'bg-emerald-500' :
    rate >= 70 ? 'bg-amber-500' :
    'bg-red-500'
  const textColor =
    rate >= 90 ? 'text-emerald-400' :
    rate >= 70 ? 'text-amber-400' :
    'text-red-400'
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
        <div className={cn('h-full rounded-full transition-all', color)} style={{ width: `${rate}%` }} />
      </div>
      <span className={cn('w-8 flex-shrink-0 text-right font-mono text-[10px] font-semibold', textColor)}>
        {rate}%
      </span>
    </div>
  )
}

function PhasesTab({ phases }: { phases: PhaseStatItem[] }) {
  return (
    <div className="flex flex-col gap-4">
      {/* Phase stats table */}
      <div className="overflow-hidden rounded-xl border border-slate-800">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-slate-800">
              {['Phase', 'Success Rate', 'Avg Duration', 'Jobs'].map(h => (
                <th key={h} className="px-3 py-2 text-left font-mono text-[9px] font-semibold uppercase tracking-widest text-slate-600 first:pl-4">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {phases.map((p, i) => (
              <tr
                key={p.phase}
                className={cn('transition-colors hover:bg-slate-800/30', i < phases.length - 1 && 'border-b border-slate-800/60')}
              >
                <td className="py-2 pl-4 pr-3">
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400">
                    {p.phase}
                  </span>
                </td>
                <td className="py-2 pr-3 w-28">
                  <SuccessRateBar rate={p.successRate} />
                </td>
                <td className="py-2 pr-3">
                  <span className="font-mono text-[11px] text-slate-400">
                    {p.avgDurationMs < 1000
                      ? `${p.avgDurationMs}ms`
                      : `${(p.avgDurationMs / 1000).toFixed(1)}s`
                    }
                  </span>
                </td>
                <td className="py-2 pr-3">
                  <span className="font-mono text-[11px] text-slate-500">{p.totalJobs}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Heatmap placeholder */}
      <div className="rounded-xl border border-dashed border-slate-800 p-4 text-center">
        <p className="text-xs font-medium text-slate-500">Phase × Time Heatmap</p>
        <p className="mt-1 text-[11px] text-slate-700">
          Phase failure heatmap will render here — requires time-series phase data
          (planned for Phase 2).
        </p>
      </div>
    </div>
  )
}

function ConfigTab({ config }: { config: PlatformDetail['config'] }) {
  const rows = [
    { key: 'rate_limit',    label: 'Rate Limit',    value: config.rateLimit },
    { key: 'cookie_pool',   label: 'Cookie Pool',   value: config.cookiePool },
    { key: 'proxy',         label: 'Proxy',         value: config.proxy },
    { key: 'retry_policy',  label: 'Retry Policy',  value: config.retryPolicy },
  ]
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col divide-y divide-slate-800/60 rounded-xl border border-slate-800">
        {rows.map(row => (
          <div key={row.key} className="flex items-start justify-between gap-4 px-4 py-3">
            <span className="flex-shrink-0 text-[11px] font-medium text-slate-500">{row.label}</span>
            <span className="text-right font-mono text-[11px] text-slate-300">{row.value}</span>
          </div>
        ))}
      </div>

      {/* Config overrides placeholder */}
      <div className="rounded-xl border border-dashed border-slate-800 p-4">
        <p className="text-xs font-medium text-slate-500">Runtime Config Overrides</p>
        <p className="mt-1 text-[11px] text-slate-700">
          Override per-platform settings at runtime without redeployment.
          Requires config API endpoints (Phase 2).
        </p>
      </div>
    </div>
  )
}

// ─── Main drawer ───────────────────────────────────────────────────────────────

interface PlatformDetailDrawerProps {
  detail: PlatformDetail | null
  onClose: () => void
  onAction?: (platform: string, action: string) => void
}

export function PlatformDetailDrawer({
  detail,
  onClose,
  onAction,
}: PlatformDetailDrawerProps) {
  const [activeTab, setActiveTab] = useState<DrawerTab>('Overview')
  const isOpen = detail !== null

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return
    function onEsc(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [isOpen, onClose])

  // Reset tab when platform changes
  useEffect(() => {
    setActiveTab('Overview')
  }, [detail?.platform])

  return (
    <>
      {/* Backdrop */}
      <div
        className={cn(
          'fixed inset-0 z-30 bg-slate-950/70 backdrop-blur-sm transition-opacity duration-200',
          isOpen ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={onClose}
        aria-hidden
      />

      {/* Drawer panel */}
      <aside
        className={cn(
          'fixed inset-y-0 right-0 z-40 flex w-full max-w-[480px] flex-col',
          'border-l border-slate-800 bg-slate-950 shadow-2xl',
          'transition-transform duration-200 ease-in-out',
          isOpen ? 'translate-x-0' : 'translate-x-full',
        )}
        aria-label="Platform detail"
      >
        {detail && (
          <>
            {/* ── Header ── */}
            <div className="flex flex-shrink-0 items-center justify-between border-b border-slate-800 px-5 py-3.5">
              <div className="flex items-center gap-3">
                <div
                  className={cn(
                    'flex h-8 w-10 items-center justify-center rounded-lg font-mono text-xs font-bold',
                    detail.status === 'critical' ? 'bg-red-950 text-red-300' :
                    detail.status === 'warning'  ? 'bg-amber-950 text-amber-300' :
                    'bg-slate-800 text-slate-300',
                  )}
                >
                  {detail.platform.slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <h3 className="text-sm font-bold capitalize text-slate-100">
                    {detail.platform}
                  </h3>
                  <div className="flex items-center gap-2">
                    <PlatformStatusBadge status={detail.status} />
                    <span className="text-slate-700">·</span>
                    <CircuitStatePill state={detail.circuitState} size="xs" />
                  </div>
                </div>
              </div>

              <button
                onClick={onClose}
                className="flex h-7 w-7 items-center justify-center rounded text-slate-600 transition-colors hover:bg-slate-800 hover:text-slate-300"
                aria-label="Close panel"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-4 w-4">
                  <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* ── Tabs ── */}
            <div className="flex flex-shrink-0 gap-0 border-b border-slate-800">
              {TABS.map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={cn(
                    'px-4 py-2.5 text-xs font-medium transition-colors',
                    activeTab === tab
                      ? 'border-b-2 border-blue-500 text-blue-400'
                      : 'text-slate-500 hover:text-slate-300',
                  )}
                >
                  {tab}
                </button>
              ))}
            </div>

            {/* ── Scrollable content ── */}
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {activeTab === 'Overview' && (
                <div className="flex flex-col gap-4">
                  {/* Description */}
                  {detail.description && (
                    <p className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-xs leading-relaxed text-slate-400">
                      {detail.description}
                    </p>
                  )}

                  {/* Stats grid */}
                  <InfoGrid
                    items={[
                      {
                        label: 'Fail Rate (1h)',
                        value: `${MOCK_FAIL_RATE(detail.platform)}%`,
                      },
                      {
                        label: 'Jobs (1h)',
                        value: MOCK_TOTAL_JOBS(detail.platform).toString(),
                      },
                      {
                        label: 'Last Success',
                        value: MOCK_LAST_SUCCESS(detail.platform),
                      },
                      {
                        label: 'Error Types',
                        value: detail.errorBreakdown.length.toString(),
                      },
                    ]}
                  />

                  {/* Quick actions */}
                  <div>
                    <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
                      Quick Actions
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {(detail.circuitState === 'open' || detail.circuitState === 'half') && (
                        <button
                          onClick={() => onAction?.(detail.platform, 'reset_circuit')}
                          className="rounded-lg border border-emerald-900 bg-emerald-950/40 px-3 py-1.5 text-xs font-medium text-emerald-400 transition-colors hover:bg-emerald-950"
                        >
                          Reset Circuit
                        </button>
                      )}
                      <button
                        onClick={() => onAction?.(detail.platform, 'test_connection')}
                        className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800"
                      >
                        Test Connection
                      </button>
                      <Link
                        to={`/vid-admin/jobs?platform=${detail.platform}`}
                        className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800"
                      >
                        View Jobs →
                      </Link>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'Jobs' && <JobsTab jobs={detail.recentJobs} />}
              {activeTab === 'Errors' && <ErrorsTab errors={detail.errorBreakdown} />}
              {activeTab === 'Phases' && <PhasesTab phases={detail.phaseStats} />}
              {activeTab === 'Config' && <ConfigTab config={detail.config} />}
            </div>
          </>
        )}
      </aside>
    </>
  )
}

// Helpers to derive stats from mock data without a full lookup
function MOCK_FAIL_RATE(platform: string) {
  const map: Record<string, number> = { youtube: 100, instagram: 34, twitter: 23, threads: 18, tiktok: 2, soundcloud: 3, reddit: 1 }
  return map[platform] ?? 0
}
function MOCK_TOTAL_JOBS(platform: string) {
  const map: Record<string, number> = { youtube: 42, instagram: 29, twitter: 17, tiktok: 84, facebook: 31 }
  return map[platform] ?? 10
}
function MOCK_LAST_SUCCESS(platform: string) {
  const map: Record<string, string> = { youtube: '3h ago', instagram: '8m ago', twitter: '22m ago' }
  return map[platform] ?? '< 10m ago'
}
