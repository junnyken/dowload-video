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
    <div className="min-h-screen bg-canvas text-fg p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-fg">Playbooks</h1>
        <p className="text-xs text-fg-muted">
          {data
            ? `${data.matched_playbook_count} playbook khớp · ${data.active_anomaly_count} bất thường đang hoạt động`
            : 'Đang tải…'}
        </p>
      </div>

      {err && <p className="text-xs text-danger">Lỗi: {err}</p>}

      <div className="space-y-3">
        {(data?.playbooks ?? []).map(pb => {
          const matched = !!pb.currently_matched
          return (
            <div key={pb.id}
                 className={`rounded-lg border p-4 ${matched
                   ? 'border-accent/50 bg-accent-soft' : 'border-line bg-surface-2'}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold text-fg">{pb.name}</h2>
                    {matched && (
                      <span className="rounded bg-accent/40 px-1.5 py-0.5 text-[10px] text-accent-text">
                        đang khớp
                      </span>
                    )}
                  </div>
                  {pb.description && (
                    <p className="mt-1 text-[11px] leading-relaxed text-fg-muted">{pb.description}</p>
                  )}
                </div>
                <button onClick={() => setOpen(open === pb.id ? null : pb.id)}
                        className="shrink-0 rounded border border-line px-2 py-1 text-[10px] text-fg-2 hover:bg-surface">
                  {open === pb.id ? 'Thu gọn' : 'Chi tiết'}
                </button>
              </div>

              {open === pb.id && (
                <div className="mt-3 space-y-3 border-t border-line pt-3">
                  {!!pb.trigger_conditions?.length && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-fg-muted">Điều kiện kích hoạt</div>
                      <ul className="mt-1 list-disc pl-4 text-[11px] text-fg-2">
                        {pb.trigger_conditions.map((c, i) => <li key={i}>{c}</li>)}
                      </ul>
                    </div>
                  )}
                  {!!pb.manual_steps?.length && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-fg-muted">Các bước thủ công</div>
                      <ol className="mt-1 list-decimal pl-4 text-[11px] text-fg-2">
                        {pb.manual_steps.map((c, i) => <li key={i}>{c}</li>)}
                      </ol>
                    </div>
                  )}
                  {!!pb.auto_actions?.length && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wide text-fg-muted">Hành động tự động</div>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        {pb.auto_actions.map(a => (
                          <button key={a} disabled={!!busy[pb.id]} onClick={() => run(pb, a)}
                            className="rounded border border-line px-2 py-0.5 text-[10px]
                                       text-fg-2 hover:bg-surface-2 disabled:opacity-50">
                            {busy[pb.id] ? '…' : a}
                          </button>
                        ))}
                        {msg[pb.id] && <span className="text-[10px] text-success">{msg[pb.id]}</span>}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <div className="rounded-lg border border-line bg-surface-2 p-4">
        <h2 className="mb-2 text-sm font-semibold text-fg">Lịch sử chạy</h2>
        {history.length === 0
          ? <p className="text-[11px] text-fg-muted">Chưa có lần chạy nào.</p>
          : (
            <div className="max-h-72 overflow-y-auto">
              <table className="w-full text-[11px]">
                <tbody>
                  {history.map((h, i) => (
                    <tr key={i} className="border-b border-line">
                      <td className="py-1 pr-3 text-fg-muted">{String(h.timestamp ?? h.ts ?? '')}</td>
                      <td className="py-1 pr-3 text-fg-2">{String(h.playbook_id ?? h.action ?? '')}</td>
                      <td className="py-1 text-fg-muted">{String(h.result ?? h.outcome ?? '')}</td>
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
