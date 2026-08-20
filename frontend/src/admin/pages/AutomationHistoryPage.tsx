import { useCallback, useEffect, useState } from 'react'
import { intelFetch } from '../utils/adminFetch'

/** Ported from the orphaned src/pages/Admin/AutomationHistoryPanel.jsx. */

interface Event {
  timestamp: string
  source: string
  action: string
  reason: string
  outcome: string
}

const SOURCE_STYLE: Record<string, string> = {
  auto_tuner:       'bg-sky-900/50 text-sky-200 border-sky-700/50',
  playbooks:        'bg-indigo-900/50 text-indigo-200 border-indigo-700/50',
  anomaly_detector: 'bg-amber-900/50 text-amber-200 border-amber-700/50',
}

export default function AutomationHistoryPage() {
  const [events, setEvents] = useState<Event[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [source, setSource] = useState<string>('all')

  const load = useCallback(async () => {
    try {
      const r = await intelFetch<{ events: Event[]; count: number }>('/automation-history')
      setEvents(r.events || [])
      setErr(null)
    } catch (e) { setErr((e as Error).message) } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [load])

  const sources = Array.from(new Set(events.map(e => e.source))).sort()
  const shown = source === 'all' ? events : events.filter(e => e.source === source)

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-white">Automation History</h1>
          <p className="text-xs text-gray-500">
            Mọi thay đổi hệ thống tự thực hiện · {events.length} sự kiện · làm mới 30s
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {['all', ...sources].map(s => (
            <button key={s} onClick={() => setSource(s)}
              className={`rounded px-2 py-1 text-[10px] border transition ${
                source === s ? 'border-gray-500 bg-gray-800 text-gray-100'
                             : 'border-gray-700 text-gray-400 hover:bg-gray-800'}`}>
              {s === 'all' ? 'Tất cả' : s}
            </button>
          ))}
        </div>
      </div>

      {err && <p className="text-xs text-red-400">Lỗi: {err}</p>}
      {loading && <p className="text-xs text-gray-500 animate-pulse">Đang tải…</p>}

      {!loading && shown.length === 0 && (
        <p className="text-xs text-gray-500">Chưa có sự kiện tự động nào được ghi lại.</p>
      )}

      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-800 bg-gray-900/60">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-gray-800 text-left text-gray-500">
                <th className="px-3 py-2 font-medium">Thời điểm</th>
                <th className="px-3 py-2 font-medium">Nguồn</th>
                <th className="px-3 py-2 font-medium">Hành động</th>
                <th className="px-3 py-2 font-medium">Lý do</th>
                <th className="px-3 py-2 font-medium">Kết quả</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((e, i) => (
                <tr key={i} className="border-b border-gray-800/60 align-top">
                  <td className="px-3 py-2 font-mono text-gray-500 whitespace-nowrap">
                    {(e.timestamp || '').replace('T', ' ').slice(0, 19) || '—'}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${
                      SOURCE_STYLE[e.source] ?? 'bg-gray-800 text-gray-300 border-gray-700'}`}>
                      {e.source}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-gray-100">{e.action || '—'}</td>
                  <td className="px-3 py-2 text-gray-400">{e.reason || '—'}</td>
                  <td className="px-3 py-2 text-gray-300">{e.outcome || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
