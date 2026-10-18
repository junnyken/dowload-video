import { useEffect, useRef, useState, type ReactNode } from 'react'
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
        <textarea className={cn(inputCls, 'font-mono')} rows={3} value={value.ips} disabled={disabled} placeholder={'203.0.113.10\n198.51.100.7'} onChange={e => set('ips', e.target.value)} />
      </Field>
      <Field label="Ngày hết hạn (tuỳ chọn)" hint="Để trống = không hết hạn.">
        <input className={inputCls} type="date" value={value.expires} disabled={disabled} onChange={e => set('expires', e.target.value)} />
      </Field>
    </div>
  )
}
