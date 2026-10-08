import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

// Cobalt (task #6133). Read-only. Backend: GET /api/v1/admin/cobalt/status.

export type CobaltStep =
  | 'cobalt_first_ok' | 'cobalt_first_miss' | 'anon_ok' | 'cobalt_ok'
  | 'cookie_ok' | 'all_fail' | 'gone'

export interface CobaltInstance {
  host: string
  ok: boolean
  ms: number | null
  version: string | null
  cooling_down: boolean
}

export interface CobaltOkFail { ok: number; fail: number }

export interface CobaltStatus {
  days: number
  day_keys: string[]
  redis_ok: boolean
  instances: CobaltInstance[]
  platforms: Record<string, Record<string, CobaltOkFail>>
  platforms_total: Record<string, CobaltOkFail>
  steps: Record<string, Record<string, Partial<Record<CobaltStep, number>>>>
  steps_total: Record<string, Partial<Record<CobaltStep, number>>>
  tripped: { platform: string; seconds_left: number }[]
  flags: {
    cobalt_first: string
    cookie_last: string
    trip_after: number
    trip_seconds: number
  }
}

export async function fetchCobaltStatus(days: number): Promise<CobaltStatus> {
  const headers: Record<string, string> = {}
  const token = getAdminToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/v1/admin/cobalt/status?days=${days}`, { headers })
  if (!res.ok) {
    let msg = `Lỗi HTTP ${res.status}`
    try {
      const data = await res.json()
      const d = data?.detail
      msg = typeof d === 'string' ? d : d?.message ?? msg
    } catch { /* keep the status text */ }
    throw new Error(msg)
  }
  return res.json() as Promise<CobaltStatus>
}
