import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { desktopAppApi, postDesktopAllowance, resetDesktopIp } from '../api/desktopApp'
import type { DesktopDevice, DesktopRoute, DevicesPayload, KindSummary, RouteGrid, StatsPayload } from '../api/desktopApp'
import { PageHeader } from '../shared/PageHeader'
import { StatCard } from '../shared/StatCard'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'
import { SectionHeader } from '../shared/SectionHeader'
import { StatusPill } from '../shared/StatusPill'
import { EmptyState } from '../shared/EmptyState'
import { ErrorState } from '../shared/ErrorState'
import { StatSkeleton, TableSkeleton } from '../shared/LoadingSkeleton'
import { TABLE, TABLE_SCROLL, TD, TD_MONO, TD_NUM, TH, TH_NUM, TR } from '../shared/tableStyles'
import { fmtTime } from '../panels/apify/ApifyPoolManager'

/**
 * App Windows (task #6087, PLAN-32D §7.1). Lượt tải app Windows tự làm trên
 * máy người dùng so với lượt qua máy chủ, chế độ đếm lượt, và danh sách máy.
 * Chỉ xem, không sửa. Ngày tính theo giờ UTC (lượt reset 07:00 giờ Việt Nam).
 */

/** Machines per page (same as the Apify token list). */
export const PAGE_SIZE = 10

const dash = '—'
const n = (v: number | null | undefined) => (v == null ? dash : v.toLocaleString('vi-VN'))

// wording: BA review
const ROUTE_VI: Record<DesktopRoute, string> = {
  local: 'Trên máy',
  local_cookie: 'Trên máy + đăng nhập',
  server: 'Qua máy chủ',
}

// wording: BA review
const ENFORCE_FOR_VI: Record<string, string> = {
  user: 'tài khoản',
  device: 'khách dùng app',
  anon: 'khách trên web',
}

const ROUTES: DesktopRoute[] = ['local', 'local_cookie', 'server']

/** Columns per route in the daily table. */
const COLS: { key: string; label: string; get: (g: RouteGrid, r: DesktopRoute) => number; warn?: boolean }[] = [
  // wording: BA review
  { key: 'ok', label: 'Trong hạn mức', get: (g, r) => g[r].ok },
  { key: 'over', label: 'Vượt lượt (chỉ đếm)', get: (g, r) => g[r].over_shadow, warn: true },
  { key: 'refused', label: 'Bị chặn', get: (g, r) => g[r].refused },
  { key: 'failed', label: 'Lỗi / huỷ', get: (g, r) => g[r].settle_failed + g[r].settle_cancelled },
]

const pct = (v: number | null | undefined) =>
  v == null ? dash : `${(v * 100).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} %`

/**
 * PLAN-32E §2 decision table for the 14/10 gate (owner 07-10 filled 15–30 %).
 * Exported for tests.
 */
export function vcBand(r: number | null | undefined): { key: string; label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' } | null {
  // wording: BA review
  if (r == null) return null
  if (r < 0.02) return { key: 'lt2', label: 'Bật ngay', tone: 'success' }
  if (r <= 0.15) return { key: '2to15', label: 'Bật theo thứ tự', tone: 'success' }
  if (r <= 0.30) return { key: '15to30', label: 'Bật chậm, bước tài khoản 5 ngày', tone: 'warning' }
  return { key: 'gt30', label: 'Xem lại mức 5 lượt của khách', tone: 'danger' }
}

// wording: BA review
const VC_BANDS: { key: string; range: string; text: string }[] = [
  { key: 'lt2', range: '< 2 %', text: 'bật ngay' },
  { key: '2to15', range: '2–15 %', text: 'bật theo thứ tự, mỗi bước 3 ngày' },
  { key: '15to30', range: '15–30 %', text: 'bật chậm, bước tài khoản (S2) giữ 5 ngày' },
  { key: 'gt30', range: '> 30 %', text: 'xem lại mức 5 lượt của khách trước khi bật' },
]

