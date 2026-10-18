import { useEffect, useState, useCallback } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { cn } from '../utils/cn'
import { Button } from '../shared/Button'
import {
  DEFAULT_KEY_FORM, Field, FormError, KeyFields, Modal, RawKeyModal, buildKeyPayload, inputCls,
  type KeyFormValues, type PartnerKeyRow,
} from '../shared/PartnerKeyUi'

interface TenantSettings {
  custom_domain: string | null
  features: Record<string, boolean>
}

interface TodayUsage {
  api_calls?: number
  downloads?: number
  webhook_deliveries?: number
  storage_bytes_used?: number
}

interface Tenant {
  id: string
  name: string
  slug: string
  plan: string
  plan_seats: number
  plan_api_calls_per_month: number
  plan_storage_gb: number
  status: string
  trial_ends_at: string | null
  subscription_id: string | null
  license_expires_at: string | null
  created_at: string
  updated_at: string
  tenant_settings: TenantSettings | null
  today_usage: TodayUsage
}

interface TenantsResponse {
  tenants: Tenant[]
  total: number
  plan_counts: Record<string, number>
  status_counts: Record<string, number>
}

const PLAN_COLORS: Record<string, string> = {
  starter: 'border-line bg-surface-2 text-fg-2',
  growth: 'border-line-strong bg-surface text-fg',
  enterprise: 'border-line-strong bg-surface text-fg',
}

const STATUS_COLORS: Record<string, string> = {
  active: 'border-success/30 bg-success-soft text-success',
  trial: 'border-line bg-surface-2 text-fg-2',
  suspended: 'border-warning/30 bg-warning-soft text-warning',
  canceled: 'border-danger/30 bg-danger-soft text-danger',
}

function PlanBadge({ plan }: { plan: string }) {
  return (
    <span className={cn('inline-block rounded-md border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider', PLAN_COLORS[plan] ?? 'border-line bg-surface-2 text-fg-2')}>
      {plan}
    </span>
  )
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={cn('inline-block rounded-md border px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider', STATUS_COLORS[status] ?? 'border-line bg-surface-2 text-fg-2')}>
      {status}
    </span>
  )
}

