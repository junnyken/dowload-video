import { useCallback, useEffect, useState } from 'react'
import { fetchCobaltStatus } from '../api/cobalt'
import type { CobaltStatus, CobaltStep } from '../api/cobalt'
import { PageHeader } from '../shared/PageHeader'
import { Card } from '../shared/Card'
import { Button } from '../shared/Button'
import { SectionHeader } from '../shared/SectionHeader'
import { StatusPill } from '../shared/StatusPill'
import { EmptyState } from '../shared/EmptyState'
import { ErrorState } from '../shared/ErrorState'
import { TableSkeleton } from '../shared/LoadingSkeleton'
import { TABLE, TABLE_SCROLL, TD, TD_MONO, TD_NUM, TH, TH_NUM, TR } from '../shared/tableStyles'
import { cn } from '../utils/cn'

/**
 * Cobalt (task #6133). Máy Cobalt có trả lời không và Cobalt phục vụ từng nền
 * tảng bao nhiêu lần. Chỉ xem, không sửa.
 */

const DAY_OPTIONS = [1, 7, 30] as const

const PLATFORM_NAME: Record<string, string> = {
  facebook: 'Facebook',
  instagram: 'Instagram',
  twitter: 'X',
  tiktok: 'TikTok',
  youtube: 'YouTube',
}
const platformName = (p: string) => PLATFORM_NAME[p] ?? (p ? p.charAt(0).toUpperCase() + p.slice(1) : p)
const fmt = (v: number) => v.toLocaleString('vi-VN')

const STEP_COLS: { key: CobaltStep; label: string; warn?: boolean }[] = [
  // wording: BA review
  { key: 'cobalt_first_ok', label: 'Cobalt (trước)' },
  // wording: BA review
  { key: 'cobalt_first_miss', label: 'Cobalt trượt' },
  // wording: BA review
  { key: 'anon_ok', label: 'Không cookie' },
  // wording: BA review
  { key: 'cobalt_ok', label: 'Cobalt (sau)' },
  // wording: BA review
  { key: 'cookie_ok', label: 'Dùng cookie' },
  // wording: BA review
  { key: 'all_fail', label: 'Hỏng hết', warn: true },
  // wording: BA review
  { key: 'gone', label: 'Video không còn' },
]

