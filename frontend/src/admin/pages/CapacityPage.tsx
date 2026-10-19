import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Cpu } from 'lucide-react'
import { adminFetch, adminPost } from '../utils/adminFetch'
import { ADMIN_TZ } from '../utils/vnTime'

/**
 * "Tải đồng thời & quá tải": how close the service runs to its concurrent
 * download ceiling, and the knob that moves the ceiling.
 * Three single-series SVG charts share one time axis (never dual-axis).
 */

interface Point {
  t: number; peak: number; limit: number; refused: number; acquired: number
  cpu_avg: number | null; cpu_max: number | null; mem_max: number | null
  queue_max: number | null; samples: number
}
interface Summary {
  range: string; minutes_sampled: number; first_sample: number | null
  peak: number | null; refused: number; acquired: number; minutes_at_limit: number
  cpu_p95: number | null; cpu_max: number | null; mem_max: number | null
}
interface Latency {
  platform: string; total: number; p50_ms: number | null; p95_ms: number | null
  buckets: Record<string, number>
}
interface Setting {
  max_concurrent_downloads: number; source: 'admin' | 'env'
  env_default: number; min: number; max: number
}
interface CapacityData {
  points: Point[]; summary: Summary; latency: Latency[]; setting: Setting
  resources: { cpu_cores: number | null; memory_pct_now: number | null }
}
type Range = '24h' | '7d'

const fmtNum = (n: number | null | undefined, d = 0) =>
  n == null ? '—' : n.toLocaleString('vi-VN', { maximumFractionDigits: d })