function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(1)} ${units[i]}`
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
}

const PLAN_DEFAULT_CAP: Record<string, string> = { starter: '1.000', growth: '50.000', enterprise: 'không giới hạn' }

// wording: BA review
function formatCap(n: number | null | undefined): string {
  if (n === -1) return 'Không giới hạn'
  if (n === null || n === undefined) return '—'
  return `${n.toLocaleString('vi-VN')} job/tháng`
}

/** '' = not set, 'unlimited' handled by the checkbox. Returns number or error. */
function parseCap(text: string, unlimited: boolean): { value: number } | { error: string } {
  if (unlimited) return { value: -1 }
  const n = Number(text)
  // wording: BA review
  if (text.trim() === '' || !Number.isInteger(n) || n < 0) return { error: 'Hạn mức job/tháng phải là số nguyên từ 0 trở lên, hoặc chọn “Không giới hạn”.' }
  return { value: n }
}

function CapFields({ cap, setCap, unlimited, setUnlimited, disabled, optional }: {
  cap: string; setCap: (v: string) => void; unlimited: boolean; setUnlimited: (v: boolean) => void; disabled: boolean; optional?: boolean
}) {
  return (
    <div className="space-y-2">
      {/* wording: BA review */}
      <Field label="Hạn mức job/tháng" hint={optional ? 'Để trống = dùng mặc định theo gói.' : undefined}>
        <input className={inputCls} type="number" min={0} inputMode="numeric" value={cap} disabled={disabled || unlimited} placeholder={optional ? 'Mặc định theo gói' : 'Ví dụ 5000'} onChange={e => setCap(e.target.value)} />
      </Field>
      <label className="flex cursor-pointer items-center gap-2 text-xs text-fg-muted">
        <input type="checkbox" checked={unlimited} disabled={disabled} onChange={e => setUnlimited(e.target.checked)} />
        Không giới hạn
      </label>
    </div>
  )
}

function CreateTenantModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [plan, setPlan] = useState('starter')
  const [cap, setCap] = useState('')
  const [unlimited, setUnlimited] = useState(false)
  const [pending, setPending] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    // wording: BA review
    if (!name.trim() || name.trim().length > 120) { setErr('Tên khách phải có từ 1 đến 120 ký tự.'); return }
    const body: Record<string, unknown> = { name: name.trim(), plan }
    if (unlimited || cap.trim() !== '') {
      const r = parseCap(cap, unlimited)
      if ('error' in r) { setErr(r.error); return }
      body.api_calls_per_month = r.value
    }
    setPending(true); setErr(null)
    try {
      await adminPost('/enterprise/tenants', body)
      onCreated()
    } catch (e2) {
      setErr((e2 as Error).message)
      setPending(false)
    }
  }

  return (
    // wording: BA review
    <Modal title="Tạo khách partner" subtitle="Khách gọi Partner API từ backend của họ bằng key vgp_." onClose={onClose} locked={pending}>
      <form onSubmit={submit} className="space-y-3">
        <Field label="Tên khách">
          <input className={inputCls} value={name} maxLength={120} disabled={pending} autoFocus onChange={e => setName(e.target.value)} />
        </Field>
        <Field label="Gói" hint={`Hạn mức job/tháng mặc định của gói này: ${PLAN_DEFAULT_CAP[plan]}.`}>
          <select className={inputCls} value={plan} disabled={pending} onChange={e => setPlan(e.target.value)}>
            <option value="starter">Starter</option>
            <option value="growth">Growth</option>
            <option value="enterprise">Enterprise</option>
          </select>
        </Field>
        <CapFields cap={cap} setCap={setCap} unlimited={unlimited} setUnlimited={setUnlimited} disabled={pending} optional />
        <FormError message={err} />
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={pending}>Huỷ</Button>
          <Button type="submit" variant="primary" disabled={pending}>{pending ? 'Đang tạo…' : 'Tạo khách'}</Button>
        </div>
      </form>
    </Modal>
  )
}

function EditCapModal({ tenant, onClose, onSaved }: { tenant: Tenant; onClose: () => void; onSaved: () => void }) {
  const initUnlimited = tenant.plan_api_calls_per_month === -1
  const [cap, setCap] = useState(initUnlimited ? '' : String(tenant.plan_api_calls_per_month ?? ''))
  const [unlimited, setUnlimited] = useState(initUnlimited)
  const [pending, setPending] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const r = parseCap(cap, unlimited)
    if ('error' in r) { setErr(r.error); return }
    setPending(true); setErr(null)
    try {
      await adminPost(`/enterprise/tenants/${tenant.id}/limits`, { api_calls_per_month: r.value })
      onSaved()
    } catch (e2) {
      setErr((e2 as Error).message)
      setPending(false)
    }
  }

  return (
    // wording: BA review
    <Modal title="Sửa hạn mức job/tháng" subtitle={tenant.name} onClose={onClose} locked={pending}>
      <form onSubmit={submit} className="space-y-3">
        <CapFields cap={cap} setCap={setCap} unlimited={unlimited} setUnlimited={setUnlimited} disabled={pending} />
        <FormError message={err} />
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={pending}>Huỷ</Button>
          <Button type="submit" variant="primary" disabled={pending}>{pending ? 'Đang lưu…' : 'Lưu'}</Button>
        </div>
      </form>
    </Modal>
  )
}

function IssueKeyModal({ tenant, onClose, onIssued }: { tenant: Tenant; onClose: () => void; onIssued: (rawKey: string) => void }) {
  const [form, setForm] = useState<KeyFormValues>(DEFAULT_KEY_FORM)
  const [pending, setPending] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const built = buildKeyPayload(form)
    if ('error' in built) { setErr(built.error); return }
    setPending(true); setErr(null)
    try {
      const row = await adminPost<PartnerKeyRow>(`/enterprise/tenants/${tenant.id}/api-keys`, built.payload)
      if (!row.raw_key) throw new Error('Máy chủ không trả về key. Hãy dùng “Đổi key” trong trang Partner API Keys để lấy key mới.')
      onIssued(row.raw_key)
    } catch (e2) {
      setErr((e2 as Error).message)
      setPending(false)
    }
  }

  return (
    // wording: BA review
    <Modal title="Cấp API key" subtitle={`${tenant.name} · tối đa 10 key đang hoạt động`} onClose={onClose} locked={pending}>
      <form onSubmit={submit} className="space-y-3">
        <KeyFields value={form} onChange={setForm} disabled={pending} />
        <FormError message={err} />
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose} disabled={pending}>Huỷ</Button>
          <Button type="submit" variant="primary" disabled={pending}>{pending ? 'Đang cấp…' : 'Cấp key'}</Button>
        </div>
      </form>
    </Modal>
  )
}

export default function TenantsPage() {
  const [data, setData] = useState<TenantsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [planFilter, setPlanFilter] = useState('')
  const [actionInProgress, setActionInProgress] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [showCreate, setShowCreate] = useState(false)
  const [capTenant, setCapTenant] = useState<Tenant | null>(null)
  const [keyTenant, setKeyTenant] = useState<Tenant | null>(null)
  // One-time secret: lives only while the reveal modal is open.
  const [revealKey, setRevealKey] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (statusFilter) params.set('status', statusFilter)
      if (planFilter) params.set('plan', planFilter)
      const result = await adminFetch<TenantsResponse>(`/enterprise/tenants?${params}`)
      setData(result)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [statusFilter, planFilter])

  useEffect(() => { fetchData() }, [fetchData])

  async function handleStatusChange(tenantId: string, newStatus: string) {
    setActionInProgress(p => ({ ...p, [tenantId]: true }))
    try {
      await adminPost(`/enterprise/tenants/${tenantId}/status`, { status: newStatus })
      setMsg(m => ({ ...m, [tenantId]: `Status → ${newStatus}` }))
      setTimeout(() => setMsg(m => { const n = { ...m }; delete n[tenantId]; return n }), 2000)
      fetchData()
    } catch (e) {
      setMsg(m => ({ ...m, [tenantId]: `Error: ${(e as Error).message}` }))
    } finally {
      setActionInProgress(p => ({ ...p, [tenantId]: false }))
    }
  }

  async function handlePlanChange(tenantId: string, newPlan: string) {
    setActionInProgress(p => ({ ...p, [tenantId + '_plan']: true }))
    try {
      await adminPost(`/enterprise/tenants/${tenantId}/plan`, { plan: newPlan })
      setMsg(m => ({ ...m, [tenantId + '_plan']: `Plan → ${newPlan}` }))
      setTimeout(() => setMsg(m => { const n = { ...m }; delete n[tenantId + '_plan']; return n }), 2000)
      fetchData()
    } catch (e) {
      setMsg(m => ({ ...m, [tenantId + '_plan']: `Error: ${(e as Error).message}` }))
    } finally {
      setActionInProgress(p => ({ ...p, [tenantId + '_plan']: false }))
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Tenants</h1>
          <p className="mt-0.5 text-xs text-fg-muted">Multi-tenant enterprise accounts</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {/* wording: BA review */}
          <Button variant="primary" onClick={() => setShowCreate(true)}>Tạo khách partner</Button>
          <button onClick={fetchData} className="px-3 py-1.5 text-xs rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2">
            ↺ Refresh
          </button>
        </div>
      </div>

      {showCreate && (
        <CreateTenantModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); fetchData() }} />
      )}
      {capTenant && (
        <EditCapModal tenant={capTenant} onClose={() => setCapTenant(null)} onSaved={() => { setCapTenant(null); fetchData() }} />
      )}
      {keyTenant && (
        <IssueKeyModal
          tenant={keyTenant}
          onClose={() => setKeyTenant(null)}
          onIssued={raw => { setKeyTenant(null); setRevealKey(raw); fetchData() }}
        />
      )}
      {revealKey && (
        // wording: BA review
        <RawKeyModal rawKey={revealKey} title="API key mới" onClose={() => setRevealKey(null)} />
      )}

      {/* Summary cards */}
      {data && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'Total', value: data.total },
            { label: 'Active', value: data.status_counts['active'] ?? 0 },
            { label: 'Suspended', value: data.status_counts['suspended'] ?? 0 },
            { label: 'Enterprise', value: data.plan_counts['enterprise'] ?? 0 },
          ].map(card => (
            <div key={card.label} className="rounded-card border border-line bg-surface shadow-card p-4">
              <p className="font-mono text-xs text-fg-muted">{card.label}</p>
              <p className="mt-1 font-mono text-2xl font-bold text-fg">{card.value}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-2">
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-xs text-fg-2"
        >
          <option value="">All status</option>
          <option value="active">Active</option>
          <option value="suspended">Suspended</option>
          <option value="canceled">Canceled</option>
        </select>
        <select
          value={planFilter}
          onChange={e => setPlanFilter(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1.5 text-xs text-fg-2"
        >
          <option value="">All plans</option>
          <option value="starter">Starter</option>
          <option value="growth">Growth</option>
          <option value="enterprise">Enterprise</option>
        </select>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{error}</div>
      )}

      {/* Table */}
      {loading ? (
        <div className="py-12 text-center font-mono text-xs text-fg-muted">Loading tenants…</div>
      ) : (
        <div className="overflow-x-auto rounded-control border border-line">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-line bg-surface-2">
                {['Tenant', 'Plan', 'Status', "Today's Usage", 'Seats', 'Custom Domain', 'Created', 'Actions'].map(h => (
                  <th key={h} className="px-3 py-2.5 font-mono font-semibold text-fg-muted">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.tenants ?? []).map(t => (
                <tr key={t.id} className="border-b border-line hover:bg-surface-2">
                  <td className="px-3 py-2.5">
                    <p className="font-medium text-fg-2">{t.name}</p>
                    <p className="font-mono text-[10px] text-fg-muted">{t.slug}</p>
                  </td>
                  <td className="px-3 py-2.5">
                    <PlanBadge plan={t.plan} />
                    <p className="mt-0.5 text-[10px] text-fg-muted">{/* wording: BA review */}{formatCap(t.plan_api_calls_per_month)}</p>
                  </td>
                  <td className="px-3 py-2.5"><StatusBadge status={t.status} /></td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">
                    <span title="API calls">⚡{t.today_usage?.api_calls ?? 0}</span>
                    {' · '}
                    <span title="Downloads">↓{t.today_usage?.downloads ?? 0}</span>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">{t.plan_seats}</td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted text-[10px]">
                    {t.tenant_settings?.custom_domain ?? '—'}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-fg-muted">{formatDate(t.created_at)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      {/* wording: BA review */}
                      <button
                        onClick={() => setKeyTenant(t)}
                        className="rounded border border-line bg-surface px-2 py-1 text-[10px] font-medium text-fg hover:border-line-strong hover:bg-surface-2"
                      >
                        Cấp API key
                      </button>
                      <button
                        onClick={() => setCapTenant(t)}
                        className="rounded border border-line bg-surface px-2 py-1 text-[10px] font-medium text-fg hover:border-line-strong hover:bg-surface-2"
                      >
                        Sửa hạn mức
                      </button>
                      {/* Plan change */}
                      <select
                        disabled={actionInProgress[t.id + '_plan']}
                        value={t.plan}
                        onChange={e => handlePlanChange(t.id, e.target.value)}
                        className="rounded border border-line bg-surface px-1.5 py-1 text-[10px] text-fg-2"
                      >
                        <option value="starter">Starter</option>
                        <option value="growth">Growth</option>
                        <option value="enterprise">Enterprise</option>
                      </select>
                      {/* Status toggle */}
                      {t.status === 'active' ? (
                        <button
                          disabled={actionInProgress[t.id]}
                          onClick={() => handleStatusChange(t.id, 'suspended')}
                          className="rounded bg-warning-soft px-2 py-1 text-[10px] text-warning hover:bg-warning-soft disabled:opacity-40"
                        >
                          Suspend
                        </button>
                      ) : (
                        <button
                          disabled={actionInProgress[t.id]}
                          onClick={() => handleStatusChange(t.id, 'active')}
                          className="rounded bg-success-soft px-2 py-1 text-[10px] text-success hover:bg-success/40 disabled:opacity-40"
                        >
                          Activate
                        </button>
                      )}
                    </div>
                    {(msg[t.id] || msg[t.id + '_plan']) && (
                      <p className="mt-1 font-mono text-[10px] text-success">
                        {msg[t.id] || msg[t.id + '_plan']}
                      </p>
                    )}
                  </td>
                </tr>
              ))}
              {(data?.tenants ?? []).length === 0 && (
                <tr>
                  <td colSpan={8} className="py-10 text-center">
                    {/* wording: BA review */}
                    {statusFilter || planFilter ? (
                      <p className="font-mono text-xs text-fg-muted">Không có khách nào khớp bộ lọc.</p>
                    ) : (
                      <div className="space-y-3">
                        <p className="text-xs text-fg-muted">Chưa có khách nào. Tạo khách partner đầu tiên để cấp API key.</p>
                        <Button variant="primary" onClick={() => setShowCreate(true)}>Tạo khách partner</Button>
                      </div>
                    )}
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
