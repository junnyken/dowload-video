import { getAdminToken } from '../hooks/useAdminAuth'
import { API_BASE } from '../../lib/apiBase'

// Douyin cookie for whole-channel listings (backend: /china-platforms/douyin/channel-cookie*).
// Separate from the download cookie pool; several cookies taken in turn, tested every 12 hours.
// The backend returns cookie NAMES only, never values.

export interface ChannelCookieTest {
  ok: boolean
  pages: number
  items: number
  reason: string
  at?: number
  auto?: boolean
}

export interface ChannelCookieSettings {
  enabled: boolean
  per_hour: number
  test_url: string
}

export interface ChannelCookie {
  id: string
  label: string
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
}

/** The last 12-hourly automatic test of every cookie. */
export interface ChannelCookieAuto {
  at: number
  tested: number
  ok: number
  dead: number
  ip_blocked: boolean
  skipped: number
}

export interface ChannelCookieStatus extends ChannelCookieSettings {
  cookies: ChannelCookie[]
  has_cookie: boolean
  live: number
  calls_this_hour: number
  auto: ChannelCookieAuto | null
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
  add: (cookies: string, label: string) =>
    call<{ id: string; names: string[]; test: ChannelCookieTest; status: ChannelCookieStatus }>('', 'POST', { cookies, label }),
  saveSettings: (body: Partial<ChannelCookieSettings>) => call<ChannelCookieSettings>('/settings', 'POST', body),
  test: (id: string) => call<ChannelCookieTest>(`/${encodeURIComponent(id)}/test`, 'POST'),
  remove: (id: string) => call<ChannelCookieStatus>(`/${encodeURIComponent(id)}`, 'DELETE'),
}
