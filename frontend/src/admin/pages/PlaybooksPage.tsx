import { useCallback, useEffect, useState } from 'react'
import { intelFetch, intelPost } from '../utils/adminFetch'

/** Ported from the orphaned src/pages/Admin/PlaybooksPanel.jsx. */

interface Playbook {
  id: string
  name: string
  description?: string
  trigger_conditions?: string[]
  auto_actions?: string[]
  manual_steps?: string[]
  currently_matched?: boolean
}

interface PlaybooksData {
  playbooks: Playbook[]
  active_anomaly_count: number
  matched_playbook_count: number
}

export default function PlaybooksPage() {
  const [data, setData] = useState<PlaybooksData | null>(null)
  const [history, setHistory] = useState<Array<Record<string, unknown>>>([])
  const [err, setErr] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    try {
      setData(await intelFetch<PlaybooksData>('/playbooks'))
      setErr(null)
    } catch (e) { setErr((e as Error).message) }
    try {
      const h = await intelFetch<{ history: Array<Record<string, unknown>> }>('/playbooks/history')
      setHistory(h.history || [])
    } catch { /* secondary */ }
  }, [])

  useEffect(() => { load() }, [load])

  async function run(pb: Playbook, action: string) {
    setBusy(p => ({ ...p, [pb.id]: true }))
    setMsg(p => ({ ...p, [pb.id]: '' }))
    try {
      const r = await intelPost<{ success?: boolean; message?: string }>(
        '/playbooks/execute', { playbook_id: pb.id, action, params: {} })
      setMsg(p => ({ ...p, [pb.id]: r?.message || (r?.success ? 'Đã chạy.' : 'Đã gửi.') }))
      await load()
    } catch (e) {
      setMsg(p => ({ ...p, [pb.id]: (e as Error).message }))
    } finally {
      setBusy(p => ({ ...p, [pb.id]: false }))
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Playbooks</h1>
        <p className="text-xs text-gray-500">
          {data
            ? `${data.matched_playbook_count} playbook khớp · ${data.active_anomaly_count} bất thường đang hoạt động`
            : 'Đang tải…'}
        </p>
      </div>

      {err && <p className="text-xs text-red-400">Lỗi: {err}</p>}

      <div className="space-y-3">
        {(data?.playbooks ?? []).map(pb => {
          const matched = !!pb.currently_matched
          return (
            <div key={pb.id}
                 className={`rounded-lg border p-4 ${matched
                   ? 'border-amber-600/50 bg-amber-950/20' : 'border-gray-800 bg-gray-900/60'}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold text-white">{pb.name}</h2>
                    {matched && (
                      <span className="rounded bg-amber-700/40 px-1.5 py-0.5 text-[10px] text-amber-200">
                        đang khớp
                      </span>
                    )}
                  </div>
                  {pb.description && (
                    <p className="mt-1 text-[11px] leading-relaxed text-gray-400">{pb.description}</p>
                  )}
                </div>
                <button onClick={() => setOpen(open === pb.id ? null : pb.id)}
                        className="shrink-0 rounded border border-gray-700 px-2 py-1 text-[10px] text-gray-300 hover:bg-gray-800">
                  {open === pb.id ? 'Thu gọn' : 'Chi tiết'}
                </button>
              </div>

              {open === pb.id && (
                <div className="mt-3 space-y-3 border-t border-gray-800 pt-3">
                  {!!pb.trigger_conditions?.length && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-gray-500">Điều kiện kích hoạt</div>
                      <ul className="mt-1 list-disc pl-4 text-[11px] text-gray-300">
                        {pb.trigger_conditions.map((c, i) => <li key={i}>{c}</li>)}
                      </ul>
                    </div>
                  )}
                  {!!pb.manual_steps?.length && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-gray-500">Các bước thủ công</div>
                      <ol className="mt-1 list-decimal pl-4 text-[11px] text-gray-300">
                        {pb.manual_steps.map((c, i) => <li key={i}>{c}</li>)}
                      </ol>
                    </div>
                  )}
                  {!!pb.auto_actions?.length && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-gray-500">Hành động tự động</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        {pb.auto_actions.map(a => (
                          <button key={a} disabled={!!busy[pb.id]} onClick={() => run(pb, a)}
                            className="rounded border border-indigo-600/50 px-2 py-0.5 text-[10px]
                                       text-indigo-300 hover:bg-indigo-600/10 disabled:opacity-50">
                            {busy[pb.id] ? '…' : a}
                          </button>
                        ))}
                        {msg[pb.id] && <span className="text-[10px] text-emerald-400">{msg[pb.id]}</span>}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="rounded-lg border border-gray-800 bg-gray-900/60 p-4">
        <h2 className="mb-2 text-sm font-semibold text-white">Lịch sử chạy</h2>
        {history.length === 0
          ? <p className="text-[11px] text-gray-500">Chưa có lần chạy nào.</p>
          : (
            <div className="max-h-72 overflow-y-auto">
              <table className="w-full text-[11px]">
                <tbody>
                  {history.map((h, i) => (
                    <tr key={i} className="border-b border-gray-800/60">
                      <td className="py-1 pr-3 text-gray-500">{String(h.timestamp ?? h.ts ?? '')}</td>
                      <td className="py-1 pr-3 text-gray-200">{String(h.playbook_id ?? h.action ?? '')}</td>
                      <td className="py-1 text-gray-400">{String(h.result ?? h.outcome ?? '')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </div>
  )
}
