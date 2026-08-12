import { useEffect, useState, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'

interface Plan {
  code: string
  name: string
  price_monthly_cents: number | null
  limits: Record<string, unknown>
  features: string[]
  sort_order: number
}

interface PaymentEvent {
  event_type: string
  processed: boolean
  created_at: string
}

interface CreditGrant {
  user_id: string
  amount: number
  reason: string
  granted_by: string
  created_at: string
}

interface OverviewResponse {
  plan_counts: Record<string, number>
  total_users: number
  mrr_usd: number
  paying_users: number
  plans: Plan[]
  recent_payment_events: PaymentEvent[]
  recent_credit_grants: CreditGrant[]
}

interface DailyRevenue {
  date: string
  [event_type: string]: string | number
}

interface RevenueResponse {
  daily: DailyRevenue[]
  metric_totals: Record<string, number>
  plan_activity: Record<string, number>
  total_events: number
  days: number
}

const PLAN_COLORS: Record<string, string> = {
  free: 'bg-slate-700',
  pro: 'bg-blue-700',
  team: 'bg-indigo-700',
  api: 'bg-teal-700',
  enterprise: 'bg-purple-700',
}

function formatRelative(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function formatCents(cents: number | null): string {
  if (cents === null) return 'Custom'
  if (cents === 0) return 'Free'
  return `$${(cents / 100).toFixed(2)}/mo`
}

interface SetupStatus {
  ready: boolean
  missing_tables: string[]
  migration_file: string
  sql_hint: string
}

export default function BillingAdminPage() {
  const [overview, setOverview] = useState<OverviewResponse | null>(null)
  const [revenue, setRevenue] = useState<RevenueResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(30)
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null)
  const [showSql, setShowSql] = useState(false)

  // Credit grant form
  const [grantForm, setGrantForm] = useState({ user_id: '', amount: '', reason: 'admin_comp' })
  const [granting, setGranting] = useState(false)
  const [grantMsg, setGrantMsg] = useState('')

  const fetchAll = useCallback(async () => {
    setLoading(true)
    try {
      const [ov, rv, setup] = await Promise.all([
        adminFetch<OverviewResponse>('/enterprise/billing/overview'),
        adminFetch<RevenueResponse>(`/enterprise/billing/revenue?days=${days}`),
        adminFetch<SetupStatus>('/enterprise/billing/setup-status').catch(() => null),
      ])
      setOverview(ov)
      setRevenue(rv)
      setSetupStatus(setup)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [days])

  useEffect(() => { fetchAll() }, [fetchAll])

  async function handleGrantCredits(e: React.FormEvent) {
    e.preventDefault()
    if (!grantForm.user_id || !grantForm.amount) return
    setGranting(true)
    setGrantMsg('')
    try {
      await adminPost('/enterprise/billing/credits/grant', {
        user_id: grantForm.user_id.trim(),
        amount: parseInt(grantForm.amount),
        reason: grantForm.reason,
      })
      setGrantMsg(`✓ Granted ${grantForm.amount} credits to ${grantForm.user_id.slice(0, 12)}…`)
      setGrantForm(f => ({ ...f, user_id: '', amount: '' }))
      fetchAll()
    } catch (err) {
      setGrantMsg(`✗ ${(err as Error).message}`)
    } finally {
      setGranting(false)
    }
  }

  // Build bar chart data from revenue daily
  const dailyDownloads = (revenue?.daily ?? []).map(d => d['download'] as number || 0)
  const maxDownload = Math.max(...dailyDownloads, 1)

  const totalUsers = overview?.total_users ?? 0
  const planCountEntries = Object.entries(overview?.plan_counts ?? {}).sort((a, b) => b[1] - a[1])

  return (
    <div className="space-y-6">
      {/* P5: Migration required banner */}
      {setupStatus && !setupStatus.ready && (
        <div className="rounded-xl border border-amber-700/60 bg-amber-900/15 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-amber-400 text-sm font-semibold">⚠ Billing tables chưa được tạo</span>
          </div>
          <p className="text-xs text-amber-300/80">
            Các bảng sau chưa tồn tại trong Supabase:{' '}
            <span className="font-mono">{setupStatus.missing_tables.join(', ')}</span>.
            Hãy chạy migration SQL trong Supabase SQL Editor.
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSql(s => !s)}
              className="rounded-lg border border-amber-700 px-3 py-1.5 text-xs text-amber-300 hover:bg-amber-900/30 transition-colors"
            >
              {showSql ? 'Ẩn SQL' : 'Xem migration SQL'}
            </button>
            <span className="text-[10px] text-amber-600 font-mono">{setupStatus.migration_file}</span>
          </div>
          {showSql && setupStatus.sql_hint && (
            <pre className="rounded-lg bg-slate-950 border border-slate-800 p-3 text-[10px] font-mono text-slate-300 overflow-x-auto max-h-64 whitespace-pre-wrap">
              {setupStatus.sql_hint}
            </pre>
          )}
        </div>
      )}
      {setupStatus?.ready && (
        <div className="flex items-center gap-2 text-xs text-emerald-400">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          Billing tables sẵn sàng
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-mono text-lg font-bold text-slate-100">Billing Overview</h1>
          <p className="mt-0.5 text-xs text-slate-500">MRR, plans, usage events, credit grants</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={days}
            onChange={e => setDays(Number(e.target.value))}
            className="rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-xs text-slate-300"
          >
            <option value={7}>7d</option>
            <option value={30}>30d</option>
            <option value={90}>90d</option>
          </select>
          <button onClick={fetchAll} className="rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600">
            ↺ Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {/* Top KPI cards */}
      {overview && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'MRR', value: `$${overview.mrr_usd.toFixed(2)}` },
            { label: 'Total Users', value: overview.total_users.toLocaleString() },
            { label: 'Paying Users', value: overview.paying_users.toLocaleString() },
            { label: 'Conversion', value: totalUsers > 0 ? `${((overview.paying_users / totalUsers) * 100).toFixed(1)}%` : '—' },
          ].map(c => (
            <div key={c.label} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <p className="font-mono text-xs text-slate-500">{c.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-slate-100">{c.value}</p>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Plan distribution */}
        {overview && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-4 font-mono text-xs font-semibold uppercase text-slate-500">Plan Distribution</h2>
            <div className="space-y-2.5">
              {planCountEntries.map(([plan, count]) => {
                const pct = totalUsers > 0 ? (count / totalUsers) * 100 : 0
                return (
                  <div key={plan}>
                    <div className="flex justify-between font-mono text-xs mb-0.5">
                      <span className="capitalize text-slate-300">{plan}</span>
                      <span className="text-slate-500">{count.toLocaleString()} ({pct.toFixed(1)}%)</span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-slate-800">
                      <div
                        className={cn('h-1.5 rounded-full', PLAN_COLORS[plan] ?? 'bg-slate-600')}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Daily download activity */}
        {revenue && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-4 font-mono text-xs font-semibold uppercase text-slate-500">
              Download Events — {days}d
            </h2>
            {revenue.daily.length === 0 ? (
              <p className="font-mono text-xs text-slate-600">No events in range</p>
            ) : (
              <>
                <div className="flex h-20 items-end gap-0.5">
                  {revenue.daily.map(d => {
                    const v = (d['download'] as number) || 0
                    const h = maxDownload > 0 ? Math.max(2, (v / maxDownload) * 80) : 2
                    return (
                      <div
                        key={d.date}
                        title={`${d.date}: ${v} downloads`}
                        className="flex-1 cursor-default rounded-sm bg-emerald-600/70 transition-colors hover:bg-emerald-500"
                        style={{ height: `${h}px` }}
                      />
                    )
                  })}
                </div>
                <div className="mt-2 flex justify-between font-mono text-[10px] text-slate-700">
                  <span>{revenue.daily[0]?.date ?? ''}</span>
                  <span>{revenue.daily[revenue.daily.length - 1]?.date ?? ''}</span>
                </div>
                {/* Metric totals */}
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-slate-500">
                  {Object.entries(revenue.metric_totals).map(([m, v]) => (
                    <span key={m}>{m}: <span className="text-slate-300">{v.toLocaleString()}</span></span>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* Plans table */}
        {overview?.plans && overview.plans.length > 0 && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
            <h2 className="mb-3 font-mono text-xs font-semibold uppercase text-slate-500">Plan Catalog</h2>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-slate-500 font-mono">
                  <th className="pb-2 text-left">Plan</th>
                  <th className="pb-2 text-left">Price</th>
                  <th className="pb-2 text-left">Users</th>
                  <th className="pb-2 text-left">Downloads/day</th>
                </tr>
              </thead>
              <tbody>
                {overview.plans.map(p => (
                  <tr key={p.code} className="border-t border-slate-800">
                    <td className="py-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className={cn('h-2 w-2 rounded-full', PLAN_COLORS[p.code] ?? 'bg-slate-600')} />
                        <span className="text-slate-200 font-medium">{p.name}</span>
                      </div>
                    </td>
                    <td className="py-1.5 font-mono text-slate-400">{formatCents(p.price_monthly_cents)}</td>
                    <td className="py-1.5 font-mono text-slate-400">
                      {(overview.plan_counts[p.code] ?? 0).toLocaleString()}
                    </td>
                    <td className="py-1.5 font-mono text-slate-500">
                      {(p.limits as Record<string, unknown>)?.['downloads_per_day'] === -1
                        ? '∞'
                        : String((p.limits as Record<string, unknown>)?.['downloads_per_day'] ?? '—')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Credit grant form + recent grants */}
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-4">
          <h2 className="font-mono text-xs font-semibold uppercase text-slate-500">Grant Credits</h2>
          <form onSubmit={handleGrantCredits} className="space-y-2">
            <input
              type="text"
              placeholder="User ID (auth UID)"
              value={grantForm.user_id}
              onChange={e => setGrantForm(f => ({ ...f, user_id: e.target.value }))}
              className="w-full rounded border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 placeholder-slate-600"
            />
            <div className="flex gap-2">
              <input
                type="number"
                placeholder="Amount"
                min={1}
                value={grantForm.amount}
                onChange={e => setGrantForm(f => ({ ...f, amount: e.target.value }))}
                className="flex-1 rounded border border-slate-700 bg-slate-800 px-3 py-1.5 text-xs text-slate-300 placeholder-slate-600"
              />
              <select
                value={grantForm.reason}
                onChange={e => setGrantForm(f => ({ ...f, reason: e.target.value }))}
                className="rounded border border-slate-700 bg-slate-800 px-2 py-1.5 text-xs text-slate-300"
              >
                <option value="admin_comp">Admin comp</option>
                <option value="refund">Refund</option>
                <option value="promo">Promo</option>
                <option value="welcome_bonus">Welcome bonus</option>
                <option value="referral">Referral</option>
              </select>
            </div>
            <button
              type="submit"
              disabled={granting || !grantForm.user_id || !grantForm.amount}
              className="rounded bg-blue-700 px-4 py-1.5 text-xs text-white hover:bg-blue-600 disabled:opacity-40"
            >
              {granting ? 'Granting…' : 'Grant Credits'}
            </button>
            {grantMsg && (
              <p className={cn('font-mono text-xs', grantMsg.startsWith('✓') ? 'text-emerald-400' : 'text-red-400')}>
                {grantMsg}
              </p>
            )}
          </form>

          {/* Recent grants */}
          {(overview?.recent_credit_grants ?? []).length > 0 && (
            <>
              <h3 className="font-mono text-[10px] font-semibold uppercase text-slate-600 pt-1">Recent Grants</h3>
              <div className="space-y-1.5">
                {(overview?.recent_credit_grants ?? []).slice(0, 5).map((g, i) => (
                  <div key={i} className="flex items-center justify-between rounded bg-slate-800 px-2.5 py-1.5">
                    <div>
                      <span className="font-mono text-[10px] text-slate-400">{g.user_id.slice(0, 12)}…</span>
                      <span className="ml-2 font-mono text-[10px] text-slate-600">{g.reason}</span>
                    </div>
                    <div className="text-right">
                      <span className="font-mono text-xs font-bold text-emerald-400">+{g.amount}</span>
                      <span className="ml-2 font-mono text-[10px] text-slate-600">{formatRelative(g.created_at)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Recent payment events */}
      {(overview?.recent_payment_events ?? []).length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <h2 className="mb-3 font-mono text-xs font-semibold uppercase text-slate-500">Recent Payment Events</h2>
          <div className="space-y-1">
            {(overview?.recent_payment_events ?? []).map((ev, i) => (
              <div key={i} className="flex items-center justify-between rounded px-2.5 py-1.5 hover:bg-slate-800">
                <span className="font-mono text-xs text-slate-400">{ev.event_type}</span>
                <div className="flex items-center gap-3">
                  <span className={cn('font-mono text-[10px]', ev.processed ? 'text-emerald-400' : 'text-amber-400')}>
                    {ev.processed ? '✓ processed' : '○ pending'}
                  </span>
                  <span className="font-mono text-[10px] text-slate-600">{formatRelative(ev.created_at)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
