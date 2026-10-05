import { useCallback, useEffect, useState } from 'react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { getAdminToken, useAdminAuth } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'
import { PageHeader } from '../shared/PageHeader'
import { StatCard } from '../shared/StatCard'
import type { StatValueTone } from '../shared/StatCard'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'

/**
 * Phiên âm (ASR) — Phase 32A admin. Viewing: operator+. Kill switch and
 * selftest: admin+ (hidden for lower roles, not just disabled).
 * Unknown numbers render "—", never 0.
 */

interface ProviderStat { jobs: number; done: number; failed: number; est_cost_usd: number; actual_cost_usd: number }
interface Summary {
  day_utc: string
  enabled: boolean
  provider: string
  model: string | null
  price_per_min_usd: number | null
  price_verified: boolean
  killswitch: boolean | null
  spend_today_usd: number | null
  spend_ceiling_usd: number | null
  minutes_reserved_today: number | null
  minutes_cap_today: number | null
  minutes_in_jobs_today: number | null
  per_user_daily_minutes_limit: number | null
  jobs_today: number | null
  jobs_by_status: Record<string, number>
  failure_rate: number | null
  failures_by_code: Record<string, number>
  avg_processing_sec: number | null
  avg_turnaround_sec: number | null
  per_provider: Record<string, ProviderStat>
  redis_ok: boolean
  db_ok: boolean
}

interface SelftestSegment { start: number; end: number; text: string; speaker?: string }
interface SelftestResult {
  ok: boolean
  error_code?: string
  detail?: string
  provider?: string
  model?: string | null
  clip_sec?: number
  language?: string | null
  segment_count?: number
  segments?: SelftestSegment[]
  srt_preview?: string
  timings_ms?: Record<string, number>
  cost_estimate_usd?: number
  raw_output?: string | null
}

const STATUS_VI: Record<string, string> = {
  queued: 'Đang chờ', extracting_audio: 'Đang tách âm thanh', transcribing: 'Đang nhận diện',
  done: 'Hoàn tất', failed: 'Thất bại',
}

const dash = '—'
const num = (v: number | null | undefined, digits = 0) =>
  v == null || Number.isNaN(Number(v)) ? dash : Number(v).toLocaleString('vi-VN', { maximumFractionDigits: digits })
const usd = (v: number | null | undefined, digits = 4) =>
  v == null || Number.isNaN(Number(v)) ? dash : `$${Number(v).toFixed(digits)}`
const secs = (v: number | null | undefined) => (v == null ? dash : `${num(v, 1)} giây`)
const pct = (v: number | null | undefined) => (v == null ? dash : `${num(v * 100, 1)}%`)
const ms = (v: number | undefined) => (v == null ? dash : `${v.toLocaleString('vi-VN')} ms`)
const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

const INPUT =
  'h-11 w-full rounded-control border border-line bg-surface px-3 text-sm text-fg placeholder:text-fg-muted ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'

