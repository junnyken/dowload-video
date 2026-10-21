import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

// Douyin cookie for whole-channel listings (backend: /china-platforms/douyin/channel-cookie*).
// Separate from the download cookie pool. The backend returns cookie NAMES only, never values.

export interface ChannelCookieTest {
  ok: boolean
  pages: number
  items: number
  reason: string
  at?: number
}

export interface ChannelCookieSettings {
  enabled: boolean
  per_hour: number
  test_url: string
}

export interface ChannelCookieStatus extends ChannelCookieSettings {
  has_cookie: boolean
  saved_at: number | null
  names: string[]
  ok: number
  fail: number
  fail_streak: number
  dead: boolean
  last_ok_at: number | null
  last_fail_at: number | null
  last_fail: string | null
  last_test: ChannelCookieTest | null
  calls_this_hour: number
}

const BASE = '/china-platforms/douyin/channel-cookie'

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

export const douyinChannelCookieApi = {
  status: () => call<ChannelCookieStatus>(''),
  save: (cookies: string) =>
    call<{ names: string[]; test: ChannelCookieTest; status: ChannelCookieStatus }>('', 'POST', { cookies }),
  saveSettings: (body: Partial<ChannelCookieSettings>) => call<ChannelCookieSettings>('/settings', 'POST', body),
  test: () => call<ChannelCookieTest>('/test', 'POST'),
  remove: () => call<ChannelCookieStatus>('', 'DELETE'),
}
