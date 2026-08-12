import { adminFetch, adminPost } from '../utils/adminFetch'

export interface ExpiryCookieEntry {
  index: number
  hash: string
  label: string
  account_hint: string
  expires_at: number
  expires_at_str: string | null
  days_left: number | null
  expiry_status: string
  health_status: string
  blocked: boolean
  blocked_ttl_s: number | null
  cooldown_ttl_s: number | null
  last_used: number
}

export interface CookieListResponse {
  success: boolean
  platform: string
  total: number
  cookies: ExpiryCookieEntry[]
}

export interface CookieRemoveResponse {
  success: boolean
  platform: string
  pool_size: number
}

export interface CookieAddResponse {
  success: boolean
  platform: string
  pool_size: number
}

export function fetchCookieList(platform: string): Promise<CookieListResponse> {
  return adminFetch<CookieListResponse>(`/cookies/list/${platform}`)
}

export function deleteCookie(platform: string, index: number): Promise<CookieRemoveResponse> {
  return adminFetch<CookieRemoveResponse>('/cookies/remove', {
    method: 'DELETE',
    body: JSON.stringify({ platform, index }),
  })
}

export function addCookie(
  platform: string,
  rawCookie: string,
  label?: string,
): Promise<CookieAddResponse> {
  const cookieBytes = new TextEncoder().encode(rawCookie)
  const cookies_b64 = btoa(Array.from(cookieBytes, b => String.fromCharCode(b)).join(''))
  return adminPost<CookieAddResponse>('/cookies/add', { platform, cookies_b64, label })
}

export interface CookiePlatformSummary {
  total: number
  healthy: number
  in_cooldown: number
  soft_blocked: number
  hard_blocked: number
  expiry_warnings: number
  min_days_left: number | null
}

export interface CookieStatusResponse {
  success: boolean
  pools: Record<string, CookiePlatformSummary>
}

export function fetchCookieStatus(): Promise<CookieStatusResponse> {
  return adminFetch<CookieStatusResponse>('/cookies/status')
}

export interface TestCookieResponse {
  ok: boolean
  message: string
  found_keys?: string[]
  missing_keys?: string[]
  expires_at_str?: string | null
}

export function testCookieApi(platform: string, rawCookie: string): Promise<TestCookieResponse> {
  const cookieBytes = new TextEncoder().encode(rawCookie)
  const cookies_b64 = btoa(Array.from(cookieBytes, b => String.fromCharCode(b)).join(''))
  return adminPost<TestCookieResponse>('/cookies/test', { platform, cookies_b64 })
}

export function triggerCookieHealthCheck(): Promise<unknown> {
  return adminPost('/cookies/check-expiry')
}