async function runSelftest(body: { file_id: string; diarize: boolean; language?: string }): Promise<SelftestResult> {
  // adminFetch drops the response body on non-2xx; the selftest needs raw_output from it.
  const token = getAdminToken()
  const res = await fetch(`${API_BASE}/api/v1/admin/asr/selftest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  })
  let data: Partial<SelftestResult> & { detail?: unknown } = {}
  try { data = await res.json() } catch { /* keep empty */ }
  if (res.ok) return data as SelftestResult
  const detail = typeof data.detail === 'string' ? data.detail : `Lỗi HTTP ${res.status}`
  return { ...data, ok: false, detail } as SelftestResult
}

export default function AsrPage() {
  const { hasRole } = useAdminAuth()
  const isAdmin = hasRole('admin')

  const [data, setData] = useState<Summary | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const [confirmOpen, setConfirmOpen] = useState(false)
  const [toggling, setToggling] = useState(false)
  const [toggleMsg, setToggleMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const [fileId, setFileId] = useState('')
  const [language, setLanguage] = useState('')
  const [diarize, setDiarize] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<SelftestResult | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await adminFetch<Summary>('/asr/summary'))
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [load])

  async function applyKillswitch(on: boolean) {
    setToggling(true); setToggleMsg(null)
    try {
      await adminPost('/asr/killswitch', { on })
      setToggleMsg({ ok: true, text: on ? 'Đã tạm dừng phiên âm cho toàn hệ thống.' : 'Đã bật lại phiên âm.' })
      await load()
    } catch (e) {
      setToggleMsg({ ok: false, text: (e as Error).message })
    } finally {
      setToggling(false); setConfirmOpen(false)
    }
  }

  async function submitSelftest(e: React.FormEvent) {
    e.preventDefault()
    if (!fileId.trim() || testing) return
    setTesting(true); setResult(null)
    try {
      const body: { file_id: string; diarize: boolean; language?: string } = { file_id: fileId.trim(), diarize }
      if (language.trim()) body.language = language.trim()
      setResult(await runSelftest(body))
      load() // a selftest spends money; refresh the ledger
    } catch (e2) {
      setResult({ ok: false, detail: `Không kết nối được máy chủ: ${(e2 as Error).message}` })
    } finally {
      setTesting(false)
    }
  }

  const paused = data?.killswitch === true
  const unknownSwitch = data != null && data.killswitch == null
  const statusLabel = !data ? dash
    : !data.enabled ? 'Đang tắt (cờ ASR_ENABLED)'
    : paused ? 'Đã tạm dừng'
    : unknownSwitch ? 'Không đọc được'
    : 'Đang bật'
  const statusTone: StatValueTone = !data ? 'default' : !data.enabled || paused ? 'danger' : unknownSwitch ? 'warning' : 'success'

  const spendRatio = data?.spend_today_usd != null && data.spend_ceiling_usd
    ? data.spend_today_usd / data.spend_ceiling_usd : null
  const failureTotal = data ? Object.values(data.failures_by_code).reduce((a, b) => a + b, 0) : 0

  return (
    <div className="space-y-6">
      <PageHeader
        title="Phiên âm (ASR)"
        description="Chi phí, hạn mức và sức khoẻ của tính năng tạo phụ đề từ giọng nói · ngày UTC · làm mới 30 giây"
        actions={<Button onClick={load} disabled={loading}>Làm mới</Button>}
      />

      {err && (
        <div role="alert" className="rounded-card border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
          Không tải được số liệu phiên âm: {err}
        </div>
      )}
      {data && !data.redis_ok && (
        <div role="alert" className="rounded-card border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning-fg">
          Không đọc được Redis: chi phí, số phút và công tắc bên dưới hiển thị "—" vì chưa biết giá trị thật. Người dùng cũng không tạo được job mới cho đến khi Redis hoạt động lại.
        </div>
      )}
      {data && !data.db_ok && (
        <div role="alert" className="rounded-card border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning-fg">
          Không đọc được bảng job: số job, tỷ lệ lỗi và thời gian xử lý bên dưới không đáng tin.
        </div>
      )}

      {loading && !data && <p className="text-sm text-fg-muted animate-pulse">Đang tải số liệu phiên âm…</p>}

      {data && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="asr-stats">
            <StatCard label="Trạng thái" value={statusLabel} tone={statusTone}
              hint={`Nhà cung cấp: ${data.provider}${data.model ? ` · ${data.model}` : ''}`} className="[&>p:nth-child(2)]:text-base [&>p:nth-child(2)]:leading-tight" />
            <StatCard label="Chi phí hôm nay" value={usd(data.spend_today_usd)}
              tone={spendRatio != null && spendRatio >= 0.8 ? 'warning' : 'default'}
              hint={`Trần ${usd(data.spend_ceiling_usd, 2)}${spendRatio != null ? ` · đã dùng ${pct(spendRatio)}` : ''}`} />
            <StatCard label="Phút đã giữ hôm nay" value={num(data.minutes_reserved_today, 1)}
              hint={`Trần toàn hệ thống ${num(data.minutes_cap_today)} phút`} />
            <StatCard label="Job hôm nay" value={num(data.jobs_today)}
              hint={`${num(data.minutes_in_jobs_today, 1)} phút âm thanh · tối đa ${num(data.per_user_daily_minutes_limit)} phút/người/ngày`} />
            <StatCard label="Tỷ lệ lỗi" value={pct(data.failure_rate)}
              tone={data.failure_rate != null && data.failure_rate > 0.2 ? 'danger' : 'default'}
              hint={data.failure_rate == null ? 'Chưa có job nào kết thúc hôm nay' : `${failureTotal} job lỗi`} />
            <StatCard label="Xử lý trung bình" value={secs(data.avg_processing_sec)} hint="Từ lúc chạy đến khi xong" />
            <StatCard label="Chờ + xử lý" value={secs(data.avg_turnaround_sec)} hint="Từ lúc tạo job đến khi xong" />
            <StatCard label="Giá ước tính / phút" value={usd(data.price_per_min_usd, 4)}
              tone={data.price_verified ? 'default' : 'warning'}
              hint={data.price_verified ? 'Đã đối chiếu hoá đơn' : 'Chưa đối chiếu với hoá đơn nhà cung cấp'} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card padding="md">
              <h2 className="mb-3 text-sm font-semibold text-fg">Công tắc khẩn cấp</h2>
              <p className="mb-3 text-xs leading-relaxed text-fg-2">
                Tạm dừng sẽ chặn việc tạo job phiên âm mới trên toàn hệ thống. Bật lại bằng cùng nút này.
              </p>
              {isAdmin ? (
                <>
                  <Button
                    variant={paused ? 'primary' : 'danger'}
                    size="md"
                    className="min-h-11"
                    disabled={toggling || unknownSwitch || !data.redis_ok}
                    onClick={() => setConfirmOpen(true)}
                  >
                    {paused ? 'Bật lại phiên âm' : 'Tạm dừng phiên âm'}
                  </Button>
                  {(unknownSwitch || !data.redis_ok) && (
                    <p className="mt-2 text-xs text-fg-muted">Không đọc được trạng thái công tắc nên chưa thể đổi.</p>
                  )}
                </>
              ) : (
                <p className="text-xs text-fg-muted">Cần quyền admin để đổi công tắc.</p>
              )}
              {toggleMsg && (
                <p role="status" className={`mt-3 text-xs ${toggleMsg.ok ? 'text-success' : 'text-danger'}`}>{toggleMsg.text}</p>
              )}
            </Card>

            <Card padding="md">
              <h2 className="mb-3 text-sm font-semibold text-fg">Job theo trạng thái</h2>
              {Object.keys(data.jobs_by_status).length === 0 ? (
                <p className="text-xs text-fg-muted">Chưa có job nào hôm nay.</p>
              ) : (
                <ul className="space-y-1.5 text-sm">
                  {Object.entries(data.jobs_by_status).map(([k, v]) => (
                    <li key={k} className="flex justify-between">
                      <span className="text-fg-2">{STATUS_VI[k] ?? k}</span>
                      <span className="font-mono tabular-nums text-fg">{v}</span>
                    </li>
                  ))}
                </ul>
              )}
              {failureTotal > 0 && (
                <>
                  <h3 className="mb-2 mt-4 font-mono text-[10px] uppercase tracking-widest text-fg-muted">Lỗi theo mã</h3>
                  <ul className="space-y-1 text-xs">
                    {Object.entries(data.failures_by_code).map(([k, v]) => (
                      <li key={k} className="flex justify-between"><span className="font-mono text-fg-2">{k}</span><span className="font-mono text-danger">{v}</span></li>
                    ))}
                  </ul>
                </>
              )}
            </Card>
          </div>

          {Object.keys(data.per_provider).length > 0 && (
            <Card padding="md">
              <h2 className="mb-3 text-sm font-semibold text-fg">Theo nhà cung cấp</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="font-mono text-[10px] uppercase tracking-widest text-fg-muted">
                    <tr><th className="py-1 pr-3">Nhà cung cấp</th><th className="pr-3">Job</th><th className="pr-3">Xong</th><th className="pr-3">Lỗi</th><th className="pr-3">Chi phí ước tính</th><th>Chi phí thực</th></tr>
                  </thead>
                  <tbody className="font-mono tabular-nums text-fg-2">
                    {Object.entries(data.per_provider).map(([k, p]) => (
                      <tr key={k} className="border-t border-line">
                        <td className="py-1.5 pr-3 text-fg">{k}</td><td className="pr-3">{p.jobs}</td><td className="pr-3">{p.done}</td>
                        <td className="pr-3">{p.failed}</td><td className="pr-3">{usd(p.est_cost_usd)}</td>
                        <td>{p.actual_cost_usd > 0 ? usd(p.actual_cost_usd) : dash}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </>
      )}

      {isAdmin ? (
        <Card padding="md" as="section">
          <h2 className="text-sm font-semibold text-fg">Chạy thử nhà cung cấp (selftest)</h2>
          <p className="mt-1 text-xs leading-relaxed text-warning-fg" data-testid="selftest-cost-warning">
            Gọi thật đến nhà cung cấp phiên âm và tốn tiền thật: tối đa 60 giây âm thanh đầu của video, tính vào trần chi phí hôm nay. Dùng được cả khi tính năng đang tắt với người dùng.
          </p>
          <form onSubmit={submitSelftest} className="mt-4 grid gap-3 md:grid-cols-2">
            <div className="md:col-span-2">
              <label htmlFor="asr-file-id" className="mb-1 block text-xs font-medium text-fg-2">File ID của video</label>
              <input id="asr-file-id" className={`${INPUT} font-mono`} value={fileId} onChange={(e) => setFileId(e.target.value)}
                placeholder="ví dụ: 3f9c1e2a7b.mp4" autoComplete="off" spellCheck={false} />
              <p className="mt-1 text-[11px] leading-relaxed text-fg-muted">
                Là tên file video đã tải trên server. Cách lấy: tải 1 video ngắn ở trang chủ, bấm chuột phải nút tải về chọn "Sao chép địa chỉ liên kết", rồi lấy phần sau <span className="font-mono">file=</span> (bỏ phần từ <span className="font-mono">&amp;</span> trở đi).
              </p>
            </div>
            <div>
              <label htmlFor="asr-lang" className="mb-1 block text-xs font-medium text-fg-2">Ngôn ngữ (không bắt buộc)</label>
              <input id="asr-lang" className={INPUT} value={language} onChange={(e) => setLanguage(e.target.value)}
                placeholder="vi, en, ja… để trống = tự nhận diện" autoComplete="off" />
            </div>
            <label className="flex min-h-11 items-center gap-2 self-end text-sm text-fg-2">
              <input type="checkbox" className="h-4 w-4 accent-accent" checked={diarize} onChange={(e) => setDiarize(e.target.checked)} />
              Tách người nói (diarize)
            </label>
            <div className="md:col-span-2">
              <Button type="submit" variant="primary" size="md" className="min-h-11" disabled={!fileId.trim() || testing}>
                {testing ? 'Đang gọi nhà cung cấp…' : 'Chạy thử (tốn phí)'}
              </Button>
            </div>
          </form>

          {result && result.ok && (
            <div className="mt-5 space-y-4" data-testid="selftest-ok">
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatCard label="Đoạn nhận diện" value={num(result.segment_count)} hint={`Ngôn ngữ: ${result.language ?? dash}`} />
                <StatCard label="Chi phí ước tính" value={usd(result.cost_estimate_usd)} hint={`${result.provider ?? dash}${result.model ? ` · ${result.model}` : ''}`} />
                <StatCard label="Tách âm thanh" value={ms(result.timings_ms?.extract_ms)} />
                <StatCard label="Nhà cung cấp trả lời" value={ms(result.timings_ms?.provider_ms)} hint={`Tổng ${ms(result.timings_ms?.total_ms)}`} />
              </div>
              <div>
                <h3 className="mb-1 font-mono text-[10px] uppercase tracking-widest text-fg-muted">Xem trước SRT</h3>
                <pre className="max-h-64 overflow-auto rounded-control border border-line bg-surface-2 p-3 font-mono text-[11px] leading-relaxed text-fg-2 whitespace-pre-wrap">{result.srt_preview || dash}</pre>
              </div>
              {result.segments && result.segments.length > 0 && (
                <div>
                  <h3 className="mb-1 font-mono text-[10px] uppercase tracking-widest text-fg-muted">Các đoạn ({result.segments.length})</h3>
                  <ul className="max-h-64 space-y-1 overflow-auto rounded-control border border-line p-2 text-xs">
                    {result.segments.map((s, i) => (
                      <li key={i} className="flex gap-2">
                        <span className="shrink-0 font-mono text-fg-muted">{clock(s.start)}–{clock(s.end)}</span>
                        <span className="text-fg-2">{s.speaker ? `[${s.speaker}] ` : ''}{s.text}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {result && !result.ok && (
            <div role="alert" className="mt-5 space-y-3 rounded-card border border-danger/30 bg-danger-soft p-4" data-testid="selftest-error">
              <p className="text-sm font-medium text-danger">
                {result.detail || 'Chạy thử thất bại.'}
                {result.error_code && <span className="ml-2 font-mono text-xs">({result.error_code})</span>}
              </p>
              {result.cost_estimate_usd != null && (
                <p className="text-xs text-fg-2">Lần gọi này đã được tính phí ước tính {usd(result.cost_estimate_usd)}.</p>
              )}
              {result.raw_output && (
                <div>
                  <h3 className="mb-1 font-mono text-[10px] uppercase tracking-widest text-fg-muted">Phản hồi thô từ nhà cung cấp (đã cắt ngắn)</h3>
                  <pre className="max-h-64 overflow-auto rounded-control border border-line bg-surface p-3 font-mono text-[11px] text-fg-2 whitespace-pre-wrap break-all">{result.raw_output}</pre>
                </div>
              )}
            </div>
          )}
        </Card>
      ) : (
        <p className="text-xs text-fg-muted">Cần quyền admin để chạy thử nhà cung cấp.</p>
      )}

      {confirmOpen && data && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/80 p-4 backdrop-blur-sm" onClick={() => !toggling && setConfirmOpen(false)}>
          <div role="alertdialog" aria-modal="true" aria-label="Xác nhận đổi công tắc" onClick={(e) => e.stopPropagation()}
            className="w-full max-w-sm rounded-card border border-line bg-surface p-5 shadow-card">
            <h3 className="text-sm font-semibold text-fg">{paused ? 'Bật lại phiên âm?' : 'Tạm dừng phiên âm?'}</h3>
            <p className="mt-2 text-xs leading-relaxed text-fg-2">
              {paused
                ? 'Người dùng sẽ tạo được phụ đề từ giọng nói trở lại (nếu cờ ASR_ENABLED đang bật).'
                : 'Mọi người dùng sẽ không tạo được job phiên âm mới cho đến khi bạn bật lại. Thao tác được ghi vào nhật ký admin.'}
            </p>
            <div className="mt-4 flex gap-2">
              <Button className="min-h-11 flex-1" onClick={() => setConfirmOpen(false)} disabled={toggling}>Huỷ</Button>
              <Button className="min-h-11 flex-1" variant={paused ? 'primary' : 'danger'} onClick={() => applyKillswitch(!paused)} disabled={toggling}>
                {toggling ? 'Đang xử lý…' : paused ? 'Bật lại' : 'Tạm dừng'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
