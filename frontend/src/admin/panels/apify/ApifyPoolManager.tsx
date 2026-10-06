import { useCallback, useEffect, useState } from 'react'
import { apifyPoolApi } from '../../api/apifyPool'
import type { PoolEntry, PoolPayload, PoolState } from '../../api/apifyPool'
import { Button } from '../../shared/Button'
import { StatusPill } from '../../shared/StatusPill'
import type { PillTone } from '../../shared/StatusPill'
import { TABLE, TABLE_SCROLL, TD, TD_NUM, TH, TH_NUM, TR } from '../../shared/tableStyles'

// Danh sách token Apify (pool). Dùng ở trang "Chi phí Apify" và thẻ trong Config.
// Không bao giờ hiển thị token: chỉ tên, ••••4 ký tự cuối, tài khoản.

const dash = '—'
/** Dollars: 2 decimals from $1 up, more below so small per-call costs stay visible. */
export const usd = (v: number | null | undefined, digits = 2) =>
  v == null || Number.isNaN(Number(v))
    ? dash
    : `$${Number(v).toFixed(Math.abs(Number(v)) >= 1 ? 2 : digits)}`
export const fmtTime = (iso: string | null | undefined) => {
  if (!iso) return dash
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('vi-VN')
}
const fmtDate = (iso: string | null | undefined) => {
  if (!iso) return dash
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('vi-VN')
}

export const STATE_VI: Record<PoolState, { label: string; tone: PillTone }> = {
  active:    { label: 'Đang dùng',     tone: 'success' },
  cooldown:  { label: 'Tạm nghỉ',      tone: 'warning' },
  exhausted: { label: 'Hết tiền',      tone: 'danger' },
  invalid:   { label: 'Token lỗi',     tone: 'danger' },
  disabled:  { label: 'Đã tắt',        tone: 'neutral' },
}

function stateHint(e: PoolEntry): string | null {
  if (e.state === 'exhausted') return `Tự dùng lại từ ${fmtDate(e.exhausted_until)}`
  if (e.state === 'cooldown') return `Thử lại sau ${fmtTime(e.cooldown_until)}`
  if (e.state === 'invalid') return 'Kiểm tra token trên Apify rồi bấm Làm mới'
  if (e.ineligible_reason === 'entry_ceiling') return 'Đã chạm trần chi tiêu riêng'
  return null
}

function askReason(action: string): string | null {
  const reason = window.prompt(`Lý do ${action} (bắt buộc):`)
  if (!reason || reason.trim().length < 3) return null
  return reason.trim()
}

const INPUT_BASE =
  'h-9 rounded-control border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-muted ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'
const INPUT = `${INPUT_BASE} w-full`

interface EditDraft { label: string; priority: string; ceiling: string }

