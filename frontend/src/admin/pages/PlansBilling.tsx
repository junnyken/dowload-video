import { useCallback, useEffect, useMemo, useState } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import {
  type PlanSettings,
  NUMERIC_PLAN_KEYS,
  buildPlanSettingsPayload,
  findMismatchedPlanFields,
  findSameCycleDuplicateVndWarnings,
} from '../utils/planSettingsValidation'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'

/**
 * Gói & thanh toán (owner 13/10): Starter / Pro / Team prices + bank-transfer
 * details, and the "who paid?" lookup by transfer code. Backend: app/api/plans.py.
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

const RAPIDAPI_STUDIO_URL = 'https://rapidapi.com/studio/api_b8b4154e-99d8-404b-a85c-4ac6e14f63cd/publish/general'
const RAPIDAPI_GATEWAY_URL = 'https://rapidapi.com/studio/api_b8b4154e-99d8-404b-a85c-4ac6e14f63cd/publish/gateway?pub_hub=true'
const OPENAPI_SPEC_URL = 'https://dvid-api.vibe1.tinhgon.xyz/api/v1/rapidapi-openapi.json'

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
    setForm(f => (f ? { ...f, [k]: NUMERIC_PLAN_KEYS.has(k) ? (v === '' ? 0 : Number(v)) : v } : f))

  const duplicateWarnings = useMemo(
    () => findSameCycleDuplicateVndWarnings(form),
    [form],
  )

  async function save() {
    if (!form) return
    setBusy(true); setMsg(null)
    try {
      const payload = buildPlanSettingsPayload(form)
      const saved = await adminPost<PlanSettings>('/plans/settings', payload)
      const mismatches = findMismatchedPlanFields(payload, saved)
      setForm(saved)
      if (mismatches.length > 0) {
        setMsg({
          ok: false,
          text: `Không lưu được đúng giá trị ở trường: ${mismatches.join('; ')}`,
        })
      } else {
        setMsg({ ok: true, text: 'Đã lưu. Trang bảng giá cập nhật ngay.' })
      }
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
        Giá Starter / Pro / Team và thông tin chuyển khoản hiển thị ở trang /pricing. Chưa điền đủ ngân hàng, số tài khoản, chủ tài khoản thì trang giá chỉ hiện thông tin liên hệ.
      </p>

      {duplicateWarnings.length > 0 && (
        <div
          role="alert"
          className="mt-3 rounded-card border border-danger/50 bg-danger/10 p-3 text-xs text-danger space-y-1"
        >
          <p className="font-semibold">⚠️ Cảnh báo trùng / sát giá VNĐ cùng kỳ thanh toán</p>
          {duplicateWarnings.map((w, idx) => (
            <p key={idx}>{w}</p>
          ))}
        </div>
      )}

      {!form ? (
        <p className="mt-3 text-xs text-fg-muted">{msg ? '' : 'Đang tải…'}</p>
      ) : (
        <div className="mt-3 space-y-4">
          {/* ── Gói Starter ── */}
          <div className="rounded-card border border-line bg-surface-2/30 p-3 space-y-2.5">
            <h3 className="text-xs font-semibold text-fg">🌱 Gói Starter (mua lần đầu / niêm yết)</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-fg-2">🇻🇳 Giá Starter / tháng (VNĐ — VietQR)
                <input type="number" inputMode="numeric" value={form.starter_month_vnd ?? 49000} className={inputCls}
                  onChange={e => set('starter_month_vnd', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2">🇻🇳 Giá Starter / năm (VNĐ — VietQR)
                <input type="number" inputMode="numeric" value={form.starter_year_vnd ?? 590000} className={inputCls}
                  onChange={e => set('starter_year_vnd', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2">🌎 Giá Starter / tháng (USD Cents — vd: 199 = $1.99)
                <input type="number" inputMode="numeric" value={form.starter_month_usd_cents ?? 199} className={inputCls}
                  onChange={e => set('starter_month_usd_cents', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2">🌎 Giá Starter / năm (USD Cents — vd: 2499 = $24.99)
                <input type="number" inputMode="numeric" value={form.starter_year_usd_cents ?? 2499} className={inputCls}
                  onChange={e => set('starter_year_usd_cents', e.target.value)} />
              </label>
            </div>
          </div>

          {/* ── Gói Pro ── */}
          <div className="rounded-card border border-line bg-surface-2/30 p-3 space-y-2.5">
            <h3 className="text-xs font-semibold text-fg">⭐ Gói Pro (mua lần đầu / niêm yết)</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-fg-2">🇻🇳 Giá Pro / tháng (VNĐ — VietQR)
                <input type="number" inputMode="numeric" value={form.price_month_vnd} className={inputCls}
                  onChange={e => set('price_month_vnd', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2">🇻🇳 Giá Pro / năm (VNĐ — VietQR)
                <input type="number" inputMode="numeric" value={form.price_year_vnd} className={inputCls}
                  onChange={e => set('price_year_vnd', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2 font-semibold text-accent-text">🌎 Giá Quốc tế Pro / tháng (USD Cents — vd: 399 = $3.99)
                <input type="number" inputMode="numeric" value={form.price_month_usd_cents ?? 399} className={inputCls}
                  onChange={e => set('price_month_usd_cents', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2 font-semibold text-accent-text">🌎 Giá Quốc tế Pro / năm (USD Cents — vd: 3900 = $39.00)
                <input type="number" inputMode="numeric" value={form.price_year_usd_cents ?? 3900} className={inputCls}
                  onChange={e => set('price_year_usd_cents', e.target.value)} />
              </label>
            </div>
          </div>

          {/* ── Gói Team ── */}
          <div className="rounded-card border border-line bg-surface-2/30 p-3 space-y-2.5">
            <h3 className="text-xs font-semibold text-fg">🏢 Gói Team (mua lần đầu / niêm yết)</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs text-fg-2">🇻🇳 Giá Team / tháng (VNĐ — VietQR)
                <input type="number" inputMode="numeric" value={form.team_month_vnd ?? 279000} className={inputCls}
                  onChange={e => set('team_month_vnd', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2">🇻🇳 Giá Team / năm (VNĐ — VietQR)
                <input type="number" inputMode="numeric" value={form.team_year_vnd ?? 2790000} className={inputCls}
                  onChange={e => set('team_year_vnd', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2">🌎 Giá Team / tháng (USD Cents — vd: 1199 = $11.99)
                <input type="number" inputMode="numeric" value={form.team_month_usd_cents ?? 1199} className={inputCls}
                  onChange={e => set('team_month_usd_cents', e.target.value)} />
              </label>
              <label className="text-xs text-fg-2">🌎 Giá Team / năm (USD Cents — vd: 11900 = $119.00)
                <input type="number" inputMode="numeric" value={form.team_year_usd_cents ?? 11900} className={inputCls}
                  onChange={e => set('team_year_usd_cents', e.target.value)} />
              </label>
            </div>
          </div>

          {/* ── Hạn mức API & Tài khoản ngân hàng ── */}
          <div className="grid gap-3 sm:grid-cols-2">
            {/* wording: BA review — owner 14/10 monthly API quota */}
            <label className="text-xs text-fg-2">Lượt gọi API / tháng — Pro
              <input type="number" inputMode="numeric" value={form.api_calls_month_pro} className={inputCls}
                onChange={e => set('api_calls_month_pro', e.target.value)} />
            </label>
            <label className="text-xs text-fg-2">Lượt gọi API / tháng — Team
              <input type="number" inputMode="numeric" value={form.api_calls_month_team} className={inputCls}
                onChange={e => set('api_calls_month_team', e.target.value)} />
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
        </div>
      )}

      {form && (
        <div className="mt-4 rounded-card border border-line bg-surface-2/40 p-3.5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-xs font-semibold text-fg">💳 Thanh toán Quốc tế (PayPal) &amp; Chợ RapidAPI</h3>
              <p className="text-[11px] text-fg-muted mt-0.5">
                Cấu hình nhanh PayPal &amp; liên kết chợ RapidAPI. Khi lưu, trang <code className="text-fg">/pricing</code> và <code className="text-fg">/api-docs</code> sẽ tự động cập nhật.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <a
                href={RAPIDAPI_STUDIO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2.5 py-1.5 font-medium text-fg hover:border-line-strong"
              >
                🚀 Mở RapidAPI Studio ↗
              </a>
              <a
                href={RAPIDAPI_GATEWAY_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2.5 py-1.5 font-medium text-fg-2 hover:border-line-strong"
              >
                🛡️ Lấy Proxy Secret ↗
              </a>
              <a
                href={OPENAPI_SPEC_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2.5 py-1.5 font-medium text-fg-2 hover:border-line-strong"
              >
                📄 File OpenAPI JSON ↗
              </a>
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-fg-2">🛡️ X-RapidAPI-Proxy-Secret
              <input
                value={form.rapidapi_proxy_secret || ''}
                maxLength={240}
                placeholder="9cb59af0-d757-11f1-..."
                className={`${inputCls} font-mono`}
                onChange={e => set('rapidapi_proxy_secret', e.target.value)}
              />
            </label>
            <label className="text-xs text-fg-2">⚡ Link API trên chợ RapidAPI (View in Hub)
              <input
                value={form.rapidapi_url || ''}
                maxLength={240}
                placeholder="https://rapidapi.com/..."
                className={inputCls}
                onChange={e => set('rapidapi_url', e.target.value)}
              />
            </label>
            <label className="text-xs text-fg-2 sm:col-span-2">🅿️ Link PayPal.Me hoặc Email PayPal của bạn
              <input
                value={form.paypal_me_url || ''}
                maxLength={240}
                placeholder="https://paypal.me/username hoặc email@paypal.com"
                className={inputCls}
                onChange={e => set('paypal_me_url', e.target.value)}
              />
            </label>
          </div>

          {/* ── SePay VietQR Tự động ── */}
          <h4 className="text-sm font-semibold text-fg-1 mt-4 mb-2">🏦 SePay — Tự động xác nhận chuyển khoản VietQR</h4>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-fg-2">🏛️ Mã BIN ngân hàng (VietQR)
              <input
                value={form.bank_bin || ''}
                maxLength={20}
                placeholder="970416 (ACB)"
                className={inputCls}
                onChange={e => set('bank_bin', e.target.value)}
              />
            </label>
            <label className="text-xs text-fg-2">🔐 SePay Webhook Secret (HMAC-SHA256)
              <input
                value={form.sepay_webhook_secret || ''}
                maxLength={240}
                placeholder="Dán secret từ SePay Dashboard"
                className={inputCls}
                type="password"
                onChange={e => set('sepay_webhook_secret', e.target.value)}
              />
            </label>
            <div className="sm:col-span-2 text-xs text-fg-3">
              <p>📋 Webhook URL cho SePay: <code className="bg-surface-2 px-1 py-0.5 rounded text-fg-2">https://dvid-api.vibe1.tinhgon.xyz/api/v1/payments/sepay/webhook</code></p>
              <p className="mt-1">Sự kiện: <b>In</b> (tiền vào) · Xác thực: <b>HMAC-SHA256</b> · Lọc nội dung: <b>VGP</b></p>
            </div>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button variant="primary" size="md" className="min-h-11" disabled={busy || !form} onClick={save}>
          {busy ? 'Đang lưu…' : '💾 Lưu cài đặt (Giá gói, Ngân hàng, PayPal & RapidAPI)'}
        </Button>
        {msg && (
          <span
            role={msg.ok ? 'status' : 'alert'}
            className={`text-xs ${msg.ok ? 'text-success font-medium' : 'text-danger font-semibold'}`}
          >
            {msg.text}
          </span>
        )}
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

export default function PlansBillingPage() {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Gói &amp; Cổng thanh toán</h1>
          <p className="text-xs text-fg-muted mt-0.5">
            Cấu hình bảng giá Starter / Pro / Team, ngân hàng nội địa (VietQR + SePay HMAC-SHA256), thanh toán quốc tế (PayPal &amp; RapidAPI) và tra cứu mã chuyển khoản.
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2 items-start">
        <PlansSettingsCard />
        <TransferCodeFinder />
      </div>
    </div>
  )
}
