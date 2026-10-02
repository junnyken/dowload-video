/**
 * Honest rate helpers. A rate over zero attempts is "unknown", never 100%
 * (and never 0%). Callers pass the attempt count whenever they have one so the
 * UI stays correct against both the old backend (1.0 / 100 with total 0) and
 * the new one (null).
 */

export const NO_TRAFFIC_HINT = 'Chưa có lượt tải'

/** Returns a 0–100 percentage, or null when there is nothing to measure. */
export function safeRate(rate: number | null | undefined, attempts?: number | null): number | null {
  if (attempts !== undefined && attempts !== null && attempts <= 0) return null
  if (rate === null || rate === undefined || Number.isNaN(rate)) return null
  return rate
}

export function fmtRate(rate: number | null, decimals = 1): string {
  return rate === null ? '—' : `${rate.toFixed(decimals)}%`
}