export function ApifyPoolManager({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<PoolPayload | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<EditDraft>({ label: '', priority: '', ceiling: '' })
  const [adding, setAdding] = useState(false)
  const [newTok, setNewTok] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [newPriority, setNewPriority] = useState('')
  const [newCeiling, setNewCeiling] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await apifyPoolApi.list())
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Không tải được danh sách token')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const run = async (key: string, fn: () => Promise<unknown>, okText: string) => {
    setBusy(key); setMsg(null)
    try {
      await fn()
      setMsg({ ok: true, text: okText })
      await load()
      onChanged?.()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Thao tác thất bại' })
    } finally {
      setBusy(null)
    }
  }

  const submitAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    const token = newTok.trim()
    if (!token) { setMsg({ ok: false, text: 'Hãy dán token Apify.' }); return }
    if (!newLabel.trim()) { setMsg({ ok: false, text: 'Hãy đặt tên cho token (ví dụ: "Tổ chức A").' }); return }
    const reason = askReason('thêm token')
    if (!reason) { setMsg({ ok: false, text: 'Cần nhập lý do (ít nhất 3 ký tự).' }); return }
    await run('add', () => apifyPoolApi.add({
      token, label: newLabel.trim(), reason,
      priority: newPriority.trim() ? Number(newPriority) : undefined,
      monthly_ceiling_usd: newCeiling.trim() ? Number(newCeiling) : null,
    }), 'Đã kiểm tra và thêm token.')
    setNewTok('')
    setNewLabel(''); setNewPriority(''); setNewCeiling('')
    setAdding(false)
  }

  const startEdit = (e: PoolEntry) => {
    setEditing(e.id)
    setDraft({ label: e.label, priority: String(e.priority), ceiling: e.monthly_ceiling_usd != null ? String(e.monthly_ceiling_usd) : '' })
  }

  const saveEdit = async (e: PoolEntry) => {
    const reason = askReason('sửa token')
    if (!reason) { setMsg({ ok: false, text: 'Cần nhập lý do (ít nhất 3 ký tự).' }); return }
    await run(`edit:${e.id}`, () => apifyPoolApi.update(e.id, {
      label: draft.label.trim(), priority: Number(draft.priority),
      monthly_ceiling_usd: draft.ceiling.trim() ? Number(draft.ceiling) : null, reason,
    }), 'Đã lưu thay đổi.')
    setEditing(null)
  }

  const toggle = async (e: PoolEntry) => {
    const reason = askReason(e.enabled ? 'tắt token' : 'bật token')
    if (!reason) { setMsg({ ok: false, text: 'Cần nhập lý do (ít nhất 3 ký tự).' }); return }
    await run(`toggle:${e.id}`, () => apifyPoolApi.update(e.id, { enabled: !e.enabled, reason }),
      e.enabled ? 'Đã tắt token.' : 'Đã bật token.')
  }

  const remove = async (e: PoolEntry) => {
    if (!window.confirm(`Xoá token "${e.label}" (••••${e.last4 ?? '?'})?`)) return
    const reason = askReason('xoá token')
    if (!reason) { setMsg({ ok: false, text: 'Cần nhập lý do (ít nhất 3 ký tự).' }); return }
    await run(`del:${e.id}`, () => apifyPoolApi.remove(e.id, reason), 'Đã xoá token.')
  }

  const entries = data?.entries ?? []

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" onClick={() => setAdding((v) => !v)} disabled={busy !== null}>
          {adding ? 'Đóng' : 'Thêm token'}
        </Button>
        <Button onClick={() => run('refresh-all', apifyPoolApi.refreshAll, 'Đã cập nhật số liệu từ Apify.')}
          disabled={busy !== null || entries.length === 0}>
          {busy === 'refresh-all' ? 'Đang làm mới...' : 'Làm mới tất cả'}
        </Button>
        {data && (
          <span className="text-xs text-fg-muted">
            {data.summary.eligible}/{data.summary.entries} token đang dùng được
          </span>
        )}
      </div>

      {adding && (
        <form onSubmit={submitAdd} className="grid grid-cols-1 gap-2 rounded-control border border-line bg-surface-2 p-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Tên gợi nhớ
            <input className={INPUT} value={newLabel} onChange={(e) => setNewLabel(e.target.value)}
              placeholder="Ví dụ: Tổ chức A" maxLength={60} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Token Apify
            <input className={`${INPUT} font-mono`} type="password" autoComplete="off" value={newTok}
              onChange={(e) => setNewTok(e.target.value)} placeholder="Dán token của tài khoản tổ chức" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Thứ tự ưu tiên (số nhỏ dùng trước, mặc định 100)
            <input className={INPUT} inputMode="numeric" value={newPriority}
              onChange={(e) => setNewPriority(e.target.value)} placeholder="100" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-fg-muted">
            Trần chi tiêu tháng (USD, để trống = không đặt)
            <input className={INPUT} inputMode="decimal" value={newCeiling}
              onChange={(e) => setNewCeiling(e.target.value)} placeholder="Ví dụ: 5" />
          </label>
          <div className="sm:col-span-2 flex items-center gap-2">
            <Button type="submit" variant="primary" disabled={busy !== null}>
              {busy === 'add' ? 'Đang kiểm tra...' : 'Kiểm tra & thêm'}
            </Button>
            <span className="text-[11px] text-fg-muted">Token được kiểm tra bằng lệnh miễn phí của Apify trước khi lưu.</span>
          </div>
        </form>
      )}

      {loadError ? (
        <div className="rounded-control border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{loadError}</div>
      ) : !data ? (
        <p className="text-sm text-fg-muted">Đang tải...</p>
      ) : entries.length === 0 ? (
        <p className="rounded-control border border-line bg-surface-2 px-4 py-3 text-sm text-fg-2">
          Chưa có token Apify nào. Lượt lấy video trả phí (Douyin, Kuaishou, Xiaohongshu) sẽ không chạy cho tới khi thêm token.
        </p>
      ) : (
        <div className={`${TABLE_SCROLL} rounded-control border border-line`}>
          <table className={TABLE}>
            <thead>
              <tr>
                <th className={TH}>Tên · token · tài khoản Apify</th>
                <th className={TH}>Trạng thái · ưu tiên</th>
                <th className={TH_NUM}>Apify đã dùng / giới hạn</th>
                <th className={TH_NUM}>Ghi nhận / trần tháng</th>
                <th className={TH_NUM}>Còn lại</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const st = STATE_VI[e.state] ?? STATE_VI.active
                const hint = stateHint(e)
                const isEditing = editing === e.id
                return (
                  <tr key={e.id} className={TR}>
                    <td className={TD}>
                      {isEditing ? (
                        <input className={INPUT} value={draft.label} maxLength={60} aria-label="Tên gợi nhớ"
                          onChange={(ev) => setDraft({ ...draft, label: ev.target.value })} />
                      ) : (
                        <div className="flex flex-col">
                          <span className="font-medium text-fg">
                            {e.label}
                            <span className="ml-2 font-mono text-xs text-fg-2">{e.last4 ? `••••${e.last4}` : dash}</span>
                          </span>
                          {e.source === 'env' && <span className="text-[11px] text-fg-muted">Dự phòng (biến môi trường)</span>}
                          {e.legacy_slot && <span className="text-[11px] text-fg-muted">Token cũ đã chuyển vào danh sách</span>}
                        </div>
                      )}
                      <div className="text-[11px] text-fg-muted">
                        {e.account?.username ?? 'Chưa rõ tài khoản'}
                        {e.account?.plan ? ` (${e.account.plan})` : ''} · làm mới {fmtTime(e.refreshed_at)}
                      </div>
                      <div className="mt-1.5 flex flex-wrap gap-1">
                        {isEditing ? (
                          <>
                            <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => saveEdit(e)}>Lưu</Button>
                            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Huỷ</Button>
                          </>
                        ) : (
                          <>
                            <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => startEdit(e)}>Sửa</Button>
                            <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => toggle(e)}>
                              {e.enabled ? 'Tắt' : 'Bật'}
                            </Button>
                            <Button size="sm" variant="ghost" disabled={busy !== null}
                              onClick={() => run(`refresh:${e.id}`, () => apifyPoolApi.refresh(e.id), 'Đã làm mới số liệu token.')}>
                              {busy === `refresh:${e.id}` ? '...' : 'Làm mới'}
                            </Button>
                            {e.source !== 'env' && (
                              <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => remove(e)}
                                className="hover:text-danger">Xoá</Button>
                            )}
                          </>
                        )}
                      </div>
                    </td>
                    <td className={TD}>
                      <StatusPill tone={st.tone} label={st.label} size="xs" />
                      {hint && <div className="mt-1 text-[11px] text-fg-muted">{hint}</div>}
                      <div className="mt-1 text-[11px] text-fg-muted">
                        Ưu tiên:{' '}
                        {isEditing ? (
                          <input className={`${INPUT_BASE} w-20 text-right`} inputMode="numeric" value={draft.priority}
                            aria-label="Thứ tự ưu tiên"
                            onChange={(ev) => setDraft({ ...draft, priority: ev.target.value })} />
                        ) : <span className="font-mono text-fg-2">{e.priority}</span>}
                      </div>
                    </td>
                    <td className={TD_NUM}>
                      {usd(e.apify_usage_now_usd)} / {usd(e.apify_limit_usd)}
                    </td>
                    <td className={TD_NUM}>
                      {usd(e.spend_month_usd, 4)}
                      {' / '}
                      {isEditing ? (
                        <input className={`${INPUT_BASE} inline-block w-24 text-right`} inputMode="decimal" value={draft.ceiling}
                          aria-label="Trần chi tiêu tháng (USD)"
                          placeholder="không đặt" onChange={(ev) => setDraft({ ...draft, ceiling: ev.target.value })} />
                      ) : e.monthly_ceiling_usd != null ? usd(e.monthly_ceiling_usd) : <span className="text-fg-muted">không đặt</span>}
                    </td>
                    <td className={TD_NUM}>
                      {usd(e.remaining_usd)}
                      <div className="text-[11px] text-fg-muted">
                        {e.projected_days_left == null ? dash : `hết sau ~${e.projected_days_left.toLocaleString('vi-VN')} ngày`}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {msg && <p className={`text-xs ${msg.ok ? 'text-success' : 'text-danger'}`}>{msg.text}</p>}
      <p className="text-xs text-fg-muted">
        Đặt giới hạn chi tiêu trong trang Billing của Apify trước khi lưu token.
      </p>
      <p className="text-[11px] text-fg-muted">
        Mỗi token phải thuộc một tài khoản tổ chức (organization) hoặc một chủ sở hữu khác nhau, có số dư riêng.
        Điều khoản Apify không cho một người mở nhiều tài khoản cá nhân.
      </p>
    </div>
  )
}
