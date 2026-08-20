import { useCallback, useEffect, useState } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'

/**
 * Ported from the orphaned src/pages/Admin/YouTubeGatePanel.jsx.
 * These endpoints sit under /admin, so the normal adminFetch prefix applies.
 */

interface Snapshot {
  success?: boolean
  enabled?: boolean
  circuit_state?: string
  cost_today?: number
  daily_limit_gb?: number
  bytes_today?: number
  [k: string]: unknown
}

function fmtBytes(n: unknown) {
  const v = Number(n)
  if (!Number.isFinite(v) || v <= 0) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(v) / Math.log(1024)), u.length - 1)
  return `${(v / 1024 ** i).toFixed(1)} ${u[i]}`
}

export default function YouTubeGatePage() {
  const [snap, setSnap] = useState<Snapshot | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    try {
      setSnap(await adminFetch<Snapshot>('/youtube/status'))
      setErr(null)
    } catch (e) { setErr((e as Error).message) }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 20_000)
    return () => clearInterval(t)
  }, [load])

  async function toggle(next: boolean) {
    setBusy(true); setMsg('')
    try {
      await adminPost('/youtube/toggle', { enabled: next })
      setMsg(next ? 'Đã bật tải YouTube.' : 'Đã tắt tải YouTube.')
      await load()
    } catch (e) { setMsg((e as Error).message) } finally { setBusy(false) }
  }

  const enabled = !!snap?.enabled
  const circuit = String(snap?.circuit_state ?? '—')

  // Everything not already surfaced above, shown rather than dropped: this
  // endpoint returns a dashboard snapshot whose keys vary by build.
  const known = new Set(['success', 'enabled', 'circuit_state', 'cost_today', 'bytes_today', 'daily_limit_gb'])
  const extra = Object.entries(snap ?? {}).filter(([k]) => !known.has(k))

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">YouTube Gate</h1>
          <p className="text-xs text-gray-500">Công tắc, circuit breaker và trần băng thông · làm mới 20s</p>
        </div>
        <button onClick={() => toggle(!enabled)} disabled={busy || !snap}
          className={`rounded-md px-3 py-1.5 text-xs font-semibold border transition disabled:opacity-50 ${
            enabled ? 'border-red-600/50 text-red-300 hover:bg-red-600/10'
                    : 'border-emerald-600/50 text-emerald-300 hover:bg-emerald-600/10'}`}>
          {busy ? 'Đang xử lý…' : enabled ? 'Tắt tải YouTube' : 'Bật tải YouTube'}
        </button>
      </div>

      {err && <p className="text-xs text-red-400">Lỗi: {err}</p>}
      {msg && <p className="text-xs text-emerald-400">{msg}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Trạng thái</div>
          <div className={`mt-1 text-xl font-semibold ${enabled ? 'text-emerald-400' : 'text-red-400'}`}>
            {snap ? (enabled ? 'Đang bật' : 'Đang tắt') : '…'}
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Circuit breaker</div>
          <div className={`mt-1 text-xl font-semibold ${
            circuit === 'open' ? 'text-red-400' : circuit === 'half_open' ? 'text-amber-400' : 'text-emerald-400'}`}>
            {circuit}
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Băng thông hôm nay</div>
          <div className="mt-1 text-xl font-semibold text-gray-100">{fmtBytes(snap?.bytes_today)}</div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-500">Chi phí ước tính</div>
          <div className="mt-1 text-xl font-semibold text-gray-100">
            {snap?.cost_today != null ? `$${Number(snap.cost_today).toFixed(2)}` : '—'}
          </div>
        </div>
      </div>

      {extra.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Chi tiết khác</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {extra.map(([k, v]) => (
              <div key={k} className="rounded border border-gray-800 px-2 py-1">
                <div className="text-[10px] text-gray-500">{k}</div>
                <div className="font-mono text-[11px] text-gray-200 break-all">
                  {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
