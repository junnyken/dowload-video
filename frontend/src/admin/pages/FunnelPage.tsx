import { useState } from 'react'
import { useAdminFunnel } from '../hooks/useAdminFunnel'

const RANGES = [7, 14, 30, 90]

/** Grey when there is no traffic: 0% failure and "nobody tried" are different
 *  facts, and showing the second as the first is how a dead platform reads as
 *  perfectly healthy. */
function rateColour(pct: number | null): string {
  if (pct === null) return 'text-slate-500'
  if (pct >= 25) return 'text-red-400'
  if (pct >= 10) return 'text-amber-400'
  return 'text-emerald-400'
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
        <div className="text-sm text-slate-500 animate-pulse">Đang tải phễu chuyển đổi…</div>
      </div>
    )
  }

  if (isError) {
    return (
      <div className="max-w-2xl mx-auto mt-16 rounded-xl border border-red-500/30 bg-red-500/5 p-6">
        <p className="text-red-300 font-semibold mb-1">Không tải được phễu</p>
        <p className="text-sm text-slate-400 mb-4">{(error as Error)?.message || 'Lỗi không xác định.'}</p>
        <button onClick={() => refetch()} className="px-4 py-2 rounded-lg bg-slate-700 text-slate-200 text-sm hover:bg-slate-600">
          Thử lại
        </button>
      </div>
    )
  }

  const steps = data?.steps ?? []
  const top = steps[0]?.users ?? 0
  const noData = top === 0 && steps.every(s => s.users === 0)

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-8">

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Phễu chuyển đổi</h1>
          <p className="text-sm text-slate-400 mt-0.5">Đếm theo SỐ NGƯỜI, không phải số lượt</p>
        </div>
        <div className="flex gap-1.5">
          {RANGES.map(d => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                d === days
                  ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                  : 'bg-slate-800/60 border-slate-700 text-slate-400 hover:text-slate-200'
              }`}
            >{d} ngày</button>
          ))}
        </div>
      </div>

      {/* The number above every other number: what these steps do and do not mean. */}
      {data?.note && (
        <div className="rounded-xl border border-slate-700/60 bg-slate-800/40 px-4 py-3">
          <p className="text-xs text-slate-400 leading-relaxed">{data.note}</p>
        </div>
      )}

      {data?.truncated && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3">
          <p className="text-xs text-amber-300">
            Đã chạm trần số dòng đọc được — mọi tỷ lệ dưới đây là của phần dữ liệu
            bị cắt, không phải của cả khoảng thời gian. Thu hẹp số ngày để có số đúng.
          </p>
        </div>
      )}

      {noData ? (
        <div className="rounded-xl border border-slate-700/60 bg-slate-800/30 px-5 py-8 text-center">
          <p className="text-slate-300 font-semibold mb-1">Chưa có dữ liệu trong {days} ngày qua</p>
          <p className="text-sm text-slate-500">
            Sự kiện chỉ bắt đầu chảy về từ lúc frontend mang bản có gắn theo dõi được
            deploy — không hồi tố được về trước đó.
          </p>
        </div>
      ) : (
        <section className="space-y-2">
          {steps.map((s, i) => (
            <div key={s.event} className="rounded-xl border border-slate-700/60 bg-slate-800/40 px-4 py-3">
              <div className="flex items-baseline justify-between gap-3 mb-2">
                <div className="flex items-baseline gap-2 min-w-0">
                  <span className="text-slate-600 text-xs tabular-nums">{i + 1}</span>
                  <span className="text-slate-200 font-semibold text-sm truncate">{s.label}</span>
                  <span className="text-slate-600 text-[11px] font-mono truncate">{s.event}</span>
                </div>
                <div className="flex items-baseline gap-3 flex-shrink-0">
                  <span className="text-white font-bold tabular-nums">{s.users.toLocaleString()}</span>
                  <span className="text-slate-500 text-xs tabular-nums w-12 text-right">{pct(s.pct_of_top)}</span>
                </div>
              </div>
              <div className="h-1.5 rounded-full bg-slate-700/50 overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-amber-500 to-orange-500"
                  style={{ width: `${top > 0 ? (s.users / top) * 100 : 0}%` }}
                />
              </div>
              <div className="mt-1.5 flex items-center justify-between text-[11px]">
                <span className="text-slate-600">{s.events.toLocaleString()} lượt</span>
                {s.drop_from_prev_pct !== null ? (
                  <span className={s.drop_from_prev_pct >= 50 ? 'text-red-400' : 'text-slate-500'}>
                    rớt {s.drop_from_prev_pct}% so với bước trước
                  </span>
                ) : i > 0 ? (
                  <span className="text-slate-600" title="Người vào bằng link sâu không đi qua bước trước">
                    cao hơn bước trước — không tính rớt
                  </span>
                ) : null}
              </div>
            </div>
          ))}
        </section>
      )}

      <section>
        <h2 className="text-sm font-bold text-white mb-2">Tỷ lệ thất bại</h2>
        <p className="text-xs text-slate-500 mb-3">
          Tách riêng khỏi phễu: thất bại không phải một bước người dùng đi qua.
        </p>
        <div className="grid sm:grid-cols-2 gap-3">
          {(data?.failures ?? []).map(f => (
            <div key={f.stage} className="rounded-xl border border-slate-700/60 bg-slate-800/40 px-4 py-3">
              <div className="flex items-baseline justify-between">
                <span className="text-slate-300 text-sm font-semibold">{f.stage}</span>
                <span className={`text-lg font-bold tabular-nums ${rateColour(f.failure_rate_pct)}`}>
                  {pct(f.failure_rate_pct)}
                </span>
              </div>
              <p className="text-[11px] text-slate-500 mt-1">
                {f.ok_users.toLocaleString()} thành công · {f.failed_users.toLocaleString()} thất bại
                {f.failure_rate_pct === null && ' · chưa ai thử'}
              </p>
            </div>
          ))}
        </div>
      </section>

      {(data?.by_platform?.length ?? 0) > 0 && (
        <section>
          <h2 className="text-sm font-bold text-white mb-2">Theo nền tảng</h2>
          <p className="text-xs text-slate-500 mb-3">Xếp theo tỷ lệ hỏng giảm dần — tệ nhất lên đầu.</p>
          <div className="rounded-xl border border-slate-700/60 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-800/70 text-slate-400 text-xs">
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
                  <tr key={p.platform} className="border-t border-slate-700/40">
                    <td className="px-4 py-2 text-slate-200">{p.platform}</td>
                    <td className="px-4 py-2 text-right text-slate-400 tabular-nums">{p.download_ok}</td>
                    <td className="px-4 py-2 text-right text-slate-400 tabular-nums">{p.download_failed}</td>
                    <td className="px-4 py-2 text-right text-slate-400 tabular-nums">{p.fetch_failed}</td>
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
