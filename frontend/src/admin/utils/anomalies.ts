import type { AlertSeverity } from '../panels/ActiveAlertsBanner'

/** Anything older than this is not "active" any more (Overview list + bell). */
export const STALE_AFTER_MS = 24 * 3_600_000

export const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  critical: 'Nghiêm trọng',
  warning:  'Cảnh báo',
  info:     'Thông tin',
}

const TYPE_LABEL: Record<string, string> = {
  success_drop:   'Tỉ lệ tải thành công giảm',
  failure_spike:  'Lỗi tăng đột biến',
  disk_pressure:  'Ổ đĩa sắp đầy',
  schedule_drift: 'Lịch chạy bị trễ',
}

/** "success_drop:tiktok" -> { type: "success_drop", platform: "tiktok" } */
export function parseAnomalyType(raw: string | undefined): { type: string; platform?: string } {
  const s = raw ?? ''
  const i = s.indexOf(':')
  if (i < 0) return { type: s }
  return { type: s.slice(0, i), platform: s.slice(i + 1) || undefined }
}

export function anomalyLabel(type: string): string {
  return TYPE_LABEL[type] ?? type
}

export function relativeTimeVi(iso: string | undefined, now = Date.now()): string {
  if (!iso) return '—'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '—'
  const min = Math.floor((now - t) / 60_000)
  if (min < 1) return 'vừa xong'
  if (min < 60) return `${min} phút trước`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} giờ trước`
  return `${Math.floor(hr / 24)} ngày trước`
}

export function absoluteTime(iso: string | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('vi-VN')
}

export function isStale(iso: string | undefined, now = Date.now()): boolean {
  if (!iso) return false
  const t = new Date(iso).getTime()
  return !Number.isNaN(t) && now - t > STALE_AFTER_MS
}

/** The fields of an anomaly record this UI understands. */
export interface AnomalyLike {
  id?: string
  type?: string
  metric?: string
  state?: string
  severity?: string
  platform?: string
  likely_cause?: string
  message?: string
  detected_at?: string
  first_seen?: string
  last_seen?: string
  occurrences?: number
  magnitude?: number
  [k: string]: unknown
}

export function lastActivity(a: AnomalyLike): string | undefined {
  return a.last_seen ?? a.detected_at
}

export function anomalySeverity(a: AnomalyLike): AlertSeverity {
  const s = (a.severity ?? '').toLowerCase()
  if (s === 'critical' || s === 'high') return 'critical'
  if (s === 'warning' || s === 'medium') return 'warning'
  if (s === 'info' || s === 'low') return 'info'
  if (a.state === 'escalated') return 'critical'
  if (a.state === 'under_watch') return 'warning'
  const m = Math.abs(a.magnitude ?? 0)
  return m >= 0.5 ? 'critical' : m >= 0.15 ? 'warning' : 'info'
}

export interface AnomalyGroup<T extends AnomalyLike = AnomalyLike> {
  key: string
  type: string
  platform?: string
  /** Most recent member: drives the text, severity and time. */
  latest: T
  members: T[]
  /** Sum of backend `occurrences` when present, else number of rows. */
  count: number
  firstSeen?: string
  lastSeen?: string
}

/** Collapse items that share type + platform key into one row. */
export function groupAnomalies<T extends AnomalyLike>(items: T[]): AnomalyGroup<T>[] {
  const map = new Map<string, AnomalyGroup<T>>()
  for (const a of items) {
    const parsed = parseAnomalyType(a.type ?? a.metric ?? a.id)
    const platform = a.platform ?? parsed.platform
    const key = `${parsed.type}|${platform ?? ''}`
    const last = lastActivity(a)
    const first = a.first_seen ?? a.detected_at
    const n = typeof a.occurrences === 'number' && a.occurrences > 0 ? a.occurrences : 1
    const g = map.get(key)
    if (!g) {
      map.set(key, { key, type: parsed.type, platform, latest: a, members: [a], count: n, firstSeen: first, lastSeen: last })
      continue
    }
    g.members.push(a)
    g.count += n
    if ((last ?? '') > (g.lastSeen ?? '')) { g.lastSeen = last; g.latest = a }
    if (first && (!g.firstSeen || first < g.firstSeen)) g.firstSeen = first
  }
  return [...map.values()].sort((x, y) => (y.lastSeen ?? '').localeCompare(x.lastSeen ?? ''))
}


/**
 * Resolved, whatever generation of the payload this is: the backend sends
 * state "anomaly_resolved" (not "resolved") plus `active` / `status` since the
 * dedupe change. Comparing state to 'resolved' alone showed every resolved
 * anomaly as active.
 */
export function isResolvedAnomaly(a: { state?: string | null; status?: string | null; active?: boolean | null }): boolean {
  if (a.active === false) return true
  if (a.active === true) return false
  const s = (a.status || a.state || '').toLowerCase()
  return s === 'resolved' || s === 'anomaly_resolved'
}
