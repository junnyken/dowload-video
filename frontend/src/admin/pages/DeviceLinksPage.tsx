import { useCallback, useEffect, useState } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { useAdminAuth } from '../hooks/useAdminAuth'
import { PageHeader } from '../shared/PageHeader'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'
import { ErrorState } from '../shared/ErrorState'
import { BlockSkeleton } from '../shared/LoadingSkeleton'
import { PILL_TONE_STYLE } from '../shared/StatusPill'
import { cn } from '../utils/cn'

/**
 * Liên kết thiết bị: giới hạn số thiết bị mỗi tài khoản.
 * Backend: /api/v1/admin/device-links*.
 */
// wording: BA review

type Mode = 'off' | 'shadow' | 'enforce'

interface Settings {
  mode: Mode
  effective_mode: Mode
  enforce_at: number | null
  free_max: number
  paid_max: number
  extra_mode?: Mode
  extra_enforce_at?: number | null
  net_free_accounts?: number
  clone_networks?: number
}
interface DayRow {
  day: string
  would_sign_out?: number
  signed_out?: number
  blocked_requests?: number
  shared_would_block?: number
  shared_blocked?: number
  no_device_would_count?: number
  clone_would_split?: number
  clone_detected?: number
  shared_would_block_family?: number
  shared_blocked_family?: number
  shared_would_block_network?: number
  shared_blocked_network?: number
}
interface DeviceEvent {
  t: number
  kind: string
  mode: string
  uid: string | number
  dev: string
  by?: string
  limit?: number
  platform?: string
  networks?: number | string | string[]
}
interface Overview {
  settings: Settings
  per_day: DayRow[]
  events: DeviceEvent[]
  multi_account_devices: { device: string; accounts_7d: number }[]
  multi_account_device_count: number
}
interface UserDevices {
  user_id: string | number
  devices: { device: string; last_seen: number | null; signed_out?: boolean; signed_out_at?: number }[]
}

const MODE_VI: Record<Mode, string> = { off: 'Tắt', shadow: 'Theo dõi', enforce: 'Đang chặn' }
const MODE_TONE: Record<Mode, 'neutral' | 'warning' | 'danger'> = { off: 'neutral', shadow: 'warning', enforce: 'danger' }
const KIND_VI: Record<string, string> = {
  would_sign_out: 'Sẽ đăng xuất (theo dõi)',
  signed_out: 'Đã đăng xuất',
  blocked_request: 'Yêu cầu bị chặn',
  shared_would_block: 'Gộp lượt Free — sẽ chặn',
  shared_blocked: 'Gộp lượt Free — đã chặn',
  clone_would_split: 'Mã thiết bị sao chép — sẽ tách theo mạng',
  clone_detected: 'Phát hiện mã thiết bị sao chép',
  self_signed_out: 'Tự đăng xuất',
  readmitted: 'Được nhận lại',
  shared_would_block_family: 'Email biến thể — sẽ chặn',
  shared_blocked_family: 'Email biến thể — đã chặn',
  shared_would_block_network: 'Nhiều tài khoản trên một mạng — sẽ chặn',
  shared_blocked_network: 'Nhiều tài khoản trên một mạng — đã chặn',
}

