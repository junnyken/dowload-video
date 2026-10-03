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
  const t = d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })
  const sameDay = d.toDateString() === new Date().toDateString()
  return sameDay
    ? `Dữ liệu theo giờ mới có từ ${t}`
    : `Dữ liệu theo giờ mới có từ ${t} ${d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit' })}`
}
