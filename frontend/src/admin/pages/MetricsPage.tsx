import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { metricsApi } from '../api/metrics'
import type {
  AdoptionPayload, CostSource, CostsPayload, MetricsDays, MetricsSettings, SuccessCell, SuccessDay, SuccessPayload,
} from '../api/metrics'
import { PageHeader } from '../shared/PageHeader'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'
import { SectionHeader } from '../shared/SectionHeader'
import { TABLE, TABLE_SCROLL, TD, TD_MONO, TD_NUM, TH, TH_NUM, TR } from '../shared/tableStyles'
import { usd } from '../panels/apify/ApifyPoolManager'

/**
 * Đo lường (Phase 34C, task #6334). Tỉ lệ tải thành công, chi phí theo nền
 * tảng / nguồn / người dùng, mức dùng trần, và mức dùng tính năng. Số nào
 * dữ liệu hiện tại không cho ra được thì hiện "chưa đo được", không hiện 0.
 */

// wording: BA review
export const NOT_MEASURED = 'chưa đo được'
const dash = '—'

const n = (v: number | null | undefined) => (v == null ? dash : v.toLocaleString('vi-VN'))
export const pct = (v: number | null | undefined) =>
  v == null ? dash : `${(v * 100).toLocaleString('vi-VN', { maximumFractionDigits: 1 })}%`
const money = (v: number | null | undefined) => (v == null ? NOT_MEASURED : usd(v, 4))

// wording: BA review
const SOURCE_VI: Record<string, string> = {
  apify_china: 'Apify (lớp Trung Quốc)',
  apify_watch: 'Apify (Theo dõi kênh)',
  proxy: 'Proxy YouTube',
  asr: 'Phiên âm (ASR)',
  cobalt: 'Cobalt',
  storage: 'Lưu trữ',
  host_share: 'Phần chia máy chủ',
}
const COST_COLS: CostSource[] = ['apify_china', 'apify_watch', 'proxy']

function Muted({ children }: { children: ReactNode }) {
  return <span className="text-fg-muted">{children}</span>
}

/** Latency label: null samples = not measured; 0 samples = no success to time. */
export function latencyText(c: Pick<SuccessCell, 'latency_samples'> & { label: string | null }): ReactNode {
  if (c.latency_samples == null) return <Muted>{NOT_MEASURED}</Muted>
  if (!c.latency_samples || !c.label) return dash
  return c.label
}

function rateCell(d: SuccessDay): ReactNode {
  if (!d.available) return <Muted>{NOT_MEASURED}</Muted>
  if (!d.attempts) return dash
  return (
    <span title={`${d.successes}/${d.attempts}`}
      className={d.success_rate != null && d.success_rate < 0.85 ? 'text-warning' : undefined}>
      {pct(d.success_rate)}
    </span>
  )
}

