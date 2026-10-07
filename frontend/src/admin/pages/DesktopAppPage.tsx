import { useCallback, useEffect, useState } from 'react'
import { desktopAppApi } from '../api/desktopApp'
import type { DesktopDevice, DesktopRoute, DevicesPayload, RouteGrid, StatsPayload } from '../api/desktopApp'
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
        <FlagRow label="Hạn mức mỗi ngày" value={`Khách ${n(f.limit_anon)} · Tài khoản ${n(f.limit_user)}`}
          hint={`Khách dùng app tính theo máy, mỗi mạng tối đa ${n(f.limit_anon * f.ip_mult)} lượt. Tài khoản dùng chung lượt với web.`} />
        <FlagRow label="Tải khi mất mạng" value={`${n(f.offline_grace)} lượt/ngày`} hint="Có mạng lại thì app báo bù, vẫn được tính vào hạn mức." />
        <FlagRow label="Hoàn lượt khi tải lỗi" value={`Tối đa ${n(f.refund_daily_max)} lượt/ngày`} />
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
                  </tr>
                </thead>
                <tbody>
                  {devices.map((d) => (
                    <tr key={d.id} className={TR}>
                      <td className={TD_MONO}>{d.code}</td>
                      <td className={TD}>
                        <span className="block max-w-[240px] truncate" title={d.display_name ?? undefined}>{d.display_name || dash}</span>
                        {d.last_ip && <span className="block font-mono text-[11px] text-fg-muted">{d.last_ip}</span>}
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
                      </td>
                      <td className={`${TD} text-xs`}>{fmtTime(d.first_seen)}</td>
                      <td className={`${TD} text-xs`}>{fmtTime(d.last_seen)}</td>
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