const fmtEpoch = (s: number) => new Date(s * 1000).toLocaleString('vi-VN')
const toLocalInput = (s: number) => {
  const d = new Date(s * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

const inputCls = 'min-h-11 rounded-card border border-line bg-surface px-2 text-fg outline-none focus:border-line-strong'

function StatusCard({ s }: { s: Settings }) {
  const m = s.effective_mode
  return (
    <Card padding="md" accent={m === 'enforce' ? 'red' : 'green'} as="section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-fg">Chế độ hiện tại</h2>
        <span data-testid="device-links-mode"
          className={cn('inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium', PILL_TONE_STYLE[MODE_TONE[m]])}>
          {MODE_VI[m]}
        </span>
      </div>
      <p className="mt-2 text-xs text-fg-2">Theo dõi = chỉ ghi lại, không chặn ai.</p>
      {s.mode === 'shadow' && s.enforce_at != null && (
        <p className="mt-1 text-xs text-warning-fg">Tự chuyển sang chặn lúc {fmtEpoch(s.enforce_at)}</p>
      )}
      <p className="mt-1 text-xs text-fg-muted">Giới hạn: Free {s.free_max} thiết bị, trả phí {s.paid_max} thiết bị.</p>
    </Card>
  )
}

function Controls({ s, canEdit, onDone }: { s: Settings; canEdit: boolean; onDone: () => void }) {
  const [mode, setMode] = useState<Mode>(s.mode)
  const [at, setAt] = useState(s.enforce_at ? toLocalInput(s.enforce_at) : '')
  const [free, setFree] = useState(String(s.free_max))
  const [paid, setPaid] = useState(String(s.paid_max))
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => {
    setMode(s.mode); setAt(s.enforce_at ? toLocalInput(s.enforce_at) : '')
    setFree(String(s.free_max)); setPaid(String(s.paid_max))
  }, [s])

  const okInt = (v: string) => Number.isInteger(Number(v)) && Number(v) >= 1 && Number(v) <= 50 && v !== ''
  const atMs = at ? new Date(at).getTime() : null
  const atValid = at === '' || (atMs != null && !Number.isNaN(atMs))
  const valid = okInt(free) && okInt(paid) && atValid

  async function save() {
    setBusy(true); setMsg(null)
    const body: Record<string, unknown> = { mode, free_max: Number(free), paid_max: Number(paid) }
    if (at && atMs != null) body.enforce_at = Math.floor(atMs / 1000)
    else body.clear_enforce_at = true
    try {
      await adminPost('/device-links/settings', body)
      setMsg({ ok: true, text: 'Đã lưu.' })
      onDone()
    } catch (e) {
      setMsg({ ok: false, text: `Không lưu được: ${(e as Error).message}` })
    } finally {
      setBusy(false); setConfirm(false)
    }
  }

  return (
    <Card padding="md" as="section">
      <h2 className="mb-3 text-sm font-semibold text-fg">Cài đặt</h2>
      <form className="grid gap-3 text-xs text-fg-2 sm:grid-cols-2"
        onSubmit={e => { e.preventDefault(); if (mode === 'enforce') setConfirm(true); else void save() }}>
        <label className="flex flex-col gap-1">Chế độ
          <select value={mode} disabled={!canEdit || busy} onChange={e => setMode(e.target.value as Mode)} className={inputCls}>
            <option value="off">Tắt</option>
            <option value="shadow">Theo dõi</option>
            <option value="enforce">Chặn</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">Tự chuyển sang chặn lúc (để trống = không tự chuyển)
          <div className="flex gap-2">
            <input type="datetime-local" value={at} disabled={!canEdit || busy} onChange={e => setAt(e.target.value)}
              aria-invalid={!atValid} className={cn(inputCls, 'min-w-0 flex-1')} />
            {at && <Button type="button" size="md" className="min-h-11" disabled={!canEdit || busy} onClick={() => setAt('')}>Không tự chuyển</Button>}
          </div>
        </label>
        <label className="flex flex-col gap-1">Số thiết bị tối đa — Free (1–50)
          <input type="number" min={1} max={50} value={free} disabled={!canEdit || busy}
            onChange={e => setFree(e.target.value)} aria-invalid={!okInt(free)} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1">Số thiết bị tối đa — trả phí (1–50)
          <input type="number" min={1} max={50} value={paid} disabled={!canEdit || busy}
            onChange={e => setPaid(e.target.value)} aria-invalid={!okInt(paid)} className={inputCls} />
        </label>
        <div className="sm:col-span-2">
          {confirm ? (
            <div role="alertdialog" aria-label="Xác nhận bật chặn"
              className="rounded-card border border-warning/30 bg-warning-soft p-3 text-fg">
              <p className="font-medium">Bật chế độ chặn: tài khoản vượt giới hạn thiết bị sẽ bị đăng xuất. Tiếp tục?</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button type="button" variant="danger" size="md" className="min-h-11" disabled={busy} onClick={() => void save()}>
                  {busy ? 'Đang lưu…' : 'Xác nhận'}
                </Button>
                <Button type="button" size="md" className="min-h-11" disabled={busy} onClick={() => setConfirm(false)}>Huỷ</Button>
              </div>
            </div>
          ) : (
            <Button type="submit" variant="primary" size="md" className="min-h-11" disabled={!canEdit || busy || !valid}>
              {busy ? 'Đang lưu…' : 'Lưu'}
            </Button>
          )}
          {!canEdit && <p className="mt-2 text-fg-muted">Cần quyền admin để đổi cài đặt.</p>}
          {!valid && <p className="mt-2 text-danger">Nhập số nguyên từ 1 đến 50 và thời điểm hợp lệ.</p>}
          {msg && <p role="status" className={cn('mt-2', msg.ok ? 'text-success' : 'text-danger')}>{msg.text}</p>}
        </div>
      </form>
    </Card>
  )
}

const COLS: { key: keyof Omit<DayRow, 'day'>; label: string }[] = [
  { key: 'would_sign_out', label: 'Sẽ đăng xuất (theo dõi)' },
  { key: 'signed_out', label: 'Đã đăng xuất' },
  { key: 'blocked_requests', label: 'Lượt bị chặn' },
  { key: 'shared_would_block', label: 'Gộp lượt Free — sẽ chặn' },
  { key: 'shared_blocked', label: 'Gộp lượt Free — đã chặn' },
  { key: 'no_device_would_count', label: 'Không gửi mã thiết bị' },
  { key: 'clone_would_split', label: 'Mã sao chép — sẽ tách' },
  { key: 'clone_detected', label: 'Mã sao chép — phát hiện' },
  { key: 'shared_would_block_family', label: 'Email biến thể — sẽ chặn' },
  { key: 'shared_blocked_family', label: 'Email biến thể — đã chặn' },
  { key: 'shared_would_block_network', label: 'Cùng mạng — sẽ chặn' },
  { key: 'shared_blocked_network', label: 'Cùng mạng — đã chặn' },
]

function ExtraCard({ s, canEdit, onDone }: { s: Settings; canEdit: boolean; onDone: () => void }) {
  const em: Mode = s.extra_mode ?? 'off'
  const cloneN = s.clone_networks ?? 3
  const netFree = s.net_free_accounts ?? 3
  const [at, setAt] = useState(s.extra_enforce_at ? toLocalInput(s.extra_enforce_at) : '')
  const [net, setNet] = useState(String(netFree))
  const [clone, setClone] = useState(String(cloneN))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => {
    setAt(s.extra_enforce_at ? toLocalInput(s.extra_enforce_at) : '')
    setNet(String(s.net_free_accounts ?? 3)); setClone(String(s.clone_networks ?? 3))
  }, [s])

  const inRange = (v: string, lo: number, hi: number) => v !== '' && Number.isInteger(Number(v)) && Number(v) >= lo && Number(v) <= hi
  const atMs = at ? new Date(at).getTime() : null
  const atValid = at === '' || (atMs != null && !Number.isNaN(atMs))
  const valid = inRange(net, 1, 100) && inRange(clone, 2, 50) && atValid

  async function post(body: Record<string, unknown>, okText: string) {
    setBusy(true); setMsg(null)
    try {
      await adminPost('/device-links/settings', body)
      setMsg({ ok: true, text: okText })
      onDone()
    } catch (e) {
      setMsg({ ok: false, text: `Không lưu được: ${(e as Error).message}` })
    } finally {
      setBusy(false)
    }
  }
  function save() {
    const body: Record<string, unknown> = { net_free_accounts: Number(net), clone_networks: Number(clone) }
    if (at && atMs != null) body.extra_enforce_at = Math.floor(atMs / 1000)
    void post(body, 'Đã lưu.')
  }

  return (
    <Card padding="md" accent={em === 'enforce' ? 'red' : 'green'} as="section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-fg">Chống lách</h2>
        <span data-testid="device-links-extra-mode"
          className={cn('inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium', PILL_TONE_STYLE[MODE_TONE[em]])}>
          {MODE_VI[em]}
        </span>
      </div>
      <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-fg-2">
        <li>Mã thiết bị bị sao chép: một mã thấy trên quá {cloneN} mạng trong 1 giờ thì mỗi mạng tính là một thiết bị.</li>
        <li>Không gửi mã thiết bị: tính là một thiết bị theo mạng.</li>
        <li>Email biến thể (ten+1@gmail.com, t.e.n@gmail.com): dùng chung lượt Free.</li>
        <li>Quá {netFree} tài khoản Free trên một mạng/ngày: các tài khoản thêm dùng chung lượt Free.</li>
      </ul>
      {em === 'shadow' && s.extra_enforce_at != null && (
        <p className="mt-2 text-xs text-warning-fg">Tự chuyển sang chặn lúc {fmtEpoch(s.extra_enforce_at)}</p>
      )}
      <p className="mt-1 text-xs text-fg-muted">Chỉ chặn khi Liên kết thiết bị cũng đang chặn.</p>

      <form className="mt-4 grid gap-3 text-xs text-fg-2 sm:grid-cols-2"
        onSubmit={e => { e.preventDefault(); save() }}>
        <label className="flex flex-col gap-1 sm:col-span-2">Tự chuyển sang chặn lúc (để trống = không tự chuyển)
          <input type="datetime-local" value={at} disabled={!canEdit || busy} onChange={e => setAt(e.target.value)}
            aria-invalid={!atValid} className={cn(inputCls, 'min-w-0')} />
        </label>
        <div className="sm:col-span-2">
          <Button type="button" size="md" className="min-h-11" disabled={!canEdit || busy}
            onClick={() => void post({ clear_extra_enforce_at: true }, 'Đã chuyển sang chặn theo thiết bị.')}>
            Chặn ngay khi thiết bị đang chặn
          </Button>
        </div>
        <label className="flex flex-col gap-1">Số tài khoản Free tối đa trên một mạng/ngày (1–100)
          <input type="number" min={1} max={100} value={net} disabled={!canEdit || busy}
            onChange={e => setNet(e.target.value)} aria-invalid={!inRange(net, 1, 100)} className={inputCls} />
        </label>
        <label className="flex flex-col gap-1">Số mạng để coi mã thiết bị là sao chép (2–50)
          <input type="number" min={2} max={50} value={clone} disabled={!canEdit || busy}
            onChange={e => setClone(e.target.value)} aria-invalid={!inRange(clone, 2, 50)} className={inputCls} />
        </label>
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" size="md" className="min-h-11" disabled={!canEdit || busy || !valid}>
            {busy ? 'Đang lưu…' : 'Lưu'}
          </Button>
          {!canEdit && <p className="mt-2 text-fg-muted">Cần quyền admin để đổi cài đặt.</p>}
          {!valid && <p className="mt-2 text-danger">Nhập số tài khoản 1–100, số mạng 2–50 và thời điểm hợp lệ.</p>}
          {msg && <p role="status" className={cn('mt-2', msg.ok ? 'text-success' : 'text-danger')}>{msg.text}</p>}
        </div>
      </form>
    </Card>
  )
}