function VcCard({ s }: { s: StatsPayload }) {
  const sum = s.summary
  const ratio = sum.vc_ratio ?? null
  const band = vcBand(ratio)
  const g = sum.by_kind?.guest
  const a = sum.by_kind?.account
  return (
    <Card as="section" className="ring-2 ring-warning/60">
      {/* wording: BA review */}
      <SectionHeader title="Tỉ lệ vượt lượt (V/C)"
        subtitle={`Lượt vượt hạn mức khi chỉ đếm chia cho lượt đã đếm, ${s.days} ngày. Dùng để quyết định có bật chặn 5/20 lượt.`} />
      <div className="flex flex-col gap-4 p-4 lg:flex-row lg:items-start">
        <div className="flex min-w-[200px] flex-col gap-2">
          <p className={`font-mono text-4xl font-semibold tabular-nums ${band?.tone === 'danger' ? 'text-danger' : band?.tone === 'warning' ? 'text-warning' : 'text-fg'}`}
            data-testid="vc-ratio">{pct(ratio)}</p>
          <p className="text-xs text-fg-muted">
            {/* wording: BA review */}
            V = {n(sum.over_shadow)} · C = {n(sum.counted_local + sum.counted_local_cookie + sum.counted_server)}
          </p>
          {band ? <StatusPill tone={band.tone} label={band.label} />
            : <span className="text-xs text-fg-muted">Chưa có lượt nào được đếm.</span>}
          {g && a && (
            <p className="text-xs text-fg-muted">
              {/* wording: BA review */}
              Khách {pct(g.vc_ratio)} · Tài khoản {pct(a.vc_ratio)}
            </p>
          )}
        </div>
        <ul className="flex-1 space-y-1 text-xs">
          {VC_BANDS.map((b) => (
            <li key={b.key}
              className={`rounded-control px-2 py-1 ${band?.key === b.key ? 'bg-warning-soft font-semibold text-fg' : 'text-fg-muted'}`}>
              <span className="inline-block w-16 font-mono">{b.range}</span>{b.text}
            </li>
          ))}
          {/* wording: BA review */}
          <li className="px-2 pt-1 text-[11px] text-fg-muted">
            Gợi ý theo kế hoạch 32E §2; nếu ≥ 70 % lượt vượt là của khách thì có thể bật cho khách sớm hơn. Chủ sản phẩm quyết định.
          </li>
        </ul>
      </div>
    </Card>
  )
}

/** Guest vs account rows for the period, and per day. */
function KindRow({ label, k }: { label: string; k: KindSummary }) {
  return (
    <tr className={TR}>
      <td className={TD}>{label}</td>
      <td className={TD_NUM}>{n(k.counted)}</td>
      <td className={`${TD_NUM} ${k.over_shadow > 0 ? 'font-semibold text-warning' : ''}`}>{n(k.over_shadow)}</td>
      <td className={TD_NUM}>{pct(k.vc_ratio)}</td>
      <td className={TD_NUM}>{n(k.refused)}</td>
      <td className={TD_NUM}>{n(k.refunded)}</td>
      <td className={TD_NUM}>{n(k.retro)}</td>
    </tr>
  )
}

