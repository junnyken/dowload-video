import { useEffect, useRef, useState, useCallback, type ReactNode } from 'react'
import { adminFetch } from '../utils/adminFetch'
import { cn } from '../utils/cn'
import { Button } from './Button'

// Shared UI for the Partner API (vgp_) admin flows: modal shell, form fields,
// raw-key reveal and the key limits form (used by issue + edit).

export interface PartnerKeyRow {
  id: string
  label: string
  rate_limit_per_min: number
  rate_limit_per_day: number
  ip_allowlist: string[] | null
  expires_at: string | null
  raw_key?: string
}

export const inputCls =
  'w-full rounded-control border border-line bg-surface px-3 py-2 text-xs text-fg placeholder:text-fg-muted outline-none focus:border-line-strong focus:ring-1 focus:ring-line-strong disabled:opacity-50'

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-fg-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-fg-muted">{hint}</span>}
    </label>
  )
}

export function Modal({ title, subtitle, onClose, children, locked }: {
  title: string
  subtitle?: string
  onClose: () => void
  children: ReactNode
  /** true = Esc / backdrop do not close (pending request or one-time secret) */
  locked?: boolean
}) {
  useEffect(() => {
    function onEsc(e: KeyboardEvent) { if (e.key === 'Escape' && !locked) onClose() }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [onClose, locked])

  return (
    <>
      <div className="fixed inset-0 z-40 bg-fg/40 backdrop-blur-sm" onClick={() => { if (!locked) onClose() }} aria-hidden />
      <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-3 sm:items-center sm:p-4">
        <div role="dialog" aria-modal aria-label={title} className="relative w-full max-w-lg rounded-card border border-line bg-surface shadow-2xl">
          <div className="border-b border-line px-5 py-4">
            <h2 className="text-sm font-semibold text-fg">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-fg-muted">{subtitle}</p>}
          </div>
          <div className="px-5 py-4">{children}</div>
        </div>
      </div>
    </>
  )
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null
  return <div role="alert" className="rounded-control border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{message}</div>
}

/**
 * One-time raw key reveal. The secret lives only in the parent's state while this
 * modal is mounted; the parent must clear it in onClose. Never logged or persisted.
 */
export function RawKeyModal({ rawKey, title, onClose }: { rawKey: string; title: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  const [failed, setFailed] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current) }, [])

  function copy() {
    navigator.clipboard.writeText(rawKey).then(
      () => {
        setCopied(true); setFailed(false)
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(() => setCopied(false), 2000)
      },
      () => setFailed(true),
    )
  }

  return (
    <Modal title={title} onClose={onClose} locked>
      <div className="space-y-3">
        {/* wording: BA review */}
        <div className="rounded-control border border-warning/30 bg-warning-soft px-3 py-2 text-xs text-warning">
          Đây là lần duy nhất key được hiển thị. Sau khi đóng cửa sổ này sẽ không xem lại được. Hãy sao chép và gửi cho khách qua kênh an toàn ngay bây giờ.
        </div>
        <code className="block break-all rounded-control border border-line bg-surface-2 px-3 py-2.5 font-mono text-xs text-fg select-all">{rawKey}</code>
        {/* wording: BA review */}
        {failed && <p className="text-xs text-danger">Không sao chép tự động được. Hãy bôi đen key ở trên và sao chép thủ công.</p>}
        <div className="flex flex-wrap justify-end gap-2">
          {/* wording: BA review */}
          <Button onClick={copy}>{copied ? 'Đã sao chép' : 'Sao chép key'}</Button>
          <Button variant="primary" onClick={onClose}>Tôi đã lưu key</Button>
        </div>
      </div>
    </Modal>
  )
}

// ─── Key limits form ──────────────────────────────────────────────────────────

export interface KeyFormValues {
  label: string
  perMin: string
  perDay: string
  ips: string
  expires: string // yyyy-mm-dd or ''
}

export const DEFAULT_KEY_FORM: KeyFormValues = { label: 'API Key', perMin: '60', perDay: '10000', ips: '', expires: '' }

export function keyFormFromRow(k: PartnerKeyRow): KeyFormValues {
  return {
    label: k.label,
    perMin: String(k.rate_limit_per_min),
    perDay: String(k.rate_limit_per_day),
    ips: (k.ip_allowlist ?? []).join('\n'),
    expires: k.expires_at ? k.expires_at.slice(0, 10) : '',
  }
}

function parseIps(text: string): string[] | null {
  const list = text.split(/[\s,]+/).map(s => s.trim()).filter(Boolean)
  return list.length ? list : null
}

