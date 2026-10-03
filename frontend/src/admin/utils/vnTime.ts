/**
 * Admin days are Vietnam calendar days (Asia/Ho_Chi_Minh, UTC+7) — the
 * backend buckets "today" and every per-day number that way (ADMIN_TIMEZONE).
 * Render times in the same zone, whatever the viewer's browser timezone is.
 */

export const ADMIN_TZ = 'Asia/Ho_Chi_Minh'

/** Shown once next to a "hôm nay" label. */
export const VN_TIME_HINT = 'giờ VN'

/** Tooltip / note for a day the backend could only approximate. */
export const APPROX_DAY_HINT = 'Ước tính theo ngày UTC'

/** "YYYY-MM-DD" (already a VN calendar date from the backend) → "DD/MM". */
export function vnDayLabel(date?: string | null): string {
  const p = (date ?? '').split('-')
  return p.length === 3 ? `${p[2]}/${p[1]}` : (date ?? '')
}

/** ISO instant → "HH:mm" in Vietnam time. */
export function vnTime(iso: string | Date): string {
  const d = iso instanceof Date ? iso : new Date(iso)
  return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', timeZone: ADMIN_TZ })
}

/** ISO instant → "DD/MM" (Vietnam calendar date). */
export function vnDate(iso: string | Date): string {
  const d = iso instanceof Date ? iso : new Date(iso)
  return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', timeZone: ADMIN_TZ })
}

/** True when both instants fall on the same Vietnam calendar day. */
export function sameVnDay(a: Date, b: Date): boolean {
  const k = (d: Date) => d.toLocaleDateString('en-CA', { timeZone: ADMIN_TZ })
  return k(a) === k(b)
}
