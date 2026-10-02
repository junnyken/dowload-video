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
        <div key={label} className="rounded-card border border-line bg-surface shadow-card p-3">
          <p className="mb-1 text-[9px] font-semibold uppercase tracking-widest text-fg-muted">
            {label}
          </p>
          <p className="font-mono text-sm font-semibold text-fg-2">{value}</p>
        </div>
      ))}
    </div>
  )
}

function JobResultIcon({ result }: { result: RecentJob['result'] }) {
  if (result === 'success') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 text-success">
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    )
  }
  if (result === 'running') {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 animate-spin text-fg-2">
        <path strokeLinecap="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-3.5 w-3.5 text-danger">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  )
}

function JobsTab({ jobs }: { jobs: RecentJob[] }) {
  if (jobs.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-fg-muted">
        No recent jobs
      </div>
    )
  }
  return (
    <div className="flex flex-col divide-y divide-line">
      {jobs.map(job => (
        <div key={job.id} className="flex items-start gap-3 py-3">
          <div className="mt-0.5 flex-shrink-0">
            <JobResultIcon result={job.result} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate font-mono text-[11px] text-fg-muted" title={job.url}>
              {job.url}
            </p>
            {job.error && (
              <p className="mt-0.5 text-[11px] text-danger">{job.error}</p>
            )}
            <div className="mt-1 flex items-center gap-2 font-mono text-[9px] text-fg-muted">
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
      <div className="py-8 text-center text-sm text-fg-muted">No errors recorded</div>
    )
  }
  const total = errors.reduce((s, e) => s + e.count, 0)
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-[10px] text-fg-muted">
        {total} total errors · last 1h
      </p>
      {errors.map(item => (
        <div key={item.errorType}>
          <div className="mb-1 flex items-center justify-between">
            <span className="font-mono text-[11px] text-fg-2">{item.errorType}</span>
            <span className="font-mono text-[10px] text-fg-muted">
              {item.count} ({item.pct}%)
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface">
            <div
              className="h-full rounded-full bg-danger transition-all"
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
    rate >= 90 ? 'bg-success' :
    rate >= 70 ? 'bg-warning' :
    'bg-danger'
  const textColor =
    rate >= 90 ? 'text-success' :
    rate >= 70 ? 'text-warning' :
    'text-danger'
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface">
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
      <div className="overflow-hidden rounded-xl border border-line">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-line">
              {['Phase', 'Success Rate', 'Avg Duration', 'Jobs'].map(h => (
                <th key={h} className="px-3 py-2 text-left font-mono text-[9px] font-semibold uppercase tracking-widest text-fg-muted first:pl-4">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {phases.map((p, i) => (
              <tr
                key={p.phase}
                className={cn('transition-colors hover:bg-surface-2', i < phases.length - 1 && 'border-b border-line')}
              >
                <td className="py-2 pl-4 pr-3">
                  <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-fg-muted">
                    {p.phase}
                  </span>
                </td>
                <td className="py-2 pr-3 w-28">
                  <SuccessRateBar rate={p.successRate} />
                </td>
                <td className="py-2 pr-3">
                  <span className="font-mono text-[11px] text-fg-muted">
                    {p.avgDurationMs < 1000
                      ? `${p.avgDurationMs}ms`
                      : `${(p.avgDurationMs / 1000).toFixed(1)}s`
                    }
                  </span>
                </td>
                <td className="py-2 pr-3">
                  <span className="font-mono text-[11px] text-fg-muted">{p.totalJobs}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Heatmap placeholder */}
      <div className="rounded-xl border border-dashed border-line p-4 text-center">
        <p className="text-xs font-medium text-fg-muted">Phase × Time Heatmap</p>
        <p className="mt-1 text-[11px] text-fg-muted">
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
      <div className="flex flex-col divide-y divide-line rounded-xl border border-line">
        {rows.map(row => (
          <div key={row.key} className="flex items-start justify-between gap-4 px-4 py-3">
            <span className="flex-shrink-0 text-[11px] font-medium text-fg-muted">{row.label}</span>
            <span className="text-right font-mono text-[11px] text-fg-2">{row.value}</span>
          </div>
        ))}
      </div>

      {/* Config overrides placeholder */}
      <div className="rounded-xl border border-dashed border-line p-4">
        <p className="text-xs font-medium text-fg-muted">Runtime Config Overrides</p>
        <p className="mt-1 text-[11px] text-fg-muted">
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
          'fixed inset-0 z-30 bg-surface-2 backdrop-blur-sm transition-opacity duration-200',
          isOpen ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
        onClick={onClose}
        aria-hidden
      />

      {/* Drawer panel */}
      <aside
        className={cn(
          'fixed inset-y-0 right-0 z-40 flex w-full max-w-[480px] flex-col',
          'border-l border-line bg-canvas shadow-2xl',
          'transition-transform duration-200 ease-in-out',
          isOpen ? 'translate-x-0' : 'translate-x-full',
        )}
        aria-label="Platform detail"
      >
        {detail && (
          <>
            {/* ── Header ── */}
            <div className="flex flex-shrink-0 items-center justify-between border-b border-line px-5 py-3.5">
              <div className="flex items-center gap-3">
                <div
                  className={cn(
                    'flex h-8 w-10 items-center justify-center rounded-lg font-mono text-xs font-bold',
                    detail.status === 'critical' ? 'bg-danger-soft text-danger' :
                    detail.status === 'warning'  ? 'bg-warning-soft text-warning' :
                    'bg-surface text-fg-2',
                  )}
                >
                  {detail.platform.slice(0, 2).toUpperCase()}
                </div>
                <div>
                  <h3 className="text-sm font-bold capitalize text-fg">
                    {detail.platform}
                  </h3>
                  <div className="flex items-center gap-2">
                    <PlatformStatusBadge status={detail.status} />
                    <span className="text-fg-muted">·</span>
                    <CircuitStatePill state={detail.circuitState} size="xs" />
                  </div>
                </div>
              </div>

              <button
                onClick={onClose}
                className="flex h-7 w-7 items-center justify-center rounded text-fg-muted transition-colors hover:bg-surface hover:text-fg-2"
                aria-label="Close panel"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className="h-4 w-4">
                  <path strokeLinecap="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* ── Tabs ── */}
            <div className="flex flex-shrink-0 gap-0 border-b border-line">
              {TABS.map(tab => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={cn(
                    'px-4 py-2.5 text-xs font-medium transition-colors',
                    activeTab === tab
                      ? 'border-b-2 border-line text-fg-2'
                      : 'text-fg-muted hover:text-fg-2',
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
                    <p className="rounded-card border border-line bg-surface shadow-card p-3 text-xs leading-relaxed text-fg-muted">
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
                    <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-fg-muted">
                      Quick Actions
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {(detail.circuitState === 'open' || detail.circuitState === 'half') && (
                        <button
                          onClick={() => onAction?.(detail.platform, 'reset_circuit')}
                          className="rounded-lg border border-success/30 bg-success-soft px-3 py-1.5 text-xs font-medium text-success transition-colors hover:bg-success/20"
                        >
                          Reset Circuit
                        </button>
                      )}
                      <button
                        onClick={() => onAction?.(detail.platform, 'test_connection')}
                        className="rounded-lg border border-line px-3 py-1.5 text-xs font-medium text-fg-2 transition-colors hover:bg-surface"
                      >
                        Test Connection
                      </button>
                      <Link
                        to={`/vid-admin/jobs?platform=${detail.platform}`}
                        className="rounded-lg border border-line px-3 py-1.5 text-xs font-medium text-fg-2 transition-colors hover:bg-surface"
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
