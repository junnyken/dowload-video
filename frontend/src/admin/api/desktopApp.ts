import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

// App Windows (task #6087, PLAN-32D §7.1). Backend: app/api/admin_desktop.py.
// Read-only. The backend never returns the full machine hash: only the 8-char
// code and a 16-hex id used as a React key.

export type DesktopRoute = 'local' | 'local_cookie' | 'server'
export type DesktopOutcome =
  | 'ok' | 'over_shadow' | 'refused' | 'retro'
  | 'settle_completed' | 'settle_failed' | 'settle_cancelled'

export type RouteGrid = Record<DesktopRoute, Record<DesktopOutcome, number>>

export interface DesktopDevice {
  id: string
  code: string
  display_name: string | null
  client_version: string | null
  user_id: string | null
  user_email: string | null
  last_ip: string | null
  first_seen: string | null
  last_seen: string | null
  today: {
    counted_as: 'user' | 'device'
    used: number
    limit: number | null
    refunds: number
    retro: number
  }
}

export interface DevicesPayload {
  storage_ready: boolean
  devices: DesktopDevice[]
  total: number
  limit: number
  offset: number
  day_utc: string
  limits?: { anon: number; user: number }
}

export interface DesktopFlags {
  client_quota_enabled: boolean
  client_quota_mode: 'shadow' | 'enforce'
  client_quota_enforce_for: string[]
  cookie_platforms: string[]
  server_fallback_platforms: string[]
  desktop_latest_version: string
  desktop_min_version: string
  offline_grace: number
  refund_daily_max: number
  ip_mult: number
  limit_anon: number
  limit_user: number
}

export interface StatsPayload {
  days: number
  day_utc: string
  redis_ok: boolean
  routes: DesktopRoute[]
  outcomes: DesktopOutcome[]
  per_day: { day: string; routes: RouteGrid; over_shadow: number; refused: number; new_devices: number }[]
  totals: RouteGrid
  summary: {
    counted_local: number
    counted_local_cookie: number
    counted_server: number
    local_share: number | null
    over_shadow: number
    refused: number
    retro: number
    settle_failed: number
    settle_cancelled: number
    refunds_today: number
  }
  devices: { storage_ready: boolean; active_today: number; total: number; new_in_period: number }
  flags: DesktopFlags
}

async function call<T>(path: string): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getAdminToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/v1/admin/desktop${path}`, { headers })
  if (!res.ok) {
    let msg = `Lỗi HTTP ${res.status}`
    try {
      const data = await res.json()
      const d = data?.detail
      msg = typeof d === 'string' ? d : d?.message ?? msg
    } catch { /* keep the status text */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export const desktopAppApi = {
  stats: (days = 7) => call<StatsPayload>(`/stats?days=${days}`),
  devices: (q: string, limit: number, offset: number) =>
    call<DevicesPayload>(`/devices?${new URLSearchParams({ q, limit: String(limit), offset: String(offset) })}`),
}
