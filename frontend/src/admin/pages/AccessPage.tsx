import { useEffect, useState, useCallback } from 'react'
import { adminFetch } from '../utils/adminFetch'

interface Session {
  token_fragment: string
  ttl_seconds: number
  expires_in_human: string
}

interface SessionsResponse {
  sessions: Session[]
  count: number
}

const MAX_TTL_SECONDS = 86400 // 24h as reference ceiling for progress bar

export default function AccessPage() {
  const [data, setData] = useState<SessionsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [revoking, setRevoking] = useState(false)
  const [revokeMsg, setRevokeMsg] = useState<string | null>(null)

  const fetchSessions = useCallback(async () => {
    try {
      const res = await adminFetch<SessionsResponse>('/access/sessions')
      setData(res)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchSessions()
    const interval = setInterval(fetchSessions, 30_000)
    return () => clearInterval(interval)
  }, [fetchSessions])

  const handleRevokeAll = async () => {
    const confirmed = window.confirm(
      'Revoke ALL active sessions?\n\nThis will log out everyone immediately, including your current session. You will need to log in again.',
    )
    if (!confirmed) return

    setRevoking(true)
    setRevokeMsg(null)
    try {
      const res = await adminFetch<{ revoked: number }>('/access/sessions', { method: 'DELETE' })
      setRevokeMsg(`Revoked ${res.revoked} session${res.revoked !== 1 ? 's' : ''}. All users have been logged out.`)
      setData(null)
      setLoading(true)
      await fetchSessions()
    } catch (err) {
      setRevokeMsg(`Error: ${err instanceof Error ? err.message : 'Revoke failed'}`)
    } finally {
      setRevoking(false)
    }
  }

  const ttlPercent = (ttl: number) => {
    const pct = Math.max(0, Math.min(100, (ttl / MAX_TTL_SECONDS) * 100))
    return pct
  }

  const ttlColor = (pct: number) => {
    if (pct > 50) return 'bg-success'
    if (pct > 20) return 'bg-accent'
    return 'bg-danger'
  }

  return (
    <div className="min-h-screen bg-canvas text-fg p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-fg">Access Control</h1>
          {data !== null && (
            <span className="inline-flex items-center justify-center rounded-full bg-accent text-accent-fg text-xs font-semibold px-2.5 py-0.5 min-w-[1.5rem]">
              {data.count}
            </span>
          )}
        </div>
        <button
          onClick={handleRevokeAll}
          disabled={revoking || loading || (data?.count ?? 0) === 0}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-danger hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed text-danger-fg text-sm font-medium transition-colors"
        >
          {revoking ? (
            <>
              <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-line-strong border-t-transparent animate-spin" />
              Revoking...
            </>
          ) : (
            'Revoke All Sessions'
          )}
        </button>
      </div>

      {/* Revoke feedback */}
      {revokeMsg && (
        <div className={`mb-4 rounded-lg px-4 py-3 text-sm ${revokeMsg.startsWith('Error') ? 'bg-danger-soft border border-danger/30 text-danger' : 'bg-success-soft border border-success/30 text-success'}`}>
          {revokeMsg}
        </div>
      )}

      {/* Warning note */}
      <div className="mb-5 rounded-lg bg-accent-soft border border-accent/50 px-4 py-3 text-accent-text text-sm">
        <span className="font-semibold">Note:</span> Revoking sessions will log out <strong>all users</strong>, including your current session. You will be redirected to the login page.
      </div>

      {/* Content */}
      {loading && !data ? (
        <div className="flex items-center gap-2 text-fg-muted text-sm py-8">
          <span className="inline-block h-4 w-4 rounded-full border-2 border-line-strong border-t-transparent animate-spin" />
          Loading sessions...
        </div>
      ) : error ? (
        <div className="rounded-lg bg-danger-soft border border-danger/30 px-4 py-3 text-danger text-sm">
          {error}
        </div>
      ) : data && data.sessions.length === 0 ? (
        <div className="text-center py-16 text-fg-muted text-sm">
          No active sessions.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-line">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-fg-muted uppercase text-xs tracking-wider">
              <tr>
                <th className="px-4 py-3 text-left">Token</th>
                <th className="px-4 py-3 text-left">Expires</th>
                <th className="px-4 py-3 text-left w-56">TTL</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {data?.sessions.map((s) => {
                const pct = ttlPercent(s.ttl_seconds)
                return (
                  <tr key={s.token_fragment} className="bg-surface-2 hover:bg-surface transition-colors">
                    <td className="px-4 py-3 font-mono text-fg-2">
                      {s.token_fragment}
                    </td>
                    <td className="px-4 py-3 text-fg-muted">
                      {s.expires_in_human}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-surface-2 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${ttlColor(pct)}`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="text-fg-muted text-xs w-10 text-right">
                          {Math.round(pct)}%
                        </span>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Auto-refresh hint */}
      <p className="mt-4 text-xs text-fg-muted">Auto-refreshes every 30 seconds.</p>
    </div>
  )
}
