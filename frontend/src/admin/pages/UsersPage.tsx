import { useEffect, useState, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'

// ─── Types ────────────────────────────────────────────────────────────────────

interface TopUser {
  user_id: string
  downloads_today: number
  downloads_total?: number
  downloads_this_month?: number
  tier?: string
  plan?: string
  last_reset_at: string | null
}

interface FlaggedUser {
  user_id: string
  downloads_today: number
  tier?: string
  plan?: string
  last_reset_at: string | null
}

interface BatchDistributionItem {
  range: string
  count: number
}

interface UsersData {
  top_users: TopUser[]
  flagged_users: FlaggedUser[]
  batch_distribution: BatchDistributionItem[]
  total_users: number
  total_batches_48h: number
  abuse_threshold: number
}

interface DailySignup {
  date: string
  total: number
  free: number
  pro: number
  enterprise: number
}

interface SignupsData {
  daily_signups: DailySignup[]
  total_period: number
  today: number
  tier_breakdown: Record<string, number>
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function maskUserId(id: string): string {
  if (id.length <= 8) return id
  return `${id.slice(0, 8)}...`
}

function formatRelative(dateStr: string | null): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr)
  const diff = Math.floor((Date.now() - d.getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function shortDate(iso: string, days: number): string {
  const parts = iso.split('-')
  if (parts.length < 3) return iso
  // For 90d show month/day, for 7d show day only
  return days <= 14
    ? parseInt(parts[2]).toString()
    : `${parseInt(parts[1])}/${parseInt(parts[2])}`
}

// ─── SVG Signup Chart ─────────────────────────────────────────────────────────

function SignupChart({ stats, days }: { stats: DailySignup[]; days: number }) {
  const W = 600
  const H = 160
  const PAD_LEFT = 36
  const PAD_RIGHT = 12
  const PAD_TOP = 12
  const PAD_BOT = 28

  const innerW = W - PAD_LEFT - PAD_RIGHT
  const innerH = H - PAD_TOP - PAD_BOT

  const maxVal = Math.max(...stats.map(s => s.total), 1)

  function xOf(i: number) {
    return PAD_LEFT + (i / Math.max(stats.length - 1, 1)) * innerW
  }
  function yOf(v: number) {
    return PAD_TOP + innerH - (v / maxVal) * innerH
  }

  const points = stats.map((s, i) => `${xOf(i)},${yOf(s.total)}`).join(' ')
  const fillPoints = `${xOf(0)},${PAD_TOP + innerH} ${points} ${xOf(stats.length - 1)},${PAD_TOP + innerH}`

  // Y grid: 3 lines
  const yTicks = [0, 0.5, 1].map(pct => ({
    y: PAD_TOP + innerH - pct * innerH,
    label: Math.round(pct * maxVal).toString(),
  }))

  // X labels: step to avoid crowding
  const step = stats.length > 21 ? Math.ceil(stats.length / 10) : stats.length > 10 ? 2 : 1

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full max-w-full"
        style={{ minWidth: 280, height: 160 }}
        aria-label="Daily signups chart"
      >
        {/* Grid lines */}
        {yTicks.map(({ y, label }) => (
          <g key={y}>
            <line x1={PAD_LEFT} y1={y} x2={W - PAD_RIGHT} y2={y} stroke="var(--vg-surface-2)" strokeWidth={1} />
            <text x={PAD_LEFT - 4} y={y + 3.5} textAnchor="end" fontSize={8} fill="var(--vg-fg-muted)">{label}</text>
          </g>
        ))}

        {/* X baseline */}
        <line x1={PAD_LEFT} y1={PAD_TOP + innerH} x2={W - PAD_RIGHT} y2={PAD_TOP + innerH} stroke="var(--vg-surface-2)" strokeWidth={1} />

        {/* Area fill */}
        <polygon points={fillPoints} fill="var(--vg-success)" opacity={0.08} />

        {/* Line */}
        <polyline
          points={points}
          fill="none"
          stroke="var(--vg-success)"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* Dots (only for <= 30d to avoid clutter) */}
        {stats.length <= 30 && stats.map((s, i) => (
          <circle key={i} cx={xOf(i)} cy={yOf(s.total)} r={2.5} fill="var(--vg-success)" stroke="var(--vg-canvas)" strokeWidth={1.5}>
            <title>{`${s.date}: ${s.total} signups`}</title>
          </circle>
        ))}

        {/* X labels */}
        {stats.map((s, i) => {
          if (i % step !== 0 && i !== stats.length - 1) return null
          return (
            <text key={i} x={xOf(i)} y={H - PAD_BOT + 14} textAnchor="middle" fontSize={7.5} fill="var(--vg-fg-muted)">
              {shortDate(s.date, days)}
            </text>
          )
        })}
      </svg>
    </div>
  )
}

// ─── Tier pill ────────────────────────────────────────────────────────────────

function TierBadge({ tier }: { tier: string }) {
  const classes: Record<string, string> = {
    free:       'border-line bg-surface-2 text-fg-2',
    pro:        'border-line-strong bg-surface text-fg',
    team:       'border-line-strong bg-surface text-fg',
    enterprise: 'border-line-strong bg-surface text-fg',
  }
  const cls = classes[tier.toLowerCase()] ?? 'border-line bg-surface-2 text-fg-2'
  return (
    <span className={`inline-block whitespace-nowrap rounded-md border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider ${cls}`}>
      {tier}
    </span>
  )
}

// ─── Stat card ────────────────────────────────────────────────────────────────

function StatCard({ label, value, accent, hint }: {
  label: string; value: string; accent?: 'amber' | 'green' | 'blue'; hint?: string
}) {
  const valueClass = {
    amber: 'text-warning',
    green: 'text-success',
    blue:  'text-fg-2',
    undefined: 'text-fg',
  }[accent ?? 'undefined']

  return (
    <div className="rounded-card border border-line bg-surface shadow-card px-5 py-4 space-y-1">
      <div className="text-[10px] text-fg-muted uppercase tracking-widest font-mono">{label}</div>
      <div className={`text-2xl font-bold font-mono ${valueClass}`}>{value}</div>
      {hint && <div className="text-[10px] text-fg-muted leading-snug">{hint}</div>}
    </div>
  )
}

// ─── Reset button ─────────────────────────────────────────────────────────────

function ResetButton({
  userId, loading, message, onReset, accent,
}: {
  userId: string; loading: boolean; message?: string; onReset: (id: string) => void; accent?: 'amber'
}) {
  const btnClass = accent === 'amber'
    ? 'border border-warning/30 bg-warning-soft text-warning hover:border-accent disabled:opacity-50'
    : 'border border-line bg-surface text-fg-2 hover:border-line-strong hover:bg-surface-2 disabled:opacity-50'
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => onReset(userId)}
        disabled={loading}
        className={`px-2.5 py-1 rounded-control text-xs font-medium transition-colors cursor-pointer ${btnClass}`}
      >
        {loading ? 'Resetting…' : 'Reset Quota'}
      </button>
      {message && (
        <span className={`text-xs ${message === 'Reset!' ? 'text-success' : 'text-danger'}`}>
          {message}
        </span>
      )}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

interface Account {
  user_id: string
  email: string | null
  display_name: string | null
  tier: string
  billing_status: string | null
  plan_name: string | null
  created_at: string | null
  downloads_today: number
  daily_limit: number
}

interface AccountsData {
  success: boolean
  accounts: Account[]
  total: number
  available_tiers: string[]
  error?: string
}

export default function UsersPage() {
  const [accounts, setAccounts]       = useState<AccountsData | null>(null)
  const [accQuery, setAccQuery]       = useState('')
  const [accBusy, setAccBusy]         = useState<Record<string, boolean>>({})
  const [accMsg, setAccMsg]           = useState<Record<string, string>>({})
  const [data, setData]       = useState<UsersData | null>(null)
  const [signups, setSignups] = useState<SignupsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [signupDays, setSignupDays] = useState<7 | 30 | 90>(30)
  const [error, setError]     = useState<string | null>(null)
  const [resetting, setResetting] = useState<Record<string, boolean>>({})
  const [resetMsg, setResetMsg]   = useState<Record<string, string>>({})

  const fetchData = useCallback(async () => {
    try {
      const result = await adminFetch<UsersData>('/users')
      setData(result)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchAccounts = useCallback(async (q: string) => {
    try {
      const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}&limit=50` : '?limit=50'
      setAccounts(await adminFetch<AccountsData>(`/accounts${qs}`))
    } catch (e) {
      setAccounts({ success: false, accounts: [], total: 0, available_tiers: [],
                    error: (e as Error).message })
    }
  }, [])

  const fetchSignups = useCallback(async (d: number) => {
    try {
      const result = await adminFetch<SignupsData>(`/users/signups?days=${d}`)
      setSignups(result)
    } catch {
      // non-critical — don't block the rest of the page
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 60_000)
    return () => clearInterval(interval)
  }, [fetchData])

  useEffect(() => {
    fetchSignups(signupDays)
  }, [signupDays, fetchSignups])

  useEffect(() => {
    const t = setTimeout(() => fetchAccounts(accQuery), accQuery ? 350 : 0)
    return () => clearTimeout(t)
  }, [accQuery, fetchAccounts])

  async function handleSetTier(userId: string, tier: string) {
    setAccBusy(p => ({ ...p, [userId]: true }))
    setAccMsg(p => ({ ...p, [userId]: '' }))
    try {
      // /user-action answers 200 with {success:false, error} for a refusal, so
      // "didn't throw" is not the same as "the tier changed" — reporting the
      // new tier on the strength of the HTTP status alone is how a failed
      // upgrade still read as "→ pro" on screen.
      const res = await adminPost<{ success: boolean; error?: string }>(
        '/user-action', { action: 'set_tier', user_id: userId, params: { tier } },
      )
      if (!res?.success) throw new Error(res?.error ?? 'Đổi gói không thành công')
      setAccMsg(p => ({ ...p, [userId]: `→ ${tier}` }))
      await fetchAccounts(accQuery)
    } catch (e) {
      setAccMsg(p => ({ ...p, [userId]: (e as Error).message }))
    } finally {
      setAccBusy(p => ({ ...p, [userId]: false }))
    }
  }

  async function handleResetQuota(userId: string) {
    setResetting(prev => ({ ...prev, [userId]: true }))
    setResetMsg(prev => ({ ...prev, [userId]: '' }))
    try {
      await adminPost('/reset-user-quota', { user_id: userId, action: 'reset_quota' })
      setResetMsg(prev => ({ ...prev, [userId]: 'Reset!' }))
      await fetchData()
    } catch (e) {
      setResetMsg(prev => ({ ...prev, [userId]: (e as Error).message }))
    } finally {
      setResetting(prev => ({ ...prev, [userId]: false }))
    }
  }

  const maxBatch = data ? Math.max(...data.batch_distribution.map(b => b.count), 1) : 1

  // Tier breakdown for signups
  const tierBreakdown = signups?.tier_breakdown ?? {}
  const tierTotal = Object.values(tierBreakdown).reduce((s, v) => s + v, 0) || 1

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold tracking-tight text-fg">Users</h1>
        <div className="flex items-center gap-3">
          {loading && <span className="text-xs text-fg-muted animate-pulse">Refreshing…</span>}
          {!loading && <span className="text-xs text-fg-muted">Auto-refresh 60s</span>}
        </div>
      </div>

      {/* ── Registered accounts ─────────────────────────────────────────
          The panels below this one aggregate: /users reports usage keyed by an
          opaque id, /users/signups counts registrations per day. Neither lists
          who actually signed up, so an operator could see "3 new accounts" and
          still have no way to put one of them on Pro. This is that roster. */}
      <div className="rounded-card border border-line bg-surface shadow-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <div>
            <h2 className="text-sm font-semibold text-fg">Tài khoản đăng ký</h2>
            <p className="text-[11px] text-fg-muted">
              {accounts ? `${accounts.total} tài khoản` : 'Đang tải…'} · mới nhất trước
            </p>
          </div>
          <input
            value={accQuery}
            onChange={e => setAccQuery(e.target.value)}
            placeholder="Tìm theo email…"
            className="w-64 rounded-control border border-line bg-surface px-3 py-1.5
                       text-xs text-fg placeholder:text-fg-muted focus:border-line
                       focus:outline-none"
          />
        </div>

        {accounts && !accounts.success && (
          <p className="text-xs text-danger">Lỗi: {accounts.error}</p>
        )}

        {accounts?.success && accounts.accounts.length === 0 && (
          <p className="text-xs text-fg-muted py-3">
            {accQuery ? 'Không có tài khoản nào khớp.' : 'Chưa có tài khoản nào.'}
          </p>
        )}

        {accounts?.success && accounts.accounts.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-fg-muted border-b border-line">
                  <th className="py-2 pr-4 font-medium">Email</th>
                  <th className="py-2 pr-4 font-medium">Gói</th>
                  <th className="py-2 pr-4 font-medium">Hôm nay</th>
                  <th className="py-2 pr-4 font-medium">Đăng ký</th>
                  <th className="py-2 font-medium">Đổi gói</th>
                </tr>
              </thead>
              <tbody>
                {accounts.accounts.map(a => (
                  <tr key={a.user_id} className="border-b border-line">
                    <td className="py-2 pr-4">
                      <span className="text-fg">{a.email ?? '—'}</span>
                      {a.display_name && (
                        <span className="text-fg-muted ml-2">({a.display_name})</span>
                      )}
                    </td>
                    <td className="py-2 pr-4"><TierBadge tier={a.tier} /></td>
                    <td className="py-2 pr-4 font-mono text-fg-2">
                      {a.downloads_today}
                      <span className="text-fg-muted">
                        /{a.daily_limit === -1 ? '∞' : a.daily_limit}
                      </span>
                    </td>
                    <td className="py-2 pr-4 text-fg-muted">
                      {a.created_at ? a.created_at.slice(0, 10) : '—'}
                    </td>
                    <td className="py-2">
                      <div className="flex flex-wrap items-center gap-1">
                        {(accounts.available_tiers ?? []).map(t => (
                          <button
                            key={t}
                            disabled={accBusy[a.user_id] || t === a.tier}
                            onClick={() => handleSetTier(a.user_id, t)}
                            title={t === 'enterprise' ? 'Không giới hạn lượt tải' : undefined}
                            className={`rounded px-2 py-0.5 border text-[10px] transition
                              ${t === a.tier
                                ? 'border-line text-fg-muted cursor-default'
                                : 'border-line-strong text-fg-2 hover:bg-surface'}`}
                          >
                            {t}
                          </button>
                        ))}
                        {accMsg[a.user_id] && (
                          // Failures land in the same slot as "→ pro"; painting
                          // them green too would hide the one thing worth
                          // noticing.
                          <span className={`text-[10px] ml-1 ${
                            accMsg[a.user_id].startsWith('→')
                              ? 'text-success'
                              : 'text-danger'
                          }`}>
                            {accMsg[a.user_id]}
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-control bg-danger-soft border border-danger/30 px-4 py-3 text-danger text-sm">
          {error}
        </div>
      )}

      {/* Summary stats */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {/* "Total Users" read data.total_users, which is a count of rows in
              user_usage — and that table is keyed by account id OR by client
              IP for anonymous downloads. It showed 6 against 4 real accounts,
              the extra two being IP addresses. Two different numbers, so show
              both and say which is which. */}
          <StatCard
            label="Tài khoản"
            value={(accounts?.total ?? 0).toLocaleString()}
            hint={`${data.total_users.toLocaleString()} lượt dùng (gồm khách vãng lai theo IP)`}
          />
          <StatCard label="Signups Today"  value={(signups?.today ?? 0).toLocaleString()} accent="green" />
          <StatCard label="Flagged Today"  value={data.flagged_users.length.toLocaleString()} accent="amber" />
          <StatCard label="Batches (48h)"  value={data.total_batches_48h.toLocaleString()} accent="blue" />
        </div>
      )}

      {/* ── Signup trend chart ─────────────────────────────────────────── */}
      <section className="rounded-card border border-line bg-surface shadow-card p-5 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h2 className="text-sm font-semibold text-fg">New Signups</h2>
            <p className="text-[11px] text-fg-muted mt-0.5">
              {signups
                ? `${signups.total_period.toLocaleString()} total in last ${signupDays} days`
                : 'Loading…'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => fetchSignups(signupDays)} className="text-[10px] text-fg-muted hover:text-fg-2 mr-1">
              ↺
            </button>
            <div className="flex rounded-control overflow-hidden border border-line">
              {([7, 30, 90] as const).map(d => (
                <button
                  key={d}
                  onClick={() => setSignupDays(d)}
                  className={[
                    'px-3 py-1 font-mono text-xs transition-colors',
                    signupDays === d
                      ? 'bg-surface-2 text-fg'
                      : 'bg-surface text-fg-muted hover:text-fg',
                  ].join(' ')}
                >
                  {d}d
                </button>
              ))}
            </div>
          </div>
        </div>

        {signups && signups.daily_signups.length > 0 ? (
          <SignupChart stats={signups.daily_signups} days={signupDays} />
        ) : (
          <div className="h-40 animate-pulse rounded-card bg-surface-2" />
        )}

        {/* Tier breakdown bar */}
        {signups && Object.keys(tierBreakdown).length > 0 && (
          <div className="pt-2 border-t border-line">
            <p className="text-[10px] text-fg-muted uppercase tracking-widest font-mono mb-3">
              Tier Breakdown — last {signupDays}d
            </p>
            <div className="space-y-2">
              {(['free', 'pro', 'team', 'enterprise'] as const).map(tier => {
                const count = tierBreakdown[tier] ?? 0
                if (count === 0) return null
                const pct = (count / tierTotal) * 100
                const barColor = {
                  free:       'bg-line-strong',
                  pro:        'bg-accent',
                  team:       'bg-fg-2',
                  enterprise: 'bg-fg',
                }[tier]
                return (
                  <div key={tier} className="flex items-center gap-3 text-xs">
                    <span className="w-20 text-right text-fg-muted font-mono capitalize">{tier}</span>
                    <div className="flex-1 bg-surface rounded-full h-2 overflow-hidden">
                      <div className={`h-2 rounded-full ${barColor} transition-all duration-500`} style={{ width: `${pct}%` }} />
                    </div>
                    <span className="w-20 text-right font-mono text-fg-2">
                      {count.toLocaleString()} <span className="text-fg-muted">({pct.toFixed(1)}%)</span>
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </section>

      {/* ── Flagged users ──────────────────────────────────────────────── */}
      {data && data.flagged_users.length > 0 && (
        <section className="rounded-card border border-warning/30 bg-warning-soft p-5 space-y-3">
          <h2 className="text-base font-semibold text-warning flex items-center gap-2">
            <span className="inline-block w-2 h-2 rounded-full bg-accent" />
            Flagged Users
            <span className="ml-1 text-xs text-warning font-normal">
              (&gt;{data.abuse_threshold} downloads today)
            </span>
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-warning border-b border-warning/30">
                  <th className="pb-2 pr-4 font-medium">User ID</th>
                  <th className="pb-2 pr-4 font-medium">Downloads Today</th>
                  <th className="pb-2 pr-4 font-medium">Tier</th>
                  <th className="pb-2 font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {data.flagged_users.map(u => (
                  <tr key={u.user_id} className="border-b border-warning/30 hover:bg-warning-soft transition-colors">
                    <td className="py-2 pr-4 font-mono text-warning">{maskUserId(u.user_id)}</td>
                    <td className="py-2 pr-4 text-warning font-semibold">{u.downloads_today}</td>
                    <td className="py-2 pr-4"><TierBadge tier={u.tier ?? u.plan ?? 'free'} /></td>
                    <td className="py-2">
                      <ResetButton
                        userId={u.user_id}
                        loading={!!resetting[u.user_id]}
                        message={resetMsg[u.user_id]}
                        onReset={handleResetQuota}
                        accent="amber"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Top users ──────────────────────────────────────────────────── */}
      {data && (
        <section className="rounded-card border border-line bg-surface shadow-card p-5 space-y-3">
          <h2 className="text-base font-semibold text-fg-2">Top Users</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-fg-muted border-b border-line">
                  <th className="pb-2 pr-4 font-medium">User ID</th>
                  <th className="pb-2 pr-4 font-medium">Today</th>
                  <th className="pb-2 pr-4 font-medium">Total</th>
                  <th className="pb-2 pr-4 font-medium">Tier</th>
                  <th className="pb-2 pr-4 font-medium">Last Active</th>
                  <th className="pb-2 font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {data.top_users.map(u => (
                  <tr key={u.user_id} className="border-b border-line hover:bg-surface transition-colors">
                    <td className="py-2 pr-4 font-mono text-fg-2">{maskUserId(u.user_id)}</td>
                    <td className="py-2 pr-4 text-fg font-semibold">{u.downloads_today}</td>
                    <td className="py-2 pr-4 text-fg-2">{(u.downloads_total ?? u.downloads_this_month ?? 0).toLocaleString()}</td>
                    <td className="py-2 pr-4"><TierBadge tier={u.tier ?? u.plan ?? 'free'} /></td>
                    <td className="py-2 pr-4 text-fg-muted text-xs">{formatRelative(u.last_reset_at)}</td>
                    <td className="py-2">
                      <ResetButton
                        userId={u.user_id}
                        loading={!!resetting[u.user_id]}
                        message={resetMsg[u.user_id]}
                        onReset={handleResetQuota}
                      />
                    </td>
                  </tr>
                ))}
                {data.top_users.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-fg-muted text-sm">No users yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* ── Batch distribution ─────────────────────────────────────────── */}
      {data && data.batch_distribution.length > 0 && (
        <section className="rounded-card border border-line bg-surface shadow-card p-5 space-y-3">
          <h2 className="text-base font-semibold text-fg-2">Batch Distribution (48h)</h2>
          <div className="space-y-2">
            {data.batch_distribution.map(b => {
              const pct = maxBatch > 0 ? (b.count / maxBatch) * 100 : 0
              return (
                <div key={b.range} className="flex items-center gap-3 text-sm">
                  <span className="w-24 shrink-0 text-right text-fg-muted text-xs font-mono">{b.range}</span>
                  <div className="flex-1 bg-surface rounded-full h-4 overflow-hidden">
                    <div className="h-4 rounded-full bg-accent transition-all duration-500" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="w-10 shrink-0 text-right text-fg-2 text-xs font-semibold">{b.count}</span>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {!data && !loading && !error && (
        <div className="text-center text-fg-muted py-16">No data available.</div>
      )}
    </div>
  )
}
