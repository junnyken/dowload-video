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
  auto_tuner:       'bg-surface-2 text-fg-2 border-line',
  playbooks:        'bg-surface-2 text-fg-2 border-line',
  anomaly_detector: 'bg-warning-soft text-warning border-warning/30',
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
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Automation History</h1>
          <p className="text-xs text-fg-muted">
            Mọi thay đổi hệ thống tự thực hiện · {events.length} sự kiện · làm mới 30s
          </p>
        </div>
        <div className="flex flex-wrap gap-1">
          {['all', ...sources].map(s => (
            <button key={s} onClick={() => setSource(s)}
              className={`rounded px-2 py-1 text-[10px] border transition ${
                source === s ? 'border-line-strong bg-surface text-fg'
                             : 'border-line text-fg-muted hover:bg-surface'}`}>
              {s === 'all' ? 'Tất cả' : s}
            </button>
          ))}
        </div>
      </div>

      {err && <p className="text-xs text-danger">Lỗi: {err}</p>}
      {loading && <p className="text-xs text-fg-muted animate-pulse">Đang tải…</p>}

      {!loading && shown.length === 0 && (
        <p className="text-xs text-fg-muted">Chưa có sự kiện tự động nào được ghi lại.</p>
      )}

      {shown.length > 0 && (
        <div className="overflow-x-auto rounded-control border border-line bg-surface">
          <table className="w-full text-[11px]">
            <thead>
              <tr className="border-b border-line text-left text-fg-muted">
                <th className="px-3 py-2 font-medium">Thời điểm</th>
                <th className="px-3 py-2 font-medium">Nguồn</th>
                <th className="px-3 py-2 font-medium">Hành động</th>
                <th className="px-3 py-2 font-medium">Lý do</th>
                <th className="px-3 py-2 font-medium">Kết quả</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((e, i) => (
                <tr key={i} className="border-b border-line align-top">
                  <td className="px-3 py-2 font-mono text-fg-muted whitespace-nowrap">
                    {(e.timestamp || '').replace('T', ' ').slice(0, 19) || '—'}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${
                      SOURCE_STYLE[e.source] ?? 'bg-surface text-fg-2 border-line'}`}>
                      {e.source}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-fg">{e.action || '—'}</td>
                  <td className="px-3 py-2 text-fg-muted">{e.reason || '—'}</td>
                  <td className="px-3 py-2 text-fg-2">{e.outcome || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
