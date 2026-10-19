import { useEffect, useState, useCallback, useRef } from 'react'
import { adminDelete, adminFetch, adminPatch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'
import { Button } from '../shared/Button'
import {
  FormError, KeyFields, Modal, RawKeyModal, buildKeyPayload, keyFormFromRow,
  type KeyFormValues, type PartnerKeyRow,
} from '../shared/PartnerKeyUi'

interface TenantRef {
  name: string
  slug: string
  plan: string
}

interface ApiKey {
  id: string
  tenant_id: string
  key_prefix: string
  label: string
  scopes: string[]
  rate_limit_per_min: number
  rate_limit_per_day: number
  is_active: boolean
  created_by: string | null
  last_used_at: string | null
  requests_today: number
  requests_this_month: number
  requests_total: number
  ip_allowlist: string[] | null
  expires_at: string | null
  created_at: string
  tenants: TenantRef | null
  /** requests/minute: this minute, busiest minute in 24 h, refused (429) in 24 h */
  rpm?: { now: number; peak_24h: number; refused_24h: number }
}

/** Warn when the busiest minute reached 80% of the key's limit or anything was refused. */
function RpmCell({ k }: { k: ApiKey }) {
  const r = k.rpm ?? { now: 0, peak_24h: 0, refused_24h: 0 }
  const limit = k.rate_limit_per_min || 1
  const pct = Math.round((r.peak_24h / limit) * 100)
  const hot = r.refused_24h > 0 || r.peak_24h >= limit * 0.8
  // wording: BA review
  return (
    <div className={cn('font-mono', hot ? 'text-warning' : 'text-fg-muted')}
      title="Số request trong phút hiện tại và phút đông nhất của 24 giờ qua, so với giới hạn request/phút của key">
      <div>Phút này: {r.now.toLocaleString('vi-VN')}</div>
      <div>Cao nhất 24h: {r.peak_24h.toLocaleString('vi-VN')}/{limit.toLocaleString('vi-VN')} ({pct}%)</div>
      {r.refused_24h > 0
        ? <div className="font-semibold">Bị từ chối 24h: {r.refused_24h.toLocaleString('vi-VN')}</div>
        : <div>Bị từ chối 24h: 0</div>}
      {hot && <div className="mt-0.5 text-[10px]">Cân nhắc tăng request/phút</div>}
    </div>
  )
}

interface ApiKeysResponse {
  api_keys: ApiKey[]
  total: number
  total_active: number
  total_requests_today: number
  total_requests_month: number
}

function formatRelative(iso: string | null): string {
  // wording: BA review
  if (!iso) return 'Chưa dùng'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff} giây trước`
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`
  return `${Math.floor(diff / 86400)} ngày trước`
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleCopy(e: React.MouseEvent) {
    e.stopPropagation()
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <button
      onClick={handleCopy}
      title="Sao chép phần đầu key"
      className={cn(
        'ml-1 rounded px-1 py-0.5 font-mono text-[10px] transition-colors',
        copied
          ? 'bg-success-soft text-success'
          : 'bg-surface text-fg-muted hover:bg-surface-2 hover:text-fg-2',
      )}
    >
      {/* wording: BA review */}{copied ? 'Đã chép' : 'Sao chép'}
    </button>
  )
}

const SCOPE_COLORS: Record<string, string> = {
  read: 'bg-surface-2 text-fg-2',
  write: 'bg-success-soft text-success',
  batch: 'bg-surface-2 text-fg-2',
  webhook: 'bg-warning-soft text-warning',
  admin: 'bg-danger-soft text-danger',
}