function fmtMs(ms: number | null): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} s`
}

function fmtClock(t: number, withDate: boolean): string {
  const d = new Date(t * 1000)
  const hm = d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', timeZone: ADMIN_TZ })
  if (!withDate) return hm
  const dm = d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', timeZone: ADMIN_TZ })
  return `${dm} ${hm}`
}

function fmtDay(t: number): string {
  return new Date(t * 1000).toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', timeZone: ADMIN_TZ })
}

/* ─── shared geometry ─────────────────────────────────────────────────── */

interface Axis { t0: number; t1: number; bucket: number }

function useWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(600)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    setW(el.clientWidth || 600)
    const ro = new ResizeObserver(() => setW(el.clientWidth || 600))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

function niceMax(v: number): number {
  if (v <= 4) return 4
  const p = Math.pow(10, Math.floor(Math.log10(v)))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p
  return v
}

interface ChartProps {
  title: string
  note?: string
  points: Point[]
  axis: Axis
  range: Range
  hover: number | null
  setHover: (i: number | null) => void
  render: (c: { x: (t: number) => number; plotW: number; plotH: number; px: number }) => React.ReactNode
  yMax: number
  yTicks: number[]
  yFmt?: (v: number) => string
  refLines?: { value: number; label: string }[]
  height?: number
}

const PAD_L = 34, PAD_R = 10, PAD_T = 8, PAD_B = 20

function Chart(p: ChartProps) {
  const [ref, W] = useWidth()
  const H = p.height ?? 120
  const plotW = Math.max(W - PAD_L - PAD_R, 10)
  const plotH = H - PAD_T - PAD_B
  const span = p.axis.t1 - p.axis.t0
  const x = (t: number) => PAD_L + ((t - p.axis.t0) / span) * plotW
  const y = (v: number) => PAD_T + plotH - (Math.min(v, p.yMax) / p.yMax) * plotH
  const px = (p.axis.bucket / span) * plotW

  // x ticks: ~5 evenly spaced, day labels for 7d, HH:mm for 24h
  const xt = useMemo(() => {
    const n = W < 420 ? 4 : 6
    return Array.from({ length: n + 1 }, (_, i) => p.axis.t0 + (span * i) / n)
  }, [W, span, p.axis.t0])

  const byBucket = useMemo(() => {
    const m = new Map<number, number>()
    p.points.forEach((pt, i) => m.set(Math.floor((pt.t - p.axis.t0) / p.axis.bucket), i))
    return m
  }, [p.points, p.axis])

  // The crosshair is shared by the three charts; the tooltip only shows on
  // the one under the pointer (three identical tooltips read as clutter).
  const [owner, setOwner] = useState(false)

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const r = (e.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect()
    const t = p.axis.t0 + ((e.clientX - r.left - PAD_L) / plotW) * span
    const idx = byBucket.get(Math.floor((t - p.axis.t0) / p.axis.bucket))
    p.setHover(idx ?? null)
    setOwner(true)
  }

  const hp = p.hover != null ? p.points[p.hover] : null
  return (
    <div className="rounded-card border border-line bg-surface shadow-card p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h3 className="text-sm font-semibold text-fg">{p.title}</h3>
        {p.note && <span className="text-[11px] text-fg-muted">{p.note}</span>}
      </div>
      <div ref={ref} className="mt-2 w-full">
        <svg width={W} height={H} role="img" aria-label={p.title} className="block select-none" style={{ touchAction: 'pan-y' }}>
          {p.yTicks.map(v => (
            <g key={v}>
              <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} stroke="var(--vg-line)" strokeWidth={1} />
              <text x={PAD_L - 6} y={y(v) + 3} textAnchor="end" fontSize={10} fill="var(--vg-fg-muted)">
                {p.yFmt ? p.yFmt(v) : v}
              </text>
            </g>
          ))}
          {xt.map((t, i) => (
            <text key={i} x={x(t)} y={H - 5} fontSize={10} fill="var(--vg-fg-muted)"
              textAnchor={i === 0 ? 'start' : i === xt.length - 1 ? 'end' : 'middle'}>
              {p.range === '7d' ? fmtDay(t) : fmtClock(t, false)}
            </text>
          ))}
          {p.render({ x, plotW, plotH, px })}
          {p.refLines?.map(r => (
            <g key={r.label}>
              <line x1={PAD_L} x2={W - PAD_R} y1={y(r.value)} y2={y(r.value)}
                stroke="var(--vg-fg-muted)" strokeWidth={1} strokeDasharray="4 3" />
              <text x={W - PAD_R - 2} y={y(r.value) - 4} textAnchor="end" fontSize={10} fill="var(--vg-fg-muted)">
                {r.label}
              </text>
            </g>
          ))}
          {hp && (
            <line x1={x(hp.t) + px / 2} x2={x(hp.t) + px / 2} y1={PAD_T} y2={PAD_T + plotH}
              stroke="var(--vg-fg-2)" strokeWidth={1} />
          )}
          <rect x={PAD_L} y={PAD_T} width={plotW} height={plotH} fill="transparent"
            onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => { setOwner(false); p.setHover(null) }} />
        </svg>
        {hp && owner && (
          <Tooltip pt={hp} range={p.range} left={Math.min(Math.max(x(hp.t) + px / 2, 90), W - 90)} />
        )}
      </div>
    </div>
  )
}

function Tooltip({ pt, range, left }: { pt: Point; range: Range; left: number }) {
  return (
    <div className="pointer-events-none relative z-10" style={{ height: 0 }}>
      <div className="absolute -translate-x-1/2 rounded-md border border-line bg-surface px-2.5 py-1.5 text-[11px] leading-snug text-fg shadow-md"
        style={{ left, bottom: 0, transform: 'translate(-50%, 6px)', minWidth: 150 }}>
        <div className="font-medium">{fmtClock(pt.t, range === '7d')}</div>
        {/* wording: BA review */}
        <div className="text-fg-2">Cao nhất cùng lúc: <b className="text-fg">{pt.peak}</b> / giới hạn {pt.limit}</div>
        {/* wording: BA review */}
        <div className="text-fg-2">Lượt bị từ chối (quá tải): <b className="text-fg">{pt.refused}</b></div>
        {/* wording: BA review */}
        <div className="text-fg-2">CPU: <b className="text-fg">{fmtNum(pt.cpu_avg, 0)}%</b> (cao nhất {fmtNum(pt.cpu_max, 0)}%)</div>
        {/* wording: BA review */}
        <div className="text-fg-2">Việc tải hàng loạt đang chờ (tối đa): <b className="text-fg">{fmtNum(pt.queue_max)}</b></div>
      </div>
    </div>
  )
}

/** Split into runs of consecutive buckets so missing data stays a gap. */
function runs<T extends { t: number }>(pts: T[], bucket: number): T[][] {
  const out: T[][] = []
  let cur: T[] = []
  pts.forEach((pt, i) => {
    if (i > 0 && pt.t - pts[i - 1].t > bucket * 1.5) { out.push(cur); cur = [] }
    cur.push(pt)
  })
  if (cur.length) out.push(cur)
  return out
}

/* ─── page ────────────────────────────────────────────────────────────── */

function Tile({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return (
    <div className="rounded-card border border-line bg-surface shadow-card p-3">
      <div className="text-[11px] text-fg-muted">{label}</div>
      <div className={`mt-1 flex items-center gap-1.5 text-xl font-semibold ${warn ? 'text-warning' : 'text-fg'}`}>
        {warn && <AlertTriangle size={16} aria-hidden="true" />}
        <span>{value}</span>
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-fg-muted">{sub}</div>}
    </div>
  )
}

export default function CapacityPage() {
  const [range, setRange] = useState<Range>('24h')
  const [data, setData] = useState<CapacityData | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [hover, setHover] = useState<number | null>(null)
  const [asTable, setAsTable] = useState(false)

  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState<string | null>(null)

  const seq = useRef(0)
  const load = useCallback(async () => {
    const my = ++seq.current
    try {
      const d = await adminFetch<CapacityData>(`/capacity?range=${range}`)
      if (my !== seq.current) return
      setData(d); setErr(null)
    } catch (e) {
      if (my === seq.current) setErr((e as Error).message)
    } finally {
      if (my === seq.current) setLoading(false)
    }
  }, [range])

  useEffect(() => {
    setLoading(true)
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [load])

  // Seed the input once per server value (not on every refresh, so typing isn't clobbered).
  const serverVal = data?.setting.max_concurrent_downloads
  useEffect(() => { if (serverVal != null) setDraft(String(serverVal)) }, [serverVal])

  async function save(value: number | null) {
    setSaving(true); setSaveErr(null); setSaveOk(null)
    try {
      await adminPost('/capacity/settings', { max_concurrent_downloads: value })
      // wording: BA review
      setSaveOk(value == null ? 'Đã quay về giá trị mặc định.' : `Đã lưu: tối đa ${value} lượt cùng lúc.`)
      await load()
    } catch (e) { setSaveErr((e as Error).message) } finally { setSaving(false) }
  }

  function onSave() {
    if (!data) return
    const n = Number(draft)
    const { min, max, max_concurrent_downloads: cur } = data.setting
    if (!Number.isInteger(n) || n < min || n > max) {
      // wording: BA review
      setSaveErr(`Nhập số nguyên từ ${min} đến ${max}.`); setSaveOk(null); return
    }
    if (n > cur * 2 &&
      // wording: BA review
      !window.confirm(`Bạn đang tăng giới hạn từ ${cur} lên ${n} (hơn gấp đôi). Khi CPU đầy, mỗi lượt tải sẽ chậm đi. Vẫn lưu?`)) return
    save(n)
  }

  const axis: Axis | null = useMemo(() => {
    if (!data) return null
    const bucket = range === '24h' ? 300 : 3600
    const span = range === '24h' ? 86400 : 7 * 86400
    const last = data.points.length ? data.points[data.points.length - 1].t : 0
    const now = Math.floor(Date.now() / 1000)
    const t1 = Math.ceil(Math.max(now, last + bucket) / bucket) * bucket
    return { t0: t1 - span, t1, bucket }
  }, [data, range])

  if (loading && !data) {
    return <p className="text-sm text-fg-muted">{/* wording: BA review */}Đang tải số liệu…</p>
  }
  if (!data || !axis) {
    return (
      <div className="space-y-2">
        <h1 className="text-lg font-semibold text-fg">{/* wording: BA review */}Tải đồng thời</h1>
        <p className="text-sm text-danger" role="alert">{/* wording: BA review */}Không tải được số liệu: {err}</p>
        <button onClick={load} className="rounded-md border border-line px-3 py-1.5 text-xs text-fg-2 hover:bg-surface">
          {/* wording: BA review */}Thử lại
        </button>
      </div>
    )
  }

  const { summary: s, setting, resources, points } = data
  const lim = setting.max_concurrent_downloads
  const lastLimit = points.length ? points[points.length - 1].limit : lim
  const partial = s.minutes_sampled < 180
  const peakMax = niceMax(Math.max(1, ...points.map(p => Math.max(p.peak, p.limit))) * 1.1)
  const refMax = niceMax(Math.max(1, ...points.map(p => p.refused)))
  const dirty = draft !== String(lim)
  const latency = [...data.latency].sort((a, b) => b.total - a.total)
  const common = { points, axis, range, hover, setHover }

  const cpuTone = s.cpu_p95 != null && s.cpu_p95 >= 80

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-fg">{/* wording: BA review */}Tải đồng thời</h1>
        <p className="mt-1 text-xs text-fg-muted">
          {/* wording: BA review */}
          Tối đa {lim} lượt tải cùng lúc (qua /fetch-link); lượt thứ {lim + 1} bị từ chối ngay với lỗi 429.
          Máy chủ có {resources.cpu_cores != null ? fmtNum(resources.cpu_cores, 2) : '—'} nhân CPU
          {resources.memory_pct_now != null ? `, RAM đang dùng ${fmtNum(resources.memory_pct_now, 0)}%` : ''}.
        </p>
      </div>

      {/* Settings */}
      <section className="rounded-card border border-line bg-surface shadow-card p-4" aria-labelledby="cap-set">
        <h2 id="cap-set" className="text-sm font-semibold text-fg">{/* wording: BA review */}Số lượt tải cùng lúc tối đa</h2>
        <p className="mt-1 text-xs text-fg-2">
          {/* wording: BA review */}
          Hiện tại: <b className="text-fg">{lim}</b> —{' '}
          {setting.source === 'admin' ? 'do quản trị viên đặt' : `mặc định từ cấu hình máy chủ (${setting.env_default})`}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label htmlFor="cap-input" className="sr-only">{/* wording: BA review */}Số lượt tải cùng lúc tối đa</label>
          <input id="cap-input" type="number" inputMode="numeric" min={setting.min} max={setting.max} step={1}
            value={draft} disabled={saving}
            onChange={e => { setDraft(e.target.value); setSaveErr(null); setSaveOk(null) }}
            className="w-24 rounded-md border border-line bg-canvas px-2 py-1.5 text-sm text-fg disabled:opacity-50" />
          <span className="text-[11px] text-fg-muted">({setting.min}–{setting.max})</span>
          <button onClick={onSave} disabled={saving || !dirty}
            className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:bg-accent-hover disabled:opacity-50">
            {/* wording: BA review */}{saving ? 'Đang lưu…' : 'Lưu'}
          </button>
          <button onClick={() => save(null)} disabled={saving || setting.source === 'env'}
            className="rounded-md border border-line px-3 py-1.5 text-xs text-fg-2 hover:bg-surface-2 disabled:opacity-50">
            {/* wording: BA review */}Đặt lại mặc định
          </button>
        </div>
        <p className="mt-2 text-[11px] text-fg-muted">
          {/* wording: BA review */}
          Tăng giới hạn giúp nhiều người tải cùng lúc hơn, nhưng mỗi lượt sẽ chậm đi khi CPU đã đầy. Sau khi đổi, hãy theo dõi biểu đồ CPU bên dưới.
        </p>
        {saveErr && <p className="mt-2 text-xs text-danger" role="alert">{saveErr}</p>}
        {saveOk && <p className="mt-2 text-xs text-success" role="status">{saveOk}</p>}
      </section>

      {/* Range + status */}
      <div className="flex flex-wrap items-center gap-3">
        <div role="group" aria-label="Khoảng thời gian" className="inline-flex overflow-hidden rounded-md border border-line">
          {(['24h', '7d'] as Range[]).map(r => (
            <button key={r} onClick={() => setRange(r)} aria-pressed={range === r}
              className={`px-3 py-1.5 text-xs ${range === r ? 'bg-accent text-accent-fg' : 'text-fg-2 hover:bg-surface-2'}`}>
              {/* wording: BA review */}{r === '24h' ? '24 giờ' : '7 ngày'}
            </button>
          ))}
        </div>
        <label className="inline-flex items-center gap-1.5 text-xs text-fg-2">
          <input type="checkbox" checked={asTable} onChange={e => setAsTable(e.target.checked)} />
          {/* wording: BA review */}Xem dạng bảng
        </label>
        {err && <span className="text-xs text-danger" role="alert">{/* wording: BA review */}Làm mới thất bại: {err}</span>}
      </div>

      {partial && (
        <p className="rounded-md border border-line bg-surface-2 px-3 py-2 text-xs text-fg-2" role="status">
          {/* wording: BA review */}
          {s.first_sample
            ? `Mới bắt đầu đo từ ${fmtClock(s.first_sample, true)} (giờ VN); cần vài giờ dữ liệu để biểu đồ có ý nghĩa.`
            : 'Chưa có dữ liệu đo; hệ thống ghi mỗi phút, cần vài giờ để biểu đồ có ý nghĩa.'}
        </p>
      )}

      {/* Tiles */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {/* wording: BA review */}
        <Tile label="Cao nhất cùng lúc" value={s.peak == null ? '—' : `${s.peak} / ${lim}`} sub="lượt / giới hạn hiện tại" />
        {/* wording: BA review */}
        <Tile label="Lượt bị từ chối (quá tải)" value={fmtNum(s.refused)} warn={s.refused > 0}
          sub={s.acquired ? `trên tổng ${fmtNum(s.acquired + s.refused)} lượt` : undefined} />
        {/* wording: BA review */}
        <Tile label="Số phút chạm giới hạn" value={fmtNum(s.minutes_at_limit)} warn={s.minutes_at_limit > 0}
          sub={`trong ${fmtNum(s.minutes_sampled)} phút đã đo`} />
        {/* wording: BA review */}
        <Tile label="CPU mức cao (p95)" value={s.cpu_p95 == null ? '—' : `${fmtNum(s.cpu_p95, 0)}%`} warn={cpuTone}
          sub={s.cpu_max == null ? undefined : `95% thời gian thấp hơn mức này · cao nhất ${fmtNum(s.cpu_max, 0)}%`} />
      </div>

      {points.length === 0 ? (
        <p className="rounded-card border border-line bg-surface p-6 text-center text-sm text-fg-muted">
          {/* wording: BA review */}Chưa có điểm đo nào trong khoảng này.
        </p>
      ) : asTable ? (
        <div className="rounded-card border border-line bg-surface shadow-card overflow-x-auto max-h-96 overflow-y-auto">
          <table className="w-full text-xs">
            <caption className="sr-only">{/* wording: BA review */}Số liệu tải đồng thời theo khoảng thời gian</caption>
            <thead className="sticky top-0 bg-surface text-left text-fg-muted">
              <tr>
                {/* wording: BA review */}
                {['Thời điểm', 'Cao nhất', 'Giới hạn', 'Bị từ chối', 'CPU trung bình %', 'CPU cao nhất %', 'Việc đang chờ'].map(h => (
                  <th key={h} scope="col" className="px-3 py-2 font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...points].reverse().map(p => (
                <tr key={p.t} className="border-t border-line text-fg-2">
                  <td className="px-3 py-1.5 whitespace-nowrap">{fmtClock(p.t, true)}</td>
                  <td className="px-3 py-1.5">{p.peak}</td>
                  <td className="px-3 py-1.5">{p.limit}</td>
                  <td className="px-3 py-1.5">{p.refused}</td>
                  <td className="px-3 py-1.5">{fmtNum(p.cpu_avg, 0)}</td>
                  <td className="px-3 py-1.5">{fmtNum(p.cpu_max, 0)}</td>
                  <td className="px-3 py-1.5">{fmtNum(p.queue_max)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="space-y-3">
          <Chart {...common}
            /* wording: BA review */
            title={range === '24h' ? 'Lượt tải cùng lúc (cao nhất mỗi 5 phút)' : 'Lượt tải cùng lúc (cao nhất mỗi giờ)'}
            yMax={peakMax} yTicks={[0, peakMax / 2, peakMax]}
            refLines={[{ value: lastLimit, label: `Giới hạn ${lastLimit}` }]}
            render={({ x, plotH, px }) => {
              const y = (v: number) => 8 + plotH - (Math.min(v, peakMax) / peakMax) * plotH
              return runs(points, axis.bucket).map((run, i) => {
                const d = run.map((p, j) => `${j ? 'L' : 'M'}${x(p.t)},${y(p.peak)} L${x(p.t) + px},${y(p.peak)}`).join(' ')
                return run.length === 1
                  ? <rect key={i} x={x(run[0].t)} y={y(run[0].peak) - 1} width={Math.max(px, 2)} height={2} fill="var(--vg-accent)" />
                  : <path key={i} d={d} fill="none" stroke="var(--vg-accent)" strokeWidth={2} strokeLinejoin="round" />
              })
            }} />
          <Chart {...common}
            /* wording: BA review */
            title="Lượt bị từ chối (quá tải)"
            height={100}
            yMax={refMax} yTicks={[0, refMax]}
            render={({ x, plotH, px }) => {
              const y = (v: number) => 8 + plotH - (v / refMax) * plotH
              return points.filter(p => p.refused > 0).map(p => {
                const w = Math.max(px - 2, 2)
                const top = y(p.refused)
                const h = Math.max(8 + plotH - top, 2)
                return <rect key={p.t} x={x(p.t) + (px - w) / 2} y={8 + plotH - h} width={w} height={h} rx={Math.min(2, w / 2)}
                  fill="var(--vg-danger)" />
              })
            }} />
          <Chart {...common}
            /* wording: BA review */
            title="CPU (%)"
            note="Đường đậm: trung bình · đường mờ: cao nhất"
            yMax={100} yTicks={[0, 50, 100]} yFmt={v => `${v}%`}
            refLines={[{ value: 80, label: '80%' }]}
            render={({ x, plotH, px }) => {
              const y = (v: number) => 8 + plotH - (Math.min(v, 100) / 100) * plotH
              const line = (key: 'cpu_avg' | 'cpu_max', w: number, op: number) =>
                runs(points.filter(p => p[key] != null), axis.bucket).map((run, i) => {
                  const cx = (p: Point) => x(p.t) + px / 2
                  return run.length === 1
                    ? <circle key={`${key}${i}`} cx={cx(run[0])} cy={y(run[0][key] as number)} r={w} fill="var(--vg-accent)" opacity={op} />
                    : <path key={`${key}${i}`} fill="none" stroke="var(--vg-accent)" strokeWidth={w} opacity={op} strokeLinejoin="round"
                        d={run.map((p, j) => `${j ? 'L' : 'M'}${cx(p)},${y(p[key] as number)}`).join(' ')} />
                })
              return <>{line('cpu_max', 1, 0.4)}{line('cpu_avg', 2, 1)}</>
            }} />
          <p className="flex items-center gap-1.5 text-[11px] text-fg-muted">
            <Cpu size={12} aria-hidden="true" />
            {/* wording: BA review */}
            Khoảng trống trên biểu đồ nghĩa là không có số liệu đo ở thời điểm đó.
          </p>
        </div>
      )}

      {/* Latency */}
      <section className="rounded-card border border-line bg-surface shadow-card p-4" aria-labelledby="cap-lat">
        <h2 id="cap-lat" className="text-sm font-semibold text-fg">{/* wording: BA review */}Thời gian một lượt tải (lượt thành công)</h2>
        {latency.length === 0 ? (
          <p className="mt-2 text-xs text-fg-muted">{/* wording: BA review */}Chưa có dữ liệu.</p>
        ) : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-fg-muted">
                <tr>
                  {/* wording: BA review */}
                  <th scope="col" className="py-1.5 pr-3 font-medium">Nền tảng</th>
                  <th scope="col" className="py-1.5 px-3 text-right font-medium">Số lượt</th>
                  <th scope="col" className="py-1.5 px-3 text-right font-medium">Trung vị (p50)</th>
                  <th scope="col" className="py-1.5 pl-3 text-right font-medium">Chậm nhất 5% (p95)</th>
                </tr>
              </thead>
              <tbody>
                {latency.map(l => (
                  <tr key={l.platform} className="border-t border-line text-fg-2">
                    <td className="py-1.5 pr-3 text-fg">{l.platform}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums">{fmtNum(l.total)}</td>
                    <td className="py-1.5 px-3 text-right tabular-nums">{fmtMs(l.p50_ms)}</td>
                    {/* wording: BA review */}
                    <td className="py-1.5 pl-3 text-right tabular-nums" title={l.p95_ms == null && l.total > 0 ? 'Lâu hơn 60 giây' : undefined}>{l.p95_ms == null ? (l.total > 0 ? '> 60 s' : '—') : fmtMs(l.p95_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
