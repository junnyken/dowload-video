import { useCallback, useEffect, useState } from 'react'
import { intelFetch, intelPost } from '../utils/adminFetch'

/**
 * Ported from the orphaned src/pages/Admin/AnomalyPanel.jsx. The new shell only
 * ever surfaced anomalies as a count on the home page and as alert items; the
 * list itself, and the ability to resolve one, had no home.
 */

interface Anomaly {
  id: string
  state?: string
  metric?: string
  detected_at?: string
  [k: string]: unknown
}

const STATE_ORDER: Record<string, number> = {
  escalated: 0, under_watch: 1, detected: 2, resolved: 3,
}
const STATE_STYLE: Record<string, string> = {
  escalated:   'border-red-700/60 bg-red-950/30 text-red-200',
  under_watch: 'border-amber-700/60 bg-amber-950/30 text-amber-200',
  detected:    'border-sky-700/60 bg-sky-950/30 text-sky-200',
  resolved:    'border-gray-700 bg-gray-900/40 text-gray-400',
}

export default function AnomaliesPage() {
  const [items, setItems] = useState<Anomaly[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [showResolved, setShowResolved] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await intelFetch<{ anomalies: Anomaly[]; count: number }>('/anomalies')
      setItems(r.anomalies || [])
      setErr(null)
    } catch (e) { setErr((e as Error).message) } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [load])

  async function resolve(a: Anomaly) {
    setBusy(p => ({ ...p, [a.id]: true }))
    setMsg(p => ({ ...p, [a.id]: '' }))
    try {
      await intelPost(`/anomalies/${encodeURIComponent(a.id)}/resolve`)
      setMsg(p => ({ ...p, [a.id]: 'Đã đánh dấu xử lý xong.' }))
      await load()
    } catch (e) {
      setMsg(p => ({ ...p, [a.id]: (e as Error).message }))
    } finally {
      setBusy(p => ({ ...p, [a.id]: false }))
    }
  }

  const sorted = [...items].sort(
    (a, b) => (STATE_ORDER[a.state ?? ''] ?? 9) - (STATE_ORDER[b.state ?? ''] ?? 9))
  const shown = showResolved ? sorted : sorted.filter(a => a.state !== 'resolved')
  const activeCount = items.filter(a => a.state !== 'resolved').length

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Anomalies</h1>
          <p className="text-xs text-gray-500">
            {activeCount} đang hoạt động · {items.length} tổng · làm mới 30s
          </p>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-gray-400">
          <input type="checkbox" checked={showResolved}
                 onChange={e => setShowResolved(e.target.checked)} />
          Hiện cả mục đã xử lý
        </label>
      </div>

      {err && <p className="text-xs text-red-400">Lỗi: {err}</p>}
      {loading && <p className="text-xs text-gray-500 animate-pulse">Đang tải…</p>}
      {!loading && shown.length === 0 && (
        <p className="text-xs text-gray-500">
          {items.length === 0 ? 'Không có bất thường nào.' : 'Không còn mục nào đang hoạt động.'}
        </p>
      )}

      <div className="space-y-2">
        {shown.map(a => {
          // The detector's payload varies; show whatever it sent rather than
          // silently dropping fields this page has not been taught about.
          const extra = Object.entries(a).filter(
            ([k]) => !['id', 'state', 'metric', 'detected_at'].includes(k))
          return (
            <div key={a.id}
                 className={`rounded-lg border p-3 ${STATE_STYLE[a.state ?? ''] ?? 'border-gray-800 bg-gray-900/60'}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">{a.metric || a.id}</span>
                    <span className="rounded border border-current/40 px-1.5 py-0.5 text-[10px] opacity-80">
                      {a.state ?? 'unknown'}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[10px] opacity-70 font-mono">
                    {(a.detected_at || '').replace('T', ' ').slice(0, 19) || '—'} · {a.id}
                  </div>
                </div>
                {a.state !== 'resolved' && (
                  <div className="flex items-center gap-2">
                    {msg[a.id] && <span className="text-[10px] text-emerald-400">{msg[a.id]}</span>}
                    <button disabled={!!busy[a.id]} onClick={() => resolve(a)}
                      className="rounded border border-current/40 px-2 py-0.5 text-[10px]
                                 hover:bg-white/5 disabled:opacity-50">
                      {busy[a.id] ? '…' : 'Đánh dấu đã xử lý'}
                    </button>
                  </div>
                )}
              </div>
              {extra.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2 border-t border-current/20 pt-2">
                  {extra.map(([k, v]) => (
                    <span key={k} className="text-[10px] opacity-75">
                      <span className="opacity-60">{k}:</span>{' '}
                      <span className="font-mono">
                        {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                      </span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
