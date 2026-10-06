import { useCallback, useEffect, useState } from 'react'
import { apifyPoolApi } from '../api/apifyPool'
import type { ChinaPlatform, MetricsPayload, Period, PeriodMetrics } from '../api/apifyPool'
import { PageHeader } from '../shared/PageHeader'
import { StatCard } from '../shared/StatCard'
import type { StatValueTone } from '../shared/StatCard'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'
import { SectionHeader } from '../shared/SectionHeader'
import { TABLE, TABLE_SCROLL, TD, TD_NUM, TH, TH_NUM, TR } from '../shared/tableStyles'
import { ApifyPoolManager, usd } from '../panels/apify/ApifyPoolManager'

/**
 * Chi phí Apify (task #6036). Đo chi phí lấy video Douyin / Kuaishou /
 * Xiaohongshu qua Apify và quản lý các token. Số liệu theo ngày UTC, lưu
 * 40 ngày. Không bao giờ hiển thị token hay link video có chữ ký.
 */

const PLATFORM_VI: Record<ChinaPlatform, string> = {
  douyin: 'Douyin',
  kuaishou: 'Kuaishou',
  xiaohongshu: 'Xiaohongshu',
}

const PERIOD_VI: Record<Period, string> = {
  today: 'Hôm nay',
  '7d': '7 ngày',
  month: 'Tháng này',
}

const dash = '—'
const pct = (v: number | null | undefined) =>
  v == null ? dash : `${(v * 100).toLocaleString('vi-VN', { maximumFractionDigits: 1 })}%`
const n = (v: number | null | undefined) => (v == null ? dash : v.toLocaleString('vi-VN'))

function Row({ name, m }: { name: string; m: PeriodMetrics }) {
  return (
    <tr className={TR}>
      <td className={TD}>{name}</td>
      <td className={TD_NUM}>{n(m.managed_calls)}</td>
      <td className={TD_NUM}>{n(m.managed_success)} <span className="text-fg-muted">({pct(m.success_rate)})</span></td>
      <td className={TD_NUM}>
        {m.usable_known ? pct(m.usable_rate) : dash}
        {m.usable_known ? <span className="text-fg-muted"> / {n(m.usable_known)} đã kiểm</span> : null}
      </td>
      <td className={TD_NUM}>{n(m.cache_hits)} <span className="text-fg-muted">({n(m.cache_hits_managed)} trả phí)</span></td>
      <td className={TD_NUM}>{usd(m.saved_usd, 4)}</td>
      <td className={TD_NUM}>{usd(m.spend_usd, 4)}</td>
      <td className={TD_NUM}>{usd(m.cost_per_success_usd, 4)}</td>
    </tr>
  )
}

