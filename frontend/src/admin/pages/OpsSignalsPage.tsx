import { useCallback, useEffect, useState } from 'react'
import { adminFetch } from '../utils/adminFetch'

/**
 * Ported from the orphaned src/pages/Admin/OpsPanel.jsx. The new shell reused
 * a couple of these signals for platform badges, but the aggregated view — one
 * screen answering "is anything wrong right now" — had no home.
 */

interface Ops {
  generated_at?: string
  window_minutes?: number
  success_rate?: number
  queue_depths?: Record<string, number>
  total_queue_depth?: number
  stale_job_count?: number
  job_by_platform?: Record<string, Record<string, number> | number>
  provider_circuits?: Record<string, string>
  open_platforms?: string[]
  cookie_health?: Record<string, unknown>
  depleted_cookie_platforms?: string[]
  quota_denials_30m?: number
  recovery_actions_30m?: number
  recovery_log?: Array<Record<string, unknown>>
  worker_count?: number
  alerts?: Array<Record<string, unknown>>
}

function Stat({ label, value, tone, hint }: {
  label: string; value: string; tone?: 'ok' | 'warn' | 'bad'; hint?: string
}) {
  const c = tone === 'bad' ? 'text-red-400' : tone === 'warn' ? 'text-amber-400'
    : tone === 'ok' ? 'text-emerald-400' : 'text-gray-100'
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-3">
      <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${c}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[10px] text-gray-500">{hint}</div>}
    </div>
  )
}

export default function OpsSignalsPage() {
  const [d, setD] = useState<Ops | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try { setD(await adminFetch<Ops>('/ops-signals')); setErr(null) }
    catch (e) { setErr((e as Error).message) }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 20_000)
    return () => clearInterval(t)
  }, [load])

  const sr = d?.success_rate
  const srPct = typeof sr === 'number' ? (sr <= 1 ? sr * 100 : sr) : null
  const workers = d?.worker_count ?? -1
  const open = d?.open_platforms ?? []
  const depleted = d?.depleted_cookie_platforms ?? []

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Ops Signals</h1>
        <p className="text-xs text-gray-500">
          Cửa sổ {d?.window_minutes ?? 30} phút · làm mới 20s
          {d?.generated_at ? ` · cập nhật ${String(d.generated_at).replace('T', ' ').slice(11, 19)}` : ''}
        </p>
      </div>

      {err && <p className="text-xs text-red-400">Lỗi: {err}</p>}

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <Stat label="Tỷ lệ thành công"
              value={srPct == null ? '—' : `${srPct.toFixed(1)}%`}
              tone={srPct == null ? undefined : srPct < 70 ? 'bad' : srPct < 90 ? 'warn' : 'ok'} />
        <Stat label="Hàng đợi" value={String(d?.total_queue_depth ?? '—')}
              tone={(d?.total_queue_depth ?? 0) > 100 ? 'bad' : (d?.total_queue_depth ?? 0) > 20 ? 'warn' : 'ok'} />
        {/* -1 means "could not determine" — printing it would read as zero workers. */}
        <Stat label="Worker" value={workers < 0 ? '—' : String(workers)}
              hint={workers < 0 ? 'chưa xác định được' : undefined}
              tone={workers < 0 ? 'warn' : workers === 0 ? 'bad' : 'ok'} />
        <Stat label="Job treo" value={String(d?.stale_job_count ?? '—')}
              tone={(d?.stale_job_count ?? 0) > 0 ? 'warn' : 'ok'} />
        <Stat label="Từ chối quota" value={String(d?.quota_denials_30m ?? '—')} />
        <Stat label="Lần tự khôi phục" value={String(d?.recovery_actions_30m ?? '—')} />
      </div>

      {(open.length > 0 || depleted.length > 0) && (
        <div className="rounded-lg border border-amber-800/50 bg-amber-950/20 p-4 space-y-2">
          {open.length > 0 && (
            <div className="text-[11px] text-amber-200">
              <span className="font-semibold">Circuit đang mở:</span> {open.join(', ')}
            </div>
          )}
          {depleted.length > 0 && (
            <div className="text-[11px] text-amber-200">
              <span className="font-semibold">Cookie đã cạn:</span> {depleted.join(', ')}
            </div>
          )}
        </div>
      )}

      {d?.queue_depths && Object.keys(d.queue_depths).length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Độ sâu từng hàng đợi</h2>
          <div className="flex flex-wrap gap-2">
            {Object.entries(d.queue_depths).map(([k, v]) => (
              <span key={k} className="rounded border border-gray-700 px-2 py-0.5 text-[11px] text-gray-300">
                {k}: <span className="font-mono text-gray-100">{v}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      {d?.provider_circuits && Object.keys(d.provider_circuits).length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Circuit theo nền tảng</h2>
          <div className="flex flex-wrap gap-2">
            {Object.entries(d.provider_circuits).map(([k, v]) => (
              <span key={k}
                className={`rounded border px-2 py-0.5 text-[11px] ${
                  v === 'open' ? 'border-red-700/60 text-red-300'
                  : v === 'half_open' ? 'border-amber-700/60 text-amber-300'
                  : 'border-gray-700 text-gray-300'}`}>
                {k}: {String(v)}
              </span>
            ))}
          </div>
        </div>
      )}

      {!!d?.recovery_log?.length && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
          <h2 className="mb-2 text-sm font-semibold text-white">Nhật ký tự khôi phục</h2>
          <div className="max-h-64 overflow-y-auto">
            <table className="w-full text-[11px]">
              <tbody>
                {d.recovery_log.map((e, i) => (
                  <tr key={i} className="border-b border-gray-800/60">
                    <td className="py-1 pr-3 text-gray-500 font-mono whitespace-nowrap">
                      {String(e.timestamp ?? e.ts ?? '').replace('T', ' ').slice(0, 19)}
                    </td>
                    <td className="py-1 pr-3 text-gray-200">{String(e.action ?? e.type ?? '')}</td>
                    <td className="py-1 text-gray-400">{String(e.detail ?? e.reason ?? e.result ?? '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