function RotateModal({ k, onClose, onRotated }: { k: ApiKey; onClose: () => void; onRotated: (raw: string) => void }) {
  const [pending, setPending] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function confirm() {
    setPending(true); setErr(null)
    try {
      const row = await adminPost<PartnerKeyRow>(`/enterprise/api-keys/${k.id}/rotate`)
      if (!row.raw_key) throw new Error('Máy chủ không trả về key mới.')
      onRotated(row.raw_key)
    } catch (e) {
      setErr((e as Error).message)
      setPending(false)
    }
  }

  return (
    // wording: BA review
    <Modal title="Đổi key" subtitle={`${k.key_prefix}… · ${k.label}`} onClose={onClose} locked={pending}>
      <div className="space-y-3">
        <p className="text-sm text-fg-2">
          Key cũ sẽ ngừng hoạt động ngay lập tức: mọi hệ thống của khách đang dùng key này sẽ bị lỗi 401 cho đến khi họ cập nhật sang key mới.
          Key mới chỉ hiển thị một lần.
        </p>
        <FormError message={err} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} disabled={pending}>Huỷ</Button>
          <Button variant="danger" onClick={confirm} disabled={pending}>{pending ? 'Đang đổi…' : 'Đổi key ngay'}</Button>
        </div>
      </div>
    </Modal>
  )
}

function DeleteKeyModal({ k, onClose, onDeleted }: { k: ApiKey; onClose: () => void; onDeleted: () => void }) {
  const [pending, setPending] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function confirm() {
    setPending(true); setErr(null)
    try {
      await adminDelete(`/enterprise/api-keys/${k.id}`)
      onDeleted()
    } catch (e) {
      setErr((e as Error).message)
      setPending(false)
    }
  }

  return (
    // wording: BA review
    <Modal title="Xoá key" subtitle={`${k.key_prefix}… · ${k.label}`} onClose={onClose} locked={pending}>
      <div className="space-y-3">
        <p className="text-sm text-fg-2">
          Key sẽ bị xoá vĩnh viễn. Mọi hệ thống đang dùng key này sẽ nhận lỗi 401 ngay lập tức và không thể hoàn tác.
          Các job đã chạy trước đó vẫn được giữ lại.
        </p>
        <p className="text-xs text-fg-muted">Nếu chỉ muốn tạm dừng key, hãy dùng nút “Tắt key” thay vì xoá.</p>
        <FormError message={err} />
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} disabled={pending}>Huỷ</Button>
          <Button variant="danger" onClick={confirm} disabled={pending}>{pending ? 'Đang xoá…' : 'Xoá key vĩnh viễn'}</Button>
        </div>
      </div>
    </Modal>
  )
}

function EditLimitsModal({ k, onClose, onSaved }: { k: ApiKey; onClose: () => void; onSaved: () => void }) {
  const initial = keyFormFromRow(k)
  const [form, setForm] = useState<KeyFormValues>(initial)
  const [pending, setPending] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const built = buildKeyPayload(form)
    if ('error' in built) { setErr(built.error); return }
    // Only send what the admin changed.
    const body: Record<string, unknown> = {}
    const orig = buildKeyPayload(initial)
    const origPayload = 'payload' in orig ? orig.payload : {}
    for (const [key, val] of Object.entries(built.payload)) {
      if (key === 'expires_at' && form.expires === initial.expires) continue
      if (JSON.stringify(val) !== JSON.stringify(origPayload[key])) body[key] = val
    }
    if (Object.keys(body).length === 0) { onClose(); return }
    setPending(true); setErr(null)
    try {
      await adminPatch(`/enterprise/api-keys/${k.id}`, body)
      onSaved()
    } catch (e2) {
      setErr((e2 as Error).message)
      setPending(false)
    }
  }

  return (
    // wording: BA review
    <Modal title="Sửa giới hạn" subtitle={`${k.key_prefix}… · ${k.tenants?.name ?? ''}`} onClose={onClose} locked={pending}>
      <form onSubmit={submit} className="space-y-3">
        <KeyFields value={form} onChange={setForm} disabled={pending} />
        <FormError message={err} />
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={pending}>Huỷ</Button>
          <Button type="submit" variant="primary" disabled={pending}>{pending ? 'Đang lưu…' : 'Lưu'}</Button>
        </div>
      </form>
    </Modal>
  )
}