function SuccessSection({ s }: { s: SuccessPayload }) {
  const rows = [...s.platforms.map((p) => ({ name: p.platform, c: p.totals })), { name: 'Tổng', c: s.totals }]
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Tải thành công" subtitle={`Theo nền tảng, ${s.days} ngày (ngày ${s.day_basis})`} />
      {s.platforms.length === 0 ? (
        <p className="px-4 py-3 text-sm text-fg-muted">Chưa có lượt tải nào trong kỳ.</p>
      ) : (
        <>
          <div className={TABLE_SCROLL}>
            <table className={TABLE}>
              <thead>
                <tr>
                  {/* wording: BA review */}
                  <th className={TH}>Nền tảng</th>
                  <th className={TH_NUM}>Lượt thử</th>
                  <th className={TH_NUM}>Thành công</th>
                  <th className={TH_NUM}>Tỉ lệ</th>
                  <th className={TH_NUM}>Thời gian tới kết quả p50</th>
                  <th className={TH_NUM}>p95</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ name, c }) => (
                  <tr key={name} className={TR}>
                    <td className={`${TD} ${name === 'Tổng' ? 'font-semibold' : ''}`}>{name}</td>
                    <td className={TD_NUM}>{n(c.attempts)}</td>
                    <td className={TD_NUM}>{n(c.successes)}</td>
                    <td className={TD_NUM}>{c.attempts ? pct(c.success_rate) : dash}</td>
                    <td className={TD_NUM}>{latencyText({ latency_samples: c.latency_samples, label: c.p50_label })}</td>
                    <td className={TD_NUM}>{latencyText({ latency_samples: c.latency_samples, label: c.p95_label })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${TABLE_SCROLL} border-t border-line`}>
            <table className={TABLE}>
              <thead>
                <tr>
                  {/* wording: BA review */}
                  <th className={TH}>Tỉ lệ theo ngày</th>
                  {s.dates.map((d) => <th key={d} className={TH_NUM}>{d.slice(5)}</th>)}
                </tr>
              </thead>
              <tbody>
                {s.platforms.map((p) => (
                  <tr key={p.platform} className={TR}>
                    <td className={TD}>{p.platform}</td>
                    {p.per_day.map((d) => <td key={d.date} className={TD_NUM}>{rateCell(d)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <ul className="list-disc space-y-1 border-t border-line px-8 py-3 text-[11px] text-fg-muted">
        {s.notes.map((t) => <li key={t}>{t}</li>)}
      </ul>
    </Card>
  )
}

function CostsSection({ c }: { c: CostsPayload }) {
  const notMeasured = Object.entries(c.sources).filter(([, v]) => !v.available)
  const srcAvail = (s: CostSource) => c.sources[s]?.available !== false
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Chi phí" subtitle={`Theo nền tảng và nguồn, ${c.days} ngày (ngày ${c.day_basis}). Tổng đo được: ${money(c.total_usd)}`} />
      <div className={TABLE_SCROLL}>
        <table className={TABLE}>
          <thead>
            <tr>
              {/* wording: BA review */}
              <th className={TH}>Nền tảng</th>
              {COST_COLS.map((s) => <th key={s} className={TH_NUM}>{SOURCE_VI[s]}</th>)}
              <th className={TH_NUM}>Lượt thành công</th>
              <th className={TH_NUM}>Chi phí / lượt thành công</th>
            </tr>
          </thead>
          <tbody>
            {c.platforms.map((p) => (
              <tr key={p.platform} className={TR}>
                <td className={TD}>{p.platform}</td>
                {COST_COLS.map((s) => (
                  <td key={s} className={TD_NUM}>
                    {p.by_source_usd[s] != null ? usd(p.by_source_usd[s], 4)
                      : srcAvail(s) ? dash : <Muted>{NOT_MEASURED}</Muted>}
                  </td>
                ))}
                <td className={TD_NUM}>{n(p.successes)}</td>
                <td className={TD_NUM}>{p.successes ? usd(p.cost_per_success_usd, 5) : dash}</td>
              </tr>
            ))}
            <tr className={TR}>
              <td className={TD}>{SOURCE_VI.asr}</td>
              <td className={TD_NUM} colSpan={COST_COLS.length}>
                {c.asr_total_usd == null ? <Muted>{NOT_MEASURED}</Muted> : usd(c.asr_total_usd, 4)}
              </td>
              <td className={TD_NUM}>{dash}</td>
              <td className={TD_NUM}><Muted>không gắn nền tảng</Muted></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className={`${TABLE_SCROLL} border-t border-line`}>
        <table className={TABLE}>
          <thead>
            <tr>
              {/* wording: BA review */}
              <th className={TH}>Ngày</th>
              {COST_COLS.map((s) => <th key={s} className={TH_NUM}>{SOURCE_VI[s]}</th>)}
              <th className={TH_NUM}>{SOURCE_VI.asr}</th>
              <th className={TH_NUM}>Tổng</th>
            </tr>
          </thead>
          <tbody>
            {[...c.per_day].reverse().map((d) => {
              const sum = (s: CostSource) => {
                const vals = Object.values(d.platforms).map((p) => p[s]).filter((v): v is number => v != null)
                return vals.length ? vals.reduce((a, b) => a + b, 0) : null
              }
              return (
                <tr key={d.date} className={TR}>
                  <td className={TD_MONO}>{d.date}</td>
                  {COST_COLS.map((s) => {
                    const v = sum(s)
                    return <td key={s} className={TD_NUM}>{v != null ? usd(v, 4) : srcAvail(s) ? dash : <Muted>{NOT_MEASURED}</Muted>}</td>
                  })}
                  <td className={TD_NUM}>{d.asr_usd == null ? <Muted>{NOT_MEASURED}</Muted> : usd(d.asr_usd, 4)}</td>
                  <td className={TD_NUM}>{usd(d.total_usd, 4)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 border-t border-line p-4 lg:grid-cols-2">
        <div>
          {/* wording: BA review */}
          <h3 className="mb-2 font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted">10 tài khoản tốn nhất</h3>
          {c.top_users == null ? (
            <p className="text-xs text-fg-muted">{NOT_MEASURED}</p>
          ) : c.top_users.length === 0 ? (
            <p className="text-xs text-fg-muted">Chưa có chi phí gắn với tài khoản.</p>
          ) : (
            <ol className="space-y-1 text-xs">
              {c.top_users.map((u) => (
                <li key={u.user_id} className="flex justify-between gap-3">
                  <span className="truncate font-mono" title={u.user_id}>{u.user_id}</span>
                  <span className="font-mono tabular-nums">{usd(u.cost_usd, 4)}</span>
                </li>
              ))}
            </ol>
          )}
          {/* wording: BA review */}
          <p className="mt-2 text-xs text-fg-2">
            Không gắn được người dùng (khách): {c.unattributed.available ? usd(c.unattributed.guests_usd, 4) : NOT_MEASURED}
            {' · '}Hệ thống: {c.unattributed.available ? usd(c.unattributed.system_usd, 4) : NOT_MEASURED}
          </p>
        </div>
        <div>
          {/* wording: BA review */}
          <h3 className="mb-2 font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted">Mức dùng trần ngày</h3>
          <div className={TABLE_SCROLL}>
            <table className={TABLE}>
              <thead>
                <tr>
                  <th className={TH}>Nhà cung cấp</th>
                  <th className={TH_NUM}>Trần / ngày</th>
                  <th className={TH_NUM}>TB / ngày</th>
                  <th className={TH_NUM}>Cao nhất</th>
                  <th className={TH_NUM}>Gợi ý trần (2–3× TB)</th>
                </tr>
              </thead>
              <tbody>
                {c.ceilings.map((r) => (
                  <tr key={r.provider} className={TR}>
                    <td className={TD}>{r.provider}</td>
                    <td className={TD_NUM}>{usd(r.daily_ceiling_usd)}</td>
                    <td className={TD_NUM}>
                      {r.available ? <>{usd(r.avg_daily_usd, 4)} <Muted>({r.avg_pct_of_ceiling ?? dash}%)</Muted></> : <Muted>{NOT_MEASURED}</Muted>}
                    </td>
                    <td className={`${TD_NUM} ${(r.peak_pct_of_ceiling ?? 0) >= 80 ? 'text-warning' : ''}`}>
                      {r.available ? `${r.peak_pct_of_ceiling ?? dash}%` : dash}
                    </td>
                    <td className={TD_NUM}>
                      {r.suggested_ceiling_usd ? `${usd(r.suggested_ceiling_usd[0], 4)}–${usd(r.suggested_ceiling_usd[1], 4)}` : dash}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <ul className="list-disc space-y-1 border-t border-line px-8 py-3 text-[11px] text-fg-muted">
        {c.notes.map((t) => <li key={t}>{t}</li>)}
        {notMeasured.map(([k, v]) => <li key={k}>{SOURCE_VI[k] ?? k}: {v.note ?? NOT_MEASURED}</li>)}
      </ul>
    </Card>
  )
}

function AdoptionSection({ a }: { a: AdoptionPayload }) {
  const na = (x: { available: boolean; note?: string }) => <Muted>{NOT_MEASURED}{x.note ? ` — ${x.note}` : ''}</Muted>
  const retention = (k: 'd1' | 'd7') => {
    if (!a.retention.available) return na(a.retention)
    const r = a.retention[k]
    return r.cohort_users ? <>{pct(r.rate)} <Muted>({r.returned}/{r.cohort_users})</Muted></> : dash
  }
  // wording: BA review
  const rows: [string, ReactNode][] = [
    ['Đăng ký mới', a.registrations.available ? n(a.registrations.total) : na(a.registrations)],
    ['Quay lại ngày 1 (D1)', retention('d1')],
    ['Quay lại ngày 7 (D7)', retention('d7')],
    ['Tài khoản đang theo dõi kênh', a.watch.available ? n(a.watch.accounts) : na(a.watch)],
    ['Nguồn đang theo dõi / tài khoản', a.watch.available
      ? <>{n(a.watch.active_sources)} nguồn · TB {a.watch.avg_per_account ?? dash} · tối đa {a.watch.max_per_account ?? dash}
        <Muted> (1: {a.watch.distribution['1']} · 2–5: {a.watch.distribution['2-5']} · 6+: {a.watch.distribution['6+']})</Muted></>
      : na(a.watch)],
    ['Hộp thư', a.inbox.available
      ? <>{n(a.inbox.active_in_period)} tài khoản trong kỳ <Muted>/ {n(a.inbox.accounts_marked_read)} từ trước tới nay</Muted></>
      : na(a.inbox)],
    ['Máy App Windows', a.app_devices.available
      ? <>{n(a.app_devices.active_in_period)} đang dùng · {n(a.app_devices.new_in_period)} mới <Muted>/ {n(a.app_devices.total)} tổng</Muted></>
      : na(a.app_devices)],
    ['Phiên bản App Windows', a.app_devices.available
      ? (a.app_devices.versions.length ? a.app_devices.versions.map((v) => `${v.version}: ${v.machines}`).join(' · ') : dash)
      : na(a.app_devices)],
    ['Phiên bản tiện ích', na(a.extension_versions)],
  ]
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Mức dùng tính năng" subtitle={`${a.from} → ${a.to} (${a.day_basis})`} />
      <div className={TABLE_SCROLL}>
        <table className={TABLE}>
          <thead>
            <tr>
              <th className={TH}>Chỉ số</th>
              <th className={TH}>Giá trị</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k} className={TR}>
                <td className={TD}>{k}</td>
                <td className={`${TD} text-xs`}>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {a.registrations.available && (
        <div className={`${TABLE_SCROLL} border-t border-line`}>
          <table className={TABLE}>
            <thead>
              <tr>
                {/* wording: BA review */}
                <th className={TH}>Đăng ký / ngày</th>
                {a.registrations.per_day.map((d) => <th key={d.date} className={TH_NUM}>{d.date.slice(5)}</th>)}
              </tr>
            </thead>
            <tbody>
              <tr className={TR}>
                <td className={TD}>Tài khoản mới</td>
                {a.registrations.per_day.map((d) => <td key={d.date} className={TD_NUM}>{d.count}</td>)}
              </tr>
            </tbody>
          </table>
        </div>
      )}
      {a.retention.available && <p className="border-t border-line px-4 py-3 text-[11px] text-fg-muted">{a.retention.note}</p>}
    </Card>
  )
}

function Section<T>({ data, err, children }: { data: T | null; err: string | null; children: (d: T) => ReactNode }) {
  if (err) return <div className="rounded-control border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{err}</div>
  if (!data) return <p className="text-sm text-fg-muted">Đang tải...</p>
  return <>{children(data)}</>
}

export default function MetricsPage() {
  const [days, setDays] = useState<MetricsDays>(7)
  const [success, setSuccess] = useState<SuccessPayload | null>(null)
  const [costs, setCosts] = useState<CostsPayload | null>(null)
  const [adoption, setAdoption] = useState<AdoptionPayload | null>(null)
  const [errs, setErrs] = useState<Record<string, string | null>>({})
  const [settings, setSettings] = useState<MetricsSettings | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const run = async <T,>(key: string, fn: () => Promise<T>, set: (v: T) => void) => {
      try {
        set(await fn())
        setErrs((e) => ({ ...e, [key]: null }))
      } catch (e) {
        setErrs((prev) => ({ ...prev, [key]: (e as Error).message }))
      }
    }
    await Promise.all([
      run('success', () => metricsApi.success(days), setSuccess),
      run('costs', () => metricsApi.costs(days), setCosts),
      run('adoption', () => metricsApi.adoption(days), setAdoption),
      run('settings', () => metricsApi.settings(), setSettings),
    ])
  }, [days])

  useEffect(() => { load() }, [load])

  const toggleWeekly = async () => {
    if (!settings) return
    setBusy(true)
    try {
      setSettings(await metricsApi.setWeekly(!settings.weekly_report_enabled))
      setMsg(null)
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const sendNow = async () => {
    setBusy(true)
    try {
      const r = await metricsApi.sendWeekly()
      // wording: BA review
      setMsg(r.sent ? 'Đã gửi báo cáo tuần qua Telegram.' : 'Chưa gửi được: Telegram chưa cấu hình hoặc gửi lỗi.')
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        // wording: BA review
        title="Đo lường"
        description="Tỉ lệ tải thành công, chi phí trên mỗi lượt tải thành công và mức dùng tính năng. Số nào chưa có dữ liệu sẽ ghi 'chưa đo được'."
        actions={
          <>
            <div className="flex gap-1" role="tablist">
              {([7, 30] as MetricsDays[]).map((d) => (
                <Button key={d} size="sm" variant={days === d ? 'primary' : 'ghost'}
                  aria-pressed={days === d} onClick={() => setDays(d)}>{d} ngày</Button>
              ))}
            </div>
            <Button onClick={load}>Tải lại</Button>
          </>
        }
      />

      <Card as="section" padding="md">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0 text-sm">
            {/* wording: BA review */}
            <p className="font-medium text-fg">Báo cáo tuần qua Telegram</p>
            <p className="text-xs text-fg-2">
              {settings?.weekly_report_schedule ?? 'Thứ Hai 08:00 giờ Việt Nam'} ·{' '}
              {settings == null ? dash : settings.weekly_report_enabled ? 'Đang bật' : 'Đang tắt'}
            </p>
          </div>
          <div className="flex gap-2">
            <Button disabled={busy || !settings} onClick={toggleWeekly}>
              {settings?.weekly_report_enabled ? 'Tắt báo cáo tuần' : 'Bật báo cáo tuần'}
            </Button>
            <Button variant="primary" disabled={busy} onClick={sendNow}>Gửi thử ngay</Button>
          </div>
        </div>
        {msg && <p className="mt-2 text-xs text-fg-2">{msg}</p>}
      </Card>

      <Section data={success} err={errs.success ?? null}>{(s) => <SuccessSection s={s} />}</Section>
      <Section data={costs} err={errs.costs ?? null}>{(c) => <CostsSection c={c} />}</Section>
      <Section data={adoption} err={errs.adoption ?? null}>{(a) => <AdoptionSection a={a} />}</Section>
    </div>
  )
}
