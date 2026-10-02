import { useState } from 'react'
import { useAdminProbes, useSetProbeTarget, useRunProbes } from '../hooks/useAdminProbes'
import type { ProbeRow, ProbeStatus } from '../api/probes'

const LABEL: Record<ProbeStatus, string> = {
  ok:             'Tải được',
  failed:         'HỎNG',
  unconfirmed:    'Lỗi 1 lần, chờ xác nhận',
  stale:          'Kết quả đã cũ',
  not_configured: 'Chưa cấu hình',
}

// not_configured is grey, never green. The whole point of this page is that
// "nobody has checked" and "checked and fine" must not look alike.
const TONE: Record<ProbeStatus, string> = {
  ok:             'text-success border-success/30 bg-success-soft',
  failed:         'text-danger border-danger/30 bg-danger-soft',
  // One failed probe: worth a look, not an outage until the next one confirms it.
  unconfirmed:    'text-warning border-warning/30 bg-warning-soft',
  stale:          'text-warning border-warning/30 bg-warning-soft',
  not_configured: 'text-fg-muted border-line-strong bg-surface-2',
}

function age(s: number | null): string {
  if (s === null) return '—'
  if (s < 60) return `${s}s trước`
  if (s < 3600) return `${Math.floor(s / 60)} phút trước`
  return `${Math.floor(s / 3600)} giờ trước`
}