/** Returns an error string, or the parsed payload. */
export function buildKeyPayload(v: KeyFormValues): { error: string } | { payload: Record<string, unknown> } {
  const perMin = Number(v.perMin)
  const perDay = Number(v.perDay)
  // wording: BA review
  if (!Number.isInteger(perMin) || perMin < 1 || perMin > 6000) return { error: 'Request/phút phải là số nguyên từ 1 đến 6.000.' }
  if (!Number.isInteger(perDay) || perDay < 1 || perDay > 10_000_000) return { error: 'Job/ngày phải là số nguyên từ 1 đến 10.000.000.' }
  if (!v.label.trim()) return { error: 'Vui lòng nhập nhãn cho key.' }
  return {
    payload: {
      label: v.label.trim(),
      rate_limit_per_min: perMin,
      rate_limit_per_day: perDay,
      ip_allowlist: parseIps(v.ips),
      // end of the chosen day, UTC
      expires_at: v.expires ? `${v.expires}T23:59:59Z` : null,
    },
  }
}

export function KeyFields({ value, onChange, disabled }: { value: KeyFormValues; onChange: (v: KeyFormValues) => void; disabled?: boolean }) {
  const set = (k: keyof KeyFormValues, x: string) => onChange({ ...value, [k]: x })
  return (
    <div className="space-y-3">
      {/* wording: BA review */}
      <Field label="Nhãn key">
        <input className={inputCls} value={value.label} maxLength={120} disabled={disabled} onChange={e => set('label', e.target.value)} />
      </Field>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Request/phút" hint="Từ 1 đến 6.000. Mặc định 60.">
          <input className={inputCls} type="number" min={1} max={6000} inputMode="numeric" value={value.perMin} disabled={disabled} onChange={e => set('perMin', e.target.value)} />
        </Field>
        <Field label="Job/ngày" hint="Từ 1 đến 10.000.000. Mặc định 10.000.">
          <input className={inputCls} type="number" min={1} max={10000000} inputMode="numeric" value={value.perDay} disabled={disabled} onChange={e => set('perDay', e.target.value)} />
        </Field>
      </div>
      <Field label="IP được phép gọi" hint="Mỗi dòng một địa chỉ IP (không nhận dải CIDR). Để trống = mọi IP đều gọi được.">
        <textarea className={cn(inputCls, 'font-mono')} rows={3} value={value.ips} disabled={disabled} placeholder={'Ví dụ: 203.0.113.10 (để trống = mọi IP)'} onChange={e => set('ips', e.target.value)} />
      </Field>
      <Field label="Ngày hết hạn (tuỳ chọn)" hint="Key hết hạn cuối ngày đã chọn (giờ UTC, tức 06:59 sáng hôm sau giờ Việt Nam). Để trống = không hết hạn.">
        <input className={inputCls} type="date" value={value.expires} disabled={disabled} onChange={e => set('expires', e.target.value)} />
      </Field>
    </div>
  )
}

// ─── Partner Activity & Job History Modal ─────────────────────────────────────

export interface ActivityKeyRow {
  id: string
  tenant_id: string
  key_prefix: string
  label: string
  is_active: boolean
  rate_limit_per_min: number
  rate_limit_per_day: number
  requests_today: number
  requests_this_month: number
  requests_total: number
  last_used_at: string | null
  created_at: string
  rpm?: { now: number; peak_24h: number; refused_24h: number }
  job_counts?: { total: number; done: number; failed: number; queued: number }
}

export interface ActivityJobRow {
  id: string
  tenant_id: string
  api_key_id: string | null
  key_prefix?: string
  key_label?: string
  url: string
  quality: string
  format: string | null
  status: string
  error_message: string | null
  result: { title?: string; file_size_mb?: number; direct_mp4_url?: string } | null
  created_at: string
  updated_at?: string
}

interface ActivityResponse {
  keys: ActivityKeyRow[]
  jobs: ActivityJobRow[]
  summary: { total_jobs: number; done_jobs: number; failed_jobs: number; queued_jobs: number }
}

