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
    action.includes('denied') || action.includes('cancel') ? 'text-danger border-danger/30 bg-danger-soft' :
    action.includes('delete') || action.includes('revoke') ? 'text-warning border-warning/30 bg-warning-soft' :
    action.includes('login')   ? 'text-fg-2 border-line bg-surface-2' :
    'text-fg-2 border-line bg-surface-2'
  return (
    <span className={`inline-block rounded-md border px-1.5 py-0.5 font-mono text-[10px] ${color}`}>
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Audit Log</h1>
          <p className="mt-0.5 text-xs text-fg-muted">{visible.length} entries shown</p>
        </div>
        <button onClick={() => fetch_(limit)} className="px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2">↺ Refresh</button>
      </div>

      {/* Filter + limit */}
      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="Filter by action, email, IP…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="flex-1 rounded-card border border-line bg-surface shadow-card px-3 py-2 text-sm text-fg-2 placeholder:text-fg-muted outline-none focus:border-line-strong"
        />
        <select
          value={limit}
          onChange={e => setLimit(Number(e.target.value))}
          className="rounded-card border border-line bg-surface shadow-card px-3 py-2 text-sm text-fg-2 outline-none"
        >
          {[50, 100, 200].map(n => <option key={n} value={n}>{n} entries</option>)}
        </select>
      </div>

      {loading ? (
        <div className="py-16 text-center text-sm text-fg-muted animate-pulse">Loading…</div>
      ) : error ? (
        <div className="py-16 text-center text-sm text-danger">{error}</div>
      ) : visible.length === 0 ? (
        <div className="py-16 text-center text-sm text-fg-muted">No audit entries found.</div>
      ) : (
        <div className="overflow-x-auto rounded-card border border-line">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-line bg-surface-2">
                <th className="py-2 pl-4 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted">Action</th>
                <th className="py-2 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted">Actor</th>
                <th className="py-2 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted hidden sm:table-cell">Resource</th>
                <th className="py-2 text-left font-mono text-[10px] uppercase tracking-widest text-fg-muted hidden md:table-cell">IP</th>
                <th className="py-2 pr-4 text-right font-mono text-[10px] uppercase tracking-widest text-fg-muted">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {visible.map((e, i) => (
                <tr key={e.id ?? i} className="group hover:bg-surface-2">
                  <td className="py-2.5 pl-4">
                    <ActionBadge action={e.action} />
                  </td>
                  <td className="py-2.5 font-mono text-[10px] text-fg-muted">
                    {e.actor_email ?? '—'}
                  </td>
                  <td className="py-2.5 hidden sm:table-cell text-fg-muted">
                    {e.resource_type && <span className="font-mono text-[10px] text-fg-muted">{e.resource_type}/</span>}
                    {e.resource_id ?? '—'}
                  </td>
                  <td className="py-2.5 hidden md:table-cell font-mono text-[10px] text-fg-muted">
                    {e.ip_address ?? '—'}
                  </td>
                  <td className="py-2.5 pr-4 text-right font-mono text-[10px] text-fg-muted">
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
