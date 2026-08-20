import { useCallback, useEffect, useState } from 'react'
import { intelFetch, intelPost } from '../utils/adminFetch'

/**
 * Ported from the orphaned src/pages/Admin/QueueHealthPanel.jsx, which no route
 * could reach. Same endpoints, now on the admin session token instead of the
 * legacy X-Admin-Token header.
 */

interface Health {
  queue_depth: number
  estimated_wait_seconds: number
  active_workers: number
  paused_low_priority: boolean
  jobs_by_priority: Record<string, number>
  disk_pressure_pct: number
  provider_pressure: string
}

interface Tune {
  params: Record<string, unknown>
  history: Array<Record<string, unknown>>
}

function Stat({ label, value, hint, tone }: {
  label: string; value: string; hint?: string; tone?: 'ok' | 'warn' | 'bad'
}) {
  const colour = tone === 'bad' ? 'text-red-400'
    : tone === 'warn' ? 'text-amber-400'
    : tone === 'ok' ? 'text-emerald-400' : 'text-gray-100'
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
      <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${colour}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[10px] text-gray-500">{hint}</div>}
    </div>
  )
}

export default function QueueHealthPage() {
  const [health, setHealth] = useState<Health | null>(null)
  const [tune, setTune] = useState<Tune | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const load = useCallback(async () => {
    try {
      setHealth(await intelFetch<Health>('/queue-health'))
      setErr(null)
    } catch (e) { setErr((e as Error).message) }
    try { setTune(await intelFetch<Tune>('/auto-tune')) } catch { /* secondary */ }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 15_000)
    return () => clearInterval(t)
  }, [load])

  async function resetTuning() {
    setBusy(true); setMsg('')
    try {
      await intelPost('/auto-tune/reset')
      setMsg('Đã đặt lại tham số về mặc định.')
      await load()
    } catch (e) { setMsg((e as Error).message) } finally { setBusy(false) }
  }

  const workers = health?.active_workers ?? -1
  const wait = health ? Math.round(health.estimated_wait_seconds) : 0

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Queue Health</h1>
          <p className="text-xs text-gray-500">Tự làm mới mỗi 15s</p>
        </div>
        <button onClick={resetTuning} disabled={busy}
          className="rounded-md border border-gray-700 px-3 py-1.5 text-xs text-gray-300
                     hover:bg-gray-800 disabled:opacity-50">
          {busy ? 'Đang đặt lại…' : 'Đặt lại auto-tune'}
        </button>
      </div>

      {err && <p className="text-xs text-red-400">Lỗi: {err}</p>}
      {msg && <p className="text-xs text-emerald-400">{msg}</p>}

      {health && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <Stat label="Hàng đợi" value={String(health.queue_depth)}
                tone={health.queue_depth > 100 ? 'bad' : health.queue_depth > 20 ? 'warn' : 'ok'} />
          {/* -1 means the count could not be determined — say so rather than
              printing a number that looks like "no workers". */}
          <Stat label="Worker" value={workers < 0 ? '—' : String(workers)}
                hint={workers < 0 ? 'chưa xác định được' : undefined}
                tone={workers < 0 ? 'warn' : workers === 0 ? 'bad' : 'ok'} />
          <Stat label="Chờ ước tính" value={`${wait}s`} />
          <Stat label="Ưu tiên thấp"
                value={health.paused_low_priority ? 'Đang tạm dừng' : 'Đang chạy'}
                tone={health.paused_low_priority ? 'warn' : 'ok'} />
          <Stat label="Đĩa" value={`${Math.round(health.disk_pressure_pct)}%`}
                tone={health.disk_pressure_pct >= 90 ? 'bad' : health.disk_pressure_pct >= 75 ? 'warn' : 'ok'} />
          <Stat label="Nguồn tải" value={String(health.provider_pressure)} />
        </div>
      )}

      {health && Object.keys(health.jobs_by_priority || {}).length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Job theo mức ưu tiên</h2>
          <div className="flex flex-wrap gap-2">
            {Object.entries(health.jobs_by_priority).map(([k, v]) => (
              <span key={k} className="rounded border border-gray-700 px-2 py-0.5 text-[11px] text-gray-300">
                {k}: <span className="font-mono text-gray-100">{v}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {tune && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Auto-tune</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
            {Object.entries(tune.params || {}).map(([k, v]) => (
              <div key={k} className="rounded border border-gray-800 px-2 py-1">
                <div className="text-[10px] text-gray-500">{k}</div>
                <div className="font-mono text-xs text-gray-100">{String(v)}</div>
              </div>
            ))}
          </div>
          {(tune.history || []).length === 0
            ? <p className="text-[11px] text-gray-500">Chưa có lần điều chỉnh nào.</p>
            : (
              <div className="max-h-64 overflow-y-auto">
                <table className="w-full text-[11px]">
                  <tbody>
                    {tune.history.map((h, i) => (
                      <tr key={i} className="border-b border-gray-800/60">
                        <td className="py-1 pr-3 text-gray-500">{String(h.timestamp ?? h.ts ?? '')}</td>
                        <td className="py-1 pr-3 text-gray-200">{String(h.param ?? h.action ?? '')}</td>
                        <td className="py-1 text-gray-400">{String(h.new_value ?? h.reason ?? '')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      )}
    </div>
  )
}
