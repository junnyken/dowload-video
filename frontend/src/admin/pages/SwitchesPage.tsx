import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
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
 * Công tắc khẩn cấp (task #6332 follow-up). Backend: app/api/admin_switches.py.
 * Every switch can be flipped here without a redeploy; the env value only
 * applies when no admin override is set ("Về mặc định" removes the override).
 */
// wording: BA review

type State = 'on' | 'off'
type Action = State | 'clear'

interface LastChange { at: string; by: string; action: string; old: unknown; new: unknown }
/** overload_guard only (task #6397): the per-process limit next to the switch. */
interface Threshold {
  label: string
  help: string
  value: number
  default: number
  source: 'admin' | 'default'
  min: number
  max: number
  readable: boolean
  now: { inflight: number; threads: number }
  retry_after: number
  rejected_today: number | null
}
interface Switch {
  key: string
  label: string
  on_label: string
  off_label: string
  danger_state: State
  description: string
  user_impact: string
  env_name: string | null
  kind: 'override' | 'china'
  state: State
  source: 'admin' | 'env' | 'default'
  env_state: State | null
  override: State | null
  readable: boolean
  env_forced: boolean
  last_change: LastChange | null
  threshold?: Threshold
}

const SOURCE_VI: Record<Switch['source'], string> = {
  admin: 'Admin đặt trên trang này',
  env: 'Biến môi trường máy chủ',
  default: 'Mặc định',
}

const fmtTime = (iso: string) => {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('vi-VN')
}

function actionText(s: Switch, a: Action): string {
  if (a === 'clear') {
    const envLabel = s.env_state === 'on' ? s.on_label : s.off_label
    return `Bỏ giá trị admin, quay về theo biến môi trường (${s.env_name ?? '—'}): ${envLabel.toLowerCase()}.`
  }
  return `Chuyển sang: ${(a === 'on' ? s.on_label : s.off_label).toLowerCase()}.`
}

function ThresholdBox({ t, canEdit, onDone }: { t: Threshold; canEdit: boolean; onDone: () => void }) {
  const [val, setVal] = useState(String(t.value))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => { setVal(String(t.value)) }, [t.value])

  const n = Number(val)
  const valid = Number.isInteger(n) && n >= t.min && n <= t.max

  async function save(limit: number | null) {
    setBusy(true); setMsg(null)
    try {
      await adminPost('/switches/overload_guard/limit', { limit })
      setMsg({ ok: true, text: 'Đã lưu ngưỡng. Có hiệu lực trong vài giây.' })
      onDone()
    } catch (e) {
      setMsg({ ok: false, text: `Không lưu được: ${(e as Error).message}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 rounded-card border border-line p-3 text-xs" data-testid="overload-threshold">
      <p className="font-medium text-fg">{t.label}</p>
      <p className="mt-1 text-fg-2">{t.help}</p>
      <p className="mt-2 text-fg-muted">
        Tiến trình vừa trả lời trang này: {t.now.inflight} yêu cầu nặng đang chạy, {t.now.threads} luồng trích xuất còn sống.
        {' '}Báo bận hôm nay: {t.rejected_today ?? '—'} lượt. Người dùng được mời thử lại sau {t.retry_after} giây.
      </p>
      {canEdit && t.readable ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-fg-2">
            Ngưỡng
            <input type="number" min={t.min} max={t.max} value={val} disabled={busy}
              onChange={e => setVal(e.target.value)}
              aria-invalid={!valid}
              className="min-h-11 w-24 rounded-card border border-line bg-surface px-2 text-fg outline-none focus:border-line-strong" />
          </label>
          <Button variant="primary" size="md" className="min-h-11" disabled={busy || !valid || n === t.value}
            onClick={() => save(n)}>{busy ? 'Đang lưu…' : 'Lưu ngưỡng'}</Button>
          {t.source === 'admin' && (
            <Button size="md" className="min-h-11" disabled={busy} onClick={() => save(null)}>
              Về mặc định ({t.default})
            </Button>
          )}
          {!valid && <span className="text-danger">Nhập số nguyên từ {t.min} đến {t.max}.</span>}
        </div>
      ) : (
        <p className="mt-2 text-fg-muted">Ngưỡng hiện tại: {t.value}{t.source === 'default' ? ' (mặc định)' : ''}.</p>
      )}
      {msg && <p role="status" className={`mt-2 ${msg.ok ? 'text-success' : 'text-danger'}`}>{msg.text}</p>}
    </div>
  )
}

function SwitchCard({ s, canEdit, onDone }: { s: Switch; canEdit: boolean; onDone: () => void }) {
  const [pending, setPending] = useState<Action | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const danger = s.state === s.danger_state
  const target: State = s.state === 'on' ? 'off' : 'on'
  // China switches have no separate "admin said off": off = remove the key.
  const canClear = s.kind === 'override' && s.override != null
  const locked = !s.readable || (s.env_forced && target === 'off')

  async function apply(a: Action) {
    setBusy(true); setMsg(null)
    try {
      await adminPost(`/switches/${s.key}`, { state: a })
      setMsg({ ok: true, text: 'Đã lưu. Có hiệu lực trong vài giây, không cần deploy lại.' })
      onDone()
    } catch (e) {
      setMsg({ ok: false, text: `Không lưu được: ${(e as Error).message}` })
    } finally {
      setBusy(false); setPending(null)
    }
  }

  return (
    <Card padding="md" accent={danger ? 'red' : 'green'} as="section">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-fg">{s.label}</h2>
          <p className="mt-1 text-xs leading-relaxed text-fg-2">{s.description}</p>
        </div>
        <span
          data-testid={`switch-state-${s.key}`}
          className={cn('inline-flex shrink-0 items-center rounded-full border px-2.5 py-0.5 text-xs font-medium',
            PILL_TONE_STYLE[!s.readable ? 'neutral' : danger ? 'danger' : 'success'])}
        >
          {!s.readable ? 'Không đọc được' : s.state === 'on' ? s.on_label : s.off_label}
        </span>
      </div>

      <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
        <dt className="text-fg-muted">Người dùng sẽ thấy</dt>
        <dd className="text-fg-2">{s.user_impact}</dd>
        <dt className="text-fg-muted">Đang theo</dt>
        <dd className="text-fg-2">
          {SOURCE_VI[s.source]}
          {s.env_name && s.source !== 'env' && s.env_state && (
            <span className="text-fg-muted"> · biến {s.env_name} đang là {s.env_state === 'on' ? s.on_label.toLowerCase() : s.off_label.toLowerCase()}</span>
          )}
        </dd>
        <dt className="text-fg-muted">Đổi lần cuối</dt>
        <dd className="text-fg-2">
          {s.last_change ? `${fmtTime(s.last_change.at)} · ${s.last_change.by}` : 'Chưa đổi trên trang này'}
        </dd>
      </dl>

      {s.threshold && <ThresholdBox t={s.threshold} canEdit={canEdit} onDone={onDone} />}

      {s.env_forced && (
        <p className="mt-3 text-xs text-warning-fg">
          Biến môi trường {s.env_name} đang ép công tắc này BẬT, trang admin không tắt được. Cần sửa biến rồi deploy lại.
        </p>
      )}
      {!s.readable && (
        <p className="mt-3 text-xs text-warning-fg">Không đọc được Redis nên chưa thể đổi công tắc này.</p>
      )}

      {canEdit ? (
        pending ? (
          <div role="alertdialog" aria-label="Xác nhận đổi công tắc"
            className="mt-4 rounded-card border border-warning/30 bg-warning-soft p-3 text-xs text-fg">
            <p className="font-medium">{actionText(s, pending)}</p>
            <p className="mt-1 text-fg-2">Thay đổi áp dụng cho toàn hệ thống và được ghi vào Audit Log. Tiếp tục?</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant={pending === s.danger_state ? 'danger' : 'primary'} size="md" className="min-h-11"
                disabled={busy} onClick={() => apply(pending)}>
                {busy ? 'Đang lưu…' : 'Xác nhận'}
              </Button>
              <Button size="md" className="min-h-11" disabled={busy} onClick={() => setPending(null)}>Huỷ</Button>
            </div>
          </div>
        ) : (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button variant={target === s.danger_state ? 'danger' : 'primary'} size="md" className="min-h-11"
              disabled={busy || locked} onClick={() => { setMsg(null); setPending(target) }}>
              {target === 'on' ? `Chuyển: ${s.on_label.toLowerCase()}` : `Chuyển: ${s.off_label.toLowerCase()}`}
            </Button>
            {canClear && (
              <Button size="md" className="min-h-11" disabled={busy || !s.readable}
                onClick={() => { setMsg(null); setPending('clear') }}>
                Về mặc định (theo biến môi trường)
              </Button>
            )}
          </div>
        )
      ) : (
        <p className="mt-3 text-xs text-fg-muted">Cần quyền admin để đổi công tắc.</p>
      )}
      {msg && <p role="status" className={`mt-3 text-xs ${msg.ok ? 'text-success' : 'text-danger'}`}>{msg.text}</p>}
    </Card>
  )
}

export default function SwitchesPage() {
  const { hasRole } = useAdminAuth()
  const canEdit = hasRole('admin')
  const [items, setItems] = useState<Switch[] | null>(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    try {
      const d = await adminFetch<{ switches: Switch[] }>('/switches')
      setItems(d.switches)
      setErr('')
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const main = items?.filter(s => s.kind === 'override' || s.key === 'china_global') ?? []
  const china = items?.filter(s => s.kind === 'china' && s.key !== 'china_global') ?? []

  return (
    <div className="space-y-6">
      <PageHeader
        title="Công tắc khẩn cấp"
        description="Bật/tắt nhanh khi có sự cố, có hiệu lực sau vài giây, không cần deploy lại. Mọi thay đổi được ghi vào Audit Log."
        actions={<Button variant="secondary" size="sm" onClick={load}>Làm mới</Button>}
      />
      {err && <ErrorState compact title="Không tải được danh sách công tắc" message={err} onRetry={load} />}
      {!items && !err && <BlockSkeleton height="h-40" />}

      {items && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            {main.map(s => <SwitchCard key={s.key} s={s} canEdit={canEdit} onDone={load} />)}
          </div>

          {china.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-semibold text-fg">Từng nền tảng Trung Quốc</h2>
              <div className="grid gap-4 lg:grid-cols-2">
                {china.map(s => <SwitchCard key={s.key} s={s} canEdit={canEdit} onDone={load} />)}
              </div>
            </div>
          )}

          <Card padding="md" as="section">
            <h2 className="mb-2 text-sm font-semibold text-fg">Công tắc ở trang khác</h2>
            <ul className="space-y-2 text-xs text-fg-2">
              <li>
                <Link to="/vid-admin/asr" className="text-accent-text underline">Phiên âm (ASR) → Tạm dừng phiên âm</Link>
                {' '}— chặn tạo job phiên âm mới trên toàn hệ thống.
              </li>
              <li>
                <Link to="/vid-admin/desktop-app" className="text-accent-text underline">App Windows → Bản yt-dlp cho app</Link>
                {' '}— tắt thì app quay về bản yt-dlp có sẵn trong bộ cài.
              </li>
            </ul>
          </Card>
        </>
      )}
    </div>
  )
}