function UserLookup() {
  const [uid, setUid] = useState('')
  const [busy, setBusy] = useState(false)
  const [res, setRes] = useState<UserDevices | null>(null)
  const [err, setErr] = useState('')

  async function look() {
    const id = uid.trim()
    if (!id) return
    setBusy(true); setErr(''); setRes(null)
    try {
      setRes(await adminFetch<UserDevices>(`/device-links/user/${encodeURIComponent(id)}`))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card padding="md" as="section">
      <h2 className="mb-2 text-sm font-semibold text-fg">Tra cứu thiết bị của tài khoản</h2>
      <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); void look() }}>
        <input value={uid} onChange={e => setUid(e.target.value)} placeholder="User id" aria-label="User id"
          className={cn(inputCls, 'min-w-0 flex-1')} />
        <Button type="submit" variant="primary" size="md" className="min-h-11" disabled={busy || !uid.trim()}>
          {busy ? 'Đang tra…' : 'Tra cứu'}
        </Button>
      </form>
      {err && <p role="status" className="mt-2 text-xs text-danger">{err}</p>}
      {res && (
        res.devices.length === 0 ? (
          <p className="mt-3 text-xs text-fg-muted">Tài khoản {res.user_id} chưa có thiết bị nào.</p>
        ) : (
          <ul className="mt-3 divide-y divide-line text-xs text-fg-2">
            {res.devices.map(d => (
              <li key={d.device} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="font-mono text-fg">{d.device}</span>
                <span>{d.last_seen ? `Lần cuối: ${fmtEpoch(d.last_seen)}` : 'Chưa rõ lần cuối'}</span>
                {d.signed_out && (
                  <span className="text-danger">Đã bị đăng xuất{d.signed_out_at ? ` lúc ${fmtEpoch(d.signed_out_at)}` : ''}</span>
                )}
              </li>
            ))}
          </ul>
        )
      )}
    </Card>
  )
}