function fmtRelTime(iso: string | null): string {
  if (!iso) return 'Chưa dùng'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${Math.max(1, diff)} giây trước`
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`
  return `${Math.floor(diff / 86400)} ngày trước`
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleString('vi-VN', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export function PartnerActivityModal({
  tenantId,
  keyId,
  title,
  subtitle,
  onClose,
}: {
  tenantId?: string
  keyId?: string
  title: string
  subtitle?: string
  onClose: () => void
}) {
  const [data, setData] = useState<ActivityResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [selectedKeyId, setSelectedKeyId] = useState<string>(keyId ?? '')
  const [statusFilter, setStatusFilter] = useState<string>('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit: '100' })
      if (tenantId) params.set('tenant_id', tenantId)
      if (keyId) params.set('key_id', keyId)
      const res = await adminFetch<ActivityResponse>(`/enterprise/activity?${params}`)
      setData(res)
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [tenantId, keyId])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    function onEsc(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [onClose])

  const filteredJobs = (data?.jobs ?? []).filter(j => {
    if (selectedKeyId && j.api_key_id !== selectedKeyId) return false
    if (statusFilter && j.status !== statusFilter) return false
    return true
  })

  return (
    <>
      <div className="fixed inset-0 z-40 bg-fg/40 backdrop-blur-sm" onClick={onClose} aria-hidden />
      <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-3 sm:items-center sm:p-4">
        <div role="dialog" aria-modal aria-label={title} className="relative w-full max-w-4xl rounded-card border border-line bg-surface shadow-2xl">
          <div className="flex items-center justify-between border-b border-line px-5 py-4">
            <div>
              <h2 className="text-sm font-semibold text-fg">{title}</h2>
              {subtitle && <p className="mt-0.5 text-xs text-fg-muted">{subtitle}</p>}
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={load}>↺ Làm mới</Button>
              <Button size="sm" onClick={onClose}>Đóng</Button>
            </div>
          </div>

          <div className="max-h-[80vh] overflow-y-auto px-5 py-4 space-y-5">
            <FormError message={err} />

            {loading ? (
              <div className="py-12 text-center font-mono text-xs text-fg-muted">Đang tải dữ liệu hoạt động…</div>
            ) : data ? (
              <>
                {/* 1. Keys table for this account */}
                <div>
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="font-mono text-xs font-semibold uppercase text-fg-muted">
                      Danh sách API Key ({data.keys.length})
                    </h3>
                    {selectedKeyId && !keyId && (
                      <button
                        onClick={() => setSelectedKeyId('')}
                        className="text-xs text-accent hover:underline"
                      >
                        Xem tất cả key
                      </button>
                    )}
                  </div>
                  {data.keys.length === 0 ? (
                    <p className="rounded border border-line bg-surface-2 px-3 py-4 text-center text-xs text-fg-muted">
                      Tài khoản này chưa được cấp API key nào.
                    </p>
                  ) : (
                    <div className="overflow-x-auto rounded-control border border-line">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-line bg-surface-2 font-mono text-fg-muted">
                            <th className="px-3 py-2">Key</th>
                            <th className="px-3 py-2">Trạng thái</th>
                            <th className="px-3 py-2">Lượt gọi API</th>
                            <th className="px-3 py-2">Job tải video</th>
                            <th className="px-3 py-2">Request/phút (24h)</th>
                            <th className="px-3 py-2">Dùng lần cuối</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.keys.map(k => {
                            const jc = k.job_counts ?? { total: 0, done: 0, failed: 0, queued: 0 }
                            const r = k.rpm ?? { now: 0, peak_24h: 0, refused_24h: 0 }
                            const isSelected = selectedKeyId === k.id
                            return (
                              <tr
                                key={k.id}
                                onClick={() => { if (!keyId) setSelectedKeyId(isSelected ? '' : k.id) }}
                                className={cn(
                                  'border-b border-line transition-colors',
                                  !keyId && 'cursor-pointer hover:bg-surface-2',
                                  isSelected && 'bg-surface-2',
                                )}
                              >
                                <td className="px-3 py-2">
                                  <div className="font-mono font-medium text-fg">{k.key_prefix}…</div>
                                  <div className="text-[10px] text-fg-muted">{k.label}</div>
                                </td>
                                <td className="px-3 py-2">
                                  <span className={cn(
                                    'rounded px-1.5 py-0.5 text-[10px] font-semibold',
                                    k.is_active ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger',
                                  )}>
                                    {k.is_active ? 'Đang bật' : 'Đã tắt'}
                                  </span>
                                </td>
                                <td className="px-3 py-2 font-mono text-fg-2">
                                  <div>Hôm nay: <strong>{k.requests_today.toLocaleString('vi-VN')}</strong></div>
                                  <div className="text-[11px] text-fg-muted">
                                    Tháng: {k.requests_this_month.toLocaleString('vi-VN')} · Tổng: {k.requests_total.toLocaleString('vi-VN')}
                                  </div>
                                </td>
                                <td className="px-3 py-2 font-mono">
                                  <span className="text-success">{jc.done} xong</span>
                                  {' · '}
                                  <span className={jc.failed > 0 ? 'text-danger font-semibold' : 'text-fg-muted'}>{jc.failed} lỗi</span>
                                  {jc.queued > 0 && (
                                    <>
                                      {' · '}
                                      <span className="text-warning">{jc.queued} chờ</span>
                                    </>
                                  )}
                                </td>
                                <td className="px-3 py-2 font-mono text-[11px] text-fg-muted">
                                  <div>Hiện tại: {r.now} · Đỉnh 24h: {r.peak_24h}/{k.rate_limit_per_min}</div>
                                  {r.refused_24h > 0 && (
                                    <div className="text-warning font-semibold">Từ chối (429): {r.refused_24h}</div>
                                  )}
                                </td>
                                <td className="px-3 py-2 font-mono text-fg-muted">
                                  {fmtRelTime(k.last_used_at)}
                                </td>
                              </tr>
                            )
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* 2. Job history table */}
                <div>
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-3">
                      <h3 className="font-mono text-xs font-semibold uppercase text-fg-muted">
                        Lịch sử Job gần nhất ({filteredJobs.length})
                      </h3>
                      <div className="flex gap-1.5 text-[11px] font-mono">
                        <span className="rounded bg-surface-2 px-2 py-0.5 text-fg-2">
                          Tổng: {data.summary.total_jobs}
                        </span>
                        <span className="rounded bg-success-soft px-2 py-0.5 text-success">
                          Xong: {data.summary.done_jobs}
                        </span>
                        <span className="rounded bg-danger-soft px-2 py-0.5 text-danger">
                          Lỗi: {data.summary.failed_jobs}
                        </span>
                        {data.summary.queued_jobs > 0 && (
                          <span className="rounded bg-warning-soft px-2 py-0.5 text-warning">
                            Đang xử lý: {data.summary.queued_jobs}
                          </span>
                        )}
                      </div>
                    </div>

                    <select
                      value={statusFilter}
                      onChange={e => setStatusFilter(e.target.value)}
                      className="rounded border border-line bg-surface px-2 py-1 text-xs text-fg-2"
                    >
                      <option value="">Mọi trạng thái</option>
                      <option value="done">Thành công (done)</option>
                      <option value="failed">Thất bại (failed)</option>
                      <option value="queued">Đang xử lý (queued)</option>
                    </select>
                  </div>

                  {filteredJobs.length === 0 ? (
                    <p className="rounded border border-line bg-surface-2 px-3 py-6 text-center font-mono text-xs text-fg-muted">
                      Chưa có job nào được gọi từ {selectedKeyId ? 'key này' : 'tài khoản này'}.
                    </p>
                  ) : (
                    <div className="overflow-x-auto rounded-control border border-line">
                      <table className="w-full text-left text-xs">
                        <thead>
                          <tr className="border-b border-line bg-surface-2 font-mono text-fg-muted">
                            <th className="px-3 py-2">Thời gian</th>
                            <th className="px-3 py-2">Key đã dùng</th>
                            <th className="px-3 py-2">URL & Tiêu đề video</th>
                            <th className="px-3 py-2">Chất lượng</th>
                            <th className="px-3 py-2">Trạng thái</th>
                            <th className="px-3 py-2">Kết quả / Lỗi</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredJobs.map(j => (
                            <tr key={j.id} className="border-b border-line hover:bg-surface-2">
                              <td className="px-3 py-2 font-mono text-[11px] text-fg-muted whitespace-nowrap">
                                {fmtDateTime(j.created_at)}
                              </td>
                              <td className="px-3 py-2 font-mono text-[11px]">
                                {j.key_prefix ? (
                                  <>
                                    <div className="text-fg-2">{j.key_prefix}…</div>
                                    <div className="text-[10px] text-fg-muted">{j.key_label}</div>
                                  </>
                                ) : (
                                  <span className="text-fg-muted">{j.api_key_id ? `${j.api_key_id.slice(0, 8)}…` : '—'}</span>
                                )}
                              </td>
                              <td className="px-3 py-2 max-w-xs">
                                <a
                                  href={j.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="block truncate font-mono text-[11px] text-accent hover:underline"
                                  title={j.url}
                                >
                                  {j.url}
                                </a>
                                {j.result?.title && (
                                  <div className="truncate text-[11px] text-fg-muted" title={j.result.title}>
                                    {j.result.title}
                                  </div>
                                )}
                              </td>
                              <td className="px-3 py-2 font-mono text-[11px] text-fg-muted">
                                {j.quality}{j.format ? ` (${j.format})` : ''}
                              </td>
                              <td className="px-3 py-2">
                                <span className={cn(
                                  'rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase',
                                  j.status === 'done'
                                    ? 'bg-success-soft text-success'
                                    : j.status === 'failed'
                                    ? 'bg-danger-soft text-danger'
                                    : 'bg-warning-soft text-warning',
                                )}>
                                  {j.status === 'done' ? 'Thành công' : j.status === 'failed' ? 'Lỗi' : j.status}
                                </span>
                              </td>
                              <td className="px-3 py-2 max-w-xs text-[11px]">
                                {j.status === 'failed' ? (
                                  <span className="text-danger break-words">{j.error_message ?? 'Lỗi tải video'}</span>
                                ) : j.status === 'done' ? (
                                  <span className="font-mono text-fg-muted">
                                    {j.result?.file_size_mb ? `${j.result.file_size_mb.toFixed(1)} MB` : 'Hoàn tất'}
                                  </span>
                                ) : (
                                  <span className="text-fg-muted">Đang xử lý…</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </>
  )
}