export default function ProbesPage() {
  const { data, isLoading, isError, error, refetch } = useAdminProbes()
  const setTarget = useSetProbeTarget()
  const runNow = useRunProbes()
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  if (isLoading) {
    return <div className="flex min-h-[60vh] items-center justify-center">
      <div className="text-sm text-fg-muted animate-pulse">Đang tải trạng thái nền tảng…</div>
    </div>
  }

  if (isError) {
    return <div className="max-w-2xl mx-auto mt-16 rounded-card border border-danger/30 bg-danger-soft p-6">
      <p className="text-danger font-semibold mb-1">Không tải được</p>
      <p className="text-sm text-fg-muted mb-4">{(error as Error)?.message}</p>
      <button onClick={() => refetch()} className="px-4 py-2 rounded-control bg-surface-2 text-fg-2 text-sm">Thử lại</button>
    </div>
  }

  const rows: ProbeRow[] = data?.platforms ?? []
  const c = data?.counts ?? {}
  const unchecked = (c.not_configured ?? 0) + (c.stale ?? 0)

  const save = (platform: string) => {
    setTarget.mutate({ platform, url: draft.trim() || null })
    setEditing(null); setDraft('')
  }

  return (
    <div className="space-y-6">

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Sức khoẻ nền tảng</h1>
          <p className="text-sm text-fg-muted mt-0.5">Dò chủ động mỗi 30 phút · chỉ lấy metadata, không tải</p>
        </div>
        <button
          onClick={() => runNow.mutate()}
          disabled={runNow.isPending}
          className="px-4 py-2 rounded-control border border-accent bg-accent text-accent-fg
                     text-sm font-semibold hover:bg-accent-hover disabled:opacity-50 transition-colors"
        >
          {runNow.isPending ? 'Đang xếp hàng…' : '▶ Dò ngay'}
        </button>
      </div>

      {/* The button queues work on a worker and returns immediately, so without
          this the only visible change was a 200ms flicker — identical to a
          button that does nothing, which is how it was first reported. */}
      {runNow.isSuccess && (
        <div className="rounded-card border border-success/30 bg-success-soft px-4 py-3">
          <p className="text-sm text-success font-semibold">Đã xếp hàng đợi</p>
          <p className="text-xs text-fg-muted mt-0.5">
            Mỗi nền tảng mất tới 30 giây, nên một lượt dò có thể chạy hơn một phút.
            Bảng dưới tự làm mới trong 90 giây tới — cột «Lần dò gần nhất» đổi là xong.
          </p>
        </div>
      )}
      {runNow.isError && (
        <div className="rounded-card border border-danger/30 bg-danger-soft px-4 py-3">
          <p className="text-sm text-danger font-semibold">Không xếp được hàng đợi</p>
          <p className="text-xs text-fg-muted mt-0.5 break-words">
            {(runNow.error as Error)?.message || 'Lỗi không xác định.'}
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {([['ok', 'Tải được'], ['failed', 'Hỏng'], ['stale', 'Cũ'], ['not_configured', 'Chưa cấu hình']] as const).map(([k, lbl]) => (
          <div key={k} className="rounded-card border border-line bg-surface px-4 py-3 shadow-card">
            <div className="font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted">{lbl}</div>
            <div className={`mt-1.5 font-mono text-2xl font-semibold tabular-nums ${TONE[k].split(' ')[0]}`}>{c[k] ?? 0}</div>
          </div>
        ))}
      </div>

      {unchecked > 0 && (
        <div className="rounded-card border border-line bg-surface shadow-card px-4 py-3">
          <p className="text-xs text-fg-muted leading-relaxed">
            <b className="text-fg-2">{unchecked}/{data?.total} nền tảng chưa có bằng chứng nào.</b>{' '}
            Chúng không được tính là khoẻ — nền tảng không ai dò trông y hệt lúc nó vừa chết.
            Dán một link công khai bất kỳ của nền tảng đó vào ô bên dưới (link bạn đã tải
            thành công là tốt nhất), hệ thống sẽ tự dò mỗi 30 phút.
          </p>
        </div>
      )}

      {data?.note && <p className="text-xs text-fg-muted leading-relaxed">{data.note}</p>}

      <div className="rounded-card border border-line overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-surface text-fg-muted text-xs">
            <tr>
              <th className="text-left px-4 py-2 font-semibold">Nền tảng</th>
              <th className="text-left px-4 py-2 font-semibold">Trạng thái</th>
              <th className="text-left px-4 py-2 font-semibold">Lần dò gần nhất</th>
              <th className="text-left px-4 py-2 font-semibold">Link mẫu</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.platform} className="border-t border-line align-top">
                <td className="px-4 py-2.5 text-fg-2 font-medium">{r.platform}</td>
                <td className="px-4 py-2.5">
                  <span className={`inline-block px-2 py-0.5 rounded-md border text-[11px] font-bold ${TONE[r.status]}`}>
                    {LABEL[r.status]}
                  </span>
                  {r.reason && r.status !== 'ok' && (
                    <div className="text-[11px] text-fg-muted mt-1 max-w-[280px] break-words">{r.reason}</div>
                  )}
                </td>
                <td className="px-4 py-2.5 text-fg-muted text-xs whitespace-nowrap">
                  {age(r.age_s)}{r.ms !== null && <span className="text-fg-muted"> · {r.ms}ms</span>}
                </td>
                <td className="px-4 py-2.5">
                  {editing === r.platform ? (
                    <div className="flex gap-1.5">
                      <input
                        autoFocus value={draft} onChange={e => setDraft(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') save(r.platform); if (e.key === 'Escape') setEditing(null) }}
                        placeholder="https://…"
                        className="flex-1 min-w-0 px-2 py-1 rounded-md bg-surface border border-line-strong
                                   text-xs text-fg-2 focus:outline-none focus:border-accent/60"
                      />
                      <button onClick={() => save(r.platform)} className="px-2 py-1 rounded-md border border-accent bg-accent text-accent-fg text-xs font-semibold">Lưu</button>
                      <button onClick={() => setEditing(null)} className="px-2 py-1 rounded-md text-fg-2 text-xs">Huỷ</button>
                    </div>
                  ) : (
                    <button
                      onClick={() => { setEditing(r.platform); setDraft(r.target || '') }}
                      className="text-xs text-left text-fg-muted hover:text-accent-text transition-colors break-all max-w-[320px]"
                    >
                      {r.target || <span className="text-fg-muted italic">chưa đặt — bấm để thêm</span>}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
