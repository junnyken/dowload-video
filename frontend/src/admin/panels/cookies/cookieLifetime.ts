// Remaining lifetime shown per cookie. Everything here derives from the
// cookie's OWN claimed expiry (`expires_at` parsed from the cookies.txt) —
// a platform may invalidate a session earlier or extend it later.

export type LifetimeTone = 'ok' | 'soon' | 'past' | 'unknown'

export interface Lifetime {
  text: string
  tone: LifetimeTone
}

const DAY = 86400

export function describeLifetime(expiresAt: number | undefined, nowSec: number): Lifetime {
  if (!expiresAt || expiresAt <= 0) {
    return { text: 'Không rõ hạn (cookie phiên)', tone: 'unknown' }
  }
  const diff = expiresAt - nowSec
  if (diff >= 0) {
    if (diff < DAY) return { text: 'còn dưới 1 ngày', tone: 'soon' }
    const d = Math.round(diff / DAY)
    return { text: `còn ~${d} ngày`, tone: d <= 30 ? 'soon' : 'ok' }
  }
  const ago = Math.floor(-diff / DAY)
  return { text: ago < 1 ? 'hết hạn hôm nay' : `hết hạn ${ago} ngày trước`, tone: 'past' }
}

/** dd/MM/yyyy in Vietnam time, or null when unknown. */
export function formatAddedDate(addedAt: number | undefined): string | null {
  if (!addedAt || addedAt <= 0) return null
  return new Date(addedAt * 1000).toLocaleDateString('vi-VN', {
    timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit', year: 'numeric',
  })
}

/** HH:mm:ss in Vietnam time. */
export function formatClock(ms: number): string {
  return new Date(ms).toLocaleTimeString('vi-VN', {
    timeZone: 'Asia/Ho_Chi_Minh', hour12: false,
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

/** dd/MM HH:mm in Vietnam time. */
export function formatStamp(sec: number): string {
  return new Date(sec * 1000)
    .toLocaleString('en-GB', {
      timeZone: 'Asia/Ho_Chi_Minh', hour12: false,
      day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    })
    .replace(',', '')
}
