import { useCallback, useEffect, useMemo, useState } from 'react'
import { intelFetch, intelPost } from '../utils/adminFetch'
import {
  absoluteTime, anomalyLabel, anomalySeverity, groupAnomalies, relativeTimeVi,
  SEVERITY_LABEL, type AnomalyGroup, type AnomalyLike,
} from '../utils/anomalies'
import type { AlertSeverity } from '../panels/ActiveAlertsBanner'

/**
 * Ported from the orphaned src/pages/Admin/AnomalyPanel.jsx. The new shell only
 * ever surfaced anomalies as a count on the home page and as alert items; the
 * list itself, and the ability to resolve one, had no home.
 *
 * The detector can emit many near-identical records (same type + platform, one
 * per detection). They are folded into one row per type+platform with a count;
 * the raw payload stays available behind "Chi tiết kỹ thuật".
 */

interface Anomaly extends AnomalyLike {
  id: string
}

const SEV_STYLE: Record<AlertSeverity, string> = {
  critical: 'border-danger/40 bg-danger-soft text-danger',
  warning:  'border-warning/30 bg-warning-soft text-warning',
  info:     'border-line bg-surface-2 text-fg-2',
}
const SEV_RANK: Record<AlertSeverity, number> = { critical: 0, warning: 1, info: 2 }

/** Fields shown in the main row; everything else is technical detail. */
const MAIN_FIELDS = new Set([
  'type', 'metric', 'state', 'severity', 'platform', 'likely_cause', 'message',
  'detected_at', 'first_seen', 'last_seen', 'occurrences',
])

function techFields(a: Anomaly): Array<[string, string]> {
  return Object.entries(a)
    // null / false / empty carry no information; keep 0 and true.
    .filter(([k, v]) => !MAIN_FIELDS.has(k) && v !== null && v !== undefined && v !== false && v !== '')
    .map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)])
}

function Time({ iso }: { iso?: string }) {
  if (!iso) return <span>—</span>
  return <time dateTime={iso} title={absoluteTime(iso)}>{relativeTimeVi(iso)}</time>
}

export default function AnomaliesPage() {
  const [items, setItems] = useState<Anomaly[]>([])
  const [err, setErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [showResolved, setShowResolved] = useState(false)

  const load = useCallback(async () => {
    try {
      const r = await intelFetch<{ anomalies: Anomaly[]; count: number }>('/anomalies')
      setItems(r.anomalies || [])
      setErr(null)
    } catch (e) { setErr((e as Error).message) } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [load])

  // Same API call as before, once per underlying record of the row.
  async function resolveGroup(g: AnomalyGroup<Anomaly>) {
    setBusy(p => ({ ...p, [g.key]: true }))
    setMsg(p => ({ ...p, [g.key]: '' }))
    try {
      for (const a of g.members.filter(m => m.state !== 'resolved')) {
        await intelPost(`/anomalies/${encodeURIComponent(a.id)}/resolve`)
      }
      setMsg(p => ({ ...p, [g.key]: 'Đã đánh dấu xử lý xong.' }))
      await load()
    } catch (e) {
      setMsg(p => ({ ...p, [g.key]: (e as Error).message }))
    } finally {
      setBusy(p => ({ ...p, [g.key]: false }))
    }
  }

  const groups = useMemo(() => {
    const visible = showResolved ? items : items.filter(a => a.state !== 'resolved')
    return groupAnomalies(visible).sort((a, b) =>
      SEV_RANK[anomalySeverity(a.latest)] - SEV_RANK[anomalySeverity(b.latest)]
      || (b.lastSeen ?? '').localeCompare(a.lastSeen ?? ''))
  }, [items, showResolved])

  const activeRecords = items.filter(a => a.state !== 'resolved').length
  const activeGroups = groupAnomalies(items.filter(a => a.state !== 'resolved')).length

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-fg">Anomalies</h1>
          <p className="text-xs text-fg-muted">
            {activeGroups} loại đang hoạt động ({activeRecords} bản ghi) · {items.length} tổng · làm mới 30s
          </p>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-fg-muted">
          <input type="checkbox" checked={showResolved}
                 onChange={e => setShowResolved(e.target.checked)} />
          Hiện cả mục đã xử lý
        </label>
      </div>

      {err && <p className="text-xs text-danger">Lỗi: {err}</p>}
      {loading && <p className="text-xs text-fg-muted animate-pulse">Đang tải…</p>}
      {!loading && groups.length === 0 && (
        <p className="text-xs text-fg-muted">
          {items.length === 0 ? 'Không có bất thường nào.' : 'Không còn mục nào đang hoạt động.'}
        </p>
      )}

      <div className="space-y-2">
        {groups.map(g => {
          const a = g.latest
          const sev = anomalySeverity(a)
          const resolved = g.members.every(m => m.state === 'resolved')
          const cause = a.likely_cause ?? a.message ?? ''
          const repeat = g.count >= 2
          const tech = techFields(a)
          return (
            <div key={g.key}
                 className={`rounded-card border border-line bg-surface p-3 shadow-card ${resolved ? 'opacity-70' : ''}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${SEV_STYLE[sev]}`}>
                      {SEVERITY_LABEL[sev]}
                    </span>
                    <span className="text-sm font-semibold text-fg">{anomalyLabel(g.type)}</span>
                    {g.platform && (
                      <span className="rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-fg-2">
                        {g.platform}
                      </span>
                    )}
                    {resolved && <span className="text-[10px] text-fg-muted">Đã xử lý</span>}
                  </div>
                  {cause && <p className="text-xs text-fg-2">{cause}</p>}
                  <p className="text-[11px] text-fg-muted">
                    {repeat
                      ? <>Lặp lại {g.count} lần · lần cuối <Time iso={g.lastSeen} /></>
                      : <Time iso={g.lastSeen} />}
                  </p>
                </div>
                {!resolved && (
                  <div className="flex items-center gap-2">
                    {msg[g.key] && <span className="text-[10px] text-success">{msg[g.key]}</span>}
                    <button disabled={!!busy[g.key]} onClick={() => resolveGroup(g)}
                      className="rounded border border-line px-2 py-0.5 text-[10px] text-fg-2
                                 hover:bg-surface-2 disabled:opacity-50">
                      {busy[g.key] ? '…' : 'Đánh dấu đã xử lý'}
                    </button>
                  </div>
                )}
              </div>
              <details className="mt-2 border-t border-line pt-2">
                <summary className="cursor-pointer text-[11px] text-fg-muted hover:text-fg-2">
                  Chi tiết kỹ thuật
                </summary>
                <div className="mt-2 space-y-1 font-mono text-[10px] text-fg-muted">
                  <div>loại: {a.type ?? a.metric ?? '—'}</div>
                  {tech.map(([k, v]) => (
                    <div key={k}><span className="opacity-70">{k}:</span> {v}</div>
                  ))}
                  <div>
                    <span className="opacity-70">bản ghi ({g.members.length}):</span>{' '}
                    {g.members.slice(0, 10).map(m => m.id).join(', ')}
                    {g.members.length > 10 ? ` … +${g.members.length - 10}` : ''}
                  </div>
                </div>
              </details>
            </div>
          )
        })}
      </div>
    </div>
  )
}
