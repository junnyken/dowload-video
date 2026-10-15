import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

// Douyin visitor jar (backend: /china-platforms/douyin/visitor*).
// The backend never returns mint_key: only mint_key_set.

export interface VisitorSettings {
  enabled: boolean
  mint_url: string
  mint_key_set: boolean
  max_age_h: number
  per_min: number
  per_hour: number
  pause_min: number
}

export interface VisitorStatus extends VisitorSettings {
  has_jar: boolean
  age_min: number | null
  ok: number
  fail: number
  fail_streak: number
  last_ok_at: number | null
  last_fail_at: number | null
  last_fail: string | null
  last_mint_error: string | null
  mints: number
  cooling_down: boolean
  paused_for_s: number
  calls_this_min: number
  calls_this_hour: number
}

export type VisitorSettingsInput = Partial<Omit<VisitorSettings, 'mint_key_set'>> & { mint_key?: string }

const BASE = '/china-platforms/douyin/visitor'

async function call<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getAdminToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`${API_BASE}/api/v1/admin${BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
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

export const douyinVisitorApi = {
  status: () => call<VisitorStatus>(''),
  saveSettings: (body: VisitorSettingsInput) => call<VisitorSettings>('/settings', 'POST', body),
  mint: () => call<Omit<VisitorStatus, 'ok'> & { ok: boolean }>('/mint', 'POST'),
}