export default function DeviceLinksPage() {
  const { hasRole } = useAdminAuth()
  const canEdit = hasRole('admin')
  const [data, setData] = useState<Overview | null>(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await adminFetch<Overview>('/device-links'))
      setErr('')
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-6">
      <PageHeader
        title="Liên kết thiết bị"
        description="Giới hạn số thiết bị đăng nhập cùng lúc trên mỗi tài khoản. Nên để chế độ Theo dõi một thời gian trước khi bật chặn."
        actions={<Button variant="secondary" size="sm" onClick={load}>Làm mới</Button>}
      />
      {err && <ErrorState compact title="Không tải được dữ liệu liên kết thiết bị" message={err} onRetry={load} />}
      {!data && !err && <BlockSkeleton height="h-40" />}

      {data && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <StatusCard s={data.settings} />
            <Controls s={data.settings} canEdit={canEdit} onDone={load} />
          </div>
          <ExtraCard s={data.settings} canEdit={canEdit} onDone={load} />

          <Card padding="md" as="section">
            <h2 className="mb-2 text-sm font-semibold text-fg">7 ngày gần nhất</h2>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1100px] text-left text-xs">
                <thead className="text-fg-muted">
                  <tr>
                    <th className="py-2 pr-3 font-medium">Ngày</th>
                    {COLS.map(c => <th key={c.key} className="py-2 pr-3 text-right font-medium">{c.label}</th>)}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line text-fg-2">
                  {(data.per_day ?? []).map(r => (
                    <tr key={r.day}>
                      <td className="py-2 pr-3 text-fg">{r.day}</td>
                      {COLS.map(c => <td key={c.key} className="py-2 pr-3 text-right tabular-nums">{r[c.key] ?? 0}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card padding="md" as="section">
              <h2 className="mb-2 text-sm font-semibold text-fg">Sự kiện gần đây</h2>
              {(data.events ?? []).length === 0 ? (
                <p className="text-xs text-fg-muted">Chưa có sự kiện.</p>
              ) : (
                <ul className="max-h-96 divide-y divide-line overflow-y-auto text-xs text-fg-2">
                  {(data.events ?? []).map((ev, i) => (
                    <li key={`${ev.t}-${i}`} className="space-y-0.5 py-2">
                      <p className="font-medium text-fg">{KIND_VI[ev.kind] ?? ev.kind}</p>
                      <p className="text-fg-muted">{fmtEpoch(ev.t)}</p>
                      <p className="break-all">
                        Tài khoản {ev.uid} · thiết bị <span className="font-mono">{ev.dev}</span>
                        {ev.by ? <> · bởi thiết bị <span className="font-mono">{ev.by}</span></> : null}
                        {ev.platform ? ` · ${ev.platform}` : ''}
                        {ev.networks != null && ev.networks !== '' ? ` · mạng: ${Array.isArray(ev.networks) ? ev.networks.join(', ') : ev.networks}` : ''}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card padding="md" as="section">
              <h2 className="mb-1 text-sm font-semibold text-fg">Thiết bị dùng nhiều tài khoản</h2>
              <p className="mb-2 text-xs text-fg-muted">Tổng: {data.multi_account_device_count} thiết bị.</p>
              {data.multi_account_devices.length === 0 ? (
                <p className="text-xs text-fg-muted">Không có thiết bị nào.</p>
              ) : (
                <ul className="max-h-96 divide-y divide-line overflow-y-auto text-xs text-fg-2">
                  {data.multi_account_devices.map(d => (
                    <li key={d.device} className="flex items-center justify-between gap-2 py-2">
                      <span className="font-mono text-fg">{d.device}</span>
                      <span>{d.accounts_7d} tài khoản / 7 ngày</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <UserLookup />
        </>
      )}
    </div>
  )
}
