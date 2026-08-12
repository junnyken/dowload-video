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
    if (pct > 50) return 'bg-emerald-500'
    if (pct > 20) return 'bg-yellow-400'
    return 'bg-red-500'
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-white">Access Control</h1>
          {data !== null && (
            <span className="inline-flex items-center justify-center rounded-full bg-indigo-600 text-white text-xs font-semibold px-2.5 py-0.5 min-w-[1.5rem]">
              {data.count}
            </span>
          )}
        </div>
        <button
          onClick={handleRevokeAll}
          disabled={revoking || loading || (data?.count ?? 0) === 0}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600 hover:bg-red-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium transition-colors"
        >
          {revoking ? (
            <>
              <span className="inline-block h-3.5 w-3.5 rounded-full border-2 border-white border-t-transparent animate-spin" />
              Revoking...
            </>
          ) : (
            'Revoke All Sessions'
          )}
        </button>
      </div>

      {/* Revoke feedback */}
      {revokeMsg && (
        <div className={`mb-4 rounded-lg px-4 py-3 text-sm ${revokeMsg.startsWith('Error') ? 'bg-red-900/50 border border-red-700 text-red-300' : 'bg-emerald-900/50 border border-emerald-700 text-emerald-300'}`}>
          {revokeMsg}
        </div>
      )}

      {/* Warning note */}
      <div className="mb-5 rounded-lg bg-amber-900/30 border border-amber-700/50 px-4 py-3 text-amber-300 text-sm">
        <span className="font-semibold">Note:</span> Revoking sessions will log out <strong>all users</strong>, including your current session. You will be redirected to the login page.
      </div>

      {/* Content */}
      {loading && !data ? (
        <div className="flex items-center gap-2 text-gray-400 text-sm py-8">
          <span className="inline-block h-4 w-4 rounded-full border-2 border-gray-400 border-t-transparent animate-spin" />
          Loading sessions...
        </div>
      ) : error ? (
        <div className="rounded-lg bg-red-900/40 border border-red-700 px-4 py-3 text-red-300 text-sm">
          {error}
        </div>
      ) : data && data.sessions.length === 0 ? (
        <div className="text-center py-16 text-gray-500 text-sm">
          No active sessions.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-800">
          <table className="w-full text-sm">
            <thead className="bg-gray-900 text-gray-400 uppercase text-xs tracking-wider">
              <tr>
                <th className="px-4 py-3 text-left">Token</th>
                <th className="px-4 py-3 text-left">Expires</th>
                <th className="px-4 py-3 text-left w-56">TTL</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {data?.sessions.map((s) => {
                const pct = ttlPercent(s.ttl_seconds)
                return (
                  <tr key={s.token_fragment} className="bg-gray-900/50 hover:bg-gray-800/60 transition-colors">
                    <td className="px-4 py-3 font-mono text-gray-300">
                      {s.token_fragment}
                    </td>
                    <td className="px-4 py-3 text-gray-400">
                      {s.expires_in_human}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-gray-700 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${ttlColor(pct)}`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="text-gray-500 text-xs w-10 text-right">
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
      <p className="mt-4 text-xs text-gray-600">Auto-refreshes every 30 seconds.</p>
    </div>
  )
}