function InstancesCard({ data }: { data: CobaltStatus }) {
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title="Máy Cobalt" subtitle="Mỗi máy được hỏi thử khi mở trang này" />
      <div className="mt-3">
        {data.instances.length === 0
          // wording: BA review
          ? <EmptyState compact title="Chưa cấu hình máy Cobalt nào" />
          : (
            <div className={TABLE_SCROLL}>
              <table className={TABLE}>
                <thead>
                  <tr>
                    {/* wording: BA review */}
                    <th className={TH}>Máy</th>
                    <th className={TH}>Trạng thái</th>
                    <th className={TH_NUM}>Độ trễ (ms)</th>
                    <th className={TH}>Phiên bản</th>
                  </tr>
                </thead>
                <tbody>
                  {data.instances.map((i) => (
                    <tr key={i.host} className={TR}>
                      <td className={TD_MONO}>{i.host}</td>
                      <td className={TD}>
                        {/* wording: BA review */}
                        {i.cooling_down
                          ? <StatusPill tone="warning" label="Đang tạm nghỉ" />
                          : i.ok
                            ? <StatusPill tone="success" label="Đang chạy" />
                            : <StatusPill tone="danger" label="Không trả lời" />}
                      </td>
                      <td className={TD_NUM}>{i.ms == null ? '—' : fmt(i.ms)}</td>
                      <td className={TD_MONO}>{i.version ?? '—'}</td>
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

function TrippedCard({ data }: { data: CobaltStatus }) {
  const { trip_after, trip_seconds } = data.flags
  return (
    <Card as="section">
      <SectionHeader
        // wording: BA review
        title="Tạm ngắt theo nền tảng"
        // wording: BA review
        subtitle={`Ngắt sau ${trip_after} lỗi liên tiếp, trong ${Math.round(trip_seconds / 60)} phút`}
      />
      <div className="mt-3">
        {data.tripped.length === 0
          ? <p className="text-sm text-success">{/* wording: BA review */}Không nền tảng nào đang bị ngắt.</p>
          : (
            <ul className="flex flex-col gap-2">
              {data.tripped.map((t) => (
                <li key={t.platform}
                  className="rounded-control border border-warning/30 bg-warning-soft px-4 py-2 text-sm text-warning">
                  {/* wording: BA review */}
                  {platformName(t.platform)} — Cobalt tạm bỏ qua, còn ~{Math.max(1, Math.ceil(t.seconds_left / 60))} phút
                </li>
              ))}
            </ul>
          )}
      </div>
    </Card>
  )
}

function RatesCard({ data }: { data: CobaltStatus }) {
  const rows = Object.entries(data.platforms_total)
    .map(([p, v]) => ({ p, ok: v.ok ?? 0, fail: v.fail ?? 0 }))
    .sort((a, b) => (b.ok + b.fail) - (a.ok + a.fail))
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title={`Tỉ lệ thành công theo nền tảng (${data.days} ngày)`} />
      <div className="mt-3">
        {rows.length === 0
          // wording: BA review
          ? <EmptyState compact title={`Chưa có lượt tải nào qua Cobalt trong ${data.days} ngày.`} />
          : (
            <div className={TABLE_SCROLL}>
              <table className={TABLE}>
                <thead>
                  <tr>
                    {/* wording: BA review */}
                    <th className={TH}>Nền tảng</th>
                    <th className={TH_NUM}>Thành công</th>
                    <th className={TH_NUM}>Lỗi</th>
                    <th className={TH_NUM}>Tỉ lệ</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const total = r.ok + r.fail
                    const pct = total > 0 ? Math.round((r.ok / total) * 100) : null
                    return (
                      <tr key={r.p} className={TR}>
                        <td className={TD}>{platformName(r.p)}</td>
                        <td className={TD_NUM}>{fmt(r.ok)}</td>
                        <td className={cn(TD_NUM, r.fail > 0 && 'text-warning')}>{fmt(r.fail)}</td>
                        <td className={TD_NUM}>{pct == null ? '—' : `${pct}%`}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
      </div>
    </Card>
  )
}

function StepsCard({ data }: { data: CobaltStatus }) {
  const rows = Object.entries(data.steps_total)
  return (
    <Card as="section">
      {/* wording: BA review */}
      <SectionHeader title={`Bước phục vụ tải Facebook / Instagram / X (${data.days} ngày)`} />
      <div className="mt-3">
        {rows.length === 0
          // wording: BA review
          ? <EmptyState compact title={`Chưa có lượt tải nào trong ${data.days} ngày.`} />
          : (
            <div className={TABLE_SCROLL}>
              <table className={TABLE}>
                <thead>
                  <tr>
                    {/* wording: BA review */}
                    <th className={TH}>Nền tảng</th>
                    {STEP_COLS.map((c) => <th key={c.key} className={TH_NUM}>{c.label}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {rows.map(([p, s]) => (
                    <tr key={p} className={TR}>
                      <td className={TD}>{platformName(p)}</td>
                      {STEP_COLS.map((c) => {
                        const v = s[c.key] ?? 0
                        return (
                          <td key={c.key} className={cn(TD_NUM, c.warn && v > 0 && 'text-warning')}>{fmt(v)}</td>
                        )
                      })}
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

export default function CobaltPage() {
  const [days, setDays] = useState<number>(7)
  const [data, setData] = useState<CobaltStatus | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await fetchCobaltStatus(days))
      setErr(null)
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [days])

  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [load])

  return (
    <div className="flex flex-col gap-5">
      {/* wording: BA review */}
      <PageHeader
        title="Cobalt"
        description="Cobalt là máy tải thứ hai (sau yt-dlp), được dùng trước cho Facebook / Instagram / X. Trang này cho biết máy Cobalt của mình có trả lời không và Cobalt phục vụ từng nền tảng bao nhiêu lần."
        actions={(
          <>
            <div className="flex overflow-hidden rounded-control border border-line">
              {DAY_OPTIONS.map((d) => (
                <button key={d} type="button" onClick={() => setDays(d)}
                  className={cn(
                    'px-3 py-1 font-mono text-xs transition-colors',
                    days === d ? 'bg-surface-2 text-fg' : 'bg-surface text-fg-muted hover:text-fg',
                  )}>
                  {d}d
                </button>
              ))}
            </div>
            {/* wording: BA review */}
            <Button onClick={load}>Tải lại</Button>
          </>
        )}
      />

      {/* wording: BA review */}
      {err && <ErrorState compact title="Không tải được số liệu Cobalt" message={err} onRetry={load} />}
      {data && !data.redis_ok && (
        <div className="rounded-control border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning">
          {/* wording: BA review */}
          Không đọc được số liệu đếm lượt (Redis), các số bên dưới có thể bằng 0.
        </div>
      )}

      {!data && !err ? (
        <>
          <Card as="section"><TableSkeleton rows={3} /></Card>
          <Card as="section"><TableSkeleton rows={3} /></Card>
          <Card as="section"><TableSkeleton rows={4} /></Card>
        </>
      ) : data ? (
        <>
          <InstancesCard data={data} />
          <TrippedCard data={data} />
          <RatesCard data={data} />
          <StepsCard data={data} />
          <p className="text-[11px] text-fg-muted">
            {/* wording: BA review */}
            Cobalt trước: {data.flags.cobalt_first || '—'}; Không cookie trước: {data.flags.cookie_last || '—'}
          </p>
        </>
      ) : null}
    </div>
  )
}