export default function ApifyCostsPage() {
  const [data, setData] = useState<MetricsPayload | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [period, setPeriod] = useState<Period>('7d')

  const load = useCallback(async () => {
    try {
      setData(await apifyPoolApi.metrics())
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

  const pool = data?.pool
  const pctLeft = pool?.remaining_pct
  const leftTone: StatValueTone =
    pctLeft == null ? 'default' : pctLeft < (pool?.low_pct_threshold ?? 20) ? 'danger' : pctLeft < 50 ? 'warning' : 'success'

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Chi phí Apify"
        description="Chi phí lấy video Douyin, Kuaishou, Xiaohongshu qua Apify và các token đang dùng. Ngày tính theo giờ UTC."
        actions={<Button onClick={load}>Tải lại</Button>}
      />

      {err && (
        <div className="rounded-control border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{err}</div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard label="Còn lại (ước tính)" tone={leftTone}
          value={usd(pool?.remaining_usd)}
          hint={pool?.capacity_usd != null ? `trên ${usd(pool.capacity_usd)} (${pctLeft ?? dash}%)` : 'Chưa có số liệu giới hạn'} />
        <StatCard label="Đã chi tháng này" value={usd(pool?.spend_month_usd, 4)}
          hint={`Hôm nay: ${usd(pool?.spend_today_usd, 4)}`} />
        <StatCard label="Tốc độ chi" value={usd(pool?.burn_per_day_usd, 4)} hint="mỗi ngày, trung bình 7 ngày" />
        <StatCard label="Dự kiến hết sau"
          value={pool?.projected_days_left == null ? dash : `${pool.projected_days_left.toLocaleString('vi-VN')} ngày`}
          hint="nếu giữ tốc độ chi 7 ngày qua" />
        <StatCard label="Token dùng được" tone={pool && pool.eligible === 0 ? 'danger' : 'default'}
          value={pool ? `${pool.eligible}/${pool.entries}` : dash}
          hint={pool ? `Hết tiền: ${pool.by_state.exhausted} · Lỗi: ${pool.by_state.invalid} · Tạm nghỉ: ${pool.by_state.cooldown}` : undefined} />
      </div>

      <Card as="section">
        <SectionHeader
          title="Theo nền tảng"
          subtitle="Lượt gọi trả phí, kết quả, lượt dùng lại từ bộ nhớ đệm và chi phí ghi nhận"
          right={
            <div className="flex gap-1" role="tablist">
              {(Object.keys(PERIOD_VI) as Period[]).map((p) => (
                <Button key={p} size="sm" variant={period === p ? 'primary' : 'ghost'}
                  aria-pressed={period === p} onClick={() => setPeriod(p)}>{PERIOD_VI[p]}</Button>
              ))}
            </div>
          }
        />
        {!data ? (
          <p className="px-4 py-3 text-sm text-fg-muted">Đang tải...</p>
        ) : (
          <div className={TABLE_SCROLL}>
            <table className={TABLE}>
              <thead>
                <tr>
                  <th className={TH}>Nền tảng</th>
                  <th className={TH_NUM}>Lượt gọi trả phí</th>
                  <th className={TH_NUM}>Thành công</th>
                  <th className={TH_NUM}>Video dùng được</th>
                  <th className={TH_NUM}>Dùng lại từ bộ nhớ đệm</th>
                  <th className={TH_NUM}>Tiết kiệm ước tính</th>
                  <th className={TH_NUM}>Đã chi</th>
                  <th className={TH_NUM}>Chi phí / video thành công</th>
                </tr>
              </thead>
              <tbody>
                {(Object.keys(PLATFORM_VI) as ChinaPlatform[]).map((p) => (
                  <Row key={p} name={PLATFORM_VI[p]} m={data.platforms[p][period]} />
                ))}
                <Row name="Tổng" m={data.totals[period]} />
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card as="section">
        <SectionHeader title="Token Apify" subtitle="Hệ thống tự chọn token theo thứ tự ưu tiên, rồi token còn nhiều tiền nhất" />
        <div className="p-4">
          <ApifyPoolManager onChanged={load} />
        </div>
      </Card>

      <Card as="section" padding="md">
        <h2 className="mb-2 font-mono text-[10px] font-medium uppercase tracking-widest text-fg-muted">Cách đọc số liệu</h2>
        <ul className="list-disc space-y-1 pl-5 text-xs text-fg-2">
          <li>"Đã chi" là số hệ thống tự ghi cho mỗi lượt gọi, không thấp hơn giá ước tính (Apify không trả về phí tính theo kết quả trong từng lượt chạy).</li>
          <li>"Apify đã dùng" lấy từ Apify ở lần làm mới gần nhất, cộng thêm phần hệ thống ghi nhận sau đó. Bấm "Làm mới" để lấy số mới (miễn phí).</li>
          <li>"Tiết kiệm ước tính" = số lần dùng lại kết quả trả phí từ bộ nhớ đệm × giá ước tính một lượt gọi.</li>
          <li>"Video dùng được" chỉ có khi bật kiểm tra link video; nếu chưa kiểm sẽ hiện "—".</li>
          <li>Token "Hết tiền" tự được dùng lại khi Apify sang chu kỳ tháng mới, hoặc khi bấm "Làm mới" thấy còn tiền.</li>
        </ul>
        {data && (
          <p className="mt-3 text-[11px] text-fg-muted">
            Giá ước tính mỗi lượt: Douyin {usd(data.estimated_cost_per_call_usd.douyin, 5)} · Kuaishou {usd(data.estimated_cost_per_call_usd.kuaishou, 5)} · Xiaohongshu {usd(data.estimated_cost_per_call_usd.xiaohongshu, 5)}.
            Bộ nhớ đệm: {data.cache.ttl_sec} giây{data.cache.extended_ttl_sec > 0 ? `, kéo dài tới ${data.cache.extended_ttl_sec} giây khi link còn hạn` : ''}.
          </p>
        )}
      </Card>
    </div>
  )
}
