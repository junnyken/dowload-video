import { sameVnDay, vnDate, vnTime } from './vnTime'

/**
 * The 24h admin numbers come from hourly counters that only exist since the
 * build that started writing them. The backend says so (partial /
 * covered_since); the UI must say it too instead of showing a quiet low number.
 */

export interface WindowCoverage {
  partial?: boolean
  coverage_hours?: number
  covered_since?: string | null
}

/** A hint when the 24h window is not fully covered, else null. */
export function coverageHint(c?: WindowCoverage | null): string | null {
  if (!c || c.partial !== true) return null
  if (!c.covered_since) return 'Chưa có dữ liệu theo giờ'
  const d = new Date(c.covered_since)
  if (Number.isNaN(d.getTime())) return 'Dữ liệu theo giờ chưa đủ 24h'
  // Times are Vietnam time, like every admin day.
  const t = vnTime(d)
  return sameVnDay(d, new Date())
    ? `Dữ liệu theo giờ mới có từ ${t}`
    : `Dữ liệu theo giờ mới có từ ${t} ${vnDate(d)}`
}