export default function ApiKeysPage() {
  const [data, setData] = useState<ApiKeysResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeOnly, setActiveOnly] = useState(false)
  const [search, setSearch] = useState('')
  const [toggling, setToggling] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [rotateKey, setRotateKey] = useState<ApiKey | null>(null)
  const [editKey, setEditKey] = useState<ApiKey | null>(null)
  const [deleteKey, setDeleteKey] = useState<ApiKey | null>(null)
  // One-time secret: lives only while the reveal modal is open.
  const [revealKey, setRevealKey] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (activeOnly) params.set('active_only', 'true')
      const result = await adminFetch<ApiKeysResponse>(`/enterprise/api-keys?${params}`)
      setData(result)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [activeOnly])

  useEffect(() => { fetchData() }, [fetchData])

  async function handleToggle(keyId: string, currentState: boolean) {
    setToggling(p => ({ ...p, [keyId]: true }))
    try {
      await adminPost(`/enterprise/api-keys/${keyId}/toggle`)
      const label = currentState ? 'Đã tắt' : 'Đã bật'
      setMsg(m => ({ ...m, [keyId]: label }))
      setTimeout(() => setMsg(m => { const n = { ...m }; delete n[keyId]; return n }), 2000)
      fetchData()
    } catch (e) {
      setMsg(m => ({ ...m, [keyId]: `Lỗi: ${(e as Error).message}` }))
    } finally {
      setToggling(p => ({ ...p, [keyId]: false }))
    }
  }

  const keys = (data?.api_keys ?? []).filter(k =>
    !search || k.key_prefix.includes(search) || k.label.toLowerCase().includes(search.toLowerCase())
      || k.tenants?.name.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Partner API Keys</h1>
          {/* wording: BA review */}
          <p className="mt-0.5 text-xs text-fg-muted">Key bắt đầu bằng vgp_ để khách Partner gọi API</p>
        </div>
        <button onClick={fetchData} className="px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2">
          ↺ Làm mới
        </button>
      </div>

      {rotateKey && (
        <RotateModal
          k={rotateKey}
          onClose={() => setRotateKey(null)}
          onRotated={raw => { setRotateKey(null); setRevealKey(raw); fetchData() }}
        />
      )}
      {editKey && (
        <EditLimitsModal k={editKey} onClose={() => setEditKey(null)} onSaved={() => { setEditKey(null); fetchData() }} />
      )}
      {deleteKey && (
        <DeleteKeyModal k={deleteKey} onClose={() => setDeleteKey(null)} onDeleted={() => { setDeleteKey(null); fetchData() }} />
      )}
      {revealKey && (
        // wording: BA review
        <RawKeyModal rawKey={revealKey} title="Key mới sau khi đổi" onClose={() => setRevealKey(null)} />
      )}

      {/* Summary */}
      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            // wording: BA review
            { label: 'Tổng số key', value: data.total },
            { label: 'Đang bật', value: data.total_active },
            { label: 'Lượt gọi hôm nay', value: data.total_requests_today.toLocaleString() },
            { label: 'Lượt gọi tháng này', value: data.total_requests_month.toLocaleString() },
          ].map(c => (
            <div key={c.label} className="rounded-card border border-line bg-surface shadow-card p-4">
              <p className="font-mono text-xs text-fg-muted">{c.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-fg">{c.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-2">
        <input
          type="text"
          placeholder="Tìm theo key, nhãn, khách…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="rounded border border-line bg-surface px-3 py-1.5 text-xs text-fg-2 placeholder:text-fg-muted w-52"
        />
        <label className="flex items-center gap-1.5 text-xs text-fg-muted cursor-pointer">
          <input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />
          Chỉ key đang bật
        </label>
      </div>

      {error && (
        <div className="rounded border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>
      )}

      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-fg-muted">Đang tải danh sách key…</div>
      ) : (
        <div className="overflow-x-auto rounded-control border border-line">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-line bg-surface-2">
                {['Key', 'Khách', 'Quyền', 'IP được phép', 'Người tạo', 'Lượt gọi', 'Giới hạn', 'Request/phút', 'Dùng lần cuối', 'Hết hạn', 'Trạng thái'].map(h => (
                  <th key={h} className="px-3 py-2.5 font-mono font-semibold text-fg-muted">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {keys.map(k => (
                <tr key={k.id} className="border-b border-line hover:bg-surface-2">
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-0.5">
                      <p className="font-mono font-medium text-fg-2">{k.key_prefix}…</p>
                      <CopyButton text={k.key_prefix} />
                    </div>
                    <p className="text-[10px] text-fg-muted">{k.label}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <p className="text-fg-2">{k.tenants?.name ?? '—'}</p>
                    <span className={cn('inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase mt-0.5',
                      k.tenants?.plan === 'enterprise' ? 'bg-accent-hover text-fg-2'
                      : k.tenants?.plan === 'growth' ? 'bg-accent-hover text-fg-2'
                      : 'bg-surface-2 text-fg-2')}>
                      {k.tenants?.plan ?? '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap gap-1">
                      {(k.scopes ?? []).map(s => (
                        <span key={s} className={cn('rounded px-1.5 py-0.5 text-[10px] font-semibold', SCOPE_COLORS[s] ?? 'bg-surface-2 text-fg-2')}>
                          {s}
                        </span>
                      ))}
                    </div>
                  </td>
                  {/* IP Allowlist */}
                  <td className="px-3 py-2.5">
                    {(!k.ip_allowlist || k.ip_allowlist.length === 0) ? (
                      <span className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-fg-muted">Mọi IP</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {k.ip_allowlist.map(ip => (
                          <span key={ip} className="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-fg-2">
                            {ip}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  {/* Created By */}
                  <td className="px-3 py-2.5 font-mono text-[11px] text-fg-muted">
                    {k.created_by
                      ? <span title={k.created_by}>{k.created_by.slice(0, 8)}…</span>
                      : <span className="text-fg-muted">—</span>
                    }
                  </td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">
                    <div>Hôm nay: {k.requests_today.toLocaleString()}</div>
                    <div className="text-fg-muted">Tháng: {k.requests_this_month.toLocaleString()}</div>
                    <div className="text-fg-muted">Tổng: {k.requests_total.toLocaleString()}</div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">
                    <div>{k.rate_limit_per_min}/phút</div>
                    <div>{k.rate_limit_per_day.toLocaleString('vi-VN')}/ngày</div>
                  </td>
                  <td className="px-3 py-2.5"><RpmCell k={k} /></td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">{formatRelative(k.last_used_at)}</td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">{formatDate(k.expires_at)}</td>
                  <td className="px-3 py-2.5">
                    <button
                      disabled={toggling[k.id]}
                      onClick={() => handleToggle(k.id, k.is_active)}
                      className={cn(
                        'rounded px-2 py-1 text-[10px] font-semibold transition-colors disabled:opacity-40',
                        k.is_active
                          ? 'bg-danger-soft text-danger hover:bg-danger/20'
                          : 'bg-success-soft text-success hover:bg-success/20',
                      )}
                    >
                      {toggling[k.id] ? '…' : k.is_active ? 'Tắt key' : 'Bật key'}
                    </button>
                    {/* wording: BA review */}
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      <button
                        onClick={() => setEditKey(k)}
                        className="rounded border border-line bg-surface px-2 py-1 text-[10px] font-medium text-fg hover:border-line-strong hover:bg-surface-2"
                      >
                        Sửa giới hạn
                      </button>
                      <button
                        onClick={() => setRotateKey(k)}
                        className="rounded border border-line bg-surface px-2 py-1 text-[10px] font-medium text-fg hover:border-line-strong hover:bg-surface-2"
                      >
                        Đổi key
                      </button>
                      <button
                        onClick={() => setDeleteKey(k)}
                        className="rounded border border-danger/30 bg-danger-soft px-2 py-1 text-[10px] font-medium text-danger hover:border-danger/60"
                      >
                        Xoá key
                      </button>
                    </div>
                    {msg[k.id] && (
                      <p className={cn('mt-1 font-mono text-[10px]', msg[k.id].startsWith('Lỗi') ? 'text-danger' : 'text-success')}>{msg[k.id]}</p>
                    )}
                  </td>
                </tr>
              ))}
              {keys.length === 0 && (
                <tr>
                  <td colSpan={11} className="py-10 text-center font-mono text-xs text-fg-muted">
                    Không có key nào
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
