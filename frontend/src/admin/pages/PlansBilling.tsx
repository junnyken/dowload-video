import { useCallback, useEffect, useState } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'

/**
 * Gói & thanh toán (owner 13/10): Pro price + bank-transfer details, and the
 * "who paid?" lookup by transfer code. Backend: app/api/plans.py.
 * Payment is a manual bank transfer; the admin grants Pro for 30/365 days.
 */
// wording: BA review

export interface GrantResult { success: boolean; expiry?: string; message?: string }

export const fmtDate = (iso?: string | null) => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('vi-VN')
}

export async function grantPro(userId: string, days: 30 | 365): Promise<GrantResult> {
  const res = await adminPost<GrantResult>('/update-user', { user_id: userId, plan: 'pro', days })
  if (res && res.success === false) throw new Error(res.message ?? 'Cấp Pro không thành công')
  return res
}

interface PlanSettings {
  price_month_vnd: number
  price_year_vnd: number
  bank_name: string
  bank_account: string
  bank_holder: string
  contact: string
}

const inputCls = 'min-h-11 w-full rounded-card border border-line bg-surface px-3 text-sm text-fg outline-none focus:border-line-strong'

export function PlansSettingsCard() {
  const [form, setForm] = useState<PlanSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(async () => {
    try {
      setForm(await adminFetch<PlanSettings>('/plans/settings'))
    } catch (e) {
      setMsg({ ok: false, text: `Không tải được cài đặt: ${(e as Error).message}` })
    }
  }, [])
  useEffect(() => { load() }, [load])

  const set = (k: keyof PlanSettings, v: string) =>
    setForm(f => (f ? { ...f, [k]: k.startsWith('price_') ? (v === '' ? 0 : Number(v)) : v } : f))

  async function save() {
    if (!form) return
    setBusy(true); setMsg(null)
    try {
      // A 422 puts the Vietnamese reason in `detail`; adminFetch throws it as the message.
      const saved = await adminPost<PlanSettings>('/plans/settings', form)
      setForm(saved)
      setMsg({ ok: true, text: 'Đã lưu. Trang bảng giá cập nhật sau tối đa vài phút.' })
    } catch (e) {
      setMsg({ ok: false, text: `Không lưu được: ${(e as Error).message}` })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card padding="md" as="section">
      <h2 className="text-sm font-semibold text-fg">Gói &amp; thanh toán</h2>
      <p className="mt-1 text-[11px] text-fg-muted">
        Giá Pro và thông tin chuyển khoản hiển thị ở trang /pricing. Chưa điền đủ ngân hàng, số tài khoản, chủ tài khoản thì trang giá chỉ hiện thông tin liên hệ.
      </p>
      {!form ? (
        <p className="mt-3 text-xs text-fg-muted">{msg ? '' : 'Đang tải…'}</p>
      ) : (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-fg-2">Giá Pro / tháng (đồng)
            <input type="number" inputMode="numeric" value={form.price_month_vnd} className={inputCls}
              onChange={e => set('price_month_vnd', e.target.value)} />
          </label>
          <label className="text-xs text-fg-2">Giá Pro / năm (đồng)
            <input type="number" inputMode="numeric" value={form.price_year_vnd} className={inputCls}
              onChange={e => set('price_year_vnd', e.target.value)} />
          </label>
          <label className="text-xs text-fg-2">Ngân hàng
            <input value={form.bank_name} maxLength={120} className={inputCls} onChange={e => set('bank_name', e.target.value)} />
          </label>
          <label className="text-xs text-fg-2">Số tài khoản
            <input value={form.bank_account} maxLength={120} className={inputCls} onChange={e => set('bank_account', e.target.value)} />
          </label>
          <label className="text-xs text-fg-2">Chủ tài khoản
            <input value={form.bank_holder} maxLength={120} className={inputCls} onChange={e => set('bank_holder', e.target.value)} />
          </label>
          <label className="text-xs text-fg-2">Liên hệ (hiện khi chưa đủ thông tin ngân hàng)
            <input value={form.contact} maxLength={120} className={inputCls} onChange={e => set('contact', e.target.value)} />
          </label>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button variant="primary" size="md" className="min-h-11" disabled={busy || !form} onClick={save}>
          {busy ? 'Đang lưu…' : 'Lưu cài đặt'}
        </Button>
        {msg && <span role="status" className={`text-xs ${msg.ok ? 'text-success' : 'text-danger'}`}>{msg.text}</span>}
      </div>
    </Card>
  )
}

interface Match {
  id: string
  email: string | null
  tier: string
  billing_status: string | null
  subscription_expiry: string | null
  effective_tier: string
  transfer_code: string
}

export function TransferCodeFinder() {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [matches, setMatches] = useState<Match[] | null>(null)
  const [err, setErr] = useState('')
  const [grantMsg, setGrantMsg] = useState<Record<string, { ok: boolean; text: string }>>({})

  async function find() {
    setBusy(true); setErr(''); setMatches(null); setGrantMsg({})
    try {
      const r = await adminFetch<{ matches: Match[] }>(`/plans/find?code=${encodeURIComponent(code.trim())}`)
      setMatches(r.matches)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function grant(m: Match, days: 30 | 365) {
    setBusy(true)
    try {
      const r = await grantPro(m.id, days)
      setGrantMsg(p => ({ ...p, [m.id]: { ok: true, text: `Đã cấp Pro ${days} ngày, hạn đến ${fmtDate(r.expiry)}` } }))
      const again = await adminFetch<{ matches: Match[] }>(`/plans/find?code=${encodeURIComponent(m.transfer_code)}`)
      setMatches(again.matches)
    } catch (e) {
      setGrantMsg(p => ({ ...p, [m.id]: { ok: false, text: (e as Error).message } }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card padding="md" as="section">
      <h2 className="text-sm font-semibold text-fg">Tìm theo mã chuyển khoản</h2>
      <p className="mt-1 text-[11px] text-fg-muted">Mã có dạng VG + 8 ký tự (vd VG1A2B3C4D), nằm trong nội dung chuyển khoản của khách.</p>
      <form className="mt-3 flex flex-wrap items-center gap-2" onSubmit={e => { e.preventDefault(); if (code.trim()) find() }}>
        <input value={code} onChange={e => setCode(e.target.value)} placeholder="VGxxxxxxxx" maxLength={20}
          aria-label="Mã chuyển khoản" className={`${inputCls} sm:w-56 font-mono uppercase`} />
        <Button type="submit" variant="primary" size="md" className="min-h-11" disabled={busy || !code.trim()}>Tìm</Button>
      </form>
      {err && <p role="alert" className="mt-2 text-xs text-danger">{err}</p>}
      {matches && matches.length === 0 && <p className="mt-2 text-xs text-fg-muted">Không có tài khoản nào khớp mã này.</p>}
      {matches && matches.map(m => (
        <div key={m.id} className="mt-3 rounded-card border border-line p-3 text-xs">
          <p className="text-fg break-all">{m.email ?? m.id} <span className="text-fg-muted">· {m.transfer_code}</span></p>
          <p className="mt-1 text-fg-2">
            Gói hiện tại: <b>{m.effective_tier}</b> · trạng thái: {m.billing_status ?? 'none'} · hạn: {fmtDate(m.subscription_expiry)}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Button size="md" className="min-h-11" disabled={busy} onClick={() => grant(m, 30)}>Cấp Pro 30 ngày</Button>
            <Button size="md" className="min-h-11" disabled={busy} onClick={() => grant(m, 365)}>Cấp Pro 365 ngày</Button>
            {grantMsg[m.id] && (
              <span role="status" className={grantMsg[m.id].ok ? 'text-success' : 'text-danger'}>{grantMsg[m.id].text}</span>
            )}
          </div>
        </div>
      ))}
    </Card>
  )
}
