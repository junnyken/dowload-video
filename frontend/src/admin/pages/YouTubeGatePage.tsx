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
  daily_limit_gb?: number
  bytes_today?: number
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

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">YouTube Gate</h1>
          <p className="text-xs text-fg-muted">Công tắc, circuit breaker và trần băng thông · làm mới 20s</p>
        </div>
        <button onClick={() => toggle(!enabled)} disabled={busy || !snap}
          className={`rounded-md px-3 py-1.5 text-xs font-semibold border transition disabled:opacity-50 ${
            enabled ? 'border-danger/50 text-danger hover:bg-danger-soft'
                    : 'border-success/50 text-success hover:bg-success-soft'}`}>
          {busy ? 'Đang xử lý…' : enabled ? 'Tắt tải YouTube' : 'Bật tải YouTube'}
        </button>
      </div>

      {err && <p className="text-xs text-danger">Lỗi: {err}</p>}
      {msg && <p className="text-xs text-success">{msg}</p>}

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="rounded-card border border-line bg-surface shadow-card p-3">
          <div className="text-[10px] uppercase tracking-wide text-fg-muted">Trạng thái</div>
          <div className={`mt-1 text-xl font-semibold ${enabled ? 'text-success' : 'text-danger'}`}>
            {snap ? (enabled ? 'Đang bật' : 'Đang tắt') : '…'}
          </div>
        </div>
        <div className="rounded-card border border-line bg-surface shadow-card p-3">
          <div className="text-[10px] uppercase tracking-wide text-fg-muted">Circuit breaker</div>
          <div className={`mt-1 text-xl font-semibold ${
            circuit === 'open' ? 'text-danger' : circuit === 'half_open' ? 'text-warning' : 'text-success'}`}>
            {circuit}
          </div>
        </div>
        <div className="rounded-card border border-line bg-surface shadow-card p-3">
          <div className="text-[10px] uppercase tracking-wide text-fg-muted">Băng thông hôm nay</div>
          <div className="mt-1 text-xl font-semibold text-fg">{fmtBytes(snap?.bytes_today)}</div>
        </div>
        <div className="rounded-card border border-line bg-surface shadow-card p-3">
          <div className="text-[10px] uppercase tracking-wide text-fg-muted">Trần băng thông / ngày</div>
          <div className="mt-1 text-xl font-semibold text-fg">
            {/* wording: BA review */}
            {snap?.daily_limit_gb != null && Number(snap.daily_limit_gb) > 0 ? `${snap.daily_limit_gb} GB` : 'Không giới hạn'}
          </div>
        </div>
      </div>
    </div>
  )
}
