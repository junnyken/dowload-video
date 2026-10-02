import { useState } from 'react'
import { useAdminFunnel } from '../hooks/useAdminFunnel'

const RANGES = [7, 14, 30, 90]

/** Grey when there is no traffic: 0% failure and "nobody tried" are different
 *  facts, and showing the second as the first is how a dead platform reads as
 *  perfectly healthy. */
function rateColour(pct: number | null): string {
  if (pct === null) return 'text-fg-muted'
  if (pct >= 25) return 'text-danger'
  if (pct >= 10) return 'text-warning'
  return 'text-success'
}

function pct(v: number | null, suffix = '%'): string {
  return v === null ? '—' : `${v}${suffix}`
}

export default function FunnelPage() {
  const [days, setDays] = useState(7)
  const { data, isLoading, isError, error, refetch } = useAdminFunnel(days)

  if (isLoading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="text-sm text-fg-muted animate-pulse">Đang tải phễu chuyển đổi…</div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="max-w-2xl mx-auto mt-16 rounded-card border border-danger/30 bg-danger-soft p-6">
        <p className="text-danger font-semibold mb-1">Không tải được phễu</p>
        <p className="text-sm text-fg-muted mb-4">{(error as Error)?.message || 'Lỗi không xác định.'}</p>
        <button onClick={() => refetch()} className="px-4 py-2 text-sm rounded-control border border-line bg-surface font-medium text-fg hover:border-line-strong hover:bg-surface-2">
          Thử lại
        </button>
      </div>
    )
  }

  const steps = data?.steps ?? []
  const top = steps[0]?.users ?? 0
  const noData = top === 0 && steps.every(s => s.users === 0)

  return (
    <div className="space-y-8">

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Phễu chuyển đổi</h1>
          <p className="text-sm text-fg-muted mt-0.5">Đếm theo SỐ NGƯỜI, không phải số lượt</p>
        </div>
        <div className="flex gap-1.5">
          {RANGES.map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-3 py-1.5 rounded-control text-xs font-semibold border transition-colors ${
                d === days
                  ? 'bg-surface-2 border-line-strong text-fg'
                  : 'bg-surface border-line text-fg-2 hover:bg-surface-2 hover:text-fg'
              }`}
            >{d} ngày</button>
          ))}
        </div>
      </div>

      {/* The number above every other number: what these steps do and do not mean. */}
      {data?.note && (
        <div className="rounded-card border border-line bg-surface shadow-card px-4 py-3">
          <p className="text-xs text-fg-muted leading-relaxed">{data.note}</p>
        </div>
      )}

      {data?.truncated && (
        <div className="rounded-card border border-warning/30 bg-warning-soft px-4 py-3">
          <p className="text-xs text-warning">
            Đã chạm trần số dòng đọc được — mọi tỷ lệ dưới đây là của phần dữ liệu
            bị cắt, không phải của cả khoảng thời gian. Thu hẹp số ngày để có số đúng.
          </p>
        </div>
      )}

      {noData ? (
        <div className="rounded-card border border-line bg-surface shadow-card px-5 py-8 text-center">
          <p className="text-fg-2 font-semibold mb-1">Chưa có dữ liệu trong {days} ngày qua</p>
          <p className="text-sm text-fg-muted">
            Sự kiện chỉ bắt đầu chảy về từ lúc frontend mang bản có gắn theo dõi được
            deploy — không hồi tố được về trước đó.
          </p>
        </div>
      ) : (
        <section className="space-y-2">
          {steps.map((s, i) => (
            <div key={s.event} className="rounded-card border border-line bg-surface shadow-card px-4 py-3">
              <div className="flex items-baseline justify-between gap-3 mb-2">
                <div className="flex items-baseline gap-2 min-w-0">
                  <span className="text-fg-muted text-xs tabular-nums">{i + 1}</span>
                  <span className="text-fg-2 font-semibold text-sm truncate">{s.label}</span>
                  <span className="text-fg-muted text-[11px] font-mono truncate">{s.event}</span>
                </div>
                <div className="flex items-baseline gap-3 flex-shrink-0">
                  <span className="text-fg font-bold tabular-nums">{s.users.toLocaleString()}</span>
                  <span className="text-fg-muted text-xs tabular-nums w-12 text-right">{pct(s.pct_of_top)}</span>
                </div>
              </div>
              <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${top > 0 ? (s.users / top) * 100 : 0}%` }}
                />
              </div>
              <div className="mt-1.5 flex items-center justify-between text-[11px]">
                <span className="text-fg-muted">{s.events.toLocaleString()} lượt</span>
                {s.drop_from_prev_pct !== null ? (
                  <span className={s.drop_from_prev_pct >= 50 ? 'text-danger' : 'text-fg-muted'}>
                    rớt {s.drop_from_prev_pct}% so với bước trước
                  </span>
                ) : i > 0 ? (
                  <span className="text-fg-muted" title="Người vào bằng link sâu không đi qua bước trước">
                    cao hơn bước trước — không tính rớt
                  </span>
                ) : null}
              </div>
            </div>
          ))}
        </section>
      )}

      <section>
        <h2 className="text-sm font-bold text-fg mb-2">Tỷ lệ thất bại</h2>
        <p className="text-xs text-fg-muted mb-3">
          Tách riêng khỏi phễu: thất bại không phải một bước người dùng đi qua.
        </p>
        <div className="grid sm:grid-cols-2 gap-3">
          {(data?.failures ?? []).map(f => (
            <div key={f.stage} className="rounded-card border border-line bg-surface shadow-card px-4 py-3">
              <div className="flex items-baseline justify-between">
                <span className="text-fg-2 text-sm font-semibold">{f.stage}</span>
                <span className={`text-lg font-bold tabular-nums ${rateColour(f.failure_rate_pct)}`}>
                  {pct(f.failure_rate_pct)}
                </span>
              </div>
              <p className="text-[11px] text-fg-muted mt-1">
                {f.ok_users.toLocaleString()} thành công · {f.failed_users.toLocaleString()} thất bại
                {f.failure_rate_pct === null && ' · chưa ai thử'}
              </p>
            </div>
          ))}
        </div>
      </section>

      {(data?.by_platform?.length ?? 0) > 0 && (
        <section>
          <h2 className="text-sm font-bold text-fg mb-2">Theo nền tảng</h2>
          <p className="text-xs text-fg-muted mb-3">Xếp theo tỷ lệ hỏng giảm dần — tệ nhất lên đầu.</p>
          <div className="rounded-card border border-line overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-surface text-fg-muted text-xs">
                <tr>
                  <th className="text-left px-4 py-2 font-semibold">Nền tảng</th>
                  <th className="text-right px-4 py-2 font-semibold">Tải xong</th>
                  <th className="text-right px-4 py-2 font-semibold">Tải hỏng</th>
                  <th className="text-right px-4 py-2 font-semibold">Lấy tin hỏng</th>
                  <th className="text-right px-4 py-2 font-semibold">Tỷ lệ hỏng</th>
                </tr>
              </thead>
              <tbody>
                {data!.by_platform.map(p => (
                  <tr key={p.platform} className="border-t border-line">
                    <td className="px-4 py-2 text-fg-2">{p.platform}</td>
                    <td className="px-4 py-2 text-right text-fg-muted tabular-nums">{p.download_ok}</td>
                    <td className="px-4 py-2 text-right text-fg-muted tabular-nums">{p.download_failed}</td>
                    <td className="px-4 py-2 text-right text-fg-muted tabular-nums">{p.fetch_failed}</td>
                    <td className={`px-4 py-2 text-right font-bold tabular-nums ${rateColour(p.failure_rate_pct)}`}>
                      {pct(p.failure_rate_pct)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
