import { useState, useEffect, useCallback } from 'react'
import { adminFetch } from '../utils/adminFetch'

interface AuditEntry {
  id?: string
  action: string
  actor_email?: string
  resource_type?: string
  resource_id?: string
  metadata?: Record<string, unknown>
  ip_address?: string
  created_at?: string
}

interface AuditLogResponse {
  success: boolean
  count: number
  entries: AuditEntry[]
}

function ActionBadge({ action }: { action: string }) {
  const color =
    action.includes('denied') || action.includes('cancel') ? 'text-red-400 border-red-900 bg-red-950/40' :
    action.includes('delete') || action.includes('revoke') ? 'text-amber-400 border-amber-900 bg-amber-950/40' :
    action.includes('login')   ? 'text-blue-400 border-blue-900 bg-blue-950/30' :
    'text-slate-300 border-slate-700 bg-slate-800/40'
  return (
    <span className={`inline-block rounded-md border px-1.5 py-0.5 font-mono text-[9px] ${color}`}>
      {action}
    </span>
  )
}

function fmtTime(iso?: string) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('vi-VN', { hour12: false })
  } catch {
    return iso
  }
}

export function AuditLogPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [limit, setLimit] = useState(50)
  const [filter, setFilter] = useState('')

  const fetch_ = useCallback(async (n: number) => {
    setLoading(true)
    try {
      const res = await adminFetch<AuditLogResponse>(`/audit-log?limit=${n}`)
      setEntries(res.entries ?? [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load audit log')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetch_(limit) }, [fetch_, limit])

  const visible = filter
    ? entries.filter(e =>
        e.action.includes(filter) ||
        (e.actor_email ?? '').includes(filter) ||
        (e.resource_id ?? '').includes(filter) ||
        (e.ip_address ?? '').includes(filter)
      )
    : entries

  return (
    <div className="flex flex-col gap-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm font-semibold text-slate-100">Audit Log</h2>
          <p className="mt-0.5 text-xs text-slate-500">{visible.length} entries shown</p>
        </div>
        <button onClick={() => fetch_(limit)} className="text-[10px] text-slate-500 hover:text-slate-300">↺ Refresh</button>
      </div>

      {/* Filter + limit */}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by action, email, IP…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder-slate-600 outline-none focus:border-slate-500"
        />
        <select
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          className="rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-300 outline-none"
        >
          {[50, 100, 200].map(n => <option key={n} value={n}>{n} entries</option>)}
        </select>
      </div>

      {loading ? (
        <div className="py-16 text-center text-sm text-slate-500 animate-pulse">Loading…</div>
      ) : error ? (
        <div className="py-16 text-center text-sm text-red-400">{error}</div>
      ) : visible.length === 0 ? (
        <div className="py-16 text-center text-sm text-slate-600">No audit entries found.</div>
      ) : (
        <div className="overflow-hidden rounded-2xl border border-slate-800">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-900/80">
                <th className="py-2 pl-4 text-left font-mono text-[9px] uppercase tracking-widest text-slate-600">Action</th>
                <th className="py-2 text-left font-mono text-[9px] uppercase tracking-widest text-slate-600">Actor</th>
                <th className="py-2 text-left font-mono text-[9px] uppercase tracking-widest text-slate-600 hidden sm:table-cell">Resource</th>
                <th className="py-2 text-left font-mono text-[9px] uppercase tracking-widest text-slate-600 hidden md:table-cell">IP</th>
                <th className="py-2 pr-4 text-right font-mono text-[9px] uppercase tracking-widest text-slate-600">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40">
              {visible.map((e, i) => (
                <tr key={e.id ?? i} className="group hover:bg-slate-800/20">
                  <td className="py-2.5 pl-4">
                    <ActionBadge action={e.action} />
                  </td>
                  <td className="py-2.5 font-mono text-[10px] text-slate-400">
                    {e.actor_email ?? '—'}
                  </td>
                  <td className="py-2.5 hidden sm:table-cell text-slate-500">
                    {e.resource_type && <span className="font-mono text-[9px] text-slate-600">{e.resource_type}/</span>}
                    {e.resource_id ?? '—'}
                  </td>
                  <td className="py-2.5 hidden md:table-cell font-mono text-[10px] text-slate-600">
                    {e.ip_address ?? '—'}
                  </td>
                  <td className="py-2.5 pr-4 text-right font-mono text-[10px] text-slate-600">
                    {fmtTime(e.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