function KindPanel({ s }: { s: StatsPayload }) {
  const bk = s.summary.by_kind
  if (!bk) return null
  const days = s.per_day.filter((d) => d.by_kind)
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Khách và tài khoản"
        subtitle="Khách = máy chưa đăng nhập (và khách không gửi mã máy). Tài khoản dùng chung lượt với web. Chỉ có số từ khi bản đo P0 chạy." />
      <div className="flex flex-col gap-4 p-4">
        <div className={TABLE_SCROLL}>
          <table className={TABLE}>
            <thead>
              <tr>
                {/* wording: BA review */}
                <th className={TH}>{s.days} ngày</th>
                <th className={TH_NUM}>Đã đếm</th>
                <th className={TH_NUM}>Vượt lượt</th>
                <th className={TH_NUM}>V/C</th>
                <th className={TH_NUM}>Bị chặn</th>
                <th className={TH_NUM}>Hoàn lượt</th>
                <th className={TH_NUM}>Tải khi mất mạng</th>
              </tr>
            </thead>
            <tbody>
              <KindRow label="Khách" k={bk.guest} />
              <KindRow label="Tài khoản" k={bk.account} />
            </tbody>
          </table>
        </div>
        {days.length > 0 && (
          <div className={TABLE_SCROLL}>
            <table className={TABLE}>
              <thead>
                <tr>
                  {/* wording: BA review */}
                  <th className={TH}>Ngày (UTC)</th>
                  <th className={TH_NUM}>Khách đếm / vượt</th>
                  <th className={TH_NUM}>Tài khoản đếm / vượt</th>
                  <th className={TH_NUM}>V/C cả ngày</th>
                  <th className={TH_NUM}>Hoàn lượt</th>
                  <th className={TH_NUM}>Tải khi mất mạng</th>
                </tr>
              </thead>
              <tbody>
                {days.map((d) => (
                  <tr key={d.day} className={TR}>
                    <td className={TD_MONO}>{fmtDay(d.day)}</td>
                    <td className={TD_NUM}>{n(d.by_kind!.guest.counted)} / {n(d.by_kind!.guest.over_shadow)}</td>
                    <td className={TD_NUM}>{n(d.by_kind!.account.counted)} / {n(d.by_kind!.account.over_shadow)}</td>
                    <td className={TD_NUM}>{pct(d.vc_ratio)}</td>
                    <td className={TD_NUM}>{n(d.refunds)}</td>
                    <td className={TD_NUM}>{n(d.retro)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  )
}

function TopOverPanel({ s }: { s: StatsPayload }) {
  const rows = s.top_over_today
  if (!rows) return null
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Máy vượt lượt nhiều nhất hôm nay" subtitle="Tối đa 10, chỉ hiện mã. Tra mã ở bảng máy bên dưới." />
      <div className="p-4">
        {rows.length === 0
          ? <EmptyState compact title="Hôm nay chưa có máy nào vượt lượt" />
          : (
            <div className={TABLE_SCROLL}>
              <table className={TABLE}>
                <thead>
                  <tr>
                    {/* wording: BA review */}
                    <th className={TH}>Mã</th>
                    <th className={TH}>Loại</th>
                    <th className={TH_NUM}>Lượt vượt</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.kind}-${r.code}`} className={TR}>
                      <td className={TD_MONO}>{r.code}</td>
                      <td className={TD}>{r.kind === 'user' ? 'Tài khoản' : 'Máy (khách)'}</td>
                      <td className={`${TD_NUM} font-semibold text-warning`}>{n(r.over)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </Card>
  )
}

function VersionPanel({ s }: { s: StatsPayload }) {
  const rows = s.devices.versions
  if (!rows || !s.devices.storage_ready) return null
  const all = rows.reduce((a, r) => a + r.machines, 0)
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Phiên bản app đang dùng"
        subtitle={`Máy mở app trong ${s.days} ngày, theo phiên bản. App dưới 0.6.0 không báo về nên không có ở đây.`} />
      <div className="p-4">
        {rows.length === 0
          ? <EmptyState compact title="Chưa có máy nào mở app trong thời gian này" />
          : (
            <div className={TABLE_SCROLL}>
              <table className={TABLE}>
                <thead>
                  <tr>
                    {/* wording: BA review */}
                    <th className={TH}>Phiên bản</th>
                    <th className={TH_NUM}>Số máy</th>
                    <th className={TH_NUM}>Tỉ lệ</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.version ?? 'none'} className={TR}>
                      <td className={TD_MONO}>{r.version ?? 'Không rõ'}</td>
                      <td className={TD_NUM}>{n(r.machines)}</td>
                      <td className={TD_NUM}>{pct(all ? r.machines / all : null)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </Card>
  )
}

function fmtDay(day: string) {
  const [y, m, d] = day.split('-')
  return y && m && d ? `${d}/${m}` : day
}

function FlagRow({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-line py-2 last:border-0 sm:flex-row sm:items-start sm:gap-4">
      <dt className="w-56 flex-shrink-0 text-xs text-fg-muted">{label}</dt>
      <dd className="flex-1 text-sm text-fg">
        {value}
        {hint && <p className="mt-0.5 text-[11px] text-fg-muted">{hint}</p>}
      </dd>
    </div>
  )
}

function ModeCard({ s }: { s: StatsPayload }) {
  const f = s.flags
  // wording: BA review
  const mode = !f.client_quota_enabled
    ? { tone: 'neutral' as const, label: 'Đang tắt', hint: 'App tải như trước, không đếm lượt.' }
    : f.client_quota_mode === 'enforce'
      ? { tone: 'danger' as const, label: 'Chặn thật', hint: 'Hết lượt thì app dừng tải và mời đăng nhập hoặc nâng cấp.' }
      : { tone: 'warning' as const, label: 'Chỉ đếm', hint: 'Hết lượt app vẫn tải bình thường, hệ thống chỉ ghi lại để đo.' }
  const list = (xs: string[]) => (xs.length ? xs.join(', ') : 'Chưa bật nền tảng nào')
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Chế độ đếm lượt" subtitle="Đặt bằng biến môi trường trên máy chủ, trang này chỉ xem" />
      <dl className="px-4 py-2">
        {/* wording: BA review */}
        <FlagRow label="Đếm lượt tải của app" value={<StatusPill tone={mode.tone} label={mode.label} />} hint={mode.hint} />
        <FlagRow label="Áp dụng chặn cho"
          value={f.client_quota_enforce_for.map((k) => ENFORCE_FOR_VI[k] ?? k).join(', ') || dash}
          hint={f.client_quota_mode === 'enforce' ? undefined : 'Chỉ có tác dụng khi chuyển sang "Chặn thật".'} />
        {/* wording: BA review */}
        {f.client_quota_enforce_users_count != null && (
          <FlagRow label="Chặn thử cho tài khoản"
            value={f.client_quota_enforce_users_count > 0 ? `${n(f.client_quota_enforce_users_count)} tài khoản được chọn` : 'Mọi tài khoản'}
            hint="Khi có danh sách, chỉ các tài khoản này bị chặn thật; tài khoản khác vẫn chỉ đếm." />
        )}
        {f.client_update_gate_enabled != null && (
          <FlagRow label="Bắt cập nhật app cũ" value={f.client_update_gate_enabled ? 'Đang bật' : 'Tắt'}
            hint={`App cũ hơn ${f.desktop_min_version ?? '—'} sẽ được yêu cầu cập nhật trước khi tải.`} />
        )}
        <FlagRow label="Hạn mức mỗi ngày" value={`Khách ${n(f.limit_anon)} · Tài khoản ${n(f.limit_user)}`}
          hint={`Khách dùng app tính theo máy, mỗi mạng tối đa ${n(f.limit_anon * f.ip_mult)} lượt. Tài khoản dùng chung lượt với web.`} />
        <FlagRow label="Tải khi mất mạng" value={`${n(f.offline_grace)} lượt/ngày`} hint="Có mạng lại thì app báo bù, vẫn được tính vào hạn mức." />
        {/* wording: BA review */}
        <FlagRow label="Hoàn lượt khi tải lỗi"
          value={f.refund_daily_max_guest == null
            ? `Tối đa ${n(f.refund_daily_max)} lượt/ngày`
            : `Tài khoản tối đa ${n(f.refund_daily_max)} · Khách tối đa ${n(f.refund_daily_max_guest)} lượt/ngày`} />
        <FlagRow label="Tải bằng đăng nhập trong app" value={list(f.cookie_platforms)} />
        <FlagRow label="Lỗi trên máy thì thử qua máy chủ" value={list(f.server_fallback_platforms)} />
        <FlagRow label="Phiên bản app" value={`Mới nhất ${f.desktop_latest_version} · Tối thiểu ${f.desktop_min_version}`} />
      </dl>
    </Card>
  )
}

function StatsTable({ s }: { s: StatsPayload }) {
  const rows = [...s.per_day]
  return (
    <div className={TABLE_SCROLL}>
      <table className={TABLE}>
        <thead>
          <tr>
            <th className={TH} rowSpan={2}>Ngày (UTC)</th>
            {ROUTES.map((r) => (
              <th key={r} className={`${TH} border-l border-line text-center`} colSpan={COLS.length}>{ROUTE_VI[r]}</th>
            ))}
            <th className={`${TH_NUM} border-l border-line`} rowSpan={2}>Máy mới</th>
          </tr>
          <tr>
            {ROUTES.flatMap((r) => COLS.map((c, i) => (
              <th key={`${r}-${c.key}`} className={`${TH_NUM} ${i === 0 ? 'border-l border-line' : ''}`}>{c.label}</th>
            )))}
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.day} className={TR}>
              <td className={TD_MONO}>{fmtDay(d.day)}</td>
              {ROUTES.flatMap((r) => COLS.map((c, i) => {
                const v = c.get(d.routes, r)
                return (
                  <td key={`${r}-${c.key}`}
                    className={`${TD_NUM} ${i === 0 ? 'border-l border-line' : ''} ${c.warn && v > 0 ? 'font-semibold text-warning' : ''} ${v === 0 ? 'text-fg-muted' : ''}`}>
                    {n(v)}
                  </td>
                )
              }))}
              <td className={`${TD_NUM} border-l border-line`}>{n(d.new_devices)}</td>
            </tr>
          ))}
          <tr className={`${TR} bg-surface-2 font-semibold`}>
            <td className={TD}>Tổng</td>
            {ROUTES.flatMap((r) => COLS.map((c, i) => (
              <td key={`t-${r}-${c.key}`}
                className={`${TD_NUM} ${i === 0 ? 'border-l border-line' : ''} ${c.warn && c.get(s.totals, r) > 0 ? 'text-warning' : ''}`}>
                {n(c.get(s.totals, r))}
              </td>
            )))}
            <td className={`${TD_NUM} border-l border-line`}>{n(s.devices.new_in_period)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

function usageText(d: DesktopDevice) {
  const t = d.today
  return t.limit == null || t.limit === -1 ? n(t.used) : `${n(t.used)}/${n(t.limit)}`
}

type AllowanceAction = 'grant' | 'reset' | 'ip'

/** Task #6125: grant extra downloads for today / reset today's count for one machine. Mode 'ip' (PLAN-32E): clear today's shared guest count of the machine's network. */
function AllowanceDialog({ device, action, onClose, onDone }: {
  device: DesktopDevice
  action: AllowanceAction
  onClose: () => void
  onDone: (notice: string) => void
}) {
  const [amount, setAmount] = useState('5')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    function onEsc(e: KeyboardEvent) { if (e.key === 'Escape' && !busy) onClose() }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [busy, onClose])

  const grant = action === 'grant'
  const ipMode = action === 'ip'
  const amountNum = Number(amount)
  const amountOk = !grant || (Number.isInteger(amountNum) && amountNum >= 1 && amountNum <= 50)
  const reasonLen = reason.trim().length
  const reasonOk = reasonLen >= 3 && reasonLen <= 300
  const byAccount = device.today.counted_as === 'user'

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy || !amountOk || !reasonOk) return
    setBusy(true)
    setError(null)
    try {
      if (ipMode) {
        const res = await resetDesktopIp(device.last_ip ?? '', reason.trim())
        // wording: BA review
        onDone(`Đã đặt lại lượt khách của mạng ${res.ip} (${res.removed} lượt hôm nay).`)
        return
      }
      await postDesktopAllowance({
        device_id: device.id, action, reason: reason.trim(),
        ...(grant ? { amount: amountNum } : {}),
      })
      // wording: BA review
      const who = byAccount ? `tài khoản ${device.user_email || device.user_id}` : `máy ${device.code}`
      onDone(grant ? `Đã cộng ${amountNum} lượt hôm nay cho ${who}.` : `Đã đặt lại lượt hôm nay của ${who}.`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  const field = 'w-full rounded-control border border-line bg-surface px-3 py-2 text-sm text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'

  return (
    <>
      <div className="fixed inset-0 z-40 bg-surface-2 backdrop-blur-sm" onClick={busy ? undefined : onClose} aria-hidden />
      <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4">
        {/* wording: BA review */}
        <form onSubmit={submit} role="dialog" aria-modal aria-label={grant ? 'Cộng lượt' : ipMode ? 'Đặt lại IP' : 'Đặt lại lượt'}
          className="relative w-full max-w-md rounded-card border border-line bg-surface shadow-2xl">
          <div className="border-b border-line px-5 py-4">
            <h2 className="text-sm font-semibold text-fg">{grant ? 'Cộng lượt tải hôm nay' : ipMode ? 'Đặt lại lượt khách của mạng' : 'Đặt lại lượt hôm nay'}</h2>
            <p className="mt-1 text-xs text-fg-muted">
              Chỉ áp dụng cho hôm nay (ngày tính theo giờ UTC), sang ngày mới trở về mặc định.{' '}
              {ipMode
                ? <>Xoá lượt khách dùng chung hôm nay của mạng <b className="font-mono text-fg-2">{device.last_ip}</b> (ví dụ nhiều máy khách cùng một văn phòng). Lượt riêng của từng máy giữ nguyên.</>
                : byAccount
                ? <>Lượt đang tính cho <b className="text-fg-2">tài khoản {device.user_email || device.user_id}</b>, nên áp dụng cho cả web và mọi máy của tài khoản này.</>
                : <>Lượt đang tính cho <b className="text-fg-2">máy {device.code}</b>.</>}
            </p>
          </div>
          <div className="flex flex-col gap-4 px-5 py-4">
            {grant && (
              <div>
                <label htmlFor="allow-amount" className="mb-1.5 block text-xs font-medium text-fg-muted">Số lượt cộng thêm (1–50)</label>
                <input id="allow-amount" type="number" min={1} max={50} step={1} className={field}
                  value={amount} onChange={(e) => setAmount(e.target.value)} disabled={busy} />
                {!amountOk && <p className="mt-1 text-[11px] text-danger">Nhập số nguyên từ 1 đến 50.</p>}
              </div>
            )}
            <div>
              <label htmlFor="allow-reason" className="mb-1.5 block text-xs font-medium text-fg-muted">
                Lý do<span className="ml-0.5 text-danger">*</span>
              </label>
              <textarea id="allow-reason" rows={3} maxLength={300} className={`${field} resize-y`}
                value={reason} onChange={(e) => setReason(e.target.value)} disabled={busy}
                placeholder="Ví dụ: khách báo lỗi tải, đã xác nhận qua email" />
              <p className={`mt-1 text-[11px] ${reasonLen > 0 && !reasonOk ? 'text-danger' : 'text-fg-muted'}`}>
                {reasonLen}/300 ký tự, tối thiểu 3.
              </p>
            </div>
            {error && (
              <div role="alert" className="rounded-control border border-danger/30 bg-danger-soft px-3 py-2 text-xs text-danger">{error}</div>
            )}
          </div>
          <div className="flex items-center justify-end gap-2 border-t border-line px-5 py-3">
            <Button variant="ghost" onClick={onClose} disabled={busy}>Huỷ</Button>
            <Button type="submit" variant="primary" disabled={busy || !amountOk || !reasonOk}>
              {busy ? 'Đang xử lý…' : grant ? 'Cộng lượt' : ipMode ? 'Đặt lại IP' : 'Đặt lại'}
            </Button>
          </div>
        </form>
      </div>
    </>
  )
}

function pageList(current: number, count: number): (number | '…')[] {
  const keep = new Set([1, count, current - 1, current, current + 1])
  const out: (number | '…')[] = []
  for (let i = 1; i <= count; i++) {
    if (keep.has(i)) out.push(i)
    else if (out[out.length - 1] !== '…') out.push('…')
  }
  return out
}

function DevicesPanel() {
  const [query, setQuery] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<DevicesPayload | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [dlg, setDlg] = useState<{ device: DesktopDevice; action: AllowanceAction } | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  // Search runs on the server; wait for the admin to stop typing.
  useEffect(() => {
    const t = setTimeout(() => { setQ(query.trim()); setPage(1) }, 300)
    return () => clearTimeout(t)
  }, [query])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await desktopAppApi.devices(q, PAGE_SIZE, (page - 1) * PAGE_SIZE))
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [q, page])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(t)
  }, [notice])

  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const devices = data?.devices ?? []

  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Máy đã dùng app"
        subtitle="Mỗi máy Windows một mã. Máy có tài khoản đăng nhập thì lượt hôm nay là lượt của tài khoản (chung với web)."
        right={data?.storage_ready ? <span className="text-xs text-fg-muted">{n(total)} máy</span> : undefined} />
      <div className="flex flex-col gap-3 p-4">
        <input
          className="h-9 w-full max-w-sm rounded-control border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          value={query} onChange={(e) => setQuery(e.target.value)}
          // wording: BA review
          placeholder="Tìm theo mã máy, tên máy, email hoặc mã tài khoản" aria-label="Tìm máy" />

        {notice && (
          <div role="status" className="rounded-control border border-success/30 bg-success-soft px-4 py-2 text-sm text-success">{notice}</div>
        )}

        {err ? (
          <ErrorState compact title="Không tải được danh sách máy" message={err} onRetry={load} />
        ) : !data && loading ? (
          <TableSkeleton rows={5} />
        ) : data && !data.storage_ready ? (
          // wording: BA review
          <EmptyState compact title="Chưa có bảng lưu máy"
            description="Cần chạy migration 037_desktop_devices.sql trên Supabase. App vẫn tải bình thường." />
        ) : devices.length === 0 ? (
          q
            ? <EmptyState compact title="Không có máy nào khớp" description={`Không tìm thấy "${q}".`} />
            : <EmptyState compact title="Chưa có máy nào" description="Máy sẽ xuất hiện khi app 0.6.0 trở lên mở lần đầu." />
        ) : (
          <>
            <div className={`${TABLE_SCROLL} ${loading ? 'opacity-60' : ''}`}>
              <table className={TABLE}>
                <thead>
                  <tr>
                    {/* wording: BA review */}
                    <th className={TH}>Mã máy</th>
                    <th className={TH}>Tên máy</th>
                    <th className={TH}>Phiên bản</th>
                    <th className={TH}>Tài khoản</th>
                    <th className={TH_NUM}>Hôm nay đã dùng</th>
                    <th className={TH}>Lần đầu</th>
                    <th className={TH}>Lần cuối</th>
                    <th className={TH}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => (
                    <tr key={d.id} className={TR}>
                      <td className={TD_MONO}>{d.code}</td>
                      <td className={TD}>
                        <span className="block max-w-[240px] truncate" title={d.display_name ?? undefined}>{d.display_name || dash}</span>
                        {d.last_ip && (
                          <span className="flex items-center gap-1 font-mono text-[11px] text-fg-muted">
                            {d.last_ip}
                            {/* wording: BA review */}
                            <Button size="sm" variant="ghost" title="Xoá lượt khách dùng chung hôm nay của mạng này"
                              onClick={() => setDlg({ device: d, action: 'ip' })}>Đặt lại IP</Button>
                          </span>
                        )}
                      </td>
                      <td className={TD_MONO}>{d.client_version || dash}</td>
                      <td className={TD}>
                        {d.user_id
                          ? <span className="block max-w-[220px] truncate" title={d.user_id}>{d.user_email || d.user_id}</span>
                          : <span className="text-fg-muted">Khách</span>}
                      </td>
                      <td className={TD_NUM}>
                        <span className={d.today.limit != null && d.today.limit !== -1 && d.today.used > d.today.limit ? 'font-semibold text-warning' : ''}>
                          {usageText(d)}
                        </span>
                        {(d.today.refunds > 0 || d.today.retro > 0) && (
                          <span className="block text-[11px] text-fg-muted">
                            {[d.today.refunds > 0 ? `hoàn ${d.today.refunds}` : null,
                              d.today.retro > 0 ? `mất mạng ${d.today.retro}` : null].filter(Boolean).join(' · ')}
                          </span>
                        )}
                        {(d.today.bonus ?? 0) > 0 && (
                          // wording: BA review
                          <span className="block text-[11px] text-accent">(+{n(d.today.bonus)} admin)</span>
                        )}
                      </td>
                      <td className={`${TD} text-xs`}>{fmtTime(d.first_seen)}</td>
                      <td className={`${TD} text-xs`}>{fmtTime(d.last_seen)}</td>
                      <td className={TD}>
                        <div className="flex gap-1">
                          {/* wording: BA review */}
                          <Button size="sm" onClick={() => setDlg({ device: d, action: 'grant' })}>Cộng lượt</Button>
                          <Button size="sm" variant="ghost" onClick={() => setDlg({ device: d, action: 'reset' })}>Đặt lại</Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {pageCount > 1 && (
              <div className="flex flex-wrap items-center gap-1">
                <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>‹ Trước</Button>
                {pageList(page, pageCount).map((p, i) => p === '…'
                  ? <span key={`gap-${i}`} className="px-1 text-xs text-fg-muted">…</span>
                  : <Button key={p} size="sm" variant={p === page ? 'primary' : 'ghost'}
                      aria-current={p === page ? 'page' : undefined} onClick={() => setPage(p)}>{p}</Button>)}
                <Button size="sm" variant="ghost" disabled={page >= pageCount} onClick={() => setPage(page + 1)}>Sau ›</Button>
              </div>
            )}
          </>
        )}
      </div>
      {dlg && (
        <AllowanceDialog device={dlg.device} action={dlg.action} onClose={() => setDlg(null)}
          onDone={(msg) => { setDlg(null); setNotice(msg); load() }} />
      )}
    </Card>
  )
}

export default function DesktopAppPage() {
  const [stats, setStats] = useState<StatsPayload | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setStats(await desktopAppApi.stats(7))
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [load])

  const sum = stats?.summary
  const local = sum ? sum.counted_local + sum.counted_local_cookie : null

  return (
    <div className="flex flex-col gap-5">
      {/* wording: BA review */}
      <PageHeader
        title="App Windows"
        description="Lượt tải app Windows tự làm trên máy người dùng so với lượt qua máy chủ, và các máy đang dùng app. 7 ngày gần nhất, ngày tính theo giờ UTC."
        actions={<Button onClick={load}>Tải lại</Button>}
      />

      {err && <ErrorState compact title="Không tải được số liệu" message={err} onRetry={load} />}
      {stats && !stats.redis_ok && (
        <div className="rounded-control border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning">
          Không đọc được số liệu đếm lượt (Redis), các số bên dưới có thể bằng 0.
        </div>
      )}

      {!stats && !err ? (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => <StatSkeleton key={i} />)}
        </div>
      ) : stats && sum ? (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            {/* wording: BA review */}
            <div className="col-span-2 flex rounded-card ring-2 ring-warning/60 lg:col-span-1">
              <StatCard className="w-full" label="Vượt lượt khi chỉ đếm" tone={sum.over_shadow > 0 ? 'warning' : 'default'}
                value={n(sum.over_shadow)}
                hint="Số lượt sẽ bị chặn nếu bật chặn 5/20 lượt. Dùng số này để quyết định có bật chặn." />
            </div>
            <StatCard label="Tải trên máy người dùng" value={n(local)}
              hint={`Có đăng nhập: ${n(sum.counted_local_cookie)} · Bị chặn: ${n(sum.refused)} · Tải khi mất mạng: ${n(sum.retro)}`} />
            <StatCard label="Tải qua máy chủ" value={n(sum.counted_server)}
              hint="Lượt app tải qua máy chủ VidGrab (khi tải trên máy lỗi, và mọi video Douyin). Máy chủ tự đếm vào hạn mức." />
            <StatCard label="Máy dùng hôm nay" value={stats.devices.storage_ready ? n(stats.devices.active_today) : dash}
              hint={stats.devices.storage_ready ? `Tổng ${n(stats.devices.total)} máy · mới 7 ngày: ${n(stats.devices.new_in_period)}` : 'Chưa có bảng lưu máy (migration 037)'} />
            <StatCard label="Hoàn lượt hôm nay" value={n(sum.refunds_today)}
              hint={`Lỗi / huỷ 7 ngày: ${n(sum.settle_failed + sum.settle_cancelled)}`} />
          </div>

          {sum.vc_ratio !== undefined && <VcCard s={stats} />}
          <KindPanel s={stats} />
          <div className="grid gap-5 lg:grid-cols-2">
            <TopOverPanel s={stats} />
            <VersionPanel s={stats} />
          </div>

          <ModeCard s={stats} />

          <Card as="section">
            {/* wording: BA review */}
            <SectionHeader title="Theo ngày" subtitle="Mỗi lượt tải app báo về máy chủ, chia theo cách tải" />
            {stats.per_day.every((d) => ROUTES.every((r) => COLS.every((c) => c.get(d.routes, r) === 0)))
              ? <EmptyState compact title="Chưa có lượt tải nào được ghi"
                  description={stats.flags.client_quota_enabled ? 'App 0.6.0 trở lên sẽ báo lượt tải về đây.' : 'Đếm lượt tải của app đang tắt (CLIENT_QUOTA_ENABLED).'} />
              : <StatsTable s={stats} />}
            <p className="border-t border-line px-4 py-2 text-[11px] text-fg-muted">
              "Vượt lượt (chỉ đếm)": người dùng đã hết lượt nhưng app vẫn tải vì đang ở chế độ chỉ đếm. "Lỗi / huỷ": lượt tải báo lỗi hoặc bị huỷ, có thể đã được hoàn lượt.
            </p>
          </Card>
        </>
      ) : null}

      <DevicesPanel />
    </div>
  )
}
