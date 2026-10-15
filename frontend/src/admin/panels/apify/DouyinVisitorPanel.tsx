import { useCallback, useEffect, useState } from 'react'
import { douyinVisitorApi } from '../../api/douyinVisitor'
import type { VisitorStatus } from '../../api/douyinVisitor'
import { Card } from '../../shared/Card'
import { Button } from '../../shared/Button'
import { SectionHeader } from '../../shared/SectionHeader'
import { StatCard } from '../../shared/StatCard'

const INPUT =
  'h-9 w-full rounded-control border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'

interface Form {
  enabled: boolean
  mint_url: string
  mint_key: string
  max_age_h: string
  per_min: string
  per_hour: string
  pause_min: string
}

const toForm = (s: VisitorStatus): Form => ({
  enabled: s.enabled,
  mint_url: s.mint_url,
  mint_key: '',
  max_age_h: String(s.max_age_h),
  per_min: String(s.per_min),
  per_hour: String(s.per_hour),
  pause_min: String(s.pause_min),
})

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-fg-2">{label}</label>
      {children}
    </div>
  )
}

export function DouyinVisitorPanel() {
  const [st, setSt] = useState<VisitorStatus | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<'save' | 'mint' | null>(null)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const load = useCallback(async (resetForm: boolean) => {
    try {
      const s = await douyinVisitorApi.status()
      setSt(s)
      if (resetForm) setForm(toForm(s))
      setLoadErr(null)
    } catch (e) {
      setLoadErr((e as Error).message)
    }
  }, [])

  useEffect(() => {
    load(true)
    const t = setInterval(() => load(false), 30_000)
    return () => clearInterval(t)
  }, [load])

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f))

  async function save() {
    if (!form) return
    const nums = { max_age_h: form.max_age_h, per_min: form.per_min, per_hour: form.per_hour, pause_min: form.pause_min }
    const parsed: Record<string, number> = {}
    for (const [k, v] of Object.entries(nums)) {
      const num = Number(v)
      if (v.trim() === '' || !Number.isFinite(num) || num < 0) {
        setMsg({ ok: false, text: 'Các ô số phải là số không âm.' })
        return
      }
      parsed[k] = num
    }
    const body = {
      enabled: form.enabled,
      mint_url: form.mint_url.trim(),
      ...parsed,
      ...(form.mint_key.trim() ? { mint_key: form.mint_key.trim() } : {}),
    }
    setBusy('save')
    setMsg(null)
    try {
      await douyinVisitorApi.saveSettings(body)
      set('mint_key', '')
      await load(true)
      setMsg({ ok: true, text: 'Đã lưu' })
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message })
    } finally {
      setBusy(null)
    }
  }

  async function mint() {
    setBusy('mint')
    setMsg(null)
    try {
      const r = await douyinVisitorApi.mint()
      await load(false) // mint's `ok` is a boolean; re-read counters via GET
      setMsg(r.ok
        ? { ok: true, text: 'Đã lấy mã mới' }
        : { ok: false, text: `Không lấy được mã: ${r.last_mint_error ?? 'không rõ lý do'}` })
    } catch (e) {
      setMsg({ ok: false, text: `Không lấy được mã: ${(e as Error).message}` })
    } finally {
      setBusy(null)
    }
  }

  const pausedMin = st && st.paused_for_s > 0 ? Math.ceil(st.paused_for_s / 60) : 0

  return (
    <Card as="section">
      <SectionHeader title="Douyin tự ký (miễn phí, trước Apify)" subtitle="Mã khách dùng chung để gọi Douyin trực tiếp" />
      <div className="flex flex-col gap-4 p-4">
        {loadErr && (
          <div className="rounded-control border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{loadErr}</div>
        )}
        {!st && !loadErr && <p className="text-sm text-fg-muted">Đang tải...</p>}

        {st && (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Trạng thái mã khách"
                tone={st.has_jar ? 'success' : 'default'}
                value={st.has_jar ? 'Có' : 'Chưa có'}
                hint={st.has_jar && st.age_min != null ? `Tạo ${Math.round(st.age_min)} phút trước` : undefined} />
              <StatCard label="Thành công / thất bại"
                value={`${st.ok.toLocaleString('vi-VN')} / ${st.fail.toLocaleString('vi-VN')}`} />
              <StatCard label="Lượt gọi phút này / giờ này"
                value={`${st.calls_this_min}/${st.per_min} · ${st.calls_this_hour}/${st.per_hour}`} />
              <StatCard label="Tạm nghỉ"
                tone={pausedMin ? 'warning' : 'default'}
                value={pausedMin ? `còn ${pausedMin} phút` : 'Không'} />
            </div>
            <div className="space-y-1 text-xs text-fg-muted">
              {st.cooling_down && <p>Đang chờ 10 phút trước khi thử lấy mã lại</p>}
              {st.last_fail && <p className="break-words">Lỗi gần nhất: {st.last_fail}</p>}
              {st.last_mint_error && <p className="break-words">Lỗi lấy mã: {st.last_mint_error}</p>}
            </div>
          </>
        )}

        {form && st && (
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); save() }}>
            <label className="flex items-center gap-2 text-sm text-fg">
              <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
              Bật bước tự ký
            </label>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field id="dv-url" label="Địa chỉ dịch vụ lấy mã">
                <input id="dv-url" className={INPUT} value={form.mint_url} placeholder="https://douyin-mint...."
                  autoComplete="off" onChange={(e) => set('mint_url', e.target.value)} />
              </Field>
              <Field id="dv-key" label="Khoá dịch vụ">
                <input id="dv-key" type="password" className={INPUT} value={form.mint_key} autoComplete="new-password"
                  placeholder={st.mint_key_set ? 'Đã đặt — để trống nếu giữ nguyên' : 'Dán MINT_KEY'}
                  onChange={(e) => set('mint_key', e.target.value)} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Field id="dv-age" label="Tuổi tối đa của mã (giờ)">
                <input id="dv-age" type="number" min={0} className={INPUT} value={form.max_age_h} onChange={(e) => set('max_age_h', e.target.value)} />
              </Field>
              <Field id="dv-pm" label="Trần lượt/phút">
                <input id="dv-pm" type="number" min={0} className={INPUT} value={form.per_min} onChange={(e) => set('per_min', e.target.value)} />
              </Field>
              <Field id="dv-ph" label="Trần lượt/giờ">
                <input id="dv-ph" type="number" min={0} className={INPUT} value={form.per_hour} onChange={(e) => set('per_hour', e.target.value)} />
              </Field>
              <Field id="dv-pause" label="Thời gian tạm nghỉ (phút)">
                <input id="dv-pause" type="number" min={0} className={INPUT} value={form.pause_min} onChange={(e) => set('pause_min', e.target.value)} />
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" variant="primary" disabled={busy !== null}>{busy === 'save' ? 'Đang lưu...' : 'Lưu'}</Button>
              <Button onClick={mint} disabled={busy !== null}>{busy === 'mint' ? 'Đang lấy mã...' : 'Lấy mã mới ngay'}</Button>
              {msg && (
                <span role="status" className={`text-sm ${msg.ok ? 'text-success' : 'text-danger'}`}>{msg.text}</span>
              )}
            </div>
          </form>
        )}

        <p className="text-[11px] text-fg-muted">
          Dùng chữ ký web của Douyin với một mã khách dùng chung. Douyin chặn theo IP khi gọi quá dày, nên có trần lượt và tự tạm nghỉ; khi bỏ qua, hệ thống chuyển sang Apify.
        </p>
      </div>
    </Card>
  )
}
